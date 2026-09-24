# Process X-Ray

Upload a WhatsApp chat export. See how the business really runs, where time is
lost, and what to automate, with savings proven by replaying the real history.

## Status
- [x] Step 1: WhatsApp parser with privacy redaction and process signals
- [x] Step 2: Mapper agent reconstructs the process (Gemini, with retries and model fallback)
- [ ] Step 3: Analyst agent scores every step
- [ ] Step 4: Recommender + devil's advocate agents
- [ ] Step 5: Replay engine (counterfactual savings on real chat history)
- [ ] Step 6: Streamlit app

## Run it
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env        # then add your free Gemini key from aistudio.google.com
    python3 xray.py sample_data/sharma_traders_orders.txt

Use `--parse` to run only the parser (no AI key needed).

## Privacy
Names become "Person 1, 2, 3", and phone numbers and emails are removed on your
machine before anything is sent to an AI model.
