"""Fills a separate database with realistic sample data so you can see the dashboard
before setting anything up: python -m jobagent demo"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db

COMPANIES = {
    "workday": ["Adobe", "Salesforce", "Accenture", "Dell Technologies", "Intel"],
    "successfactors": ["SAP", "Wipro", "HCLTech", "EY"],
    "amazon": ["Amazon"],
    "linkedin": ["Google", "Microsoft", "Cisco", "Swiggy", "PhonePe", "Flipkart", "Intuit", "Oracle"],
    "naukri": ["Infosys", "TCS", "Capgemini", "Cognizant", "IBM", "Tech Mahindra"],
    "greenhouse": ["Razorpay", "Postman", "Groww"],
    "lever": ["CRED", "Meesho"],
    "wellfound": ["Zepto", "Zomato", "Atlan"],
}
TITLES = [
    "Python Developer", "Backend Developer", "Software Engineer", "Software Engineer II",
    "Software Development Engineer", "Application Developer", "Backend Engineer (Python)",
    "Python Django Developer", "API Engineer", "Platform Engineer",
]
CITIES = ["Bengaluru, Karnataka", "Hyderabad, Telangana", "Remote, India", "Bengaluru (Hybrid)", "Hyderabad (Hybrid)"]
SKILLS = ["django", "fastapi", "sql", "aws", "docker", "rest api", "flask"]
ATTENTION_NOTES = [
    "Unanswered: Do you have a valid passport?",
    "CAPTCHA required",
    "No Easy Apply button (external application)",
    "[My Experience] Unanswered: From (MM/YYYY)",
    "Apply by hand on amazon.jobs",
]
# Final status weights for jobs that were applied to.
OUTCOMES = [("applied", 30), ("viewed", 18), ("shortlisted", 8), ("interview", 5), ("offer", 1), ("rejected", 14)]
PATHS = {
    "applied": ["applied"],
    "viewed": ["applied", "viewed"],
    "shortlisted": ["applied", "viewed", "shortlisted"],
    "interview": ["applied", "viewed", "shortlisted", "interview"],
    "offer": ["applied", "viewed", "shortlisted", "interview", "offer"],
    "rejected": ["applied", "rejected"],
}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def seed(path: Path, n: int = 220, rng_seed: int = 7) -> None:
    if path.exists():
        path.unlink()
    rnd = random.Random(rng_seed)
    conn = db.connect(path)
    now = datetime.now(timezone.utc)
    for i in range(n):
        platform = rnd.choices(list(COMPANIES), weights=[5, 3, 2, 6, 4, 2, 1, 2])[0]
        company = rnd.choice(COMPANIES[platform])
        title = rnd.choice(TITLES)
        found = now - timedelta(days=rnd.uniform(0, 30), hours=rnd.uniform(0, 12))
        hits = rnd.sample(SKILLS, rnd.randint(1, 4))
        score = round(min(100, 45 + len(hits) * 8 + rnd.uniform(0, 20)), 1)
        reasons = f"Title ~ '{title.split(' (')[0]}' ({rnd.randint(70, 100)}%) | Skills: {', '.join(hits)} | Location OK"
        roll = rnd.random()
        if roll < 0.18:
            final, path_ = "matched", ["matched"]
        elif roll < 0.30:
            final, path_ = "skipped", ["skipped"]
        elif roll < 0.36:
            final, path_ = "approved", ["matched", "approved"]
        elif roll < 0.42:
            final, path_ = "needs_attention", ["matched", "approved", "needs_attention"]
        else:
            final = rnd.choices([o for o, _ in OUTCOMES], weights=[w for _, w in OUTCOMES])[0]
            path_ = ["matched", "approved"] + PATHS[final]

        t = found
        events = [("discovered", "", t)]
        applied_at = None
        # Spread the status changes between discovery and now.
        step = (now - found) / (len(path_) + 1)
        for status in path_:
            t = t + step * rnd.uniform(0.4, 1.0)
            note = ""
            if status == "needs_attention":
                note = "Apply by hand on amazon.jobs" if platform == "amazon" else rnd.choice(ATTENTION_NOTES[:4])
            elif status in ("viewed", "shortlisted", "interview", "offer", "rejected"):
                note = f"Email: Update on your application to {company}"
            elif status == "matched":
                note = f"Score {score}"
            if status == "applied":
                applied_at = t
            events.append((status, note, t))
        if final == "matched":
            score = max(score, 55)

        cur = conn.execute(
            """INSERT INTO jobs (platform, external_id, url, title, company, location, description, discovered_at,
                                 score, match_reasons, status, status_updated_at, applied_at)
               VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)""",
            (platform, f"demo-{i}", f"https://example.com/jobs/{i}", title, company, rnd.choice(CITIES),
             _iso(found), score, reasons, final, _iso(events[-1][2]), _iso(applied_at) if applied_at else None),
        )
        job_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO events (job_id, status, note, created_at) VALUES (?, ?, ?, ?)",
            [(job_id, s, note, _iso(ts)) for s, note, ts in events],
        )
    conn.commit()
    conn.close()
