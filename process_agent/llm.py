"""One place for all model calls: JSON output, validation, retries, fallback.

Models are listed in order of preference. Plain names are Gemini models;
"groq:<model>" and "openrouter:<model>" use those providers' free tiers
through their OpenAI-compatible APIs. If a provider's key is missing, its
models are skipped, so the app works with just a Gemini key."""
import json
import os
import re
import time
from typing import TypeVar

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv(override=True)
# Another project may have set GOOGLE_API_KEY globally; ignore it so this
# project always uses its own GEMINI_API_KEY.
os.environ.pop("GOOGLE_API_KEY", None)

T = TypeVar("T", bound=BaseModel)

PROVIDERS = {   # prefix -> (base URL, env var holding the key)
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
}


def _default_models() -> list[str]:
    models = ["gemini-2.5-flash"]
    if os.getenv("GROQ_API_KEY"):
        models.append("groq:" + os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))
    models += ["gemini-flash-latest", "gemini-flash-lite-latest"]
    if os.getenv("OPENROUTER_API_KEY") and os.getenv("OPENROUTER_MODEL"):
        models.append("openrouter:" + os.environ["OPENROUTER_MODEL"])
    return models


MODELS = [m.strip() for m in os.getenv("LLM_MODELS", "").split(",") if m.strip()] or _default_models()

BUSY_CODES = (429, 500, 502, 503, 504)
ROUNDS = 4                      # full passes through the model list when all are busy
ROUND_WAITS = [0, 20, 45, 90]   # seconds to wait before each pass
_out_of_quota: set[str] = set() # models that said "quota used up" during this run
last_model_used: str = ""       # which model answered the most recent call


class ProviderError(Exception):
    def __init__(self, code: int | None, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------- Gemini ----------

_gemini = None


def _gemini_client():
    global _gemini
    if _gemini is None:
        from google import genai
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise ProviderError(401, "GEMINI_API_KEY is missing. Add it to your .env file.")
        _gemini = genai.Client(api_key=key)
    return _gemini


def _call_gemini(model: str, system: str, prompt: str) -> str:
    from google.genai import errors, types
    try:
        response = _gemini_client().models.generate_content(
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
    except errors.APIError as e:
        raise ProviderError(getattr(e, "code", None), str(e)[:300]) from e
    return response.text or ""


# ---------- OpenAI-compatible providers (Groq, OpenRouter) ----------

def _call_openai_compatible(provider: str, model: str, system: str, prompt: str) -> str:
    base_url, key_var = PROVIDERS[provider]
    key = os.getenv(key_var)
    if not key:
        raise ProviderError(401, f"{key_var} is not set")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}"}
    for _ in range(2):
        try:
            r = httpx.post(f"{base_url}/chat/completions", json=body, headers=headers, timeout=180)
        except httpx.HTTPError as e:
            raise ProviderError(503, f"network error: {e}") from e
        if r.status_code == 400 and "response_format" in body and "response_format" in r.text:
            body.pop("response_format")           # some models don't support JSON mode
            continue
        break
    if r.status_code != 200:
        raise ProviderError(r.status_code, r.text[:300])
    try:
        return r.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, ValueError) as e:
        raise ProviderError(502, f"unexpected response: {r.text[:200]}") from e


def _call_model(model: str, system: str, prompt: str) -> str:
    provider, _, name = model.partition(":")
    if name and provider in PROVIDERS:
        return _call_openai_compatible(provider, name, system, prompt)
    return _call_gemini(model, system, prompt)


# ---------- JSON handling, retries and fallback ----------

def _extract_json(text: str) -> str:
    """Strip ```json fences or stray text around the JSON object."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end != -1 else text


def generate_json(system: str, prompt: str, schema: type[T], max_attempts: int = 3) -> T:
    """Ask a model for JSON matching `schema`.

    - Bad JSON: tell the model what was wrong and retry (same model).
    - Busy: retry once, then move to the next model.
    - Quota used up (429): skip that model for the rest of the run.
    - Prompt too large or model unavailable: skip it for this call.
    - Every model busy: wait, then try the whole list again (up to ROUNDS times)."""
    global last_model_used
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
                    result = schema.model_validate(json.loads(_extract_json(raw)))
                    last_model_used = model
                    return result
                except (json.JSONDecodeError, ValidationError) as e:
                    last_error = e
                    feedback = (
                        "\n\nYour previous reply was not valid for the required JSON "
                        f"format. Error: {str(e)[:500]}\nReturn corrected JSON only."
                    )
                    print(f"  [{model}] invalid JSON, retrying ({attempt}/{max_attempts})")
                except ProviderError as e:
                    last_error = e
                    code = e.code
                    if code == 401:
                        print(f"  [{model}] no key or key rejected, skipping")
                        _out_of_quota.add(model)
                        usable.remove(model)
                        break
                    if code in BUSY_CODES and attempt == 1:
                        print(f"  [{model}] busy ({code}), retrying in 5s")
                        time.sleep(5)
                        continue
                    if code == 429:
                        _out_of_quota.add(model)
                        usable.remove(model)
                        print(f"  [{model}] quota used up, skipping it for this run")
                        break
                    print(f"  [{model}] unavailable ({code}), trying next model")
                    if code in BUSY_CODES:
                        any_busy = True
                    else:
                        usable.remove(model)   # e.g. prompt too large for this model
                    break
        if not any_busy or not usable:
            break
    raise RuntimeError(f"All models failed. Last error: {last_error}")
