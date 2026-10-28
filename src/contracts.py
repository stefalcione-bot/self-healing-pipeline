from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
import duckdb


class ColumnContract(BaseModel):
    name: str
    expected_dtype: str  # e.g., "BIGINT", "DOUBLE", "VARCHAR", "TIMESTAMP"
    max_null_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    min_val: Optional[float] = None
    max_val: Optional[float] = None


class DriftViolation(BaseModel):
    violation_type: str  # "MISSING_COLUMN", "TYPE_MISMATCH", "NULL_SPIKE", "DISTRIBUTION_SHIFT"
    column: str
    expected: str
    observed: str


class DriftDiagnostic(BaseModel):
    """Compact, privacy-safe payload passed to the LLM Synthesizer."""
    target_table: str
    expected_schema: Dict[str, str]
    observed_schema: Dict[str, str]
    violations: List[DriftViolation]
    observed_sample_rows: List[Dict[str, Any]]  # Strictly capped at 3 rows


class DataContract:
    def __init__(self, table_name: str, columns: List[ColumnContract], min_row_count: int = 1):
        self.table_name = table_name
        self.columns = {col.name: col for col in columns}
        self.min_row_count = min_row_count

    def evaluate(self, conn: duckdb.DuckDBPyConnection, view_name: str) -> Optional[DriftDiagnostic]:
        """Evaluates an incoming DuckDB relation against structural and statistical invariants."""
        violations: List[DriftViolation] = []

        # 1. Inspect observed schema via DuckDB DESCRIBE
        schema_rows = conn.execute(f"DESCRIBE {view_name}").fetchall()
        observed_schema = {row[0]: row[1] for row in schema_rows}
        expected_schema = {name: col.expected_dtype for name, col in self.columns.items()}

        # 2. Check structural invariants (missing columns & type mismatches)
        for col_name, contract in self.columns.items():
            if col_name not in observed_schema:
                violations.append(DriftViolation(
                    violation_type="MISSING_COLUMN",
                    column=col_name,
                    expected=contract.expected_dtype,
                    observed="MISSING"
                ))
            elif observed_schema[col_name] != contract.expected_dtype:
                violations.append(DriftViolation(
                    violation_type="TYPE_MISMATCH",
                    column=col_name,
                    expected=contract.expected_dtype,
                    observed=observed_schema[col_name]
                ))

        # 3. Check statistical invariants (null rate & numeric bounds) for matching columns
        total_rows = conn.execute(f"SELECT COUNT(*) FROM {view_name}").fetchone()[0]
        if total_rows > 0:
            for col_name, contract in self.columns.items():
                if col_name in observed_schema:
                    stats = conn.execute(f"""
                        SELECT 
                            COUNT(*) FILTER (WHERE "{col_name}" IS NULL) * 1.0 / COUNT(*),
                            TRY_CAST(AVG(TRY_CAST("{col_name}" AS DOUBLE)) AS DOUBLE)
                        FROM {view_name}
                    """).fetchone()
                    null_rate, mean_val = stats[0], stats[1]

                    if null_rate > contract.max_null_rate:
                        violations.append(DriftViolation(
                            violation_type="NULL_SPIKE",
                            column=col_name,
                            expected=f"null_rate <= {contract.max_null_rate:.2f}",
                            observed=f"null_rate == {null_rate:.2f}"
                        ))
                    if mean_val is not None:
                        if (contract.min_val is not None and mean_val < contract.min_val) or \
                           (contract.max_val is not None and mean_val > contract.max_val):
                            violations.append(DriftViolation(
                                violation_type="DISTRIBUTION_SHIFT",
                                column=col_name,
                                expected=f"mean in [{contract.min_val}, {contract.max_val}]",
                                observed=f"mean == {mean_val:.2f}"
                            ))

        if not violations:
            return None

        # Fetch 3 sample rows as dicts to help the LLM spot renamed columns or unit shifts (e.g., cents vs dollars)
        sample_df = conn.execute(f"SELECT * FROM {view_name} LIMIT 3").fetchdf()
        sample_rows = sample_df.to_dict(orient="records")

        return DriftDiagnostic(
            target_table=self.table_name,
            expected_schema=expected_schema,
            observed_schema=observed_schema,
            violations=violations,
            observed_sample_rows=sample_rows
        )