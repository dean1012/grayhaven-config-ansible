from __future__ import annotations

import base64
import os
import pathlib
import subprocess
import tempfile
import unittest

import yaml


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TASKS_PATH = REPO_ROOT / "roles/backupctl/tasks/main.yml"
DEFAULTS_PATH = REPO_ROOT / "roles/backupctl/defaults/main.yml"
RESTIC_SETTINGS_PATH = REPO_ROOT / "roles/restic_backup/tasks/settings.yml"
TASKS = yaml.safe_load(TASKS_PATH.read_text(encoding="utf-8"))
DEFAULTS = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))


def find_task(name: str, tasks: list[dict[str, object]] | None = None) -> dict[str, object]:
    """Return one named task, including tasks nested in a block."""
    for task in tasks or TASKS:
        if task.get("name") == name:
            return task
        block = task.get("block")
        if isinstance(block, list):
            try:
                return find_task(name, block)
            except LookupError:
                pass
    raise LookupError(name)


def run_playbook(tasks: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
    """Run a deterministic localhost-only playbook containing supplied tasks."""
    playbook = [
        {
            "name": "Exercise Backupctl contract",
            "hosts": "localhost",
            "gather_facts": False,
            "tasks": tasks,
        }
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        path = root / "playbook.yml"
        path.write_text(yaml.safe_dump(playbook, sort_keys=False), encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "ANSIBLE_LOCAL_TEMP": str(root / "local"),
                "ANSIBLE_REMOTE_TEMP": str(root / "remote"),
                "ANSIBLE_NOCOLOR": "1",
            }
        )
        return subprocess.run(
            [
                "ansible-playbook",
                "--inventory",
                "localhost,",
                "--connection=local",
                str(path),
            ],
            cwd=REPO_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )


class BackupctlContractTests(unittest.TestCase):
    def test_defaults_use_exact_nested_release_contract(self) -> None:
        self.assertNotIn("backupctl_repo_url", DEFAULTS)
        self.assertNotIn("backupctl_repo_ref", DEFAULTS)
        self.assertNotIn("backupctl_checkout_dir", DEFAULTS)
        self.assertEqual(
            DEFAULTS["grayhaven_backupctl"],
            {
                "repo_url": "https://github.com/dean1012/grayhaven-backupctl.git",
                "checkout_dir": "/home/ansible/grayhaven-backupctl",
                "version": "207a02af4921080efbc46950a14961874952bf92",
            },
        )

    def test_schema_assertions_accept_only_the_exact_nested_shape(self) -> None:
        legacy = find_task("Reject obsolete grayhaven-backupctl selectors")
        contract = find_task("Validate grayhaven-backupctl configuration")
        legacy_assertions = legacy["ansible.builtin.assert"]["that"]
        contract_assertions = contract["ansible.builtin.assert"]["that"]
        valid = {
            "grayhaven_backupctl": {
                "repo_url": "file:///tmp/backupctl.git",
                "checkout_dir": "/home/ansible/backupctl-test",
                "version": "a" * 40,
            }
        }
        cases = [
            ("valid", valid, True),
            (
                "legacy-flat",
                {**valid, "backupctl_repo_ref": "main"},
                False,
            ),
            (
                "extra-key",
                {
                    "grayhaven_backupctl": {
                        **valid["grayhaven_backupctl"],
                        "branch": "main",
                    }
                },
                False,
            ),
            (
                "moving-version",
                {
                    "grayhaven_backupctl": {
                        **valid["grayhaven_backupctl"],
                        "version": "main",
                    }
                },
                False,
            ),
            (
                "uppercase-version",
                {
                    "grayhaven_backupctl": {
                        **valid["grayhaven_backupctl"],
                        "version": "A" * 40,
                    }
                },
                False,
            ),
            (
                "unsafe-checkout",
                {
                    "grayhaven_backupctl": {
                        **valid["grayhaven_backupctl"],
                        "checkout_dir": "/tmp/backupctl",
                    }
                },
                False,
            ),
        ]
        tasks: list[dict[str, object]] = []
        for name, variables, expected in cases:
            tasks.append(
                {
                    "name": f"Evaluate {name}",
                    "ansible.builtin.assert": {
                        "that": legacy_assertions + contract_assertions,
                        "quiet": True,
                    },
                    "vars": variables,
                    "ignore_errors": not expected,
                    "register": f"backupctl_case_{name.replace('-', '_')}",
                }
            )
            tasks.append(
                {
                    "name": f"Require {name} result",
                    "ansible.builtin.assert": {
                        "that": [
                            f"backupctl_case_{name.replace('-', '_')} is "
                            + ("succeeded" if expected else "failed")
                        ],
                        "quiet": True,
                    },
                }
            )
        result = run_playbook(tasks)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_install_decision_handles_missing_upgrade_downgrade_and_repeat(self) -> None:
        decision = find_task(
            "Decide whether grayhaven-backupctl installation is required"
        )["ansible.builtin.set_fact"]["backupctl_install_required"]
        expected_identity = "grayhaven-backupctl 1.0.4-207a02a"
        cases = [
            ("unchanged", {"rc": 0, "stdout_lines": [expected_identity], "stderr": ""}, False),
            ("missing", {"rc": 2, "stdout_lines": [], "stderr": ""}, True),
            ("upgrade", {"rc": 0, "stdout_lines": ["grayhaven-backupctl 1.0.3-old"], "stderr": ""}, True),
            ("downgrade", {"rc": 0, "stdout_lines": ["grayhaven-backupctl 2.0.0-new"], "stderr": ""}, True),
            ("extra-output", {"rc": 0, "stdout_lines": [expected_identity, "extra"], "stderr": ""}, True),
            ("stderr", {"rc": 0, "stdout_lines": [expected_identity], "stderr": "warning"}, True),
        ]
        tasks: list[dict[str, object]] = []
        for name, installed, expected in cases:
            fact_name = f"backupctl_required_{name.replace('-', '_')}"
            tasks.extend(
                [
                    {
                        "name": f"Decide {name}",
                        "ansible.builtin.set_fact": {fact_name: decision},
                        "vars": {
                            "backupctl_installed_version": installed,
                            "backupctl_expected_version_output": expected_identity,
                        },
                    },
                    {
                        "name": f"Require {name} decision",
                        "ansible.builtin.assert": {
                            "that": [f"{fact_name} == {str(expected).lower()}"],
                            "quiet": True,
                        },
                    },
                ]
            )
        result = run_playbook(tasks)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_release_metadata_derives_exact_runtime_identity(self) -> None:
        derive_version = find_task("Derive checked-out grayhaven-backupctl version")
        validate_version = find_task("Validate checked-out grayhaven-backupctl version")
        derive_identity = find_task("Derive requested grayhaven-backupctl identity")
        result = run_playbook(
            [
                {
                    "name": "Derive canonical version",
                    "ansible.builtin.set_fact": derive_version[
                        "ansible.builtin.set_fact"
                    ],
                    "vars": {
                        "backupctl_checkout_version": {
                            "content": base64.b64encode(b"1.0.4\n").decode("ascii")
                        }
                    },
                },
                {
                    "name": "Validate canonical version",
                    "ansible.builtin.assert": validate_version[
                        "ansible.builtin.assert"
                    ],
                },
                {
                    "name": "Derive requested identity",
                    "ansible.builtin.set_fact": derive_identity[
                        "ansible.builtin.set_fact"
                    ],
                    "vars": {
                        "grayhaven_backupctl": {
                            "version": "207a02af4921080efbc46950a14961874952bf92"
                        }
                    },
                },
                {
                    "name": "Require exact requested identity",
                    "ansible.builtin.assert": {
                        "that": [
                            "backupctl_expected_version_output == "
                            "'grayhaven-backupctl 1.0.4-207a02a'"
                        ],
                        "quiet": True,
                    },
                },
            ]
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_checkout_metadata_runtime_and_idempotence_contract(self) -> None:
        checkout = find_task("Check out exact grayhaven-backupctl commit")
        self.assertEqual(
            checkout["ansible.builtin.git"]["version"],
            "{{ grayhaven_backupctl.version }}",
        )
        head = find_task("Read checked-out grayhaven-backupctl commit")
        self.assertEqual(
            head["ansible.builtin.command"]["argv"][-2:],
            ["rev-parse", "HEAD"],
        )
        verify = find_task("Verify checked-out grayhaven-backupctl commit")
        self.assertIn(
            "backupctl_checkout_head.stdout | trim == grayhaven_backupctl.version",
            verify["ansible.builtin.assert"]["that"],
        )

        version_copy = find_task("Install grayhaven-backupctl version metadata")
        self.assertEqual(
            version_copy["ansible.builtin.copy"]["dest"],
            "{{ backupctl_libexec_dir }}/VERSION",
        )
        commit_copy = find_task("Install grayhaven-backupctl commit metadata")
        self.assertEqual(
            commit_copy["ansible.builtin.copy"]["dest"],
            "{{ backupctl_libexec_dir }}/COMMIT_SHA",
        )
        self.assertEqual(
            commit_copy["ansible.builtin.copy"]["content"],
            "{{ grayhaven_backupctl.version }}\n",
        )

        identity = find_task("Derive requested grayhaven-backupctl identity")
        expression = identity["ansible.builtin.set_fact"][
            "backupctl_expected_version_output"
        ]
        self.assertIn("grayhaven-backupctl", expression)
        self.assertIn("grayhaven_backupctl.version[:7]", expression)

        install = find_task("Install requested grayhaven-backupctl runtime")
        self.assertEqual(install["when"], "backupctl_install_required | bool")
        top_level_names = [task["name"] for task in TASKS]
        self.assertGreater(
            top_level_names.index("Install grayhaven-backupctl command wrapper"),
            top_level_names.index("Install requested grayhaven-backupctl runtime"),
        )
        self.assertIn("Verify installed grayhaven-backupctl identity", top_level_names)

    def test_restic_exclusion_uses_nested_checkout(self) -> None:
        text = RESTIC_SETTINGS_PATH.read_text(encoding="utf-8")
        self.assertIn("grayhaven_backupctl.checkout_dir ~ '/.git'", text)
        self.assertNotIn("backupctl_checkout_dir", text)


if __name__ == "__main__":
    unittest.main()
