# Telemetry (Langfuse)

lightcone-cli traces Claude Code sessions to [Langfuse](https://langfuse.com/) for observability, debugging, and research workflow analysis. Telemetry is **opt-out** — it is enabled by default in all projects created with `lc init`.

## What is traced

Each Claude Code session in a lightcone-cli project generates:

- One **trace per turn** (user message + all assistant responses in that turn).
- **Tool calls** with input arguments and output results (truncated to 2000 chars).
- **Git metadata** attached to spans after commits: commit SHA, GitHub URL, branch.
- **Session metadata**: Claude Code version, project name, transcript path.
- **User identity**: Claude user email (from `~/.claude.json`).

## Architecture overview

Telemetry is implemented as a set of Claude Code hooks:

| Hook type | Script | When fired |
|-----------|--------|------------|
| `PreToolUse` | `langfuse_session_init_hook.py` | Before the very first tool in a session |
| `PostToolUse` (Bash) | `langfuse_git_commit_hook.py` | After any bash command (checks for git commits) |
| `Stop` + `SessionEnd` | `langfuse_hook.py` | At session end or when Claude stops responding |

See [Hooks Architecture](hooks.md) for details.

## Configuration

Telemetry credentials are stored in `.claude/settings.local.json`:

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

The `LANGFUSE_HOST` points to Lightcone's Cloudflare Worker relay, which forwards traces to the managed Langfuse instance.

## Disabling telemetry

See [Opt Out](opt-out.md).
