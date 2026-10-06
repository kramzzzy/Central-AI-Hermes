"""Own isolated runtime processes so access revocation can terminate their tree."""
import os
import signal
import subprocess
import threading
import time

class RunProcesses:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = {}
        self.cancelled = {}

    def cancel(self, run_id):
        with self.lock:
            self.cancelled = {k: v for k, v in self.cancelled.items() if v > time.monotonic()}
            self.cancelled[run_id] = time.monotonic() + 300
            process = self.active.get(run_id)
            if process is not None:
                self._kill(process)

    @staticmethod
    def _kill(process):
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def run(self, run_id, command, payload, **kwargs):
        with self.lock:
            self.cancelled = {k: v for k, v in self.cancelled.items() if v > time.monotonic()}
            if run_id in self.cancelled:
                raise RuntimeError("Run cancelled before launch")
            if run_id in self.active:
                raise RuntimeError("Duplicate run")
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", **options, **kwargs)
            self.active[run_id] = process
        try:
            stdout, stderr = process.communicate(payload, timeout=210)
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except BaseException:
            self._kill(process)
            process.communicate(timeout=10)
            raise
        finally:
            with self.lock:
                self.active.pop(run_id, None)
