/**
 * OnlineJobs.ph → Google Sheets
 * =============================
 *
 * The receiving end of sheet_sync.py. Accepts a POST like:
 *
 *     { "run_at": "2026-09-13T08:17:04Z",
 *       "rows": [ { "job_id": "1729025", "title": "...", ... } ] }
 *
 * and appends the rows whose job_id isn't already in the sheet. Sending a row
 * twice does nothing the second time — which is what lets the GitHub Action
 * re-send its newest 60 rows every run and repair anything that failed.
 *
 * It also sends a Telegram message when rows are actually added, and stays
 * silent otherwise. Both are optional.
 *
 * SETUP (once, about three minutes)
 * ------------------------------------------------------------------------
 *   1. Open your Google Sheet → Extensions → Apps Script.
 *   2. Delete the stub function, paste this whole file, click Save.
 *   3. Click the gear (Project Settings) → Script properties → Add property:
 *
 *        WEBHOOK_TOKEN        a long random string   (recommended)
 *        TELEGRAM_BOT_TOKEN   from @BotFather        (optional)
 *        TELEGRAM_CHAT_ID     your numeric chat id   (optional)
 *
 *   4. Deploy → New deployment → Web app.
 *        Execute as:      Me
 *        Who has access:  Anyone
 *      "Anyone" is required and is safe here: it means the URL is reachable
 *      without a Google login, and the URL carries the token.
 *
 *   5. Copy the /exec URL and put it in GitHub secrets as SHEETS_WEBHOOK_URL,
 *      and the token as SHEETS_WEBHOOK_TOKEN. See CLOUD.md.
 *
 *   6. Open the /exec URL in a browser once. You should see a small JSON
 *      status block — that confirms the deployment is live before you rely
 *      on it from the workflow.
 *
 * RE-DEPLOYING: after editing this file, Deploy → Manage deployments → edit
 * the existing one → Version: New version. A brand-new deployment gives you a
 * different URL and the old one keeps serving the old code.
 */

/** Tab name in the spreadsheet. Created if missing. */
var SHEET_NAME = 'Jobs';

/** Written to row 1 and used as the append shape. */
var HEADERS = [
  'Job ID',
  'Title',
  'Wage',
  'Type',
  'Posted',
  'Found At',
  'Tags',
  'URL',
  'Description'
];

/**
 * Google Sheets hard-rejects a cell over 50,000 characters, and appendRow
 * throws rather than truncating — which would lose the whole batch. Job
 * overviews are long (some run to tens of thousands of characters), so they
 * get clipped with a marker. The untruncated text is always in the repo's
 * jobs.xlsx.
 */
var MAX_CELL = 49000;


function doPost(e) {
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(30000);
  } catch (err) {
    return json({ ok: false, error: 'busy — another run is writing' });
  }

  try {
    var props = PropertiesService.getScriptProperties();
    var expected = props.getProperty('WEBHOOK_TOKEN');

    // Apps Script cannot read request headers, so the token arrives as a
    // query parameter rather than an X-Webhook-Token header.
    var got = (e && e.parameter) ? e.parameter.token : null;
    if (expected && got !== expected) {
      return json({ ok: false, error: 'bad or missing token' });
    }

    var body = JSON.parse(e.postData.contents);
    var incoming = body.rows || [];
    if (!incoming.length) {
      return json({ ok: true, added: 0, skipped: 0, total: 0 });
    }

    var sheet = getSheet_();
    var known = existingIds_(sheet);
    var now = body.run_at || new Date().toISOString();

    var fresh = [];
    for (var i = 0; i < incoming.length; i++) {
      var row = incoming[i];
      var id = String(row.job_id || '');
      if (!id || known[id]) continue;
      known[id] = true;
      fresh.push(row);
    }

    if (!fresh.length) {
      return json({ ok: true, added: 0, skipped: incoming.length, total: sheet.getLastRow() - 1 });
    }

    var values = [];
    for (var j = 0; j < fresh.length; j++) {
      values.push(toRow_(fresh[j], now));
    }
    sheet.getRange(sheet.getLastRow() + 1, 1, values.length, HEADERS.length)
         .setValues(values);

    notify_(fresh, now);

    return json({
      ok: true,
      added: fresh.length,
      skipped: incoming.length - fresh.length,
      total: sheet.getLastRow() - 1
    });

  } catch (err) {
    return json({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}


/** Opening the /exec URL in a browser lands here — a liveness check. */
function doGet() {
  var sheet = getSheet_();
  return json({
    ok: true,
    message: 'OnlineJobs.ph sink is live',
    sheet: sheet.getName(),
    rows: Math.max(sheet.getLastRow() - 1, 0),
    telegram: PropertiesService.getScriptProperties().getProperty('TELEGRAM_BOT_TOKEN')
      ? 'configured' : 'not configured'
  });
}


/** The sheet to append to, with a header row guaranteed on row 1. */
function getSheet_() {
  var props = PropertiesService.getScriptProperties();
  var id = props.getProperty('SPREADSHEET_ID');
  var ss = id ? SpreadsheetApp.openById(id) : SpreadsheetApp.getActiveSpreadsheet();
  if (!ss) {
    throw new Error('No spreadsheet. Bind this script to a sheet, or set the '
      + 'SPREADSHEET_ID script property.');
  }

  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) sheet = ss.insertSheet(SHEET_NAME);

  if (sheet.getLastRow() === 0) {
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
  } else {
    var head = sheet.getRange(1, 1, 1, HEADERS.length).getValues()[0];
    if (String(head[0]) !== HEADERS[0]) {
      throw new Error('Row 1 of "' + SHEET_NAME + '" is not the expected header. '
        + 'Move that tab aside and re-run, or fix the header by hand.');
    }
  }
  return sheet;
}


/** Set of every job_id already in column A. */
function existingIds_(sheet) {
  var known = {};
  var last = sheet.getLastRow();
  if (last < 2) return known;

  var ids = sheet.getRange(2, 1, last - 1, 1).getValues();
  for (var i = 0; i < ids.length; i++) {
    var v = ids[i][0];
    if (v !== '' && v !== null) known[String(v)] = true;
  }
  return known;
}


/** One job → one row of values, in HEADERS order. */
function toRow_(job, now) {
  return [
    String(job.job_id || ''),
    clip_(job.title, 900),
    clip_(job.wage, 900),
    clip_(job.job_type, 900),
    clip_(job.posted, 900),
    clip_(job.found_at || now, 900),
    clip_(job.tags, 4000),
    clip_(job.url, 900),
    clip_(job.description, MAX_CELL)
  ];
}


function clip_(value, max) {
  var s = (value === null || value === undefined) ? '' : String(value);
  if (s.length <= max) return s;
  return s.substring(0, max - 40) + '\n\n[truncated — full text in jobs.xlsx]';
}


/** Telegram, only when rows were actually added. Silent if unconfigured. */
function notify_(fresh, now) {
  var props = PropertiesService.getScriptProperties();
  var token = props.getProperty('TELEGRAM_BOT_TOKEN');
  var chat = props.getProperty('TELEGRAM_CHAT_ID');
  if (!token || !chat) return;

  var lines = [fresh.length + ' new job(s) on OnlineJobs.ph', ''];
  for (var i = 0; i < Math.min(fresh.length, 15); i++) {
    var j = fresh[i];
    lines.push('• ' + (j.title || '(untitled)'));
    lines.push('  ' + (j.wage || 'wage not stated'));
    lines.push('  ' + (j.url || ''));
    lines.push('');
  }
  if (fresh.length > 15) {
    lines.push('… and ' + (fresh.length - 15) + ' more in the sheet.');
  }

  try {
    UrlFetchApp.fetch('https://api.telegram.org/bot' + token + '/sendMessage', {
      method: 'post',
      payload: { chat_id: chat, text: lines.join('\n') },
      muteHttpExceptions: true
    });
  } catch (err) {
    // A failed alert must never fail the sync — the rows are already written.
    console.error('telegram failed: ' + err);
  }
}


function json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
