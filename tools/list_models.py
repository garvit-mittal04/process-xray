"""Show which models your API keys can use, and the order the agents will try them.

Usage: python3 tools/list_models.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx  # noqa: E402

from process_agent import llm  # noqa: E402  (also loads .env)

print("Agents will try, in order:\n  " + "\n  ".join(llm.MODELS))

if os.getenv("GEMINI_API_KEY"):
    from google import genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    names = [m.name.removeprefix("models/") for m in client.models.list() if "flash" in m.name]
    print("\nGemini flash models:\n  " + "\n  ".join(names))

for provider, (base, key_var) in llm.PROVIDERS.items():
    key = os.getenv(key_var)
    if not key:
        print(f"\n{provider}: no {key_var} in .env (optional)")
        continue
    r = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    if r.status_code != 200:
        print(f"\n{provider}: key check failed ({r.status_code})")
        continue
    ids = sorted(m["id"] for m in r.json().get("data", []))
    if provider == "openrouter":
        ids = [i for i in ids if i.endswith(":free")]
    print(f"\n{provider} models ({len(ids)}):\n  " + "\n  ".join(ids[:40]))
