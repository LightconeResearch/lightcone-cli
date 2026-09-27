# lightcone.engine.venue

Site placement policy for local allocations and execution workers. Allocation
lifecycle lives in [compute](compute.md).

`require_compute_node(command)` refuses execution on recognized sites unless the
current host matches `SLURMD_NODENAME` and has a Slurm job ID. A job ID inherited
by a submit shell is insufficient. Workstations outside known sites are permitted.
NERSC is the current entry in the small `_SITES` table.

The guard applies when launching local compute and on actual execution workers,
including the rerun entry point. A login-node driver can inspect projects, submit
Slurm allocations, and attach to a cluster whose workers satisfy placement policy.
No execution command chooses or starts a cluster from ambient Slurm variables.

`tests/test_venue.py` tests the placement policy by varying native environment and
hostname evidence, including a login shell that inherited a job ID.
