import os
import time
from typing import List, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types
from src.contracts import DriftDiagnostic
from src.orchestrator import PatchProposal

load_dotenv()


class LLMSynthesizer:
    def __init__(
        self,
        primary_model: str = "gemini-3.8-flash",
        fallback_models: Optional[List[str]] = None,
    ):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found in .env file.")
        self.client = genai.Client(api_key=api_key)
        self.models = [primary_model] + (
            fallback_models or [
                "gemini-3.1-pro-preview",
                "gemini-3.1-flash-lite-preview",
            ]
        )

    def _build_system_prompt(self, diagnostic: DriftDiagnostic) -> str:
        return f"""You are an autonomous SQL repair compiler for an ingestion pipeline running on DuckDB.
Your mission is to generate a single, deterministic SQL SELECT query that transforms the corrupted view `raw_batch` back into the target contract schema `{diagnostic.target_table}`.

STRICT OPERATIONAL RULES:
1. Output MUST be valid DuckDB SQL.
2. Root query MUST be a `SELECT` projection. Never use DROP, DELETE, INSERT, UPDATE, or ALTER.
3. Every referenced source column MUST exist in the observed schema: {list(diagnostic.observed_schema.keys())}.
4. Output column names and data types MUST strictly match the expected contract:
{diagnostic.expected_schema}
5. If units have changed (e.g., cents vs. dollars), apply the arithmetic transformation and cast to DOUBLE.
6. If a column was renamed, alias the observed column name to the expected column name.
7. Do not include markdown fences or trailing semicolons in `sql_patch`."""

    def generate_patch(self, diagnostic: DriftDiagnostic, error_history: List[str]) -> PatchProposal:
        system_prompt = self._build_system_prompt(diagnostic)

        user_content = f"""Current Data Contract Violations:
{diagnostic.model_dump_json(indent=2)}

Observed Sample Rows (up to 3):
{diagnostic.observed_sample_rows}
"""

        if error_history:
            user_content += "\n\nCRITICAL - PREVIOUS ATTEMPTS FAILED WITH THESE ERRORS:\n"
            for i, err in enumerate(error_history, 1):
                user_content += f"{i}. {err}\n"
            user_content += "\nAdjust your SQL query to resolve the above errors."

        last_error = None
        for model_name in self.models:
            # Strip any 'gemini/' prefix if passed from older caller code
            clean_model = model_name.replace("gemini/", "")
            for attempt in range(3):
                try:
                    print(f"  [Synthesizer] Requesting patch from {clean_model} (attempt {attempt + 1})...")
                    response = self.client.models.generate_content(
                        model=clean_model,
                        contents=user_content,
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            response_mime_type="application/json",
                            response_schema=PatchProposal,
                        ),
                    )
                    return PatchProposal.model_validate_json(response.text)
                except Exception as e:
                    last_error = e
                    err_str = str(e)
                    # If model name is not found (404), immediately jump to next fallback model
                    if "404" in err_str or "NOT_FOUND" in err_str:
                        print(f"  [Synthesizer] Model {clean_model} unavailable (404), switching model...")
                        break
                    # If 503 high demand, wait with exponential backoff before retrying
                    wait_sec = 2 ** (attempt + 1)
                    print(f"  [Synthesizer] Temporary error ({err_str[:80]}...). Retrying in {wait_sec}s...")
                    time.sleep(wait_sec)

        raise RuntimeError(f"All model endpoints failed after retries. Last error: {last_error}")
