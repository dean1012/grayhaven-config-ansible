"""Isolated behavior and coverage tests for managed shell programs."""

from __future__ import annotations

import hashlib
import os
import pathlib
import stat
import subprocess
import tempfile
import textwrap
import unittest

from tests import shell_coverage


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def executable(path: pathlib.Path, content: str) -> None:
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class ShellProgramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        shell_coverage.reset()

    @classmethod
    def tearDownClass(cls) -> None:
        shell_coverage.write_report()

    def run_bash(self, source: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            trace = pathlib.Path(temp_dir) / "trace.bash"
            trace.write_text(shell_coverage.TRACE_SCRIPT, encoding="utf-8")
            run_env = os.environ.copy()
            run_env.update(shell_coverage.coverage_environment(trace))
            if env:
                run_env.update(env)
            return subprocess.run(
                ["bash", "-c", source],
                cwd=REPO_ROOT,
                env=run_env,
                check=False,
                capture_output=True,
                text=True,
            )

    def test_runner_arguments_and_dispatch(self) -> None:
        for arguments, expected in (
            ("--converge", "converge|false|"),
            ("--cleanup-gcs-restic-buckets --execute", "cleanup-gcs-restic-buckets|true|"),
            ("--rotate-vault-password --extra-vars-file /tmp/vars", "rotate-vault-password|false|/tmp/vars"),
            ("--rotate-vault-deploy-key", "rotate-vault-deploy-key|false|"),
            ("--rotate-ansible-control-key", "rotate-ansible-control-key|false|"),
        ):
            result = self.run_bash(
                f"export GRAYHAVEN_UNIT_TEST_SOURCE_ONLY=1; source files/grayhaven-ansible-runner; "
                f"parse_args {arguments}; printf '%s|%s|%s\\n' \"$RUNNER_COMMAND\" \"$GCS_CLEANUP_EXECUTE\" \"${{RUNNER_EXTRA_VARS_FILES[*]}}\""
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), expected)
        for arguments, message in (
            ("--unknown", "Unknown runner argument"),
            ("--execute", "only valid"),
            ("--rotate-vault-password", "requires --extra-vars-file"),
            ("--extra-vars-file", "requires a path"),
        ):
            result = self.run_bash(
                f"export GRAYHAVEN_UNIT_TEST_SOURCE_ONLY=1; source files/grayhaven-ansible-runner; parse_args {arguments}"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(message, result.stdout + result.stderr)
        result = self.run_bash(
            """
            export GRAYHAVEN_UNIT_TEST_SOURCE_ONLY=1
            source files/grayhaven-ansible-runner
            prepare_runner_runtime() { printf 'prepare\n'; }
            prepare_rotate_vault_password_file() { printf 'password\n'; }
            run_convergence() { printf 'converge\n'; }
            run_gcs_restic_bucket_cleanup() { printf 'cleanup\n'; }
            run_rotate_vault_password() { printf 'rotate-password\n'; }
            run_rotate_vault_deploy_key() { printf 'rotate-deploy\n'; }
            run_rotate_ansible_control_key() { printf 'rotate-control\n'; }
            for RUNNER_COMMAND in converge cleanup-gcs-restic-buckets rotate-vault-password rotate-vault-deploy-key rotate-ansible-control-key; do run_once; done
            """
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rotate-control", result.stdout)

    def test_poller_change_and_no_change_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            for name in ("runner.env", "control", "vault"):
                (root / name).touch()
            (root / "runner.env").write_text(
                "VAULT_REPO_URL=url\nVAULT_REPO_REF=staging\n", encoding="utf-8"
            )
            result = self.run_bash(
                f"""
                export GRAYHAVEN_UNIT_TEST_SOURCE_ONLY=1
                source files/grayhaven-ansible-poller
                STATE_DIR={str(root)!r}; RUNNER_ENV={str(root / 'runner.env')!r}
                ANSIBLE_CONTROL_PRIVATE_KEY={str(root / 'control')!r}; VAULT_DEPLOY_PRIVATE_KEY={str(root / 'vault')!r}
                drop_to_ansible() {{ :; }}; prepare_github_known_hosts() {{ :; }}
                remote_ref() {{ [[ "$1" == url ]] && printf 'vault-sha\n' || printf 'config-sha\n'; }}
                sudo() {{ printf 'trigger:%s\n' "$*"; }}
                main
                printf '%s|%s|%s|%s\n' "$(cat "$STATE_DIR/config.ref")" "$(cat "$STATE_DIR/vault.ref")" "$(format_ref_for_log '')" "$(format_ref_for_log abc)"
                main
                """
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("trigger:-n systemctl start --no-block", result.stdout)
        self.assertIn("config-sha|vault-sha|<none>|abc", result.stdout)
        self.assertIn("No repository changes detected", result.stdout)
        missing = self.run_bash(
            "export GRAYHAVEN_UNIT_TEST_SOURCE_ONLY=1; source files/grayhaven-ansible-poller; require_file /definitely/missing"
        )
        self.assertNotEqual(missing.returncode, 0)

    def test_gtmux_validation_create_and_attach(self) -> None:
        script = REPO_ROOT / "roles/admin_access/files/gtmux"
        result = self.run_bash(f"bash {script} bad")
        self.assertEqual(result.returncode, 2)
        result = self.run_bash(f"TMUX=active bash {script} --reset")
        self.assertEqual(result.returncode, 1)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            fake_bin, home = root / "bin", root / "home"
            fake_bin.mkdir()
            home.mkdir()
            executable(
                fake_bin / "tmux",
                """
                #!/usr/bin/env bash
                printf '%s\n' "$*" >> "$TMUX_LOG"
                if [[ "$1" == has-session ]]; then [[ -f "$TMUX_STATE" ]];
                elif [[ "$1" == new-session ]]; then touch "$TMUX_STATE";
                elif [[ "$1" == kill-session ]]; then rm -f "$TMUX_STATE"; fi
                """,
            )
            executable(fake_bin / "hostname", "#!/usr/bin/env bash\nprintf 'host.example.invalid\n'\n")
            env = {
                "HOME": str(home),
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "SSH_AUTH_SOCK": str(root / "agent.sock"),
                "TMUX_LOG": str(root / "tmux.log"),
                "TMUX_STATE": str(root / "state"),
            }
            result = self.run_bash(f"env -u TMUX bash {script}", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            stable_sock = home / ".ssh" / "ssh_auth_sock"
            self.assertTrue(stable_sock.is_symlink())
            self.assertEqual(os.readlink(stable_sock), str(root / "agent.sock"))

            result = self.run_bash(f"env -u TMUX bash {script}", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("new-session", (root / "tmux.log").read_text(encoding="utf-8"))

            (root / "state").touch()
            log_start = (root / "tmux.log").read_text(encoding="utf-8").splitlines()
            result = self.run_bash(f"env -u TMUX bash {script} --reset", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            log_end = (root / "tmux.log").read_text(encoding="utf-8").splitlines()
            reset_log = log_end[len(log_start) :]
            self.assertEqual(reset_log[0], "has-session -t Grayhaven Systems LLC")
            self.assertEqual(reset_log[1], "kill-session -t Grayhaven Systems LLC")
            self.assertTrue(any(entry.startswith("new-session ") for entry in reset_log))
            self.assertTrue((root / "state").exists())

    def test_galaxy_installer_success_and_count_failure(self) -> None:
        script = REPO_ROOT / "scripts/install-galaxy-collections"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            artifact = root / "example.tar.gz"
            artifact.write_bytes(b"artifact")
            requirements, checksums = root / "requirements.yml", root / "checksums"
            requirements.write_text("collections: []\n", encoding="utf-8")
            checksums.write_text(
                f"{hashlib.sha256(artifact.read_bytes()).hexdigest()}  example.tar.gz\n",
                encoding="utf-8",
            )
            fake = root / "ansible-galaxy"
            executable(
                fake,
                f"""
                #!/usr/bin/env bash
                if [[ "$1 $2" == "collection download" ]]; then cp {str(artifact)!r} "${{@: -1}}/example.tar.gz";
                else printf '%s\n' "$3" >> {str(root / 'installed')!r}; fi
                """,
            )
            env = {"ANSIBLE_GALAXY_BIN": str(fake), "ANSIBLE_COLLECTIONS_PATH": str(root / "collections")}
            result = self.run_bash(f"{script} {requirements} {checksums}", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "installed").exists())
            executable(fake, "#!/usr/bin/env bash\nexit 0\n")
            result = self.run_bash(f"{script} {requirements} {checksums}", env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Expected 1 collection artifact", result.stderr)
