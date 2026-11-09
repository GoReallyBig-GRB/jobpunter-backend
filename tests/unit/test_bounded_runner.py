"""Standard-library-only unit tests; no production credentials or DB required."""
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.workers.bounded_runner import ExecutionOptions, _command, _stop_process, execute


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

    @unittest.skipUnless(os.name == "posix", "POSIX process groups are required")
    @patch("app.workers.bounded_runner.os.killpg")
    def test_timeout_kills_descendants_even_after_worker_leader_exits(self, killpg):
        process = unittest.mock.Mock()
        process.pid = 12345
        process.poll.return_value = 0  # leader has exited but children may survive
        process.wait.return_value = 0
        _stop_process(process, 0.1)
        self.assertEqual(
            killpg.call_args_list,
            [unittest.mock.call(12345, signal.SIGTERM),
             unittest.mock.call(12345, signal.SIGKILL)],
        )
        self.assertEqual(process.wait.call_count, 2)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups are required")
    def test_terminated_worker_cannot_leave_real_child_running(self):
        """Kill a real descendant after its worker parent has already exited."""
        parent_code = (
            "import subprocess, sys; "
            "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]])"
        )
        grandchild_code = (
            "import pathlib, sys, time; "
            "pathlib.Path(sys.argv[1]).write_text('ready'); "
            "time.sleep(1.5); "
            "pathlib.Path(sys.argv[2]).write_text('escaped')"
        )
        with TemporaryDirectory() as directory:
            ready = Path(directory) / "ready"
            escaped = Path(directory) / "escaped"
            parent = subprocess.Popen(
                [sys.executable, "-c", parent_code, grandchild_code, str(ready), str(escaped)],
                start_new_session=True,
            )
            parent.wait(timeout=5)
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists(), "grandchild never started")
            _stop_process(parent, 0.2)
            time.sleep(1.7)
            self.assertFalse(escaped.exists(), "worker descendant escaped termination")

    @unittest.skipUnless(os.name == "nt", "Windows process-tree cleanup is required")
    def test_windows_timeout_kills_real_child_process(self):
        parent_code = (
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]]); "
            "time.sleep(10)"
        )
        child_code = (
            "import pathlib, sys, time; "
            "pathlib.Path(sys.argv[1]).write_text('ready'); "
            "time.sleep(1.5); "
            "pathlib.Path(sys.argv[2]).write_text('escaped')"
        )
        with TemporaryDirectory() as directory:
            ready = Path(directory) / "ready"
            escaped = Path(directory) / "escaped"
            parent = subprocess.Popen(
                [sys.executable, "-c", parent_code, child_code, str(ready), str(escaped)],
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists(), "child never started")
            _stop_process(parent, 0.2)
            time.sleep(1.7)
            self.assertFalse(escaped.exists(), "worker descendant escaped termination")

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
