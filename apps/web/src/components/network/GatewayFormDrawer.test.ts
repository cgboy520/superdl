/** Form state ↔ request body of the gateway drawer. */
import type { GatewayOut } from "@superdl/api-client";
import { describe, expect, it } from "vitest";

import { EMPTY, fromGateway, toBody } from "./GatewayFormDrawer";

describe("toBody", () => {
  it("sends the WireGuard fields and the tunnel address, no PSK when empty", () => {
    const body = toBody({
      ...EMPTY,
      name: "corp",
      remote: ["10.1.0.0/16"],
      local: "10.250.0.2",
      endpoint: " vpn.example.com:51820 ",
      peerKey: "k",
    });
    expect(body).toEqual({
      name: "corp",
      remote_cidrs: ["10.1.0.0/16"],
      local_address: "10.250.0.2",
      dns: null,
      tunnel: {
        protocol: "wireguard",
        endpoint: "vpn.example.com:51820",
        peer_public_key: "k",
        preshared_key: null,
        mtu: 1420,
      },
    });
  });

  it("drops the tunnel address for server-assigned protocols", () => {
    const body = toBody({ ...EMPTY, protocol: "openvpn", local: "10.250.0.2", profile: "client" });
    expect(body.local_address).toBeNull();
  });

  it("sends null for untouched secrets so an edit keeps the stored ones", () => {
    const ipsec = toBody({ ...EMPTY, protocol: "ipsec", peer: "9.9.9.9" });
    expect(ipsec.tunnel).toMatchObject({ protocol: "ipsec", psk: null });
    const ovpn = toBody({ ...EMPTY, protocol: "openvpn" });
    expect(ovpn.tunnel).toMatchObject({ protocol: "openvpn", config: null, password: null });
    const oc = toBody({ ...EMPTY, protocol: "openconnect", server: "https://vpn.example.com", username: "u" });
    expect(oc.tunnel).toMatchObject({ protocol: "openconnect", password: null });
  });

  it("sends DNS only when servers or domains are given", () => {
    const body = toBody({ ...EMPTY, dnsServers: ["10.1.0.53"], dnsDomains: ["corp.example"] });
    expect(body.dns).toEqual({ servers: ["10.1.0.53"], domains: ["corp.example"] });
  });
});

describe("fromGateway", () => {
  it("round-trips the non-secret IPsec fields for editing", () => {
    const g = {
      name: "dc",
      protocol: "ipsec",
      remote_cidrs: ["10.2.0.0/16"],
      local_address: "10.250.0.9",
      dns: null,
      tunnel: { protocol: "ipsec", version: 1, peer: "9.9.9.9", local_id: "a", remote_id: null, proposals: "modern" },
    } as unknown as GatewayOut;
    const f = fromGateway(g);
    expect(f).toMatchObject({
      ipsecVersion: 1,
      peer: "9.9.9.9",
      localId: "a",
      remoteId: "",
      proposals: "modern",
      psk: "",
    });
    expect(toBody(f).tunnel).toMatchObject({
      protocol: "ipsec",
      version: 1,
      local_id: "a",
      remote_id: null,
      psk: null,
    });
  });
});
