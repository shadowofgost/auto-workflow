import contextlib
import importlib.util
import io
from pathlib import Path
import signal
import subprocess
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("supervisor", Path(__file__).parents[1] / "supervisor.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class SupervisorTests(unittest.TestCase):
    def supervisor(self, **env):
        result = module.Supervisor({"WARP_SLEEP": "0", **env})
        result.log = Mock()
        return result

    def configure(self, supervisor, registered=True, license="same"):
        calls = []

        def cli(*args, **kwargs):
            calls.append(args)
            if args == ("registration", "show"):
                if len([c for c in calls if c == args]) == 1 and not registered:
                    return None
                return subprocess.CompletedProcess([], 0, f"Account type: Free\nLicense: {license}\n")
            return subprocess.CompletedProcess([], 0, "Success")

        supervisor.cli = Mock(side_effect=cli)
        supervisor.configure()
        return calls

    def test_first_registration_and_license_change(self):
        calls = self.configure(self.supervisor(WARP_LICENSE_KEY="secret"), registered=False)
        self.assertIn(("registration", "new"), calls)
        self.assertIn(("registration", "license", "secret"), calls)
        self.assertEqual(calls[-1], ("connect",))

    def test_existing_registration_reapplies_settings_without_reregistering(self):
        calls = self.configure(self.supervisor(WARP_LICENSE_KEY="same", WARP_PORT="41000", WARP_CUSTOM_ENDPOINT="1.2.3.4:443"))
        self.assertNotIn(("registration", "new"), calls)
        self.assertNotIn(("registration", "license", "same"), calls)
        self.assertIn(("mode", "proxy"), calls)
        self.assertIn(("proxy", "port", "41000"), calls)
        self.assertIn(("tunnel", "endpoint", "set", "1.2.3.4:443"), calls)

    def test_full_tunnel_and_diagnostics_defaults(self):
        calls = self.configure(self.supervisor(WARP_PROXY="FALSE"))
        self.assertIn(("mode", "warp"), calls)
        self.assertFalse(any(c[:2] == ("proxy", "port") for c in calls))
        self.assertIn(("debug", "qlog", "disable"), calls)
        self.assertIn(("debug", "extra", "stop"), calls)
        self.assertIn(("tunnel", "endpoint", "reset"), calls)

    def test_explicit_qlog(self):
        self.assertIn(("debug", "qlog", "enable"), self.configure(self.supervisor(WARP_ENABLE_QLOG="true")))

    def test_invalid_configuration(self):
        for env in ({"WARP_PORT": "0"}, {"WARP_SLEEP": "-1"}, {"WARP_PROXY": "maybe"}, {"WARP_LOG_LEVEL": "silent"}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                self.supervisor(**env)

    def test_cli_failure_redacts_license(self):
        subject = self.supervisor(WARP_LICENSE_KEY="secret")
        with patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "invalid secret")):
            with self.assertRaisesRegex(RuntimeError, "redacted") as caught:
                subject.cli("registration", "license", "secret")
        self.assertNotIn("secret", str(caught.exception))

    def test_status_timeout_is_nonfatal(self):
        with patch.object(module.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 8)):
            self.assertIsNone(self.supervisor().cli("status", check=False))

    def test_stopping_already_disabled_extra_logging_is_idempotent(self):
        result = subprocess.CompletedProcess([], 1, "Error: Extra logging has not yet been started.")
        with patch.object(module.subprocess, "run", return_value=result):
            self.assertIsNotNone(self.supervisor().cli("debug", "extra", "stop", allow_errors=("Extra logging has not yet been started",)))

    def test_readiness_retries(self):
        subject = self.supervisor()
        subject.daemon = Mock()
        subject.daemon.poll.return_value = None
        subject.cli = Mock(side_effect=[None, subprocess.CompletedProcess([], 0, "Disconnected")])
        subject.stopping = Mock()
        subject.stopping.wait.return_value = False
        subject.stopping.is_set.return_value = False
        self.assertTrue(subject.wait_ready())
        self.assertEqual(subject.cli.call_count, 2)

    def test_readiness_deadline(self):
        subject = self.supervisor()
        subject.stopping.wait = Mock(return_value=False)
        with patch.object(module.time, "monotonic", side_effect=[0, 61]):
            with self.assertRaisesRegex(RuntimeError, "60 seconds"):
                subject.wait_ready()

    def test_signal_forwards_to_daemon(self):
        subject = self.supervisor()
        subject.daemon = Mock()
        subject.daemon.poll.return_value = None
        subject.stop(signal.SIGTERM, None)
        self.assertTrue(subject.stopping.is_set())
        subject.daemon.terminate.assert_called_once()

    def test_daemon_death_preserves_exit_code(self):
        subject = self.supervisor()
        daemon = Mock(stdout=io.StringIO(""))
        daemon.poll.return_value = 7
        subject.wait_ready = Mock(return_value=True)
        subject.configure = Mock()
        with patch.object(module.subprocess, "Popen", return_value=daemon), patch.object(module.signal, "signal"):
            self.assertEqual(subject.run(), 7)
        daemon.wait.assert_called_once()

    def test_configuration_failure_cleans_up(self):
        subject = self.supervisor()
        daemon = Mock(stdout=io.StringIO(""))
        daemon.poll.return_value = None
        subject.wait_ready = Mock(return_value=True)
        subject.configure = Mock(side_effect=RuntimeError("setup failed"))
        with patch.object(module.subprocess, "Popen", return_value=daemon), patch.object(module.signal, "signal"):
            self.assertEqual(subject.run(), 1)
        daemon.terminate.assert_called_once()
        daemon.wait.assert_called_once()

    def test_log_filter_redaction_and_error_continuation(self):
        subject = self.supervisor(WARP_LICENSE_KEY="secret")
        subject.daemon = Mock(stdout=io.StringIO("\x1b[31mINFO noise\x1b[0m\nDEBUG spam\nERROR secret\n  stack frame\n"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            subject.daemon_logs()
        self.assertEqual(output.getvalue(), "ERROR [redacted]\n  stack frame\n")

    def test_debug_keeps_info(self):
        subject = self.supervisor(WARP_LOG_LEVEL="debug")
        subject.daemon = Mock(stdout=io.StringIO("INFO detail\nDEBUG more\n"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            subject.daemon_logs()
        self.assertIn("DEBUG more", output.getvalue())


if __name__ == "__main__":
    unittest.main()
