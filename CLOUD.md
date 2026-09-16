# Running this in the cloud — GitHub Actions + Google Sheets

The scraper stops when your laptop sleeps. This moves the whole run into
GitHub's cloud, on a schedule, so it happens whether your laptop is asleep,
shut down, or in another country.

```
   GitHub's cloud  (always on)                Google
┌──────────────────────────────┐          ┌──────────────────┐
│ cron: 08:17 + 20:17 Manila   │          │ Sheets: your     │
│   python scraper.py --append │          │   live job list  │
│   commit jobs.xlsx ──────────┼──┐       │                  │
│   sheet_sync.py ─────────────┼──┼──────▶│ Telegram: ping   │
└──────────────────────────────┘  │       │   when new      │
                                  ▼       └──────────────────┘
                            the repo file
                          (what stops duplicates)
```

**Cost: $0.** A run takes ~4 minutes and you get 2,000 minutes a month on the
free tier. Twice daily is ~240 minutes.

---

## Read this first — the one thing that might not work

GitHub's runners are Microsoft Azure machines. **OnlineJobs.ph may block or
challenge datacenter IP addresses**, and there is no way to know from here.
Step 5 is a manual test run that answers it in two minutes.

If it *is* blocked, the log says so plainly and the fallback is to run it on
the laptop instead (see [§8](#8-if-github-is-blocked)).

Everything else in here is boring and reliable.

---

## Part 1 — Google Sheets (about 3 minutes)

You do **not** need a Google Cloud project, a service account, or billing.
Apps Script does the work.

1. Make a new Google Sheet. Name it whatever — `OnlineJobs` is fine.
2. **Extensions → Apps Script.** A stub `myFunction` appears.
3. Delete the stub. Paste the entire contents of
   [`apps-script/Code.gs`](apps-script/Code.gs). Click **Save**.
4. **Project Settings** (the gear, left sidebar) → scroll to **Script
   properties** → **Add property**:

   | Property | Value |
   | --- | --- |
   | `WEBHOOK_TOKEN` | a long random string — this is half your password |
   | `TELEGRAM_BOT_TOKEN` | *optional* — from @BotFather |
   | `TELEGRAM_CHAT_ID` | *optional* — your numeric chat id |

   Make the token unguessable. Anything you'd be happy typing into a URL.

5. **Deploy → New deployment.** Click the gear next to "Select type" → **Web
   app**. Then:

   - **Execute as:** `Me`
   - **Who has access:** `Anyone`

   `Anyone` sounds alarming but is correct: it means the URL works without a
   Google login. The token in the URL is what actually guards it.

6. **Copy the Web app URL.** It ends in `/exec` and looks like:

   ```
   https://script.google.com/macros/s/AKfycb.../exec
   ```

7. **Open that URL in a browser.** You should see:

   ```json
   {"ok":true,"message":"OnlineJobs.ph sink is live","sheet":"Jobs","rows":0,...}
   ```

   That's your proof the deployment is live. If you see an error page instead,
   the deployment settings in step 5 are wrong.

> **Editing Code.gs later?** You must re-deploy: **Deploy → Manage
> deployments → pencil icon → Version: New version**. Just saving is not
> enough, and creating a *new* deployment gives you a *different* URL.

---

## Part 2 — Put this on GitHub

You need Git. It isn't installed on this machine, and `winget` is:

```powershell
winget install --id Git.Git -e
```

**Then close this window and open a new one** — the PATH only updates in a
fresh shell. Check it worked with `git --version`.

Next, create the repo. In your browser:

1. Go to **https://github.com/new**
2. **Repository name:** `vajobscraper` (anything)
3. **Visibility: Private** ← important; the workbook holds job data
4. Do **not** add a README, .gitignore or license — you already have them
5. **Create repository**

GitHub shows you a page with setup commands. Ignore them and instead run the
helper in this folder:

```powershell
.\setup-github.ps1 -RepoUrl "https://github.com/YOURNAME/vajobscraper.git"
```

It initialises the repo, commits, and pushes. It refuses to push a `.pdf` or
`.docx`, so your CV stays on your machine — `.gitignore` excludes those
anyway, and this is the second lock on that door.

> **Why private matters.** `output/jobs.xlsx` is committed to the repo — it's
> the file that stops duplicate alerts, and on a cloud runner it's the only
> thing that survives between runs. That's your job-hunting data. Keep it
> private.

---

## Part 3 — Connect the two

In the repo: **Settings → Secrets and variables → Actions → New repository
secret.** Two are required:

| Secret | Value |
| --- | --- |
| `SHEETS_WEBHOOK_URL` | the `/exec` URL from Part 1 |
| `SHEETS_WEBHOOK_TOKEN` | the same string you put in `WEBHOOK_TOKEN` |

Two more are optional — set them and a failed run pings your phone, which is
how you find out the automation quietly broke:

| Secret | Value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | from @BotFather |
| `TELEGRAM_CHAT_ID` | your numeric chat id |

You can skip all four to start. Without the first two the scrape still runs
and still commits; it just doesn't reach the Sheet, and the log says so.

---

## Part 4 — The first run (the test)

1. Repo → **Actions** tab.
2. If GitHub asks to enable workflows, click the green button.
3. Left sidebar → **Scrape OnlineJobs.ph** → **Run workflow** → **Run
   workflow**.

Watch it. Roughly:

| Step | Time | What you're looking for |
| --- | --- | --- |
| Install dependencies | ~20s | |
| Scrape | ~3-4 min | `30 scraped — N new, M already in the sheet` |
| Commit the workbook | ~5s | `jobs: 2026-09-13 08:17 UTC` |
| Sync to Google Sheets | ~10s | `added N, already there M, sheet now holds T` |

**Open your Google Sheet.** If the jobs are there, you're done — it runs
twice a day from now on and you can shut the laptop.

That `30 scraped` line is the answer to the datacenter-IP question. If it says
`No jobs scraped`, or the scrape step fails with a 403, see §8.

> **The first run will ping you about everything.** `sheet_sync.py` sends the
> newest 60 rows, and the Sheet starts empty, so run one adds all 60 at once
> and Telegram reports all 60 as new. That's the seed, not a bug — every run
> after it only reports what genuinely appeared since.

---

## Part 5 — Check the schedule

**Actions → Scrape OnlineJobs.ph** now lists every run. The crons are in
`.github/workflows/scrape.yml`:

```yaml
on:
  schedule:
    - cron: "17 0 * * *"    # 08:17 Asia/Manila
    - cron: "17 12 * * *"   # 20:17 Asia/Manila
```

**Cron in GitHub Actions is always UTC.** Manila is UTC+8 with no DST, so
`0 17` UTC is `8:17 AM` there. The `17` is deliberate — the top of the hour is
when everyone else's job is queued, and GitHub delays those.

To run once a day instead, delete the second line. To change the hour, add 8
to the local hour you want and wrap past 24 (`8 AM` → `0`; `8 PM` → `12`).

GitHub's scheduler is best-effort: under load a run can start 10-30 minutes
late. For job listings that's fine. It is not a precision timer.

---

## How duplicates are avoided

Two independent layers, and neither is clever:

1. **`output/jobs.xlsx` in the repo is the memory.** `--append` reads it and
   skips any `job_id` already there. Without it, every run would "discover"
   the same 30 jobs.
2. **The Apps Script dedups too.** It reads column A before appending, so
   re-sending a row it already has does nothing.

Layer 2 exists because of how `sheet_sync.py` works: every run re-sends the
**newest 60 rows**, not just the ones that were new. That sounds wasteful and
is deliberate — it means a Sheets outage costs you one run of delay instead of
a permanent hole in the sheet. The next run sends the last 60 again and the
sink fills in whatever is missing.

That's also why the workflow commits *before* it syncs. If Sheets is down, the
workbook is already safe.

---

## 7 — What breaks, and what it looks like

| Symptom | Cause | Fix |
| --- | --- | --- |
| `No jobs scraped` | Site blocked the runner's IP, or the markup changed | See §8 / §9 |
| Sync step: *did not return JSON* | Deployment isn't set to **Anyone** | Re-do Part 1 step 5 |
| Sync step: *bad or missing token* | `SHEETS_WEBHOOK_TOKEN` ≠ `WEBHOOK_TOKEN` | Make them match |
| Sheet stopped updating, runs still green | Deployment URL changed after a re-deploy | Update `SHEETS_WEBHOOK_URL` |
| No runs at all for days | The 60-day rule, below | See §9 |
| Run red at *Commit the workbook* | Repo permissions | Settings → Actions → General → Workflow permissions → **Read and write** |

---

## 8 — If GitHub is blocked

That's the datacenter-IP risk, confirmed. Two options, in order of effort:

**Run it on the laptop instead.** Task Scheduler, with *Wake the computer to
run this task* checked — that's the design that was here before, and it
reaches the same Google Sheet via the same `sheet_sync.py`. It works from
sleep; it fails from a full shutdown.

**Or use a small VPS** (~$5/mo, Hetzner or DigitalOcean). Same Linux command,
same script, and you can self-host n8n on it if you ever want that back. It's
still a datacenter IP, so test before paying.

Whichever you pick, `sheet_sync.py` and the Apps Script don't change. That's
the point of splitting them.

---

## 9 — The 60-day rule

GitHub disables scheduled workflows on a **public** repo after 60 days with no
repository activity. A private repo isn't subject to it, and yours is private.

It's handled anyway: the workflow writes `output/last-run.txt` every run, so
every run produces a commit even when no new jobs were found. A quiet month
never looks like an abandoned repo.

If runs ever do stop, go to **Actions → Scrape OnlineJobs.ph → Enable
workflow**. That button is the whole fix.

---

## 10 — What lives where

| Thing | Where | Notes |
| --- | --- | --- |
| `output/jobs.xlsx` | Committed to the repo | Dedup memory. Rewritten and committed each run — git history is your backup |
| `output/last-run.txt` | Committed to the repo | Heartbeat. Keeps the schedule alive |
| `output/jobs-backup.xlsx` | Runner only, discarded | Local safety net, irrelevant in the cloud |
| `.cache/` | Runner only, discarded | Cold every run — that's the ~3 min |
| Your Google Sheet | Google | The view. Dedup'd, append-only |
| Telegram token | Script properties | Never in the repo, never in a log |

The scraper itself needed no changes for any of this.

---

## 11 — Cost

Free. A run is ~4 minutes; twice a day is ~240 minutes a month against a
2,000-minute allowance, and public repos don't count at all. If you ever blow
through it, the fix is to drop to one run a day.
