# Session Lifecycle

How telemetry events flow from a Claude Code session to Langfuse.
Telemetry is dormant unless the credentials are wired (see
[Telemetry overview](index.md)).

## Timeline

```
Claude Code session opens
        │
        ▼
[SessionStart]
  activate-venv.sh       → activates .venv/
  session-start.sh       → shows ASTRA project summary; detects
                           interrupted lc-build loops

        │
        ▼  (first tool use)
[PreToolUse]
  langfuse_session_init_hook.py
        → trace_id = sha256(session_id)[:32]
        → save to .langfuse/last_trace.json

        │
        ▼  (for each tool use)
[PostToolUse]
  validate-on-save.sh           → runs astra validate on Write/Edit of astra.yaml or universes/*
  check-lc-run.sh                → warns if python <recipe-script> is run directly
  langfuse_git_commit_hook.py    → on bash git commits:
                                    extract commit SHA + GitHub URL,
                                    write .langfuse/git_trace.json

        │
        ▼  (session ends or Claude stops)
[Stop / SessionEnd]
  langfuse_hook.py
        → read transcript.jsonl from last byte offset
        → build_turns(): group messages into turns
        → for each new turn:
            emit_turn() → Langfuse trace + generation span
        → update .langfuse/state.json
```

## Incremental processing

`langfuse_hook.py` runs at every `Stop` event (not only `SessionEnd`).
It reads only new content since the last call using a stored byte
offset. Practical consequences:

- Turns are emitted incrementally throughout the session.
- A crash or timeout does not lose already-emitted turns.
- Short sessions with one turn emit once; long sessions with many turns
  emit progressively.

## Trace linking

The session-init hook generates the trace id deterministically from the
Claude Code session id, and writes it to `.langfuse/last_trace.json`.
The main hook reuses that id for the first turn so the pre-session
"empty" trace entry and the first turn's trace share an id — Langfuse
displays them as one entry.

## Status of the project-side hooks

The two non-telemetry session hooks (`session-start.sh` and
`check-lc-run.sh`) currently have outdated branches that look for
status terms (`pending`, `materialized`, `no_recipe`, …) that today's
`lc status` no longer emits. The hooks degrade silently in that case —
no false positives, just dimmer crash recovery hints. See the
[maintainer summary](../index.md) for the fix-list.
