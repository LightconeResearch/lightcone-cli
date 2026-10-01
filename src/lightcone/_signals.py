"""Start every lc process with an empty signal mask.

A blocked-signal mask survives ``fork`` and ``exec``, so whatever lc
inherits it hands to every uv, recipe and Dask worker it spawns. Some
Slurm sites start a step's first process with ``SIGCHLD`` blocked (seen
on Leonardo: ``SigBlk 0x10000``); a shell clears that for the children it
forks but not across ``exec``. A uv that inherits the block never learns
that its bytecode-compile workers exited, so ``uv sync --compile-bytecode``
waits on zombies forever while holding the cache lock.

Nothing lc runs blocks a signal on purpose, so an inherited mask is never
configuration — only a launcher's leftover state. Each process entry point
clears it before spawning anything (and before starting threads, which
take their mask from the thread that creates them).
"""

from __future__ import annotations

import signal


def clear_inherited_mask() -> None:
    """Unblock every signal on the calling thread.

    Call from a process entry point, on the main thread, before any thread
    or child process is started.
    """
    signal.pthread_sigmask(signal.SIG_SETMASK, ())
