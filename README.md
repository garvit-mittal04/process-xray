# 🩻 Process X-Ray

**See how a small business really runs, from its WhatsApp group.**
Upload a chat export and AI agents reconstruct the process, find where time is
lost, propose automations, stress-test them, and **replay the real history** to
show what would have changed.

**Live demo:** _add your Streamlit link here_

## Why it's different
- **Works from WhatsApp**, where most small Indian businesses actually run their
  operations, instead of ERP logs or screen recording.
- **Replay, not guesswork:** savings are measured by re-running the business's
  own timeline with the automations applied.
- **Honest by design:** a devil's-advocate agent attacks every idea, and code
  guardrails stop the AI from inflating numbers (for example, it can never
  "speed up" a customer's payment or a transporter's truck).
- **Private:** names, customer names, phone numbers and emails are masked
  before any AI call; uploads are processed in memory and not stored.
- **No setup for visitors:** upload a chat and press one button. The app uses its
  own free Gemini and Groq keys, with a daily cap to protect the quota; visitors
  can optionally bring their own key.

## How it works
1. **Parser** reads Android or iPhone exports and redacts personal data
2. **Mapper agent** reconstructs the process, citing message numbers as evidence
3. **Measurement** (code) computes frequencies and waits from timestamps, in working hours
4. **Analyst agent** scores every step and chooses: eliminate, simplify, integrate, automate, or keep human
5. **Event-log agent** traces every order through the interleaved chat, in chunks
6. **Recommender agent** designs complete automations and states how each changes timing
7. **Devil's advocate agent** lists risks, who may resist, and a verdict
8. **Replay engine** (code) re-runs the history with the approved automations

## Run it
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env        # add a free Gemini key (and optionally Groq)
    streamlit run app.py        # the web app
    python3 xray.py sample_data/sharma_traders_2months.txt   # or the command line

## Deploy on Streamlit Community Cloud
1. Deploy `app.py` from this repo (Python 3.12).
2. In the app's **Settings → Secrets**, add the app's own keys:

       GEMINI_API_KEY = "..."
       GROQ_API_KEY = "..."
       SHARED_RUNS_PER_DAY = "20"      # optional, free analyses per day for all visitors

   Keys stay on the server; visitors never see them. Use keys created just for
   this app, so a busy day never affects your own projects.

## Sample data
Both sample chats are fictional. `tools/generate_sample.py` creates the
two-month chat (32 orders) reproducibly, with realistic delays, partial stock,
invoice rate errors, reminders, and slow payers.

## Project status
- [x] Parser with privacy redaction and customer-name masking
- [x] Mapper, Analyst, Event-log, Recommender and Devil's-advocate agents
- [x] Replay engine with guardrails
- [x] Free multi-provider fallback (Gemini, Groq, OpenRouter) with per-visitor keys
- [x] Streamlit app with instant demo mode
- [ ] Local-only mode with Ollama (nothing leaves the computer)
