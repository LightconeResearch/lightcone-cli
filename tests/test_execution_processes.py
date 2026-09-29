"""Real command ownership: timeout, interruption, background children and worker loss."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import psutil
import pytest

from lightcone.engine.sandbox import Policy, Unavailable, run
from lightcone.engine.sandbox.model import ExecutionCancelled, ExecutionUncertain
from lightcone.engine.sandbox.processes import CIDFILE, Command


def test_finished_group_is_reaped_without_signalling(monkeypatch: pytest.MonkeyPatch) -> None:
    from lightcone.engine.sandbox import processes

    process = Mock(spec=subprocess.Popen, pid=1234)
    signal_group = Mock(side_effect=PermissionError("no live signalable processes"))
    monkeypatch.setattr(processes, "members", lambda **kwargs: [])
    monkeypatch.setattr(processes.os, "killpg", signal_group)
    assert processes._drain(process)
    signal_group.assert_not_called()
    process.wait.assert_called_once_with()


def test_permission_error_after_last_group_member_exits_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lightcone.engine.sandbox import processes

    process = Mock(spec=subprocess.Popen, pid=1234)
    monkeypatch.setattr(processes, "members", Mock(side_effect=[[object()], []]))
    monkeypatch.setattr(
        processes.os, "killpg", Mock(side_effect=PermissionError("no live signalable processes")),
    )
    assert processes._drain(process)
    process.wait.assert_called_once_with()


def test_permission_error_with_a_live_group_member_remains_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lightcone.engine.sandbox import processes

    process = Mock(spec=subprocess.Popen, pid=1234)
    monkeypatch.setattr(processes, "members", lambda **kwargs: [object()])
    monkeypatch.setattr(
        processes.os, "killpg", Mock(side_effect=PermissionError("not permitted")),
    )
    with pytest.raises(PermissionError, match="not permitted"):
        processes._drain(process)
    process.wait.assert_not_called()


def test_kill_confirmation_uses_the_remaining_cleanup_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lightcone.engine.sandbox import processes

    clock = [0.0]
    process = Mock(spec=subprocess.Popen, pid=1234)
    signal_group = Mock()
    monkeypatch.setattr(processes.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        processes.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    monkeypatch.setattr(processes, "members", lambda **kwargs: [object()] if clock[0] < 2.6 else [])
    monkeypatch.setattr(processes.os, "killpg", signal_group)
    assert processes._drain(process, deadline=10)
    assert [call.args[1] for call in signal_group.call_args_list] == [
        signal.SIGTERM, signal.SIGKILL,
    ]
    assert 2.6 <= clock[0] < 3
    process.wait.assert_called_once_with()


def _policy(root: Path) -> Policy:
    return Policy(read=(root,), write=(root,), execute=(), tmp_home=root)


def _gone(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def test_failed_configuration_handoff_reaps_custodian_without_waiting_for_pipe_eof(
    tmp_path: Path,
) -> None:
    # Bound the reproduction in another process: the old constructor retained
    # the report pipe's writer and blocked forever while reading its child reply.
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
from pathlib import Path
import psutil
from lightcone.engine.sandbox.processes import Command

try:
    Command([sys.executable, '-c', "open('started', 'w').close()"],
            cwd=Path(sys.argv[1]), env={'INVALID': object()}, capture=False)
except OSError:
    pass
else:
    raise AssertionError('unserializable configuration was accepted')
assert not psutil.Process().children(), 'custodian was not reaped'
""", str(tmp_path)], capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "started").exists()


def test_custodian_spawn_failure_closes_all_pipe_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lightcone.engine.sandbox import processes

    pipe = os.pipe
    descriptors: list[int] = []

    def record_pipe() -> tuple[int, int]:
        pair = pipe()
        descriptors.extend(pair)
        return pair

    monkeypatch.setattr(processes.os, "pipe", record_pipe)
    monkeypatch.setattr(processes.subprocess, "Popen", Mock(side_effect=OSError("spawn failed")))
    with pytest.raises(OSError, match="spawn failed"):
        Command([sys.executable], cwd=tmp_path, env=dict(os.environ), capture=False)
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_timeout_escalates_ignoring_command(tmp_path: Path) -> None:
    pidfile = tmp_path / "pid"
    outcome = run(
        Unavailable(), _policy(tmp_path),
        [sys.executable, "-c", (
            "import os,signal,time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)"
        )], cwd=tmp_path, env=dict(os.environ), timeout=1,
    )
    assert outcome.returncode == 124
    assert any("timed out" in note for note in outcome.notes)
    assert _gone(int(pidfile.read_text()))


def test_cancel_stops_only_its_command(tmp_path: Path) -> None:
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    started = time.monotonic()
    try:
        with pytest.raises(ExecutionCancelled, match="processes have stopped"):
            run(
                Unavailable(), _policy(tmp_path),
                [sys.executable, "-c", "import time; time.sleep(60)"],
                cwd=tmp_path, env=dict(os.environ),
                cancelled=lambda: time.monotonic() - started > 0.2,
            )
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait()


def test_interrupt_cleanup_does_not_wait_for_the_original_task_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = Command(
        [sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path,
        env=dict(os.environ), capture=False, timeout=60,
    )
    wait = Mock(wraps=command.process.wait)
    monkeypatch.setattr(command.process, "poll", Mock(side_effect=KeyboardInterrupt))
    monkeypatch.setattr(command.process, "wait", wait)
    with pytest.raises(KeyboardInterrupt):
        command.wait()
    assert wait.call_args.kwargs["timeout"] <= 16


def test_successful_leader_cannot_leave_a_background_writer(tmp_path: Path) -> None:
    pidfile = tmp_path / "child"
    child = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    outcome = run(
        Unavailable(), _policy(tmp_path),
        [sys.executable, "-c", (
            "import subprocess,sys,time; from pathlib import Path; "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
            f"\nwhile not Path({str(pidfile)!r}).exists(): time.sleep(.01)"
        )], cwd=tmp_path, env=dict(os.environ),
    )
    assert outcome.returncode == 1
    assert any("background processes" in note for note in outcome.notes)
    assert _gone(int(pidfile.read_text()))


def test_short_lived_helpers_can_finish_after_the_command(tmp_path: Path) -> None:
    helper = (
        "import time; from pathlib import Path; "
        "Path('ready').touch(); time.sleep(.2); Path('finished').touch()"
    )
    outcome = run(
        Unavailable(), _policy(tmp_path),
        [sys.executable, "-c", (
            "import subprocess,sys,time; from pathlib import Path; "
            f"subprocess.Popen([sys.executable, '-c', {helper!r}]); "
            "\nwhile not Path('ready').exists(): time.sleep(.01)"
        )], cwd=tmp_path, env=dict(os.environ),
    )
    assert outcome.returncode == 0
    assert (tmp_path / "finished").exists()
    assert not any("background processes" in note for note in outcome.notes)


def test_worker_sigkill_closes_custody_pipe_and_stops_child(tmp_path: Path) -> None:
    pidfile = tmp_path / "child"
    command = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    worker = subprocess.Popen(
        [sys.executable, "-c", (
            "import os,sys; from pathlib import Path; "
            "from lightcone.engine.sandbox.processes import Command; "
            f"c=Command([sys.executable, '-c', {command!r}], cwd=Path({str(tmp_path)!r}), "
            "env=dict(os.environ), capture=False); c.wait()"
        )], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    pid = None
    try:
        deadline = time.monotonic() + 5
        while not pidfile.exists():
            assert worker.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.02)
        pid = int(pidfile.read_text())
        worker.kill()
        worker.wait()
        while not _gone(pid):
            assert time.monotonic() < deadline
            time.sleep(0.02)
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait()
        if pid is not None and not _gone(pid):
            os.kill(pid, signal.SIGKILL)


def test_stdout_bytes_are_unchanged_by_custody(tmp_path: Path) -> None:
    received: list[bytes] = []
    outcome = run(
        Unavailable(), _policy(tmp_path),
        [sys.executable, "-c", "import os; os.write(1, b'\\xff\\r\\n')"],
        cwd=tmp_path, env=dict(os.environ),
        output=lambda stream, value: received.append(value) if stream == "stdout" else None,
    )
    assert outcome.returncode == 0
    assert b"".join(received) == b"\xff\r\n"


@pytest.mark.parametrize("inspection_delay", [0, 2.2])
def test_container_timeout_uses_its_immutable_runtime_id(
    tmp_path: Path, inspection_delay: float,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.write_text(f"#!{sys.executable}\n" + '''
import json, os, signal, subprocess, sys, time
from pathlib import Path
import psutil
root = Path(os.environ['STATE_ROOT'])
identity = 'a' * 64
argv = sys.argv[1:]
with (root / 'calls').open('a') as log:
    log.write(json.dumps(argv) + '\\n')
if argv[0] == 'run':
    process = subprocess.Popen([sys.executable, '-c',
        'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'],
        start_new_session=True)
    (root / 'payload').write_text(str(process.pid))
    Path(argv[argv.index('--cidfile') + 1]).write_text(identity)
    (root / 'cidfile').write_text(str(Path(argv[argv.index('--cidfile') + 1]).resolve()))
    process.wait()
elif argv[0] == 'inspect':
    time.sleep(float(os.environ['INSPECTION_DELAY']))
    try:
        alive = psutil.Process(int((root / 'payload').read_text())).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        alive = False
    print('true' if alive else 'false')
elif argv[0] == 'kill':
    os.kill(int((root / 'payload').read_text()), signal.SIGKILL)
elif argv[0] == 'rm':
    Path((root / 'cidfile').read_text()).unlink()
''')
    runtime.chmod(0o700)
    command = Command(
        [str(runtime), "run", "--cidfile", CIDFILE, "image"], cwd=tmp_path,
        env={**os.environ, "STATE_ROOT": str(tmp_path), "INSPECTION_DELAY": str(inspection_delay)},
        capture=False, oci_runtime=str(runtime), timeout=1,
    )
    try:
        code, note = command.wait()
        assert code == 124
        assert "timed out" in note
        assert _gone(int((tmp_path / "payload").read_text()))
        import json

        calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
        assert ["stop", "--time", "1", "a" * 64] in calls
        assert ["kill", "a" * 64] in calls
        assert ["rm", "a" * 64] in calls
    finally:
        path = tmp_path / "payload"
        if path.exists() and not _gone(int(path.read_text())):
            os.kill(int(path.read_text()), signal.SIGKILL)


def test_killed_custodian_reports_uncertainty(tmp_path: Path) -> None:
    command = Command(
        [sys.executable, "-c", "pass"], cwd=tmp_path,
        env=dict(os.environ), capture=False,
    )
    command.process.kill()
    with pytest.raises(ExecutionUncertain, match="without confirming"):
        command.wait()


def test_uncertain_cleanup_retains_sandbox_home(tmp_path: Path) -> None:
    from lightcone.engine.sandbox import scope

    home = tmp_path / "home"
    home.mkdir()
    policy = Policy(read=(), write=(), execute=(), tmp_home=home)
    with pytest.raises(ExecutionUncertain):
        with scope(policy):
            raise ExecutionUncertain("worker lost")
    assert home.is_dir()


def test_stream_setup_failure_stops_command_before_propagating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lightcone.engine.sandbox import boundary

    pidfile = tmp_path / "pid"

    def fail(_self: object) -> None:
        deadline = time.monotonic() + 5
        while not pidfile.exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        raise RuntimeError("cannot start stream thread")

    monkeypatch.setattr(boundary._Tail, "start", fail)
    with pytest.raises(RuntimeError, match="stream thread"):
        run(
            Unavailable(), _policy(tmp_path),
            [sys.executable, "-c", (
                "import os,time; from pathlib import Path; "
                f"Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)"
            )], cwd=tmp_path, env=dict(os.environ),
        )
    assert _gone(int(pidfile.read_text()))
