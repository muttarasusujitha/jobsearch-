"""Styling and HTML components for the dashboard.

All text that comes from job sites (titles, companies, notes) is passed through
`esc()` before being put into HTML, so a job posting can't inject markup.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

STATUS_META: dict[str, tuple[str, str, str]] = {
    # status: (label, text colour, background)
    "discovered": ("Discovered", "#475569", "#F1F5F9"),
    "matched": ("To review", "#6D28D9", "#EDE9FE"),
    "approved": ("Approved", "#4338CA", "#E0E7FF"),
    "skipped": ("Skipped", "#64748B", "#F1F5F9"),
    "needs_attention": ("Needs attention", "#B45309", "#FEF3C7"),
    "failed": ("Failed", "#B91C1C", "#FEE2E2"),
    "applied": ("Applied", "#1D4ED8", "#DBEAFE"),
    "viewed": ("Viewed", "#0E7490", "#CFFAFE"),
    "shortlisted": ("Shortlisted", "#047857", "#D1FAE5"),
    "interview": ("Interview", "#0F766E", "#CCFBF1"),
    "offer": ("Offer", "#15803D", "#DCFCE7"),
    "rejected": ("Rejected", "#BE123C", "#FFE4E6"),
}
STATUS_CHART_COLOR = {
    "discovered": "#94A3B8", "matched": "#8B5CF6", "approved": "#6366F1", "skipped": "#CBD5E1",
    "needs_attention": "#F59E0B", "failed": "#EF4444", "applied": "#3B82F6", "viewed": "#06B6D4",
    "shortlisted": "#10B981", "interview": "#14B8A6", "offer": "#22C55E", "rejected": "#F43F5E",
}
PLATFORM_META: dict[str, tuple[str, str]] = {
    # platform: (label, brand colour)
    "linkedin": ("LinkedIn", "#0A66C2"),
    "naukri": ("Naukri", "#275DF5"),
    "wellfound": ("Wellfound", "#111827"),
    "greenhouse": ("Greenhouse", "#24A47F"),
    "lever": ("Lever", "#334155"),
    "ashby": ("Ashby", "#5B4FE9"),
    "workday": ("Workday", "#F38B00"),
    "successfactors": ("SuccessFactors", "#0070F2"),
    "amazon": ("Amazon", "#FF9900"),
}

CSS = """
<style>
:root {
  --ink: #0F172A; --muted: #64748B; --line: #E2E8F0; --card: #FFFFFF;
  --brand: #6366F1; --brand-2: #8B5CF6;
}
#MainMenu, footer, [data-testid="stToolbar"] {visibility: hidden;}
header[data-testid="stHeader"] {background: transparent;}
.block-container {padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1500px;}

/* Sidebar */
[data-testid="stSidebar"] {background: linear-gradient(180deg, #111827 0%, #1E1B4B 100%);}
[data-testid="stSidebar"] * {color: #E5E7EB;}
[data-testid="stSidebar"] .stButton > button {
  background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.12);
  color: #F9FAFB; border-radius: 10px; font-weight: 600; transition: all .15s ease;
}
[data-testid="stSidebar"] .stButton > button:hover {background: rgba(99,102,241,0.35); border-color: #818CF8;}
[data-testid="stSidebar"] .stButton > button[kind="primary"] {
  background: linear-gradient(135deg, #6366F1, #8B5CF6); border: none;
}
[data-testid="stSidebar"] [data-baseweb="tag"] {background: rgba(99,102,241,.35) !important;}
[data-testid="stSidebar"] code, [data-testid="stSidebar"] pre {background: rgba(0,0,0,.35) !important; color: #C7D2FE !important;}
.side-brand {display:flex; align-items:center; gap:.6rem; margin:.2rem 0 1rem 0;}
.side-brand .logo {width:38px; height:38px; border-radius:11px; display:flex; align-items:center; justify-content:center;
  background: linear-gradient(135deg, #6366F1, #EC4899); font-size:20px;}
.side-brand .name {font-weight:800; font-size:1.15rem; color:#fff;}
.side-brand .tag {font-size:.75rem; color:#A5B4FC !important;}
.side-label {font-size:.7rem; letter-spacing:.08em; text-transform:uppercase; color:#A5B4FC !important; margin:.8rem 0 .4rem;}

/* Hero */
.hero {background: linear-gradient(135deg, #4F46E5 0%, #7C3AED 55%, #DB2777 100%); border-radius: 20px;
  padding: 1.5rem 1.8rem; color: #fff; display:flex; justify-content:space-between; align-items:center; gap:1.5rem;
  box-shadow: 0 10px 30px -12px rgba(79,70,229,.55); margin-bottom: 1.2rem;}
.hero h1 {font-size: 1.7rem; margin: 0; color:#fff; font-weight:800; letter-spacing:-.02em;}
.hero p {margin:.25rem 0 0; opacity:.85; font-size:.95rem;}
.hero .goal {min-width: 260px; background: rgba(255,255,255,.14); border-radius: 14px; padding: .8rem 1rem;}
.hero .goal .row {display:flex; justify-content:space-between; font-size:.85rem; opacity:.95;}
.hero .bar {height: 8px; background: rgba(255,255,255,.25); border-radius: 99px; margin-top:.5rem; overflow:hidden;}
.hero .bar > div {height: 100%; background: #fff; border-radius: 99px;}

/* KPI cards */
.kpi-grid {display:grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .85rem; margin-bottom: 1.2rem;}
.kpi {background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 1rem 1.05rem;
  box-shadow: 0 1px 2px rgba(15,23,42,.04); position:relative; overflow:hidden;}
.kpi:before {content:""; position:absolute; inset:0 auto 0 0; width:4px; background: var(--accent);}
.kpi .top {display:flex; justify-content:space-between; align-items:center;}
.kpi .label {font-size:.78rem; color: var(--muted); font-weight:600; text-transform:uppercase; letter-spacing:.04em;}
.kpi .icon {width:32px; height:32px; border-radius:10px; display:flex; align-items:center; justify-content:center;
  background: var(--soft); font-size: 16px;}
.kpi .value {font-size: 1.85rem; font-weight: 800; color: var(--ink); margin-top:.35rem; letter-spacing:-.02em;}
.kpi .sub {font-size: .78rem; color: var(--muted); margin-top:.1rem;}
.kpi .sub b {color: #059669;}

/* Section titles */
.section {display:flex; align-items:center; justify-content:space-between; margin: .6rem 0 .6rem;}
.section h3 {font-size:1.05rem; margin:0; color: var(--ink); font-weight:700;}
.section span {font-size:.8rem; color: var(--muted);}

/* Badges and chips */
.badge {display:inline-block; padding: .18rem .6rem; border-radius: 99px; font-size: .74rem; font-weight: 700; white-space:nowrap;}
.chip {display:inline-block; padding: .14rem .55rem; border-radius: 8px; font-size: .72rem; font-weight:600;
  background:#F1F5F9; color:#334155; margin: 0 .3rem .3rem 0;}
.pchip {display:inline-flex; align-items:center; gap:.35rem; font-size:.74rem; font-weight:700; color:#334155;}
.pchip i {width:8px; height:8px; border-radius:50%; display:inline-block;}

/* Job cards */
.job {background: var(--card); border:1px solid var(--line); border-radius: 14px; padding: .9rem 1rem; margin-bottom:.55rem;
  transition: box-shadow .15s ease, transform .15s ease;}
.job:hover {box-shadow: 0 8px 22px -12px rgba(15,23,42,.25); transform: translateY(-1px);}
.job .t {font-weight:700; color: var(--ink); font-size: .98rem; line-height:1.3;}
.job .t a {color: inherit; text-decoration: none;}
.job .t a:hover {color: var(--brand);}
.job .m {color: var(--muted); font-size: .83rem; margin-top:.15rem;}
.job .row {display:flex; justify-content:space-between; align-items:flex-start; gap:.8rem;}
.job .note {margin-top:.5rem; font-size:.8rem; color:#92400E; background:#FFFBEB; border:1px solid #FDE68A;
  border-radius:8px; padding:.4rem .6rem;}
.score {min-width:52px; height:52px; border-radius:50%; display:flex; align-items:center; justify-content:center;
  font-weight:800; font-size:.95rem; color: var(--ink);
  background: conic-gradient(var(--c) calc(var(--p) * 1%), #EEF2FF 0);}
.score span {width:42px; height:42px; border-radius:50%; background:#fff; display:flex; align-items:center; justify-content:center;}

/* Kanban */
.col-head {display:flex; justify-content:space-between; align-items:center; padding:.55rem .75rem; border-radius:12px;
  font-weight:700; font-size:.85rem; margin-bottom:.6rem;}
.col-head .n {background: rgba(255,255,255,.7); border-radius: 99px; padding: 0 .5rem; font-size:.75rem;}
.kcard {background:#fff; border:1px solid var(--line); border-radius:12px; padding:.6rem .7rem; margin-bottom:.5rem;}
.kcard .t {font-weight:700; font-size:.84rem; color:var(--ink); line-height:1.25;}
.kcard .t a {color:inherit; text-decoration:none;}
.kcard .m {font-size:.76rem; color:var(--muted); margin-top:.15rem;}
.kcard .f {display:flex; justify-content:space-between; align-items:center; margin-top:.4rem; font-size:.72rem; color:var(--muted);}
.more {text-align:center; font-size:.78rem; color:var(--muted); padding:.3rem;}

/* Timeline */
.tl {border-left: 2px solid var(--line); margin-left:.4rem; padding-left: 1rem;}
.tl .e {position:relative; margin-bottom: .8rem;}
.tl .e:before {content:""; position:absolute; left:-1.42rem; top:.3rem; width:10px; height:10px; border-radius:50%;
  background: var(--dot); box-shadow: 0 0 0 3px #fff;}
.tl .e .w {font-size:.74rem; color: var(--muted);}
.tl .e .x {font-size:.86rem; color: var(--ink);}

/* Empty state */
.empty {text-align:center; padding: 2.2rem 1rem; border:1.5px dashed var(--line); border-radius:16px; color: var(--muted);}
.empty .i {font-size: 2rem;}
.empty b {display:block; color: var(--ink); margin:.3rem 0 .15rem;}

/* Tabs as pills */
.stTabs [data-baseweb="tab-list"] {gap: .35rem; background: #EEF2FF; padding: .3rem; border-radius: 14px; width: fit-content; max-width:100%; overflow-x:auto;}
.stTabs [data-baseweb="tab"] {height: 2.4rem; padding: 0 1rem; border-radius: 10px; font-weight: 600; color:#475569;}
.stTabs [aria-selected="true"] {background: #fff !important; color: #4338CA !important; box-shadow: 0 1px 3px rgba(15,23,42,.12);}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {display:none;}

/* Bordered containers look like cards */
[data-testid="stVerticalBlockBorderWrapper"] {border-radius: 14px !important; background: #fff;}
.stDataFrame, [data-testid="stDataFrame"] {border-radius: 12px; overflow:hidden;}
</style>
"""


def _missing(value: object) -> bool:
    return value is None or value != value  # NaN and NaT are not equal to themselves


def esc(value: object) -> str:
    if _missing(value):
        return ""
    return html.escape(str(value), quote=True)


def safe_url(url: object) -> str:
    """Only allow http(s) links in generated HTML."""
    text = str(url or "")
    return esc(text) if text.startswith(("http://", "https://")) else "#"


def badge(status: str) -> str:
    label, fg, bg = STATUS_META.get(status, (status.title(), "#334155", "#F1F5F9"))
    return f'<span class="badge" style="color:{fg};background:{bg}">{esc(label)}</span>'


def platform_chip(platform: str) -> str:
    label, color = PLATFORM_META.get(platform, (platform.title(), "#64748B"))
    return f'<span class="pchip"><i style="background:{color}"></i>{esc(label)}</span>'


def ago(ts: object) -> str:
    """'5m ago' style text for a UTC timestamp (datetime, pandas Timestamp or ISO string)."""
    if _missing(ts):
        return ""
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return ""
    if not isinstance(ts, datetime):
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    secs = int((datetime.now(timezone.utc) - ts).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    days = secs // 86400
    return f"{days}d ago" if days < 30 else ts.strftime("%d %b")


def hero(title: str, subtitle: str, done: int, goal: int) -> str:
    pct = 0 if goal <= 0 else min(100, round(100 * done / goal))
    return f"""
<div class="hero">
  <div><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></div>
  <div class="goal">
    <div class="row"><span>Today's applications</span><b>{done} / {goal}</b></div>
    <div class="bar"><div style="width:{pct}%"></div></div>
  </div>
</div>"""


def kpi_grid(items: list[tuple[str, object, str, str, str]]) -> str:
    """items: (label, value, sub-text html, icon, accent colour)."""
    cards = []
    for label, value, sub, icon, color in items:
        cards.append(
            f'<div class="kpi" style="--accent:{color};--soft:{color}1A">'
            f'<div class="top"><span class="label">{esc(label)}</span><span class="icon">{icon}</span></div>'
            f'<div class="value">{esc(value)}</div><div class="sub">{sub}</div></div>'
        )
    return f'<div class="kpi-grid">{"".join(cards)}</div>'


def section(title: str, hint: str = "") -> str:
    return f'<div class="section"><h3>{esc(title)}</h3><span>{esc(hint)}</span></div>'


def score_ring(score: object) -> str:
    try:
        value = float(score)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = 0.0
    color = "#10B981" if value >= 75 else "#6366F1" if value >= 60 else "#F59E0B"
    return f'<div class="score" style="--p:{value:.0f};--c:{color}"><span>{value:.0f}</span></div>'


def reason_chips(reasons: object) -> str:
    parts = [p.strip() for p in str(reasons or "").split("|") if p.strip()]
    return "".join(f'<span class="chip">{esc(p)}</span>' for p in parts[:5])


def job_card(row: Mapping[str, Any], note: str = "", show_score: bool = False, show_status: bool = True) -> str:
    right = score_ring(row.get("score")) if show_score else (badge(row["status"]) if show_status else "")
    meta = " · ".join(x for x in (esc(row.get("company")), esc(row.get("location"))) if x)
    note_html = f'<div class="note">⚠ {esc(note)}</div>' if note else ""
    chips = reason_chips(row.get("match_reasons")) if show_score else ""
    return f"""
<div class="job">
  <div class="row">
    <div>
      <div class="t"><a href="{safe_url(row.get('url'))}" target="_blank">{esc(row.get('title'))}</a></div>
      <div class="m">{meta}</div>
      <div style="margin-top:.45rem">{platform_chip(row.get('platform', ''))}
        <span class="m" style="margin-left:.6rem">{esc(ago(row.get('status_updated_at')))}</span></div>
      {f'<div style="margin-top:.45rem">{chips}</div>' if chips else ''}
    </div>
    <div>{right}</div>
  </div>
  {note_html}
</div>"""


def kanban_column(status: str, rows: list[Mapping[str, Any]], limit: int = 12) -> str:
    label, fg, bg = STATUS_META[status]
    cards = []
    for r in rows[:limit]:
        cards.append(
            f'<div class="kcard"><div class="t"><a href="{safe_url(r["url"])}" target="_blank">{esc(r["title"])}</a></div>'
            f'<div class="m">{esc(r["company"])}</div>'
            f'<div class="f">{platform_chip(r["platform"])}<span>{esc(ago(r["status_updated_at"]))}</span></div></div>'
        )
    extra = len(rows) - limit
    more = f'<div class="more">+ {extra} more</div>' if extra > 0 else ""
    empty = '<div class="more">—</div>' if not rows else ""
    return (
        f'<div class="col-head" style="color:{fg};background:{bg}"><span>{esc(label)}</span>'
        f'<span class="n">{len(rows)}</span></div>{"".join(cards)}{more}{empty}'
    )


def timeline(events: list[Mapping[str, Any]]) -> str:
    items = []
    for e in events:
        color = STATUS_CHART_COLOR.get(e["status"], "#94A3B8")
        label = STATUS_META.get(e["status"], (e["status"],))[0]
        note = f" — {esc(e['note'])}" if e.get("note") else ""
        items.append(
            f'<div class="e" style="--dot:{color}"><div class="w">{esc(ago(e["created_at"]))} · '
            f'{platform_chip(e["platform"])}</div>'
            f'<div class="x"><b>{esc(label)}</b> · {esc(e["title"])} at {esc(e["company"])}{note}</div></div>'
        )
    return f'<div class="tl">{"".join(items)}</div>'


def empty_state(icon: str, title: str, text: str) -> str:
    return f'<div class="empty"><div class="i">{icon}</div><b>{esc(title)}</b>{esc(text)}</div>'
