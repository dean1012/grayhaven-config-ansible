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
    def test_repository_config_uses_one_commit_and_existing_token(self):
        tasks = yaml.safe_load((ROLE / "tasks/host.yml").read_text())
        resolve = next(t for t in tasks if t["name"] == "Resolve the MCP configuration main commit")
        prepare = next(t for t in tasks if t["name"] == "Prepare and validate MCP configuration before installation")
        download = prepare["block"][0]
        self.assertEqual(download["loop"], ["config.json", "config.secrets.enc"])
        self.assertTrue(resolve["no_log"])
        self.assertTrue(download["no_log"])
        self.assertEqual(download["ansible.builtin.uri"]["headers"]["Authorization"],
                         "Bearer {{ mcp_secrets.registry_token }}")
        self.assertEqual(download["ansible.builtin.uri"]["unredirected_headers"], ["Authorization"])
        defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
        for environment, directory in (("staging", "staging"), ("prod", "production"), ("production", "production")):
            selected = Environment(undefined=StrictUndefined).from_string(
                defaults["mcp_configuration_environment"]
            ).render(baseline_validation_environment=environment)
            for filename in download["loop"]:
                url = Environment(undefined=StrictUndefined).from_string(download["ansible.builtin.uri"]["url"]).render(
                    mcp_configuration_api_url=defaults["mcp_configuration_api_url"],
                    mcp_configuration_environment=selected, item=filename,
                    mcp_configuration_commit={"json": {"sha": "a" * 40}},
                )
                self.assertEqual(url, f"https://api.github.com/repos/dean1012/grayhaven-mcp/contents/{directory}/{filename}?ref=" + "a" * 40)
        names = [task["name"] for task in prepare["block"]]
        self.assertLess(names.index("Validate both candidate MCP configuration files using the approved image"),
                        names.index("Persist validated MCP configuration"))
        self.assertEqual(prepare["always"][0]["ansible.builtin.file"]["state"], "absent")
        validation = next(t for t in prepare["block"] if t["name"].startswith("Validate both candidate"))
        self.assertIn("--network=none", validation["ansible.builtin.command"]["argv"])
        self.assertTrue(validation["no_log"])
        install = prepare["block"][-1]
        self.assertTrue(install["ansible.builtin.copy"]["remote_src"])
        self.assertEqual(install["notify"], "Restart MCP")

    def test_candidate_failure_preserves_installed_files_and_always_cleans_up(self):
        import grp
        import pwd
        import sys

        original = yaml.safe_load((ROLE / "tasks/host.yml").read_text())
        prepare = next(t for t in original if t["name"] == "Prepare and validate MCP configuration before installation")
        for outcome in ("success", "download_failure", "wrong_environment", "validation_failure"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                installed, credentials, candidate = (root / name for name in ("installed", "credentials", "candidate"))
                for path in (installed, credentials, candidate):
                    path.mkdir()
                for filename in ("config.json", "config.secrets.enc"):
                    (installed / filename).write_text("original")
                (credentials / "config_passphrase").write_text("original-password")
                tasks = copy.deepcopy(prepare)
                for task in tasks["block"]:
                    task.pop("notify", None)
                if outcome == "download_failure":
                    tasks["block"][0] = {"name": "Simulate failed authenticated download", "ansible.builtin.fail": {"msg": "fixture download failed"}}
                else:
                    source = {"deployment_environment": "production" if outcome == "wrong_environment" else "staging"}
                    tasks["block"][0] = {"name": "Supply downloaded fixture files", "ansible.builtin.set_fact": {
                        "mcp_repository_files": {"results": [{"content": json.dumps(source)}, {"content": "fixture-envelope"}]}
                    }}
                for task in tasks["block"]:
                    if task["name"] == "Prepare the candidate MCP public configuration":
                        task["ansible.builtin.template"]["src"] = str(ROLE / "templates/config.json.j2")
                    if task["name"].startswith("Validate both candidate"):
                        task["ansible.builtin.command"]["argv"] = [sys.executable, "-c", "raise SystemExit(" + ("1" if outcome == "validation_failure" else "0") + ")"]
                values = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
                values.update(
                    mcp={"hostname": "mcp.example.invalid", "config_dir": str(installed), "secrets_dir": str(credentials),
                         "data_dir": str(root / "data"), "sessions_dir": str(root / "sessions"), "logs_dir": str(root / "logs")},
                    mcp_configuration_temporary={"path": str(candidate)},
                    mcp_secrets={"config_passphrase": "new-password"},
                    baseline_validation_environment="staging",
                    mcp_user=pwd.getpwuid(os.getuid()).pw_name,
                    mcp_group=grp.getgrgid(os.getgid()).gr_name,
                )
                # The fixture runs as the local operator, without managed-host privilege changes.
                for task in tasks["block"]:
                    for module in ("ansible.builtin.copy", "ansible.builtin.template"):
                        if module in task:
                            task[module]["owner"] = values["mcp_user"]
                            task[module]["group"] = values["mcp_group"]
                result = run_playbook([{"name": "Exercise configuration installation", "vars": values, "block": [tasks]}])
                self.assertEqual(result.returncode == 0, outcome == "success", result.stdout + result.stderr)
                self.assertFalse(candidate.exists())
                if outcome == "success":
                    self.assertEqual(json.loads((installed / "config.json").read_text())["deployment_environment"], "staging")
                    self.assertEqual((installed / "config.secrets.enc").read_text(), "fixture-envelope")
                    self.assertEqual((credentials / "config_passphrase").read_text(), "new-password")
                else:
                    for filename in ("config.json", "config.secrets.enc"):
                        self.assertEqual((installed / filename).read_text(), "original")
                    self.assertEqual((credentials / "config_passphrase").read_text(), "original-password")

    def test_known_hosts_use_only_managed_environment_facts(self):
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
        self.assertNotIn("production.example.invalid", output)
        self.assertIn("bastion.staging.example.invalid ssh-ed25519 bastion-key", output)
        self.assertIn("10.20.0.3 ssh-ed25519 web-key", output)
        self.assertEqual(output.count("ssh-ed25519"), 2)

    def test_mcp_always_uses_production_acme_even_in_staging(self):
        import ast

        tasks = yaml.safe_load((ROLE / "tasks/certificate.yml").read_text())
        issue = next(task for task in tasks if task["name"] == "Issue MCP certificate when required")
        for global_environment in ("staging", "production"):
            values = {
                "mcp": {"hostname": "mcp.example.invalid", "certificate_environment": "staging"},
                "baseline_validation_certificate_environment": global_environment,
                "deploy_websites_certbot_email": "fixture@example.invalid",
                "mcp_certbot_credentials_file": "/fixture/credentials",
            }
            output = Environment(undefined=StrictUndefined).from_string(issue["vars"]["mcp_certbot_command"]).render(**values)
            command = ast.literal_eval(output)
            self.assertNotIn("--test-cert", command)
            self.assertEqual(command[command.index("--server") + 1], "https://acme-v02.api.letsencrypt.org/directory")
        marker = next(task for task in tasks if task["name"] == "Record MCP certificate environment")
        self.assertEqual(marker["ansible.builtin.copy"]["content"], "production\n")
        self.assertNotIn("mcp_certificate_environment", yaml.safe_load((ROLE / "defaults/main.yml").read_text()))

    def test_required_deployment_contract_accepts_only_complete_inputs(self):
        task = yaml.safe_load((ROLE / "tasks/host.yml").read_text())[0]
        valid = {
            "mcp": {
                "hostname": "mcp.staging.grayhavensystems.com",
                "image_digest": "sha256:" + "a" * 64,
                **{key + "_dir": "/var/lib/grayhaven/mcp/" + key for key in ("data", "config", "secrets", "sessions", "logs", "backup")},
            },
            "mcp_secrets": {
                "config_passphrase": "fixture-password",
                "registry_username": "fixture",
                "registry_token": "fixture",
            },
            "baseline_validation_environment": "staging",
            "deploy_websites_certbot_email": "fixture@example.invalid",
            "digitalocean_dns_api_token": "fixture",
        }
        cases = [("valid", valid, True)]
        for key, value in [
            ("image_digest", "latest"),
            ("hostname", "https://invalid"),
            ("data_dir", "/"),
            ("config_dir", "relative/path"),
            ("secrets_dir", "/var/../etc"),
            ("sessions_dir", "/var/./sessions"),
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
            mcp={"hostname": "mcp.staging.grayhavensystems.com", **{key + "_dir": "/var/lib/grayhaven/mcp/" + key for key in ("data", "config", "secrets", "sessions", "logs", "backup")}},
            baseline_validation_environment="staging",
            mcp_config_overrides={
                "polling_seconds": {"mail": 45},
                "secret_passphrase_path": "/wrong",
            },
            mcp_repository_configuration={
                "proton": {"profile": "grayhaven-mail"},
                "polling_seconds": {"mail": 30},
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
        self.assertEqual(config["deployment_environment"], "staging")
        values["baseline_validation_environment"] = "prod"
        self.assertEqual(json.loads(render(ROLE / "templates/config.json.j2", values))["deployment_environment"], "production")
        self.assertEqual(
            config["transport"]["public_base_url"],
            "https://mcp.staging.grayhavensystems.com",
        )

    def test_custom_paths_are_used_for_config_mounts_and_backups(self):
        mcp = {key + "_dir": "/srv/mcp/" + key for key in ("data", "config", "secrets", "sessions", "logs", "backup")}
        mcp.update(hostname="mcp.example.invalid", image_digest="sha256:" + "a" * 64)
        values = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
        values.update(mcp=mcp, baseline_validation_environment="staging", baseline_validation_timezone="America/Chicago", mcp_repository_configuration={})
        config = json.loads(render(ROLE / "templates/config.json.j2", values))
        self.assertEqual(config["state_database_path"], "/srv/mcp/data/state.sqlite3")
        self.assertEqual(config["secret_passphrase_path"], "/srv/mcp/secrets/config_passphrase")
        self.assertEqual(config["system_error_logpath"], "/srv/mcp/logs")
        service = render(ROLE / "templates/grayhaven-mcp.container.j2", values)
        self.assertIn("Environment=GRAYHAVEN_MCP_ENVIRONMENT=staging", service)
        production = render(ROLE / "templates/grayhaven-mcp.container.j2", {**values, "baseline_validation_environment": "prod"})
        self.assertIn("Environment=GRAYHAVEN_MCP_ENVIRONMENT=production", production)
        for key in ("data", "config", "secrets", "sessions", "logs", "backup"):
            path = mcp[key + "_dir"]
            self.assertIn("Volume=" + path + ":" + path + ":Z", service)
        self.assertIn("GRAYHAVEN_MCP_CONFIG=/srv/mcp/config/config.json", service)
        settings = yaml.safe_load((REPO_ROOT / "roles/restic_backup/tasks/settings.yml").read_text())
        values.update(grayhaven_role="mcp", restic_backup_homedir_archive_path="/archive", backup={"include": []})
        expression = settings[2]["ansible.builtin.set_fact"]["restic_backup_include_paths"].replace("| unique", "| unique | list")
        included = Environment(undefined=StrictUndefined).from_string(expression).render(**values)
        for key in ("data", "config", "secrets", "sessions", "logs", "backup"):
            self.assertIn(mcp[key + "_dir"], included)

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
