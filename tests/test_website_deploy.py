from __future__ import annotations

import hashlib
import hmac
import io
import json
import pathlib
import tempfile
import unittest
import urllib.error
from http import HTTPStatus
from unittest import mock

from tests.helpers import load_program


deploy = load_program(
    "grayhaven_website_deploy",
    "roles/deploy_websites/files/grayhaven-website-deploy",
)


def deployment(root: pathlib.Path) -> object:
    branches = {}
    for name in ("main", "dev"):
        checkout = root / f"checkout-{name}"
        source = checkout / "site" / "frontend"
        destination = root / f"destination-{name}"
        checkout.mkdir(parents=True)
        source.mkdir(parents=True)
        branches[name] = deploy.BranchDeployment(
            name=name,
            checkout=checkout,
            source=source,
            destination=destination,
            render_source=None,
        )
    return deploy.WebsiteDeployment(
        root_name="example.invalid",
        host="example.invalid",
        repository_url="https://github.com/example/site.git",
        webhook_secret="secret",
        branches=branches,
    )


class ConfigurationAndHtmlTests(unittest.TestCase):
    def test_config_normalization_and_matching(self) -> None:
        self.assertEqual(
            deploy.normalize_repository_url(" HTTPS://GitHub.com/Example/Site.git/ "),
            "https://github.com/example/site",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "deployments": [
                            {
                                "root_name": "site",
                                "host": "EXAMPLE.INVALID",
                                "repository_url": "https://github.com/example/site.git",
                                "webhook_secret": "secret",
                                "branches": {
                                    "main": {
                                        "checkout": "/checkout",
                                        "source": "/source",
                                        "destination": "/destination",
                                    }
                                },
                            }
                        ],
                        "fanout": {
                            "enabled": True,
                            "secret": "fanout",
                            "peers": ["peer"],
                            "port": 9000,
                        },
                    }
                ),
                encoding="utf-8",
            )
            config = deploy.load_config(config_path)
        self.assertTrue(config.fanout.enabled)
        self.assertEqual(config.fanout.port, 9000)
        self.assertIsNotNone(
            deploy.find_deployment(
                config.deployments,
                host="example.invalid:443",
                repository_url="https://github.com/example/site",
                branch_name="main",
            )
        )
        self.assertIsNone(
            deploy.find_deployment(
                config.deployments,
                host="other.invalid",
                repository_url="https://github.com/example/site",
                branch_name="main",
            )
        )

    def test_development_cues_and_tree_rendering(self) -> None:
        html = "<html><head><title>Example</title></head><body>Body</body></html>"
        updated = deploy.add_dev_cues(html)
        self.assertIn("Example [Dev]", updated)
        self.assertIn("grayhaven-dev-footer-style", updated)
        self.assertIn("Development Environment", updated)
        self.assertEqual(deploy.add_dev_cues(updated), updated)
        self.assertIn("Development Environment", deploy.add_dev_cues("<p>No body</p>"))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "index.html").write_text(html, encoding="utf-8")
            (source / "asset.txt").write_text("asset", encoding="utf-8")
            (target / "stale").mkdir(parents=True)
            (target / "stale" / "file").write_text("stale", encoding="utf-8")
            self.assertTrue(deploy.render_dev_source(source, target))
            self.assertIn("[Dev]", (target / "index.html").read_text(encoding="utf-8"))
            self.assertEqual((target / "asset.txt").read_text(encoding="utf-8"), "asset")
            self.assertFalse((target / "stale").exists())
            self.assertFalse(deploy.render_dev_source(source, target))
            self.assertFalse(deploy.inject_dev_cues(target))
            raw_root = root / "raw"
            raw_root.mkdir()
            (raw_root / "index.html").write_text(html, encoding="utf-8")
            self.assertTrue(deploy.inject_dev_cues(raw_root))
            with self.assertRaises(deploy.DeployError):
                deploy.render_dev_source(root / "missing", target)


class DeploymentHelperTests(unittest.TestCase):
    def test_status_command_and_git_helpers(self) -> None:
        self.assertEqual(deploy.environment_for_branch("main"), "prod")
        self.assertEqual(deploy.environment_for_branch("dev"), "dev")
        self.assertEqual(deploy.safe_status_component("a/b c"), "a_b_c")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            item = deployment(root)
            self.assertEqual(deploy.read_status(root, item, "main"), {})
            deploy.write_status(root, item, "main", {"sha": "abc"})
            self.assertEqual(deploy.read_status(root, item, "main"), {"sha": "abc"})
            path = deploy.status_path(root, item, "main")
            path.write_text("{", encoding="utf-8")
            self.assertEqual(deploy.read_status(root, item, "main"), {})
            status = deploy.success_status(
                item,
                "main",
                "a" * 40,
                delivery_id="delivery",
                role="coordinator",
                started_at=10,
                finished_at=12.25,
            )
            self.assertEqual(status["duration_seconds"], 2.25)
            self.assertEqual(status["environment"], "prod")

        with mock.patch.object(deploy.subprocess, "run") as run:
            run.return_value = mock.Mock(stdout=" output\n", returncode=0)
            deploy.run_command(["true"], user="ansible", cwd=pathlib.Path("/tmp"))
            self.assertEqual(deploy.run_output(["echo"], user="ansible"), "output")
            self.assertTrue(deploy.git_commit_is_ancestor(pathlib.Path("/tmp"), "a", "b"))
        with (
            mock.patch.object(deploy, "run_command") as command,
            mock.patch.object(deploy, "run_output", return_value="sha"),
        ):
            self.assertEqual(deploy.git_branch_head(pathlib.Path("/tmp"), "main"), "sha")
        command.assert_called_once()
        with mock.patch.object(deploy.shutil, "which", return_value=None):
            deploy.restore_selinux_context(pathlib.Path("/tmp"))
        with (
            mock.patch.object(deploy.shutil, "which", return_value="/usr/sbin/restorecon"),
            mock.patch.object(deploy, "run_command") as command,
        ):
            deploy.restore_selinux_context(pathlib.Path("/tmp"))
        command.assert_called_once()

    def test_deploy_branch_paths(self) -> None:
        sha = "a" * 40
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            item = deployment(root)
            state = root / "state"
            with self.assertRaises(deploy.DeployError):
                deploy.deploy_branch(
                    item,
                    "main",
                    "invalid",
                    state_dir=state,
                    delivery_id="delivery",
                    role="coordinator",
                )

            with (
                mock.patch.object(deploy, "git_branch_head", return_value=sha),
                mock.patch.object(deploy, "run_command") as command,
                mock.patch.object(deploy, "restore_selinux_context"),
            ):
                self.assertEqual(
                    deploy.deploy_branch(
                        item,
                        "main",
                        sha,
                        state_dir=state,
                        delivery_id="delivery",
                        role="coordinator",
                    ),
                    "deployed",
                )
            self.assertGreaterEqual(command.call_count, 3)
            with mock.patch.object(deploy, "git_branch_head", return_value=sha):
                self.assertEqual(
                    deploy.deploy_branch(
                        item,
                        "main",
                        sha,
                        state_dir=state,
                        delivery_id="delivery",
                        role="coordinator",
                    ),
                    "unchanged",
                )

            missing_item = deployment(root / "missing-item")
            missing_item.branches["main"] = deploy.BranchDeployment(
                name="main",
                checkout=root / "missing-checkout",
                source=root / "missing-source",
                destination=root / "destination",
                render_source=None,
            )
            with self.assertRaisesRegex(deploy.DeployError, "Checkout path"):
                deploy.deploy_branch(
                    missing_item,
                    "main",
                    sha,
                    state_dir=state,
                    delivery_id="delivery",
                    role="coordinator",
                )

            older = "b" * 40
            with (
                mock.patch.object(deploy, "git_branch_head", return_value=sha),
                mock.patch.object(deploy, "git_commit_is_ancestor", return_value=True),
                self.assertRaises(deploy.StaleDeployment),
            ):
                deploy.deploy_branch(
                    item,
                    "main",
                    older,
                    state_dir=state,
                    delivery_id="delivery",
                    role="coordinator",
                )

            with (
                mock.patch.object(deploy, "git_branch_head", return_value=sha),
                mock.patch.object(deploy, "git_commit_is_ancestor", return_value=False),
                self.assertRaisesRegex(deploy.StaleDeployment, "not the branch head"),
            ):
                deploy.deploy_branch(
                    item,
                    "main",
                    "c" * 40,
                    state_dir=state,
                    delivery_id="delivery",
                    role="coordinator",
                )

            deploy.write_status(root / "stale-state", item, "main", {"sha": sha})
            with (
                mock.patch.object(deploy, "git_branch_head", return_value="c" * 40),
                mock.patch.object(deploy, "git_commit_is_ancestor", return_value=True),
                self.assertRaisesRegex(deploy.StaleDeployment, "older than deployed"),
            ):
                deploy.deploy_branch(
                    item,
                    "main",
                    "b" * 40,
                    state_dir=root / "stale-state",
                    delivery_id="delivery",
                    role="coordinator",
                    require_branch_head=False,
                )

            lock_dir = root / "locks"
            with mock.patch.object(deploy, "deploy_branch", return_value="deployed"):
                self.assertEqual(
                    deploy.deploy_with_lock(
                        item,
                        "main",
                        sha,
                        runtime_dir=lock_dir,
                        state_dir=state,
                        delivery_id="delivery",
                        role="peer",
                    ),
                    "deployed",
                )

    def test_rollback_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            item = deployment(root)
            state = root / "state"
            ok, detail = deploy.rollback_coordinator_after_failed_fanout(
                item,
                "main",
                "a" * 40,
                previous_status={},
                failed_peers=["peer"],
                state_dir=state,
                delivery_id="delivery",
            )
            self.assertFalse(ok)
            self.assertIn("unavailable", detail)
            with mock.patch.object(deploy, "deploy_branch", return_value="deployed"):
                ok, detail = deploy.rollback_coordinator_after_failed_fanout(
                    item,
                    "main",
                    "a" * 40,
                    previous_status={"sha": "b" * 40},
                    failed_peers=["peer"],
                    state_dir=state,
                    delivery_id="delivery",
                )
            self.assertTrue(ok)
            self.assertEqual(detail, "b" * 40)
            with mock.patch.object(
                deploy, "deploy_branch", side_effect=deploy.DeployError("failed")
            ):
                ok, detail = deploy.rollback_coordinator_after_failed_fanout(
                    item,
                    "main",
                    "a" * 40,
                    previous_status={"sha": "b" * 40},
                    failed_peers=["peer"],
                    state_dir=state,
                    delivery_id="delivery",
                )
            self.assertFalse(ok)
            self.assertEqual(detail, "failed")


class WebhookTests(unittest.TestCase):
    def test_signatures_and_fanout(self) -> None:
        self.assertEqual(deploy.parse_branch("refs/heads/main"), "main")
        self.assertIsNone(deploy.parse_branch("refs/tags/main"))
        self.assertIsNone(deploy.parse_branch("refs/heads/feature"))
        body = b"payload"
        signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(deploy.verify_signature(body, f"sha256={signature}", "secret"))
        self.assertFalse(deploy.verify_signature(body, "bad", "secret"))

        with tempfile.TemporaryDirectory() as temp_dir:
            item = deployment(pathlib.Path(temp_dir))
            fanout = deploy.FanoutConfig(
                enabled=True,
                secret="secret",
                peers=["peer-one", "peer-two"],
                port=8791,
                timeout_seconds=1,
                retries=1,
            )
            with mock.patch.object(
                deploy.urllib.request, "urlopen", return_value=mock.Mock(read=lambda: b"")
            ):
                deploy.call_fanout_peer("peer", fanout, body)
            with mock.patch.object(
                deploy,
                "call_fanout_peer",
                side_effect=[None, urllib.error.URLError("failed"), None],
            ):
                self.assertEqual(
                    deploy.fanout_to_peers(
                        deployment=item,
                        branch_name="main",
                        commit_sha="a" * 40,
                        delivery_id="delivery",
                        fanout=fanout,
                    ),
                    [],
                )
            fanout = deploy.FanoutConfig(False, "", [], 8791, 1, 0)
            self.assertEqual(
                deploy.fanout_to_peers(
                    deployment=item,
                    branch_name="main",
                    commit_sha="a" * 40,
                    delivery_id="delivery",
                    fanout=fanout,
                ),
                [],
            )
            failing = deploy.FanoutConfig(True, "secret", ["peer"], 8791, 1, 1)
            with mock.patch.object(
                deploy,
                "call_fanout_peer",
                side_effect=urllib.error.URLError("failed"),
            ):
                self.assertEqual(
                    deploy.fanout_to_peers(
                        deployment=item,
                        branch_name="main",
                        commit_sha="a" * 40,
                        delivery_id="delivery",
                        fanout=failing,
                    ),
                    ["peer"],
                )

    def handler(self, body: bytes = b"{}") -> object:
        handler = deploy.DeploymentRequestHandler.__new__(deploy.DeploymentRequestHandler)
        handler.path = "/deploy"
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.responses = []
        handler.send_text = lambda status, message: handler.responses.append((status, message))
        return handler

    def test_handler_routing_and_body_errors(self) -> None:
        handler = self.handler()
        handler.path = "/missing"
        handler.do_POST()
        self.assertEqual(handler.responses[-1][0], HTTPStatus.NOT_FOUND)
        handler.path = "/deploy"
        with mock.patch.object(handler, "handle_public_deploy") as public:
            handler.do_POST()
        public.assert_called_once()
        handler.path = "/fanout"
        with mock.patch.object(handler, "handle_fanout_deploy") as fanout:
            handler.do_POST()
        fanout.assert_called_once()

        handler = self.handler(b"{")
        self.assertIsNone(handler.read_json_body())
        handler = self.handler()
        handler.headers = {"Content-Length": "bad"}
        self.assertIsNone(handler.read_json_body())

        handler = deploy.DeploymentRequestHandler.__new__(deploy.DeploymentRequestHandler)
        handler.wfile = io.BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        deploy.DeploymentRequestHandler.send_text(handler, HTTPStatus.OK, "ready")
        handler.send_response.assert_called_once_with(HTTPStatus.OK.value)
        self.assertEqual(handler.wfile.getvalue(), b"ready\n")

    def test_public_and_fanout_handlers(self) -> None:
        sha = "a" * 40
        with tempfile.TemporaryDirectory() as temp_dir:
            item = deployment(pathlib.Path(temp_dir))
            payload = {
                "ref": "refs/heads/main",
                "after": sha,
                "repository": {"clone_url": item.repository_url},
            }
            body = json.dumps(payload).encode()
            signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
            handler = self.handler(body)
            handler.headers.update(
                {
                    "Host": item.host,
                    "X-Hub-Signature-256": f"sha256={signature}",
                    "X-GitHub-Delivery": "delivery",
                }
            )
            handler.deployments = [item]
            handler.runtime_dir = pathlib.Path(temp_dir) / "runtime"
            handler.state_dir = pathlib.Path(temp_dir) / "state"
            handler.fanout = deploy.FanoutConfig(False, "", [], 8791, 1, 0)
            with mock.patch.object(deploy, "deploy_branch", return_value="deployed"):
                handler.handle_public_deploy()
            self.assertEqual(handler.responses[-1], (HTTPStatus.OK, "deployment deployed"))

            handler = self.handler(body)
            handler.headers.update({"Host": item.host, "X-Hub-Signature-256": "bad"})
            handler.deployments = [item]
            handler.handle_public_deploy()
            self.assertEqual(handler.responses[-1][0], HTTPStatus.FORBIDDEN)

            invalid = self.handler(json.dumps({"ref": "refs/tags/main"}).encode())
            invalid.deployments = [item]
            invalid.handle_public_deploy()
            self.assertEqual(invalid.responses[-1][0], HTTPStatus.FORBIDDEN)

            unsupported = self.handler(body)
            unsupported.headers.update(
                {"Host": "other.invalid", "X-Hub-Signature-256": f"sha256={signature}"}
            )
            unsupported.deployments = [item]
            unsupported.handle_public_deploy()
            self.assertEqual(unsupported.responses[-1][0], HTTPStatus.FORBIDDEN)

            for exception, status in (
                (deploy.StaleDeployment("stale"), HTTPStatus.OK),
                (deploy.DeployError("failed"), HTTPStatus.INTERNAL_SERVER_ERROR),
            ):
                failed = self.handler(body)
                failed.headers.update(
                    {
                        "Host": item.host,
                        "X-Hub-Signature-256": f"sha256={signature}",
                    }
                )
                failed.deployments = [item]
                failed.runtime_dir = pathlib.Path(temp_dir) / "runtime-errors"
                failed.state_dir = pathlib.Path(temp_dir) / "state-errors"
                failed.fanout = deploy.FanoutConfig(False, "", [], 8791, 1, 0)
                with mock.patch.object(deploy, "deploy_branch", side_effect=exception):
                    failed.handle_public_deploy()
                self.assertEqual(failed.responses[-1][0], status)

            for rollback_result, expected_text in (
                ((True, "b" * 40), "rolled back"),
                ((False, "unavailable"), "rollback failed"),
            ):
                partial = self.handler(body)
                partial.headers.update(
                    {
                        "Host": item.host,
                        "X-Hub-Signature-256": f"sha256={signature}",
                    }
                )
                partial.deployments = [item]
                partial.runtime_dir = pathlib.Path(temp_dir) / "runtime-partial"
                partial.state_dir = pathlib.Path(temp_dir) / "state-partial"
                partial.fanout = deploy.FanoutConfig(True, "fanout", ["peer"], 8791, 1, 0)
                with (
                    mock.patch.object(deploy, "deploy_branch", return_value="deployed"),
                    mock.patch.object(deploy, "fanout_to_peers", return_value=["peer"]),
                    mock.patch.object(
                        deploy,
                        "rollback_coordinator_after_failed_fanout",
                        return_value=rollback_result,
                    ),
                ):
                    partial.handle_public_deploy()
                self.assertEqual(
                    partial.responses[-1][0], HTTPStatus.INTERNAL_SERVER_ERROR
                )
                self.assertIn(expected_text, partial.responses[-1][1])

            fanout_payload = {
                "branch": "main",
                "sha": sha,
                "repository_url": item.repository_url,
                "host": item.host,
                "delivery_id": "delivery",
            }
            fanout_body = json.dumps(fanout_payload).encode()
            fanout_signature = hmac.new(b"fanout", fanout_body, hashlib.sha256).hexdigest()
            handler = self.handler(fanout_body)
            handler.headers["X-Grayhaven-Fanout-Signature"] = f"sha256={fanout_signature}"
            handler.deployments = [item]
            handler.fanout = deploy.FanoutConfig(True, "fanout", [], 8791, 1, 0)
            handler.runtime_dir = pathlib.Path(temp_dir) / "runtime"
            handler.state_dir = pathlib.Path(temp_dir) / "state"
            with mock.patch.object(deploy, "deploy_with_lock", return_value="deployed"):
                handler.handle_fanout_deploy()
            self.assertEqual(handler.responses[-1], (HTTPStatus.OK, "deployment deployed"))

            disabled = self.handler(fanout_body)
            disabled.fanout = deploy.FanoutConfig(False, "", [], 8791, 1, 0)
            disabled.handle_fanout_deploy()
            self.assertEqual(disabled.responses[-1][0], HTTPStatus.NOT_FOUND)

            for mutate, expected_status in (
                (lambda candidate: candidate.headers.update({"X-Grayhaven-Fanout-Signature": "bad"}), HTTPStatus.FORBIDDEN),
                (lambda candidate: setattr(candidate, "deployments", []), HTTPStatus.FORBIDDEN),
            ):
                candidate = self.handler(fanout_body)
                candidate.headers["X-Grayhaven-Fanout-Signature"] = f"sha256={fanout_signature}"
                candidate.deployments = [item]
                candidate.fanout = deploy.FanoutConfig(True, "fanout", [], 8791, 1, 0)
                mutate(candidate)
                candidate.handle_fanout_deploy()
                self.assertEqual(candidate.responses[-1][0], expected_status)

            for exception, status in (
                (deploy.StaleDeployment("stale"), HTTPStatus.OK),
                (deploy.DeployError("failed"), HTTPStatus.INTERNAL_SERVER_ERROR),
            ):
                candidate = self.handler(fanout_body)
                candidate.headers["X-Grayhaven-Fanout-Signature"] = f"sha256={fanout_signature}"
                candidate.deployments = [item]
                candidate.fanout = deploy.FanoutConfig(True, "fanout", [], 8791, 1, 0)
                candidate.runtime_dir = pathlib.Path(temp_dir) / "runtime-errors"
                candidate.state_dir = pathlib.Path(temp_dir) / "state-errors"
                with mock.patch.object(deploy, "deploy_with_lock", side_effect=exception):
                    candidate.handle_fanout_deploy()
                self.assertEqual(candidate.responses[-1][0], status)

    def test_parser_and_main(self) -> None:
        parser = deploy.build_parser()
        self.assertEqual(parser.parse_args(["inject-dev-cues", "--root", "/tmp"]).command, "inject-dev-cues")
        with (
            mock.patch("sys.argv", ["deploy", "inject-dev-cues", "--root", "/tmp"]),
            mock.patch.object(deploy, "inject_dev_cues", return_value=True),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            self.assertEqual(deploy.main(), 0)
        self.assertIn("changed", stdout.getvalue())
        with (
            mock.patch(
                "sys.argv",
                ["deploy", "render-dev-source", "--source", "/src", "--target", "/dst"],
            ),
            mock.patch.object(deploy, "render_dev_source", return_value=False),
        ):
            self.assertEqual(deploy.main(), 0)
        with (
            mock.patch(
                "sys.argv",
                ["deploy", "serve", "--config", "/config", "--port", "9000"],
            ),
            mock.patch.object(deploy, "serve") as serve,
        ):
            self.assertEqual(deploy.main(), 0)
        serve.assert_called_once()

    def test_server_configuration(self) -> None:
        config = mock.Mock(deployments=["deployment"], fanout="fanout")
        server = mock.Mock()
        with (
            mock.patch.object(deploy, "load_config", return_value=config),
            mock.patch.object(deploy, "ThreadingHTTPServer", return_value=server) as factory,
        ):
            deploy.serve(
                pathlib.Path("/config"),
                "127.0.0.1",
                9000,
                pathlib.Path("/runtime"),
                pathlib.Path("/state"),
            )
        factory.assert_called_once_with(
            ("127.0.0.1", 9000), deploy.DeploymentRequestHandler
        )
        server.serve_forever.assert_called_once()
