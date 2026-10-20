import unittest

from fixtures import PROFILE
from netgw_agent import ovpn


class OvpnAllowListTest(unittest.TestCase):
    def test_typical_profile_round_trips(self) -> None:
        """Fails when an ordinary vendor-exported client profile is rejected."""
        out = ovpn.render(PROFILE)
        self.assertIn("remote vpn.example.com 1194", out)
        self.assertIn("<ca>\n-----BEGIN CERTIFICATE-----", out)

    def test_command_and_file_directives_rejected(self) -> None:
        """Fails when a profile can run a command or read an agent-side file."""
        for line in (
            "up /bin/sh",
            "script-security 2",
            "plugin /tmp/x.so",
            "route-up /bin/sh",
            "tls-verify /bin/sh",
            "ca /etc/netgw/spec.json",
            "management 0.0.0.0 7505",
            "config /etc/passwd",
            "redirect-gateway def1",
            "route 0.0.0.0 0.0.0.0",
            "dev-node /dev/sda",
            "http-proxy 10.0.0.1 3128",
        ):
            with self.subTest(line=line), self.assertRaises(ovpn.ProfileError):
                ovpn.parse(PROFILE + line + "\n")

    def test_auth_user_pass_with_file_rejected(self) -> None:
        with self.assertRaises(ovpn.ProfileError):
            ovpn.parse(PROFILE.replace("auth-user-pass", "auth-user-pass /etc/shadow"))

    def test_dev_must_be_tun(self) -> None:
        with self.assertRaises(ovpn.ProfileError):
            ovpn.parse(PROFILE.replace("dev tun", "dev tap"))

    def test_unknown_inline_block_rejected(self) -> None:
        with self.assertRaises(ovpn.ProfileError):
            ovpn.parse(PROFILE + "<connection>\nremote x 1\n</connection>\n")

    def test_unclosed_inline_block_rejected(self) -> None:
        with self.assertRaises(ovpn.ProfileError):
            ovpn.parse(PROFILE + "<key>\nabc\n")

    def test_profile_without_remote_rejected(self) -> None:
        with self.assertRaises(ovpn.ProfileError):
            ovpn.parse("client\ndev tun\n")

    def test_remotes_inherit_profile_port_and_proto(self) -> None:
        """Fails when the platform would open the wrong egress port for the peer."""
        profile = (
            "client\nproto tcp-client\nport 443\n"
            "remote a.example.com\nremote b.example.com 1194 udp\n"
        )
        self.assertEqual(
            ovpn.remotes(profile), [("a.example.com", 443, "tcp"), ("b.example.com", 1194, "udp")]
        )

    def test_comments_and_crlf_ignored(self) -> None:
        directives, _ = ovpn.parse("# c\r\n; c\r\nclient\r\nremote a.example.com 1194\r\n")
        self.assertEqual([d for d, _ in directives], ["client", "remote"])


if __name__ == "__main__":
    unittest.main()
