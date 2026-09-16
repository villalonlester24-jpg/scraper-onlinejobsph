> **This is the scraper only.** The unattended pipeline that used to live here —
> resume matching, AI scoring, Telegram alerts and the Task Scheduler tasks — has
> been removed. Automation now lives in the cloud: **[CLOUD.md](CLOUD.md)**
> covers running it on GitHub Actions with Google Sheets, which works whether
> or not your laptop is awake. **[N8N.md](N8N.md)** describes the older
> n8n-based path, still valid if you'd rather run it locally.

# OnlineJobs.ph Job Scraper

Scrapes public job listings from
[onlinejobs.ph/jobseekers/jobsearch](https://www.onlinejobs.ph/jobseekers/jobsearch).

Yes, it's scrapeable — and it's one of the easy ones:

- **Server-rendered HTML.** No JS execution, no headless browser, no API keys.
- **No login required** for the search results or for individual job pages.
- **`robots.txt` permits `/jobseekers/`.** It asks for a 5s crawl-delay, which
  this script reads from `robots.txt` and honours automatically.
- **Structured data.** Each card carries the posted timestamp in a `data-temp`
  attribute and the job id in the URL, so nothing needs fuzzy parsing.

## Install

```powershell
pip install -r requirements.txt
```

## Usage

```powershell
# The daily run: newest 30, accumulating in output/jobs.xlsx
python scraper.py --append

# First 10 listings with full job-overview details
python scraper.py --limit 10

# Everything: all ~209 listings + each job's full description (~18 min at 5s/req)
python scraper.py --limit 0

# Listing data only — 7 requests, about 35 seconds
python scraper.py --no-details

# Search
python scraper.py --keyword "video editor"
python scraper.py --skills "Video Editing" "2d Animation"
python scraper.py --full-time --limit 60

# Scrape specific job pages directly, skipping the search entirely
python scraper.py --urls "https://www.onlinejobs.ph/jobseekers/job/video-editor-full-time-job-1723927"

# Be more aggressive (please don't hammer it)
python scraper.py --delay 2
```

### Options

| Flag | Meaning |
| --- | --- |
| `--keyword TEXT` | Search job title / company (maps to the site's `jobkeyword`) |
| `--skills S1 S2` | Skill tags; the site caps this at 3 |
| `--gig` / `--part-time` / `--full-time` | Employment types. All three on by default (matches the site's default form state) |
| `--urls URL...` | Scrape these job detail URLs directly, skipping the search |
| `--limit N` | Stop after N jobs. Defaults to 30 (`DEFAULT_LIMIT` in `scraper.py`); `0` means no limit |
| `--max-pages N` | Stop after N listing pages (30 jobs each) |
| `--no-details` | Skip detail pages — listing fields only, much faster |
| `--delay SECS` | Seconds between requests. Default: `robots.txt` crawl-delay (5s) |
| `--refresh` | Ignore all caching, detail pages included |
| `--no-xlsx` | Skip the `.xlsx` workbook (JSONL + CSV still written) |
| `--append` | Add only jobs not already in `jobs.xlsx`, instead of a new file |
| `--stem NAME` | Pin the filename (defaults to `jobs` when `--append`) |
| `--webhook URL` | POST newly found jobs to this n8n webhook — see [N8N.md](N8N.md) |
| `--webhook-token T` | Sent as the `X-Webhook-Token` header (n8n Header Auth) |
| `--out DIR` | Output directory (default `output/`) |

## Output

A normal run writes three timestamped files to `output/`:

- `jobs-YYYYMMDD-HHMMSS.jsonl` — one JSON object per line
- `jobs-YYYYMMDD-HHMMSS.csv` — same fields, UTF-8 BOM for Excel
- `jobs-YYYYMMDD-HHMMSS.xlsx` — formatted workbook: frozen header row,
  autofilter, clickable URLs, and the full job overview wrapped in a wide column

With **`--append`** the workbook becomes a running list instead:

- `jobs.xlsx` — **accumulates across runs**, de-duplicated on `job_id`
- `.jsonl` and `.csv` are **not** written, because they record what a single run
  saw and rewriting them from the accumulated sheet would claim this run found
  jobs it never looked at

Appending is idempotent, so running twice in a day is harmless. Three
safeguards worth knowing about:

- **A workbook this version didn't write** (an older column layout, say) is
  never appended into — the columns would shift and do it silently. It's renamed
  to `jobs-YYYYMMDD-HHMMSS.xlsx` and a fresh sheet is started.
- **If Excel has `jobs.xlsx` open**, the save fails, so the run writes
  `jobs-pending.xlsx` instead and says so. The next run merges it back in and
  deletes it — nothing is stranded, and nothing is scraped twice.
- **`jobs-backup.xlsx`** holds the sheet exactly as it was before the most recent
  append. One file, overwritten each run — it can't accumulate. If a rewrite ever
  goes wrong, this is the undo.

| Field | Source |
| --- | --- |
| `job_id` | URL / `data-jobid` |
| `title`, `url` | Listing card |
| `job_type` | Listing badge (`Any` / `Full Time` / `Part Time` / `Gig`) |
| `wage` | Listing card; also on the detail page |
| `posted` | `data-temp` attribute (structured timestamp) |
| `summary` | Truncated description from the listing |
| `tags` | Skill badges on the card |
| `description` | **Full** text — detail pages only |
| `hours_per_week`, `date_updated`, `categories` | Detail pages only |
| `scraped_at` | When this run touched the record |

`--webhook` posts the newly found jobs to n8n after the files are written — see
[N8N.md](N8N.md).

## How pagination works

The site ignores `?page=N`. It uses **offset-based paths**:

```
/jobseekers/jobsearch          # offset 0
/jobseekers/jobsearch/30       # offset 30
/jobseekers/jobsearch/60       # offset 60
```

The script walks these by 30, carrying the search params along, and stops when a
total is reached or a page repeats.

## Notes

- **Be polite.** The default 5s delay is what the site asks for. A full
  detail-inclusive run is ~215 requests. Leave `--delay` alone unless you need
  a quick sample.
- **Job detail pages are cached** in `.cache/` keyed by URL hash, so re-runs and
  interrupted runs skip the ~3 minutes of detail fetching. They expire after 3
  hours (`CACHE_TTL` in `scraper.py`).
- **Search-result pages are never cached** (`LIST_TTL = 0` in `scraper.py`).
  They're the volatile end — new postings land at the front and push everything
  down — so a cached listing page makes a run report the same jobs as last time
  and silently miss everything posted since. Refetching one costs a single
  request. `--refresh` bypasses the detail cache as well.
- **Descriptions contain `<ojfilter>` tags** where the site masks contact
  details. The tag markup is stripped; the surrounding text is kept as-is.
- **Login-gated data is not collected.** Applying to a job, employer contact
  details, and bookmark state all require an account — and none of it is in the
  public HTML. Don't expect a login flow here.
- Listings change constantly (209 at the time of writing, 281 for
  "video editor"), so counts drift between runs. That's the site, not the script.

## Automation

Two ways to run this unattended. **[CLOUD.md](CLOUD.md)** is the one to read if
you want it to keep working while your laptop is asleep.

**GitHub Actions + Google Sheets** — free, runs in GitHub's cloud twice a day,
appends new listings to a Sheet you can open anywhere, and pings Telegram when
something appears. Nothing runs on your machine.

```
GitHub Actions ──▶ scraper.py ──▶ jobs.xlsx (committed = dedup memory)
                       └────────▶ sheet_sync.py ──▶ Google Sheets + Telegram
```

The repo holds the whole thing: [`.github/workflows/scrape.yml`](.github/workflows/scrape.yml),
[`sheet_sync.py`](sheet_sync.py) and [`apps-script/Code.gs`](apps-script/Code.gs).
`setup-github.ps1` gets it onto GitHub.

**n8n** — [N8N.md](N8N.md). The trigger runs locally and pushes results out to
n8n. Still works, but the scraping stops when the machine does.
`scraper.py --webhook URL` exists for this path; the cloud path doesn't use it.

## Scope

Public listings only, for reading — job hunting, market research, tracking
postings. If you plan to republish the data or hit it at volume, check
OnlineJobs.ph's Terms of Use first.