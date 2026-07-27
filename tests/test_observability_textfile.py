from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
import unittest
import urllib.error
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.helpers import load_program


collector = load_program(
    "grayhaven_observability_textfile",
    "roles/observability/files/grayhaven-observability-textfile",
)


class Response:
    def __init__(self, body: object) -> None:
        self.body = json.dumps(body).encode()

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def host(*, control_node: bool = True) -> dict[str, object]:
    return {
        "client": "grayhaven",
        "control_node": control_node,
        "environment": "prod",
        "fqdn": "bastion.grayhavensystems.com",
        "project": "security",
        "role": "bastion",
        "short_hostname": "bastion",
    }


def full_config() -> dict[str, object]:
    return {
        "host": host(),
        "hosts": [host(), {**host(control_node=False), "short_hostname": "web", "fqdn": "web.grayhavensystems.com"}],
        "restic": {"repositories": ["local", "remote"]},
        "gcs": {
            "enabled": True,
            "credentials_file": "/credentials.json",
            "location": "US-EAST1",
            "monitoring_usage_cache_file": "/cache/monitoring.json",
            "monitoring_usage_refresh_seconds": 3600,
            "monitoring_usage_stale_seconds": 10800,
            "operation_cache_file": "/cache/gcs.json",
            "operation_refresh_seconds": 3600,
            "operation_stale_seconds": 10800,
            "project_id": "grayhaven",
        },
        "proton": {
            "enabled": True,
            "services": [{"name": "Mail", "component_name": "Proton Mail"}],
        },
        "fail2ban": {
            "jails": [
                {
                    "name": "sshd",
                    "display_name": "SSH",
                    "domain": "",
                    "category": "access",
                }
            ]
        },
    }


class HttpAndGoogleTests(unittest.TestCase):
    def test_http_token_and_values(self) -> None:
        with mock.patch.object(
            collector.urllib.request, "urlopen", return_value=Response({"ok": True})
        ):
            self.assertEqual(collector.request_json("https://example.invalid"), {"ok": True})
            self.assertEqual(
                collector.post_form_json("https://example.invalid", {"a": "b"}),
                {"ok": True},
            )
        self.assertEqual(collector.base64url(b"test"), "dGVzdA")
        self.assertEqual(collector.point_value({"value": {"int64Value": "2"}}), 2.0)
        self.assertEqual(collector.point_value({"value": {"doubleValue": 2.5}}), 2.5)
        self.assertEqual(collector.point_value({}), 0.0)

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "credentials.json"
            path.write_text(
                json.dumps(
                    {
                        "client_email": "service@example.invalid",
                        "private_key": pem,
                        "token_uri": "https://oauth.example.invalid/token",
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(collector.time, "time", return_value=1000),
                mock.patch.object(
                    collector,
                    "post_form_json",
                    return_value={"access_token": "token"},
                ) as post,
            ):
                self.assertEqual(collector.monitoring_token(str(path)), "token")
            self.assertIn("assertion", post.call_args.args[1])
            with (
                mock.patch.object(collector.serialization, "load_pem_private_key", return_value=object()),
                self.assertRaisesRegex(RuntimeError, "not an RSA"),
            ):
                collector.google_token(str(path), "scope")
            with (
                mock.patch.object(collector, "post_form_json", return_value={}),
                self.assertRaisesRegex(RuntimeError, "access token"),
            ):
                collector.google_token(str(path), "scope")

    def test_monitoring_queries_and_operation_classes(self) -> None:
        with mock.patch.object(collector, "request_json", return_value={"ok": True}) as request:
            self.assertEqual(
                collector.monitoring_request("project/name", "timeSeries", {"a": "b"}, "token"),
                {"ok": True},
            )
        self.assertIn("projects/project%2Fname/timeSeries", request.call_args.args[0])
        self.assertEqual(collector.gcs_operation_class("storage.objects.insert"), "Class A")
        self.assertEqual(collector.gcs_operation_class("storage.objects.get"), "Class B")
        self.assertEqual(collector.gcs_operation_class("unknown"), "")
        self.assertEqual(collector.gcs_operation_counts("project", "token", set()), {"Class A": 0.0, "Class B": 0.0})

        pages = [
            {
                "timeSeries": [
                    {
                        "resource": {"labels": {"bucket_name": "bucket"}},
                        "metric": {"labels": {"method": "storage.objects.insert"}},
                        "points": [{"value": {"int64Value": "2"}}],
                    },
                    {
                        "resource": {"labels": {"bucket_name": "other"}},
                        "metric": {"labels": {"method": "storage.objects.get"}},
                        "points": [{"value": {"int64Value": "99"}}],
                    },
                ],
                "nextPageToken": "next",
            },
            {
                "timeSeries": [
                    {
                        "resource": {"labels": {"bucket_name": "bucket"}},
                        "metric": {"labels": {"method": "storage.objects.get"}},
                        "points": [{"value": {"doubleValue": 3.5}}],
                    }
                ]
            },
        ]
        with mock.patch.object(collector, "monitoring_request", side_effect=pages) as request:
            self.assertEqual(
                collector.gcs_operation_counts("project", "token", {"bucket"}),
                {"Class A": 2.0, "Class B": 3.5},
            )
        self.assertEqual(request.call_count, 2)

        with mock.patch.object(
            collector,
            "monitoring_request",
            return_value={
                "timeSeries": [{"points": [{"value": {"int64Value": "12"}}]}]
            },
        ):
            self.assertEqual(collector.monitoring_billed_series_total("project", "token"), 12)
        with (
            mock.patch.object(
                collector,
                "monitoring_request",
                return_value={"timeSeries": [{}, {}]},
            ),
            self.assertRaisesRegex(RuntimeError, "cardinality"),
        ):
            collector.monitoring_billed_series_total("project", "token")


class CacheTests(unittest.TestCase):
    def test_cache_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "cache.json"
            self.assertIsNone(
                collector.load_gcs_operation_cache(path, "project", {"bucket"}, "2026-07")
            )
            valid = {
                "version": 1,
                "project_id": "project",
                "month": "2026-07",
                "bucket_names": ["bucket"],
                "counts": {"Class A": 1, "Class B": 2},
                "refreshed_at": 100,
            }
            path.write_text(json.dumps(valid), encoding="utf-8")
            self.assertEqual(
                collector.load_gcs_operation_cache(path, "project", {"bucket"}, "2026-07"),
                valid,
            )
            path.write_text("[]", encoding="utf-8")
            self.assertIsNone(
                collector.load_gcs_operation_cache(path, "project", {"bucket"}, "2026-07")
            )
            for key, value in (
                ("version", 2),
                ("project_id", "other"),
                ("month", "2026-06"),
                ("bucket_names", []),
                ("counts", []),
                ("refreshed_at", "bad"),
            ):
                invalid = dict(valid)
                invalid[key] = value
                path.write_text(json.dumps(invalid), encoding="utf-8")
                self.assertIsNone(
                    collector.load_gcs_operation_cache(
                        path, "project", {"bucket"}, "2026-07"
                    )
                )
            invalid = dict(valid)
            invalid["counts"] = {"Class A": "bad", "Class B": 2}
            path.write_text(json.dumps(invalid), encoding="utf-8")
            self.assertIsNone(
                collector.load_gcs_operation_cache(path, "project", {"bucket"}, "2026-07")
            )

            usage = {
                "version": 1,
                "project_id": "project",
                "month": "2026-07",
                "billed_series": 10,
                "refreshed_at": 100,
            }
            path.write_text(json.dumps(usage), encoding="utf-8")
            self.assertEqual(
                collector.load_google_monitoring_usage_cache(path, "project", "2026-07"),
                usage,
            )
            for key, value in (
                ("version", 2),
                ("project_id", "other"),
                ("month", "2026-06"),
                ("billed_series", "bad"),
                ("refreshed_at", "bad"),
            ):
                invalid_usage = dict(usage)
                invalid_usage[key] = value
                path.write_text(json.dumps(invalid_usage), encoding="utf-8")
                self.assertIsNone(
                    collector.load_google_monitoring_usage_cache(
                        path, "project", "2026-07"
                    )
                )
            path.write_text("[]", encoding="utf-8")
            self.assertIsNone(
                collector.load_google_monitoring_usage_cache(path, "project", "2026-07")
            )

    def test_cached_refresh_and_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            gcs_path = pathlib.Path(temp_dir) / "gcs.json"
            usage_path = pathlib.Path(temp_dir) / "usage.json"
            config = {
                "credentials_file": "credentials",
                "operation_cache_file": str(gcs_path),
                "operation_refresh_seconds": 60,
                "operation_stale_seconds": 120,
                "monitoring_usage_cache_file": str(usage_path),
                "monitoring_usage_refresh_seconds": 60,
                "monitoring_usage_stale_seconds": 120,
                "project_id": "project",
            }
            with (
                mock.patch.object(collector.time, "time", return_value=1000),
                mock.patch.object(collector, "monitoring_token", return_value="token"),
                mock.patch.object(
                    collector,
                    "gcs_operation_counts",
                    return_value={"Class A": 1, "Class B": 2},
                ),
            ):
                self.assertEqual(
                    collector.cached_gcs_operation_counts(config, {"bucket"}),
                    ({"Class A": 1.0, "Class B": 2.0}, 1000, True),
                )
            self.assertEqual(gcs_path.stat().st_mode & 0o777, 0o600)
            with (
                mock.patch.object(collector.time, "time", return_value=1100),
                mock.patch.object(
                    collector, "monitoring_token", side_effect=OSError("expected")
                ),
            ):
                self.assertEqual(
                    collector.cached_gcs_operation_counts(config, {"bucket"})[0],
                    {"Class A": 1.0, "Class B": 2.0},
                )
            gcs_path.unlink()
            with (
                mock.patch.object(collector.time, "time", return_value=1200),
                mock.patch.object(
                    collector, "monitoring_token", side_effect=OSError("expected")
                ),
                self.assertRaises(OSError),
            ):
                collector.cached_gcs_operation_counts(config, {"bucket"})

            with (
                mock.patch.object(collector.time, "time", return_value=2000),
                mock.patch.object(collector, "monitoring_token", return_value="token"),
                mock.patch.object(
                    collector, "monitoring_billed_series_total", return_value=123
                ),
            ):
                self.assertEqual(
                    collector.cached_google_monitoring_usage(config),
                    (123.0, 2000, True),
                )
            with (
                mock.patch.object(collector.time, "time", return_value=2100),
                mock.patch.object(
                    collector, "monitoring_token", side_effect=RuntimeError("expected")
                ),
            ):
                self.assertEqual(
                    collector.cached_google_monitoring_usage(config)[:2],
                    (123.0, 2000),
                )


class PublicStatusAndFail2banTests(unittest.TestCase):
    def test_google_and_proton_status(self) -> None:
        self.assertTrue(collector.location_matches("US-EAST1", []))
        self.assertTrue(collector.location_matches("US", [{"title": "United States"}]))
        self.assertTrue(collector.location_matches("US-EAST1", [{"id": "global"}]))
        self.assertFalse(collector.location_matches("US-EAST1", [{"id": "europe"}]))
        with mock.patch.object(
            collector,
            "request_json",
            return_value={"products": [{"title": "Cloud Monitoring", "id": "found"}]},
        ):
            self.assertEqual(
                collector.google_cloud_product_id("Cloud Monitoring", "fallback"), "found"
            )
        with mock.patch.object(
            collector, "request_json", side_effect=urllib.error.URLError("expected")
        ):
            self.assertEqual(
                collector.google_cloud_product_id("Cloud Monitoring", "fallback"),
                "fallback",
            )

        incidents = [
            {
                "affected_products": [{"id": "product"}],
                "currently_affected_locations": [{"id": "global"}],
                "status_impact": "SERVICE_DISRUPTION",
            }
        ]
        with (
            mock.patch.object(collector, "google_cloud_product_id", return_value="product"),
            mock.patch.object(collector, "request_json", return_value=incidents),
        ):
            self.assertEqual(
                collector.google_cloud_status("Product", "fallback", "US"),
                ("Service Disruption", 0, 1),
            )
        with (
            mock.patch.object(collector, "google_cloud_product_id", return_value="product"),
            mock.patch.object(collector, "request_json", return_value=[]),
        ):
            self.assertEqual(
                collector.google_cloud_status("Product", "fallback", "US"),
                ("Operational", 1, 1),
            )
        with mock.patch.object(
            collector, "request_json", side_effect=OSError("expected")
        ):
            self.assertEqual(
                collector.google_cloud_status("Product", "fallback", "US"),
                ("Unknown", 0, 0),
            )
        with (
            mock.patch.object(collector, "google_cloud_product_id", return_value="product"),
            mock.patch.object(
                collector,
                "request_json",
                return_value=[
                    {**incidents[0], "end": "finished"},
                    {**incidents[0], "affected_products": [{"id": "other"}]},
                    {
                        **incidents[0],
                        "currently_affected_locations": [{"id": "europe"}],
                    },
                ],
            ),
        ):
            self.assertEqual(
                collector.google_cloud_status("Product", "fallback", "US"),
                ("Operational", 1, 1),
            )
        with mock.patch.object(
            collector, "google_cloud_status", return_value=("Operational", 1, 1)
        ) as status:
            self.assertEqual(collector.gcs_status("US"), ("Operational", 1, 1))
            self.assertEqual(
                collector.google_monitoring_status("US"), ("Operational", 1, 1)
            )
        self.assertEqual(status.call_count, 2)

        with mock.patch.object(
            collector,
            "request_json",
            return_value={
                "components": [{"name": "Proton Mail", "status": "operational"}, {}]
            },
        ):
            self.assertIn("Proton Mail", collector.proton_components())
        self.assertEqual(collector.proton_status_value("operational"), 1)
        self.assertEqual(collector.proton_status_value("degraded"), 0)
        self.assertEqual(collector.render_status("partial_outage"), "Partial Outage")
        self.assertEqual(collector.render_status(""), "Unknown")

    def test_fail2ban_parsing(self) -> None:
        self.assertEqual(collector.normalize_ip_token("[192.0.2.1],"), "192.0.2.1")
        self.assertEqual(collector.normalize_ip_token("invalid"), "")
        self.assertEqual(collector.format_fail2ban_expiry_date("permanent"), "permanent")
        self.assertRegex(
            collector.format_fail2ban_expiry_date("expires=2026-07-27 09:00:00"),
            r"2026-07-27 \d{1,2}:00 (AM|PM)",
        )
        success = subprocess.CompletedProcess(
            [],
            0,
            stdout=(
                "Status for the jail: sshd\n"
                "|- Currently banned: 1\n"
                "|- Total banned: 3\n"
                "`- Banned IP list: 192.0.2.1\n"
            ),
            stderr="",
        )
        with mock.patch.object(collector.subprocess, "run", return_value=success):
            self.assertEqual(
                collector.fail2ban_status("sshd"), (1, 1, 3, ["192.0.2.1"])
            )
        with mock.patch.object(
            collector.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="192.0.2.1 2026-07-27 09:00:00\n", stderr=""
            ),
        ):
            self.assertIn("192.0.2.1", collector.fail2ban_ban_expiry_dates("sshd"))
        for result in (
            OSError("expected"),
            subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        ):
            kwargs = (
                {"side_effect": result}
                if isinstance(result, OSError)
                else {"return_value": result}
            )
            with mock.patch.object(collector.subprocess, "run", **kwargs):
                self.assertEqual(collector.fail2ban_ban_expiry_dates("sshd"), {})
        with mock.patch.object(
            collector.subprocess, "run", side_effect=OSError("expected")
        ):
            self.assertEqual(collector.fail2ban_status("sshd"), (0, 0, 0, []))
        with mock.patch.object(
            collector.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        ):
            self.assertEqual(collector.fail2ban_status("sshd"), (0, 0, 0, []))


class RenderingTests(unittest.TestCase):
    def test_render_sections_and_failures(self) -> None:
        config = full_config()
        self.assertEqual(collector.render_expected_restic_repositories({"host": host(control_node=False)}), [])
        with (
            mock.patch.object(collector, "gcs_status", return_value=("Operational", 1, 1)),
            mock.patch.object(
                collector,
                "cached_gcs_operation_counts",
                return_value=({"Class A": 10, "Class B": 20}, 1000, True),
            ),
            mock.patch.object(
                collector, "google_monitoring_status", return_value=("Operational", 1, 1)
            ),
            mock.patch.object(
                collector,
                "cached_google_monitoring_usage",
                return_value=(1234, 1000, True),
            ),
            mock.patch.object(
                collector,
                "proton_components",
                return_value={"Proton Mail": {"status": "operational"}},
            ),
            mock.patch.object(
                collector, "fail2ban_status", return_value=(1, 1, 2, ["192.0.2.1"])
            ),
            mock.patch.object(
                collector,
                "fail2ban_ban_expiry_dates",
                return_value={"192.0.2.1": "date"},
            ),
        ):
            rendered = collector.render(config)
        for metric in (
            "grayhaven_host_info",
            "grayhaven_restic_repository_expected",
            "grayhaven_gcs_restic_monthly_operations_total",
            "grayhaven_google_monitoring_monthly_billed_series_total",
            "grayhaven_proton_service_status",
            "grayhaven_fail2ban_jail_banned_ip_info",
        ):
            self.assertIn(metric, rendered)

        with (
            mock.patch.object(collector, "gcs_status", return_value=("Unknown", 0, 0)),
            mock.patch.object(
                collector,
                "cached_gcs_operation_counts",
                side_effect=RuntimeError("expected"),
            ),
        ):
            self.assertIn(
                "telemetry_success",
                "\n".join(collector.render_gcs_metrics(config)),
            )
        with (
            mock.patch.object(
                collector, "google_monitoring_status", return_value=("Unknown", 0, 0)
            ),
            mock.patch.object(
                collector,
                "cached_google_monitoring_usage",
                side_effect=RuntimeError("expected"),
            ),
        ):
            self.assertIn(
                "telemetry_success",
                "\n".join(collector.render_google_monitoring_metrics(config)),
            )
        with mock.patch.object(
            collector, "proton_components", side_effect=OSError("expected")
        ):
            self.assertIn(
                "telemetry_success",
                "\n".join(collector.render_proton_metrics(config)),
            )
        self.assertEqual(
            collector.render_proton_metrics({"host": host(), "proton": {"enabled": True}}),
            [],
        )
        self.assertEqual(collector.render_fail2ban_metrics({"host": host()}), [])

    def test_atomic_write_and_main(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            output = root / "metrics.prom"
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"host": host(control_node=False)}), encoding="utf-8")
            collector.atomic_write(output, "content\n", mode=0o600)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with mock.patch.object(
                collector.os, "replace", side_effect=OSError("expected")
            ):
                with self.assertRaises(OSError):
                    collector.atomic_write(output, "replacement\n")
            self.assertEqual(list(root.glob(".metrics.prom.*")), [])
            with (
                mock.patch.object(collector, "CONFIG_PATH", config_path),
                mock.patch.object(collector, "OUTPUT_PATH", output),
            ):
                self.assertEqual(collector.main(), 0)
            self.assertIn("grayhaven_host_info", output.read_text(encoding="utf-8"))
