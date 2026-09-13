# OnlineJobs.PH Job Scraper

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Scrape workflow](https://github.com/villalonlester24-jpg/scraper-onlinejobsph/actions/workflows/scrape.yml/badge.svg)

Automatically scrape OnlineJobs.PH job posts into a Google Sheet.

## Overview

This is a Scrapy spider that collects the current job posts from the
[OnlineJobs.PH job search](https://www.onlinejobs.ph/jobseekers/jobsearch),
follows each post to capture its full description, and writes the results to a
Google Sheet. It is built to run on a schedule so the sheet always shows the
latest set of jobs without any manual work.

## Features

- Scrapes every post on the OnlineJobs.PH job search
- Follows each job page to capture the complete description, not the short teaser
- Writes results directly to Google Sheets
- Replaces the sheet contents on every run so the list stays current
- Runs automatically every 2 hours with GitHub Actions
- Reuses previously scraped descriptions so runs stay fast and light

## What's in This Version

Maintained by [Lester Matthew Villalon](https://github.com/villalonlester24-jpg).
Key additions in this version:

- Google Sheets replace each run (a fresh list every run, keeping the `Sent` column)
- Every 2-hour automatic schedule
- GitHub Actions hosting

## Requirements

- Python 3.9 or newer
- Scrapy, gspread, and google-auth (see `requirements.txt`)
- A Google Sheet and Google OAuth credentials

## Installation

```bash
pip install -r requirements.txt
```

## Google Sheets Setup

1. Create a Google Sheet and copy its ID or full URL.
2. In Google Cloud, enable the Google Sheets API and create an OAuth
   Desktop app client.
3. Save the downloaded client file as `oauth_client.json` next to the scripts.
4. Copy `gsheet_config.example.json` to `gsheet_config.json` and set your
   spreadsheet ID.
5. On the first run, a browser opens to authorize access; the token is then
   cached as `oauth_token.json`.

## Usage

Scrape the current job posts and write them to your Google Sheet:

```bash
python -m scrapy crawl jobs -a offset=0 -o jobs.json
python to_gsheet.py --replace --jobs jobs.json
```

On Windows you can use the helper script instead:

```
run.bat
```

## How It Works

1. Crawls the OnlineJobs.PH job search listing.
2. Follows each job post and extracts the full description.
3. Reuses descriptions already stored in the sheet so unchanged posts are not
   fetched again.
4. Replaces the Google Sheet with the current set of jobs, preserving any extra
   columns (for example a manual `Sent` column).

## Project Structure

```
scraper_onlinejobsph/
  spiders/jobs_spider.py   The Scrapy spider
  settings.py              Scrapy settings
to_gsheet.py               Exports, caches, and writes to Google Sheets
.github/workflows/scrape.yml   Scheduled GitHub Actions workflow
run.bat                    Windows helper
requirements.txt           Python dependencies
```

## Credits

- Maintained by: [Lester Matthew Villalon](https://github.com/villalonlester24-jpg)

## License

Released under the MIT License. See [LICENSE](LICENSE) for details.

## Disclaimer

This project is intended for educational and personal use. Web scraping may be
subject to a website's terms of service. Use it responsibly and respect the
site's `robots.txt` and rate limits.
