"""Native Slurm allocations with standard Dask processes inside one job step."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from functools import cached_property
from pathlib import Path
from typing import Any

from lightcone.engine.compute.model import (
    ComputeError,
    Identity,
    LaunchPlan,
    Offer,
    Request,
    Resources,
    Snapshot,
    positive_int,
    validate_name,
)
from lightcone.engine.compute.runtime import (
    NOT_STARTED,
    configured_directory,
    open_client,
    private_directory,
    read_private_json,
)

_PREFIX = "lc-v1-"
_COMMENT_PREFIX = "lightcone:v1:kind=dask:token="
_TOKEN = re.compile(r"[0-9a-f]{32}")
_JOB_NAME = re.compile(r"lc-v1-([a-z](?:[a-z0-9-]{0,61}[a-z0-9])?)")
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


def attempt_directory(root: Path, identity: Identity, restarts: int) -> Path:
    """Locate connection material for one native allocation incarnation and attempt."""
    return (
        configured_directory(root)
        / "slurm"
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


def _native_gpus(row: Mapping[str, str]) -> tuple[str, int] | None:
    """Read a per-node GPU count, never divide an aggregate into invented grants."""
    for field in ("TresPerNode", "Gres"):
        value = row.get(field, "")
        counts = []
        names = set()
        for entry in value.split(","):
            if field == "TresPerNode":
                entry = entry.removeprefix("gres/").removeprefix("gres:")
            if not entry.startswith("gpu"):
                continue
            match = re.fullmatch(
                r"gpu(?::([A-Za-z0-9][A-Za-z0-9_.-]*))?[:=]([0-9]+)", entry,
            )
            if match is None:
                return None
            names.add(match[1] or "GPU")
            counts.append(int(match[2]))
        if counts:
            if "GPU" in names and len(names) > 1:
                return None  # A total plus typed subcounts must not be double-counted.
            return next(iter(names)) if len(names) == 1 else "GPU", sum(counts)
        if field == "Gres" and value in {"(null)", "N/A", "none"}:
            return "GPU", 0
    # Complete native TRES with no GPU entry proves a CPU-only job. An
    # aggregate GPU total does not prove a homogeneous per-node allocation.
    for field in ("ReqTRES", "AllocTRES", "TRES"):
        value = row.get(field, "")
        if value and value not in {"(null)", "N/A"}:
            entries = value.split(",")
            if any(entry.startswith("gres/gpu") for entry in entries):
                return None
            if all("=" in entry for entry in entries) and any(
                entry.startswith("cpu=") for entry in entries
            ):
                return "GPU", 0
            return None
    return None


class SlurmProvider:
    """Submit, observe, and cancel allocations in the Slurm environment lc runs in."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @cached_property
    def _uid(self) -> int:
        """Resolve ownership where native commands execute, once per provider."""
        value = self._command(["id", "-u"]).stdout.strip()
        if not re.fullmatch(r"[0-9]+", value):
            raise ComputeError("id -u did not return a numeric Slurm command user ID")
        return int(value)

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
        config = offer.config
        if extra := config.keys() - {
            "submit",
            "account",
            "partition",
            "qos",
            "constraint",
            "reservation",
            "gpu_type",
            "python",
            "scratch_root",
            "task_slots_per_node",
            "interface",
            "cwd",
        }:
            raise ComputeError(f"unknown Slurm offer settings: {', '.join(sorted(extra))}")
        # The defaults assume a home directory shared by login and compute
        # nodes: workers run the driver's own installation, so they match it
        # exactly, and rendezvous under its home. Scratch left unset is chosen
        # by each node, whose temporary directory may not be the driver's.
        defaults = {"python": sys.executable, "cwd": str(Path.home())}
        paths: dict[str, str | None] = {"connection_root": str(configured_directory(self.root))}
        for name in ("python", "scratch_root", "cwd"):
            if name not in config and name not in defaults:
                paths[name] = None
                continue
            path = Path(_value(config.get(name, defaults.get(name)), name))
            if name == "scratch_root":
                path = configured_directory(path)
            elif not path.is_absolute() or ".." in path.parts:
                raise ComputeError(f"Slurm {name} must be an absolute path without '..'")
            paths[name] = str(path)
        submit = config.get("submit", "sbatch")
        if submit not in {"sbatch", "salloc"}:
            raise ComputeError("Slurm submit must be sbatch or salloc")
        gpu_type = config.get("gpu_type")
        if gpu_type is not None:
            if not isinstance(gpu_type, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]*", gpu_type,
            ):
                raise ComputeError("Slurm gpu_type must name one native GPU GRES type")
            if not offer.resources.gpus:
                raise ComputeError("Slurm gpu_type requires accelerator resources")
        elif (offer.resources.accelerator_name or "GPU").casefold() != "gpu":
            raise ComputeError("a named Slurm accelerator offer requires its native gpu_type")
        gres = (
            f"gpu:{gpu_type + ':' if gpu_type else ''}{offer.resources.gpus}"
            if offer.resources.gpus else "none"
        )
        cpus, memory = offer.resources.cpus, offer.resources.memory_bytes
        if memory % _MIB:
            raise ComputeError("Slurm offer memory must be an exact whole number of MiB")
        slots = positive_int(
            config.get("task_slots_per_node", max(1, cpus - 1)), "task_slots_per_node"
        )
        if slots > cpus:
            raise ComputeError(
                "Slurm task_slots_per_node cannot exceed the allocation CPU envelope"
            )
        interface = config.get("interface")
        if interface is not None:
            interface = _value(interface, "interface")
        seconds = request.seconds or offer.time.default_seconds
        if seconds is None or offer.time.idle is not None:
            raise ComputeError(
                "Slurm allocations end at their native walltime: "
                "set the offer's time.default and remove time.idle"
            )
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds_part = divmod(remainder, 60)
        args: list[str] = []
        for name in ("account", "qos", "constraint", "reservation"):
            if name in config:
                args.append(f"--{name}={_value(config[name], name)}")
        if "partition" in config:
            partition = _value(config["partition"], "partition")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", partition):
                raise ComputeError("Slurm partition must name one native partition")
            args.append(f"--partition={partition}")
        args += [
            f"--nodes={request.num_nodes}",
            "--ntasks-per-node=1",
            f"--cpus-per-task={cpus}",
            f"--mem={memory // _MIB}M",
            f"--time={hours:02}:{minutes:02}:{seconds_part:02}",
            f"--chdir={paths['cwd']}",
        ]
        if offer.resources.gpus:
            args.append(f"--gres={gres}")
        return LaunchPlan(
            offer=offer,
            request=request,
            seconds=seconds,
            details={
                "submit": submit,
                "native_args": args,
                "gres": gres,
                **paths,
                "task_slots_per_node": slots,
                "interface": interface,
            },
        )

    def _payload(self, plan: LaunchPlan, token: str) -> list[str]:
        details = plan.details
        argv = [
            "srun",
            f"--ntasks={plan.num_nodes}",
            "--ntasks-per-node=1",
            f"--cpus-per-task={plan.resources.cpus}",
            f"--gres={details['gres']}",
            # Each rank and its worker inherit the node's allocated CPU mask.
            "--cpu-bind=threads",
            # Losing a rank must not terminate healthy ranks and the scheduler.
            "--kill-on-bad-exit=0",
            "--wait=0",
            details["python"],
            "-P",
            "-m",
            "lightcone.engine.compute.slurm_bootstrap",
            "--submission",
            token,
            "--connection-root",
            details["connection_root"],
            "--num-nodes",
            str(plan.num_nodes),
            "--cpus",
            str(plan.resources.cpus),
            "--memory-bytes",
            str(plan.resources.memory_bytes),
            "--gpus",
            str(plan.resources.gpus),
            "--task-slots",
            str(details["task_slots_per_node"]),
        ]
        if details["scratch_root"] is not None:
            argv += ["--scratch-root", details["scratch_root"]]
        if details["interface"] is not None:
            argv += ["--interface", details["interface"]]
        return argv

    def launch(self, plan: LaunchPlan) -> Identity:
        """Submit once; preserve the nonce when native acceptance is uncertain."""
        if plan.offer.provider != "slurm" or plan.details["connection_root"] != str(
            configured_directory(self.root)
        ):
            raise ComputeError("Slurm launch plan belongs to another provider or connection root")
        token = uuid.uuid4().hex
        name = plan.name if plan.name is not None else f"lc-{token[:12]}"
        validate_name(name)
        job_name = f"{_PREFIX}{name}"
        details = plan.details
        logs = private_directory(
            Path(details["connection_root"]) / "submissions" / token, create=True
        )
        common = [
            *details["native_args"],
            f"--job-name={job_name}",
            f"--comment={_COMMENT_PREFIX}{token}",
        ]
        payload = self._payload(plan, token)
        if details["submit"] == "sbatch":
            argv = ["sbatch", "--parsable", "--no-requeue", *common, f"--output={logs}/%j.out"]
            script = "#!/bin/bash\nset -euo pipefail\numask 077\nexec " + shlex.join(payload) + "\n"
            try:
                result = self._command(argv, payload=script, timeout=_SUBMIT_TIMEOUT)
                native_id = result.stdout.strip()
                if re.fullmatch(r"[0-9]+", native_id):
                    return Identity(
                        provider="slurm", native_id=native_id, token=token, name=name,
                    )
                reason = "sbatch did not return an unambiguous allocation ID"
            except ComputeError as exc:
                reason = str(exc)
            return self._recover_or_raise(token, name, reason)

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
                matches = self._find_token(token, name)
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
                    token, name, f"salloc exited with status {process.returncode}"
                )
            time.sleep(0.2)
        return self._recover_or_raise(
            token, name, "salloc acceptance could not be established before the deadline"
        )

    def _recover_or_raise(self, token: str, name: str, reason: str) -> Identity:
        try:
            matches = self._find_token(token, name, history=True)
        except ComputeError:
            matches = []
        if len(matches) == 1:
            return matches[0]
        raise ComputeError(
            f"{reason}; submission outcome is uncertain. Do not resubmit automatically; "
            f"reconcile native job name {_PREFIX}{name} and comment token {token}.",
            submission_token=token,
        )

    def _live(self, native_id: str | None = None) -> list[dict[str, str]]:
        argv = [
            "squeue",
            "--noheader",
            f"--user={self._uid}",
            "--format=%i|%128j|%U|%T|%128k",
        ]
        rows = []
        for line in self._command(argv).stdout.splitlines():
            if not line.strip():
                continue
            fields = [value.strip() for value in line.split("|", 4)]
            if len(fields) != 5:
                raise ComputeError("squeue returned an unrecognized allocation record")
            job_id, name, uid, state, comment = fields
            if native_id is not None and job_id != native_id:
                continue
            if not re.fullmatch(r"[0-9]+", job_id) or not _JOB_NAME.fullmatch(name):
                continue
            rows.append({
                "JobId": job_id, "JobName": name, "UID": uid,
                "JobState": state, "Comment": comment,
            })
        return rows

    def _history(
        self, identity: Identity | None = None, *, job_name: str | None = None
    ) -> list[dict[str, str]]:
        argv = [
            "sacct",
            "--noheader",
            "--parsable2",
            "--allocations",
            "--duplicates",
            f"--uid={self._uid}",
            "--starttime=1970-01-01",
            "--format=JobIDRaw,JobName%128,UID,State,Submit,Comment%128",
        ]
        argv.append(f"--jobs={identity.native_id}" if identity else f"--name={job_name}")
        rows = []
        for line in self._command(argv).stdout.splitlines():
            if not line.strip():
                continue
            fields = [value.strip() for value in line.split("|", 5)]
            if len(fields) != 6:
                raise ComputeError("sacct returned an unrecognized allocation record")
            job_id, name, uid, state, submitted, comment = fields
            if not re.fullmatch(r"[0-9]+", job_id):
                continue
            rows.append(
                {
                    "JobId": job_id,
                    "JobName": name,
                    "UID": uid,
                    "JobState": state,
                    "SubmitTime": submitted,
                    "Comment": comment,
                }
            )
        return rows

    def _find_token(self, token: str, name: str, *, history: bool = False) -> list[Identity]:
        rows = self._live()
        job_name = f"{_PREFIX}{name}"
        matches = {
            Identity(provider="slurm", native_id=row["JobId"], token=token, name=name)
            for row in rows
            if row["JobName"] == job_name and row["UID"] == str(self._uid)
            and row["Comment"] == _COMMENT_PREFIX + token
        }
        if matches or not history:
            return list(matches)
        return list(
            {
                Identity(provider="slurm", native_id=row["JobId"], token=token, name=name)
                for row in self._history(job_name=job_name)
                if row["JobName"] == job_name and row["UID"] == str(self._uid)
                and row["Comment"] == _COMMENT_PREFIX + token
            }
        )

    def _validate_identity(self, identity: Identity) -> None:
        if (
            identity.provider != "slurm"
            or identity.host
            or not re.fullmatch(r"[0-9]+", identity.native_id)
            or not _TOKEN.fullmatch(identity.token)
            or not _JOB_NAME.fullmatch(_PREFIX + identity.name)
        ):
            raise ComputeError(
                "cluster ID does not identify a Slurm allocation"
            )

    def _validate_row(self, identity: Identity, row: Mapping[str, str]) -> None:
        if (
            row.get("JobId") != identity.native_id
            or row.get("JobName") != _PREFIX + identity.name
            or row.get("Comment") != _COMMENT_PREFIX + identity.token
            or row.get("UID") != str(self._uid)
        ):
            raise ComputeError(
                "Slurm allocation ownership, submission token, or name "
                "does not match this cluster ID"
            )

    def _control(self, identity: Identity) -> dict[str, str]:
        result = self._command(["scontrol", "show", "job", identity.native_id])
        rows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if sum(row.startswith("JobId=") for row in rows) != 1:
            raise ComputeError("Slurm did not return exactly one allocation record")
        # Slurm prints Comment on its own line. Preserve it before parsing
        # other fields so comment text such as "extra=value" cannot be truncated.
        comments = [row.removeprefix("Comment=") for row in rows if row.startswith("Comment=")]
        fields = " ".join(row for row in rows if not row.startswith("Comment="))
        values = dict(re.findall(r"(?:^|\s)([A-Za-z][A-Za-z0-9_/:]*)=(\S*)", fields))
        values["Comment"] = comments[0] if len(comments) == 1 else ""
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
        accelerators = _native_gpus(row)
        resources = None
        if (
            cpus.isdigit() and int(cpus) > 0 and memory and int(memory[1]) > 0
            and accelerators is not None
        ):
            scale = {"": _MIB, "K": 1024, "M": _MIB, "G": 1024**3, "T": 1024**4}
            resources = Resources.from_bytes(
                cpus=int(cpus), memory_bytes=int(memory[1]) * scale[memory[2]],
                accelerator_name=accelerators[0], gpus=accelerators[1],
            )
        return Snapshot(
            identity=identity,
            phase=_phase(state),
            resources=resources,
            num_nodes=nodes,
            evidence="requested" if resources else "unknown",
            native_state=state,
            reason=row.get("Reason", "").replace("None", ""),
        )

    def discover(self) -> Sequence[Snapshot]:
        """List managed allocations from one query of this user's live Slurm jobs.

        Each job's controller record, a single-job lookup, supplies its
        requested resources and re-verifies its owner, name and token.
        """
        snapshots = []
        for row in self._live():
            name = row["JobName"].removeprefix(_PREFIX)
            comment = row["Comment"]
            token = comment.removeprefix(_COMMENT_PREFIX)
            if not comment.startswith(_COMMENT_PREFIX) or not _TOKEN.fullmatch(token):
                raise ComputeError(
                    f"Slurm job {row['JobId']} ({row['JobName']}) has a missing or malformed "
                    "submission token in Comment; its allocation identity is unknown"
                )
            identity = Identity(provider="slurm", native_id=row["JobId"], token=token, name=name)
            self._validate_row(identity, row)
            snapshots.append(self._snapshot(identity, self._control(identity)))
        return snapshots

    def inspect(self, identity: Identity) -> Snapshot:
        """Read current native state, using accounting only after live absence."""
        self._validate_identity(identity)
        if self._is_live(identity):
            return self._snapshot(identity, self._control(identity))
        return self._recorded(identity)

    def _is_live(self, identity: Identity) -> bool:
        live = [row for row in self._live(identity.native_id) if row["JobId"] == identity.native_id]
        if len(live) > 1:
            raise ComputeError("Slurm returned multiple live records for this allocation")
        if live:
            self._validate_row(identity, live[0])
        return bool(live)

    def _recorded(self, identity: Identity) -> Snapshot:
        matches = [
            row
            for row in self._history(identity)
            if row["JobId"] == identity.native_id
            and row["JobName"] == _PREFIX + identity.name
            and row["Comment"] == _COMMENT_PREFIX + identity.token
            and row["UID"] == str(self._uid)
        ]
        if matches:
            latest = max(matches, key=lambda row: row["SubmitTime"])
            snapshot = self._snapshot(identity, latest)
            if snapshot.phase != "ended":
                snapshot.phase = "unknown"
                snapshot.reason = "allocation is absent from squeue but accounting is not terminal"
            return snapshot
        return Snapshot(
            identity=identity,
            phase="unknown",
            reason=(
                "allocation is absent from live jobs and its submission token "
                "cannot be verified in accounting"
            ),
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
        directory = attempt_directory(self.root, identity, restarts)
        if not (directory / "identity.json").exists():
            raise ComputeError(NOT_STARTED, cluster_id=identity.encode())
        metadata = read_private_json(directory / "identity.json")
        expected = {
            "native_id": identity.native_id,
            "token": identity.token,
            "uid": self._uid,
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
        """Cancel the allocation after fresh native ownership and nonce validation.

        A live job whose owner, name and token match is cancelled whatever
        state Slurm reports; only a job absent from live jobs must prove from
        accounting that it ended.
        """
        self._validate_identity(identity)
        if not self._is_live(identity):
            if self._recorded(identity).phase == "ended":
                return
            raise ComputeError("cannot cancel an allocation whose native identity/state is unknown")
        if _phase(self._control(identity).get("JobState", "UNKNOWN")) == "ended":
            return
        self._command(
            [
                "scancel",
                "--ctld",
                f"--user={self._uid}",
                f"--name={_PREFIX}{identity.name}",
                identity.native_id,
            ]
        )
