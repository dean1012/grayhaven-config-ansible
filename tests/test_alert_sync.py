from __future__ import annotations

import io
import json
import pathlib
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest import mock

from tests.helpers import load_program


alert_sync = load_program(
    "grayhaven_grafana_alert_sync",
    "roles/observability/files/grayhaven-grafana-alert-sync",
)
validator = load_program(
    "alert_sync_fixture", "scripts/validate-generated-grafana-alerts"
)


class Response:
    def __init__(self, body: str = "{}", status: int = 200) -> None:
        self.body = body.encode()
        self.status = status

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return self.body


def complete_config() -> dict[str, object]:
    config = validator.fixture_config()
    config["grafana"].update(
        {
            "api_token": "token",
            "evaluation_interval": "1m",
            "folder": "Grayhaven Systems LLC",
            "prometheus_datasource_name": "Prometheus",
            "stack_url": "https://grafana.example.invalid",
        }
    )
    return config


class AlertRuleTests(unittest.TestCase):
    def test_helpers_and_rule_generation(self) -> None:
        self.assertEqual(alert_sync.stable_uid("Title"), alert_sync.stable_uid("Title"))
        self.assertEqual(alert_sync.promql_string('a"b\\c'), 'a\\"b\\\\c')
        self.assertIn(r"example\\.invalid", alert_sync.domain_regex(["example.invalid"]))
        self.assertEqual(alert_sync.duration_seconds(5), 5)
        self.assertEqual(alert_sync.duration_seconds("2h"), 7200)
        with self.assertRaises(alert_sync.GrafanaError):
            alert_sync.duration_seconds("invalid")

        query = alert_sync.query_data("A", "prom", "up")
        threshold = alert_sync.threshold_data("lt", 1)
        self.assertEqual(query["model"]["expr"], "up")
        self.assertEqual(threshold["model"]["conditions"][0]["evaluator"]["type"], "lt")
        rule = alert_sync.alert_rule(
            title="Example",
            folder_uid="folder",
            rule_group="group",
            datasource_uid="prom",
            expression="up",
            evaluator_type="lt",
            threshold=1,
            labels={"client": "grayhaven"},
            annotations={"summary": "Example"},
            contact_point="IRM",
        )
        self.assertEqual(rule["labels"][alert_sync.MANAGED_LABEL], alert_sync.MANAGED_VALUE)

        config = complete_config()
        desired = alert_sync.desired_rules(config, "folder", "prom")
        titles = {item["title"] for item in desired.values()}
        self.assertIn("grayhaven-core-prod-web-01 Time Tracker service", titles)
        self.assertIn("Google Monitoring series >= 80% usage", titles)
        self.assertEqual(len(desired), len(set(desired)))
        self.assertTrue(alert_sync.is_managed(next(iter(desired.values())), "grayhaven"))
        self.assertFalse(alert_sync.is_managed({}, "grayhaven"))
        self.assertEqual(
            alert_sync.comparable({**rule, "unmanaged": True}),
            alert_sync.comparable(rule),
        )
        self.assertEqual(
            alert_sync.timestamp(datetime(2026, 1, 1, tzinfo=timezone.utc)),
            "2026-01-01T00:00:00.000Z",
        )

    def test_grafana_client_request_and_helpers(self) -> None:
        client = alert_sync.GrafanaClient("https://grafana.example.invalid/", "token")
        with mock.patch.object(
            alert_sync.urllib.request, "urlopen", return_value=Response('{"uid":"prom"}')
        ):
            self.assertEqual(client.request("GET", "/test"), {"uid": "prom"})
        with mock.patch.object(
            alert_sync.urllib.request, "urlopen", return_value=Response("", 204)
        ):
            self.assertIsNone(client.request("DELETE", "/test", expected=(204,)))

        error = urllib.error.HTTPError("url", 400, "bad", {}, io.BytesIO(b"bad request"))
        with (
            mock.patch.object(alert_sync.urllib.request, "urlopen", side_effect=error),
            self.assertRaisesRegex(alert_sync.GrafanaError, "HTTP 400"),
        ):
            client.request("GET", "/test")
        retry = urllib.error.HTTPError("url", 503, "failed", {}, io.BytesIO(b""))
        with (
            mock.patch.object(alert_sync.urllib.request, "urlopen", side_effect=retry),
            mock.patch.object(alert_sync.time, "sleep"),
            self.assertRaisesRegex(alert_sync.GrafanaError, "retry budget"),
        ):
            client.request("GET", "/test")
        retry = urllib.error.HTTPError(
            "url", 429, "limited", {"Retry-After": "0"}, io.BytesIO(b"")
        )
        with (
            mock.patch.object(
                alert_sync.urllib.request,
                "urlopen",
                side_effect=[retry, Response("{}")],
            ),
            mock.patch.object(alert_sync.time, "sleep"),
        ):
            self.assertEqual(client.request("GET", "/test"), {})
        with (
            mock.patch.object(alert_sync.urllib.request, "urlopen", return_value=Response("{}", 201)),
            self.assertRaisesRegex(alert_sync.GrafanaError, "unexpected HTTP status"),
        ):
            client.request("GET", "/test")

        with mock.patch.object(client, "request") as request:
            request.side_effect = [
                {"uid": "prom"},
                [{"title": "Other", "uid": "one"}, {"title": "Folder", "uid": "two"}],
                [{"uid": "rule"}],
                {"interval": 60},
                {"interval": 30},
                None,
                None,
                None,
                None,
                {"silenceID": "silence"},
            ]
            self.assertEqual(client.datasource_uid("Prometheus Main"), "prom")
            self.assertEqual(client.folder_uid("Folder"), "two")
            self.assertEqual(client.alert_rules(), [{"uid": "rule"}])
            self.assertFalse(client.update_rule_group_interval("folder", "group", 60))
            self.assertTrue(client.update_rule_group_interval("folder", "group", 60))
            client.create_rule({})
            client.update_rule("uid", {})
            client.delete_rule("uid")
            self.assertEqual(client.create_silence({}), "silence")
        with (
            mock.patch.object(client, "request", return_value=[]),
            self.assertRaisesRegex(alert_sync.GrafanaError, "was not found"),
        ):
            client.folder_uid("Missing")
        with (
            mock.patch.object(client, "request", return_value={}),
            self.assertRaisesRegex(alert_sync.GrafanaError, "silence ID"),
        ):
            client.create_silence({})
        with (
            mock.patch.object(client, "request", return_value=[]),
            self.assertRaisesRegex(alert_sync.GrafanaError, "unexpected response"),
        ):
            client.create_silence({})

    def test_rule_generation_boundaries(self) -> None:
        config = complete_config()
        config["hosts"].append(
            {
                **config["hosts"][0],
                "control_node": True,
                "fqdn": "bastion.example.invalid",
                "role": "bastion",
                "short_hostname": "bastion",
            }
        )
        config["service_units"]["common"] = [
            {
                "check": "common_service",
                "name": "common.service",
                "summary": "Common is down",
            }
        ]
        config["service_units"]["control_node"] = [
            {
                "check": "control_service",
                "name": "control.service",
                "summary": "Control is down",
            }
        ]
        config["sites"][0]["dev_domain"] = "dev.example.invalid"
        titles = {
            rule["title"]
            for rule in alert_sync.desired_rules(config, "folder", "prom").values()
        }
        self.assertIn("bastion Control", titles)
        self.assertIn("bastion Ansible convergence", titles)
        self.assertIn("dev.example.invalid basic auth", titles)

        no_sites = complete_config()
        no_sites["sites"] = []
        self.assertEqual(alert_sync.site_rules(no_sites, "folder", "prom"), [])
        no_web = complete_config()
        no_web["hosts"][0]["role"] = "bastion"
        self.assertEqual(alert_sync.site_rules(no_web, "folder", "prom"), [])

        load_balancer = complete_config()
        load_balancer["tls_mode"] = "load_balancer"
        titles = {
            rule["title"]
            for rule in alert_sync.site_rules(load_balancer, "folder", "prom")
        }
        self.assertNotIn(
            "timetracker.grayhavensystems.com SSL certificate expired", titles
        )

    def test_sync_silence_and_main(self) -> None:
        config = complete_config()
        desired = alert_sync.desired_rules(config, "folder", "prom")
        first_uid, first_rule = next(iter(desired.items()))
        changed_existing = json.loads(json.dumps(first_rule))
        changed_existing["for"] = "99m"
        stale = {
            "uid": "stale",
            "labels": {"configured_by": "ansible", "client": "grayhaven"},
        }

        fake = mock.Mock()
        fake.datasource_uid.return_value = "prom"
        fake.folder_uid.return_value = "folder"
        fake.alert_rules.return_value = [changed_existing, stale, {"uid": "foreign"}]
        fake.update_rule_group_interval.return_value = True
        with mock.patch.object(alert_sync, "GrafanaClient", return_value=fake):
            created, updated, deleted = alert_sync.sync(config)
        self.assertEqual(created, len(desired) - 1)
        self.assertEqual(updated, 2)
        self.assertEqual(deleted, 1)
        fake.update_rule.assert_called_once_with(first_uid, first_rule)
        fake.delete_rule.assert_called_once_with("stale")

        with self.assertRaises(alert_sync.GrafanaError):
            alert_sync.create_initial_silence(config, 0)
        fake = mock.Mock()
        fake.create_silence.return_value = "silence"
        with mock.patch.object(alert_sync, "GrafanaClient", return_value=fake):
            self.assertEqual(alert_sync.create_initial_silence(config, 15), "silence")
        payload = fake.create_silence.call_args.args[0]
        self.assertEqual(payload["matchers"][0]["name"], "configured_by")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with (
                mock.patch("sys.argv", ["sync", "--config", str(path)]),
                mock.patch.object(alert_sync, "sync", return_value=(1, 2, 3)),
                mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
            ):
                self.assertEqual(alert_sync.main(), 0)
            self.assertIn("changed=true created=1 updated=2 deleted=3", stdout.getvalue())
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "sync",
                        "--config",
                        str(path),
                        "--create-initial-silence",
                        "--silence-minutes",
                        "10",
                    ],
                ),
                mock.patch.object(alert_sync, "create_initial_silence", return_value="id"),
                mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
            ):
                self.assertEqual(alert_sync.main(), 0)
            self.assertIn("silence_id=id", stdout.getvalue())
