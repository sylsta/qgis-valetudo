"""Reboot the robot over SSH (Valetudo deliberately has no reboot API).

Runs ``ssh`` through QProcess (non-blocking) with key-based authentication
only: BatchMode makes ssh fail instead of asking for a password, so the
plugin never stores or prompts for credentials.

The reboot is scheduled in the background on the robot, so the SSH command
returns at once with a clean exit code instead of being cut off by the reboot.

This module contains no user-facing text: it emits result keys (see RESULTS)
that the UI layer turns into translated messages.
"""

import shutil

from qgis.PyQt.QtCore import QObject, QProcess, pyqtSignal

RESULTS = ("sent", "ssh_missing", "failed")
REMOTE_COMMAND = "(sleep 1; reboot) </dev/null >/dev/null 2>&1 &"
CONNECT_TIMEOUT_S = 10


def ssh_arguments(host, user="root", port=22):
    return [
        "-n",                                        # no stdin
        "-o", "BatchMode=yes",                       # key only, never prompt
        "-o", f"ConnectTimeout={CONNECT_TIMEOUT_S}",
        "-p", str(int(port)),
        f"{user}@{host}",
        REMOTE_COMMAND,
    ]


class SshReboot(QObject):
    finished = pyqtSignal(str, str)   # result key (see RESULTS), detail

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = None
        self._stderr = b""

    def is_running(self):
        return self._process is not None

    def start(self, host, user="root", port=22):
        if self._process is not None:
            return
        program = shutil.which("ssh")
        if not program:
            self.finished.emit("ssh_missing", "")
            return
        self._stderr = b""
        process = QProcess(self)
        process.readyReadStandardError.connect(self._on_stderr)
        process.errorOccurred.connect(self._on_error)
        process.finished.connect(self._on_finished)
        self._process = process
        process.start(program, ssh_arguments(host, user, port))

    def _on_stderr(self):
        if self._process is not None:
            self._stderr = (self._stderr + bytes(self._process.readAllStandardError()))[-2000:]

    def _detail(self):
        lines = [l.strip() for l in self._stderr.decode("utf-8", "replace").splitlines()]
        return next((l for l in reversed(lines) if l), "")

    def _done(self, result, detail):
        process, self._process = self._process, None
        if process is not None:
            process.deleteLater()
        self.finished.emit(result, detail)

    def _on_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._done("ssh_missing", "")

    def _on_finished(self, exit_code, *_):
        if self._process is None:   # already reported by _on_error
            return
        if exit_code == 0:
            self._done("sent", "")
        else:
            self._done("failed", self._detail() or f"exit code {exit_code}")
