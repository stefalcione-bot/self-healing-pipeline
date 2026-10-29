from typing import Set, Tuple, Optional
import sqlglot
from sqlglot import exp
import duckdb
from src.contracts import DataContract, DriftDiagnostic


class ASTGuardrail:
    """Static analysis barrier using SQLGlot AST parsing."""

    FORBIDDEN_EXPRESSIONS = (
        exp.Drop,
        exp.Delete,
        exp.Update,
        exp.Insert,
        exp.Alter,
        exp.Command,
        exp.Create,
        exp.TruncateTable,
    )

    @classmethod
    def verify_sql_ast(
        cls, sql_query: str, allowed_source_columns: Set[str], source_table: str = "raw_batch"
    ) -> Tuple[bool, Optional[str]]:
        if not sql_query or not sql_query.strip():
            return False, "SQL Syntax Error: Empty query provided."

        try:
            statements = sqlglot.parse(sql_query, read="duckdb")
        except sqlglot.errors.ParseError as e:
            return False, f"SQL Syntax Error: {str(e)}"

        # 0. Block stacked multi-statement queries (e.g., SELECT ...; DROP TABLE ...)
        valid_stmts = [s for s in statements if s is not None]
        if len(valid_stmts) != 1:
            return False, f"Security Violation: Expected exactly 1 SQL statement, got {len(valid_stmts)}."

        parsed = valid_stmts[0]

        # 1. Must be a SELECT query at the root
        if not isinstance(parsed, exp.Select):
            return False, f"Security Violation: Root AST node must be SELECT, got {type(parsed).__name__}."

        # 2. Block any destructive DDL/DML nodes anywhere in the AST
        for forbidden in cls.FORBIDDEN_EXPRESSIONS:
            if parsed.find(forbidden):
                return False, f"Security Violation: Forbidden AST node '{forbidden.__name__}' detected."

        # 3. Verify all referenced source columns actually exist in the incoming corrupted table
        referenced_columns = {col.name for col in parsed.find_all(exp.Column) if col.name}
        select_aliases = {alias.alias for alias in parsed.find_all(exp.Alias) if alias.alias}
        unknown_cols = (referenced_columns - allowed_source_columns) - select_aliases

        if unknown_cols:
            return False, f"Hallucinated source columns not present in raw batch: {sorted(unknown_cols)}"

        return True, None


class EphemeralSandbox:
    """Executes the candidate SQL transformation in an isolated view and re-checks the DataContract."""

    @staticmethod
    def verify_in_sandbox(
        conn: duckdb.DuckDBPyConnection,
        candidate_sql: str,
        contract: DataContract
    ) -> Tuple[bool, Optional[str]]:
        sandbox_view = "staging_candidate_view"
        try:
            conn.execute(f"CREATE OR REPLACE TEMP VIEW {sandbox_view} AS ({candidate_sql})")
            post_diagnostic: Optional[DriftDiagnostic] = contract.evaluate(conn, sandbox_view)
            if post_diagnostic is not None:
                return False, f"Post-transform contract validation failed: {post_diagnostic.model_dump_json()}"
            return True, None
        except duckdb.Error as e:
            return False, f"DuckDB Runtime Execution Error: {str(e)}"
