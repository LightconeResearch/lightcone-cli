# Disabling Telemetry

Telemetry is opt-out. To disable it for a project, edit `.claude/settings.local.json`:

```json
{
  "env": {
    "TRACE_TO_LANGFUSE": "false"
  }
}
```

This environment variable is checked by `tracing_enabled()` in `langfuse_utils.py`. When set to `false`, all hooks exit immediately without connecting to Langfuse.

## Disabling globally

To disable telemetry for all new projects, unset the key or set it to `false` before running `lc init`, or patch `_create_claude_settings()` in `cli.py` to set `TRACE_TO_LANGFUSE=false` by default.

## What is not collected

- File contents (only tool call metadata and truncated outputs).
- Actual script outputs beyond the last 2000 characters.
- Passwords, tokens, or credential values.
- Any data outside the project directory.

## Transparency

The full telemetry implementation is in `claude/lightcone/hooks/`. All hooks are plain Python scripts installed in each project's `.claude/hooks/` directory — they can be inspected, modified, or deleted per-project.
