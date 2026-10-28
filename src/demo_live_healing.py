import duckdb
from src.contracts import DataContract, ColumnContract
from src.orchestrator import FSMOrchestrator
from src.synthesizer import LLMSynthesizer


def run_live_test():
    # 1. Simulate an upstream data breakage
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE VIEW raw_batch AS
        SELECT 
            'USR_9821' AS client_identifier,
            12500 AS price_in_cents,
            '2026-03-15' AS order_day
        UNION ALL
        SELECT 
            'USR_4311' AS client_identifier,
            499 AS price_in_cents,
            '2026-03-16' AS order_day
    """)

    # 2. Target contract expected downstream
    contract = DataContract(
        table_name="fact_orders",
        columns=[
            ColumnContract(name="user_id", expected_dtype="VARCHAR"),
            ColumnContract(name="total_usd", expected_dtype="DOUBLE", min_val=1.0, max_val=500.0),
            ColumnContract(name="order_date", expected_dtype="DATE"),
        ],
    )

    print("Target Contract:")
    for col, c in contract.columns.items():
        print(f" - {col}: {c.expected_dtype}")

    # 3. Instantiate synthesizer with primary model & fallback chain
    synthesizer = LLMSynthesizer(primary_model="gemini/gemini-3.8-flash")
    orchestrator = FSMOrchestrator(
        conn=conn,
        contract=contract,
        synthesizer=synthesizer,
        checkpoint_db_path="live_trace.db"
    )

    print("\nRunning FSM Orchestrator with real Gemini API...")
    final_state = orchestrator.run(run_id="live_demo_01")
    print(f"\nFinal State: {final_state.value}")

    # 4. Print healed data
    if final_state.value == "COMMITTED":
        print("\nVerified Transformed Output:")
        res = conn.execute("SELECT * FROM staging_candidate_view").fetchdf()
        print(res)


if __name__ == "__main__":
    run_live_test()
