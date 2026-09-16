# Automating this with n8n

The scraper is deliberately dumb: scrape the newest listings, append the ones it
hasn't seen to `jobs.xlsx`, and — optionally — tell n8n what it found. Everything
else (alerts, spreadsheets, digests, retries) belongs to n8n.

## Why the flow points this way

n8n **Cloud** cannot run commands on your PC. The Execute Command node carries a
"Not available on Cloud" badge — hosted instances never had it — and no cloud
workflow can read your `C:\` drive.

So the direction of the wire is reversed: **your PC runs the scraper, then POSTs
the results to an n8n webhook.** Nothing needs to be exposed to the internet,
no port forwarding, no tunnel, no inbound firewall rule.

```
   your PC                                    n8n Cloud
┌──────────────────────────┐              ┌────────────────────┐
│ python scraper.py        │              │ Webhook (POST)     │
│   --append               │  ── HTTPS ──▶│   ↓                │
│   --webhook <url>        │              │ Telegram / Sheets  │
│                          │              │   ↓                │
│ output/jobs.xlsx         │              │ your phone         │
└──────────────────────────┘              └────────────────────┘
   accumulates forever                       never touches your disk
```

## 1. Prerequisites

On the machine running the scraper:

```powershell
pip install -r requirements.txt
```

That is `requests`, `beautifulsoup4` and `openpyxl` — nothing else. There is no
API key, no login, no browser and no AI service involved. n8n Cloud is the only
external thing, and only if you use `--webhook`.

## 2. Run it locally

```powershell
python scraper.py --append
```

- `--append` — accumulate into `output/jobs.xlsx`, adding only jobs that aren't
  already there. The newest 30 listings is the built-in default (`DEFAULT_LIMIT`
  in `scraper.py`), so there is no `--limit` to remember.

The first run takes about **3 minutes**: 30 detail pages at the 5s crawl-delay
`robots.txt` asks for. Later runs are faster, because job detail pages are
cached (3-hour TTL in `.cache/`). Search-result pages are deliberately **never**
cached — they're the part that changes — so every run sees the current postings
rather than replaying the ones from last time.

Output on a typical day:

```
30 scraped — 7 new, 23 already in the sheet:
  output\jobs.xlsx
```

`jobs.xlsx` accumulates across runs and is de-duplicated on `job_id`, so running
it twice a day is harmless. Run it as often as you like.

> **Note:** `--append` fixes the filename to `jobs.xlsx`. Without it you get a
> timestamped file per run, which is handy for one-off searches but never
> accumulates.

### Testing the webhook before you have an n8n account

You can watch the exact payload locally. `webhook_sink.py` is a throwaway
receiver that prints whatever the scraper sends.

```powershell
# terminal 1
python webhook_sink.py

# terminal 2 — a FRESH --out dir, so every job counts as new
python scraper.py --limit 5 --append --out "$env:TEMP\webtest" `
  --webhook http://127.0.0.1:9000 --webhook-token dev-secret
```

> **Why a fresh directory matters.** The scraper stays silent when nothing is
> new — that's deliberate, so a quiet night doesn't fire a pointless alert. Point
> it at a fresh `--out` and all 30 jobs are new, so the webhook actually fires.
> Against your real `output/jobs.xlsx` an up-to-date run sends nothing at all.

Terminal 1 prints the `X-Webhook-Token` header, the run counts and the first few
jobs. That's exactly what n8n will receive, so if it looks right here, the
scraper side is done.

## 3. The n8n Cloud side

1. **New workflow** → add a **Webhook** node.
2. Set **HTTP Method** to `POST`.
3. Set **Path** to something unguessable, e.g. `onlinejobs-7f3a91c2`. The path
   is half your secret — a webhook URL is public by definition.
4. Under **Authentication**, choose **Header Auth** and create a credential:
   - **Name:** `X-Webhook-Token`
   - **Value:** a long random string
5. **Copy the Production URL.** It looks like:

   ```
   https://<your-instance>.app.n8n.cloud/webhook/onlinejobs-7f3a91c2
   ```

6. Add whatever should happen next (see §5), then **save and activate the
   workflow.**

> **The production URL returns 404 until the workflow is active.** This is the
> single most common gotcha. While you're still building, use the **Test URL**
> instead — but it only accepts one request per click of "Listen for test
> event".

## 4. Connect the two

```powershell
python scraper.py --append `
  --webhook "https://<your-instance>.app.n8n.cloud/webhook/onlinejobs-7f3a91c2" `
  --webhook-token "the-same-long-random-string"
```

`--webhook-token` sends the `X-Webhook-Token` header n8n's Header Auth checks.
If it doesn't match, n8n rejects the request and the scraper prints the HTTP
error — but **the run still succeeds**, because the spreadsheet is already saved
by then. A broken webhook never costs you data.

The scraper POSTs this:

```json
{
  "run_at": "2026-09-13T22:00:03",
  "scraped": 30,
  "new": 7,
  "skipped_existing": 23,
  "jobs": [
    {
      "job_id": "1728987",
      "title": "Google Ads",
      "url": "https://www.onlinejobs.ph/jobseekers/job/google-ads-1728987",
      "job_type": "Any",
      "wage": "$10/hour",
      "posted": "2026-09-12 20:43:08",
      "tags": ["Google Ads", "Youtube Ads"],
      "description": "..."
    }
  ]
}
```

`jobs` contains **only the new listings**, never all 30. That's what lets n8n
distinguish a busy night from a quiet one.

**Nothing is sent when there are no new jobs** — a quiet run skips the webhook
entirely rather than firing an empty alert.

## 5. Downstream recipes

### Telegram alert, only when something new appeared

Your existing bot works as-is. In n8n:

1. Add an **If** node after the Webhook: `{{ $json.body.new }}` **is greater
   than** `0`.
2. From the `true` branch, add a **Telegram** node.
3. Create a Telegram credential with the bot token from @BotFather. To find your
   chat ID, message the bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and read
   `"chat":{"id":...}`.
4. **Chat ID:** your numeric ID.
5. **Text:**

   ```
   {{ $json.body.new }} new job(s) on OnlineJobs.ph

   {{ $json.body.jobs.map(j => "• " + j.title + "\n  " + j.wage + "\n  " + j.url).join("\n\n") }}
   ```

Set the Telegram node's **Parse Mode** to `HTML` if you want formatting.

### Log every job to Google Sheets

Add a **Google Sheets** node → *Append Row*, map `job_id`, `title`, `wage`,
`posted` and `url` from the item. Turn on **Execute Once** if the node is inside
a loop, or split `jobs` into items first with an **Item Lists** / **Split Out**
node on the `body.jobs` field.

### Formatting in a Code node

```javascript
const { new: count, jobs } = $input.first().json.body;
return jobs.map(j => ({
  json: {
    summary: `${j.title} — ${j.wage || "wage not stated"}`,
    link: j.url,
    fresh: count,
  },
}));
```

## 6. Scheduling

Be clear about what n8n can and cannot do here.

- **n8n's Schedule Trigger cannot start this scraper.** It runs in n8n's cloud,
  which has no way to reach your PC. A Schedule Trigger is useful for things
  that happen *after* the webhook — a daily digest, a cleanup, a reminder.
- **The local run needs its own trigger.** Two options:
  - **Windows Task Scheduler** — `python scraper.py --append
    --webhook "<url>" --webhook-token "<token>"`, daily. Point it at the full
    path to `python.exe` and set "Start in" to the project folder.
  - **Just run it by hand** when you think of it. Appending is idempotent, so
    running it twice in a day costs nothing but a few minutes.

Either way, the split is: **your PC decides when to scrape; n8n decides what to
do about it.**
