from __future__ import annotations

import http.server
import os
import pathlib
import subprocess
import tempfile
import threading
import unittest

import yaml


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TASKS_PATH = REPO_ROOT / "roles/deploy_websites/tasks/deploy-webhook.yml"
TASKS = yaml.safe_load(TASKS_PATH.read_text(encoding="utf-8"))


def task(name: str) -> dict[str, object]:
    for candidate in TASKS:
        if candidate.get("name") == name:
            return candidate
    raise LookupError(name)


class MetadataHandler(http.server.BaseHTTPRequestHandler):
    status = 200
    body = b'{"actions":["192.0.2.0/24"],"hooks":["198.51.100.0/24"]}'
    requests = 0

    def do_GET(self) -> None:  # noqa: N802
        type(self).requests += 1
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, format: str, *args: object) -> None:
        return


def run_metadata_tasks(status: int) -> tuple[subprocess.CompletedProcess[str], int]:
    MetadataHandler.status = status
    MetadataHandler.requests = 0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MetadataHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    names = (
        "Fetch GitHub network metadata for deployment source allowlist",
        "Retry GitHub network metadata for deployment source allowlist",
        "Retry GitHub network metadata for deployment source allowlist once more",
        "Select latest GitHub network metadata response",
        "Derive GitHub deployment source ranges",
        "Preserve existing GitHub deployment source allowlist when metadata is unavailable",
    )
    playbook = [
        {
            "name": "Exercise GitHub metadata resilience",
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "deploy_websites_repository_deployments": [{}],
                "deploy_websites_github_meta_url": (
                    f"http://127.0.0.1:{server.server_port}/meta"
                ),
            },
            "tasks": [task(name) for name in names],
        }
    ]
    try:
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
            result = subprocess.run(
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
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    return result, MetadataHandler.requests


class GitHubMetadataAllowlistTests(unittest.TestCase):
    def test_all_metadata_attempts_are_nonfatal(self) -> None:
        names = (
            "Fetch GitHub network metadata for deployment source allowlist",
            "Retry GitHub network metadata for deployment source allowlist",
            "Retry GitHub network metadata for deployment source allowlist once more",
        )
        for name in names:
            current = task(name)
            self.assertFalse(current["failed_when"])
            self.assertFalse(current["changed_when"])
            self.assertTrue(current["ansible.builtin.uri"]["return_content"])

    def test_successful_metadata_refresh_requires_usable_ranges(self) -> None:
        derive = task("Derive GitHub deployment source ranges")
        expression = derive["ansible.builtin.set_fact"][
            "deploy_websites_github_deployment_source_ranges"
        ]
        self.assertIn("deploy_websites_github_meta.json.actions", expression)
        self.assertIn("deploy_websites_github_meta.json.hooks", expression)
        self.assertIn("| unique", expression)
        self.assertIn("| sort", expression)

        install = task("Install GitHub deployment source allowlist for nginx")
        conditions = install["when"]
        self.assertIn(
            "deploy_websites_github_meta.status | default(0) == 200",
            conditions,
        )
        self.assertIn(
            "deploy_websites_github_deployment_source_ranges | default([]) | length > 0",
            conditions,
        )

    def test_unavailable_metadata_preserves_existing_allowlist(self) -> None:
        preserve = task(
            "Preserve existing GitHub deployment source allowlist when metadata is unavailable"
        )
        message = preserve["ansible.builtin.debug"]["msg"]
        self.assertIn("preserving", message)
        self.assertIn("existing deployment source allowlist", message)
        condition = " ".join(preserve["when"])
        self.assertIn("status | default(0) != 200", condition)
        self.assertIn("length == 0", condition)

    def test_successful_metadata_response_refresh_path(self) -> None:
        result, requests = run_metadata_tasks(200)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(requests, 1, result.stdout + result.stderr)
        self.assertIn("failed=0", result.stdout)

    def test_metadata_outage_is_nonfatal_after_three_attempts(self) -> None:
        result, requests = run_metadata_tasks(504)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(requests, 3)
        self.assertIn("failed=0", result.stdout)
        self.assertIn("preserving the existing deployment source allowlist", result.stdout)


if __name__ == "__main__":
    unittest.main()
