"""Job Agent dashboard. Run with: python -m jobagent dashboard  (or: python -m jobagent demo)"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ui  # noqa: E402
from jobagent import db  # noqa: E402
from jobagent.config import DATA_DIR, ConfigError, load_settings  # noqa: E402

st.set_page_config(page_title="Job Agent", page_icon="💼", layout="wide", initial_sidebar_state="expanded")
st.markdown(ui.CSS, unsafe_allow_html=True)

DEMO = os.environ.get("JOBAGENT_DEMO") == "1"
TRACKED = ["applied", "viewed", "shortlisted", "interview", "offer", "rejected"]
BOARD = ["applied", "viewed", "shortlisted", "interview", "offer", "rejected"]
LOG_FILE = DATA_DIR / "agent.log"

# A fresh connection per run: Streamlit reruns can happen on different threads.
conn = db.connect()

try:
    settings = load_settings()
    config_error = ""
except ConfigError as exc:
    settings = None
    config_error = str(exc)


# ------------------------------------------------------------------ data
def load_jobs() -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT id, platform, title, company, location, score, status, match_reasons, url, "
        "discovered_at, applied_at, status_updated_at FROM jobs ORDER BY status_updated_at DESC",
        conn,
    )
    for col in ("discovered_at", "applied_at", "status_updated_at"):
        df[col] = pd.to_datetime(df[col], errors="coerce", utc=True).dt.tz_convert(None)
    return df


def last_notes(ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT job_id, note FROM events WHERE id IN (SELECT MAX(id) FROM events WHERE job_id IN ({marks}) GROUP BY job_id)",
        ids,
    ).fetchall()
    return {r["job_id"]: r["note"] for r in rows}


def run_agent(*args: str) -> None:
    if DEMO:
        st.toast("Demo mode: agents are disabled.", icon="🧪")
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        subprocess.Popen([sys.executable, "-m", "jobagent", *args], cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
    st.toast(f"Started: {' '.join(args)}", icon="🚀")


def set_many(ids: list[int], status: str, note: str) -> None:
    for job_id in ids:
        db.set_status(conn, int(job_id), status, note)


def utc_naive(dt: datetime) -> pd.Timestamp:
    return pd.Timestamp(dt.astimezone(timezone.utc).replace(tzinfo=None))


all_jobs = load_jobs()

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown(
        '<div class="side-brand"><div class="logo">💼</div><div><div class="name">Job Agent</div>'
        f'<div class="tag">{"Demo data" if DEMO else "Rule-based autopilot"}</div></div></div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="side-label">Run agents</div>', unsafe_allow_html=True)
    if st.button("🚀  Apply to approved jobs", type="primary", use_container_width=True):
        run_agent("apply")
    c1, c2 = st.columns(2)
    if c1.button("🔎 Discover", use_container_width=True):
        run_agent("discover")
    if c2.button("📧 Track", use_container_width=True):
        run_agent("track")
    c3, c4 = st.columns(2)
    if c3.button("🧪 Dry run", use_container_width=True):
        run_agent("apply", "--dry-run", "--limit", "3")
    if c4.button("♻️ Re-score", use_container_width=True):
        run_agent("match", "--rescore")
    if st.button("🔄  Refresh data", use_container_width=True):
        st.rerun()

    st.markdown('<div class="side-label">Filter</div>', unsafe_allow_html=True)
    platforms = sorted(all_jobs["platform"].unique()) if not all_jobs.empty else []
    labels = {p: ui.PLATFORM_META.get(p, (p.title(), ""))[0] for p in platforms}
    chosen = st.multiselect("Platforms", platforms, default=platforms, format_func=lambda p: labels[p], label_visibility="collapsed")

    st.markdown('<div class="side-label">Agent log</div>', unsafe_allow_html=True)
    with st.expander("Latest output", expanded=False):
        if LOG_FILE.exists():
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
            st.code("\n".join(lines) or "(empty)", language="text")
        else:
            st.caption("No runs yet.")

jobs = all_jobs[all_jobs["platform"].isin(chosen)] if chosen else all_jobs

# ------------------------------------------------------------------ numbers
counts = jobs["status"].value_counts().to_dict() if not jobs.empty else {}
today_start = utc_naive(datetime.fromisoformat(db.start_of_local_day_utc()))
week_start = utc_naive(datetime.now(timezone.utc) - timedelta(days=7))
applied_mask = jobs["applied_at"].notna() if not jobs.empty else pd.Series(dtype=bool)
applied_total = int(applied_mask.sum()) if not jobs.empty else 0
applied_today = int((jobs["applied_at"] >= today_start).sum()) if not jobs.empty else 0
applied_week = int((jobs["applied_at"] >= week_start).sum()) if not jobs.empty else 0
found_week = int((jobs["discovered_at"] >= week_start).sum()) if not jobs.empty else 0
responded = int(jobs[applied_mask & jobs["status"].isin(["viewed", "shortlisted", "interview", "offer"])].shape[0]) if applied_total else 0
response_rate = f"{responded / applied_total:.0%}" if applied_total else "—"
daily_goal = int(settings.limits.get("daily_max", 25)) if settings else 25

# ------------------------------------------------------------------ header
name = settings.personal.get("first_name", "") if settings else ""
last_event = conn.execute("SELECT MAX(created_at) FROM events").fetchone()[0]
subtitle = f"Last activity {ui.ago(last_event)}" if last_event else "No activity yet"
if settings:
    subtitle += f" · {'Review mode on' if settings.review_mode else 'Auto-apply on'}"
st.markdown(ui.hero(f"Welcome back{', ' + name if name else ''} 👋", subtitle, applied_today, daily_goal), unsafe_allow_html=True)

if config_error and not DEMO:
    st.warning(f"{config_error}  Agents can't run until the config is set up.", icon="⚙️")

st.markdown(
    ui.kpi_grid([
        ("Openings found", len(jobs), f"<b>+{found_week}</b> this week", "🔎", "#6366F1"),
        ("To review", counts.get("matched", 0), "waiting for approval", "✅", "#8B5CF6"),
        ("Applied", applied_total, f"<b>+{applied_week}</b> this week", "📨", "#3B82F6"),
        ("Shortlisted", counts.get("shortlisted", 0), f"{counts.get('viewed', 0)} viewed", "⭐", "#10B981"),
        ("Interviews", counts.get("interview", 0), f"{counts.get('offer', 0)} offers", "🎯", "#14B8A6"),
        ("Response rate", response_rate, f"{counts.get('needs_attention', 0)} need attention", "📈", "#F59E0B"),
    ]),
    unsafe_allow_html=True,
)

tabs = st.tabs(["📊 Overview", "🗂️ Pipeline", "✅ Review", "⚠️ Attention", "📬 Applications", "🔍 Explore", "⚙️ Settings"])

# ------------------------------------------------------------------ overview
with tabs[0]:
    if jobs.empty:
        st.markdown(ui.empty_state("🛰️", "No jobs yet", "Fill in config/profile.toml, then click Discover in the sidebar."), unsafe_allow_html=True)
    else:
        left, right = st.columns([3, 2], gap="large")
        with left:
            with st.container(border=True):
                st.markdown(ui.section("Applications over time", "last 30 days"), unsafe_allow_html=True)
                since = utc_naive(datetime.now(timezone.utc) - timedelta(days=30))
                applied = jobs[jobs["applied_at"] >= since].copy()
                if applied.empty:
                    st.caption("Nothing applied in the last 30 days.")
                else:
                    applied["day"] = applied["applied_at"].dt.floor("D")
                    per_day = applied.groupby(["day", "platform"]).size().reset_index(name="applications")
                    per_day["Platform"] = per_day["platform"].map(lambda p: ui.PLATFORM_META.get(p, (p, ""))[0])
                    domain = sorted(per_day["Platform"].unique())
                    rng = [next((c for k, (lbl, c) in ui.PLATFORM_META.items() if lbl == d), "#94A3B8") for d in domain]
                    chart = (
                        alt.Chart(per_day)
                        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                        .encode(
                            x=alt.X("day:T", title=None, axis=alt.Axis(format="%d %b", grid=False)),
                            y=alt.Y("sum(applications):Q", title=None),
                            color=alt.Color("Platform:N", scale=alt.Scale(domain=domain, range=rng), legend=alt.Legend(orient="bottom", title=None)),
                            tooltip=["day:T", "Platform:N", "applications:Q"],
                        )
                        .properties(height=260)
                        .configure_view(strokeWidth=0)
                    )
                    st.altair_chart(chart, use_container_width=True)

            with st.container(border=True):
                st.markdown(ui.section("Funnel", "where every job is right now"), unsafe_allow_html=True)
                order = [s for s in db.STATUSES if counts.get(s)]
                funnel = pd.DataFrame({
                    "status": order,
                    "Status": [ui.STATUS_META[s][0] for s in order],
                    "jobs": [counts[s] for s in order],
                })
                bars = (
                    alt.Chart(funnel)
                    .mark_bar(cornerRadiusTopRight=6, cornerRadiusBottomRight=6, height=18)
                    .encode(
                        y=alt.Y("Status:N", sort=[ui.STATUS_META[s][0] for s in order], title=None, axis=alt.Axis(labelFontWeight="bold")),
                        x=alt.X("jobs:Q", title=None, axis=alt.Axis(grid=False, labels=False, ticks=False, domain=False)),
                        color=alt.Color("status:N", scale=alt.Scale(domain=order, range=[ui.STATUS_CHART_COLOR[s] for s in order]), legend=None),
                        tooltip=["Status:N", "jobs:Q"],
                    )
                )
                text = bars.mark_text(align="left", dx=6, fontWeight="bold", color="#334155").encode(text="jobs:Q")
                st.altair_chart((bars + text).properties(height=max(160, 30 * len(order))).configure_view(strokeWidth=0), use_container_width=True)

        with right:
            with st.container(border=True):
                st.markdown(ui.section("By platform", "all openings"), unsafe_allow_html=True)
                by_platform = jobs.groupby("platform").size().reset_index(name="jobs")
                by_platform["Platform"] = by_platform["platform"].map(lambda p: ui.PLATFORM_META.get(p, (p, ""))[0])
                donut = (
                    alt.Chart(by_platform)
                    .mark_arc(innerRadius=62, cornerRadius=4, padAngle=0.015)
                    .encode(
                        theta="jobs:Q",
                        color=alt.Color(
                            "Platform:N",
                            scale=alt.Scale(
                                domain=list(by_platform["Platform"]),
                                range=[ui.PLATFORM_META.get(p, ("", "#94A3B8"))[1] for p in by_platform["platform"]],
                            ),
                            legend=alt.Legend(orient="right", title=None),
                        ),
                        tooltip=["Platform:N", "jobs:Q"],
                    )
                    .properties(height=230)
                    .configure_view(strokeWidth=0)
                )
                st.altair_chart(donut, use_container_width=True)

            with st.container(border=True):
                st.markdown(ui.section("Recent updates", "from email and agents"), unsafe_allow_html=True)
                ev = pd.read_sql_query(
                    "SELECT e.created_at, e.status, e.note, j.platform, j.title, j.company FROM events e JOIN jobs j ON j.id = e.job_id "
                    "WHERE e.status IN ('applied','viewed','shortlisted','interview','offer','rejected','needs_attention') "
                    "ORDER BY e.id DESC LIMIT 7",
                    conn,
                )
                if ev.empty:
                    st.caption("No updates yet.")
                else:
                    ev["created_at"] = pd.to_datetime(ev["created_at"], utc=True).dt.tz_convert(None)
                    ev = ev[ev["platform"].isin(chosen)] if chosen else ev
                    st.markdown(ui.timeline(ev.to_dict("records")), unsafe_allow_html=True)

# ------------------------------------------------------------------ pipeline board
with tabs[1]:
    st.markdown(ui.section("Application pipeline", "newest first · click a title to open the posting"), unsafe_allow_html=True)
    cols = st.columns(len(BOARD), gap="small")
    for col, status in zip(cols, BOARD, strict=True):
        with col:
            subset = jobs[jobs["status"] == status].sort_values("status_updated_at", ascending=False)
            st.markdown(ui.kanban_column(status, subset.to_dict("records")), unsafe_allow_html=True)

# ------------------------------------------------------------------ review queue
with tabs[2]:
    queue = jobs[jobs["status"] == "matched"].sort_values("score", ascending=False)
    if queue.empty:
        st.markdown(ui.empty_state("🎉", "You're all caught up", "New matches show up here when review_mode = true."), unsafe_allow_html=True)
    else:
        top = st.columns([2, 2, 3])
        threshold = top[0].slider("Approve everything scoring at least", 0, 100, 75, step=5)
        above = queue[queue["score"] >= threshold]
        top[1].markdown("<div style='height:1.8rem'></div>", unsafe_allow_html=True)
        if top[1].button(f"✅ Approve {len(above)} jobs ≥ {threshold}", type="primary", disabled=above.empty):
            set_many(list(above["id"]), "approved", f"Bulk approved (score ≥ {threshold})")
            st.rerun()
        top[2].markdown(
            f"<div style='height:1.8rem'></div><span class='m'>{len(queue)} jobs waiting · "
            f"average score {queue['score'].mean():.0f}</span>",
            unsafe_allow_html=True,
        )

        show = st.select_slider("Show", options=[10, 25, 50, 100], value=25, key="review_show")
        for _, r in queue.head(show).iterrows():
            card, actions = st.columns([6, 1], vertical_alignment="center")
            card.markdown(ui.job_card(r, show_score=True), unsafe_allow_html=True)
            if actions.button("Approve", key=f"ap{r['id']}", type="primary", use_container_width=True):
                db.set_status(conn, int(r["id"]), "approved", "Approved in dashboard")
                st.rerun()
            if actions.button("Skip", key=f"sk{r['id']}", use_container_width=True):
                db.set_status(conn, int(r["id"]), "skipped", "Skipped in dashboard")
                st.rerun()

    approved = jobs[jobs["status"] == "approved"]
    if not approved.empty:
        with st.expander(f"🕒 Approved, waiting for the next Apply run ({len(approved)})"):
            for _, r in approved.head(30).iterrows():
                st.markdown(ui.job_card(r, show_status=False), unsafe_allow_html=True)

# ------------------------------------------------------------------ needs attention
with tabs[3]:
    attention = jobs[jobs["status"].isin(["needs_attention", "failed"])]
    if attention.empty:
        st.markdown(ui.empty_state("✨", "Nothing needs you", "The agent stops here when it hits a CAPTCHA, an unknown question or an external site."), unsafe_allow_html=True)
    else:
        st.caption(
            "Open the job, finish it by hand, then click **I applied**. If the same question keeps coming up, "
            "add an [[answers.custom]] rule to the config and click **Retry**."
        )
        notes = last_notes([int(i) for i in attention["id"]])
        for _, r in attention.iterrows():
            card, actions = st.columns([6, 1], vertical_alignment="center")
            card.markdown(ui.job_card(r, note=notes.get(int(r["id"]), "")), unsafe_allow_html=True)
            if actions.button("I applied", key=f"done{r['id']}", type="primary", use_container_width=True):
                db.set_status(conn, int(r["id"]), "applied", "Applied manually")
                st.rerun()
            a, b = actions.columns(2)
            if a.button("↻", key=f"retry{r['id']}", help="Retry", use_container_width=True):
                db.set_status(conn, int(r["id"]), "approved", "Retry from dashboard")
                st.rerun()
            if b.button("✕", key=f"skip{r['id']}", help="Skip", use_container_width=True):
                db.set_status(conn, int(r["id"]), "skipped", "Skipped in dashboard")
                st.rerun()

# ------------------------------------------------------------------ applications
with tabs[4]:
    apps = jobs[jobs["status"].isin(TRACKED)].sort_values("applied_at", ascending=False)
    if apps.empty:
        st.markdown(ui.empty_state("📭", "No applications yet", "Approved jobs are applied to when you click Apply."), unsafe_allow_html=True)
    else:
        f1, f2 = st.columns([3, 2])
        filt = f1.multiselect("Status", TRACKED, default=TRACKED, format_func=lambda s: ui.STATUS_META[s][0], key="app_status")
        query = f2.text_input("Search", placeholder="Company or title", key="app_q")
        apps = apps[apps["status"].isin(filt)]
        if query:
            apps = apps[apps["title"].str.contains(query, case=False, na=False) | apps["company"].str.contains(query, case=False, na=False)]
        view = apps[["id", "status", "platform", "title", "company", "location", "applied_at", "status_updated_at", "url"]]
        edited = st.data_editor(
            view,
            hide_index=True,
            use_container_width=True,
            height=min(600, 38 * (len(view) + 1) + 4),
            disabled=[c for c in view.columns if c != "status"],
            column_config={
                "id": None,
                "status": st.column_config.SelectboxColumn("Status", options=TRACKED, required=True, width="small"),
                "platform": st.column_config.TextColumn("Platform", width="small"),
                "title": st.column_config.TextColumn("Role", width="large"),
                "company": "Company",
                "location": "Location",
                "applied_at": st.column_config.DatetimeColumn("Applied", format="DD MMM, HH:mm"),
                "status_updated_at": st.column_config.DatetimeColumn("Updated", format="DD MMM, HH:mm"),
                "url": st.column_config.LinkColumn("Link", display_text="Open ↗", width="small"),
            },
            key="apps_editor",
        )
        changed = edited[edited["status"] != view["status"]]
        if not changed.empty:
            st.info(f"{len(changed)} status change(s) not saved yet.")
            if st.button("💾 Save changes", type="primary"):
                for _, r in changed.iterrows():
                    db.set_status(conn, int(r["id"]), r["status"], "Changed in dashboard")
                st.rerun()

# ------------------------------------------------------------------ explore
with tabs[5]:
    e1, e2, e3 = st.columns([3, 2, 2])
    q = e1.text_input("Search all openings", placeholder="e.g. python, Adobe, Bengaluru", key="all_q")
    status_filter = e2.multiselect("Status", db.STATUSES, format_func=lambda s: ui.STATUS_META[s][0], key="all_status")
    min_score = e3.slider("Minimum score", 0, 100, 0, step=5, key="all_score")
    view = jobs
    if q:
        hay = view["title"].fillna("") + " " + view["company"].fillna("") + " " + view["location"].fillna("")
        view = view[hay.str.contains(q, case=False, regex=False)]
    if status_filter:
        view = view[view["status"].isin(status_filter)]
    if min_score:
        view = view[view["score"].fillna(0) >= min_score]
    st.caption(f"{len(view)} openings")
    st.dataframe(
        view[["status", "score", "platform", "title", "company", "location", "match_reasons", "discovered_at", "url"]],
        hide_index=True,
        use_container_width=True,
        height=560,
        column_config={
            "status": st.column_config.TextColumn("Status", width="small"),
            "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f", width="small"),
            "platform": st.column_config.TextColumn("Platform", width="small"),
            "title": st.column_config.TextColumn("Role", width="large"),
            "company": "Company",
            "location": "Location",
            "match_reasons": st.column_config.TextColumn("Why", width="large"),
            "discovered_at": st.column_config.DatetimeColumn("Found", format="DD MMM"),
            "url": st.column_config.LinkColumn("Link", display_text="Open ↗", width="small"),
        },
    )

# ------------------------------------------------------------------ settings
with tabs[6]:
    if not settings:
        st.markdown(ui.empty_state("⚙️", "No config yet", "Run: python -m jobagent init, then edit config/profile.toml."), unsafe_allow_html=True)
    else:
        p, lim = settings.preferences, settings.limits
        s1, s2, s3 = st.columns(3, gap="large")
        with s1, st.container(border=True):
            st.markdown(ui.section("What you're looking for"), unsafe_allow_html=True)
            st.markdown("**Roles**<br>" + "".join(f'<span class="chip">{ui.esc(r)}</span>' for r in p.get("roles", [])), unsafe_allow_html=True)
            st.markdown("**Locations**<br>" + "".join(f'<span class="chip">{ui.esc(r)}</span>' for r in p.get("locations", [])), unsafe_allow_html=True)
            st.markdown("**Must have**<br>" + "".join(f'<span class="chip">{ui.esc(r)}</span>' for r in p.get("must_have_keywords", [])), unsafe_allow_html=True)
            st.markdown(f"**Experience** {ui.esc(p.get('experience_years', '—'))} years · **Min score** {ui.esc(p.get('min_score', 50))}")
        with s2, st.container(border=True):
            st.markdown(ui.section("Safety limits"), unsafe_allow_html=True)
            st.metric("Daily maximum", lim.get("daily_max", 25))
            st.metric("Per platform per day", lim.get("per_platform_daily_max", 10))
            st.caption(f"Wait {lim.get('min_delay_seconds', 25)}–{lim.get('max_delay_seconds', 70)}s between applications · "
                       f"{'Review mode' if settings.review_mode else 'Auto-apply'}")
        with s3, st.container(border=True):
            st.markdown(ui.section("Platforms enabled"), unsafe_allow_html=True)
            st.markdown("<br>".join(ui.platform_chip(x) for x in settings.enabled_platforms), unsafe_allow_html=True)
        st.caption(f"Edit these in `{settings.path}`, then click ♻️ Re-score in the sidebar.")
