from typing import List
import pytest
import duckdb
from src.contracts import DataContract, ColumnContract, DriftDiagnostic
from src.guardrails import ASTGuardrail, EphemeralSandbox
from src.orchestrator import FSMOrchestrator, PatchProposal, AgentState


ALLOWED_COLS = {"client_id", "price_cents", "tx_date"}

SAMPLE_CONTRACT = DataContract(
    table_name="fact_orders",
    columns=[
        ColumnContract(name="user_id", expected_dtype="VARCHAR"),
        ColumnContract(name="total_usd", expected_dtype="DOUBLE", min_val=1.0, max_val=500.0),
        ColumnContract(name="order_date", expected_dtype="DATE"),
    ],
)


# =====================================================================
# 1. STATIC AST GUARDRAIL TESTS
# =====================================================================

def test_ast_accepts_valid_select_projection():
    sql = """
    SELECT 
        COALESCE(CAST(client_id AS VARCHAR), 'UNKNOWN') AS user_id,
        CAST(price_cents / 100.0 AS DOUBLE) AS total_usd,
        CAST(tx_date AS DATE) AS order_date
    FROM raw_batch
    """
    ok, err = ASTGuardrail.verify_sql_ast(sql, ALLOWED_COLS)
    assert ok is True
    assert err is None


@pytest.mark.parametrize(
    "hostile_sql",
    [
        "DROP TABLE raw_batch",
        "DELETE FROM raw_batch WHERE 1=1",
        "UPDATE raw_batch SET price_cents = 0",
        "INSERT INTO raw_batch VALUES ('a', 1, '2026-01-01')",
        "ALTER TABLE raw_batch DROP COLUMN client_id",
        "TRUNCATE TABLE raw_batch",
    ],
)
def test_ast_blocks_destructive_ddl_and_dml(hostile_sql: str):
    ok, err = ASTGuardrail.verify_sql_ast(hostile_sql, ALLOWED_COLS)
    assert ok is False
    assert err is not None
    assert "Security Violation" in err


def test_ast_blocks_stacked_sql_injection():
    stacked_sql = "SELECT client_id AS user_id FROM raw_batch; DROP TABLE raw_batch;"
    ok, err = ASTGuardrail.verify_sql_ast(stacked_sql, ALLOWED_COLS)
    assert ok is False
    assert err is not None
    assert "Expected exactly 1 SQL statement" in err


def test_ast_blocks_hallucinated_source_columns():
    # LLM hallucinates `customer_uuid` and `discount_code` which do not exist in raw_batch
    hallucinated_sql = """
    SELECT 
        customer_uuid AS user_id,
        CAST(price_cents / 100.0 AS DOUBLE) AS total_usd,
        discount_code
    FROM raw_batch
    """
    ok, err = ASTGuardrail.verify_sql_ast(hallucinated_sql, ALLOWED_COLS)
    assert ok is False
    assert err is not None
    assert "Hallucinated source columns" in err
    assert "customer_uuid" in err
    assert "discount_code" in err


def test_ast_catches_malformed_syntax():
    broken_sql = "SELECT client_id AS user_id, FROM FROM WHERE raw_batch ("
    ok, err = ASTGuardrail.verify_sql_ast(broken_sql, ALLOWED_COLS)
    assert ok is False
    assert err is not None
    assert "SQL Syntax Error" in err


# =====================================================================
# 2. EPHEMERAL DUCKDB SANDBOX TESTS
# =====================================================================

def test_sandbox_rejects_unfixed_unit_drift():
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE VIEW raw_batch AS
        SELECT 'USR_1' AS client_id, 15000 AS price_cents, DATE '2026-03-01' AS tx_date
    """)

    # SQL maps names and types properly, but forgets to divide cents by 100 (violates max_val=500.0)
    bad_unit_sql = """
        SELECT 
            client_id AS user_id,
            CAST(price_cents AS DOUBLE) AS total_usd,
            tx_date AS order_date
        FROM raw_batch
    """
    ok, err = EphemeralSandbox.verify_in_sandbox(conn, bad_unit_sql, SAMPLE_CONTRACT)
    assert ok is False
    assert err is not None
    assert "DISTRIBUTION_SHIFT" in err


def test_sandbox_catches_runtime_conversion_error():
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE VIEW raw_batch AS
        SELECT 'USR_1' AS client_id, 100 AS price_cents, 'NOT_A_VALID_DATE' AS tx_date
    """)

    # Valid AST, but fails at DuckDB execution runtime when casting 'NOT_A_VALID_DATE' to DATE
    crashing_sql = """
        SELECT 
            client_id AS user_id,
            CAST(price_cents AS DOUBLE) AS total_usd,
            CAST(tx_date AS DATE) AS order_date
        FROM raw_batch
    """
    ok, err = EphemeralSandbox.verify_in_sandbox(conn, crashing_sql, SAMPLE_CONTRACT)
    assert ok is False
    assert err is not None
    assert "DuckDB Runtime Execution Error" in err


# =====================================================================
# 3. FSM SELF-CORRECTION & ROLLBACK INTEGRATION TESTS
# =====================================================================

class SelfCorrectingMockSynthesizer:
    """Fails on Attempt 1 with a hallucinated column, then uses error_history on Attempt 2 to heal."""

    def __init__(self):
        self.call_count = 0

    def generate_patch(self, diagnostic: DriftDiagnostic, error_history: List[str]) -> PatchProposal:
        self.call_count += 1
        if self.call_count == 1:
            return PatchProposal(
                reasoning="Attempt 1: accidentally hallucinated user_uuid",
                sql_patch="SELECT user_uuid AS user_id, price_cents / 100.0 AS total_usd, CAST(tx_date AS DATE) AS order_date FROM raw_batch",
            )
        # Verify the orchestrator fed the AST error message back into error_history
        assert any("Hallucinated source columns" in e for e in error_history)
        return PatchProposal(
            reasoning="Attempt 2: fixed column name to client_id after AST feedback",
            sql_patch="SELECT client_id AS user_id, CAST(price_cents / 100.0 AS DOUBLE) AS total_usd, CAST(tx_date AS DATE) AS order_date FROM raw_batch",
        )


def test_fsm_recovers_on_second_attempt_via_error_feedback(tmp_path):
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE VIEW raw_batch AS
        SELECT 'USR_99' AS client_id, 4500 AS price_cents, '2026-03-10' AS tx_date
    """)

    synth = SelfCorrectingMockSynthesizer()
    db_file = str(tmp_path / "test_trace.db")
    orchestrator = FSMOrchestrator(
        conn=conn,
        contract=SAMPLE_CONTRACT,
        synthesizer=synth,
        max_retries=3,
        checkpoint_db_path=db_file,
    )

    final_state = orchestrator.run(run_id="test_self_correction")
    assert final_state == AgentState.COMMITTED
    assert synth.call_count == 2


class AlwaysHostileMockSynthesizer:
    """Always proposes a destructive query to verify FSM rolls back safely after exhausting retries."""

    def generate_patch(self, diagnostic: DriftDiagnostic, error_history: List[str]) -> PatchProposal:
        return PatchProposal(
            reasoning="Attempting hostile table drop",
            sql_patch="DROP TABLE raw_batch",
        )


def test_fsm_rolls_back_deterministically_when_retries_exhausted(tmp_path):
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE VIEW raw_batch AS
        SELECT 'USR_99' AS client_id, 4500 AS price_cents, '2026-03-10' AS tx_date
    """)

    db_file = str(tmp_path / "test_rollback.db")
    orchestrator = FSMOrchestrator(
        conn=conn,
        contract=SAMPLE_CONTRACT,
        synthesizer=AlwaysHostileMockSynthesizer(),
        max_retries=3,
        checkpoint_db_path=db_file,
    )

    final_state = orchestrator.run(run_id="test_hostile_rollback")
    assert final_state != AgentState.COMMITTED
    # Ensure raw_batch was never dropped!
    count = conn.execute("SELECT COUNT(*) FROM raw_batch").fetchone()[0]
    assert count == 1
