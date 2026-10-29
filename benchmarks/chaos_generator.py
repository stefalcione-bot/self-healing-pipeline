from dataclasses import dataclass
from typing import List
import duckdb
from src.contracts import DataContract, ColumnContract


@dataclass
class ChaosScenario:
    scenario_id: str
    category: str          # "RENAME", "TYPE_CAST", "UNIT_SHIFT", "DATA_CLEANING", "COMPOUND"
    description: str
    setup_sql: str         # Creates `raw_batch` in DuckDB with the injected drift
    contract: DataContract # Target contract that the agent must satisfy


ORDERS_CONTRACT = DataContract(
    table_name="fact_orders",
    columns=[
        ColumnContract(name="order_id", expected_dtype="BIGINT"),
        ColumnContract(name="user_id", expected_dtype="VARCHAR"),
        ColumnContract(name="amount_usd", expected_dtype="DOUBLE", min_val=10.0, max_val=500.0),
        ColumnContract(name="order_date", expected_dtype="DATE"),
    ],
)

SHIPMENTS_CONTRACT = DataContract(
    table_name="fact_shipments",
    columns=[
        ColumnContract(name="shipment_id", expected_dtype="BIGINT"),
        ColumnContract(name="weight_kg", expected_dtype="DOUBLE", min_val=1.0, max_val=100.0),
        ColumnContract(name="latency_sec", expected_dtype="DOUBLE", min_val=0.5, max_val=60.0),
        ColumnContract(name="is_priority", expected_dtype="BOOLEAN"),
    ],
)


def get_chaos_scenarios() -> List[ChaosScenario]:
    """Returns 20 deterministic schema and statistical drift scenarios across 5 categories."""
    return [
        # CATEGORY 1: SCHEMA RENAMES
        ChaosScenario(
            scenario_id="case_01_single_rename",
            category="RENAME",
            description="Upstream renamed user_id to customer_id",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT 1001::BIGINT AS order_id, 'USR_01' AS customer_id, 120.50::DOUBLE AS amount_usd, DATE '2026-03-01' AS order_date
                UNION ALL
                SELECT 1002::BIGINT AS order_id, 'USR_02' AS customer_id, 85.00::DOUBLE AS amount_usd, DATE '2026-03-02' AS order_date
            """,
            contract=ORDERS_CONTRACT,
        ),
        ChaosScenario(
            scenario_id="case_02_multi_rename",
            category="RENAME",
            description="Upstream renamed all 4 columns to legacy ERP names",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT 1001::BIGINT AS ord_num, 'USR_01' AS client_code, 210.00::DOUBLE AS total_price_usd, DATE '2026-03-01' AS tx_date
                UNION ALL
                SELECT 1002::BIGINT AS ord_num, 'USR_02' AS client_code, 45.00::DOUBLE AS total_price_usd, DATE '2026-03-02' AS tx_date
            """,
            contract=ORDERS_CONTRACT,
        ),
        ChaosScenario(
            scenario_id="case_03_camelcase_drift",
            category="RENAME",
            description="API switched from snake_case to camelCase identifiers",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT 1001::BIGINT AS orderId, 'USR_01' AS userId, 150.00::DOUBLE AS amountUsd, DATE '2026-03-01' AS orderDate
                UNION ALL
                SELECT 1002::BIGINT AS orderId, 'USR_02' AS userId, 95.00::DOUBLE AS amountUsd, DATE '2026-03-02' AS orderDate
            """,
            contract=ORDERS_CONTRACT,
        ),
        ChaosScenario(
            scenario_id="case_04_rename_with_noise_cols",
            category="RENAME",
            description="Columns renamed plus 2 unrequested telemetry columns injected",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT 1001::BIGINT AS id_order, 'USR_01' AS usr_identifier, 60.0::DOUBLE AS usd_amount, DATE '2026-03-01' AS dt_created, 'v2.1' AS api_version, 999 AS _ingest_ts
                UNION ALL
                SELECT 1002::BIGINT AS id_order, 'USR_02' AS usr_identifier, 140.0::DOUBLE AS usd_amount, DATE '2026-03-02' AS dt_created, 'v2.1' AS api_version, 999 AS _ingest_ts
            """,
            contract=ORDERS_CONTRACT,
        ),

        # CATEGORY 2: TYPE & FORMAT DRIFT
        ChaosScenario(
            scenario_id="case_05_string_types",
            category="TYPE_CAST",
            description="All numeric and date columns serialized as VARCHAR strings",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT '1001' AS order_id, 'USR_01' AS user_id, '125.75' AS amount_usd, '2026-03-01' AS order_date
                UNION ALL
                SELECT '1002' AS order_id, 'USR_02' AS user_id, '89.25' AS amount_usd, '2026-03-02' AS order_date
            """,
            contract=ORDERS_CONTRACT,
        ),
        ChaosScenario(
            scenario_id="case_06_int_user_id_and_decimal",
            category="TYPE_CAST",
            description="user_id sent as integer and amount_usd sent as BIGINT",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT 1001::BIGINT AS order_id, 5501 AS user_id, 120::BIGINT AS amount_usd, '2026-03-01' AS order_date
                UNION ALL
                SELECT 1002::BIGINT AS order_id, 5502 AS user_id, 240::BIGINT AS amount_usd, '2026-03-02' AS order_date
            """,
            contract=ORDERS_CONTRACT,
        ),
        ChaosScenario(
            scenario_id="case_07_boolean_as_int_flag",
            category="TYPE_CAST",
            description="is_priority sent as 1/0 integer and shipment_id as VARCHAR",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT '501' AS shipment_id, 12.5::DOUBLE AS weight_kg, 4.2::DOUBLE AS latency_sec, 1 AS is_priority
                UNION ALL
                SELECT '502' AS shipment_id, 28.0::DOUBLE AS weight_kg, 8.1::DOUBLE AS latency_sec, 0 AS is_priority
            """,
            contract=SHIPMENTS_CONTRACT,
        ),
        ChaosScenario(
            scenario_id="case_08_slash_date_format",
            category="TYPE_CAST",
            description="Dates formatted as YYYY/MM/DD strings with dashes replaced",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT 1001::BIGINT AS order_id, 'USR_01' AS user_id, 75.0::DOUBLE AS amount_usd, '2026-03-10' AS order_date
                UNION ALL
                SELECT 1002::BIGINT AS order_id, 'USR_02' AS user_id, 195.0::DOUBLE AS amount_usd, '2026-03-11' AS order_date
            """,
            contract=ORDERS_CONTRACT,
        ),

        # CATEGORY 3: STATISTICAL & UNIT SHIFTS
        ChaosScenario(
            scenario_id="case_09_cents_instead_of_dollars",
            category="UNIT_SHIFT",
            description="amount_usd reported in cents (12500 instead of 125.00), violating max_val=500",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT 1001::BIGINT AS order_id, 'USR_01' AS user_id, 12500.0::DOUBLE AS amount_usd, DATE '2026-03-01' AS order_date
                UNION ALL
                SELECT 1002::BIGINT AS order_id, 'USR_02' AS user_id, 6400.0::DOUBLE AS amount_usd, DATE '2026-03-02' AS order_date
            """,
            contract=ORDERS_CONTRACT,
        ),
        ChaosScenario(
            scenario_id="case_10_grams_instead_of_kg",
            category="UNIT_SHIFT",
            description="weight_kg reported in grams (15000g instead of 15.0kg)",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT 501::BIGINT AS shipment_id, 15000.0::DOUBLE AS weight_kg, 5.0::DOUBLE AS latency_sec, TRUE AS is_priority
                UNION ALL
                SELECT 502::BIGINT AS shipment_id, 32000.0::DOUBLE AS weight_kg, 9.0::DOUBLE AS latency_sec, FALSE AS is_priority
            """,
            contract=SHIPMENTS_CONTRACT,
        ),
        ChaosScenario(
            scenario_id="case_11_ms_instead_of_seconds",
            category="UNIT_SHIFT",
            description="latency_sec reported in milliseconds (4500ms instead of 4.5s)",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT 501::BIGINT AS shipment_id, 10.0::DOUBLE AS weight_kg, 4500.0::DOUBLE AS latency_sec, TRUE AS is_priority
                UNION ALL
                SELECT 502::BIGINT AS shipment_id, 22.0::DOUBLE AS weight_kg, 12000.0::DOUBLE AS latency_sec, FALSE AS is_priority
            """,
            contract=SHIPMENTS_CONTRACT,
        ),
        ChaosScenario(
            scenario_id="case_12_thousands_scale_shift",
            category="UNIT_SHIFT",
            description="amount_usd reported in kUSD (0.12 instead of 120.0), violating min_val=10",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT 1001::BIGINT AS order_id, 'USR_01' AS user_id, 0.12::DOUBLE AS amount_usd, DATE '2026-03-01' AS order_date
                UNION ALL
                SELECT 1002::BIGINT AS order_id, 'USR_02' AS user_id, 0.25::DOUBLE AS amount_usd, DATE '2026-03-02' AS order_date
            """,
            contract=ORDERS_CONTRACT,
        ),

        # CATEGORY 4: DIRTY STRINGS & NULL SPIKES
        ChaosScenario(
            scenario_id="case_13_dollar_sign_prefix",
            category="DATA_CLEANING",
            description="amount_usd formatted with '$' prefix string ('$145.50')",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT 1001::BIGINT AS order_id, 'USR_01' AS user_id, '$145.50' AS amount_usd, DATE '2026-03-01' AS order_date
                UNION ALL
                SELECT 1002::BIGINT AS order_id, 'USR_02' AS user_id, '$78.00' AS amount_usd, DATE '2026-03-02' AS order_date
            """,
            contract=ORDERS_CONTRACT,
        ),
        ChaosScenario(
            scenario_id="case_14_unit_suffix_strings",
            category="DATA_CLEANING",
            description="weight_kg contains 'kg' text suffix ('18.5kg')",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT 501::BIGINT AS shipment_id, '18.5kg' AS weight_kg, 3.5::DOUBLE AS latency_sec, TRUE AS is_priority
                UNION ALL
                SELECT 502::BIGINT AS shipment_id, '42.0kg' AS weight_kg, 7.2::DOUBLE AS latency_sec, FALSE AS is_priority
            """,
            contract=SHIPMENTS_CONTRACT,
        ),
        ChaosScenario(
            scenario_id="case_15_null_spike_imputation",
            category="DATA_CLEANING",
            description="user_id has 50% NULL spike requiring COALESCE fallback to 'UNKNOWN_USER'",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT 1001::BIGINT AS order_id, 'USR_01' AS user_id, 110.0::DOUBLE AS amount_usd, DATE '2026-03-01' AS order_date
                UNION ALL
                SELECT 1002::BIGINT AS order_id, NULL AS user_id, 90.0::DOUBLE AS amount_usd, DATE '2026-03-02' AS order_date
            """,
            contract=ORDERS_CONTRACT,
        ),
        ChaosScenario(
            scenario_id="case_16_negative_amount_glitch",
            category="DATA_CLEANING",
            description="Sign inversion bug turned amount_usd negative (-130.0 instead of 130.0)",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT 1001::BIGINT AS order_id, 'USR_01' AS user_id, -130.0::DOUBLE AS amount_usd, DATE '2026-03-01' AS order_date
                UNION ALL
                SELECT 1002::BIGINT AS order_id, 'USR_02' AS user_id, -85.0::DOUBLE AS amount_usd, DATE '2026-03-02' AS order_date
            """,
            contract=ORDERS_CONTRACT,
        ),

        # CATEGORY 5: COMPOUND MULTI-DRIFT
        ChaosScenario(
            scenario_id="case_17_rename_plus_cents_plus_date_cast",
            category="COMPOUND",
            description="Renamed columns + cents-to-dollars shift + string date casting",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT '1001' AS id, 'USR_01' AS client_id, 14500 AS price_cents, '2026-03-01' AS created_on
                UNION ALL
                SELECT '1002' AS id, 'USR_02' AS client_id, 8900 AS price_cents, '2026-03-02' AS created_on
            """,
            contract=ORDERS_CONTRACT,
        ),
        ChaosScenario(
            scenario_id="case_18_shipments_grams_ms_rename",
            category="COMPOUND",
            description="Shipments renamed + weight in grams + latency in ms + int boolean",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT '501' AS ship_id, 25000 AS mass_grams, 3200 AS duration_ms, 1 AS priority_flag
                UNION ALL
                SELECT 502::BIGINT AS ship_id, 14000 AS mass_grams, 6800 AS duration_ms, 0 AS priority_flag
            """,
            contract=SHIPMENTS_CONTRACT,
        ),
        ChaosScenario(
            scenario_id="case_19_currency_string_plus_rename",
            category="COMPOUND",
            description="Renamed columns + '$USD ' string prefix + integer user_id",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT 1001::BIGINT AS tx_id, 9901 AS buyer_id, '$180.25' AS gross_total, '2026-03-05' AS tx_day
                UNION ALL
                SELECT 1002::BIGINT AS tx_id, 9902 AS buyer_id, '$64.50' AS gross_total, '2026-03-06' AS tx_day
            """,
            contract=ORDERS_CONTRACT,
        ),
        ChaosScenario(
            scenario_id="case_20_full_chaos_erp_export",
            category="COMPOUND",
            description="Legacy ERP export: renamed cols, cents, NULL user_id spike, and extra noise columns",
            setup_sql="""
                CREATE OR REPLACE VIEW raw_batch AS
                SELECT '1001' AS erp_order_no, 'USR_01' AS acct_code, 21000 AS minor_units_usd, '2026-03-01' AS posting_date, 'EU_WEST' AS region_code
                UNION ALL
                SELECT '1002' AS erp_order_no, NULL AS acct_code, 9500 AS minor_units_usd, '2026-03-02' AS posting_date, 'EU_WEST' AS region_code
            """,
            contract=ORDERS_CONTRACT,
        ),
    ]


def verify_all_scenarios_break_contracts() -> None:
    conn = duckdb.connect(":memory:")
    scenarios = get_chaos_scenarios()
    print(f"Verifying {len(scenarios)} Chaos Scenarios against their target DataContracts...\n")

    for idx, sc in enumerate(scenarios, 1):
        conn.execute(sc.setup_sql)
        diag = sc.contract.evaluate(conn, "raw_batch")
        assert diag is not None, f"ERROR: Scenario {sc.scenario_id} did not trigger any contract violation!"
        v_types = [v.violation_type for v in diag.violations]
        print(f"[{idx:02d}/20] {sc.scenario_id:<40} | Category: {sc.category:<13} | Violations: {len(v_types)}")

    print("\nAll 20 Chaos Scenarios verified: 100% trigger deterministic contract violations!")


if __name__ == "__main__":
    verify_all_scenarios_break_contracts()
