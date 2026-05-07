# Hooks Architecture

The telemetry hooks are shipped as part of the plugin and copied into
`.claude/hooks/` by `lc init`. They are dormant unless
`TRACE_TO_LANGFUSE=true` and the Langfuse credentials are present in
the environment — see [Telemetry overview](index.md). Today's
`lc init` does **not** seed `.claude/settings.local.json` automatically,
so a fresh project ships with the hooks installed but disabled until
you fill in the env vars.

The system is five Python scripts in `claude/lightcone/hooks/`:

```
hooks/
├── langfuse_session_init_hook.py   # PreToolUse: create trace ID
├── langfuse_hook.py                # Stop / SessionEnd: emit full session
├── langfuse_git_commit_hook.py     # PostToolUse(Bash): attach git metadata
├── langfuse_prepare_commit_msg.py  # git prepare-commit-msg hook
└── langfuse_utils.py               # shared utilities
```

These five files are copied verbatim from
[langfuse-cli](https://github.com/langfuse/langfuse-cli) (MIT). See the
NOTICE file at the repo root.

---

## `langfuse_session_init_hook.py` (PreToolUse)

Fired before the very first tool use in a session. Creates a deterministic Langfuse trace ID from the session ID using SHA-256:

```python
trace_id = sha256(session_id.encode()).hexdigest()[:32]
```

The trace ID is saved to `.langfuse/last_trace.json` so the main hook can reference it. This ensures the pre-session "empty" trace and the post-session full trace share an ID and appear as one entry in Langfuse.

---

## `langfuse_hook.py` (Stop / SessionEnd)

The main emission hook. Reads the Claude Code transcript file incrementally (using a byte-offset cursor stored in `.langfuse/state.json`) and emits new turns to Langfuse.

### Processing pipeline

```
transcript.jsonl (JSONL)
    ↓
read_new_jsonl()        # incremental read from last byte offset
    ↓
build_turns()           # group messages into (user_msg, assistant_msgs, tool_results)
    ↓
emit_turn()             # create Langfuse trace + generation span per turn
```

### Turn assembly (`build_turns`)

The Claude Code transcript is a flat JSONL stream. `build_turns()` groups it into turns:

- A **turn** starts with a user message (not a tool_result).
- All subsequent assistant messages (and their interleaved tool results) belong to that turn.
- The next non-tool-result user message starts a new turn.

Multiple assistant messages with the same `message_id` are deduplicated — only the latest version is kept (handles streaming updates).

### Langfuse data model

Each turn emits:

```
trace (session_id)
  └── generation span
        ├── input: ChatML messages (user + assistant)
        ├── output: ChatML assistant message with tool_calls
        ├── model: claude-* model name
        ├── metadata: session_id, turn_number, transcript_path, git_metadata, ...
        └── tool_calls[]: {name, arguments, output, is_error}
```

---

## `langfuse_git_commit_hook.py` (PostToolUse Bash)

Fired after every Bash tool use. Checks if the bash command was a git commit:

1. Looks for patterns like `git commit`, `git commit -m "..."`, etc.
2. If a commit happened, reads the git log to get the commit SHA and author.
3. Builds a GitHub URL from the remote origin.
4. Saves this metadata to `.langfuse/git_trace.json`.
5. The main hook picks up this metadata when emitting the next turn.

---

## `langfuse_utils.py`

Shared utilities used by all hooks:

- **Logging**: `debug()`, `info()`, `warn()`, `error()` → `.langfuse/hook.log`
- **Environment**: `tracing_enabled()`, `get_langfuse_credentials()`
- **Hook payload parsing**: `read_hook_payload()`, `extract_session_id()`, `extract_transcript_path()`
- **Git helpers**: `get_git_metadata()`, `build_github_commit_url()`, `resolve_repo_root()`
- **User identity**: `get_claude_user_email()` (reads `~/.claude.json`)
- **File I/O**: `atomic_write_json()`, `save_last_trace()`, `read_last_trace()`
- **Trace manifest**: `write_trace_manifest()` — written to `.langfuse/traces.json`

---

## State files

All state is stored in the project's `.langfuse/` directory (gitignored):

| File | Contents |
|------|----------|
| `last_trace.json` | Current session's trace ID (from init hook) |
| `state.json` | Per-session byte offsets and turn counts for incremental reads |
| `git_trace.json` | Latest git commit metadata to attach to the next span |
| `traces.json` | Manifest of all traces emitted in this project |
| `hook.log` | Debug log for hook execution |
