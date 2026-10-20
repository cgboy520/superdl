#!/usr/bin/env bash
# Local end-to-end check of the gateway image against real peers (docker; host needs the
# wireguard / esp4 / tun modules). Not run in CI.
#
#   bash deploy/netgw/tests/integration.sh [wireguard|ipsec|openvpn|openconnect|all]
#
# Topology on bridge 172.30.0.0/24: peer .10 (VPN server, "intranet" 10.1.0.1:8080 on lo),
# gateway .20 (the image under test, tunnel address 10.250.0.2), svc .30 (stands in for a cluster
# Service on 8080, reached through a reverse relay).
set -euo pipefail

IMAGE="${NETGW_IMAGE:-superdl-netgw:dev}"
PEER_IMAGE="$IMAGE"
NET=netgw-it
PSK="correct horse battery staple"
UUID=0123456789abcdef0123456789abcdef
WORK="$(mktemp -d)"
trap 'cleanup' EXIT

cleanup() {
  docker rm -f netgw-it-gw netgw-it-peer netgw-it-svc >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}

fail() {
  echo "FAIL: $*" >&2
  echo "--- gateway logs ---" >&2
  docker logs --tail 60 netgw-it-gw >&2 || true
  echo "--- peer logs ---" >&2
  docker logs --tail 30 netgw-it-peer >&2 || true
  exit 1
}

retry() {
  for _ in $(seq 1 40); do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  return 1
}

tool() { docker run --rm --entrypoint "$1" "$IMAGE" "${@:2}"; }

setup_network() {
  cleanup
  WORK="$(mktemp -d)"
  docker network create --subnet 172.30.0.0/24 "$NET" >/dev/null
  docker run -d --name netgw-it-svc --network "$NET" --ip 172.30.0.30 --entrypoint sh "$IMAGE" \
    -c 'mkdir -p /tmp/w && echo cluster-side > /tmp/w/index.html && cd /tmp/w && exec python3 -m http.server 8080' >/dev/null
}

start_peer() {
  # $1 = shell snippet that brings the VPN server up; the intranet web server is common.
  docker run -d --name netgw-it-peer --network "$NET" --ip 172.30.0.10 \
    --cap-add NET_ADMIN --cap-add MKNOD -v "$WORK:/work" --entrypoint sh "$PEER_IMAGE" -c "
      set -e
      ip address add 10.1.0.1/32 dev lo
      mkdir -p /dev/net && { [ -c /dev/net/tun ] || mknod /dev/net/tun c 10 200; }
      mkdir -p /tmp/w && echo intranet-side > /tmp/w/index.html
      (cd /tmp/w && python3 -m http.server --bind 10.1.0.1 8080 >/dev/null 2>&1 &)
      $1
      exec sleep infinity" >/dev/null
}

start_gateway() {
  docker run -d --name netgw-it-gw --network "$NET" --ip 172.30.0.20 \
    --cap-drop ALL --cap-add NET_ADMIN --cap-add NET_BIND_SERVICE --cap-add MKNOD \
    --read-only --tmpfs /run --tmpfs /tmp --security-opt no-new-privileges \
    -v "$WORK/spec.json:/etc/netgw/spec.json:ro" "$IMAGE" >/dev/null
}

write_spec() {
  # $1 = protocol, $2 = protocol section JSON, $3 = local_address JSON
  cat >"$WORK/spec.json" <<EOF
{"version": 1, "gateway": "$UUID", "protocol": "$1",
 "remote_cidrs": ["10.1.0.0/24"], "cluster_cidrs": ["172.30.0.0/24"], "local_address": $3,
 "dns": {"upstream": "127.0.0.11", "servers": [], "domains": []},
 "forwards": [{"port": 20000, "host": "10.1.0.1", "target_port": 8080}],
 "reverse": [{"port": 2222, "host": "172.30.0.30", "target_port": 8080}],
 "$1": $2}
EOF
}

# Peer-side request sourced from the intranet address: WireGuard AllowedIPs and IPsec selectors
# only admit tunnel traffic from remote_cidrs, exactly as real intranet hosts would send it.
INTRANET_GET='
import socket, sys
s = socket.socket(); s.settimeout(10); s.bind(("10.1.0.1", 0))
s.connect((sys.argv[1], int(sys.argv[2])))
s.sendall(b"GET / HTTP/1.0\r\n\r\n")
print(b"".join(iter(lambda: s.recv(4096), b"")).decode(errors="replace"))
'

SOCKS_CLIENT='
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.create_connection(("127.0.0.1", 1080), timeout=10)
s.sendall(b"\x05\x01\x00"); assert s.recv(2) == b"\x05\x00"
s.sendall(b"\x05\x01\x00\x01" + socket.inet_aton(host) + port.to_bytes(2, "big"))
reply = s.recv(10)
if reply[1] != 0:
    print("socks-refused", reply[1]); sys.exit(3)
s.sendall(b"GET / HTTP/1.0\r\n\r\n")
print(b"".join(iter(lambda: s.recv(4096), b"")).decode(errors="replace"))
'

check_paths() {
  # $1 = address the peer uses to reach the gateway's tunnel side
  retry docker exec netgw-it-gw wget -qO- http://127.0.0.1:9101/healthz || fail "tunnel never became healthy"
  docker exec netgw-it-gw wget -qO- http://127.0.0.1:20000/ | grep -q intranet-side \
    || fail "forward 20000 -> 10.1.0.1:8080 through the tunnel"
  docker exec netgw-it-gw python3 -c "$SOCKS_CLIENT" 10.1.0.1 8080 | grep -q intranet-side \
    || fail "SOCKS CONNECT to the intranet"
  if docker exec netgw-it-gw python3 -c "$SOCKS_CLIENT" 172.30.0.30 8080 >/dev/null 2>&1; then
    fail "SOCKS reached a destination outside remote_cidrs"
  fi
  docker exec netgw-it-peer python3 -c "$INTRANET_GET" "$1" 2222 | grep -q cluster-side \
    || fail "reverse relay from the tunnel side to the cluster stand-in"
  docker exec netgw-it-gw wget -qO- http://127.0.0.1:9101/metrics | grep -q '^netgw_tunnel_up 1' \
    || fail "metrics report the tunnel up"
  docker stop -t 15 netgw-it-gw >/dev/null
  [ "$(docker inspect -f '{{.State.ExitCode}}' netgw-it-gw)" = 0 ] || fail "SIGTERM exit code"
}

test_wireguard() {
  echo "== wireguard"
  setup_network
  local gw_priv gw_pub peer_priv peer_pub
  gw_priv="$(tool wg genkey)"
  gw_pub="$(echo "$gw_priv" | docker run --rm -i --entrypoint wg "$IMAGE" pubkey)"
  peer_priv="$(tool wg genkey)"
  peer_pub="$(echo "$peer_priv" | docker run --rm -i --entrypoint wg "$IMAGE" pubkey)"
  printf '%s' "$peer_priv" >"$WORK/peer.key"
  start_peer "
      ip link add wg0 type wireguard
      wg set wg0 listen-port 51820 private-key /work/peer.key peer $gw_pub allowed-ips 10.250.0.2/32
      ip address add 10.250.0.1/24 dev wg0
      ip link set wg0 up"
  write_spec wireguard "{\"private_key\": \"$gw_priv\", \"peer_public_key\": \"$peer_pub\",
    \"preshared_key\": null, \"endpoint\": \"172.30.0.10:51820\", \"mtu\": 1420}" '"10.250.0.2"'
  start_gateway
  check_paths 10.250.0.2
  echo "ok wireguard"
}

test_ipsec() {
  echo "== ipsec"
  setup_network
  cat >"$WORK/strongswan.conf" <<'EOF'
charon {
  install_routes = no
  filelog {
    stderr {
      default = 1
    }
  }
}
EOF
  cat >"$WORK/swanctl.conf" <<EOF
connections {
  gw {
    version = 2
    local_addrs = 172.30.0.10
    encap = yes
    local {
      auth = psk
      id = peer.example
    }
    remote {
      auth = psk
      id = gw.example.com
    }
    children {
      gw {
        local_ts = 10.1.0.0/24
        remote_ts = 10.250.0.2/32
      }
    }
  }
}
secrets { ike-1 { secret = "$PSK" } }
EOF
  start_peer "
      (STRONGSWAN_CONF=/work/strongswan.conf /usr/lib/strongswan/charon &)
      for i in \$(seq 1 50); do [ -S /run/charon.vici ] && break; sleep 0.2; done
      swanctl --load-all --file /work/swanctl.conf"
  write_spec ipsec "{\"version\": 2, \"peer\": \"172.30.0.10\", \"psk\": \"$PSK\",
    \"local_id\": \"gw.example.com\", \"remote_id\": \"peer.example\", \"proposals\": \"compatible\"}" \
    '"10.250.0.2"'
  start_gateway
  check_paths 10.250.0.2
  echo "ok ipsec"
}

test_openvpn() {
  echo "== openvpn"
  setup_network
  # Throwaway PKI: CA, server and client certificates.
  docker run --rm -v "$WORK:/work" --entrypoint sh alpine:3.22 -c '
    set -e; apk add -q --no-cache openssl >/dev/null; cd /work
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 2 \
      -subj /CN=it-ca -keyout ca.key -out ca.crt 2>/dev/null
    for n in server client; do
      openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -subj /CN=$n \
        -keyout $n.key -out $n.csr 2>/dev/null
      printf "keyUsage=critical,digitalSignature,keyAgreement\nextendedKeyUsage=%s\n" \
        "$([ $n = server ] && echo serverAuth || echo clientAuth)" >$n.ext
      openssl x509 -req -in $n.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 2 \
        -extfile $n.ext -out $n.crt 2>/dev/null
    done
    chmod 644 *'
  cat >"$WORK/server.conf" <<'EOF'
dev tun
proto udp
port 1194
topology subnet
server 10.250.8.0 255.255.255.0
ca /work/ca.crt
cert /work/server.crt
key /work/server.key
dh none
keepalive 10 60
verb 3
EOF
  start_peer "(openvpn --config /work/server.conf &)"
  local profile
  profile="$(printf 'client\ndev tun\nproto udp\nremote 172.30.0.10 1194\nnobind\nremote-cert-tls server\n<ca>\n%s\n</ca>\n<cert>\n%s\n</cert>\n<key>\n%s\n</key>\n' \
    "$(cat "$WORK/ca.crt")" "$(cat "$WORK/client.crt")" "$(cat "$WORK/client.key")")"
  write_spec openvpn "$(python3 -c 'import json,sys; print(json.dumps({"config": sys.argv[1], "username": None, "password": None}))' "$profile")" null
  start_gateway
  retry docker exec netgw-it-gw wget -qO- http://127.0.0.1:9101/healthz || fail "tunnel never became healthy"
  local addr
  addr="$(docker exec netgw-it-gw ip -4 -o address show dev tun0 | awk '{print $4}' | cut -d/ -f1)"
  # The server only routes its pool; the intranet address sits on the server's lo.
  check_paths "$addr"
  echo "ok openvpn"
}

test_openconnect() {
  echo "== openconnect"
  setup_network
  # ocserv is not packaged for Alpine; the AnyConnect-compatible peer runs on Debian.
  PEER_IMAGE=netgw-it-ocserv
  docker build -q -t "$PEER_IMAGE" - >/dev/null <<'EOF'
FROM debian:trixie-slim
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends ocserv python3 iproute2 openssl >/dev/null \
    && rm -rf /var/lib/apt/lists/*
EOF
  docker run --rm -v "$WORK:/work" --entrypoint sh "$PEER_IMAGE" -c '
    set -e; cd /work
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 2 \
      -subj /CN=vpn.example -keyout oc.key -out oc.crt 2>/dev/null
    printf "s3cret\ns3cret\n" | ocpasswd -c /work/ocpasswd alice
    openssl x509 -in oc.crt -pubkey -noout | openssl pkey -pubin -outform der \
      | openssl dgst -sha256 -binary | base64 >pin
    chmod 644 *'
  cat >"$WORK/ocserv.conf" <<'EOF'
auth = "plain[passwd=/work/ocpasswd]"
tcp-port = 443
udp-port = 443
run-as-user = nobody
run-as-group = nogroup
socket-file = /run/ocserv.socket
server-cert = /work/oc.crt
server-key = /work/oc.key
isolate-workers = false
max-clients = 8
keepalive = 30
dpd = 30
try-mtu-discovery = false
device = vpns
predictable-ips = true
ipv4-network = 10.250.9.0
ipv4-netmask = 255.255.255.0
route = 10.1.0.0/255.255.255.0
cisco-client-compat = true
EOF
  start_peer "(ocserv -f -c /work/ocserv.conf >/proc/1/fd/1 2>&1 &)"
  write_spec openconnect "{\"protocol\": \"anyconnect\", \"server\": \"https://172.30.0.10\",
    \"username\": \"alice\", \"password\": \"s3cret\", \"servercert\": \"pin-sha256:$(cat "$WORK/pin")\",
    \"authgroup\": null}" null
  start_gateway
  retry docker exec netgw-it-gw wget -qO- http://127.0.0.1:9101/healthz || fail "tunnel never became healthy"
  local addr
  addr="$(docker exec netgw-it-gw ip -4 -o address show dev tun0 | awk '{print $4}' | cut -d/ -f1)"
  check_paths "$addr"
  PEER_IMAGE="$IMAGE"
  echo "ok openconnect"
}

case "${1:-all}" in
  wireguard) test_wireguard ;;
  ipsec) test_ipsec ;;
  openvpn) test_openvpn ;;
  openconnect) test_openconnect ;;
  all)
    test_wireguard
    test_ipsec
    test_openvpn
    test_openconnect
    ;;
  *) echo "usage: $0 [wireguard|ipsec|openvpn|openconnect|all]" >&2; exit 2 ;;
esac
echo "integration ok"
