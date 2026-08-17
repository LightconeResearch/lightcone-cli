# Install

lightcone-cli has exactly one prerequisite: [uv](https://docs.astral.sh/uv/).
uv is the environment substrate — it manages the project's Python
interpreter, its locked dependencies, and lightcone-cli itself.

## 1. uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

(Or any of the [other installation methods](https://docs.astral.sh/uv/getting-started/installation/).)

## 2. lightcone-cli

```bash
uv tool install lightcone-cli
```

This puts the `lc` launcher on your PATH. The launcher is a thin shim:
each project locks its *own* copy of the engine (`lightcone-cli` is an
ordinary dependency in the project's `pyproject.toml`), and `lc`
delegates into it — so the engine version is pinned per experiment, in
the lock, like every other dependency.

Verify:

```bash
lc --version
```

## 3. (Only if you need it) podman

Most projects never need a container. When a project declares system
dependencies uv cannot lock — R, TeX, compilers, system libraries — it
flips into **containerized mode** and needs rootless
[podman](https://podman.io/docs/installation):

```bash
# Arch
sudo pacman -S podman
# Debian/Ubuntu
sudo apt install podman
# macOS (one-time Linux VM, ~minutes)
brew install podman
podman machine init && podman machine start
```

You'll be told exactly when this becomes necessary — the sandbox denial
message names the step. Until then, there is nothing to install.

## Notes

- **Python**: you do not need a system Python. uv installs the exact
  interpreter each project pins in `.python-version`.
- **No conda, no docker, no activation** — `lc <verb>` is the whole
  interface, from any directory inside a project.
