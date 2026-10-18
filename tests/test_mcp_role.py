"""Offline MCP deployment contracts; no provider or managed host access."""

from __future__ import annotations

import copy
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

from ansible.plugins.filter.core import combine, to_nice_json
from jinja2 import Environment, StrictUndefined
import yaml

from tests.helpers import REPO_ROOT
from tests.test_backupctl import run_playbook


ROLE = REPO_ROOT / "roles/mcp"


def render(path, values):
    environment = Environment(undefined=StrictUndefined)
    environment.filters.update(
        from_json=json.loads, to_nice_json=to_nice_json, combine=combine
    )
    return environment.from_string(path.read_text()).render(**values)


class MCPRoleTests(unittest.TestCase):
    def test_known_hosts_preserve_external_keys_and_derive_managed_environment(self):
        hosts = [
            {
                "inventory_name": "bastion",
                "fqdn": "grayhaven-sec-staging-bastion-01.staging.example.invalid",
                "host_alias": "grayhaven-sec-staging-bastion-01",
                "private_ip": "10.20.0.2",
                "role": "bastion",
            },
            {
                "inventory_name": "web",
                "fqdn": "grayhaven-core-staging-web-01.staging.example.invalid",
                "host_alias": "grayhaven-core-staging-web-01",
                "private_ip": "10.20.0.3",
                "role": "web",
            },
            {"inventory_name": "mcp", "role": "mcp"},
        ]
        output = render(
            ROLE / "templates/known_hosts.j2",
            {
                "mcp": {
                    "known_hosts": "production.example.invalid ssh-ed25519 external-key"
                },
                "managed_inventory_hosts": hosts,
                "hostvars": {
                    name: {"ansible_facts": {"ssh_host_key_ed25519_public": key}}
                    for name, key in [("bastion", "bastion-key"), ("web", "web-key")]
                },
            },
        )
        self.assertIn("production.example.invalid ssh-ed25519 external-key", output)
        self.assertIn("bastion.staging.example.invalid ssh-ed25519 bastion-key", output)
        self.assertIn("10.20.0.3 ssh-ed25519 web-key", output)
        self.assertEqual(output.count("ssh-ed25519"), 3)

    def test_certificate_environment_uses_mcp_override_or_baseline(self):
        expression = yaml.safe_load((ROLE / "defaults/main.yml").read_text())[
            "mcp_certificate_environment"
        ]
        environment = Environment(undefined=StrictUndefined)
        template = environment.from_string(expression)
        for values, expected in [
            ({"mcp": {}}, "staging"),
            (
                {
                    "mcp": {},
                    "baseline_validation_certificate_environment": "production",
                },
                "production",
            ),
            (
                {
                    "mcp": {"certificate_environment": "production"},
                    "baseline_validation_certificate_environment": "staging",
                },
                "production",
            ),
        ]:
            with self.subTest(values=values):
                self.assertEqual(template.render(**values), expected)

    def test_required_deployment_contract_accepts_only_complete_inputs(self):
        task = yaml.safe_load((ROLE / "tasks/host.yml").read_text())[0]
        valid = {
            "mcp": {
                "hostname": "mcp.staging.grayhavensystems.com",
                "image_digest": "sha256:" + "a" * 64,
                "known_hosts": "verified-key",
            },
            "mcp_secrets": {
                "config_passphrase": "fixture-password",
                "registry_username": "fixture",
                "registry_token": "fixture",
            },
            "mcp_certificate_environment": "staging",
            "deploy_websites_certbot_email": "fixture@example.invalid",
            "digitalocean_dns_api_token": "fixture",
        }
        cases = [("valid", valid, True)]
        for key, value in [
            ("image_digest", "latest"),
            ("hostname", "https://invalid"),
            ("known_hosts", ""),
            ("backup_retention_days", 0),
            ("backup_retention_days", 366),
        ]:
            changed = copy.deepcopy(valid)
            changed["mcp"][key] = value
            cases.append((key, changed, False))
        for name, values, expected in cases:
            with self.subTest(name=name):
                tested = {**copy.deepcopy(task), "vars": values}
                outcome = run_playbook([tested])
                self.assertEqual(
                    outcome.returncode == 0, expected, outcome.stdout + outcome.stderr
                )

    def test_public_config_preserves_settings_and_enforces_persistent_paths(self):
        values = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
        values.update(
            mcp={"hostname": "mcp.staging.grayhavensystems.com"},
            mcp_config_overrides={
                "polling_seconds": {"mail": 45},
                "secret_passphrase_path": "/wrong",
            },
            mcp_image_configuration={
                "stdout": json.dumps(
                    {
                        "proton": {"profile": "grayhaven-mail"},
                        "polling_seconds": {"mail": 30},
                    }
                )
            },
        )
        config = json.loads(render(ROLE / "templates/config.json.j2", values))
        self.assertEqual(
            config["secret_passphrase_path"],
            "/var/lib/grayhaven/mcp/secrets/config_passphrase",
        )
        self.assertEqual(config["polling_seconds"]["mail"], 45)
        self.assertEqual(config["proton"]["profile"], "grayhaven-mail")
        self.assertEqual(
            config["proton"]["state_dir"], "/var/lib/grayhaven/mcp/sessions/proton"
        )
        self.assertEqual(
            config["notesnook"]["state_dir"],
            "/var/lib/grayhaven/mcp/sessions/notesnook",
        )
        self.assertEqual(config["ssh"]["binary"], "/usr/bin/ssh")
        self.assertEqual(
            config["transport"]["public_base_url"],
            "https://mcp.staging.grayhavensystems.com",
        )

    def test_firewall_limits_mcp_to_bastion_without_changing_web_sources(self):
        template = (
            REPO_ROOT
            / "roles/firewalld_policy/templates/grayhaven-firewalld-zone.xml.j2"
        )
        values = {
            "firewalld_policy_role_policy": {
                "inbound": [
                    {"protocol": "tcp", "port_range": "22", "source_tags": ["bastion"]}
                ]
            },
            "managed_inventory_bastion_private_sources": ["10.20.0.2"],
            "managed_inventory_web_ssh_sources": ["10.20.0.0/20", "10.20.0.2"],
        }
        mcp = render(template, {**values, "grayhaven_role": "mcp"})
        web = render(template, {**values, "grayhaven_role": "web"})
        self.assertIn('address="10.20.0.2"', mcp)
        self.assertNotIn('address="10.20.0.0/20"', mcp)
        self.assertIn('address="10.20.0.0/20"', web)

    def test_proxy_is_https_only_and_supports_event_streaming(self):
        proxy = render(
            ROLE / "templates/grayhaven-mcp.conf.j2",
            {"mcp": {"hostname": "mcp.example.invalid"}, "mcp_host_port": 8787},
        )
        self.assertIn("listen 443 ssl;", proxy)
        self.assertNotIn("listen 80", proxy)
        self.assertIn("proxy_buffering off;", proxy)
        self.assertIn("access_log off;", proxy)

    def test_backup_snapshot_is_required_and_failed_output_is_not_published(self):
        tasks = yaml.safe_load(
            (REPO_ROOT / "roles/restic_backup/tasks/local.yml").read_text()
        )
        script = next(
            t["ansible.builtin.copy"]["content"]
            for t in tasks
            if t.get("name") == "Install local restic backup script"
        )
        functions = script[
            script.index("create_mcp_backup() {") : script.index(
                "create_timetracker_backup() {"
            )
        ]
        for enabled, active, provider, expected in [
            ("false", 0, 0, 0),
            ("true", 1, 0, 1),
            ("true", 0, 1, 1),
            ("true", 0, 0, 0),
        ]:
            with (
                self.subTest(enabled=enabled, active=active, provider=provider),
                tempfile.TemporaryDirectory() as folder,
            ):
                source = (
                    functions
                    + """
    systemctl() { return "$FIXTURE_ACTIVE"; }
    podman() {
      printf 'encrypted fixture' > "${@: -1}"
      return "$FIXTURE_PROVIDER"
    }
    create_mcp_backup
    prune_mcp_backups
    """
                )
                env = {
                    **os.environ,
                    "MCP_BACKUP_ENABLED": enabled,
                    "MCP_BACKUP_DIR": folder,
                    "MCP_BACKUP_RETENTION_DAYS": "14",
                    "FIXTURE_ACTIVE": str(active),
                    "FIXTURE_PROVIDER": str(provider),
                }
                result = subprocess.run(
                    ["bash", "-euo", "pipefail", "-c", source],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, expected, result.stderr)
                files = list(pathlib.Path(folder).iterdir())
                self.assertEqual(
                    len(files), int(enabled == "true" and active == 0 and provider == 0)
                )
                self.assertTrue(
                    all(
                        f.name.startswith("mcp-") and f.suffix == ".sqlite3"
                        for f in files
                    )
                )
