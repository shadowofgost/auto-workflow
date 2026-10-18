import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

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


if __name__ == "__main__":
    unittest.main()
