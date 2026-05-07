# Disabling Telemetry

Telemetry hooks are present but **disabled by default in today's
`lc init`** — the bundled plugin no longer auto-writes
`.claude/settings.local.json`. The hooks no-op until they see
`TRACE_TO_LANGFUSE=true` in the environment.

If you previously configured telemetry and want to turn it off, edit
`.claude/settings.local.json`:

```json
{
  "env": {
    "TRACE_TO_LANGFUSE": "false"
  }
}
```

`tracing_enabled()` in `langfuse_utils.py` checks that variable; when
it's unset or `false`, every hook exits immediately without contacting
Langfuse.

## Removing the hooks entirely

If you want zero telemetry code in your project at all:

```bash
rm -rf .claude/hooks                                   # the Python hooks
# Remove any hook entries from .claude/settings.json (PreToolUse / PostToolUse / Stop / SessionEnd)
```

The plugin install in `lc init` will recreate them on the next init,
but `lc init` won't run inside a project that already has `astra.yaml`,
so existing projects are safe.

## What is *not* collected (even when enabled)

- File contents (only tool-call metadata and truncated outputs).
- Actual script outputs beyond the last 2000 characters.
- Passwords, tokens, or credential values.
- Any data outside the project directory.

## Transparency

The full telemetry implementation is in `claude/lightcone/hooks/`. All
hooks are plain Python scripts installed in each project's
`.claude/hooks/` directory — they can be inspected, modified, or
deleted per-project. They are vendored from the upstream
[langfuse-cli](https://github.com/langfuse/langfuse-cli) under MIT.
