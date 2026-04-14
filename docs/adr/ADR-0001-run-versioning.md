# ADR-0001: Run-scoped artifacts

Status: Accepted

Decision:
- Use run_id per execution.
- Persist all derived outputs under artifacts/run_{run_id}/.

Consequences:
- Reproducible outputs.
- Easy diff between runs.
- No overwrite of historical results.
