# Private-network gateway image

One gateway Pod per tenant connection: it dials the tenant's VPN endpoint (WireGuard, IPsec, OpenVPN or an openconnect-family SSL VPN), then relays TCP between the tenant's instances and the tenant network. This directory is the image and its agent.

```
Dockerfile                 Alpine + wireguard-tools / strongswan / openvpn / openconnect / dnsmasq / python3
agent/netgw_agent/         the agent (stdlib only): spec checks, renderers, drivers, relays, health
agent/oc-script            openconnect --script hook (device up + address only)
tests/test_*.py            unit tests (unittest)
tests/integration.sh       local end-to-end run against real peers in docker
```

## Runtime contract

| Item               | Value                                                                                                                                                          |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Spec               | `/etc/netgw/spec.json` (Secret volume, read-only); invalid spec → exit 2 before anything starts                                                               |
| Process            | `tini` → `python3 -m netgw_agent`; runs as uid 0; SIGTERM stops the daemons and exits 0                                                                        |
| Capabilities       | `drop: [ALL]`, add `NET_ADMIN`, `NET_BIND_SERVICE`, `MKNOD`; `allowPrivilegeEscalation: false`, seccomp `RuntimeDefault`; no service-account token             |
| Filesystem         | read-only root; writable `emptyDir` on `/run` and `/tmp`; `/dev/net/tun` is created with `mknod` when the runtime did not provide it (openvpn / openconnect)    |
| DNS                | dnsmasq on `127.0.0.1:53`; the Pod must use `dnsPolicy: None` with nameserver `127.0.0.1`. Tenant domains go to the tenant servers through the tunnel, the rest to `dns.upstream` (the cluster DNS) |
| Ports              | `1080` SOCKS5 (CONNECT only, no auth), `9101` health / metrics, one port per forward (`forwards[].port`) and per reverse relay (`reverse[].port`)               |
| Readiness          | `GET :9101/healthz` → 200 while the tunnel is up, 503 otherwise                                                                                                |
| Liveness           | `GET :9101/livez` → 200 while the event loop answers                                                                                                           |
| Metrics            | `GET :9101/metrics`: `netgw_tunnel_up`, `netgw_connections_total{kind}`, `netgw_connections_active{kind}`, `netgw_rejected_total{kind}`, `netgw_relay_bytes_total{kind}`; kind ∈ forward / reverse / socks |
| Logs               | stderr: agent lines prefixed `netgw:`, daemon lines prefixed `[charon]` / `[openvpn]` / `[openconnect]` / `[dnsmasq]`; spec values and secrets are never logged |

The node kernel needs `wireguard`, `tun`, `xfrm_user` and `esp4`.

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

## Gates

```bash
python3 -m unittest discover -s deploy/netgw/tests -p 'test_*.py'
apps/api/.venv/bin/ruff format --check deploy/netgw && apps/api/.venv/bin/ruff check deploy/netgw
shellcheck deploy/netgw/agent/oc-script deploy/netgw/tests/integration.sh
docker build -t superdl-netgw:dev deploy/netgw && bash deploy/netgw/tests/integration.sh
```

The integration run brings up a WireGuard server, a strongSwan responder, an OpenVPN server and an ocserv (AnyConnect) server in turn, and checks for each: readiness, a forward and a SOCKS CONNECT into the tenant network, a refused SOCKS CONNECT outside it, a reverse relay from the tenant side, the metrics, and a clean SIGTERM exit.

## Build and publish

```bash
docker build -t <registry>/<project>/superdl-netgw:<tag> deploy/netgw
docker push <registry>/<project>/superdl-netgw:<tag>
```

Reference the image by digest (`<registry>/<project>/superdl-netgw@sha256:…`). A changed spec is a changed Pod template (the spec hash is a Pod annotation), so the gateway restarts with it.
