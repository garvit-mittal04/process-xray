"""Generate a realistic two-month WhatsApp order group for a fictional distributor.

Everything here is invented: the business, people, customers and numbers.
The randomness is seeded, so the same file is produced every time.

Usage: python3 tools/generate_sample.py
Writes: sample_data/sharma_traders_2months.txt
"""
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from process_agent.measure import add_working_hours  # noqa: E402

OWNER, OFFICE, GODOWN = "Rakesh Sharma", "Anjali Verma", "Suresh Yadav"
CUSTOMERS = ["Mehta Retail", "Gupta Stores", "Agarwal Traders", "Singh Kirana",
             "Bansal Mart", "Khanna Distributors", "Jain Provision", "Arora Supermart"]
PRODUCTS = {"Sunflower oil 1L": 1450, "Basmati rice 25kg": 2100, "Atta 10kg": 420,
            "Sugar 50kg": 2250, "Mustard oil 15L": 2350}
START, END = datetime(2026, 3, 2, 9, 0), datetime(2026, 4, 30, 19, 0)


def fmt(t: datetime) -> str:
    hour = t.hour % 12 or 12
    return f"{t:%d/%m/%y}, {hour}:{t:%M} {'am' if t.hour < 12 else 'pm'}"


def generate(seed: int = 7):
    rng = random.Random(seed)
    msgs = []                      # (time, sender, text, event_key, order_no)

    def say(t, who, text, key=None, order=None):
        msgs.append((t, who, text, key, order))

    def later(t, lo, hi):
        return add_working_hours(t, rng.uniform(lo, hi))

    # Order arrival times: about 30 orders spread over the period
    t = add_working_hours(START, 0.5)
    arrivals = []
    while t < datetime(2026, 4, 25):
        arrivals.append(t)
        t = add_working_hours(t, rng.uniform(8, 25))

    inv_no = 2301
    for n, t0 in enumerate(arrivals, 1):
        cust, product = rng.choice(CUSTOMERS), rng.choice(list(PRODUCTS))
        rate, qty = PRODUCTS[product], rng.choice([10, 15, 20, 25, 30, 40, 50])
        short = cust.split()[0]

        say(t0, OWNER, f"{cust} ka order aaya hai - {qty} cartons {product}. PO attached", "order", n)
        if rng.random() < 0.7:
            say(t0 + timedelta(minutes=1), OWNER, f"PO_{short}_{t0:%d%m}.pdf (file attached)")

        t1 = later(t0, 0.1, 0.8)
        if rng.random() < 0.6:
            say(t1, OFFICE, "Received sir. Stock check karti hoon")
        t2 = later(t1, 0.05, 0.3)
        say(t2, OFFICE, f"Suresh {product} kitna stock hai? {short} ji ko {qty} chahiye", "stock_request", n)

        wait = rng.choice([rng.uniform(0.3, 2), rng.uniform(2, 6), rng.uniform(4, 9)])
        if wait > 2.5:
            chaser = rng.choice([(OFFICE, "Suresh jaldi batao, customer wait kar rahe hain"),
                                 (OWNER, "Suresh any update?")])
            say(later(t2, 2, 2.3), *chaser, "chaser", n)
        t3 = add_working_hours(t2, wait)
        partial = rng.random() < 0.3
        avail = rng.choice([q for q in range(5, qty, 5)] or [qty]) if partial else qty
        say(t3, GODOWN, f"{avail} cartons hai madam", "stock_confirm", n)

        t_prev = t3
        if partial:
            t4 = later(t3, 0.1, 0.5)
            say(t4, OFFICE, f"Sir {short} ji ke liye sirf {avail} hai. Partial dispatch karein?")
            t_prev = later(t4, 0.3, 5)
            say(t_prev, OWNER, f"Haan {short} ji ko {avail} bhej do, baaki next week", "partial_decision", n)

        # Invoice, sometimes with a rate mistake
        wrong_rate = rng.random() < 0.15
        shown_rate = rate - 10 if wrong_rate else rate
        t6 = later(t_prev, 0.3, 2)
        inv = f"INV_{inv_no}"
        inv_no += 1
        say(t6, OFFICE, f"Invoice {inv} ready - {short} ji, {avail} cartons @ Rs {shown_rate}. "
                        f"Approval please", "invoice", n)
        say(t6 + timedelta(minutes=1), OFFICE, f"{inv}.pdf (file attached)")

        approve_wait = rng.choice([rng.uniform(0.3, 2), rng.uniform(2, 6), rng.uniform(6, 14)])
        if approve_wait > 3 and rng.random() < 0.7:
            say(later(t6, 3, 3.3), OFFICE, f"Gentle reminder sir, {inv} approval pending", "chaser", n)
        t7 = add_working_hours(t6, approve_wait)
        if wrong_rate:
            say(t7, OWNER, f"Rate galat hai, {rate} tha {shown_rate} nahi. Correct karo", "rate_error", n)
            t7b = later(t7, 0.2, 1)
            say(t7b, OFFICE, "Sorry sir, corrected. Revised invoice attached", "invoice_fix", n)
            say(t7b + timedelta(minutes=1), OFFICE, f"{inv}_rev.pdf (file attached)")
            t7 = later(t7b, 0.5, 4)
            say(t7, OWNER, f"Approved {inv}", "approval", n)
        else:
            say(t7, OWNER, rng.choice([f"{inv} approved. Dispatch karo", f"Approved {inv}",
                                    f"{short} ji wala invoice theek hai, dispatch karo"]), "approval", n)

        # Dispatch
        t8 = later(t7, 0.1, 0.6)
        say(t8, OFFICE, f"Suresh {short} ji ka dispatch ready karo, transporter ko call karo",
            "dispatch_request", n)
        say(later(t8, 0.5, 2.5), GODOWN,
            rng.choice(["Transporter kal subah aayega", "Transporter shaam ko aayega",
                        "Transporter ko bol diya madam"]))
        t10 = later(t8, 3, 12)
        say(t10, GODOWN, f"{short} ji ka dispatch ho gaya. LR attached", "dispatched", n)
        say(t10 + timedelta(minutes=1), GODOWN, "<Media omitted>")
        t11 = later(t10, 0.2, 1.5)
        say(t11, OFFICE, f"LR number Tally mein update kar diya, {short} ji ko bata diya", "tally_update", n)

        # Payment: 7-30 calendar days after dispatch, slow payers get chased
        pay_days = rng.uniform(7, 30)
        t_pay = add_working_hours((t10 + timedelta(days=pay_days)).replace(hour=9, minute=0), rng.uniform(0.3, 9))
        if pay_days > 15:
            t_chase = add_working_hours(t10 + timedelta(days=12), rng.uniform(0, 3))
            if t_chase < END:
                say(t_chase, OWNER, f"{short} ji ka payment aaya kya? kab tak aayega", "payment_chase", n)
                say(later(t_chase, 0.2, 2), OFFICE, "Nahi sir, abhi tak nahi aaya. Reminder bhej diya")
        if t_pay < END:
            amount = avail * rate
            say(t_pay, OFFICE, f"{short} ji ka payment aa gaya - Rs {amount:,} "
                               f"({rng.choice(['UPI', 'NEFT', 'cheque'])})", "payment_received", n)

    # A little everyday noise
    for _ in range(10):
        t = add_working_hours(START, rng.uniform(0, 470))
        say(t, rng.choice([OWNER, OFFICE, GODOWN]), rng.choice([
            "Kal godown 11 baje khulega", "Aaj bijli nahi hai, Tally thoda late update hoga",
            "Transporter ka rate badh gaya hai", "Sab log kal 10 baje meeting mein aana",
            "Stock register update kar diya"]))

    msgs.sort(key=lambda m: m[0])
    return msgs


def main() -> None:
    msgs = generate()
    header = [f"{fmt(START)} - Messages and calls are end-to-end encrypted. "
              "No one outside of this chat can read or listen to them.",
              f'{fmt(START)} - {OWNER} created group "Orders - Sharma Traders"']
    lines = header + [f"{fmt(t)} - {who}: {text}" for t, who, text, _, _ in msgs]
    out = Path(__file__).resolve().parents[1] / "sample_data" / "sharma_traders_2months.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    orders = len({o for *_, o in msgs if o})
    print(f"Wrote {out.name}: {len(msgs)} messages, {orders} orders")


if __name__ == "__main__":
    main()
