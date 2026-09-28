"""Native Slurm allocations with standard Dask processes inside one job step."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from lightcone.engine.compute.model import (
    ComputeError,
    Connection,
    Identity,
    LaunchPlan,
    Offer,
    Request,
    Resources,
    Snapshot,
    positive_int,
)
from lightcone.engine.compute.runtime import open_client, private_directory, read_private_json

_PREFIX = "lc-dask-v1-"
_TOKEN = re.compile(r"[0-9a-f]{32}")
_QUERY_TIMEOUT = 10.0
_SUBMIT_TIMEOUT = 60.0
_ACCEPT_TIMEOUT = 10.0
_MIB = 1024**2
_FINISHED = {
    "BOOT_FAIL",
    "CANCELLED",
    "COMPLETED",
    "DEADLINE",
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "REVOKED",
    "SPECIAL_EXIT",
    "TIMEOUT",
}


def native_environment() -> dict[str, str]:
    """Remove inherited job/request overrides while preserving Slurm authentication."""
    keep = {"SLURM_CONF", "SLURM_CONF_SERVER", "SLURM_JWT"}
    prefixes = ("SBATCH_", "SALLOC_", "SRUN_", "SQUEUE_", "SACCT_", "SCANCEL_", "SLURM_")
    return {
        key: value
        for key, value in os.environ.items()
        if key in keep or (not key.startswith(prefixes) and key != "SLURMD_NODENAME")
    }


def attempt_directory(connection: Connection, identity: Identity, restarts: int) -> Path:
    """Locate connection material for one native allocation incarnation and attempt."""
    root = Path(str(connection.launch.get("connection_root", "")))
    if not root.is_absolute() or ".." in root.parts:
        raise ComputeError("Slurm connection_root must be an absolute path without '..'")
    return (
        root
        / connection.namespace
        / f"{identity.native_id}-{identity.token}"
        / f"attempt-{restarts}"
    )


def _phase(state: str) -> str:
    state = state.split()[0].rstrip("+") if state.strip() else "UNKNOWN"
    if state in _FINISHED:
        return "ended"
    if state in {"PENDING", "CONFIGURING", "REQUEUED", "REQUEUE_FED", "REQUEUE_HOLD", "RESIZING"}:
        return "pending"
    return {"RUNNING": "active", "SUSPENDED": "pending", "COMPLETING": "stopping"}.get(
        state, "unknown"
    )


def _value(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise ComputeError(f"Slurm {name} must be a nonempty string without control characters")
    return value


class SlurmProvider:
    """Submit, observe, and cancel allocations using the selected Slurm authority."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def _scope(self) -> list[str]:
        context = self.connection.context
        if not context:
            return []
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", context) or context == "all":
            raise ComputeError("Slurm context must name one native cluster")
        return [f"--clusters={context}"]

    def _command(
        self, argv: list[str], *, payload: str | None = None, timeout: float = _QUERY_TIMEOUT
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                argv,
                input=payload,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                env=native_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ComputeError(f"cannot complete {argv[0]}: {exc}") from exc
        if result.returncode:
            raise ComputeError(
                f"{argv[0]} failed: {result.stderr.strip() or 'native command failed'}"
            )
        return result

    def plan(self, offer: Offer, request: Request) -> LaunchPlan:
        """Freeze one fixed, homogeneous allocation and its standard Dask launcher."""
        launch = self.connection.launch
        allowed = {
            "python",
            "connection_root",
            "scratch_root",
            "task_slots_per_node",
            "cpu_bind",
            "interface",
            "cwd",
        }
        if extra := launch.keys() - allowed:
            raise ComputeError(f"unknown Slurm launch settings: {', '.join(sorted(extra))}")
        paths: dict[str, str] = {}
        for name in ("python", "connection_root", "scratch_root", "cwd"):
            value = launch.get(name, str(Path.home()) if name == "cwd" else None)
            path = Path(_value(value, name))
            if not path.is_absolute() or ".." in path.parts:
                raise ComputeError(f"Slurm {name} must be an absolute path without '..'")
            paths[name] = str(path)
        config = offer.config
        if extra := config.keys() - {
            "submit",
            "account",
            "partition",
            "qos",
            "constraint",
            "reservation",
        }:
            raise ComputeError(f"unknown Slurm offer settings: {', '.join(sorted(extra))}")
        submit = config.get("submit", "sbatch")
        if submit not in {"sbatch", "salloc"}:
            raise ComputeError("Slurm submit must be sbatch or salloc")
        cpus, memory = offer.resources.cpus, offer.resources.memory
        if memory % _MIB:
            raise ComputeError("Slurm offer memory must be an exact whole number of MiB")
        slots = positive_int(
            launch.get("task_slots_per_node", max(1, cpus - 1)), "task_slots_per_node"
        )
        if slots > cpus:
            raise ComputeError(
                "Slurm task_slots_per_node cannot exceed the allocation CPU envelope"
            )
        binding = launch.get("cpu_bind", "threads")
        if binding not in {"threads", "cores", "none"}:
            raise ComputeError("Slurm cpu_bind must be threads, cores, or none")
        interface = launch.get("interface")
        if interface is not None:
            interface = _value(interface, "interface")
        partition, time_policy = self._time_policy(config.get("partition"))
        seconds = request.seconds or offer.default_seconds
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds_part = divmod(remainder, 60)
        args = self._scope()
        for name in ("account", "qos", "constraint", "reservation"):
            if name in config:
                args.append(f"--{name}={_value(config[name], name)}")
        args += [
            f"--partition={partition}",
            f"--nodes={request.num_nodes}",
            "--ntasks-per-node=1",
            f"--cpus-per-task={cpus}",
            f"--mem={memory // _MIB}M",
            f"--time={hours:02}:{minutes:02}:{seconds_part:02}",
            f"--chdir={paths['cwd']}",
        ]
        return LaunchPlan(
            self.connection,
            offer,
            request,
            seconds,
            {
                "submit": submit,
                "native_args": args,
                **paths,
                "task_slots_per_node": slots,
                "cpu_bind": binding,
                "interface": interface,
                "time_policy": time_policy,
            },
        )

    def _time_policy(self, configured_partition: object) -> tuple[str, dict[str, Any]]:
        partition = None
        if configured_partition is not None:
            partition = _value(configured_partition, "partition")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", partition):
                raise ComputeError("Slurm partition must name one native partition")
        config = self._command(["scontrol", *self._scope(), "show", "config"]).stdout
        global_policy = dict(re.findall(r"(?m)^\s*(OverTimeLimit|KillWait)\s*=\s*(\S+)", config))
        argv = ["scontrol", *self._scope(), "show", "partition"]
        if partition:
            argv.append(partition)
        rows = self._command([*argv, "--oneliner"]).stdout.splitlines()
        candidates = []
        for line in rows:
            values = dict(re.findall(r"(?:^|\s)([A-Za-z][A-Za-z0-9_/:]*)=(\S*)", line))
            if (partition and values.get("PartitionName") == partition) or (
                partition is None and values.get("Default") == "YES"
            ):
                candidates.append(values)
        if len(candidates) != 1:
            raise ComputeError("cannot establish one native partition and its time-limit policy")
        selected = candidates[0]
        partition = selected["PartitionName"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", partition):
            raise ComputeError("Slurm returned an invalid partition name")
        overtime = selected.get("OverTimeLimit", "")
        if overtime == "NONE":
            overtime = global_policy.get("OverTimeLimit", "")
        kill_wait = global_policy.get("KillWait", "")
        if not overtime.isdigit() or not kill_wait.isdigit():
            raise ComputeError("Slurm timed offers require finite OverTimeLimit and KillWait")
        return partition, {
            "partition": partition,
            "overtime_seconds": int(overtime) * 60,
            "kill_wait_seconds": int(kill_wait),
            "evidence": "native_configuration_at_plan",
        }

    def _payload(self, plan: LaunchPlan, token: str) -> list[str]:
        details = plan.details
        argv = [
            "srun",
            f"--ntasks={plan.num_nodes}",
            "--ntasks-per-node=1",
            f"--cpus-per-task={plan.resources.cpus}",
            f"--cpu-bind={details['cpu_bind']}",
            "--kill-on-bad-exit=1",
            details["python"],
            "-P",
            "-m",
            "lightcone.engine.compute.slurm_bootstrap",
            "--submission",
            token,
            "--namespace",
            self.connection.namespace,
            "--connection-root",
            details["connection_root"],
            "--scratch-root",
            details["scratch_root"],
            "--num-nodes",
            str(plan.num_nodes),
            "--cpus",
            str(plan.resources.cpus),
            "--memory-bytes",
            str(plan.resources.memory),
            "--task-slots",
            str(details["task_slots_per_node"]),
        ]
        if details["interface"] is not None:
            argv += ["--interface", details["interface"]]
        return argv

    def launch(self, plan: LaunchPlan) -> Identity:
        """Submit once; preserve the nonce when native acceptance is uncertain."""
        if plan.connection != self.connection:
            raise ComputeError("Slurm launch plan belongs to another connection")
        token = uuid.uuid4().hex
        details = plan.details
        logs = private_directory(
            Path(details["connection_root"]) / "submissions" / token, create=True
        )
        common = [
            *details["native_args"],
            f"--job-name={_PREFIX}{token}",
            "--comment=lightcone:v1:kind=dask",
        ]
        payload = self._payload(plan, token)
        if details["submit"] == "sbatch":
            argv = ["sbatch", "--parsable", "--no-requeue", *common, f"--output={logs}/%j.out"]
            script = "#!/bin/bash\nset -euo pipefail\numask 077\nexec " + shlex.join(payload) + "\n"
            try:
                result = self._command(argv, payload=script, timeout=_SUBMIT_TIMEOUT)
                match = re.fullmatch(r"([0-9]+)(?:;([^;\s]+))?", result.stdout.strip())
                if match and (
                    not self.connection.context or match[2] in (None, self.connection.context)
                ):
                    return Identity(self.connection.namespace, match[1], token)
                reason = "sbatch did not return an unambiguous allocation ID"
            except ComputeError as exc:
                reason = str(exc)
            return self._recover_or_raise(token, reason)

        argv = ["salloc", *common, "--kill-command=TERM", *payload]
        try:
            with (logs / "salloc.log").open("xb") as output:
                os.chmod(logs / "salloc.log", 0o600)
                process = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=native_environment(),
                )
        except OSError as exc:
            raise ComputeError(f"cannot start salloc: {exc}", submission_token=token) from exc
        deadline = time.monotonic() + _ACCEPT_TIMEOUT
        while time.monotonic() < deadline:
            try:
                matches = self._find_token(token)
            except ComputeError:
                matches = []
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise ComputeError(
                    "multiple native allocations have this submission token", submission_token=token
                )
            if process.poll() is not None:
                return self._recover_or_raise(
                    token, f"salloc exited with status {process.returncode}"
                )
            time.sleep(0.2)
        return self._recover_or_raise(
            token, "salloc acceptance could not be established before the deadline"
        )

    def _recover_or_raise(self, token: str, reason: str) -> Identity:
        try:
            matches = self._find_token(token, history=True)
        except ComputeError:
            matches = []
        if len(matches) == 1:
            return matches[0]
        raise ComputeError(
            f"{reason}; submission outcome is uncertain. Do not resubmit automatically; "
            f"reconcile native job name {_PREFIX}{token} (submission token {token}).",
            submission_token=token,
        )

    def _live(self, native_id: str | None = None) -> list[dict[str, str]]:
        argv = [
            "squeue",
            *self._scope(),
            "--noheader",
            f"--user={os.getuid()}",
            "--format=%i|%j|%U|%T",
        ]
        rows = []
        for line in self._command(argv).stdout.splitlines():
            if not line.strip() or line.startswith("CLUSTER:"):
                continue
            fields = [value.strip() for value in line.split("|")]
            if len(fields) < 4:
                raise ComputeError("squeue returned an unrecognized allocation record")
            job_id, name, uid, state = fields[0], "|".join(fields[1:-2]), fields[-2], fields[-1]
            if native_id is not None and job_id != native_id:
                continue
            if not name.startswith(_PREFIX):
                continue
            if not re.fullmatch(r"[0-9]+", job_id) or not _TOKEN.fullmatch(
                name.removeprefix(_PREFIX)
            ):
                continue
            rows.append({"JobId": job_id, "JobName": name, "UID": uid, "JobState": state})
        return rows

    def _history(
        self, identity: Identity | None = None, *, token: str | None = None
    ) -> list[dict[str, str]]:
        argv = [
            "sacct",
            *self._scope(),
            "--noheader",
            "--parsable2",
            "--allocations",
            "--duplicates",
            f"--uid={os.getuid()}",
            "--starttime=1970-01-01",
            "--format=JobIDRaw,JobName%128,UID,State,Submit",
        ]
        argv.append(f"--jobs={identity.native_id}" if identity else f"--name={_PREFIX}{token}")
        rows = []
        for line in self._command(argv).stdout.splitlines():
            if not line.strip():
                continue
            fields = line.split("|")
            if len(fields) < 5:
                raise ComputeError("sacct returned an unrecognized allocation record")
            job_id = fields[0].strip()
            name = "|".join(fields[1:-3]).strip()
            uid, state, submitted = (value.strip() for value in fields[-3:])
            if not re.fullmatch(r"[0-9]+", job_id):
                continue
            rows.append(
                {
                    "JobId": job_id,
                    "JobName": name,
                    "UID": uid,
                    "JobState": state,
                    "SubmitTime": submitted,
                }
            )
        return rows

    def _find_token(self, token: str, *, history: bool = False) -> list[Identity]:
        rows = self._live()
        matches = {
            Identity(self.connection.namespace, row["JobId"], token)
            for row in rows
            if row["JobName"] == _PREFIX + token and row["UID"] == str(os.getuid())
        }
        if matches or not history:
            return list(matches)
        return list(
            {
                Identity(self.connection.namespace, row["JobId"], token)
                for row in self._history(token=token)
                if row["JobName"] == _PREFIX + token and row["UID"] == str(os.getuid())
            }
        )

    def _validate_identity(self, identity: Identity) -> None:
        if (
            identity.namespace != self.connection.namespace
            or identity.host
            or not re.fullmatch(r"[0-9]+", identity.native_id)
            or not _TOKEN.fullmatch(identity.token)
        ):
            raise ComputeError(
                "cluster ID does not identify an allocation on this Slurm connection"
            )

    def _validate_row(self, identity: Identity, row: Mapping[str, str]) -> None:
        if (
            row.get("JobId") != identity.native_id
            or row.get("JobName") != _PREFIX + identity.token
            or row.get("UID") != str(os.getuid())
        ):
            raise ComputeError(
                "Slurm allocation ownership or submission token does not match this cluster ID"
            )

    def _control(self, identity: Identity) -> dict[str, str]:
        result = self._command(
            ["scontrol", *self._scope(), "show", "job", identity.native_id, "--oneliner"]
        )
        rows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(rows) != 1:
            raise ComputeError("Slurm did not return exactly one allocation record")
        values = dict(re.findall(r"(?:^|\s)([A-Za-z][A-Za-z0-9_/:]*)=(\S*)", rows[0]))
        owner = re.fullmatch(r".*\(([0-9]+)\)", values.get("UserId", ""))
        values["UID"] = owner[1] if owner else ""
        self._validate_row(identity, values)
        return values

    def _snapshot(self, identity: Identity, row: Mapping[str, str]) -> Snapshot:
        self._validate_row(identity, row)
        state = row.get("JobState", "UNKNOWN")
        nodes_text = row.get("NumNodes", "")
        nodes = int(nodes_text) if nodes_text.isdigit() and int(nodes_text) > 0 else None
        cpus = row.get("CPUs/Task", "")
        memory = re.fullmatch(r"([0-9]+)([KMGT]?)", row.get("MinMemoryNode", ""))
        resources = None
        if cpus.isdigit() and int(cpus) > 0 and memory:
            scale = {"": _MIB, "K": 1024, "M": _MIB, "G": 1024**3, "T": 1024**4}
            resources = Resources(int(cpus), int(memory[1]) * scale[memory[2]])
        return Snapshot(
            identity,
            _phase(state),
            resources,
            nodes,
            evidence="requested" if resources else "unknown",
            native_state=state,
            reason=row.get("Reason", "").replace("None", ""),
        )

    def discover(self) -> Sequence[Snapshot]:
        """List managed allocations directly from this user's live Slurm jobs."""
        snapshots = []
        for row in self._live():
            identity = Identity(
                self.connection.namespace, row["JobId"], row["JobName"].removeprefix(_PREFIX)
            )
            self._validate_row(identity, row)
            snapshots.append(self.inspect(identity))
        return snapshots

    def inspect(self, identity: Identity) -> Snapshot:
        """Read current native state, using accounting only after live absence."""
        self._validate_identity(identity)
        live = [row for row in self._live(identity.native_id) if row["JobId"] == identity.native_id]
        if len(live) > 1:
            raise ComputeError("Slurm returned multiple live records for this allocation")
        if live:
            self._validate_row(identity, live[0])
            return self._snapshot(identity, self._control(identity))
        matches = [
            row
            for row in self._history(identity)
            if row["JobId"] == identity.native_id
            and row["JobName"] == _PREFIX + identity.token
            and row["UID"] == str(os.getuid())
        ]
        if matches:
            latest = max(matches, key=lambda row: row["SubmitTime"])
            snapshot = self._snapshot(identity, latest)
            if snapshot.phase != "ended":
                snapshot.phase = "unknown"
                snapshot.reason = "allocation is absent from squeue but accounting is not terminal"
            return snapshot
        return Snapshot(
            identity,
            "unknown",
            reason="allocation is absent from live jobs and available accounting",
        )

    @contextmanager
    def connect(self, identity: Identity, *, timeout: float = 10) -> Iterator[Any]:
        """Borrow the TLS scheduler belonging to the current, running native attempt."""
        self._validate_identity(identity)
        native = self._control(identity)
        if _phase(native.get("JobState", "UNKNOWN")) != "active":
            raise ComputeError("Slurm allocation is not running", cluster_id=identity.encode())
        restart_text = native.get("Restarts", "")
        if not restart_text.isdigit():
            raise ComputeError("Slurm did not identify the current allocation attempt")
        restarts = int(restart_text)
        directory = attempt_directory(self.connection, identity, restarts)
        metadata = read_private_json(directory / "identity.json")
        expected = {
            "namespace": identity.namespace,
            "native_id": identity.native_id,
            "token": identity.token,
            "uid": os.getuid(),
            "restarts": restarts,
        }
        if any(
            type(metadata.get(key)) is not type(value) or metadata.get(key) != value
            for key, value in expected.items()
        ):
            raise ComputeError("Slurm connection material belongs to another allocation attempt")
        scheduler_id = metadata.get("scheduler_id")
        expected_workers = metadata.get("num_nodes")
        if (
            not isinstance(scheduler_id, str)
            or type(expected_workers) is not int
            or expected_workers < 1
        ):
            raise ComputeError("Slurm connection material is incomplete")
        client = open_client(directory, scheduler_id, timeout=timeout)
        try:
            current = self._control(identity)
            if (
                current.get("Restarts") != restart_text
                or _phase(current.get("JobState", "UNKNOWN")) != "active"
            ):
                raise ComputeError("Slurm allocation changed during connection")
            yield client
        finally:
            client.close(timeout=min(timeout, 5))

    def terminate(self, identity: Identity) -> None:
        """Cancel the allocation after fresh native ownership and nonce validation."""
        snapshot = self.inspect(identity)
        if snapshot.phase == "ended":
            return
        if snapshot.phase == "unknown":
            raise ComputeError("cannot cancel an allocation whose native identity/state is unknown")
        self._command(
            [
                "scancel",
                *self._scope(),
                f"--user={os.getuid()}",
                f"--name={_PREFIX}{identity.token}",
                identity.native_id,
            ]
        )
