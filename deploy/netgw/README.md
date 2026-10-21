# Private-network gateway image

One gateway Pod per tenant connection: it dials the tenant's VPN endpoint (WireGuard, IPsec, OpenVPN or an openconnect-family SSL VPN), then relays TCP between the tenant's instances and the tenant network, and with L3 enabled routes whole subnets between attached instances and the tenant network. This directory is the image and its agent; the platform side (tables, API, K8s objects, network policies) is in [docs/reference/netgw.md](../../docs/reference/netgw.md).

```
Dockerfile                 Alpine + wireguard-tools / strongswan / openvpn / openconnect / nftables / dnsmasq / python3
agent/netgw_agent/         the agent (stdlib only): spec checks, renderers, drivers, relays, L3 hub, health
agent/netgw_agent/attach.py  instance-side entry (init container of attached instance Pods)
agent/oc-script            openconnect --script hook (device up + address only)
tests/test_*.py            unit tests (unittest)
tests/integration.sh       local end-to-end run against real peers in docker
```

## Runtime contract

| Item               | Value                                                                                                                                                          |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Spec               | `/etc/netgw/spec.json` (Secret volume, read-only); invalid spec → exit 2 before anything starts                                                               |
| L3 peers           | `/etc/netgw/peers.json` in the same volume; re-read every tick when its content changes (an invalid file keeps the previous peers)                             |
| Process            | `tini` → `python3 -m netgw_agent`; runs as uid 0; SIGTERM stops the daemons and exits 0                                                                        |
| Capabilities       | `drop: [ALL]`, add `NET_ADMIN`, `NET_BIND_SERVICE`, `MKNOD`; `allowPrivilegeEscalation: false`, seccomp `RuntimeDefault`; no service-account token             |
| Filesystem         | read-only root; writable `emptyDir` on `/run` and `/tmp`; `/dev/net/tun` is created with `mknod` when the runtime did not provide it (openvpn / openconnect)    |
| DNS                | dnsmasq on `127.0.0.1:53`; the Pod must use `dnsPolicy: None` with nameserver `127.0.0.1`. Tenant domains go to the tenant servers through the tunnel, the rest to `dns.upstream` (the cluster DNS) |
| Ports              | `1080` SOCKS5 (CONNECT only, no auth), `9101` health / metrics, one port per forward (`forwards[].port`) and per reverse relay (`reverse[].port`); UDP `51820` hub (L3 only) |
| Firewall           | nftables table `ip netgw`, replaced atomically at prepare: forwarding is dropped except hub ↔ `remote_cidrs` (Pod netns inherit `ip_forward=1`), cleartext for `remote_cidrs` never leaves the uplink (IPsec: unless an xfrm policy matched) |
| Readiness          | `GET :9101/healthz` → 200 while the tunnel is up, 503 otherwise                                                                                                |
| Liveness           | `GET :9101/livez` → 200 while the event loop answers                                                                                                           |
| Metrics            | `GET :9101/metrics` (diagnostics inside the Pod, not scraped): `netgw_tunnel_up`, `netgw_connections_total{kind}`, `netgw_connections_active{kind}`, `netgw_rejected_total{kind}`, `netgw_relay_bytes_total{kind}`; kind ∈ forward / reverse / socks |
| Logs               | stderr: agent lines prefixed `netgw:`, daemon lines prefixed `[charon]` / `[openvpn]` / `[openconnect]` / `[dnsmasq]`; spec values and secrets are never logged |

Gateway nodes need the `wireguard`, `tun`, `xfrm_user`, `esp4` and `nf_tables` (with `nft_chain_nat`, `nft_masq`, `nft_ct`) modules; nodes running attached instances need `wireguard`.

## Spec (version 1)

| Field                       | Meaning                                                                                                                                           |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `version`                   | `1`                                                                                                                                               |
| `gateway`                   | 32-hex gateway uuid (log context only)                                                                                                            |
| `protocol`                  | `wireguard` / `ipsec` / `openvpn` / `openconnect`; the object of the same name carries the protocol parameters                                    |
| `remote_cidrs`              | 1–16 tenant IPv4 networks; routed into the tunnel with the tunnel address as source; must not overlap `cluster_cidrs`, 0/8, 127/8, 169.254/16, 224/4, 240/4 |
| `cluster_cidrs`             | Pod and Service ranges; reverse relays may only reach addresses inside them                                                                      |
| `local_address`             | Tunnel-side IPv4 of the gateway (required for wireguard / ipsec; openvpn / openconnect take the server-assigned address)                          |
| `dns`                       | `upstream` (cluster DNS IP), optional `servers` (inside `remote_cidrs`) + `domains` for split DNS                                                 |
| `forwards[]`                | `{port, host, target_port}`: listen on `port`, connect to `host:target_port`; `host` must resolve inside `remote_cidrs`                           |
| `reverse[]`                 | Same shape; reachable from the tunnel side, `host` must resolve inside `cluster_cidrs`                                                            |
| `wireguard`                 | `private_key`, `peer_public_key`, `preshared_key?`, `endpoint` (`host:port`), `mtu` (1280–1500, capped at the Pod link MTU − 80)                  |
| `ipsec`                     | `version` (1 / 2), `peer`, `psk` (16–128 printable ASCII, no `"` / `\`), `local_id?`, `remote_id?`, `proposals` (`default` / `compatible` / `modern`) |
| `openvpn`                   | `config` (client profile, allow-listed by `ovpn.py`), `username?`, `password?`                                                                   |
| `openconnect`               | `protocol` (anyconnect / array / f5 / fortinet / gp / pulse), `server` (`https://host[:port][/path]`), `username`, `password`, `servercert?` (`sha256:` / `pin-sha256:`), `authgroup?` |
| `l3?`                       | `cidr` (/16–/29, outside `remote_cidrs`, `cluster_cidrs`, the forbidden ranges and `local_address`), `address` (its first host, the hub), `nat` (required `true` for openconnect), `private_key` (hub) |

`peers.json`: `{"peers": [{"public_key", "address", "host"}]}`, at most 256; each `address` is a unique instance address of `l3.cidr` (not the hub, network or broadcast address), `host` the DNS name of the instance Pod.

## Drivers

| Protocol    | Daemon                         | Ready when                                        | Routes                                    |
| ----------- | ------------------------------ | ------------------------------------------------- | ----------------------------------------- |
| wireguard   | none (kernel `wg0`)            | latest handshake < 180 s (keepalive 25 s)         | `dev wg0 src local_address`               |
| ipsec       | `charon` + `swanctl`           | IKE SA `ESTABLISHED` and CHILD SA `INSTALLED`     | via the Pod default route, `src local_address` (policy-based, address on `lo`) |
| openvpn     | `openvpn` (management socket)  | state `CONNECTED`                                 | `dev tun0 src <assigned>`                 |
| openconnect | `openconnect` (password on stdin) | process alive and `tun0` has an address        | `dev tun0 src <assigned>`                 |

- IPsec is always UDP-encapsulated (`encap = yes`): the Pod sits behind the node SNAT, which does not carry raw ESP. The gateway only initiates.
- Server-pushed routes, DNS and `redirect-gateway` are ignored (OpenVPN `route-nopull` / `route-noexec` / `pull-filter`, openconnect's own script); the default route never enters the tunnel.
- Daemons are restarted with backoff (1 s → 30 s, reset after a minute of uptime); `prepare` (configs, interfaces) is retried until it succeeds.
- Relays and SOCKS resolve first and connect only when the address is inside the allowed ranges; at most 1024 concurrent connections.

## Whole-subnet access (L3)

```
instance Pod                         gateway Pod                              tenant network
netgw0 192.168.250.2/32  ──WireGuard──  hub0 192.168.250.1/24  ──forward──  wg0 / tun0 / xfrm  ──  remote_cidrs
(routes to remote_cidrs)   (Pod network)  (one peer per instance)   (nftables)
```

- Hub: `hub0` listens on UDP 51820; each peer accepts only its own `/32`, keeps alive every 25 s, and gets its endpoint from DNS (the instance Pod name under the gateway's headless Service), re-resolved every tick, so a restarted instance is found again without touching the gateway. Instances also dial the hub by its headless Service name; either side can start the handshake.
- Routed (`nat: false`): both directions are open between `l3.cidr` and `remote_cidrs`; the tenant side routes `l3.cidr` into the tunnel (WireGuard: add it to our peer's AllowedIPs; IPsec: it is part of our traffic selectors; OpenVPN: `iroute` on the server). NAT (`nat: true`): instance traffic leaves with the tunnel address (masquerade on `wg0` / `tun0`, IPsec SNAT to `local_address`); the tenant network cannot open connections to instances.
- TCP MSS is clamped to the route MTU on forwarded SYNs; dnsmasq also answers on the hub address, so instances can use it as their resolver for the split domains.
- `attach` (`python3 -m netgw_agent.attach`, config `/etc/netgw-attach/attach.json`: `address`, `private_key`, `peer_public_key`, `routes`, `cluster_cidrs`, `endpoint_host?`) recreates `netgw0` with `listen-port 51820`, the hub as its only peer (AllowedIPs = `routes`), MTU = Pod link MTU − 80, and `routes` via `netgw0` with the instance address as source. It exits 0 on success, 1 when a command fails, 2 on an invalid config.

## Gates

```bash
python3 -m unittest discover -s deploy/netgw/tests -p 'test_*.py'
apps/api/.venv/bin/ruff format --check deploy/netgw && apps/api/.venv/bin/ruff check deploy/netgw
shellcheck deploy/netgw/agent/oc-script deploy/netgw/tests/integration.sh
docker build -t superdl-netgw:dev deploy/netgw && bash deploy/netgw/tests/integration.sh
```

The integration run brings up a WireGuard server, a strongSwan responder, an OpenVPN server and an ocserv (AnyConnect) server in turn, and checks for each: readiness, a forward and a SOCKS CONNECT into the tenant network, a refused SOCKS CONNECT outside it, a reverse relay from the tenant side, the metrics, and a clean SIGTERM exit. The L3 cases (WireGuard routed, IPsec routed and NAT, OpenVPN NAT, openconnect NAT) attach an instance and check: peers picked up from a rewritten `peers.json`, TCP and UDP from the instance into the tenant network, the source address the tenant network sees (instance address when routed, tunnel address with NAT), that a capability-free workspace sharing the instance netns can neither change the routes nor read the key, intranet → instance only when routed, and that the tenant network cannot reach the cluster through the gateway.

## Build and publish

```bash
docker build -t <registry>/<project>/superdl-netgw:<tag> deploy/netgw
docker push <registry>/<project>/superdl-netgw:<tag>
```

Reference the image by digest (`<registry>/<project>/superdl-netgw@sha256:…`). A changed spec is a changed Pod template (the spec hash is a Pod annotation), so the gateway restarts with it; a changed `peers.json` is not, so attaching or detaching an instance never restarts the gateway.
