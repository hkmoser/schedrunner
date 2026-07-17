/**
 * balances_sync.gs — push current account balances from the budget sheet into
 * BigQuery so the __APP_NAME_LOWER__'s Balances tab can read them.
 *
 * The sheet is a YNAB-style ledger (Date, Account, Payee, Amount, Type). The
 * current balance of an account is just the running sum of its Amount column.
 * This script computes that per account and (over)writes a small snapshot table
 * the Python sidecar's /balances endpoint reads:
 *     `PROJECT.DATASET.__APP_NAME_LOWER___balances` (account STRING, balance FLOAT64, as_of TIMESTAMP)
 *
 * SETUP (one time):
 *   1. Extensions ▸ Apps Script, paste this file.
 *   2. Services (＋) ▸ add "BigQuery API" (advanced service).
 *   3. Edit the CONFIG block below (PROJECT_ID at minimum).
 *   4. Run `syncBalances` once — approve the BigQuery + Sheets scopes.
 *   5. Run `installTrigger` once — pushes every 15 minutes thereafter.
 *
 * The Apps Script account must have BigQuery write access to the dataset.
 */

// ----------------------------------------------------------------- CONFIG
var CONFIG = {
  PROJECT_ID: 'ecstatic-pod-443723-f6',          // GCP billing project
  DATASET: 'home_afm',                            // BQ dataset
  TABLE: '__APP_NAME_LOWER___balances',                    // snapshot table (created/replaced)
  SHEET_NAME: '',                                 // '' = first/active sheet
  // Only sum rows on/after the most recent budget reset (blank = all rows).
  RESET_DATE: '2024-08-30',
  // Accounts to publish, in display order. Leave [] to publish every account
  // that appears in the ledger.
  ACTIVE_ACCOUNTS: [
    'Bills', 'Spending', 'Savings',
    'Ally Spending', 'Ally Spending Alt', 'Ally Savings',
    'Auto Loan',
  ],
  // Column header names (match your sheet's header row, case-insensitive).
  COL_DATE: 'Date',
  COL_ACCOUNT: 'Account',
  COL_AMOUNT: 'Amount',
};

// ----------------------------------------------------------------- MAIN
function syncBalances() {
  var balances = computeBalances();
  writeToBigQuery(balances);
}

/** Sum the Amount column per account into { account: balance }. */
function computeBalances() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = CONFIG.SHEET_NAME ? ss.getSheetByName(CONFIG.SHEET_NAME) : ss.getSheets()[0];
  if (!sheet) throw new Error('Sheet not found: ' + CONFIG.SHEET_NAME);

  var values = sheet.getDataRange().getValues();
  if (values.length < 2) return {};
  var header = values[0].map(function (h) { return String(h).trim().toLowerCase(); });
  var iDate = header.indexOf(CONFIG.COL_DATE.toLowerCase());
  var iAcct = header.indexOf(CONFIG.COL_ACCOUNT.toLowerCase());
  var iAmt = header.indexOf(CONFIG.COL_AMOUNT.toLowerCase());
  if (iAcct < 0 || iAmt < 0) {
    throw new Error('Missing Account/Amount columns; found header: ' + header.join(', '));
  }

  var reset = CONFIG.RESET_DATE ? new Date(CONFIG.RESET_DATE) : null;
  var active = CONFIG.ACTIVE_ACCOUNTS.length
    ? CONFIG.ACTIVE_ACCOUNTS.map(function (a) { return a.toLowerCase(); })
    : null;

  var sums = {};
  for (var r = 1; r < values.length; r++) {
    var row = values[r];
    var account = String(row[iAcct] || '').trim();
    if (!account) continue;
    if (active && active.indexOf(account.toLowerCase()) < 0) continue;
    if (reset && iDate >= 0 && row[iDate]) {
      var d = (row[iDate] instanceof Date) ? row[iDate] : new Date(row[iDate]);
      if (!isNaN(d.getTime()) && d < reset) continue;
    }
    var amt = parseFloat(String(row[iAmt]).replace(/[$,]/g, ''));
    if (isNaN(amt)) continue;
    sums[account] = (sums[account] || 0) + amt;
  }
  return sums;
}

/** CREATE OR REPLACE the snapshot table with the computed balances. */
function writeToBigQuery(sums) {
  var order = CONFIG.ACTIVE_ACCOUNTS.length ? CONFIG.ACTIVE_ACCOUNTS : Object.keys(sums);
  var structs = [];
  for (var i = 0; i < order.length; i++) {
    var name = order[i];
    if (!(name in sums)) continue;
    var safe = String(name).replace(/'/g, "\\'");
    var bal = Math.round(sums[name] * 100) / 100;
    structs.push("STRUCT('" + safe + "' AS account, " + bal + " AS balance)");
  }

  var fq = '`' + CONFIG.PROJECT_ID + '.' + CONFIG.DATASET + '.' + CONFIG.TABLE + '`';
  var query;
  if (structs.length) {
    query =
      'CREATE OR REPLACE TABLE ' + fq + ' AS\n' +
      'SELECT account, balance, CURRENT_TIMESTAMP() AS as_of FROM UNNEST([\n  ' +
      structs.join(',\n  ') + '\n])';
  } else {
    // No matching rows — leave an empty, well-typed table.
    query =
      'CREATE OR REPLACE TABLE ' + fq +
      ' (account STRING, balance FLOAT64, as_of TIMESTAMP)';
  }

  var job = BigQuery.Jobs.query(
    { query: query, useLegacySql: false },
    CONFIG.PROJECT_ID
  );
  Logger.log('Pushed %s accounts to %s (job %s)', structs.length, fq, job.jobReference.jobId);
}

// ----------------------------------------------------------------- TRIGGER
/** Install a 15-minute time trigger (run once). Removes any prior copy first. */
function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'syncBalances') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('syncBalances').timeBased().everyMinutes(15).create();
  Logger.log('Installed 15-minute syncBalances trigger.');
}
