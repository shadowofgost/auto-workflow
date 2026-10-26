import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("compare", Path(__file__).parents[1] / "azure-compare.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.subject = module.Comparison(SimpleNamespace(work=self.directory.name))

    def response(self, route="on", code=0, http=200, size=200):
        metrics = {"http_code": http, "time_total": 0.2, "time_starttransfer": 0.1, "size_download": size}
        return subprocess.CompletedProcess([], code, f"ip=1.2.3.4\nwarp={route}\n" + module.METRICS + json.dumps(metrics), "")

    def test_proxy_cannot_silently_fall_back_to_direct(self):
        self.subject.command = Mock(return_value=self.response(route="off"))
        self.assertFalse(self.subject.curl()["ok"])

    def test_direct_and_proxy_have_distinct_expected_routes(self):
        self.subject.command = Mock(return_value=self.response(route="off"))
        self.assertTrue(self.subject.curl(proxy=False)["ok"])
        self.subject.command.return_value = self.response(route="on")
        self.assertTrue(self.subject.curl()["ok"])
        self.assertFalse(self.subject.curl(proxy=False)["ok"])

    def test_http_and_curl_failure_are_not_success(self):
        for response in (self.response(code=7), self.response(http=503)):
            self.subject.command = Mock(return_value=response)
            self.assertFalse(self.subject.curl()["ok"])

    def test_download_metrics_and_remote_dns_proxy(self):
        self.subject.command = Mock(return_value=self.response(size=1048576))
        self.assertEqual(self.subject.curl(download=True)["bytes"], 1048576)
        self.assertIn("--socks5-hostname", self.subject.command.call_args.args)

    def test_events_redact_credentials(self):
        self.subject.secret = "not-a-real-license"
        self.subject.event("example", error="bad not-a-real-license")
        result = self.subject.output.read_text(encoding="utf-8")
        self.assertNotIn(self.subject.secret, result)
        self.assertIn("[redacted]", result)

    def test_failure_diagnostics_are_allowlisted_and_redacted(self):
        self.subject.secret = "not-a-real-license"
        self.subject.inspect = Mock(return_value={"State": {"Pid": 0}, "Image": "test", "RestartCount": 0})
        self.subject.command = Mock(return_value=subprocess.CompletedProcess([], 0,
            "INFO noisy payload\nERROR WARP command failed: not-a-real-license\n", ""))
        self.subject.event = Mock()
        self.subject.diagnostics()
        record = self.subject.event.call_args.kwargs
        self.assertEqual(record["lines"], ["ERROR WARP command failed: [redacted]"])

    def test_net_admin_is_opt_in_and_applies_to_both_entrypoints(self):
        self.subject.original = {"Config": {"Healthcheck": {"Test": ["CMD-SHELL", "healthcheck"]}}}
        self.subject.args.supervisor = "/tmp/warp-supervisor"
        self.subject.inspect = Mock(return_value={"Image": "trial", "Config": {"Entrypoint": ["entrypoint"]}})
        info = {"RepoDigests": ["image@sha256:test"], "Config": {"Labels": {}}, "Size": 1}
        def response(*args, **kwargs):
            return subprocess.CompletedProcess(args, 0, json.dumps([info]) if args[:3] == ("docker", "image", "inspect") else "", "")
        self.subject.command = Mock(side_effect=response)
        self.subject.event = Mock()
        self.subject.launch("own")
        self.assertNotIn("--cap-add", self.subject.command.call_args_list[0].args)
        for adapted in (False, True):
            self.subject.net_admin = True
            self.subject.command.reset_mock()
            self.subject.launch("candidate", adapted=adapted)
            argv = self.subject.command.call_args_list[0].args
            self.assertEqual(argv[argv.index("--cap-add") + 1], "NET_ADMIN")
            self.assertNotIn("--privileged", argv)
            self.assertNotIn("--device", argv)
            if adapted:
                self.assertIn("/tmp/warp-supervisor:/warp-benchmark/warp-supervisor:ro", argv)
                self.assertEqual(argv[argv.index("--entrypoint") + 1], "/warp-benchmark/warp-supervisor")
                self.assertEqual(argv[argv.index("--user") + 1], "0:0")
        self.subject.command.reset_mock()
        self.subject.launch("own", net_admin=False)
        self.assertNotIn("--cap-add", self.subject.command.call_args_list[0].args)

    def test_stock_gost_is_loopback_only_and_nat_disabled(self):
        self.subject.original = {"Config": {"Healthcheck": {"Test": ["CMD-SHELL", "healthcheck"]}}}
        self.subject.inspect = Mock(return_value={"Image": "trial", "Config": {}})
        self.subject.command = Mock(side_effect=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0,
            json.dumps([{"Size": 1, "Config": {}}]) if args[:3] == ("docker", "image", "inspect") else "", ""))
        self.subject.launch("upstream", stock=True)
        argv = self.subject.command.call_args_list[0].args
        self.assertIn("GOST_ARGS=-L 127.0.0.1:1080", argv)
        self.assertIn("WARP_ENABLE_NAT=", argv)

    def test_host_regression_aborts_the_trial(self):
        self.subject.host_baseline = {}
        self.subject.host_snapshot = Mock(return_value={})
        with patch.object(module.network, "summary", return_value={"problems": ["main route changed"]}):
            with self.assertRaisesRegex(RuntimeError, "Host regression"):
                self.subject.host_check(services=False)

    def test_watchdog_recovers_if_controller_is_dead(self):
        directory = Path(self.directory.name)
        (directory / "control.json").write_text(json.dumps({"completed": False, "pid": 12345, "args": {}}))
        (directory / "network-before.json").write_text("{}")
        with patch.object(module.os, "kill", side_effect=ProcessLookupError), \
             patch.object(module.network, "snapshot", return_value={}), \
             patch.object(module.network, "regressions", return_value=[]), \
             patch.object(module, "recover") as recovery:
            self.assertEqual(module.watchdog(directory), 1)
            recovery.assert_called_once_with(directory)
        self.assertTrue((directory / "abort-requested").exists())

    def test_previous_run_evidence_is_never_overwritten(self):
        self.subject.output.write_text("previous evidence\n")
        with self.assertRaisesRegex(RuntimeError, "already contains evidence"):
            self.subject.run()
        self.assertEqual(self.subject.output.read_text(), "previous evidence\n")

    def test_missing_rust_adapter_fails_before_touching_services(self):
        self.subject.args.supervisor = str(Path(self.directory.name) / "missing-supervisor")
        self.subject.command = Mock()
        with self.assertRaisesRegex(RuntimeError, "executable Rust supervisor"):
            self.subject.run()
        self.subject.command.assert_not_called()

    def test_atomic_compose_write_preserves_owner_and_validates_both_files(self):
        compose = Path(self.directory.name) / "azure.yml"
        compose.write_text("services:\n  warp:\n    image: old\n")
        before = compose.stat()
        self.subject.command = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        self.subject.net_admin = True
        with patch.object(module, "COMPOSE", compose), patch.object(module.os, "chown", create=True) as chown:
            self.subject.persist_compose("tested")
            chown.assert_called_once_with(compose.with_name(".azure-warp-candidate.yml"), before.st_uid, before.st_gid)
        self.assertEqual(self.subject.command.call_count, 2)
        self.assertIn("NET_ADMIN", compose.read_text())
        self.assertFalse(compose.with_name(".azure-warp-candidate.yml").exists())


if __name__ == "__main__":
    unittest.main()
