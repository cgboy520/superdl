# Tenant private-network gateway image: WireGuard / strongSwan / OpenVPN / openconnect clients,
# nftables, dnsmasq and the netgw agent (gateway, plus the attach entry of instance Pods).
# Contract and spec format: deploy/netgw/README.md.
FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf

RUN apk add --no-cache dnsmasq iproute2 nftables openconnect openvpn python3 strongswan tini \
    wireguard-tools-wg

COPY agent/netgw_agent /usr/lib/netgw/netgw_agent
COPY --chmod=0755 agent/oc-script /usr/libexec/netgw/oc-script
RUN python3 -m compileall -q /usr/lib/netgw \
    && python3 -c "import sys; sys.path.insert(0, '/usr/lib/netgw'); import netgw_agent.__main__"

ENV PYTHONPATH=/usr/lib/netgw \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 1080 9101
ENTRYPOINT ["/sbin/tini", "--", "python3", "-m", "netgw_agent"]
CMD ["/etc/netgw/spec.json"]
