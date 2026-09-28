"""Run the real release shell script with an isolated, allowlisted command PATH."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
WRITERS = (
    "superdl-api", "superdl-worker", "superdl-worker-tenant-mgr",
    "superdl-worker-node-mgr", "superdl-worker-prewarm", "superdl-worker-disk-ops",
)
COUNTS = dict(zip(WRITERS, (3, 4, 2, 1, 0, 2)))
PREFIX = "registry.invalid/superdl"


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="superdl-release-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        # No real kubectl, registry client, cosign, curl, or git is reachable.
        for command in ("dirname", "grep", "sed", "mktemp", "mkdir", "cp", "rm", "date", "sleep"):
            executable = shutil.which(command)
            self.assertIsNotNone(executable, command)
            (self.bin / command).symlink_to(executable)
        stub = self.directory / "stub"
        stub.write_text(f"#!{sys.executable}\n" + (ROOT / "scripts/tests/release_stub.py").read_text())
        stub.chmod(0o700)
        for command in ("kubectl", "crane", "cosign", "curl", "git", "docker"):
            (self.bin / command).symlink_to(stub)
        self.state_path = self.directory / "state.json"
        self.log = self.directory / "commands.jsonl"
        self.initial = {
            "deployments": COUNTS.copy(), "old_pods": list(WRITERS), "images": {},
        }
        self.state_path.write_text(json.dumps(self.initial))
        self.env = {
            "PATH": str(self.bin), "TMPDIR": str(self.directory),
            "RELEASE_TEST_STATE": str(self.state_path), "RELEASE_TEST_LOG": str(self.log),
            "SUPERDL_IMAGE_PREFIX": PREFIX, "SUPERDL_RELEASE_MAINTENANCE_ACK": "yes",
            "SUPERDL_RELEASE_REPOSITORY": "example-owner/superdl",
        }

    def configure(self, **values):
        state = self.state()
        state.update(values)
        self.state_path.write_text(json.dumps(state))

    def state(self):
        return json.loads(self.state_path.read_text())

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def run_release(self, success=True):
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "scripts/release.sh"), "v1.2.3"],
            env=self.env, text=True, capture_output=True, timeout=30,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def assert_no_writes(self):
        self.assertFalse([c for c in self.calls() if c["mutation"]])

    def assert_stopped(self):
        self.assertFalse(any(self.state()["deployments"].values()))
        self.assertIsNotNone(self.state().get("checkpoint"))
        self.assertFalse(any(
            "scale" in c["args"] and any(
                a.startswith("--replicas=") and a != "--replicas=0" for a in c["args"]
            ) for c in self.calls()
        ))

    def test_verified_digests_and_all_preflight_before_stop_then_migrate_then_restart(self):
        self.run_release()
        calls = self.calls()
        verify = [i for i, c in enumerate(calls) if c["command"] == "cosign"]
        self.assertEqual(len(verify), 3)
        first_write = next(i for i, c in enumerate(calls) if c["mutation"])
        self.assertLess(max(verify), first_write)
        dryruns = [i for i, c in enumerate(calls) if "--dry-run=server" in c["args"]]
        self.assertEqual(len(dryruns), 4)
        self.assertLess(max(dryruns), first_write)
        stopped = next(i for i, c in enumerate(calls) if "--for=delete" in c["args"])
        migrated = next(i for i, c in enumerate(calls) if "--for=condition=complete" in c["args"])
        restarted = next(i for i, c in enumerate(calls) if "--current-replicas=0" in c["args"])
        self.assertLess(stopped, migrated)
        self.assertLess(migrated, restarted)
        self.assertEqual(self.state()["deployments"], COUNTS)
        self.assertIsNone(self.state()["checkpoint"])
        for image, char, index in zip(("api", "web", "admin"), "abc", verify):
            args = calls[index]["args"]
            self.assertEqual(args, [
                "verify", "--certificate-identity",
                "https://github.com/example-owner/superdl/.github/workflows/release.yml@refs/tags/v1.2.3",
                "--certificate-oidc-issuer", "https://token.actions.githubusercontent.com",
                f"{PREFIX}/superdl-{image}@sha256:{char * 64}",
            ])
        for call in calls:
            for image in call.get("images", []):
                self.assertIn("@sha256:", image)

    def test_missing_cosign_fails_without_writes(self):
        (self.bin / "cosign").unlink()
        self.run_release(False)
        self.assert_no_writes()

    def test_each_unsigned_or_wrong_identity_image_fails_without_writes(self):
        for image in ("api", "web", "admin"):
            with self.subTest(image=image):
                self.configure(signature_fail=image)
                self.run_release(False)
                self.assert_no_writes()

    def test_each_unresolvable_image_fails_without_writes(self):
        for image in ("api", "web", "admin"):
            with self.subTest(image=image):
                self.configure(digest_fail=image)
                self.run_release(False)
                self.assert_no_writes()

    def test_resolver_nonzero_status_is_fatal_even_with_valid_digest_stdout(self):
        self.configure(digest_fail="api", digest_on_failure=True)
        self.run_release(False)
        self.assert_no_writes()
        self.assertFalse(any(c["command"] == "cosign" for c in self.calls()))

    def test_docker_inspection_failure_is_not_hashed_into_a_digest(self):
        (self.bin / "crane").unlink()
        self.configure(digest_fail="api")
        self.run_release(False)
        self.assert_no_writes()
        self.assertFalse(any(c["command"] == "cosign" for c in self.calls()))

    def test_origin_is_the_default_exact_repository_identity(self):
        self.env.pop("SUPERDL_RELEASE_REPOSITORY")
        self.run_release()
        verified = [c for c in self.calls() if c["command"] == "cosign"]
        self.assertIn(
            "https://github.com/example-owner/superdl/.github/workflows/release.yml@refs/tags/v1.2.3",
            verified[0]["args"],
        )

    def test_admission_and_each_dryrun_failure_do_not_stop_writers(self):
        for failure in ("admission", "config", "stopped", "started", "migrate"):
            with self.subTest(failure=failure):
                self.configure(admission_fail=failure == "admission", dryrun_fail=failure)
                self.run_release(False)
                self.assert_no_writes()

    def test_unpinned_third_party_image_is_rejected_before_stop(self):
        self.configure(bad_manifest=True)
        self.run_release(False)
        self.assert_no_writes()

    def test_maintenance_ack_required(self):
        self.env.pop("SUPERDL_RELEASE_MAINTENANCE_ACK")
        self.run_release(False)
        self.assert_no_writes()

    def test_hpa_targeting_any_writer_is_rejected(self):
        for name in WRITERS:
            with self.subTest(writer=name):
                self.configure(hpa=name)
                self.run_release(False)
                self.assert_no_writes()

    def test_unrelated_hpa_does_not_block_release(self):
        self.configure(hpa="unrelated")
        self.run_release()

    def test_paused_deployment_is_rejected_before_stop(self):
        self.configure(paused=True)
        self.run_release(False)
        self.assert_no_writes()

    def test_unready_writer_node_blocks_preflight(self):
        self.configure(node_unready=True)
        self.run_release(False)
        self.assert_no_writes()

    def test_node_lost_while_stopping_blocks_migration(self):
        self.configure(node_lost_during_stop=True)
        self.run_release(False)
        self.assert_stopped()
        self.assertNotIn("migration_count", self.state())

    def test_pods_deleted_between_list_and_wait_are_accepted(self):
        self.configure(pods_disappear_before_wait=True)
        self.run_release()

    def test_old_pod_timeout_blocks_migration_and_keeps_writers_stopped(self):
        self.configure(stop_fail=True)
        self.run_release(False)
        self.assert_stopped()
        self.assertNotIn("migration_count", self.state())
        self.assertEqual(self.state()["old_pods"], list(WRITERS))

    def test_external_controller_restart_blocks_migration(self):
        self.configure(controller_restart=True)
        self.run_release(False)
        self.assert_stopped()
        self.assertNotIn("migration_count", self.state())

    def test_migration_failure_keeps_all_writers_stopped(self):
        self.configure(migration_fail=True)
        self.run_release(False)
        self.assert_stopped()
        self.assertFalse(any("logs" in c["args"] for c in self.calls()))

    def test_failed_apply_cannot_restart_old_writers(self):
        self.configure(apply_fail=True)
        self.run_release(False)
        self.assert_stopped()

    def test_active_migration_job_or_pod_blocks_retry_before_writes(self):
        for field in ("active_job", "active_migration"):
            with self.subTest(field=field):
                self.configure(active_job=False, active_migration=False)
                self.configure(**{field: True})
                self.run_release(False)
                self.assert_no_writes()

    def test_first_install_uses_declared_default_replicas(self):
        self.configure(deployments={}, old_pods=[])
        self.run_release()
        self.assertEqual(self.state()["deployments"], dict(zip(WRITERS, (2, 2, 2, 1, 1, 1))))
        self.assertFalse(any("--for=delete" in c["args"] for c in self.calls()))

    def test_same_tag_can_run_again_with_a_fresh_migration_attempt(self):
        self.run_release()
        first = self.state()["migration_job"]
        self.run_release()
        self.assertEqual(self.state()["migration_count"], 2)
        self.assertNotEqual(self.state()["migration_job"], first)
        self.assertEqual(self.state()["deployments"], COUNTS)

    def test_resume_restores_original_counts_not_zeros_from_failed_attempt(self):
        self.configure(migration_fail=True)
        self.run_release(False)
        self.assert_stopped()
        self.configure(migration_fail=False)
        before = len(self.calls())
        self.run_release(False)
        self.assertFalse(any(c["mutation"] for c in self.calls()[before:]))
        self.env["SUPERDL_RELEASE_RESUME"] = "yes"
        self.run_release()
        self.assertEqual(self.state()["deployments"], COUNTS)

    def test_rollout_failure_stops_new_writers_and_keeps_checkpoint(self):
        self.configure(rollout_fail=True)
        self.run_release(False)
        self.assertFalse(any(self.state()["deployments"].values()))
        self.assertEqual(self.state()["checkpoint"], COUNTS)


if __name__ == "__main__":
    unittest.main()
