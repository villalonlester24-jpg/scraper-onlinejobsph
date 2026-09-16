#!/usr/bin/env python3
"""
Sync scraped jobs from output/jobs.xlsx (or JSON) to a Google Sheet using Google Sheets API (gspread).

Preserves manual extra columns (e.g. "Sent") by URL.

Usage:
  python to_gsheet.py                       # appends new jobs (deduped by URL)
  python to_gsheet.py --replace             # replaces rows while preserving manual columns
  python to_gsheet.py --file output/jobs.xlsx
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import gspread

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADERS = ["Date", "Role", "Client", "Salary", "Description", "URL"]
FIELDS = ["date", "role", "client", "salary", "desc", "url"]

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "gsheet_config.json"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def parse_args():
    config = load_config()
    p = argparse.ArgumentParser(description="Upload scraped jobs to Google Sheets")
    p.add_argument(
        "--file",
        type=Path,
        default=HERE / "output" / "jobs.xlsx",
        help="Path to jobs workbook or json (default: output/jobs.xlsx)",
    )
    p.add_argument(
        "--auth",
        choices=["auto", "oauth", "service_account"],
        default=config.get("auth_mode", "auto"),
        help="Authentication mode (default: auto)",
    )
    p.add_argument(
        "--creds",
        default=config.get("service_account_file", "service_account.json"),
        help="Service account JSON key path",
    )
    p.add_argument(
        "--client-secret",
        default=config.get("oauth_client_file", "oauth_client.json"),
        help="OAuth desktop client JSON path",
    )
    p.add_argument(
        "--token",
        default=config.get("oauth_token_file", "oauth_token.json"),
        help="OAuth token JSON path",
    )
    p.add_argument(
        "--sheet-id",
        default=config.get("spreadsheet_id", "1ngtFTIRwHIa4usCxyXa4FjJkTbX-4xZns5SCPdQNXBo"),
        help="Spreadsheet ID or full URL",
    )
    p.add_argument(
        "--worksheet",
        default=config.get("worksheet", "Jobs"),
        help="Worksheet tab name (default: Jobs)",
    )
    p.add_argument(
        "--replace",
        action="store_true",
        help="Replace worksheet with current jobs, preserving extra columns by URL",
    )
    p.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Append all rows even if URL already exists in sheet",
    )
    return p.parse_args()


def extract_sheet_id(value: str) -> str:
    if not value:
        return ""
    if "docs.google.com/spreadsheets" in value:
        part = value.split("/d/")[1]
        return part.split("/")[0].split("?")[0]
    return value.strip()


def load_jobs_from_file(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"[ERROR] file not found: {path}\nRun the scraper first (e.g. python scraper.py)")

    if path.suffix == ".json":
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
            out = []
            for j in data:
                out.append({
                    "date": j.get("date") or j.get("posted", ""),
                    "role": j.get("role") or j.get("title", ""),
                    "client": j.get("client") or j.get("categories") or j.get("tags", ""),
                    "salary": j.get("salary") or j.get("wage", ""),
                    "desc": j.get("desc") or j.get("description") or j.get("summary", ""),
                    "url": j.get("url", ""),
                })
            return out

    if path.suffix == ".jsonl":
        out = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                j = json.loads(line)
                out.append({
                    "date": j.get("posted", ""),
                    "role": j.get("title", ""),
                    "client": ", ".join(j.get("categories") or j.get("tags") or []),
                    "salary": j.get("wage", ""),
                    "desc": j.get("description") or j.get("summary", ""),
                    "url": j.get("url", ""),
                })
        return out

    # Default to openpyxl workbook
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return []

    header = [str(h) if h is not None else "" for h in rows[0]]
    col_map = {name: i for i, name in enumerate(header)}

    out = []
    for r in rows[1:]:
        if all(v is None for v in r):
            continue
        def get_val(name: str) -> str:
            idx = col_map.get(name)
            if idx is not None and idx < len(r) and r[idx] is not None:
                return str(r[idx])
            return ""

        url = get_val("URL")
        if not url:
            continue

        date = get_val("Posted") or get_val("Date Updated") or get_val("Scraped At")
        role = get_val("Title")
        client = get_val("Categories") or get_val("Tags")
        salary = get_val("Wage / Salary")
        desc = get_val("Job Overview (full)") or get_val("Listing Summary")

        out.append({
            "date": date,
            "role": role,
            "client": client,
            "salary": salary,
            "desc": desc,
            "url": url,
        })
    return out


def build_client(args):
    creds_path = HERE / args.creds
    client_path = HERE / args.client_secret
    token_path = HERE / args.token

    mode = args.auth
    if mode == "auto":
        if client_path.exists():
            mode = "oauth"
        elif creds_path.exists():
            mode = "service_account"
        else:
            sys.exit(
                "[ERROR] No Google credentials found.\n"
                f"  OAuth client file : {client_path}\n"
                f"  Service account   : {creds_path}\n"
            )

    if mode == "oauth":
        if not client_path.exists():
            sys.exit(f"[ERROR] OAuth client file not found: {client_path}")
        return gspread.oauth(
            credentials_filename=str(client_path),
            authorized_user_filename=str(token_path),
            scopes=SCOPES,
        )

    if not creds_path.exists():
        sys.exit(f"[ERROR] Service account key not found: {creds_path}")
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(str(creds_path), scopes=SCOPES)
    return gspread.authorize(creds)


def get_worksheet(client, sheet_id: str, worksheet_name: str):
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
        sys.exit("[ERROR] No spreadsheet ID provided.")

    jobs = load_jobs_from_file(args.file)
    print(f"Loaded {len(jobs)} jobs from {args.file}")
    if not jobs:
        print("No jobs to sync.")
        return 0

    print("Connecting to Google Sheets...")
    client = build_client(args)
    ws = get_worksheet(client, sheet_id, args.worksheet)

    existing = ws.get_all_values()
    if not existing:
        ws.append_row(HEADERS)
        existing = [HEADERS]

    header_row = existing[0]
    url_col = header_row.index("URL") if "URL" in header_row else 5
    existing_urls = {row[url_col] for row in existing[1:] if len(row) > url_col and row[url_col]}

    if args.replace:
        # Preserve extra columns like "Sent" by URL
        extra_headers = header_row[len(HEADERS):] if len(header_row) > len(HEADERS) else []
        extra_by_url = {}
        for row in existing[1:]:
            if len(row) > url_col and row[url_col]:
                extra_by_url[row[url_col]] = row[len(HEADERS):]

        out = [HEADERS + extra_headers]
        for j in jobs:
            base_vals = [str(j.get(f, "")) for f in FIELDS]
            extra = extra_by_url.get(j.get("url", ""), [])
            extra = (extra + [""] * len(extra_headers))[:len(extra_headers)]
            out.append(base_vals + extra)

        ws.clear()
        ws.update(out, value_input_option="USER_ENTERED")
        print(f"Replaced '{args.worksheet}' with {len(jobs)} rows (preserved {len(extra_headers)} extra column(s)).")
        print(f"Open: https://docs.google.com/spreadsheets/d/{sheet_id}/edit")
        return 0

    # Append mode (default)
    to_add = []
    for j in jobs:
        url = j.get("url", "")
        if not args.no_dedupe and url in existing_urls:
            continue
        to_add.append([str(j.get(f, "")) for f in FIELDS])

    if not to_add:
        print(f"Nothing to add. All {len(jobs)} jobs are already in Google Sheet.")
        print(f"Open: https://docs.google.com/spreadsheets/d/{sheet_id}/edit")
        return 0

    ws.append_rows(to_add, value_input_option="USER_ENTERED")
    print(f"Added {len(to_add)} new job(s) to '{args.worksheet}' (skipped {len(jobs) - len(to_add)} existing).")
    print(f"Open: https://docs.google.com/spreadsheets/d/{sheet_id}/edit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
