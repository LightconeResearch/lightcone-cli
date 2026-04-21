# Adding an Execution Backend

Execution backends are implemented in `src/lightcone/dagster/runner.py` as methods on `ASTRAContainerRunner`.

## Steps

### 1. Add a `_run_{backend}()` method

```python
def _run_mybackend(
    self,
    command: str,
    output_id: str,
    universe_id: str,
    # ... backend-specific args
) -> ExecutionResult:
    """Execute a recipe using mybackend.

    Args:
        command: Full recipe command with CLI args already appended.
        output_id: Output identifier (used for error messages).
        universe_id: Universe being materialised.

    Returns:
        ExecutionResult with exit_code, output_path, and metadata.
    """
    # ... implementation
    return ExecutionResult(
        exit_code=returncode,
        output_path=self.project_root / "results" / universe_id,
        metadata={"backend": "mybackend", "stdout": ..., "stderr": ...},
    )
```

### 2. Add dispatch in `execute()`

```python
def execute(self, ...) -> ExecutionResult:
    ...
    if self.backend == "mybackend":
        return self._run_mybackend(...)
    ...
```

### 3. Add target config support

If the backend needs configuration, add the relevant fields to the target YAML format and document them in `docs/hpc/targets.md`.

### 4. Register in `build_definitions()`

If your backend requires special setup in `build_definitions()` (e.g. detecting a runtime), add it alongside the existing `"docker"` / `"slurm"` cases.

### 5. Write tests

Add tests in `tests/dagster/test_runner.py` following the existing pattern:

```python
def test_mybackend_basic(tmp_path):
    runner = ASTRAContainerRunner(
        project_root=str(tmp_path),
        backend="mybackend",
    )
    (tmp_path / "universes").mkdir()
    result = runner.execute(
        command="echo hello",
        output_id="test_output",
        universe_id="baseline",
    )
    assert result.exit_code == 0
```

## ExecutionResult contract

All `_run_*` methods must return an `ExecutionResult` with:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `exit_code` | `int` | Yes | 0 for success, non-zero for failure |
| `output_path` | `Path` | Yes | `project_root / "results" / universe_id` |
| `metadata["backend"]` | `str` | Yes | Backend name for Dagster materialisation metadata |
| `metadata["stdout"]` | `str` | No | Last 2000 chars of stdout |
| `metadata["stderr"]` | `str` | No | Last 2000 chars of stderr |
