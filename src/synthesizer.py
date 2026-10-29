import os
import time
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types
from src.contracts import DriftDiagnostic
from src.orchestrator import PatchProposal

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=ROOT_DIR / ".env")


class LLMSynthesizer:
    def __init__(
        self,
        primary_model: str = "gemini-3.1-flash-lite-preview",
        fallback_models: Optional[List[str]] = None,
    ):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(f"GEMINI_API_KEY not found in .env file at {ROOT_DIR / '.env'}")
        
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=12000)
        )
        # Only use active, free-tier accessible models
        self.models = [primary_model] + (
            fallback_models or [
                "gemini-3.8-flash"
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
5. If units have changed (e.g., cents vs. dollars), apply arithmetic transformation and cast to DOUBLE.
6. If a column was renamed, alias the observed column name to the expected column name.
7. Return raw JSON without markdown fences."""

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
            clean_model = model_name.replace("gemini/", "")
            for attempt in range(3):
                try:
                    print(f"  [Synthesizer] Requesting patch from {clean_model} (attempt {attempt + 1})...", flush=True)
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
                    # 429 quota exhaustion or 404: skip model immediately
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "404" in err_str:
                        print(f"  [Synthesizer] {clean_model} quota or not found, skipping to next model...", flush=True)
                        break
                    
                    wait_sec = 2.5 * (attempt + 1)
                    print(f"  [Synthesizer] Temporary error on {clean_model}. Waiting {wait_sec}s...", flush=True)
                    time.sleep(wait_sec)

        raise RuntimeError(f"All model endpoints failed after retries: {last_error}")
