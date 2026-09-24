"""One place for all model calls: JSON output, validation, retries, fallback."""
import json
import os
import re
import time
from typing import TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv(override=True)
# Another project may have set GOOGLE_API_KEY globally; ignore it so this
# project always uses its own GEMINI_API_KEY.
os.environ.pop("GOOGLE_API_KEY", None)

T = TypeVar("T", bound=BaseModel)

MODELS = [
    m.strip()
    for m in os.getenv("LLM_MODELS", "gemini-2.5-flash,gemini-flash-latest,gemini-flash-lite-latest").split(",")
    if m.strip()
]

_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is missing. Add it to your .env file.")
        _client = genai.Client(api_key=key)
    return _client


def _extract_json(text: str) -> str:
    """Strip ```json fences or stray text around the JSON object."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end != -1 else text


def _call_model(model: str, system: str, prompt: str) -> str:
    from google.genai import types
    response = _get_client().models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0.2,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    return response.text or ""


def generate_json(system: str, prompt: str, schema: type[T], max_attempts: int = 3) -> T:
    """Ask the model for JSON matching `schema`. Retries on bad JSON and on
    rate limits, then falls back to the next model in MODELS."""
    from google.genai import errors

    last_error: Exception | None = None
    for model in MODELS:
        feedback = ""
        for attempt in range(1, max_attempts + 1):
            try:
                raw = _call_model(model, system, prompt + feedback)
                return schema.model_validate(json.loads(_extract_json(raw)))
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = e
                # Tell the model exactly what was wrong and try again
                feedback = (
                    "\n\nYour previous reply was not valid for the required JSON "
                    f"format. Error: {str(e)[:500]}\nReturn corrected JSON only."
                )
                print(f"  [{model}] invalid JSON, retrying ({attempt}/{max_attempts})")
            except errors.APIError as e:
                last_error = e
                code = getattr(e, "code", None)
                if code in (429, 500, 503) and attempt < 2:
                    wait = 5
                    print(f"  [{model}] busy or rate-limited ({code}), waiting {wait}s")
                    time.sleep(wait)
                    continue
                print(f"  [{model}] failed ({code}), trying next model")
                break
    raise RuntimeError(f"All models failed. Last error: {last_error}")
