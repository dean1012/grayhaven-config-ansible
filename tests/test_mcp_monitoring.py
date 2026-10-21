"""MCP alert/probe ownership and existing web monitoring regression checks."""

from __future__ import annotations

import unittest
from unittest import mock

import yaml

from tests.helpers import REPO_ROOT, load_program
from tests.test_alert_sync import alert_sync, complete_config


class MCPMonitoringTests(unittest.TestCase):
    def test_mcp_gets_host_backup_service_health_and_tls_alerts(self):
        config = complete_config()
        defaults = yaml.safe_load(
            (REPO_ROOT / "roles/observability/defaults/main.yml").read_text()
        )
        config["hosts"] = [
            {
                "client": "grayhaven",
                "environment": "prod",
                "role": "mcp",
                "project": "core",
                "control_node": False,
                "fqdn": "grayhaven-core-prod-mcp-01.grayhavensystems.com",
                "short_hostname": "grayhaven-core-prod-mcp-01",
            }
        ]
        config["sites"] = [
            {
                "domain": "mcp.grayhavensystems.com",
                "root_name": "mcp",
                "kind": "application",
                "role": "mcp",
                "display_name": "MCP",
                "health_check": "mcp_health",
                "redirect_http": False,
                "aliases": [],
                "dev_domain": "",
            }
        ]
        config["service_units"]["common"] = defaults[
            "observability_service_units_common"
        ]
        config["service_units"]["mcp"] = defaults["observability_service_units_mcp"]
        # Web load-balancer mode must not disable the independent MCP certificate checks.
        config["tls_mode"] = "load_balancer"
        rules = [
            rule
            for function in (
                alert_sync.service_rules,
                alert_sync.host_metric_rules,
                alert_sync.site_rules,
            )
            for rule in function(config, "folder", "prom")
        ]
        checks = {rule["labels"]["check"] for rule in rules}
        self.assertTrue(
            {
                "mcp_service",
                "mcp_health",
                "nginx_service",
                "certbot_renew_timer",
                "restic_timer",
                "restic_check_timer",
                "https_200",
                "ssl_certificate_expired",
                "ssl_certificate_expiring",
                "ssl_certificate_trusted",
                "cpu_utilization",
            }
            <= checks
        )
        self.assertNotIn("http_https_redirect", checks)
        self.assertNotIn("timetracker_health", checks)
        self.assertTrue(all(rule["labels"]["role"] == "mcp" for rule in rules))
        self.assertTrue(
            all(rule["labels"]["configured_by"] == "ansible" for rule in rules)
        )
        self.assertEqual(len(rules), len({rule["uid"] for rule in rules}))

    def test_mcp_alloy_probes_own_health_and_tls_without_web_or_redirect_targets(self):
        validator = load_program(
            "mcp_alloy_fixture", "scripts/validate-rendered-alloy-config"
        )
        values = validator.fixture_context()
        values.update(grayhaven_role="mcp", observability_role="mcp")
        values["observability_web_sites"].append(
            {
                "root_name": "mcp",
                "kind": "application",
                "role": "mcp",
                "domain": "mcp.example.invalid",
                "aliases": [],
                "dev_domain": "",
                "nginx_log_names": ["access", "error"],
                "redirect_http": False,
                "health_url": "http://127.0.0.1:8787/healthz",
                "health_check": "mcp_health",
            }
        )
        rendered = (
            validator.build_environment()
            .get_template(validator.TEMPLATE_NAME)
            .render(values)
        )
        self.assertIn('address = "http://127.0.0.1:8787/healthz"', rendered)
        self.assertIn('check       = "mcp_health"', rendered)
        self.assertIn('"mcp_cert_trust"', rendered)
        self.assertNotIn('name    = "mcp_redirect_', rendered)
        self.assertNotIn('name    = "timetracker_health"', rendered)
        self.assertNotIn('name    = "grayhavensystems_com_apex_', rendered)
        self.assertIn("grayhaven-mcp", rendered)

    def test_mcp_alloy_validator_cli_selects_mcp_contract(self):
        validator = load_program(
            "mcp_alloy_cli", "scripts/validate-rendered-alloy-config"
        )
        with (
            mock.patch("sys.argv", ["validate-rendered-alloy-config", "--role", "mcp"]),
            mock.patch.object(
                validator.subprocess, "run", return_value=mock.Mock(returncode=0)
            ) as run,
        ):
            self.assertEqual(validator.main(), 0)
        self.assertEqual(run.call_args.args[0][:2], ["alloy", "validate"])
