# Self-Healing Data Pipeline & Schema Drift Agent

A deterministic, FSM-driven orchestration engine for detecting upstream data contract drift, generating type-safe SQL migration patches, and verifying them in an isolated DuckDB sandbox with SQLGlot AST guardrails.

## Key Engineering Abstractions
- **Finite State Machine:** Strict state transition budgets (`DETECT -> SYNTHESIZE -> AST_CHECK -> SANDBOX -> COMMIT`).
- **AST Guardrails:** Static SQL parsing via `sqlglot` to reject destructive operations and hallucinated column projections.
- **Transactional Sandbox:** Ephemeral in-memory DuckDB validation against structural and distribution contracts.
- **State Checkpointing:** Append-only trace logging to SQLite for deterministic replays.