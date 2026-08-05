from __future__ import annotations

import io
import json
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
    @staticmethod
    def changed_rule_namespace(
        generator: str,
        predicate: object,
        mutation: object,
    ) -> dict[str, object]:
        namespace = alerts.runpy.run_path(str(alerts.ALERT_SYNC))
        original = namespace[generator]

        def changed(*args: object, **kwargs: object) -> list[dict[str, object]]:
            rules = original(*args, **kwargs)
            for rule in rules:
                if predicate(rule):
                    mutation(rule)
            return rules

        namespace[generator] = changed
        return namespace

    def test_generated_alerts_and_cache_contracts(self) -> None:
        config = alerts.fixture_config()
        self.assertEqual(config["tls_mode"], "host")
        self.assertEqual(alerts.main([]), 0)
        self.assertEqual(cache.main(), 0)

        original_run_path = alerts.runpy.run_path
        namespace = original_run_path(str(alerts.ALERT_SYNC))
        missing_rules = dict(namespace)
        missing_rules["service_rules"] = lambda *args, **kwargs: []
        with mock.patch.object(alerts.runpy, "run_path", return_value=missing_rules):
            with self.assertRaisesRegex(RuntimeError, "alert contract mismatch"):
                alerts.main([])
        original_external = namespace["external_service_rules"]

        def changed_external(
            *args: object, **kwargs: object
        ) -> list[dict[str, object]]:
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
            alerts.main([])

    def test_live_uid_registry_validation(self) -> None:
        registry = json.loads(alerts.UID_REGISTRY.read_text(encoding="utf-8"))
        namespace = alerts.runpy.run_path(str(alerts.ALERT_SYNC))
        live_rules = []
        for identity, uid in registry.items():
            scope, resource, check = identity.split(":", 2)
            live_rules.append(
                {
                    "uid": uid,
                    "labels": {
                        "configured_by": "ansible",
                        scope: resource,
                        "check": check,
                    },
                }
            )
        alerts.validate_live_registry(namespace, registry, live_rules)
        self.assertEqual(
            alerts.imported_identity({"labels": {"host": "host", "check": "check"}}),
            "host:host:check",
        )
        self.assertEqual(
            alerts.imported_identity(
                {"labels": {"domain": "example.invalid", "check": "check"}}
            ),
            "domain:example.invalid:check",
        )
        self.assertEqual(
            alerts.imported_identity(
                {"labels": {"service": "service", "check": "check"}}
            ),
            "service:service:check",
        )
        self.assertEqual(
            alerts.imported_identity({"labels": {"check": "check"}}),
            "check:check",
        )

        with self.assertRaisesRegex(RuntimeError, "count does not match"):
            alerts.validate_live_registry(namespace, registry, live_rules[:-1])
        with self.assertRaisesRegex(RuntimeError, "count does not match"):
            alerts.validate_live_registry(
                namespace,
                registry,
                live_rules
                + [
                    {
                        "uid": "gh-000000000000000000000000",
                        "labels": {
                            "configured_by": "ansible",
                            "service": "unexpected",
                            "check": "created",
                        },
                    }
                ],
            )
        live_rules[0]["uid"] = "wrong"
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            alerts.validate_live_registry(namespace, registry, live_rules)

        live_rules = self.live_rules(registry)
        identity = next(iter(registry))
        invalid_registry = dict(registry)
        invalid_registry[identity] = "invalid"
        live_rules[0]["uid"] = "invalid"
        with self.assertRaisesRegex(RuntimeError, "invalid UID"):
            alerts.validate_live_registry(namespace, invalid_registry, live_rules)

        identities = list(registry)
        duplicate_registry = dict(registry)
        duplicate_registry[identities[1]] = duplicate_registry[identities[0]]
        live_rules = self.live_rules(duplicate_registry)
        with self.assertRaisesRegex(RuntimeError, "not unique"):
            alerts.validate_live_registry(namespace, duplicate_registry, live_rules)

        changed_registry = dict(registry)
        renamed_identity = next(
            identity
            for identity, uid in changed_registry.items()
            if uid in alerts.RENAMED_TITLES
        )
        changed_registry[renamed_identity] = "123e4567-e89b-42d3-a456-426614174000"
        with self.assertRaisesRegex(RuntimeError, "exactly eight"):
            alerts.validate_live_registry(
                namespace, changed_registry, self.live_rules(changed_registry)
            )

        config = alerts.fixture_config()
        generated_rules = [
            *namespace["service_rules"](config, "folder", "prometheus"),
            *namespace["host_metric_rules"](config, "folder", "prometheus"),
            *namespace["site_rules"](config, "folder", "prometheus"),
            *namespace["external_service_rules"](config, "folder", "prometheus"),
        ]
        generated_by_uid = {rule["uid"]: rule for rule in generated_rules}
        live_rules = self.live_rules(registry)
        live_by_uid = {rule["uid"]: rule for rule in live_rules}
        for uid, rule in generated_by_uid.items():
            live_by_uid[uid] = json.loads(json.dumps(rule))
        expected_updates = {
            uid
            for uid, rule in generated_by_uid.items()
            if uid in alerts.RENAMED_TITLES
            or rule["labels"]["check"] in alerts.COMMA_CHECKS
        }
        for uid in expected_updates:
            live_by_uid[uid]["annotations"] = {
                **live_by_uid[uid]["annotations"],
                "legacy": "changed",
            }
        full_live_rules = list(live_by_uid.values())
        alerts.validate_live_registry(
            namespace, registry, full_live_rules, generated_rules
        )
        unexpected_update = json.loads(json.dumps(full_live_rules))
        next(
            rule
            for rule in unexpected_update
            if rule["uid"] in generated_by_uid and rule["uid"] not in expected_updates
        )["annotations"] = {"unexpected": "change"}
        with self.assertRaisesRegex(RuntimeError, "unexpected rule updates"):
            alerts.validate_live_registry(
                namespace, registry, unexpected_update, generated_rules
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            export = pathlib.Path(temp_dir) / "live.json"
            export.write_text(json.dumps(self.live_rules(registry)), encoding="utf-8")
            with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(alerts.main(["--live-export", str(export)]), 0)
            self.assertIn("identity_created=0", stdout.getvalue())

    @staticmethod
    def live_rules(registry: dict[str, str]) -> list[dict[str, object]]:
        rules = []
        for identity, uid in registry.items():
            scope, resource, check = identity.split(":", 2)
            rules.append(
                {
                    "uid": uid,
                    "labels": {
                        "configured_by": "ansible",
                        scope: resource,
                        "check": check,
                    },
                }
            )
        return rules

    def test_generated_alert_validator_rejects_format_and_identity_drift(self) -> None:
        cases = (
            (
                "external_service_rules",
                lambda rule: (
                    rule["labels"]["check"] == "gcs_stale_bucket_count"
                ),
                lambda rule: rule["annotations"].update(check_value="bad"),
                "comma-grouped integer",
            ),
            (
                "host_metric_rules",
                lambda rule: rule["labels"]["check"] == "filesystem_inode",
                lambda rule: rule["annotations"].update(check_value="bad"),
                "fixed percentage",
            ),
            (
                "host_metric_rules",
                lambda rule: rule["labels"]["check"] == "cpu_utilization",
                lambda rule: rule["annotations"].update(check_value="bad"),
                "format changed",
            ),
            (
                "site_rules",
                lambda rule: rule["labels"]["check"] == "ssl_certificate_expiring",
                lambda rule: rule["annotations"].update(check_value="bad"),
                "duration annotation",
            ),
            (
                "host_metric_rules",
                lambda rule: rule["uid"] in alerts.RENAMED_TITLES,
                lambda rule: rule.update(title="bad"),
                "title mismatch",
            ),
        )
        for generator, predicate, mutation, message in cases:
            with self.subTest(message=message):
                namespace = self.changed_rule_namespace(generator, predicate, mutation)
                with (
                    mock.patch.object(alerts.runpy, "run_path", return_value=namespace),
                    self.assertRaisesRegex(RuntimeError, message),
                ):
                    alerts.main([])

        config = alerts.fixture_config()
        config["uid_registry"] = dict(config["uid_registry"])
        config["uid_registry"].pop(next(iter(config["uid_registry"])))
        with (
            mock.patch.object(alerts, "fixture_config", return_value=config),
            self.assertRaisesRegex(RuntimeError, "Expected 76"),
        ):
            alerts.main([])

    def test_generated_usage_contract_rejects_metric_threshold_and_metadata_drift(self) -> None:
        namespace = alerts.runpy.run_path(str(alerts.ALERT_SYNC))
        original_external = namespace["external_service_rules"]

        def missing_usage_rule(*args: object, **kwargs: object) -> list[dict[str, object]]:
            return [
                rule
                for rule in original_external(*args, **kwargs)
                if rule["labels"]["check"] != "gcs_class_a_monthly_operations"
            ]

        namespace["external_service_rules"] = missing_usage_rule
        with (
            mock.patch.object(alerts.runpy, "run_path", return_value=namespace),
            self.assertRaisesRegex(RuntimeError, "missing: gcs_class_a_monthly_operations"),
        ):
            alerts.main([])

        cases = (
            (
                lambda rule: rule["labels"]["check"] == "gcs_class_a_monthly_operations",
                lambda rule: rule.update(_identity="service:changed:check"),
                "Generated identity changed",
            ),
            (
                lambda rule: rule["labels"]["check"] == "gcs_class_a_monthly_operations",
                lambda rule: rule.update(uid="changed"),
                "Generated UID changed",
            ),
            (
                lambda rule: rule["labels"]["check"] == "gcs_class_a_monthly_operations",
                lambda rule: rule.update(title="changed"),
                "Generated title changed",
            ),
            (
                lambda rule: rule["labels"]["check"] == "gcs_class_a_monthly_operations",
                lambda rule: rule["data"][0]["model"].update(
                    expr=rule["data"][0]["model"]["expr"].replace(
                        "grayhaven_gcs_restic_billing_month_operations_total",
                        "grayhaven_gcs_restic_monthly_operations_total",
                    )
                ),
                "raw usage query",
            ),
            (
                lambda rule: rule["labels"]["check"] == "gcs_class_a_monthly_operations",
                lambda rule: rule["data"][0]["model"].update(
                    expr=rule["data"][0]["model"]["expr"].replace("max by", "sum by", 1)
                ),
                "raw usage query",
            ),
            (
                lambda rule: rule["labels"]["check"] == "gcs_class_b_monthly_operations",
                lambda rule: rule["data"][0]["model"].update(
                    expr=rule["data"][0]["model"]["expr"] + " >= 40000"
                ),
                "embeds a threshold",
            ),
            (
                lambda rule: rule["labels"]["check"] == "google_monitoring_monthly_billed_series",
                lambda rule: rule["data"][1]["model"]["conditions"][0]["evaluator"].update(
                    params=[0]
                ),
                "evaluator threshold",
            ),
            (
                lambda rule: rule["labels"]["check"] == "gcs_class_a_monthly_operations",
                lambda rule: rule["data"][1]["model"].update(expression="B"),
                "does not evaluate query A",
            ),
            (
                lambda rule: rule["labels"]["check"] == "gcs_class_a_monthly_operations",
                lambda rule: rule.update(noDataState="Alerting"),
                "NoData policy",
            ),
            (
                lambda rule: rule["labels"]["check"] == "gcs_class_a_monthly_operations",
                lambda rule: rule["labels"].update(service="changed"),
                "labels changed",
            ),
            (
                lambda rule: rule["labels"]["check"] == "gcs_class_a_monthly_operations",
                lambda rule: rule["annotations"].update(summary="changed"),
                "annotations changed",
            ),
            (
                lambda rule: rule["labels"]["check"] == "gcs_class_a_monthly_operations",
                lambda rule: rule.update(folderUID="changed"),
                "folder changed",
            ),
            (
                lambda rule: rule["labels"]["check"] == "gcs_class_a_monthly_operations",
                lambda rule: rule.update(ruleGroup="changed"),
                "rule group changed",
            ),
            (
                lambda rule: rule["labels"]["check"] == "gcs_class_a_monthly_operations",
                lambda rule: rule["notification_settings"].update(receiver="changed"),
                "contact point changed",
            ),
        )
        for predicate, mutation, message in cases:
            with self.subTest(message=message):
                namespace = self.changed_rule_namespace(
                    "external_service_rules", predicate, mutation
                )
                with (
                    mock.patch.object(alerts.runpy, "run_path", return_value=namespace),
                    self.assertRaisesRegex(RuntimeError, message),
                ):
                    alerts.main([])

        contract_namespace = alerts.runpy.run_path(str(alerts.ALERT_SYNC))
        contract_config = alerts.fixture_config()
        contract_rules = [
            *contract_namespace["service_rules"](contract_config, "folder", "prometheus"),
            *contract_namespace["host_metric_rules"](contract_config, "folder", "prometheus"),
            *contract_namespace["site_rules"](contract_config, "folder", "prometheus"),
            *contract_namespace["external_service_rules"](contract_config, "folder", "prometheus"),
        ]
        for invalid_threshold in (True, 1.5, -1):
            invalid_config = alerts.fixture_config()
            invalid_config["thresholds"] = dict(invalid_config["thresholds"])
            invalid_config["thresholds"]["gcs_class_a_monthly_warning_operations"] = invalid_threshold
            with self.subTest(invalid_threshold=invalid_threshold):
                with self.assertRaisesRegex(RuntimeError, "Configured usage threshold is invalid"):
                    alerts.usage_rule_contracts(
                        contract_namespace, invalid_config, contract_rules
                    )

        for check in (
            "gcs_service_telemetry_success",
            "gcs_operation_telemetry_stale",
            "google_monitoring_service_telemetry_success",
            "google_monitoring_telemetry_stale",
        ):
            with self.subTest(check=check):
                namespace = self.changed_rule_namespace(
                    "external_service_rules",
                    lambda rule, check=check: rule["labels"]["check"] == check,
                    lambda rule: rule.update(noDataState="OK"),
                )
                with (
                    mock.patch.object(alerts.runpy, "run_path", return_value=namespace),
                    self.assertRaisesRegex(RuntimeError, "Telemetry NoData policy"),
                ):
                    alerts.main([])

    def test_cache_validator_accepts_good_and_rejects_bad_values(self) -> None:
        counts = {"Class A": 11.0, "Class B": 21.0}
        cache.validate_initial_refresh(counts, 1_000, True, 0o600, "2026-07")
        for values in (
            (
                {"Class A": 0.0, "Class B": 0.0},
                1_000,
                True,
                0o600,
                "2026-07",
            ),
            (counts, 999, True, 0o600, "2026-07"),
            (counts, 1_000, False, 0o600, "2026-07"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Initial"):
                cache.validate_initial_refresh(*values)
        with self.assertRaisesRegex(RuntimeError, "mode 0600"):
            cache.validate_initial_refresh(counts, 1_000, True, 0o644, "2026-07")

        cache.validate_fresh_reuse((counts, 1_000, True, "2026-07"), counts, 1_000, 1)
        for cached, query_count in (
            ((counts, 1_000, False, "2026-07"), 1),
            ((counts, 1_000, True, "2026-07"), 2),
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
                (refreshed, 4_601, expected_fresh, "2026-07"),
                refreshed,
                4_601,
                expected_fresh=expected_fresh,
            )
            with self.assertRaisesRegex(RuntimeError, "Cached telemetry"):
                cache.validate_failure_fallback(
                    (refreshed, 4_601, not expected_fresh, "2026-07"),
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

        start_time = cache.billing_window(1_000)[0]
        end_time = cache.billing_window(1_000)[1]
        cache.validate_billing_query(
            [
                {
                    "interval.startTime": start_time.isoformat().replace("+00:00", "Z"),
                    "interval.endTime": end_time.isoformat().replace("+00:00", "Z"),
                }
            ],
            start_time,
            end_time,
        )
        with self.assertRaisesRegex(RuntimeError, "Expected one"):
            cache.validate_billing_query([], start_time, end_time)
        with self.assertRaisesRegex(RuntimeError, "Expected one"):
            cache.validate_billing_query([{}, {}], start_time, end_time)
        valid_request = {
            "interval.startTime": start_time.isoformat().replace("+00:00", "Z"),
            "interval.endTime": end_time.isoformat().replace("+00:00", "Z"),
        }
        with self.assertRaisesRegex(RuntimeError, "start"):
            cache.validate_billing_query(
                [{**valid_request, "interval.startTime": "wrong"}], start_time, end_time
            )
        with self.assertRaisesRegex(RuntimeError, "end"):
            cache.validate_billing_query(
                [{**valid_request, "interval.endTime": "wrong"}], start_time, end_time
            )

        namespace = cache.runpy.run_path(str(cache.COLLECTOR))
        module_globals = namespace["cached_gcs_operation_counts"].__globals__
        missing_metrics = dict(namespace)
        missing_metrics["render_gcs_metrics"] = lambda config: [
            "grayhaven_gcs_restic_billing_month_operations_total"
        ]
        missing_metrics["render_google_monitoring_metrics"] = lambda config: []
        with self.assertRaisesRegex(RuntimeError, "Canonical Google billing metric names"):
            cache.validate_canonical_metrics(missing_metrics, module_globals)

        wrong_labels = dict(namespace)
        wrong_labels["render_gcs_metrics"] = lambda config: [
            "grayhaven_gcs_restic_billing_month_operations_total"
        ]
        wrong_labels["render_google_monitoring_metrics"] = lambda config: [
            "grayhaven_google_monitoring_billing_month_series_total"
        ]
        with self.assertRaisesRegex(RuntimeError, "Canonical Google billing metric labels"):
            cache.validate_canonical_metrics(wrong_labels, module_globals)

    def test_cache_validator_rejects_unexpected_collection_inputs(self) -> None:
        namespace = cache.runpy.run_path(str(cache.COLLECTOR))
        original = namespace["cached_gcs_operation_counts"]
        wrapped = mock.Mock()
        wrapped.__globals__ = original.__globals__
        wrapped.side_effect = lambda config, buckets: original(config, {"unexpected"})
        namespace["cached_gcs_operation_counts"] = wrapped
        with (
            mock.patch.object(cache.runpy, "run_path", return_value=namespace),
            self.assertRaisesRegex(RuntimeError, "Unexpected bucket set"),
        ):
            cache.main()

        namespace = cache.runpy.run_path(str(cache.COLLECTOR))
        original = namespace["cached_gcs_operation_counts"]
        wrapped = mock.Mock()
        wrapped.__globals__ = original.__globals__

        def mismatched_window(config: dict[str, object], buckets: set[str]) -> object:
            original.__globals__["google_billing_window"] = lambda: (
                cache.BILLING_START,
                cache.datetime.fromtimestamp(9_999, cache.timezone.utc),
                "wrong",
            )
            return original(config, buckets)

        wrapped.side_effect = mismatched_window
        namespace["cached_gcs_operation_counts"] = wrapped
        with (
            mock.patch.object(cache.runpy, "run_path", return_value=namespace),
            self.assertRaisesRegex(RuntimeError, "deterministic billing window"),
        ):
            cache.main()

    def test_cache_validator_rejects_invalid_cache_contracts_in_main(self) -> None:
        def run_case(loader_name: str, replacement: object, message: str) -> None:
            namespace = cache.runpy.run_path(str(cache.COLLECTOR))
            original = namespace[loader_name]
            namespace[loader_name] = replacement(original)
            with (
                mock.patch.object(cache.runpy, "run_path", return_value=namespace),
                self.assertRaisesRegex(RuntimeError, message),
            ):
                cache.main()

        def invalid_gcs_version(original: object) -> object:
            def wrapped(path: pathlib.Path, project_id: str, buckets: set[str], month: str) -> object:
                result = original(path, project_id, buckets, month)
                if path.exists() and result is None:
                    try:
                        if json.loads(path.read_text(encoding="utf-8")).get("version") == 3:
                            return {}
                    except json.JSONDecodeError:
                        pass
                return result

            return wrapped

        def malformed_gcs(original: object) -> object:
            def wrapped(path: pathlib.Path, project_id: str, buckets: set[str], month: str) -> object:
                result = original(path, project_id, buckets, month)
                if path.exists() and result is None and path.read_text(encoding="utf-8") == "malformed":
                    return {}
                return result

            return wrapped

        def valid_monitoring(original: object) -> object:
            def wrapped(path: pathlib.Path, project_id: str, month: str) -> object:
                result = original(path, project_id, month)
                if result == {
                    "version": 3,
                    "project_id": "grayhaven",
                    "month": "2026-07",
                    "billed_series": 10,
                    "refreshed_at": 1_000,
                }:
                    return None
                return result

            return wrapped

        def invalid_monitoring_version(original: object) -> object:
            def wrapped(path: pathlib.Path, project_id: str, month: str) -> object:
                result = original(path, project_id, month)
                if path.exists() and result is None:
                    try:
                        if json.loads(path.read_text(encoding="utf-8")).get("version") == 2:
                            return {}
                    except json.JSONDecodeError:
                        pass
                return result

            return wrapped

        run_case("load_gcs_operation_cache", invalid_gcs_version, "GCS cache accepted invalid version")
        run_case("load_gcs_operation_cache", malformed_gcs, "GCS cache accepted malformed JSON")
        run_case(
            "load_google_monitoring_usage_cache",
            valid_monitoring,
            "Valid Cloud Monitoring usage cache was rejected",
        )
        run_case(
            "load_google_monitoring_usage_cache",
            invalid_monitoring_version,
            "Cloud Monitoring usage cache accepted invalid version",
        )

    def test_alloy_render_validation_and_main(self) -> None:
        self.assertEqual(alloy.regex_replace("abc", "b", "x"), "axc")
        environment = alloy.build_environment()
        self.assertIn("regex_replace", environment.filters)
        self.assertEqual(alloy.fixture_context()["baseline_validation_environment"], "prod")
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
            mock.patch.object(
                alloy.subprocess, "run", return_value=mock.Mock(returncode=0)
            ),
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
            broken["grayhaven-timetracker.container"] += (
                "\nPublishPort=0.0.0.0:8000:8000\n"
            )
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
