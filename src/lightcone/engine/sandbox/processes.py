"""Keep custody of command processes independently of their Dask worker.

The small child process owns the command's process group. Its control pipe
closes if the worker dies, so cleanup does not depend on a Python finally block
in that worker. Groups stay in the allocation's session for native shutdown.
"""

from __future__ import annotations

import json
import os
import re
import select
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from types import FrameType, TracebackType
from typing import Any, Self

import psutil

_GRACE = 1.0
_CLEANUP_TIMEOUT = 15.0


def members(*, group: int | None = None, session: int | None = None) -> list[psutil.Process]:
    """Find live owned processes, retaining their birth identities for signalling."""
    found = []
    for process in psutil.process_iter():
        try:
            if process.uids().real != os.getuid():
                continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        try:
            if process.status() == psutil.STATUS_ZOMBIE:
                continue
            if group is not None and os.getpgid(process.pid) != group:
                continue
            if session is not None and os.getsid(process.pid) != session:
                continue
            process.create_time()
            found.append(process)
        except (psutil.NoSuchProcess, ProcessLookupError):
            continue
    return found


def has_custodian(processes: Sequence[psutil.Process]) -> bool:
    """Check whether allocation shutdown must allow command cleanup to finish."""
    for process in processes:
        try:
            if str(Path(__file__)) in process.cmdline():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def _drain(process: subprocess.Popen[bytes]) -> bool:
    """Stop the whole command group while its unreaped leader pins the group ID."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not members(group=process.pid):
            process.wait()
            return True
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin reports EPERM when only zombies remain. Accept that race
            # only after confirming no live group member still needs stopping.
            if members(group=process.pid):
                raise
            process.wait()
            return True
        deadline = time.monotonic() + _GRACE
        while members(group=process.pid):
            if time.monotonic() >= deadline:
                break
            time.sleep(0.025)
        else:
            process.wait()
            return True
    return False


class Command:
    """Launch a custodian and collect its verified completion report.

    Args:
        argv: Fully wrapped command.
        cwd: Command working directory.
        env: Command environment.
        capture: Whether to pipe stdout and disable stdin.
        timeout: Maximum command runtime in seconds, or no bound.
        container: Whether argv starts a supported OCI runtime.
    """

    def __init__(
        self, argv: Sequence[str], *, cwd: Path, env: dict[str, str], capture: bool,
        timeout: float | None = None, container: bool = False,
    ) -> None:
        self._control, control_write = os.pipe()
        status_read, self._status = os.pipe()
        self._writer = os.fdopen(control_write, "wb", buffering=0)
        self._reader = os.fdopen(status_read, "rb")
        self._deadline = (
            time.monotonic() + timeout + _CLEANUP_TIMEOUT if timeout is not None else None
        )
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-P", str(Path(__file__)), str(self._control), str(self._status)],
                pass_fds=(self._control, self._status),
                stdin=subprocess.DEVNULL if capture else None,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            self._writer.write(json.dumps({
                "argv": list(argv), "cwd": str(cwd), "env": env,
                "timeout": timeout, "container": container,
            }).encode() + b"\n")
        except BaseException:
            self._writer.close()
            if hasattr(self, "process"):
                try:
                    self.wait()
                except Exception as cleanup_error:
                    from lightcone.engine.execution import ExecutionCancelled

                    if not isinstance(cleanup_error, ExecutionCancelled):
                        raise
            else:
                self._reader.close()
            raise
        finally:
            os.close(self._control)
            os.close(self._status)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        from lightcone.engine.execution import ExecutionCancelled

        if not self._reader.closed:
            self._writer.close()
            try:
                self.wait()
            except ExecutionCancelled:
                pass

    def wait(self, cancelled: Callable[[], bool] | None = None) -> tuple[int, str]:
        """Wait for command completion; cancellation includes verified cleanup."""
        from lightcone.engine.execution import ExecutionCancelled, ExecutionUncertain

        requested = self._writer.closed
        deadline = time.monotonic() + _CLEANUP_TIMEOUT if requested else self._deadline
        try:
            while self.process.poll() is None:
                if not requested and cancelled is not None and cancelled():
                    self._writer.close()
                    requested = True
                    deadline = time.monotonic() + _CLEANUP_TIMEOUT
                if deadline is not None and time.monotonic() >= deadline:
                    raise ExecutionUncertain("command cleanup did not finish")
                time.sleep(0.025)
        except BaseException:
            self._writer.close()
            try:
                remaining = (
                    _CLEANUP_TIMEOUT if deadline is None
                    else max(0.0, deadline - time.monotonic())
                )
                self.process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                self._reader.close()
                raise ExecutionUncertain("command cleanup did not finish") from exc
            report = self._report()
            if report.get("error"):
                raise ExecutionUncertain(str(report["error"]))
            raise
        finally:
            self._writer.close()
        report = self._report()
        if report.get("error"):
            raise ExecutionUncertain(str(report["error"]))
        if report.get("start_error"):
            raise OSError(str(report["start_error"]))
        if report.get("cancelled"):
            raise ExecutionCancelled("command cancelled; its processes have stopped")
        return int(report["returncode"]), str(report.get("note", ""))

    def _report(self) -> dict[str, Any]:
        from lightcone.engine.execution import ExecutionUncertain

        try:
            with self._reader:
                result = json.load(self._reader)
            if not isinstance(result, dict) or "returncode" not in result:
                raise ValueError("missing completion record")
            return result
        except (OSError, ValueError) as exc:
            raise ExecutionUncertain(
                "command custodian ended without confirming that its processes stopped"
            ) from exc


def _container_cleanup(runtime: str, cidfile: Path, env: dict[str, str]) -> None:
    """Stop, inspect and remove exactly the container created by this command."""
    try:
        identity = cidfile.read_text().strip()
    except OSError as exc:
        raise RuntimeError("container creation ended before its identity was recorded") from exc
    if not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise RuntimeError("container runtime did not publish a valid immutable container ID")

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [runtime, *args], env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=2,
        )

    def running() -> bool:
        result = run("inspect", "--format", "{{.State.Running}}", identity)
        if result.returncode or result.stdout.strip() not in ("true", "false"):
            raise RuntimeError(f"cannot establish whether container {identity} stopped")
        return result.stdout.strip() == "true"

    try:
        if running():
            try:
                run("stop", "--time", "1", identity)
            except subprocess.TimeoutExpired:
                pass
            if running():
                run("kill", identity)
                deadline = time.monotonic() + 2
                while running():
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"container {identity} remains running after kill")
                    time.sleep(0.025)
    except BaseException:
        # Inspection can fail while native termination still works. Attempt
        # cleanup without converting that lack of evidence into success.
        try:
            run("kill", identity)
        except (OSError, subprocess.SubprocessError):
            pass
        raise
    # A stopped container is safe even when the runtime cannot remove its metadata.
    try:
        run("rm", identity)
    except (OSError, subprocess.SubprocessError):
        pass


def _supervise(control: int, status: int) -> None:
    stopped = False

    def stop(_signum: int, _frame: FrameType | None) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    process: subprocess.Popen[bytes] | None = None
    report: dict[str, Any] = {"returncode": 125}
    with os.fdopen(control, "rb", buffering=0) as channel, tempfile.TemporaryDirectory(
        prefix="lc-command-"
    ) as directory:
        try:
            config = json.loads(channel.readline())
            argv = config["argv"]
            cidfile = Path(directory) / "container"
            if config["container"]:
                argv[2:2] = ["--cidfile", str(cidfile)]
            if stopped or select.select([channel], [], [], 0)[0]:
                report["cancelled"] = True
                return
            process = subprocess.Popen(
                argv, cwd=config["cwd"], env=config["env"], process_group=0,
            )
            started = time.monotonic()
            reason = ""
            # Keep the leader unreaped until group cleanup is complete, pinning
            # its PID/PGID against reuse. Unlike waitid(WNOWAIT), psutil also
            # supports macOS with Python 3.11 and 3.12.
            leader = psutil.Process(process.pid)
            while leader.status() != psutil.STATUS_ZOMBIE:
                if stopped or select.select([channel], [], [], 0.025)[0]:
                    reason = "cancelled"
                    break
                if (
                    config["timeout"] is not None
                    and time.monotonic() - started >= config["timeout"]
                ):
                    reason = "timed out"
                    break
            if not reason and members(group=process.pid):
                reason = "left background processes running"
            container_stopped = False
            if config["container"]:
                # A runtime startup failure with no CID has not published a
                # container; interruption in that window cannot prove the same.
                if cidfile.exists() or reason:
                    _container_cleanup(argv[0], cidfile, config["env"])
                    # Podman removes its cidfile together with the container.
                    # Keep the verified outcome, not the file's later existence.
                    container_stopped = True
            if not _drain(process):
                raise RuntimeError("command processes remain alive after SIGKILL")
            if config["container"] and not container_stopped and process.returncode != 125:
                raise RuntimeError("container runtime exited without recording its identity")
            report["returncode"] = process.returncode
            if reason == "cancelled":
                report["cancelled"] = True
                report["returncode"] = 130
            elif reason:
                report["returncode"] = 124 if reason == "timed out" else 1
                report["note"] = f"command {reason}; its processes have stopped"
        except BaseException as exc:
            if process is None:
                report["start_error"] = f"command could not start: {str(exc)[:2048]}"
            else:
                try:
                    _drain(process)
                except BaseException:
                    pass
                report["error"] = f"cannot confirm command cleanup: {str(exc)[:2048]}"
        finally:
            with os.fdopen(status, "wb", buffering=0) as result:
                try:
                    result.write(json.dumps(report).encode())
                except BrokenPipeError:
                    # The worker can die before receiving the cleanup report.
                    pass


if __name__ == "__main__":
    _supervise(int(sys.argv[1]), int(sys.argv[2]))
