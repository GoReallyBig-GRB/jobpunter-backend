"""Standard-library-only unit tests; no production credentials or DB required."""
import signal
import sys
import subprocess
import unittest
from unittest.mock import patch

from app.workers.bounded_runner import ExecutionOptions, _command, execute


class BoundedRunnerTests(unittest.TestCase):
    def test_batch_worker_command_uses_one_cycle(self):
        command = _command(ExecutionOptions("outbox", 60, batch_size=3))
        self.assertEqual(command[-3:], ["--once", "--batch-size", "3"])

    def test_new_main_source_discovery_worker_is_available(self):
        command = _command(ExecutionOptions("source_discovery_candidate_worker", 60, batch_size=1))
        self.assertEqual(command[-3:], ["--once", "--batch-size", "1"])
        self.assertIn("app.workers.source_discovery_candidate_worker", command)

    def test_retention_uses_native_one_shot_entrypoint(self):
        command = _command(ExecutionOptions("document_retention_worker", 60))
        self.assertEqual(command[-1], "app.workers.document_retention_worker")
        self.assertNotIn("--once", command)

    def test_module_injection_rejected(self):
        with self.assertRaisesRegex(ValueError, "allowlist"):
            _command(ExecutionOptions("os.system", 60))

    def test_invalid_deadlines_and_batch_sizes_rejected(self):
        for options in [
            ExecutionOptions("outbox", 0),
            ExecutionOptions("outbox", 10, termination_grace_seconds=10),
            ExecutionOptions("outbox", 60, batch_size=0),
            ExecutionOptions("outbox", 4000),
        ]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                options.validate()

    @patch("app.workers.bounded_runner._command")
    def test_real_short_lived_subprocess(self, command):
        command.return_value = [sys.executable, "-c", "raise SystemExit(0)"]
        self.assertEqual(execute(ExecutionOptions("outbox", 5, 0.2)), 0)

    @patch("app.workers.bounded_runner._command")
    def test_real_timed_out_subprocess(self, command):
        command.return_value = [sys.executable, "-c", "import time; time.sleep(3)"]
        self.assertEqual(execute(ExecutionOptions("outbox", 1, 0.2)), 124)

    @patch("app.workers.bounded_runner.subprocess.Popen")
    def test_success_returns_exact_process_status(self, popen):
        process = popen.return_value
        process.wait.return_value = 0
        self.assertEqual(execute(ExecutionOptions("outbox", 60)), 0)
        popen.assert_called_once()

    @patch("app.workers.bounded_runner.subprocess.Popen")
    def test_child_failure_propagates(self, popen):
        popen.return_value.wait.return_value = 17
        self.assertEqual(execute(ExecutionOptions("outbox", 60)), 17)

    @patch("app.workers.bounded_runner._stop_process")
    @patch("app.workers.bounded_runner.subprocess.Popen")
    def test_timeout_stops_child_and_reports_124(self, popen, stop):
        process = popen.return_value
        process.wait.side_effect = subprocess.TimeoutExpired("worker", 60)
        self.assertEqual(execute(ExecutionOptions("outbox", 60)), 124)
        stop.assert_called_once_with(process, 10.0)

    @patch("app.workers.bounded_runner._stop_process")
    @patch("app.workers.bounded_runner.subprocess.Popen")
    def test_supervisor_interrupt_stops_child(self, popen, stop):
        process = popen.return_value
        process.wait.side_effect = KeyboardInterrupt()
        self.assertEqual(execute(ExecutionOptions("outbox", 60)), 130)
        stop.assert_called_once_with(process, 10.0)


if __name__ == "__main__":
    unittest.main()
