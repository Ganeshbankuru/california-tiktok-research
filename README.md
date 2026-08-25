# California Trucking TikTok Creator Researcher

Marketing-research tool that discovers small (2,000–9,999 follower), active,
trucking/fleet/logistics-relevant TikTok creators — with a preference for
California relevance — starting from six seed accounts.

## Architecture

```
main.py                  CLI entry point (--new-run / --resume / --export / --report / --status)
src/
  pipeline.py            Orchestrator: discovery -> verification -> classification -> scoring -> persist
  browser/               agent-browser CLI wrapper (+ polite httpx fetcher, block detection)
  discovery/             Seed research, keyword & hashtag search-engine discovery
  sources/               DuckDuckGo lite, TikTok public profile reader, YouTube channel reader
  filters/               Follower band, activity, California & trucking classifiers (deterministic)
  scoring/               100-point marketing score + confidence score (deterministic)
  database/              SQLite schema + all persistence (WAL journaling)
  exporters/             CSV / XLSX / JSON / research_summary.md
  utils/                 config loader, logging, rate limiter, date/text helpers
config/seeds.yaml        Seed accounts (handles resolved at runtime; VERIFY fields)
config/keywords.yaml     Trucking topics, CA terms, hashtags, search templates
config/settings.yaml     Thresholds, weights, pacing — nothing hard-coded elsewhere
database/research.sqlite3
output/                  csv/xlsx/json/summary exports
logs/                    per-run logs
tests/                   pytest suite (offline, deterministic)
```

Design rules: deterministic Python does all filtering/scoring/dedup/persistence;
the browser only fetches public pages; SQLite holds state so any run can resume;
no CAPTCHA/anti-bot bypassing — blocked sources are recorded and skipped.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
npm i -g agent-browser   # if not already installed
agent-browser install    # one-time Chrome setup for agent-browser
copy .env.example .env   # optional; no secrets required for basic operation
```

Requires Python 3.11+, Node.js 20+.

## Usage

```powershell
# Small verified test run (max 10 candidates)
.venv\Scripts\python main.py --new-run --limit 10

# Full run later (requires explicit approval of scale)
.venv\Scripts\python main.py --new-run --limit 100

# Restrict to one seed
.venv\Scripts\python main.py --new-run --seed "Alex Nino" --limit 10

# Resume after interruption (same machine, same db)
.venv\Scripts\python main.py --resume

# Re-export / regenerate summary without new fetching
.venv\Scripts\python main.py --export
.venv\Scripts\python main.py --report

# Inspect runs
.venv\Scripts\python main.py --status
```

## Tests

```powershell
.venv\Scripts\python -m pytest tests -q
```

## Data policy

- Only publicly visible profile data is collected (bio, counts, recent posts).
- Follower counts are never estimated: unverified => NULL + UNKNOWN status.
- Dates are never invented; unknown activity stays UNKNOWN.
- A source showing captcha/anti-bot walls is recorded in `errors` and skipped.
- Conservative pacing between requests; repeated blocks disable the source.
- No private emails/phones/messages; no login to any platform.

## Scoring model (100 points)

| Component | Max |
|---|---|
| California relevance (HIGH/MEDIUM/LOW) | 25 |
| Trucking relevance (HIGH/MEDIUM/LOW) | 25 |
| Activity (ACTIVE/RECENT) | 20 |
| Audience fit (verified 2k–10k) | 10 |
| Engagement (from public like/view ratios) | 10 |
| Marketing potential (contact, links, niches) | 10 |

Confidence (0–100) reflects how much of the record was actually verified;
missing data lowers it and never inflates the marketing score.

## VPN note

Optional environment dependency. Configure any VPN at OS level before running;
the app never stores VPN credentials and never rotates servers to evade limits.
