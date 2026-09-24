"""Process X-Ray - Streamlit app.

Run locally:  streamlit run app.py
"""
import base64
import html
import io
import os
import re
import threading
import zipfile
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from process_agent import llm
from process_agent.pipeline import Report, run
from process_agent.recommender import Settings
from process_agent.word_report import build_docx

ROOT = Path(__file__).parent
DEMO_REPORT = ROOT / "demo" / "sharma_traders_2months_report.json"
SAMPLE_CHAT = ROOT / "sample_data" / "sharma_traders_orders.txt"
FRIENDLY_STAGES = {1: "Understanding how your team works",
                   2: "Following each order through the chat",
                   3: "Finding where time is lost",
                   4: "Designing automations and replaying your history",
                   5: "Double-checking every idea"}
LOCAL_MODE = (ROOT / ".env").exists()      # keys from .env are used only on your own machine
MAX_MESSAGES = 3000
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

LABELS = {
    "eliminate": "Eliminate", "simplify": "Simplify", "integrate": "Integrate",
    "automate_rules": "Automate with rules", "automate_ai": "Automate with AI", "keep_human": "Keep human",
}
VERDICTS = {"go": ("Go", "go"), "go_with_changes": ("Go, with changes", "changes"),
            "rethink": ("Rethink", "rethink")}
PAYMENT_WORDS = ("payment", "paid", "chase", "remind")
e = html.escape

st.set_page_config(page_title="Process X-Ray", page_icon="🩻", layout="wide")

# ---------------- visual identity: the trader's ledger ----------------
# Ledger paper, ruled hairlines, blue-black ink, and the red margin line of a
# bahi-khata. Everything is quiet except the order timeline at the top.
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Mukta:wght@300;400;500;600;700;800&display=swap');

:root {
  --paper: #F3F5F1; --paper-2: #E9EEE8; --rule: #D6DED6; --ink: #1D2B3A;
  --muted: #66756E; --red: #A3342B; --green: #0F6E56; --ochre: #9A6A12;
}
html, body, .stApp, p, li, label, h1, h2, h3, button, input, textarea, [data-testid="stMarkdownContainer"] {
  font-family: 'Mukta', system-ui, sans-serif !important;
}
[data-testid="stIconMaterial"], .material-symbols-rounded, [class*="material-symbols"] {
  font-family: 'Material Symbols Rounded' !important;
}
.stApp { background: var(--paper); color: var(--ink); }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"], #MainMenu, [data-testid="stDecoration"] { display: none; }
.block-container, [data-testid="stMainBlockContainer"] { max-width: 1120px; padding-top: 2.2rem; }
[data-testid="stSidebar"] { background: var(--paper-2); border-right: 1px solid var(--rule); }

h1, h2, h3 { color: var(--ink); letter-spacing: -0.01em; }
p, li { line-height: 1.6; }

/* Masthead */
.px-mast { display: flex; align-items: baseline; gap: .6rem; margin-bottom: .2rem; }
.px-mark { font-weight: 800; font-size: 1.05rem; color: var(--green); }
.px-mast-note { color: var(--muted); font-size: .95rem; }
.px-title { font-size: 2.6rem; font-weight: 700; line-height: 1.1; margin: .2rem 0 .5rem; max-width: 22ch; }
.px-lede { font-size: 1.15rem; color: var(--muted); max-width: 62ch; margin-bottom: 1.4rem; }

/* Ledger block: ruled paper with the red double margin line */
.px-ledger {
  position: relative; background: #FBFCFA; border: 1px solid var(--rule); border-radius: 3px;
  padding: 1.4rem 1.6rem 1.3rem 3.4rem; margin: .4rem 0 1.2rem;
  background-image: repeating-linear-gradient(to bottom, transparent 0, transparent 31px, #E7ECE6 31px, #E7ECE6 32px);
}
.px-ledger::before, .px-ledger::after {
  content: ""; position: absolute; top: 0; bottom: 0; width: 1px; background: var(--red); opacity: .75;
}
.px-ledger::before { left: 2.1rem; } .px-ledger::after { left: 2.35rem; }

/* Hero timeline */
.px-hero-line { font-size: 1.6rem; font-weight: 600; line-height: 1.25; margin: 0 0 1.1rem; max-width: 34ch; }
.px-hero-line b { color: var(--green); font-weight: 800; }
.px-lane { display: grid; grid-template-columns: 9.5rem 1fr 5.5rem; align-items: center; gap: .8rem; margin: .7rem 0; }
.px-lane-name { font-weight: 600; font-size: .98rem; }
.px-lane-name small { display: block; color: var(--muted); font-weight: 400; font-size: .8rem; }
.px-track { position: relative; height: 26px; }
.px-bar { position: absolute; left: 0; top: 11px; height: 4px; border-radius: 2px; }
.px-bar.today { background: var(--red); opacity: .85; }
.px-bar.after { background: var(--green); }
.px-dot { position: absolute; top: 6px; width: 14px; height: 14px; margin-left: -7px; border-radius: 50%;
  background: #FBFCFA; border: 2px solid currentColor; }
.px-dot.today { color: var(--red); } .px-dot.after { color: var(--green); }
.px-dot.end { background: currentColor; }
.px-end { font-weight: 700; font-size: 1.05rem; text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
.px-axis { display: grid; grid-template-columns: 9.5rem 1fr 5.5rem; gap: .8rem; color: var(--muted); font-size: .85rem; }
.px-steps { display: flex; justify-content: space-between; gap: .6rem; flex-wrap: wrap; }

/* Facts under the hero, set as a ledger line rather than cards */
.px-facts { display: flex; flex-wrap: wrap; gap: 0; border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule); margin: .2rem 0 1.4rem; }
.px-fact { flex: 1 1 12rem; padding: .8rem 1.1rem; border-right: 1px solid var(--rule); }
.px-fact:last-child { border-right: 0; }
.px-fact strong { display: block; font-size: 1.55rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.px-fact span { color: var(--muted); font-size: .95rem; }

/* Recommendation entries */
.px-entry { position: relative; padding: 1.1rem 0 .4rem 3.4rem; border-top: 1px solid var(--rule); }
.px-rank { position: absolute; left: 0; top: 1rem; width: 2.2rem; font-size: 1.7rem; font-weight: 800;
  color: var(--red); text-align: right; font-variant-numeric: tabular-nums; }
.px-entry h3 { margin: 0 0 .3rem; font-size: 1.3rem; font-weight: 700; }
.px-verdict { display: inline-block; padding: .05rem .6rem; border-radius: 999px; font-size: .88rem; font-weight: 600; margin-right: .5rem; }
.px-verdict.go { background: #DCEFE7; color: #0B5642; }
.px-verdict.changes { background: #F4E8CF; color: #6E4B0C; }
.px-verdict.rethink { background: #F4DAD7; color: #7C241D; }
.px-conf { color: var(--muted); font-size: .9rem; }
.px-conf i { font-style: normal; letter-spacing: .1em; color: var(--ink); }
.px-numbers { display: flex; flex-wrap: wrap; gap: 1.6rem; margin: .7rem 0 .2rem; }
.px-numbers div { font-size: .92rem; color: var(--muted); }
.px-numbers b { display: block; color: var(--ink); font-size: 1.15rem; font-variant-numeric: tabular-nums; }
.px-note { color: var(--muted); font-size: .9rem; }

/* Pull quote for the main insight */
.px-quote { border-left: 3px solid var(--red); padding: .2rem 0 .2rem 1rem; font-size: 1.15rem; line-height: 1.5; margin: .4rem 0 1.2rem; max-width: 70ch; }

/* Streamlit widgets in the ledger palette */
.stTabs [data-baseweb="tab-list"] { gap: 1.4rem; border-bottom: 1px solid var(--rule); }
.stTabs [data-baseweb="tab"] { font-size: 1rem; padding: .4rem 0; }
.stTabs [aria-selected="true"] { color: var(--green) !important; }
.stTabs [data-baseweb="tab-highlight"] { background: var(--green); }
.stButton button[kind="primary"], .stDownloadButton button {
  border-radius: 4px; font-weight: 600;
}
.stDownloadButton button { background: var(--ink); color: #fff; border: 0; }
.stDownloadButton button:hover { background: var(--green); color: #fff; }
[data-testid="stExpander"] details { border: 1px solid var(--rule); border-radius: 3px; background: #FBFCFA; }
button:focus-visible, input:focus-visible { outline: 2px solid var(--green) !important; outline-offset: 2px; }

@media (max-width: 640px) {
  .px-title { font-size: 2rem; }
  .px-mast { flex-direction: column; gap: 0; }
  .px-hero-line { font-size: 1.3rem; }
  .px-lane { grid-template-columns: 1fr 3.6rem; grid-template-areas: "name name" "track end"; row-gap: .2rem; }
  .px-lane-name { grid-area: name; } .px-lane-name small { display: inline; margin-left: .4rem; }
  .px-track { grid-area: track; } .px-end { grid-area: end; }
  .px-axis { display: none; }
  .px-lane-name small { display: none; }
  .px-dot { width: 10px; height: 10px; margin-left: -5px; top: 8px; }
  .px-entry { padding-left: 2.6rem; } .px-rank { width: 1.7rem; font-size: 1.4rem; }
  .px-ledger { padding-left: 2.8rem; }
  .px-ledger::before { left: 1.6rem; } .px-ledger::after { left: 1.85rem; }
}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------- helpers ----------------

def is_payment(name: str) -> bool:
    return any(w in name.lower() for w in PAYMENT_WORDS)


def headline_milestone(r: Report):
    """The milestone that matters most to an owner: dispatch or delivery if the
    process has one, otherwise the step reached the most hours sooner."""
    need = min(3, max(1, r.replay.cases // 2))
    common = [m for m in r.replay.milestones if m.cases >= need and not is_payment(m.step_name)]
    gain = lambda m: m.avg_hours_before - m.avg_hours_after
    common = [m for m in common if gain(m) >= 0.5]
    # Goods leaving is what owners and customers feel: dispatch, loading, LR, delivery.
    # Among those, take the latest in the process (not e.g. a "dispatch decision").
    leaving = [m for m in common if any(w in m.step_name.lower() for w in
               ("dispatch", "deliver", "ship", "load", "lr", "courier", "transport"))
               and not any(w in m.step_name.lower() for w in ("decision", "decide", "quantity", "request",
                                                                "record", "update", "email", "notify", "inform"))]
    if leaving:
        return max(leaving, key=lambda m: m.avg_hours_before)
    return max(common, key=gain, default=None)


def _horizontal(code: str) -> str:
    """Lay the flowchart out left to right, which suits a wide screen."""
    return re.sub(r"^(\s*)(flowchart|graph)\s+(TD|TB)\b", r"\1\2 LR", code, count=1, flags=re.M)


def render_mermaid(code: str) -> None:
    """Draw the flowchart. The Mermaid code comes from an AI model, so it is
    HTML-escaped and rendered from a data: URL, which runs in an isolated
    origin with no access to this app, even if the text were malicious."""
    page = f"""<!doctype html><html style="background:#F3F5F1;color-scheme:light"><body style="margin:0;padding:8px 2px 14px;background:#F3F5F1;font-family:sans-serif;overflow-x:auto;overflow-y:hidden">
<pre class="mermaid">{html.escape(_horizontal(code))}</pre>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({{ startOnLoad: true, securityLevel: "strict", theme: "base",
    flowchart: {{ useMaxWidth: false, nodeSpacing: 30, rankSpacing: 40 }},
    themeVariables: {{ primaryColor: "#FBFCFA", primaryBorderColor: "#1D2B3A", primaryTextColor: "#1D2B3A",
                       lineColor: "#A3342B", fontFamily: "sans-serif" }} }});
</script></body></html>"""
    # A left-to-right chart is one or two rows tall; branches (decisions) need a little more room
    rows = 1 + len(re.findall(r"\{[^}]*\}", code))
    st.iframe("data:text/html;base64," + base64.b64encode(page.encode()).decode(),
              height=min(420, 120 + 90 * rows))
    st.caption("Scroll sideways to follow the whole process.")


def html_block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


# ---------------- report view ----------------

def render_hero(r: Report) -> None:
    """The one memorable element: an order's journey, today versus with automations."""
    rp = r.replay
    ops = [m for m in rp.milestones if not is_payment(m.step_name)]
    head = headline_milestone(r)
    if not ops or not head:
        return
    last = ops[-1]
    scale = max(last.avg_hours_before, 1)
    pct = lambda h: min(100, 100 * h / scale)

    def lane(kind: str, key: str) -> str:
        dots = "".join(
            f'<span class="px-dot {kind}{" end" if m is last else ""}" style="left:{pct(getattr(m, key)):.1f}%" '
            f'title="{e(m.step_name)}: {getattr(m, key):g} working hours"></span>' for m in ops)
        return (f'<div class="px-track"><div class="px-bar {kind}" style="width:{pct(getattr(last, key)):.1f}%"></div>'
                f'{dots}</div>')

    gain = head.avg_hours_before - head.avg_hours_after
    html_block(f"""
<div class="px-ledger" role="figure" aria-label="Order journey today versus with automations">
  <p class="px-hero-line">Orders reach “{e(head.step_name)}” <b>{gain:.1f} working hours sooner</b>
     with the recommended automations.</p>
  <div class="px-lane"><div class="px-lane-name">Today<small>as it really happened</small></div>
    {lane("today", "avg_hours_before")}<div class="px-end">{last.avg_hours_before:g} h</div></div>
  <div class="px-lane"><div class="px-lane-name">With automations<small>same orders, replayed</small></div>
    {lane("after", "avg_hours_after")}<div class="px-end" style="color:var(--green)">{last.avg_hours_after:g} h</div></div>
  <div class="px-axis"><span></span><div class="px-steps"><span>Order arrives</span>
    <span>{e(last.step_name)}</span></div><span></span></div>
</div>""")

    html_block(f"""
<div class="px-facts">
  <div class="px-fact"><strong>{rp.waiting_removed_per_week:g} h</strong><span>of waiting removed each week</span></div>
  <div class="px-fact"><strong>{rp.chasers_avoided}</strong><span>reminder messages no longer needed</span></div>
  <div class="px-fact"><strong>{rp.cases}</strong><span>orders replayed from the chat</span></div>
</div>""")


def render_report(r: Report, key: str) -> None:
    pm, an, rp = r.process_map, r.analysis, r.replay
    recs = {x.id: x for x in r.plan.recommendations}

    render_hero(r)
    if rp.cases < 5:
        st.caption(f"Based on only {rp.cases} order{'s' if rp.cases != 1 else ''} in this chat. "
                   "A longer export (a few weeks or more) gives more reliable numbers.")
    st.download_button("Download the full report (Word)", build_docx(r),
                       file_name=f"{r.chat.source_name}_process_xray.docx", mime=DOCX_MIME,
                       key=f"docx_{key}")

    tabs = st.tabs(["Summary", "Automations", "Replay", "Process map", "Steps", "Method"])

    with tabs[0]:
        html_block(f'<div class="px-quote">{e(an.top_insight)}</div>')
        st.markdown(f"**{e(pm.process_name)}.** {e(pm.summary)}")
        st.markdown("**Who does what:** " + "; ".join(f"{e(p)} is {e(role)}" for p, role in pm.roles.items()))
        st.markdown(f"**Workload:** {an.total_hands_on_hours_per_week} hours a week of hands-on work, and "
                    f"{an.total_waiting_hours_per_week} hours a week of waiting between steps.")
        if pm.pain_points:
            st.markdown("**Where time is lost**")
            st.markdown("\n".join(f"- {e(p.description)}" for p in pm.pain_points))

    with tabs[1]:
        st.markdown('<p class="px-note">Ranked by value, adjusted for risk. A sceptical second agent '
                    'reviewed every idea before it reached this list.</p>', unsafe_allow_html=True)
        for item in r.ranking:
            rec, c = recs[item.recommendation_id], item.critique
            verdict, cls = VERDICTS[c.verdict]
            dots = "●" * c.confidence + "○" * (5 - c.confidence)
            value_note = ("" if rec.monthly_value_inr > 0 else
                          '<p class="px-note">At this volume the tools cost more than the staff time saved; '
                          'the gain is speed and fewer reminders. Prefer free tools.</p>')
            html_block(f"""
<div class="px-entry">
  <div class="px-rank">{item.rank}</div>
  <h3>{e(rec.title)}</h3>
  <span class="px-verdict {cls}">{verdict}</span><span class="px-conf">Confidence <i>{dots}</i></span>
  <p style="margin:.6rem 0 0;max-width:75ch">{e(rec.what_it_does)}</p>
  <div class="px-numbers">
    <div><b>{rec.waiting_removed_per_week:g} h/week</b>waiting removed</div>
    <div><b>{rec.hours_saved_per_week:g} h/week</b>work saved</div>
    <div><b>{rec.setup_days:g} days</b>to set up, about Rs {rec.setup_cost_inr:,.0f}</div>
    <div><b>Rs {rec.monthly_value_inr:,.0f}</b>net value a month</div>
  </div>{value_note}
</div>""")
            with st.expander("How to build it, and what could go wrong"):
                left, right = st.columns(2)
                with left:
                    st.markdown("**Build steps**")
                    st.markdown("\n".join(f"{n}. {e(s)}" for n, s in enumerate(rec.how_it_works, 1)))
                    st.markdown(f"**Tools:** {e(', '.join(rec.tools))}")
                    st.markdown(f"**A person still:** {e(rec.human_in_the_loop)}")
                with right:
                    st.markdown("**Risks the reviewer found**")
                    for risk in c.risks:
                        st.markdown(f"- **Severity {risk.severity}/5.** {e(risk.risk)}  \n  *Fix:* {e(risk.mitigation)}")
                    st.markdown(f"**Who may resist:** {e(c.who_might_resist)}")
                    if c.change_needed:
                        st.markdown(f"**Change needed:** {e(c.change_needed)}")

    with tabs[2]:
        st.markdown("Your real history, re-run as if these automations had existed. Only waits an "
                    "automation directly controls are shortened. Customers, transporters and physical "
                    "work keep their real timing, and ideas marked *Rethink* are left out.")
        ops = [x for x in rp.milestones if not is_payment(x.step_name)]
        if ops:
            order = [f"{i}. {x.step_name}" for i, x in enumerate(ops, 1)]
            rows = [{"Step": order[i], "When": when, "Hours": h}
                    for i, x in enumerate(ops)
                    for when, h in (("Today", x.avg_hours_before), ("With automations", x.avg_hours_after))]
            chart = alt.Chart(pd.DataFrame(rows)).mark_bar(cornerRadiusEnd=2).encode(
                y=alt.Y("Step:N", sort=order, title=None, axis=alt.Axis(labelLimit=320, labelFontSize=12)),
                yOffset=alt.YOffset("When:N", sort=["Today", "With automations"]),
                x=alt.X("Hours:Q", title="Working hours after the order arrives"),
                color=alt.Color("When:N", sort=["Today", "With automations"],
                                scale=alt.Scale(range=["#A3342B", "#0F6E56"]),
                                legend=alt.Legend(orient="top", title=None)),
                tooltip=["Step", "When", "Hours"],
            ).properties(height=max(260, 56 * len(ops))).configure(background="transparent", font="Mukta, system-ui, sans-serif").configure_axis(labelColor="#1D2B3A", titleColor="#66756E", gridColor="#E1E7E0", domainColor="#D6DED6")
            st.altair_chart(chart, width="stretch")
        for x in rp.milestones:
            if is_payment(x.step_name):
                st.caption(f"{x.step_name}: {x.avg_hours_before:g} h, then {x.avg_hours_after:g} h. "
                           "Customers still pay on their own schedule; this only reflects invoices "
                           "and dispatch happening sooner.")
        if rp.moments:
            st.markdown("**Moments that would have gone differently**")
            st.markdown("\n".join(
                f"- {e(mo.case_label)}: *{e(mo.step_name)}* on **{mo.after:%a %d %b, %H:%M}** "
                f"instead of {mo.before:%a %d %b, %H:%M}, {mo.hours_sooner:g} working hours sooner"
                for mo in rp.moments))
        with st.expander("Every order, before and after"):
            st.dataframe(pd.DataFrame([{
                "Order": c.label, "Finished (real)": c.end_before, "Finished (replayed)": c.end_after,
                "Working hours saved": c.working_hours_saved} for c in rp.case_results]),
                hide_index=True, width="stretch")

    with tabs[3]:
        render_mermaid(pm.mermaid)

    with tabs[4]:
        st.dataframe(pd.DataFrame([{
            "Step": f"{s.id}. {s.name}", "Who": s.actor, "Tool": s.system,
            "Per week": s.frequency_per_week, "Minutes (est.)": s.minutes_per_run,
            "Wait before (h)": s.wait_before_hours, "Treatment": LABELS[s.treatment],
            "Potential /5": s.potential, "Why": s.reason} for s in an.steps]),
            hide_index=True, width="stretch")
        st.caption("Frequencies and waits are measured from real timestamps in working hours "
                   "(Monday to Saturday, 9:00 to 19:00). Minutes per step are estimates.")

    with tabs[5]:
        s = r.plan.settings
        st.markdown(f"""
- Staff time is valued at **Rs {s.staff_cost_per_hour_inr:g} an hour**, and the owner's time at **Rs {s.owner_cost_per_hour_inr:g} an hour**.
- Building an automation costs about **Rs {s.setup_cost_per_day_inr:,.0f} a day**.
- Each reminder message costs about **{s.minutes_per_chaser:g} minutes** of someone's time.
- Chat analysed: **{r.chat.total_messages} messages**, {r.chat.start:%d %b %Y} to {r.chat.end:%d %b %Y}.
- AI answers pass through code checks: waits are measured rather than guessed, steps that depend on
  customers or transporters can't be sped up, and names are masked before any AI call.
""")
        st.download_button("Download the raw data (JSON)", r.model_dump_json(indent=2),
                           file_name=f"{r.chat.source_name}_report.json", mime="application/json",
                           key=f"json_{key}")


# ---------------- upload flow ----------------

def read_upload(file) -> str:
    """Accept the .txt export, or the .zip that iPhones produce."""
    data = file.getvalue()
    if file.name.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            txts = [n for n in z.namelist() if n.lower().endswith(".txt")]
            if not txts:
                raise ValueError("The zip has no .txt chat file inside.")
            data = z.read(txts[0])
    return data.decode("utf-8", errors="replace")


# Free analyses on the app's own keys (set as secrets on Streamlit Cloud).
# Limits protect the free-tier quota from being used up by a few visitors.
SHARED_RUNS_PER_DAY = int(os.getenv("SHARED_RUNS_PER_DAY", "20"))
SHARED_RUNS_PER_VISITOR = int(os.getenv("SHARED_RUNS_PER_VISITOR", "2"))
SHARED_MAX_LINES = 1500


def shared_keys_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GROQ_API_KEY"))


@st.cache_resource
def _shared_usage() -> dict:
    """One counter for the whole app (all visitors), reset each day."""
    return {"day": date.today(), "count": 0, "lock": threading.Lock()}


def shared_runs_left() -> int:
    u = _shared_usage()
    with u["lock"]:
        if u["day"] != date.today():
            u["day"], u["count"] = date.today(), 0
        return max(0, SHARED_RUNS_PER_DAY - u["count"])


def take_shared_run() -> bool:
    u = _shared_usage()
    with u["lock"]:
        if u["day"] != date.today():
            u["day"], u["count"] = date.today(), 0
        if u["count"] >= SHARED_RUNS_PER_DAY:
            return False
        u["count"] += 1
        return True


def upload_flow() -> None:
    if "report" in st.session_state:
        done = st.session_state["report"]
        c1, c2 = st.columns([3, 1])
        c1.success(f"Analysis complete: {done.chat.total_messages} messages, "
                   f"{done.replay.cases} order{'s' if done.replay.cases != 1 else ''} traced.")
        if c2.button("Analyse another chat", width="stretch"):
            del st.session_state["report"]
            st.rerun()
        render_report(done, key="upload")
        return

    html_block("""
<div class="px-ledger">
  <p style="margin:0 0 .4rem;font-weight:600;font-size:1.1rem">Two steps, about five minutes</p>
  <ol style="margin:0;padding-left:1.2rem">
    <li>In WhatsApp, open your orders group, tap its name, then <b>Export chat</b> and choose <b>Without media</b>.
        Send the file to your computer, or open this page on your phone.</li>
    <li>Upload it below and press <b>Analyse my chat</b>. No sign-up and nothing to install.</li>
  </ol>
</div>""")

    use_sample = st.toggle("No chat to hand? Try it with a short sample chat",
                           help="A fictional distributor's order group: 38 messages over ten days")
    if use_sample:
        file, us_dates = None, False
        with st.expander("See the sample chat"):
            st.code(SAMPLE_CHAT.read_text(encoding="utf-8"), language="text")
    else:
        file = st.file_uploader("Chat export (.txt, or the .zip an iPhone creates)", type=["txt", "zip"])
        us_dates = st.checkbox("My phone shows dates as month/day")

    with st.expander("Use your own AI key (optional)"):
        st.markdown("Only needed if today's free analyses are used up, or if you'd rather use your own "
                    "account. Keys are used for this analysis only and never stored.")
        k1, k2 = st.columns(2)
        gemini = k1.text_input("Gemini key", type="password", help="Free at aistudio.google.com")
        groq = k2.text_input("Groq key", type="password", help="Free at console.groq.com")
    own_key = bool(gemini or groq)

    with st.expander("How your chat is handled"):
        st.markdown("""
- The file is read in memory and never saved. Refreshing the page clears it.
- Before any AI call, people become *Person 1, 2, 3*, customers become *Customer A, B*, and phone numbers and emails are removed.
- The masked messages are sent to Google Gemini or Groq for analysis. These free AI services may use what they receive to improve their products, so upload only chats you're allowed to share.
""")
    with st.expander("Cost assumptions"):
        a, b = st.columns(2)
        staff = a.number_input("Staff time, Rs an hour", 50, 2000, 150, 10)
        owner = b.number_input("Owner's time, Rs an hour", 100, 10000, 500, 50)

    # Which AI access this run will use
    used_by_me = st.session_state.get("shared_runs_used", 0)
    shared_ok = shared_keys_available() and not own_key
    if own_key:
        access_note, can_run = "Your own key will be used.", True
    elif LOCAL_MODE:
        access_note, can_run = "Running on your computer, so the keys in your .env file will be used.", True
    elif not shared_keys_available():
        access_note, can_run = "Free analysis isn't switched on for this app yet. Add your own key above.", False
    elif used_by_me >= SHARED_RUNS_PER_VISITOR:
        access_note, can_run = ("You've used your free analyses for this visit. Add your own key above "
                                "to run more."), False
    elif shared_runs_left() == 0:
        access_note, can_run = ("Today's free analyses are used up. Please come back tomorrow, or add "
                                "your own key above."), False
    else:
        left = min(shared_runs_left(), SHARED_RUNS_PER_VISITOR - used_by_me)
        access_note, can_run = f"Free analysis: {left} left for you today.", True

    consent = use_sample or st.checkbox("I'm allowed to analyse this chat, and I've read how it's handled.")
    missing = [x for x, ok in (("a chat file", file or use_sample), ("your confirmation", consent)) if not ok]
    st.caption(access_note if not missing else
               "To start, add " + " and ".join(missing) + ". " + access_note)
    go = st.button("Analyse my chat", type="primary", disabled=bool(missing) or not can_run)

    if go:
        try:
            text = SAMPLE_CHAT.read_text(encoding="utf-8") if use_sample else read_upload(file)
        except (ValueError, zipfile.BadZipFile) as err:
            st.error(f"This file couldn't be read: {err}")
            return
        chat_name = "sample_chat" if use_sample else Path(file.name).stem
        using_shared = shared_ok and not LOCAL_MODE
        limit = SHARED_MAX_LINES if using_shared else MAX_MESSAGES
        lines = text.splitlines()
        if len(lines) > limit:
            st.info(f"This is a long chat, so the most recent {limit:,} lines will be analysed.")
            text = "\n".join(lines[-limit:])
        if using_shared:
            if not take_shared_run():
                st.error("Today's free analyses were just used up. Please try tomorrow, or add your own key.")
                return
            st.session_state["shared_runs_used"] = used_by_me + 1

        keys = {"gemini": gemini, "groq": groq} if own_key else None   # None = the app's own keys
        with st.status("Analysing your chat. This usually takes two to five minutes.", expanded=True) as status:
            bar = st.progress(0.0, text="Getting started")
            busy_note = st.empty()
            state = {"stage": 0, "busy": False}

            def on_progress(msg: str) -> None:
                """Turn the pipeline's technical progress into plain-language steps."""
                m = re.match(r"\[(\d)/(\d)\]", msg)
                if m:
                    state["stage"] = int(m.group(1))
                    total = int(m.group(2))
                    bar.progress((state["stage"] - 1) / total, text=FRIENDLY_STAGES.get(state["stage"], "Working"))
                    return
                part = re.search(r"\((\d+)/(\d+)\)", msg)
                if part and state["stage"] == 2:
                    i, n = int(part.group(1)), int(part.group(2))
                    bar.progress((1 + (i - 1) / n) / 5,
                                 text=f"{FRIENDLY_STAGES[2]}, part {i} of {n}")

            def on_llm_log(_msg: str) -> None:
                """Model retries and fallbacks stay behind the scenes; show one calm note at most."""
                if not state["busy"]:
                    state["busy"] = True
                    busy_note.caption("The AI service is busy, so this may take a minute longer.")

            llm.use_session(keys, log=on_llm_log)
            try:
                report = run(text=text, name=chat_name, day_first=not us_dates,
                             settings=Settings(staff_cost_per_hour_inr=staff, owner_cost_per_hour_inr=owner),
                             progress=on_progress)
            except ValueError as err:
                status.update(label="This doesn't look like a WhatsApp export", state="error")
                st.error(f"{err} Check the file, or switch the month/day setting and try again.")
                return
            except RuntimeError:
                status.update(label="The AI services are busy right now", state="error")
                st.error("The analysis couldn't finish because the free AI services are busy. "
                         "Please try again in a few minutes" + (", or check your key." if own_key else "."))
                return
            bar.progress(1.0, text="Done")
            busy_note.empty()
            status.update(label="Analysis complete", state="complete", expanded=False)
        st.session_state["report"] = report
        st.rerun()      # show the report in place of the form


# ---------------- page ----------------

with st.sidebar:
    html_block('<div class="px-mark" style="font-size:1.3rem;margin-bottom:.8rem">🩻 Process X-Ray</div>')
    mode = st.radio("View", ["Example business", "Your own chat"], label_visibility="collapsed")
    st.divider()
    st.markdown("""
**How it works**
1. Mask names and read the chat
2. Rebuild the process
3. Score every step
4. Trace every order
5. Design automations
6. Challenge each idea
7. Replay your history
""")

if mode == "Example business":
    demo = (Report.model_validate_json(DEMO_REPORT.read_text(encoding="utf-8"))
            if DEMO_REPORT.exists() else None)
    note = (f"Example: a fictional distributor's WhatsApp order group, {demo.chat.total_messages} "
            f"messages over two months" if demo else "Example report")
    html_block(f"""
<div class="px-mast"><span class="px-mark">Process X-Ray</span><span class="px-mast-note">{e(note)}</span></div>
<div class="px-title">See how your business really runs, from its WhatsApp group</div>
<div class="px-lede">AI agents rebuild the process from the chat, find where time is lost,
propose automations, challenge them, and replay your real history to show what would have changed.</div>""")
    if demo:
        render_report(demo, key="demo")
    else:
        st.info("The example report hasn't been added yet. Run the analysis locally and copy the "
                "report to demo/sharma_traders_2months_report.json.")
else:
    html_block("""
<div class="px-mast"><span class="px-mark">Process X-Ray</span><span class="px-mast-note">Your own chat</span></div>
<div class="px-title">Find out where your orders wait</div>
<div class="px-lede">Upload the WhatsApp group your team uses for orders or requests. You'll get the
process as it really runs, what to automate first, and proof from your own history.</div>""")
    upload_flow()
