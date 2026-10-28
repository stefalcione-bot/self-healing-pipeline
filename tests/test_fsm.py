import duckdb
from src.contracts import DataContract, ColumnContract, DriftDiagnostic
from src.orchestrator import FSMOrchestrator, PatchProposal

class MockSynthesizer:
    def generate_patch(self, diagnostic: DriftDiagnostic, error_history: list[str]) -> PatchProposal:
        if not error_history:
            return PatchProposal(
                reasoning="Hallucinate invalid column",
                sql_patch="SELECT user_id, CAST(price_usd AS DOUBLE) AS amount_usd FROM raw_batch"
            )
        return PatchProposal(
            reasoning="Convert amount_cents to dollars and customer_id to user_id",
            sql_patch="SELECT CAST(customer_id AS BIGINT) AS user_id, (amount_cents / 100.0) AS amount_usd FROM raw_batch"
        )

def test_pipeline_heals_end_to_end():
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE VIEW raw_batch AS
        SELECT '101' AS customer_id, 4500 AS amount_cents
        UNION ALL
        SELECT '102' AS customer_id, 8900 AS amount_cents
    """)
    contract = DataContract(
        table_name="orders_clean",
        columns=[
            ColumnContract(name="user_id", expected_dtype="BIGINT"),
            ColumnContract(name="amount_usd", expected_dtype="DOUBLE", min_val=10.0, max_val=200.0),
        ],
    )
    orchestrator = FSMOrchestrator(conn, contract, MockSynthesizer(), checkpoint_db_path=":memory:")
    final_state = orchestrator.run(run_id="run_001")
    assert final_state.value == "COMMITTED"
    print("Pipeline healed successfully!")

if __name__ == "__main__":
    test_pipeline_heals_end_to_end()