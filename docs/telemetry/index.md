# Telemetry (Langfuse)

The plugin ships Langfuse hooks that trace Claude Code sessions to
[Langfuse](https://langfuse.com/). The hooks are present in every
project that ran `lc init` (under `.claude/hooks/`), but **they don't
do anything until you give them credentials**: today's `lc init` does
not wire `.claude/settings.local.json` automatically.

## What gets traced (when enabled)

Each Claude Code session generates:

- One **trace per turn** (user message + all assistant responses in
  that turn).
- **Tool calls** with input arguments and output results (truncated to
  2000 chars).
- **Git metadata** attached to spans after commits: commit SHA, GitHub
  URL, branch.
- **Session metadata**: Claude Code version, project name, transcript
  path.
- **User identity**: Claude user email (from `~/.claude.json`).

## Hooks at a glance

| Hook event | Script | When |
|------------|--------|------|
| `PreToolUse` | `langfuse_session_init_hook.py` | Before the first tool call in a session — creates the trace id. |
| `PostToolUse` (Bash) | `langfuse_git_commit_hook.py` | After any bash command — attaches commit metadata if one happened. |
| `Stop` / `SessionEnd` | `langfuse_hook.py` | When Claude stops responding or the session ends — flushes the full trace. |

`langfuse_utils.py` carries the shared state (`STATE_DIR`, `LOCK_FILE`,
`tracing_enabled()`, etc.) and is imported by the other hooks.

See [Hooks Architecture](hooks.md) for the wiring detail.

## Configuration

Telemetry hooks read credentials from environment variables:

| Var | Purpose |
|-----|---------|
| `TRACE_TO_LANGFUSE` | Enable / disable. Hooks no-op when this is unset or `false`. |
| `LANGFUSE_PUBLIC_KEY` | Langfuse project public key. |
| `LANGFUSE_SECRET_KEY` | Langfuse project secret key (or `relay` for the Cloudflare relay). |
| `LANGFUSE_HOST` | Langfuse endpoint. For Lightcone's relay: `https://telemetry.lightconeresearch.workers.dev`. |

Set these in `.claude/settings.local.json` (per project, gitignored):

```json
{
  "env": {
    "TRACE_TO_LANGFUSE": "true",
    "LANGFUSE_PUBLIC_KEY": "...",
    "LANGFUSE_SECRET_KEY": "relay",
    "LANGFUSE_HOST": "https://telemetry.lightconeresearch.workers.dev"
  }
}
```

`settings.local.json` is in the default `.gitignore` written by
`lc init`.

## Disabling

See [Opt Out](opt-out.md).
