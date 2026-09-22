"""Execute the CronJob's actual shell blocks with offline, non-secret doubles."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "deploy/app/k8s/06-pg-backup.yaml"


class BackupShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="superdl-backup-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        self.backup = self.directory / "backup"
        self.backup.mkdir()
        self.scratch = self.directory / "scratch"
        self.scratch.mkdir()
        self.log = self.directory / "commands.jsonl"
        for command in ("mktemp", "rm", "date"):
            executable = shutil.which(command)
            self.assertIsNotNone(executable, command)
            (self.bin / command).symlink_to(executable)
        stub = self.directory / "stub"
        stub.write_text(f"#!{sys.executable}\n" + (ROOT / "scripts/tests/backup_stub.py").read_text())
        stub.chmod(0o700)
        for command in ("pg_dump", "gpg", "gpg-agent", "initdb", "pg_ctl", "createdb", "pg_restore", "psql"):
            (self.bin / command).symlink_to(stub)
        self.env = {
            "PATH": str(self.bin), "BACKUP_TEST_LOG": str(self.log),
            "BACKUP_ENCRYPT_KEY": uuid.uuid4().hex,
            "PGURL": "postgresql://offline:" + uuid.uuid4().hex + "@invalid/fixture",
        }
        self.manifest = MANIFEST.read_text()
        # Read the three literal command blocks without adding a YAML dependency.
        self.blocks = [textwrap.dedent(s) for s in re.findall(
            r"(?m)^                - \|\n((?:^ {18}.*\n)+)", self.manifest,
        )]
        self.assertEqual(len(self.blocks), 3)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def execute(self, index, success=True):
        # Replace original paths in one pass; both destinations themselves live under /tmp.
        roots = {"/backup/": str(self.backup) + "/", "/tmp/": str(self.scratch) + "/"}
        script = re.sub(r"/backup/|/tmp/", lambda match: roots[match[0]], self.blocks[index])
        result = subprocess.run(["/bin/sh", "-ec", script], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertTrue(all(value not in result.stdout + result.stderr for key, value in self.env.items()
                            if key in ("BACKUP_ENCRYPT_KEY", "PGURL")), "fixture credential leaked in output")
        self.assertEqual(result.returncode == 0, success, "backup command block returned unexpected exit status")
        return result

    def test_dump_uses_stdin_loopback_private_home_and_no_credential_argv(self):
        self.execute(0)
        calls = self.calls()
        self.assertEqual([c["command"] for c in calls], ["pg_dump", "gpg"])
        self.assertTrue(calls[1]["private_home"])
        self.assertTrue(calls[1]["loopback"])
        self.assertTrue(calls[1]["stdin_fd"])
        self.assertTrue(calls[1]["passphrase_received"])
        self.assertFalse(list(self.backup.glob("*.dump")))
        self.assertEqual(len(list(self.backup.glob("*.dump.gpg"))), 1)

    def test_failed_encryption_removes_plaintext_and_fails_the_job(self):
        self.env["BACKUP_STUB_GPG_FAIL"] = "1"
        self.execute(0, success=False)
        self.assertFalse(list(self.backup.glob("*.dump")))
        self.assertFalse(list(self.backup.glob("*.dump.gpg")))

    def test_missing_gpg_fails_before_creating_plaintext(self):
        (self.bin / "gpg").unlink()
        self.execute(0, success=False)
        self.assertEqual(self.calls(), [])
        self.assertFalse(list(self.backup.iterdir()))

    def test_empty_passphrase_fails_before_dump(self):
        self.env["BACKUP_ENCRYPT_KEY"] = ""
        # No credential is generated or consumed when this gate fails.
        result = subprocess.run(["/bin/sh", "-ec", self.blocks[0]], env=self.env, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_restore_uses_same_fd_mode_and_pg_restore_fails_on_sql_errors(self):
        self.execute(2)
        calls = self.calls()
        self.assertTrue(calls[0]["loopback"])
        self.assertTrue(calls[0]["stdin_fd"])
        self.assertTrue(calls[0]["private_home"])
        self.assertTrue(next(c for c in calls if c["command"] == "pg_restore")["exit_on_error"])
        self.assertEqual(sum(c["command"] == "pg_ctl" for c in calls), 2)

    def test_failed_decryption_does_not_start_postgres(self):
        self.env["BACKUP_STUB_GPG_FAIL"] = "1"
        self.execute(2, success=False)
        self.assertEqual([c["command"] for c in self.calls()], ["gpg"])

    def test_every_container_has_writable_tmp_and_root_remains_read_only(self):
        self.assertEqual(self.manifest.count("- {name: tmp, mountPath: /tmp}"), 3)
        self.assertEqual(self.manifest.count("readOnlyRootFilesystem: true"), 3)
        self.assertIn("runAsNonRoot: true", self.manifest)
        self.assertNotIn("--passphrase-env", self.manifest)
        self.assertNotIn("pg_dump \"$PGURL\"", self.manifest)


if __name__ == "__main__":
    unittest.main()
