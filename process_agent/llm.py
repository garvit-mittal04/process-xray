"""One place for all model calls: JSON output, validation, retries, fallback.

Models are listed in order of preference. Plain names are Gemini models;
"groq:<model>" and "openrouter:<model>" use those providers' free tiers
through their OpenAI-compatible APIs. If a provider's key is missing, its
models are skipped, so the app works with just a Gemini key."""
import hashlib
import json
import os
import re
import time
from contextvars import ContextVar
from typing import Callable, TypeVar

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv()   # command-line settings win over .env
# Another project may have set GOOGLE_API_KEY globally; ignore it so this
# project always uses its own GEMINI_API_KEY.
os.environ.pop("GOOGLE_API_KEY", None)

T = TypeVar("T", bound=BaseModel)

PROVIDERS = {   # prefix -> (base URL, env var holding the key)
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
}


# Keys and log output for the current session. The Streamlit app sets these
# per visitor; the command line falls back to environment variables / .env.
_session_keys: ContextVar[dict | None] = ContextVar("session_keys", default=None)
_session_log: ContextVar[Callable[[str], None]] = ContextVar("session_log", default=print)


def use_session(keys: dict | None = None, log: Callable[[str], None] | None = None) -> None:
    """Set API keys ({"gemini": ..., "groq": ..., "openrouter": ...}) and a log
    function for this session only."""
    _session_keys.set({k: v for k, v in (keys or {}).items() if v} if keys is not None else None)
    if log:
        _session_log.set(log)


def _key(provider: str) -> str | None:
    keys = _session_keys.get()
    if keys is not None:
        return keys.get(provider)
    env = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
    return os.getenv(env[provider])


def _log(msg: str) -> None:
    _session_log.get()(msg)


def models() -> list[str]:
    """Models to try, in order, given the keys available right now."""
    custom = [m.strip() for m in os.getenv("LLM_MODELS", "").split(",") if m.strip()]
    if custom and _session_keys.get() is None:
        return custom
    gem, groq = bool(_key("gemini")), bool(_key("groq"))
    groq_models = os.getenv("GROQ_MODELS", "openai/gpt-oss-120b,qwen/qwen3.8-27b").split(",")
    order = []
    if gem:
        order.append("gemini-2.5-flash")
    if groq:
        order.append("groq:" + groq_models[0].strip())
    if gem:
        order.append("gemini-flash-latest")
    if groq:
        order += ["groq:" + m.strip() for m in groq_models[1:] if m.strip()]
    if gem:
        order.append("gemini-flash-lite-latest")
    if _key("openrouter") and os.getenv("OPENROUTER_MODEL"):
        order.append("openrouter:" + os.environ["OPENROUTER_MODEL"])
    return order


MODELS = models()   # for display in tools/list_models.py

BUSY_CODES = (429, 500, 502, 503, 504)
ROUNDS = 4                      # full passes through the model list when all are busy
ROUND_WAITS = [0, 20, 45, 90]   # seconds to wait before each pass
_cooldown: dict[str, float] = {} # (model + key fingerprint) -> time it can be used again
last_model_used: str = ""       # which model answered the most recent call


def _slot(model: str) -> str:
    """Identify a model *for a given key*, so one visitor's exhausted quota
    never blocks another visitor's key."""
    provider = model.partition(":")[0] if ":" in model else "gemini"
    fp = hashlib.sha256((_key(provider) or "").encode()).hexdigest()[:12]
    return f"{model}|{fp}"


def _quota_cooldown(message: str) -> float:
    """Per-minute limits clear quickly; daily limits don't."""
    m = message.lower()
    if "perday" in m or "per day" in m or "daily" in m or "requests per day" in m:
        return 6 * 3600
    return 65


class ProviderError(Exception):
    def __init__(self, code: int | None, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------- Gemini ----------

_gemini_clients: dict[str, object] = {}


def _gemini_client():
    from google import genai
    key = _key("gemini")
    if not key:
        raise ProviderError(401, "No Gemini key provided")
    if key not in _gemini_clients:
        _gemini_clients[key] = genai.Client(api_key=key)
    return _gemini_clients[key]


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
    key = _key(provider)
    if not key:
        raise ProviderError(401, f"No {provider} key provided")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}"}
    for _ in range(3):
        try:
            r = httpx.post(f"{base_url}/chat/completions", json=body, headers=headers, timeout=180)
        except httpx.HTTPError as e:
            raise ProviderError(503, f"network error: {e}") from e
        if r.status_code == 400 and "response_format" in body and "response_format" in r.text:
            body.pop("response_format")           # some models don't support JSON mode
            continue
        if r.status_code in (413, 429):
            # Groq counts the reserved reply length against its per-minute limit.
            # If the message says "Limit X, Requested Y", reserve less and retry.
            m = re.search(r"Limit\s+(\d+).*?Requested\s+(\d+)", r.text, re.S | re.I)
            if m:
                smaller = body["max_tokens"] - (int(m.group(2)) - int(m.group(1))) - 200
                if smaller >= 1500 and smaller < body["max_tokens"]:
                    body["max_tokens"] = smaller
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
    - Quota hit (429): rest that model for a minute (per-minute limit) or for
      hours (daily limit), and use the others meanwhile.
    - No key, prompt too large, model unavailable: skip it for this call.
    - Everything busy or resting: wait, then try the whole list again."""
    global last_model_used
    last_error: Exception | None = None
    candidates = models()
    if not candidates:
        raise RuntimeError("No AI key available. Add a Gemini or Groq key.")
    skipped: set[str] = set()
    for round_no in range(ROUNDS):
        if round_no:
            resting = [_cooldown[_slot(m)] - time.time() for m in candidates
                       if m not in skipped and _cooldown.get(_slot(m), 0) > time.time()]
            wait = ROUND_WAITS[min(round_no, len(ROUND_WAITS) - 1)]
            if resting:
                wait = max(5, min(wait, min(resting) + 1))
            _log(f"All models busy. Waiting {wait:.0f}s, then trying again (round {round_no + 1}/{ROUNDS})...")
            time.sleep(wait)
        any_busy = False
        for model in candidates:
            if model in skipped:
                continue
            if _cooldown.get(_slot(model), 0) > time.time():
                any_busy = True
                continue
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
                    _log(f"[{model}] invalid JSON, retrying ({attempt}/{max_attempts})")
                except ProviderError as e:
                    last_error = e
                    code = e.code
                    if code == 401:
                        _log(f"[{model}] no key or key rejected, skipping")
                        skipped.add(model)
                        break
                    if code == 429:
                        rest = _quota_cooldown(str(e))
                        _cooldown[_slot(model)] = time.time() + rest
                        _log(f"[{model}] rate limit reached, resting it for "
                             f"{'a minute' if rest < 120 else 'a few hours'}")
                        any_busy = any_busy or rest < 120
                        break
                    if code in BUSY_CODES and attempt == 1:
                        _log(f"[{model}] busy ({code}), retrying in 5s")
                        time.sleep(5)
                        continue
                    _log(f"[{model}] unavailable ({code}), trying next model")
                    if code in BUSY_CODES:
                        any_busy = True
                    else:
                        skipped.add(model)   # e.g. prompt too large for this model
                    break
        if not any_busy:
            break
    raise RuntimeError(f"All models failed. Last error: {last_error}")
