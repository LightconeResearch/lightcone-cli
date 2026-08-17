# Findings: hermeticity enforcement without a container stack

- **Status:** research findings — evidence base for a future hermeticity
  revision of [execution-environment.md](execution-environment.md)
  (not yet normative; nothing here is implemented)
- **Date:** 2026-08-15
- **Method:** 4 web-research agents (syscall-trace capture / ReproZip;
  Landlock; bubblewrap; macOS Seatbelt), each verifying against primary
  sources (kernel docs, man pages, project repos, shipped
  implementations); plus the prior fabric/venue analysis in the spec.
- **The requirement (owner-stated):** it must be *mechanically
  impossible* for an output to be materialized using tools or files
  outside the declared environment without that fact being caught —
  the property containerization provided by brute force — **without**
  requiring a container stack on laptops, and ideally packaged inside
  `lc` so it is 100% transparent to the user.

## TL;DR

**Landlock (Linux) + Seatbelt (macOS) deliver the "can't use stuff
outside the environment" guarantee natively, unprivileged, with
nothing to install — and both are battle-tested in exactly this
embedded-in-a-CLI role** (OpenAI Codex ships Landlock; Anthropic's
sandbox-runtime/Claude Code and Codex both ship Seatbelt; Bazel,
Chrome, and Arch's pacman are further production users). Bubblewrap is
a stronger isolation model but is blocked out-of-the-box on stock
Ubuntu 24.04/WSL2-Ubuntu for a wheel-shipped binary — usable only as
an opportunistic upgrade. Syscall tracing (ReproZip-style) remains
valuable as *attestation*, but with enforcement this widely available
it demotes from primary mechanism to optional evidence. The proposed
shape is a per-output **hermeticity ladder** recorded in the manifest:
`enforced` / `traced-clean` / `open` — never silent.

## 1. What "capture the entire environment" actually decomposes into

The container's guarantee is really two separable properties:

- **Prevention**: a recipe *cannot* use anything outside the declared
  set — undeclared tools/files don't exist in its world, so
  irreproducibility is caught at materialization time as a loud
  failure, exactly when the agent introduces it.
- **Detection**: if a recipe *did* touch something outside the
  declared set, that fact is recorded and `lc verify` refuses to call
  the output reproducible.

Leakage channels, in order of practical frequency: PATH executables
(host `latex`, `module`-loaded tools, `/usr/local/bin` strays);
Python-level leakage (`PYTHONPATH`, stray user-site packages);
filesystem at large (absolute-path invocations, undeclared data files,
dlopened host libraries); network fetches mid-recipe (irreproducible
inputs); ambient env vars. Note the honest baseline: even full
containers never closed kernel, GPU driver, or CPU-dispatch channels —
those remain attestation under every mechanism.

## 2. Landlock (Linux) — the transparent default

A kernel LSM syscall API (mainline since **5.13**, June 2021):
a process self-restricts with an allowlist of path-scoped access
rights before exec'ing the recipe; restrictions are inherited by the
entire process tree and can never be removed, only tightened.

**Why it uniquely meets the transparency bar:**
- **Zero binaries, zero privileges, zero setup.** Applied via three
  syscalls (+`prctl(PR_SET_NO_NEW_PRIVS)`) — ~100 lines of stdlib
  ctypes in a `subprocess.Popen(preexec_fn=…)` hook (child is
  single-threaded there, so per-thread semantics are moot). Pure
  Python; ships inside `lc` itself. PyPI bindings exist
  (`landlock` — ctypes/MIT; `py-landlock` — covers net+scoping) but
  vendoring the ~100 lines is the low-dependency path.
- **Works where bubblewrap doesn't**: stock Ubuntu 24.04, WSL2, and
  (kernel-permitting) inside containers — it needs no user
  namespaces and no AppArmor blessing.
- **The enforcement fits the need exactly**: deny
  `LANDLOCK_ACCESS_FS_EXECUTE` outside {venv, uv-managed interpreter,
  minimal OS baseline} ⇒ `subprocess.run("latex")` on an undeclared
  tool fails instantly with `PermissionError`; deny reads outside
  {project, venv, baseline} ⇒ undeclared data files are caught too —
  which quietly resurrects the old `$PWD`-mount path discipline.
- **Overhead ≈ zero** at workload level: in-kernel checks at
  open/exec time only — no ptrace context switches (2025 kernel work
  moved worst cases toward O(1) per open).

**ABI/kernel availability (verified):** ABI 1 (5.13, full R/W/X file
rights) → ABI 2 (5.19, REFER) → ABI 3 (6.2, TRUNCATE) → ABI 4 (6.7,
TCP bind/connect) → ABI 5 (6.10, IOCTL_DEV) → ABI 6 (6.12, signal /
abstract-socket scoping) → ABI 7 (6.15, **audit of denials**) → ABI 8
(TSYNC; release carrying it to be re-verified). Distro reality:
Ubuntu 22.04 = ABI 1 (HWE 6.8 = 4); Ubuntu 24.04 = ABI 4+; Debian 12
= 2, Debian 13 = 6; Fedora ≥ 7; Arch 7–8 (pacman 7 itself now uses
Landlock); **WSL2 (msft 6.6 kernel) = ABI 3**. **Floor: ABI 1** —
anything higher would exclude 5.14-based HPC kernels.

**Limits, stated honestly:**
- Metadata is visible (`stat`/`access`/`chdir` are not restrictable) —
  files can be *seen*, not opened. Fine for fail-loudly-on-use; not
  an information-hiding boundary.
- Not adversarial-proof: memfd-exec (`LANDLOCK_SCOPE_MEMFD_EXEC` is
  still an RFC), interpreter-reads-script (EXECUTE gates `execve`,
  not interpretation), fd smuggling. Irrelevant to the accidental-
  leakage threat model; must be stated in the spec.
- Docker's default seccomp profile historically does not allowlist
  the `landlock_*` syscalls → probe at runtime inside pods rather
  than assume (custom seccomp profile is the hub-chart fix).
- Denials on ABI <7 surface only as EACCES/EXDEV in the recipe (the
  6.15 audit stream needs root to read); on ABI 1, cross-directory
  rename/link out of allowed trees is denied wholesale (no REFER).
- NFS/Lustre: hooks VFS path resolution, filesystem-agnostic in
  principle; **no field reports either way — Perlmutter smoke test
  required.** SLE-15's `CONFIG_LSM` inclusion of landlock is
  **unverified** (SLES 16 confirms it) — same probe.

**Precedent:** OpenAI Codex CLI's Linux sandbox uses the kernel
author's own `rust-landlock` crate (`ABI::V5`, best-effort, full-FS
read + write-allowlist, network cut via a separate seccomp filter);
its filed issues are a free lessons-learned list — most importantly
the **silent-best-effort trap**: best-effort setup "succeeding" on a
kernel without Landlock means running unsandboxed without knowing.
`lc` must probe, record the effective ABI in every manifest, and
offer a strict mode that refuses to run unenforced.

## 3. Bubblewrap (Linux) — stronger model, gated availability

`bwrap` (containers/bubblewrap, v0.11.2, Apr 2026; Flatpak's sandbox,
also under Steam) builds an **empty mount namespace** — nothing exists
inside except what is explicitly `--ro-bind`/`--bind`-ed — plus
`--unshare-net` for total network deny, `--die-with-parent`,
`--clearenv`. Policy-as-mount-table: undeclared paths yield `ENOENT`
("doesn't exist" — arguably cleaner UX than `EACCES`), read-only binds
yield `EROFS`.

**The availability wall (verified in detail):** unprivileged user
namespaces are AppArmor-gated **by binary path** on Ubuntu 23.10+ /
24.04 LTS (and therefore WSL2-Ubuntu): the apt-installed
`/usr/bin/bwrap` is whitelisted only on 25.04+ (the 24.04 profile was
shipped and then reverted), and a **wheel-shipped bwrap matches no
profile and is blocked** — the exact wall Codex, VS Code, melange, and
Anthropic's sandbox-runtime all document, all resolved by "prefer
system bwrap from PATH, else print the one-sudo-command remediation".
Docker/K8s default seccomp also blocks the required `clone` flags, so
bwrap inside pods is unreliable. Debian/Fedora/Arch/openSUSE work
untouched. Rough estimate: only ~40–60 % of Linux laptops run a
wheel-shipped bwrap with zero admin action today.

**Packaging is otherwise trivial**: static-musl builds are a known
recipe, ~100–300 KiB per arch, LGPL-as-aggregated-subprocess is the
easy license case, and Codex already ships a bundled `bwrap`
system-first. Runtime overhead: milliseconds of setup, native speed
after.

**Verdict: opportunistic upgrade, never the requirement.** Where
usable it adds empty-world isolation, PID isolation, and clean
network unshare on top of Landlock; where gated, Landlock carries the
guarantee alone.

## 4. Seatbelt / `sandbox-exec` (macOS) — deprecated in name only

- Deprecated in the man page since ~2012; **no removal timeline ever
  published**, still functional through macOS 26, and it cannot
  realistically be removed: Seatbelt is the substrate of Apple's own
  App Sandbox and of the system profiles confining Apple's daemons.
- **Production users of exactly our pattern**: Anthropic
  sandbox-runtime (Claude Code's `/sandbox`) — generated SBPL
  profiles via the `sandbox-exec` binary, writes deny-by-default,
  network via localhost-proxy allowlisting; OpenAI Codex —
  parameterized deny-by-default profile, network omitted unless
  opted in; Bazel's `darwin-sandbox`; Chrome; Homebrew; SwiftPM.
- **Inheritance is the key win**: the sandbox applies to the whole
  descendant tree; children cannot shed it. (Corollary: no nesting —
  a recipe that itself calls sandbox-exec fails; Bazel's fallback
  exists for this.)
- Practical profile: `(deny default)` + project dir RW (via
  **realpath'd** `subpath` — `/tmp`→`/private/tmp` symlinks are the
  classic silent miss), venv + interpreter RX, `/System`,`/usr/lib`,
  dyld caches, `/dev/{null,urandom}`, locale/ssl baseline RO,
  `(deny network-outbound)` by default (selective host allowlisting
  is not expressible in SBPL — binary allow/deny per recipe is the
  robust policy, as Codex chose).
- Risk posture: version-gated capability check + graceful fallback to
  trace/warn + documented opt-out; treat the generated profile as
  maintained code with a macOS CI smoke test (per-release path drift
  is the realistic breakage, not removal). Debuggability is the weak
  spot (`(trace)` was removed; `log stream` shows violations).
- Alternatives rejected: Endpoint Security (Apple-granted entitlement
  + bundle — nonstarter for a pip/uv CLI); App Sandbox (wrong model);
  Apple Containerization/`container` 1.0 (2026: per-step Linux VMs,
  macOS 26 + Apple Silicon only — changes the substrate rather than
  confining the native env; watch as a future opt-in hermetic mode).

## 5. Windows

No transparent unprivileged equivalent exists. AppContainer /
restricted tokens are browser-sandbox machinery — powerful, complex,
semi-documented for this use; Job objects limit resources, not file
access; Windows Sandbox is a VM feature. Anthropic's sandbox-runtime
Windows backend is alpha and needs a dedicated local user + WFP
network rules — admin setup, failing the transparency bar. **The
pragmatic path is WSL2** (already the de facto home of scientific
Python on Windows), whose Microsoft kernel ships **Landlock ABI 3** —
Windows users get the Linux enforcement path for free. Native Windows
stays out of scope (as the spec already states) and would run at
hermeticity `open`, recorded honestly.

## 6. Syscall tracing (ReproZip et al.) — demoted to attestation

Researched in depth before the enforcement round; retained findings:

- **ReproZip** (NYU; releases Dec 2025 / Jan 2026, one-maintainer
  bugfix cadence): ptrace tracer writing SQLite
  (`opened_files` with read/write/stat/exec mode bits + canonical
  paths, failed probes excluded; `executed_files` with argv/env;
  full process tree). Usable without its packing feature —
  WholeTale's "Recorded Runs" consumes the trace DB programmatically
  in production.
- **Reliability for accidental leakage: high.** Fork/thread
  auto-attach is atomic with standard ptrace options; static binaries
  and mmap'd libraries are covered. Real blind spots (io_uring opens,
  memfd/dlopen-from-memory, externally inherited fds) essentially
  never occur accidentally in Python/numpy stacks (glibc and CPython
  don't use io_uring).
- **Cost:** ~2–5× on the import storm (≈0.5–2 s), near-zero during
  compute — single-digit % on real recipes. `strace --seccomp-bpf -e
  trace=%file` is faster but has no structured output (you own a text
  parser forever). eBPF is better and root-only — dead on HPC.
- **Role in the design:** with Landlock/Seatbelt providing prevention
  on ~every venue, tracing becomes the *optional evidence layer* —
  proving `traced-clean` where no boundary exists (native Windows,
  exotic kernels), or auditing inside a boundary. Neither Snakemake
  nor Nextflow does anything comparable (declared-IO provenance
  only) — this remains a differentiator either way.
- Landlock ABI 7 (kernel 6.15) audit-of-denials is the eventual
  kernel-native replacement for third-party tracing, but reading the
  audit stream requires privilege — SOC-side, not `lc`-side, for now.

## 7. Proposed design (for the next spec revision)

**Per-output hermeticity ladder**, recorded in every manifest, never
silent:

| Level | Meaning |
|---|---|
| `enforced` (`landlock` \| `bwrap` \| `seatbelt` \| `container`) | recipe ran inside a boundary restricted to the declared set; mechanism + effective Landlock ABI recorded |
| `traced-clean` | no boundary available; trace diff against the declared set came back empty |
| `open` | neither — `lc verify` flags it; `lc materialize --require-sandbox` refuses it |

**Enforcement matrix:**

| Venue | Boundary | Notes |
|---|---|---|
| Linux laptop / WSL2 | **Landlock** (default; pure-Python, ABI 1 floor, probe-and-record) → **bwrap** upgrade when usable (system-first, bundled static fallback; adds empty-world + `--unshare-net`) | the only combination that is transparent on stock Ubuntu 24.04 |
| macOS laptop | **Seatbelt** generated SBPL profile | capability-checked; fallback to trace/warn |
| Hub / GKE | the pod is the boundary; Landlock inside as defense-in-depth where the pod seccomp allows | chart adds `landlock_*` to the seccomp allowlist |
| Perlmutter | **podman-hpc** recipe wrap (site-provided); Landlock candidate pending SLE-15 probe | recipe-level wrap — no dask networking involvement |
| Native Windows | none — `open` (or WSL2 ⇒ Landlock) | out of scope |

**Allowlist policy** (one policy, all mechanisms): project dir RW ·
`.venv` + uv-managed interpreter RX · OS baseline RO (`/usr`, `/lib`,
`/etc/ssl`, locale, dyld caches on mac) · scratch/`/tmp` RW · declared
ASTRA inputs RO · **network deny by default** (a mid-recipe download
is an undeclared input), per-output opt-in via the spec.

**Mandatory design rules** (each traces to a documented failure of a
shipped implementation):
1. **Probe, record, never silently degrade** — startup capability
   probe; effective mechanism + ABI into the manifest;
   `--require-sandbox` strict mode (Codex's silent-best-effort trap).
2. **Crisp denial UX** — on `EACCES`/`EXDEV`/`ENOENT`-in-sandbox, the
   parent re-stats the path (stat is never blocked) and reports
   "blocked by lc sandbox: `<path>` is not part of the declared
   environment", with `--no-sandbox` and `--sandbox-debug` escape
   hatches (landrun's absence of this is its noted flaw).
3. **State the threat model** — enforcement of declared-dependency
   *discipline* against accidental leakage; not a security boundary
   against a malicious recipe (metadata visibility,
   interpreter-reads-script, memfd — all named, all
   adversarial-only).
4. **realpath everything** before emitting policies (macOS
   `/tmp`→`/private/tmp`; symlinked venvs).

## 8. Open verification items

1. **Perlmutter probe**: Landlock present in SLE-15's boot LSM list?
   Effective ABI? Behavior on Lustre/CFS-DVS mounts (no field reports
   exist either way). One salloc session.
2. **Hub pod seccomp**: do the deployment's pods permit `landlock_*`
   syscalls? (Docker default profile historically doesn't.)
3. ABI 8 / TSYNC kernel release number (man-page says 7.0 —
   re-verify on release).
4. Ubuntu 26.04 LTS: does it ship the bwrap AppArmor profile by
   default (25.04+ does; 24.04 does not)?
5. macOS CI smoke test for the generated SBPL profile across OS
   releases.

## Sources (primary)

- Landlock: docs.kernel.org userspace-api/landlock + admin-guide;
  landlock(7) man page (ABI table); LWN 1021648 (audit, 6.15), 1028936
  (O(1) domains); Launchpad #1950381 (Ubuntu enablement);
  microsoft/WSL2-Linux-Kernel (6.6.y landlock.rst);
  openai/codex codex-rs/linux-sandbox (landlock.rs, README);
  landlock-lsm/rust-landlock v0.4.7; Edward-Knight/landlock;
  SebastienWae/py-landlock; Zouuup/landrun; SUSE SLES-16 LSM doc.
- bubblewrap: containers/bubblewrap (releases; 0.11.2 /
  CVE-2026-41163; setuid deprecation); Ubuntu userns spec SE045 +
  Launchpad #2046477/#2072811; anthropic-experimental/sandbox-runtime
  (+ issue #74); codex #15057/#16076; vscode #316046; melange #1508;
  VHSgunzo/bubblewrap-static; moby #42441; bwrap(1).
- macOS: anthropic-experimental/sandbox-runtime (Seatbelt backend);
  Codex sandbox docs (`sandbox-exec`, workspace-write);
  bazel.build/docs/sandboxing; apple/containerization#737 (no removal
  answer); Chromium seatbelt design doc; community SBPL references
  (fG! guide, HackTricks, dnesting 2026); apple/container 1.0.
- Tracing: VIDA-NYU/reprozip (PyPI 1.3.2 Jan 2026; trace schema
  docs); WholeTale Recorded Runs; arXiv 2304.08569 (strace overhead);
  Gregg strace benchmarks; strace --seccomp-bpf; Neil Mitchell file-
  tracing survey; Bomfather arXiv 2503.02097; Sciunit.
