# Session Lifecycle

How telemetry events flow from a Claude Code session to Langfuse.

## Timeline

```
Claude Code session opens
        │
        ▼
[SessionStart]
  activate-venv.sh       → activates .venv/
  session-start.sh       → shows ASTRA project summary

        │
        ▼ (first tool use)
[PreToolUse]
  langfuse_session_init_hook.py
        → generate deterministic trace_id = sha256(session_id)[:32]
        → save to .langfuse/last_trace.json

        │
        ▼ (for each tool use)
[PostToolUse]
  validate-on-save.sh    → runs astra validate on Write/Edit
  check-prism-run.sh     → warns if python run directly
  langfuse_git_commit_hook.py
        → if bash command was git commit:
            → extract commit SHA + GitHub URL
            → save to .langfuse/git_trace.json

        │
        ▼ (session ends or Claude stops)
[Stop / SessionEnd]
  langfuse_hook.py
        → read transcript.jsonl from last byte offset
        → build_turns(): group messages into turns
        → for each new turn:
            emit_turn() → Langfuse trace + generation span
        → update .langfuse/state.json with new byte offset
```

## Incremental processing

The main hook is called at every `Stop` event (not just `SessionEnd`). It reads only new content since the last call using a stored byte offset. This means:

- Turns are emitted incrementally throughout the session.
- A crash or timeout does not lose already-emitted turns.
- Short sessions with one turn emit once; long sessions with many turns emit progressively.

## Trace linking

The session init hook generates a trace ID before any tool runs. This trace ID is reused by the main hook for the first turn (`turn_num == 1`). The result is that the pre-session trace entry and the first turn's trace share an ID in Langfuse, making it easy to identify when the agent first became active.
