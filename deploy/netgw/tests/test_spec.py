import unittest

from fixtures import PARAMS, raw_spec
from netgw_agent.spec import SpecError, parse


class SpecAcceptsTest(unittest.TestCase):
    def test_every_protocol_fixture_parses(self) -> None:
        """Fails when a valid platform-rendered spec is rejected by the agent."""
        for protocol in PARAMS:
            with self.subTest(protocol=protocol):
                spec = parse(raw_spec(protocol))
                self.assertEqual(spec.protocol, protocol)
                self.assertEqual(len(spec.remote_cidrs), 2)


class SpecRejectsTest(unittest.TestCase):
    def assertRejected(self, raw: dict, needle: str) -> None:
        with self.assertRaises(SpecError) as ctx:
            parse(raw)
        self.assertIn(needle, str(ctx.exception))

    def test_remote_overlapping_cluster_ranges(self) -> None:
        """Fails when a tenant route could shadow the Pod / Service ranges."""
        self.assertRejected(raw_spec(remote_cidrs=["10.0.0.0/8"]), "overlaps")

    def test_remote_default_route_and_loopback(self) -> None:
        """Fails when the tunnel could take the default route or loopback."""
        self.assertRejected(raw_spec(remote_cidrs=["0.0.0.0/0"]), "overlaps")
        self.assertRejected(raw_spec(remote_cidrs=["127.0.0.0/8"]), "overlaps")

    def test_local_address_required_for_wireguard_and_ipsec(self) -> None:
        self.assertRejected(raw_spec("wireguard", local_address=None), "local_address")
        self.assertRejected(raw_spec("ipsec", local_address=None), "local_address")

    def test_reserved_and_duplicate_ports(self) -> None:
        """Fails when a relay could steal the SOCKS / health / DNS ports or collide."""
        fwd = {"port": 1080, "host": "10.1.0.5", "target_port": 22}
        self.assertRejected(raw_spec(forwards=[fwd]), "reserved")
        same = {"port": 2222, "host": "10.1.0.5", "target_port": 22}
        self.assertRejected(raw_spec(forwards=[same]), "share a listen port")

    def test_dns_servers_must_be_tenant_side(self) -> None:
        dns = {"upstream": "10.43.0.10", "servers": ["8.8.8.8"], "domains": ["corp.example"]}
        self.assertRejected(raw_spec(dns=dns), "inside remote_cidrs")

    def test_ipsec_psk_cannot_break_out_of_the_quoted_secret(self) -> None:
        """Fails when a PSK with a quote could inject swanctl configuration."""
        raw = raw_spec("ipsec")
        raw["ipsec"]["psk"] = 'abcdefghijklmnop" } evil {'
        self.assertRejected(raw, "psk")

    def test_openconnect_username_without_spaces(self) -> None:
        """Fails when an argv value could carry extra openconnect options."""
        raw = raw_spec("openconnect")
        raw["openconnect"]["username"] = "alice --script=/bin/sh"
        self.assertRejected(raw, "username")

    def test_openvpn_profile_runs_through_allow_list(self) -> None:
        raw = raw_spec("openvpn")
        raw["openvpn"]["config"] += "up /bin/sh\n"
        self.assertRejected(raw, "not allowed")

    def test_wireguard_key_shape(self) -> None:
        raw = raw_spec("wireguard")
        raw["wireguard"]["peer_public_key"] = "short"
        self.assertRejected(raw, "WireGuard key")

    def test_version_mismatch(self) -> None:
        self.assertRejected(raw_spec(version=2), "version")


if __name__ == "__main__":
    unittest.main()
