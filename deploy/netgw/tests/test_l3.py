import unittest

from fixtures import KEY_A, l3, peer, raw_attach, raw_spec
from netgw_agent import attach, render
from netgw_agent.spec import SpecError, parse, parse_peers


class L3SpecTest(unittest.TestCase):
    def assertRejected(self, raw: dict, needle: str) -> None:
        with self.assertRaises(SpecError) as ctx:
            parse(raw)
        self.assertIn(needle, str(ctx.exception))

    def test_absent_l3_keeps_the_relay_only_gateway(self) -> None:
        self.assertIsNone(parse(raw_spec("wireguard")).l3)

    def test_l3_parses(self) -> None:
        spec = parse(raw_spec("ipsec", l3=l3()))
        assert spec.l3 is not None
        self.assertEqual(str(spec.l3.cidr), "192.168.250.0/24")
        self.assertFalse(spec.l3.nat)

    def test_l3_range_may_not_shadow_remote_cluster_or_tunnel_ranges(self) -> None:
        """Fails when instance addresses could collide with a routed, cluster or forbidden range."""
        for cidr in ("10.1.4.0/24", "10.42.0.0/24", "127.0.0.0/24"):
            with self.subTest(cidr=cidr):
                self.assertRejected(raw_spec(l3={**l3(), "cidr": cidr}), "overlaps")
        self.assertRejected(
            raw_spec(l3={**l3(), "cidr": "10.250.0.0/24", "address": "10.250.0.1"}),
            "local_address",
        )

    def test_l3_prefix_and_hub_address(self) -> None:
        self.assertRejected(raw_spec(l3={**l3(), "cidr": "192.168.0.0/15"}), "prefix")
        self.assertRejected(raw_spec(l3={**l3(), "address": "192.168.250.9"}), "first host")

    def test_openconnect_requires_nat(self) -> None:
        """Fails when an OpenConnect gateway could route a range its server never assigned."""
        self.assertRejected(raw_spec("openconnect", l3=l3(nat=False)), "nat is required")
        self.assertTrue(parse(raw_spec("openconnect", l3=l3(nat=True))).l3)


class PeersTest(unittest.TestCase):
    def setUp(self) -> None:
        spec = parse(raw_spec(l3=l3()))
        assert spec.l3 is not None
        self.l3 = spec.l3

    def test_peers_parse(self) -> None:
        peers = parse_peers({"peers": [peer(2), peer(3, KEY_A)]}, self.l3)
        self.assertEqual([str(p.address) for p in peers], ["192.168.250.2", "192.168.250.3"])

    def test_peer_may_not_take_the_hub_or_an_outside_address(self) -> None:
        """Fails when an instance could claim the hub address or a range it was not given."""
        for address in ("192.168.250.1", "192.168.250.255", "10.1.0.5"):
            with self.subTest(address=address), self.assertRaises(SpecError):
                parse_peers({"peers": [{**peer(), "address": address}]}, self.l3)

    def test_duplicate_peers_rejected(self) -> None:
        with self.assertRaises(SpecError):
            parse_peers({"peers": [peer(2), peer(3)]}, self.l3)
        with self.assertRaises(SpecError):
            parse_peers({"peers": [peer(2), peer(2, KEY_A)]}, self.l3)

    def test_hub_peer_accepts_only_its_own_address(self) -> None:
        """Fails when an attached instance could send from another instance's address."""
        (p,) = parse_peers({"peers": [peer(2)]}, self.l3)
        argv = render.hub_peer_args(p, "10.42.1.7")
        self.assertEqual(argv[argv.index("allowed-ips") + 1], "192.168.250.2/32")
        self.assertEqual(argv[argv.index("endpoint") + 1], "10.42.1.7:51820")
        self.assertNotIn("endpoint", render.hub_peer_args(p, None))


class FirewallTest(unittest.TestCase):
    def test_relay_only_gateway_forwards_nothing(self) -> None:
        """Fails when a gateway without L3 could route between the tunnel and the cluster (pod
        netns inherit ip_forward=1)."""
        rules = render.nft_ruleset(parse(raw_spec("wireguard")), uplink="eth0", tunnel_dev="wg0")
        forward = rules.split("chain forward {", 1)[1].split("}", 1)[0]
        self.assertIn("policy drop;", forward)
        self.assertNotIn("accept", forward)
        self.assertIn('oifname "eth0" ip daddr @remote drop', rules)

    def test_routed_l3_admits_both_directions(self) -> None:
        rules = render.nft_ruleset(parse(raw_spec(l3=l3())), uplink="eth0", tunnel_dev="wg0")
        self.assertIn('iifname "hub0" ip saddr 192.168.250.0/24 ip daddr @remote accept', rules)
        self.assertIn('oifname "hub0" ip saddr @remote ip daddr 192.168.250.0/24 accept', rules)
        self.assertNotIn("masquerade", rules)
        self.assertIn("tcp option maxseg size set rt mtu", rules)

    def test_nat_l3_has_no_inbound_path(self) -> None:
        """Fails when the tenant network could open connections to instances in NAT mode."""
        rules = render.nft_ruleset(
            parse(raw_spec("openvpn", l3=l3(nat=True))), uplink="eth0", tunnel_dev="tun0"
        )
        self.assertNotIn('oifname "hub0" ip saddr @remote', rules)
        self.assertIn('ip saddr 192.168.250.0/24 ip daddr @remote oifname "tun0" masquerade', rules)

    def test_ipsec_nat_snats_to_the_tunnel_address_and_guards_cleartext(self) -> None:
        """Fails when IPsec traffic for remote_cidrs could leave the uplink unencrypted."""
        rules = render.nft_ruleset(
            parse(raw_spec("ipsec", l3=l3(nat=True))), uplink="eth0", tunnel_dev=None
        )
        self.assertIn("snat to 10.250.0.2", rules)
        self.assertIn('oifname "eth0" ip daddr @remote rt ipsec missing drop', rules)

    def test_ipsec_selectors_cover_the_routed_range(self) -> None:
        routed = render.swanctl_conf(parse(raw_spec("ipsec", l3=l3())))
        self.assertIn("local_ts = 10.250.0.2/32,192.168.250.0/24", routed)
        nat = render.swanctl_conf(parse(raw_spec("ipsec", l3=l3(nat=True))))
        self.assertIn("local_ts = 10.250.0.2/32\n", nat)

    def test_dnsmasq_answers_attached_instances(self) -> None:
        conf = render.dnsmasq_conf(parse(raw_spec(l3=l3())))
        self.assertIn("listen-address=127.0.0.1,192.168.250.1", conf)
        self.assertIn("bind-dynamic", conf)


class AttachTest(unittest.TestCase):
    def test_attach_parses(self) -> None:
        a = attach.parse(raw_attach())
        self.assertEqual(str(a.address), "192.168.250.2")
        self.assertEqual(len(a.routes), 2)

    def test_routes_may_not_capture_cluster_or_default(self) -> None:
        """Fails when the instance could lose its Pod / Service network or default route."""
        for route in ("10.42.0.0/24", "0.0.0.0/0", "10.0.0.0/8"):
            with self.subTest(route=route), self.assertRaises(SpecError):
                attach.parse(raw_attach(routes=[route]))

    def test_address_inside_a_route_rejected(self) -> None:
        with self.assertRaises(SpecError):
            attach.parse(raw_attach(address="10.1.0.9"))

    def test_bad_keys_and_version_rejected(self) -> None:
        with self.assertRaises(SpecError):
            attach.parse(raw_attach(private_key="x"))
        with self.assertRaises(SpecError):
            attach.parse(raw_attach(version=2))
        with self.assertRaises(SpecError):
            attach.parse(raw_attach(endpoint_host="bad host"))


if __name__ == "__main__":
    unittest.main()
