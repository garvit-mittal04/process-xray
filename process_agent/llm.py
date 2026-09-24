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
            max_output_tokens=32768,   # long replies, e.g. event logs for months of chat
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    return response.text or ""


BUSY_CODES = (429, 500, 502, 503, 504)
_out_of_quota: set[str] = set()   # models that said "quota used up" during this run
ROUNDS = 4                      # full passes through the model list when all are busy
ROUND_WAITS = [0, 20, 45, 90]   # seconds to wait before each pass


def generate_json(system: str, prompt: str, schema: type[T], max_attempts: int = 3) -> T:
    """Ask the model for JSON matching `schema`.

    - Bad JSON: tell the model what was wrong and retry (same model).
    - Busy or rate-limited: move to the next model quickly.
    - Every model busy: wait, then try the whole list again (up to ROUNDS times),
      because Google's servers are usually busy for minutes, not hours."""
    from google.genai import errors

    last_error: Exception | None = None
    usable = [m for m in MODELS if m not in _out_of_quota] or list(MODELS)
    for round_no in range(ROUNDS):
        if round_no:
            wait = ROUND_WAITS[min(round_no, len(ROUND_WAITS) - 1)]
            print(f"  All models busy. Waiting {wait}s, then trying again "
                  f"(round {round_no + 1}/{ROUNDS})...")
            time.sleep(wait)
        any_busy = False
        for model in list(usable):
            feedback = ""
            for attempt in range(1, max_attempts + 1):
                try:
                    raw = _call_model(model, system, prompt + feedback)
                    return schema.model_validate(json.loads(_extract_json(raw)))
                except (json.JSONDecodeError, ValidationError) as e:
                    last_error = e
                    feedback = (
                        "\n\nYour previous reply was not valid for the required JSON "
                        f"format. Error: {str(e)[:500]}\nReturn corrected JSON only."
                    )
                    print(f"  [{model}] invalid JSON, retrying ({attempt}/{max_attempts})")
                except errors.APIError as e:
                    last_error = e
                    code = getattr(e, "code", None)
                    if code in BUSY_CODES and attempt == 1:
                        print(f"  [{model}] busy ({code}), retrying in 5s")
                        time.sleep(5)
                        continue
                    if code == 429:
                        # Quota used up: skip this model for the rest of the run
                        _out_of_quota.add(model)
                        usable.remove(model)
                        print(f"  [{model}] quota used up, skipping it for this run")
                        break
                    print(f"  [{model}] unavailable ({code}), trying next model")
                    if code in BUSY_CODES:
                        any_busy = True
                    else:
                        usable.remove(model)      # e.g. not in the free tier; skip from now on
                    break
        if not any_busy or not usable:
            break
    raise RuntimeError(f"All models failed. Last error: {last_error}")
