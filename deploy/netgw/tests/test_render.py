import unittest

from fixtures import KEY_B, raw_spec
from netgw_agent import render
from netgw_agent.spec import parse


class RenderTest(unittest.TestCase):
    def test_wireguard_conf_has_no_wg_quick_hooks(self) -> None:
        """Fails when the config could carry Address / PostUp lines that wg setconf rejects or
        that would run commands under wg-quick."""
        conf = render.wireguard_conf(parse(raw_spec("wireguard")))
        self.assertIn(f"PublicKey = {KEY_B}", conf)
        self.assertIn("AllowedIPs = 10.1.0.0/16, 192.168.10.0/24", conf)
        self.assertIn("PersistentKeepalive = 25", conf)
        for word in ("Address", "PostUp", "DNS", "PreUp"):
            self.assertNotIn(word, conf)

    def test_wireguard_mtu_respects_pod_link(self) -> None:
        spec = parse(raw_spec("wireguard"))
        self.assertEqual(render.wireguard_mtu(spec, 1450), 1370)
        self.assertEqual(render.wireguard_mtu(spec, 9000), 1420)

    def test_swanctl_is_policy_based_and_encapsulated(self) -> None:
        """Fails when ESP could leave unencapsulated (dropped by the node SNAT) or the selectors
        drift from local_address ↔ remote_cidrs."""
        conf = render.swanctl_conf(parse(raw_spec("ipsec")))
        self.assertIn("encap = yes", conf)
        self.assertIn("local_ts = 10.250.0.2/32", conf)
        self.assertIn("remote_ts = 10.1.0.0/16,192.168.10.0/24", conf)
        self.assertIn("id = gw.example.com", conf)
        self.assertIn("id = %any", conf)
        self.assertIn('secret = "correct horse battery staple"', conf)
        self.assertNotIn("if_id", conf)

    def test_swanctl_default_proposals_omitted(self) -> None:
        raw = raw_spec("ipsec")
        raw["ipsec"]["proposals"] = "default"
        conf = render.swanctl_conf(parse(raw))
        self.assertNotIn("proposals", conf)

    def test_openvpn_agent_section_wins(self) -> None:
        """Fails when pushed routes / DNS / redirect-gateway or scripts could take effect."""
        conf = render.openvpn_conf(parse(raw_spec("openvpn")))
        tail = conf.split("# netgw agent", 1)[1]
        for line in (
            "route-nopull",
            "route-noexec",
            'pull-filter ignore "redirect-gateway"',
            "script-security 1",
            f"management {render.OVPN_MGMT} unix",
            f"auth-user-pass {render.OVPN_AUTH}",
        ):
            self.assertIn(line, tail)

    def test_openconnect_password_not_on_argv(self) -> None:
        """Fails when the password would show up in the process list."""
        argv = render.openconnect_argv(parse(raw_spec("openconnect")))
        self.assertIn("--passwd-on-stdin", argv)
        self.assertNotIn("s3cret", " ".join(argv))
        self.assertEqual(argv[-1], "https://vpn.example.com")

    def test_dnsmasq_split_domains(self) -> None:
        conf = render.dnsmasq_conf(parse(raw_spec("wireguard")))
        self.assertIn("server=10.43.0.10", conf)
        self.assertIn("server=/corp.example/10.1.0.53", conf)
        self.assertIn("listen-address=127.0.0.1", conf)

    def test_route_batch_carries_source_hint(self) -> None:
        spec = parse(raw_spec("ipsec"))
        out = render.route_batch(spec.remote_cidrs, dev="eth0", src="10.250.0.2", via="10.42.0.1")
        self.assertEqual(
            out.splitlines(),
            [
                "route replace 10.1.0.0/16 via 10.42.0.1 dev eth0 src 10.250.0.2",
                "route replace 192.168.10.0/24 via 10.42.0.1 dev eth0 src 10.250.0.2",
            ],
        )


if __name__ == "__main__":
    unittest.main()
