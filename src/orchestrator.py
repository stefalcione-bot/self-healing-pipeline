from enum import Enum
from typing import List, Optional, Protocol
from datetime import datetime, timezone
import json
import sqlite3
from pydantic import BaseModel
import duckdb

from src.contracts import DataContract, DriftDiagnostic
from src.guardrails import ASTGuardrail, EphemeralSandbox


class AgentState(str, Enum):
    INGEST = "INGEST"
    DETECT_DRIFT = "DETECT_DRIFT"
    SYNTHESIZE_PATCH = "SYNTHESIZE_PATCH"
    AST_VERIFY = "AST_VERIFY"
    SANDBOX_VERIFY = "SANDBOX_VERIFY"
    COMMITTED = "COMMITTED"
    ESCALATED = "ESCALATED"  # Terminal failure state when retries are exhausted


class PatchProposal(BaseModel):
    reasoning: str
    sql_patch: str  # SELECT query transforming `raw_batch` into the target contract schema


class PatchSynthesizerProtocol(Protocol):
    """Clean interface so you can swap Gemini / Groq / MockLLM in unit tests."""
    def generate_patch(
        self, diagnostic: DriftDiagnostic, error_history: List[str]
    ) -> PatchProposal: ...


class FSMOrchestrator:
    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        contract: DataContract,
        synthesizer: PatchSynthesizerProtocol,
        checkpoint_db_path: str = "state_checkpoints.db",
        max_retries: int = 3,
    ):
        self.conn = conn
        self.contract = contract
        self.synthesizer = synthesizer
        self.max_retries = max_retries
        self.sqlite_conn = sqlite3.connect(checkpoint_db_path)
        self._init_checkpoint_table()

    def _init_checkpoint_table(self) -> None:
        self.sqlite_conn.execute("""
            CREATE TABLE IF NOT EXISTS fsm_trace (
                run_id TEXT,
                step_idx INTEGER,
                state TEXT,
                attempt INTEGER,
                payload_json TEXT,
                timestamp TEXT
            )
        """)
        self.sqlite_conn.commit()

    def _log_checkpoint(self, run_id: str, step_idx: int, state: AgentState, attempt: int, payload: dict) -> None:
        self.sqlite_conn.execute(
            "INSERT INTO fsm_trace VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, step_idx, state.value, attempt, json.dumps(payload), datetime.now(timezone.utc).isoformat())
        )
        self.sqlite_conn.commit()

    def run(self, run_id: str, raw_view_name: str = "raw_batch") -> AgentState:
        state = AgentState.DETECT_DRIFT
        attempt = 0
        step_idx = 0
        error_history: List[str] = []
        diagnostic: Optional[DriftDiagnostic] = None
        current_proposal: Optional[PatchProposal] = None

        while state not in (AgentState.COMMITTED, AgentState.ESCALATED):
            step_idx += 1

            if state == AgentState.DETECT_DRIFT:
                diagnostic = self.contract.evaluate(self.conn, raw_view_name)
                self._log_checkpoint(run_id, step_idx, state, attempt, {
                    "drift_detected": diagnostic is not None,
                    "violations": [v.model_dump() for v in diagnostic.violations] if diagnostic else []
                })
                if diagnostic is None:
                    state = AgentState.COMMITTED
                else:
                    state = AgentState.SYNTHESIZE_PATCH

            elif state == AgentState.SYNTHESIZE_PATCH:
                if attempt >= self.max_retries:
                    self._log_checkpoint(run_id, step_idx, AgentState.ESCALATED, attempt, {"errors": error_history})
                    return AgentState.ESCALATED

                attempt += 1
                assert diagnostic is not None
                current_proposal = self.synthesizer.generate_patch(diagnostic, error_history)
                self._log_checkpoint(run_id, step_idx, state, attempt, current_proposal.model_dump())
                state = AgentState.AST_VERIFY

            elif state == AgentState.AST_VERIFY:
                assert current_proposal is not None and diagnostic is not None
                allowed_cols = set(diagnostic.observed_schema.keys())
                is_valid_ast, ast_err = ASTGuardrail.verify_sql_ast(
                    current_proposal.sql_patch, allowed_cols, source_table=raw_view_name
                )
                self._log_checkpoint(run_id, step_idx, state, attempt, {"valid": is_valid_ast, "error": ast_err})

                if not is_valid_ast:
                    error_history.append(f"Attempt {attempt} failed AST check: {ast_err}")
                    state = AgentState.SYNTHESIZE_PATCH
                else:
                    state = AgentState.SANDBOX_VERIFY

            elif state == AgentState.SANDBOX_VERIFY:
                assert current_proposal is not None
                is_valid_sandbox, sandbox_err = EphemeralSandbox.verify_in_sandbox(
                    self.conn, current_proposal.sql_patch, self.contract
                )
                self._log_checkpoint(run_id, step_idx, state, attempt, {"valid": is_valid_sandbox, "error": sandbox_err})

                if not is_valid_sandbox:
                    error_history.append(f"Attempt {attempt} failed Sandbox verification: {sandbox_err}")
                    state = AgentState.SYNTHESIZE_PATCH
                else:
                    state = AgentState.COMMITTED

        self._log_checkpoint(run_id, step_idx + 1, state, attempt, {"final_sql": current_proposal.sql_patch if current_proposal else "NONE"})
        return state