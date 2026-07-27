from __future__ import annotations

import pathlib
import tempfile
import unittest
from unittest import mock

from tests.helpers import load_program


alerts = load_program(
    "validate_generated_grafana_alerts", "scripts/validate-generated-grafana-alerts"
)
cache = load_program(
    "validate_observability_textfile_cache",
    "scripts/validate-observability-textfile-cache",
)
alloy = load_program(
    "validate_rendered_alloy_config", "scripts/validate-rendered-alloy-config"
)
timetracker = load_program(
    "validate_rendered_timetracker_config",
    "scripts/validate-rendered-timetracker-config",
)


class ValidatorTests(unittest.TestCase):
    def test_generated_alerts_and_cache_contracts(self) -> None:
        config = alerts.fixture_config()
        self.assertEqual(config["tls_mode"], "host")
        self.assertEqual(alerts.main(), 0)
        self.assertEqual(cache.main(), 0)

        original_run_path = alerts.runpy.run_path
        namespace = original_run_path(str(alerts.ALERT_SYNC))
        missing_rules = dict(namespace)
        missing_rules["service_rules"] = lambda *args, **kwargs: []
        with mock.patch.object(alerts.runpy, "run_path", return_value=missing_rules):
            with self.assertRaisesRegex(RuntimeError, "alert contract mismatch"):
                alerts.main()
        original_external = namespace["external_service_rules"]

        def changed_external(*args: object, **kwargs: object) -> list[dict[str, object]]:
            rules = original_external(*args, **kwargs)
            for rule in rules:
                if rule["title"] == "GCS operation telemetry stale":
                    rule["for"] = "0s"
            return rules

        namespace["external_service_rules"] = changed_external
        with (
            mock.patch.object(alerts.runpy, "run_path", return_value=namespace),
            self.assertRaisesRegex(RuntimeError, "must wait"),
        ):
            alerts.main()

    def test_cache_validator_accepts_good_and_rejects_bad_values(self) -> None:
        counts = {"Class A": 11.0, "Class B": 21.0}
        cache.validate_initial_refresh(counts, 1_000, True, 0o600)
        for values in (
            ({"Class A": 0.0, "Class B": 0.0}, 1_000, True, 0o600),
            (counts, 999, True, 0o600),
            (counts, 1_000, False, 0o600),
        ):
            with self.assertRaisesRegex(RuntimeError, "Initial"):
                cache.validate_initial_refresh(*values)
        with self.assertRaisesRegex(RuntimeError, "mode 0600"):
            cache.validate_initial_refresh(counts, 1_000, True, 0o644)

        cache.validate_fresh_reuse((counts, 1_000, True), counts, 1_000, 1)
        for cached, query_count in (
            ((counts, 1_000, False), 1),
            ((counts, 1_000, True), 2),
        ):
            with self.assertRaisesRegex(RuntimeError, "not reused"):
                cache.validate_fresh_reuse(cached, counts, 1_000, query_count)

        refreshed = {"Class A": 12.0, "Class B": 22.0}
        cache.validate_expired_refresh(refreshed, 4_601, True)
        for values in (
            (counts, 4_601, True),
            (refreshed, 4_600, True),
            (refreshed, 4_601, False),
        ):
            with self.assertRaisesRegex(RuntimeError, "not refreshed"):
                cache.validate_expired_refresh(*values)

        for expected_fresh in (True, False):
            cache.validate_failure_fallback(
                (refreshed, 4_601, expected_fresh),
                refreshed,
                4_601,
                expected_fresh=expected_fresh,
            )
            with self.assertRaisesRegex(RuntimeError, "Cached telemetry"):
                cache.validate_failure_fallback(
                    (refreshed, 4_601, not expected_fresh),
                    refreshed,
                    4_601,
                    expected_fresh=expected_fresh,
                )

        cache.validate_bucket_isolation(None)
        with self.assertRaisesRegex(RuntimeError, "different bucket set"):
            cache.validate_bucket_isolation({})

        buckets = {"bucket-a", "bucket-b"}
        valid_filter = [
            'resource.labels.bucket_name = "bucket-a" OR '
            'resource.labels.bucket_name = "bucket-b"'
        ]
        cache.validate_query_scope(valid_filter, buckets)
        for filters in ([], [valid_filter[0], valid_filter[0]], ["bucket-a"]):
            with self.assertRaisesRegex(RuntimeError, "not filtered"):
                cache.validate_query_scope(filters, buckets)

    def test_alloy_render_validation_and_main(self) -> None:
        self.assertEqual(alloy.regex_replace("abc", "b", "x"), "axc")
        environment = alloy.build_environment()
        self.assertIn("regex_replace", environment.filters)
        self.assertEqual(alloy.fixture_context()["grayhaven_environment"], "prod")
        with tempfile.TemporaryDirectory() as temp_dir:
            rendered = pathlib.Path(temp_dir) / "config.alloy"
            alloy.render_config(rendered)
            alloy.validate_timetracker_contract(rendered)
            content = rendered.read_text(encoding="utf-8")
            rendered.write_text(
                content.replace("timetracker_health:", "missing:"), encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "contract is missing"):
                alloy.validate_timetracker_contract(rendered)

        with (
            mock.patch("sys.argv", ["validate-rendered-alloy-config"]),
            mock.patch.object(alloy.subprocess, "run", return_value=mock.Mock(returncode=0)),
        ):
            self.assertEqual(alloy.main(), 0)
        with (
            mock.patch("sys.argv", ["validate-rendered-alloy-config"]),
            mock.patch.object(alloy.subprocess, "run", side_effect=FileNotFoundError),
        ):
            self.assertEqual(alloy.main(), 127)

    def test_timetracker_render_validation_and_main(self) -> None:
        context = timetracker.fixture_context()
        self.assertEqual(context["timetracker_tls_mode"], "host")
        with tempfile.TemporaryDirectory() as temp_dir:
            rendered = timetracker.render_templates(pathlib.Path(temp_dir))
            timetracker.validate(rendered)

            broken = dict(rendered)
            broken["grayhaven-timetracker.conf"] = broken[
                "grayhaven-timetracker.conf"
            ].replace("proxy_pass http://127.0.0.1:8000;", "")
            with self.assertRaisesRegex(RuntimeError, "configuration is missing"):
                timetracker.validate(broken)

            broken = dict(rendered)
            broken["grayhaven-timetracker.conf"] = broken[
                "grayhaven-timetracker.conf"
            ].replace("X-Robots-Tag", "X-Missing", 1)
            with self.assertRaisesRegex(RuntimeError, "both HTTP and HTTPS"):
                timetracker.validate(broken)

            broken = dict(rendered)
            broken["grayhaven-timetracker.container"] += "\nPublishPort=0.0.0.0:8000:8000\n"
            with self.assertRaisesRegex(RuntimeError, "loopback-only"):
                timetracker.validate(broken)

            broken_service = pathlib.Path(temp_dir) / "service.yml"
            broken_service.write_text(
                timetracker.SERVICE_TASKS.read_text(encoding="utf-8").replace(
                    'Host: "{{ timetracker.hostname }}"', ""
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(timetracker, "SERVICE_TASKS", broken_service),
                self.assertRaisesRegex(RuntimeError, "health probe"),
            ):
                timetracker.validate(rendered)

            broken_nginx = pathlib.Path(temp_dir) / "nginx.yml"
            broken_nginx.write_text(
                timetracker.NGINX_TASKS.read_text(encoding="utf-8").replace(
                    "httpd_can_network_connect", "missing_boolean"
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(timetracker, "NGINX_TASKS", broken_nginx),
                self.assertRaisesRegex(RuntimeError, "SELinux policy"),
            ):
                timetracker.validate(rendered)

            with mock.patch(
                "sys.argv",
                ["validate-rendered-timetracker-config", "--keep-rendered", temp_dir],
            ):
                self.assertEqual(timetracker.main(), 0)
        with mock.patch("sys.argv", ["validate-rendered-timetracker-config"]):
            self.assertEqual(timetracker.main(), 0)
