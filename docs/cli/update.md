# lc update (removed)

This command no longer exists. Upgrade lightcone-cli with normal Python
tooling:

```bash
pip install --upgrade lightcone-cli       # or: uv pip install -U lightcone-cli
```

The lightcone plugin (skills, agents, hooks) lives user-scoped under
`~/.claude/plugins/` — installed once by `lc init` via `claude plugin
install lightcone@lightcone-cli`, not duplicated per project. To pull in
plugin changes after a `pip install --upgrade`:

```bash
# Re-register the marketplace at its new wheel-installed location, then
# re-install the plugin so Claude Code reads from the updated source.
python -c "from lightcone.cli.plugin import get_marketplace_root; print(get_marketplace_root())" \
    | xargs claude plugin marketplace add
claude plugin install lightcone@lightcone-cli
```

Both commands are idempotent. `claude plugin marketplace remove
lightcone-cli` then `add` again is the heaviest hammer, and rarely
necessary.
