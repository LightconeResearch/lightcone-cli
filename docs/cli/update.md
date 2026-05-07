# lc update (removed)

This command no longer exists. Upgrade lightcone-cli with normal Python
tooling:

```bash
pip install --upgrade lightcone-cli       # or: uv pip install -U lightcone-cli
```

To pull updated plugin files into an existing project (`lc init` refuses to
run if `astra.yaml` already exists, so we copy by hand):

```bash
python - <<'PY'
import shutil
from pathlib import Path
from lightcone.cli.plugin import get_plugin_source_dir

src = get_plugin_source_dir()
dst = Path(".claude")
for sub in ("skills", "agents", "hooks", "scripts", "guides", "templates"):
    s, d = src / sub, dst / sub
    if d.exists():
        shutil.rmtree(d)
    if s.exists():
        shutil.copytree(s, d)
print("synced from", src)
PY
```

A short `bin/lc-sync` helper or a `lc init --sync` flag would make this
nicer — see the issue tracker if interested.
