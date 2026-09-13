"""Upload scraped jobs (jobs.json) to a Google Sheet.

Two authentication modes are supported:

  * oauth           - Log in with your own Google account in the browser.
                      No service-account key required. Use this if your org
                      blocks service-account key creation.
                      Needs an OAuth *Desktop app* client file
                      (default: oauth_client.json).
  * service_account - Use a service-account JSON key and share the sheet
                      with the service-account email (default:
                      service_account.json).

The mode is auto-detected from which file exists, or forced with --auth.

Usage:
  python to_gsheet.py
  python to_gsheet.py --auth oauth
  python to_gsheet.py --auth service_account --creds service_account.json
  python to_gsheet.py --sheet-id <ID> --worksheet Jobs --no-dedupe
"""

import argparse
import json
import os
import sys

import gspread

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADERS = ["Date", "Role", "Client", "Salary", "Description", "URL"]
FIELDS = ["date", "role", "client", "salary", "desc", "url"]

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "gsheet_config.json")


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def parse_args():
    config = load_config()
    parser = argparse.ArgumentParser(description="Upload jobs.json to Google Sheets")
    parser.add_argument("--jobs", default=os.path.join(HERE, "jobs.json"),
                        help="Path to jobs.json (default: jobs.json)")
    parser.add_argument("--auth", choices=["auto", "oauth", "service_account"],
                        default=config.get("auth_mode", "auto"),
                        help="Authentication mode (default: auto)")
    parser.add_argument("--creds",
                        default=config.get("service_account_file",
                                           config.get("credentials_file", "service_account.json")),
                        help="Service account JSON key path")
    parser.add_argument("--client-secret",
                        default=config.get("oauth_client_file", "oauth_client.json"),
                        help="OAuth desktop client JSON path")
    parser.add_argument("--token",
                        default=config.get("oauth_token_file", "oauth_token.json"),
                        help="Where to cache the OAuth token (default: oauth_token.json)")
    parser.add_argument("--sheet-id", default=config.get("spreadsheet_id", ""),
                        help="Spreadsheet ID or full URL")
    parser.add_argument("--worksheet", default=config.get("worksheet", "Jobs"),
                        help="Worksheet/tab name (default: Jobs)")
    parser.add_argument("--no-dedupe", action="store_true",
                        help="Append everything, even jobs already in the sheet")
    return parser.parse_args()


def resolve_path(path):
    if os.path.isabs(path):
        return path
    return os.path.join(HERE, path)


def load_jobs(path):
    if not os.path.exists(path):
        sys.exit(f"[ERROR] jobs file not found: {path}\n"
                 f"Run the scraper first, e.g.:\n"
                 f"  python -m scrapy crawl jobs -a offset=0 -o jobs.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def extract_sheet_id(value):
    if not value:
        return ""
    if "docs.google.com/spreadsheets" in value:
        part = value.split("/d/")[1]
        return part.split("/")[0]
    return value.strip()


def build_client(args):
    creds_path = resolve_path(args.creds)
    client_path = resolve_path(args.client_secret)
    token_path = resolve_path(args.token)

    mode = args.auth
    if mode == "auto":
        if os.path.exists(client_path):
            mode = "oauth"
        elif os.path.exists(creds_path):
            mode = "service_account"
        else:
            sys.exit(
                "[ERROR] No credentials found.\n\n"
                f"  OAuth client file : {client_path}\n"
                f"  Service account   : {creds_path}\n\n"
                "Create one of them (see the step-by-step guide). If your Google\n"
                "Workspace blocks service-account keys, use OAuth:\n"
                "  1. Google Cloud -> APIs & Services -> Credentials\n"
                "  2. Create Credentials -> OAuth client ID -> Desktop app\n"
                "  3. Download JSON, save it as oauth_client.json next to this script\n"
            )

    if mode == "oauth":
        if not os.path.exists(client_path):
            sys.exit(f"[ERROR] OAuth client file not found: {client_path}\n"
                     f"Create an OAuth 'Desktop app' client and save it there, "
                     f"or pass --client-secret.")
        print("Authenticating with Google (a browser window will open on first run)...")
        return gspread.oauth(
            credentials_filename=client_path,
            authorized_user_filename=token_path,
            scopes=SCOPES,
        )

    if not os.path.exists(creds_path):
        sys.exit(f"[ERROR] Service account key not found: {creds_path}")
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return gspread.authorize(creds)


def get_worksheet(client, sheet_id, worksheet_name):
    spreadsheet = client.open_by_key(sheet_id)
    try:
        return spreadsheet.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=len(HEADERS))
        worksheet.append_row(HEADERS)
        return worksheet


def main():
    args = parse_args()

    sheet_id = extract_sheet_id(args.sheet_id)
    if not sheet_id:
        sys.exit("[ERROR] No spreadsheet ID/URL provided.\n"
                 "Pass --sheet-id or set \"spreadsheet_id\" in gsheet_config.json.")

    jobs = load_jobs(args.jobs)
    client = build_client(args)
    worksheet = get_worksheet(client, sheet_id, args.worksheet)

    existing = worksheet.get_all_values()
    if not existing:
        worksheet.append_row(HEADERS)
        existing = [HEADERS]

    url_col = HEADERS.index("URL")
    existing_urls = {row[url_col] for row in existing[1:] if len(row) > url_col}

    rows = []
    for job in jobs:
        url = job.get("url", "")
        if not args.no_dedupe and url in existing_urls:
            continue
        rows.append([str(job.get(field, "")) for field in FIELDS])

    if not rows:
        print(f"Nothing to add. {len(jobs)} scraped job(s), all already in the sheet.")
        return

    worksheet.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"Added {len(rows)} row(s) to '{args.worksheet}' "
          f"(skipped {len(jobs) - len(rows)} duplicate(s)).")
    print(f"Open: https://docs.google.com/spreadsheets/d/{sheet_id}/edit")


if __name__ == "__main__":
    main()
