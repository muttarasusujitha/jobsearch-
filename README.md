# Job Agent

A Python tool that finds jobs, applies to them for you, and tracks what happens next. It uses no AI models. Matching, form filling and email tracking all run on rules you control in one config file.

## What it does

| Agent | What it does |
|---|---|
| Discovery | Collects openings from LinkedIn, Naukri, Wellfound, Amazon, and company career pages on Greenhouse, Lever, Ashby, Workday (Accenture, Salesforce, Adobe, Dell, Intel) and SuccessFactors (SAP, Wipro, HCLTech, EY) |
| Matcher | Scores each job 0–100 on title, skills, location and experience, and skips bad fits |
| Applier | Fills and submits applications: LinkedIn Easy Apply, the Naukri apply button and chatbot, Wellfound, Greenhouse/Lever/Ashby forms, and the multi-page Workday flow |
| Tracker | Reads your inbox over IMAP and moves each application through Applied → Viewed → Shortlisted → Interview → Offer / Rejected |
| Startup finder | Finds Bangalore and Hyderabad startups with a public careers board (Greenhouse, Lever, Ashby) and adds them to discovery automatically |

The **dashboard** (Streamlit) shows the counts, a review queue, jobs that need your attention, application tracking with editable statuses, per-platform stats, the startups found, and buttons to run each agent.

## Two ways to run it

| | Single process (CLI) | Microservices (Docker Compose) |
|---|---|---|
| Start | `python -m jobagent dashboard` | `docker compose up -d --build` |
| Needs | Python 3.11+ | Docker Desktop |
| Browser | Opens on your desktop | Runs in a container. Watch and control it at http://localhost:6080 |
| Scheduling | `python -m jobagent run --loop-minutes 180` | Built-in `scheduler` service |
| Guide | below | [docs/MICROSERVICES.md](docs/MICROSERVICES.md) |

Both use the same config files and the same rules.

## Bangalore and Hyderabad, city-wide

`locations = ["Bangalore", "Hyderabad", "Remote"]` matches every area of both cities: Whitefield, Electronic City, ORR, HSR Layout, Koramangala, HITEC City, Gachibowli, Madhapur, Financial District and more. Spellings like "Bengaluru" and "Secunderabad" count too.

The **startup finder** (`python -m jobagent find-startups`, or the 🏙️ button in the dashboard) checks:

1. the starter list in `config/startups.toml` (about 80 Bangalore and Hyderabad startups, edit freely), and
2. every company it sees in Bangalore or Hyderabad job posts on LinkedIn, Naukri, Wellfound and the other sites.

For each company it tries likely board names on Greenhouse, Lever and Ashby. It keeps a board only if it has open jobs in India, which stops a same-named foreign company from being picked up. Found boards are searched on every Discover run. Results are cached for 14 days and listed in the dashboard's **Startups** tab.

## Setup (on your own computer)

The agents control a real, visible browser that is logged into your accounts, so run this on your laptop, not a server.

```bash
cd job-agent
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

python -m jobagent init            # creates config/profile.toml
# edit config/profile.toml and copy your resume to config/resume.pdf

python -m jobagent login linkedin  # log in by hand once; the session is saved
python -m jobagent login naukri
python -m jobagent login wellfound

export JOBAGENT_EMAIL_PASSWORD="your-gmail-app-password"   # for email tracking
python -m jobagent dashboard
```

To see the dashboard before setting anything up, run `python -m jobagent demo`. It fills a separate `data-demo/` folder with sample jobs and opens the dashboard on it. Your real data isn't touched, and the agent buttons are disabled in demo mode.

Greenhouse, Lever and Ashby don't need a login. Add the company board names to the config. For example, `https://job-boards.greenhouse.io/razorpay` has the token `razorpay`.

Workday sites (Accenture and other MNCs) need a separate candidate account for each company. Add the company's job-list URL under `[platforms.workday] sites`, then run `python -m jobagent login workday`. It opens each site so you can create an account or sign in once. To find which system a company uses, click Apply on one of its jobs and look at the address bar. The config file lists the URL patterns.

For Gmail tracking, turn on 2-step verification, create an [App Password](https://myaccount.google.com/apppasswords), and put it in `JOBAGENT_EMAIL_PASSWORD`. Your password is never written to a file.

## Daily use

```bash
python -m jobagent discover        # find and score new jobs
#  -> approve jobs in the dashboard "Review queue" (when review_mode = true)
python -m jobagent apply --dry-run --limit 3   # fill forms without submitting, to check answers
python -m jobagent apply           # apply to approved jobs, within your daily limits
python -m jobagent track           # update statuses from email
python -m jobagent status          # counts per status

python -m jobagent run --loop-minutes 180      # do all three every 3 hours
python -m jobagent match --rescore             # re-score after changing preferences
```

Use `--platform linkedin` (repeatable) to limit a command to certain sites.

## How each job moves

```
discovered → matched (review queue) → approved → applied → viewed → shortlisted → interview → offer
                 ↘ skipped                ↘ needs_attention / failed               ↘ rejected
```

- **matched**: passed your rules and is waiting for you to approve it. Set `review_mode = false` to apply automatically.
- **needs_attention**: the agent stopped instead of guessing. Common causes are a screening question with no rule, a CAPTCHA, or an "apply on company site" link. The dashboard shows why. Apply by hand and click "I applied", or add a rule and click "Retry".

## Teaching it new answers

Questions the agent can't answer are listed in "Needs attention", for example `Unanswered: Do you have a valid driving licence?`. Add a rule to the config:

```toml
[[answers.custom]]
pattern = "driving licen"
answer = "Yes"
```

`pattern` is a case-insensitive regular expression. For dropdowns and radio buttons, the answer is matched to the closest option.

## Safety limits

- `daily_max` and `per_platform_daily_max` cap how many applications are sent each day. `min_delay_seconds` and `max_delay_seconds` set the random wait between applications.
- When a site shows a CAPTCHA or security check, the agent pauses and asks you to complete it in the browser window. It doesn't try to solve CAPTCHAs or hide that it's automated. In `--headless` mode it can't pause, so those jobs go to "Needs attention".
- Your inbox is opened read-only, so emails stay unread.
- **Account risk:** LinkedIn, Naukri, Indeed and Glassdoor don't allow automated applying in their terms and can restrict accounts that do it. Keep limits low (10–15 a day per site) and use review mode.

## Project layout

```
config/profile.example.toml   all settings: profile, preferences, limits, answers, platforms, email
jobagent/
  cli.py                      commands (python -m jobagent ...)
  config.py  db.py  models.py
  matching.py                 scoring rules
  answers.py                  screening-question rules and the cover letter template
  browser.py                  Playwright sessions, CAPTCHA pause, generic form filler
  agents/                     discovery, matcher, applier, tracker
  platforms/                  one adapter per site
dashboard/app.py              Streamlit dashboard
data/                         SQLite database, browser profiles, agent.log (git-ignored)
```

## MNC coverage

| Company | How it's covered |
|---|---|
| Accenture, Salesforce, Adobe, Dell, Intel | Workday adapter: found and applied to automatically (Intel's site name is unconfirmed) |
| SAP Labs, Wipro, HCLTech, EY | SuccessFactors adapter: found automatically, applied to on a best-effort basis |
| Amazon | Found and matched automatically. You apply by hand from "Needs attention" |
| Google, Microsoft, Cisco, IBM, Oracle, Intuit, Infosys, TCS, Capgemini, Cognizant, Deloitte | No direct adapter yet. Covered through LinkedIn and Naukri. Set `easy_apply_only = false` under `[platforms.linkedin]` to also list their "apply on company site" jobs |

`preferred_companies` in the config gives jobs at listed companies +10 score on every platform.

## Adding more sites

Indeed, Foundit, Glassdoor, WorkIndia, Y Combinator (Work at a Startup), Hiring.cafe and Himalayas are not built yet. To add one, create `jobagent/platforms/<site>.py` with a `Platform` subclass that implements `discover()` and `apply()`. Use `naukri.py` or `linkedin.py` as a template, then register it in `platforms/__init__.py`.

## When a site changes its layout

Job sites change their HTML often. Each browser adapter keeps its CSS selectors in a `SEL` dict at the top of the file. If discovery finds 0 jobs, or applying always stops at the same step, update those selectors. Run with `-v` to get detailed logs in `data/agent.log`.
