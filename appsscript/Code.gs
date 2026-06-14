/**
 * Code.gs — entry points + shared helpers for the Electricity Dashboard.
 *
 * Web app:   doGet -> Index.html (graph-first, read-only, anonymous access).
 * Pipeline:  runDailyUpdate -> importUsageFromGmail -> refreshWeather -> computeProjection
 *            (runs on a recurring time trigger; also from the ⚡ Electricity sheet
 *            menu or the dashboard's refresh button).
 * Storage:   tabs Config, DailyUsage, Weather, Projection, Summary on the bound Sheet.
 */

/**
 * How often the recurring trigger runs the pipeline, in hours.
 * Apps Script's everyHours() only accepts 1, 2, 4, 6, 8, or 12 — any other
 * value throws when the trigger is created. SMT email lands once a day (in the
 * evening), so 4h gives ~6 runs/day — enough to catch the nightly email and
 * pick up fresh weather a couple of times during the day without burning
 * Apps Script quota.
 */
var REFRESH_INTERVAL_HOURS = 4;

/**
 * Seed rows for the Cycles tab (the month dropdown's source). One row per billing
 * period: [cycle_key (YYYY-MM, also the dropdown value), label (month name shown
 * to the user), cycle_start, next_read] over the [cycle_start, next_read) window.
 * Add July/August rows here as their real CenterPoint read dates become known —
 * the windows below are conservative estimates. Seeded via appendMissingCycles_,
 * so editing a date in the sheet is never overwritten.
 */
var CYCLE_SEED_ROWS_ = [
  // May: confirmed from the 4Change bill — Billing Period 05/11–06/09/2026 (the
  // 06/09 meter read closes May / opens June; next_read is exclusive, so May
  // counts 05/11–06/08 = 29 days). June chains off that read date with a
  // conservative 28-day window (ends a few days short of the ~07/09 next read to
  // guard against an early read). Correct June's next_read once its bill lands.
  ['2026-05', 'May', '2026-05-11', '2026-06-09'],
  ['2026-06', 'June', '2026-06-09', '2026-07-07']
];

// ─── Entry points ──────────────────────────────────────────────────────────

/**
 * Serve the dashboard HTML to anyone with the link.
 * @return {GoogleAppsScript.HTML.HtmlOutput}
 */
function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('Electricity Dashboard')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

/** Add the sheet menu when the spreadsheet opens. */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('⚡ Electricity')
    .addItem('Refresh now', 'runDailyUpdate')
    .addItem('Repair data', 'repairDailyUsage')
    .addItem('Run setup', 'setup')
    .addToUi();
}

/**
 * One-time setup: create tabs + headers, seed Config (only if empty), set the
 * spreadsheet timezone, and install the daily trigger. Safe to re-run.
 */
function setup() {
  getSpreadsheet_().setSpreadsheetTimeZone('America/Chicago');
  ensureInitialized_();
  installRefreshTrigger_();
  logEvent_('setup_completed', {});
  try { getSpreadsheet_().toast('Setup complete. Use ⚡ Electricity → Refresh now.', 'Electricity Dashboard', 5); } catch (e) {}
}

/**
 * Create the tabs + headers and seed Config with defaults if it is empty.
 * Idempotent and safe to call on every run, so "Refresh now" works even if
 * setup() was never run explicitly.
 */
function ensureInitialized_() {
  var ss = getSpreadsheet_();
  var config = ss.getSheetByName('Config') || ss.insertSheet('Config');
  config.getRange('A:B').setNumberFormat('@');  // keep dates/zip as plain text
  // Merge: seed any missing keys without disturbing user-edited values. This way
  // adding new config keys in code (e.g. bill rate terms) lands them on the next
  // refresh — the empty-Config check was a foot-gun that hid all schema changes.
  appendMissingConfigKeys_({
    zip: '77080',
    // Confirmed from the 4Change bill (Billing Period 05/11–06/09/2026). The
    // Cycles tab is the live source of truth for the dashboard's per-month windows;
    // these Config keys are only a fallback when Cycles is empty.
    cycle_start: '2026-05-11',
    next_read: '2026-06-09',
    threshold_kwh: 1000,
    credit_usd: 125,
    cdd_base_f: 65,
    // Bill rate terms — mirrored from bill/calculator.py DEFAULT_PLAN_TERMS
    // (4Change Maxx Saver Value 12, effective 2026-04-15).
    energy_charge_usd_per_kwh: 0.14611,
    tdu_base_usd_per_cycle: 4.39,
    tdu_charge_usd_per_kwh: 0.0506,
    misc_fee_factor: 0.0205,
    sales_tax_factor: 0.01
  });
  ensureTab_('DailyUsage', ['service_date', 'total_kwh', 'source']);
  ensureTab_('Weather', ['date', 'temp_min_f', 'temp_max_f', 'mean_temp_f', 'precip_in', 'source']);
  ensureTab_('Projection', ['date', 'mean_temp_f', 'cdd', 'actual_kwh', 'predicted_kwh', 'used_kwh', 'cumulative_kwh', 'day_type']);
  // Reason: keep ISO date columns as plain text so setValues never coerces them
  // into date serials. That coercion + a timezone reformat on read-back was
  // shifting each stored date one day earlier per refresh, smearing one day's
  // kWh backward across the whole cycle. Applied every run, so it heals the
  // live sheet too. clearContents (used by writeTable_) preserves the format.
  forceColumnText_('DailyUsage', 1);
  forceColumnText_('Weather', 1);
  forceColumnText_('Projection', 1);
  ensureCyclesSeeded_();
  if (!ss.getSheetByName('Summary')) ss.insertSheet('Summary');
}

/**
 * Full pipeline: import usage from Gmail, refresh weather, recompute projection.
 * Trigger target and menu action. Fails loud so errors surface in the log.
 */
function runDailyUpdate() {
  try {
    ensureInitialized_();
    var importedDays = importUsageFromGmail();
    var weatherDays = refreshWeather();
    var summary = computeProjection();
    logEvent_('daily_update_completed', {
      imported_days: importedDays, weather_days: weatherDays,
      projected_total_kwh: summary.projected_total_kwh, verdict: summary.verdict
    });
    try {
      getSpreadsheet_().toast(summary.projected_total_kwh + ' kWh — ' + summary.verdict, 'Electricity Dashboard', 5);
    } catch (e) {}
    return summary;
  } catch (err) {
    logEvent_('daily_update_failed', {
      error_message: String(err && err.message ? err.message : err),
      fix_suggestion: 'Check Config (zip/dates), Gmail access, and Open-Meteo reachability.'
    });
    throw err;
  }
}

/**
 * One-time repair after the date-shift bug smeared one day's kWh backward across
 * the cycle. Rebuilds DailyUsage from scratch: re-seeds the 5/11–5/18 portal
 * backfill (never delivered by email, so it must be entered manually) as `manual`
 * rows, then imports the email days (which preserves those manual rows) and
 * recomputes. Run once from the editor; safe to re-run. Values come from
 * data/smt/IntervalData_portal_20260511_20260518.CSV.
 * @return {Object} the recomputed summary.
 */
function repairDailyUsage() {
  ensureInitialized_();  // also forces the ISO date columns to plain text
  var portalBackfill = [
    ['2026-05-11', 29.166],
    ['2026-05-12', 37.353],
    ['2026-05-13', 63.541],
    ['2026-05-14', 37.848],
    ['2026-05-15', 33.236],
    ['2026-05-16', 55.833],
    ['2026-05-17', 85.59],
    ['2026-05-18', 40.766]
  ];
  var rows = portalBackfill.map(function (day) { return [day[0], day[1], 'manual']; });
  writeTable_('DailyUsage', ['service_date', 'total_kwh', 'source'], rows);
  var importedDays = importUsageFromGmail();
  var summary = computeProjection();
  logEvent_('repair_daily_usage_completed', {
    seeded_manual_days: portalBackfill.length, imported_days: importedDays,
    projected_total_kwh: summary.projected_total_kwh, verdict: summary.verdict
  });
  return summary;
}

/**
 * Shape the chart series for one billing cycle (the month dropdown's selection).
 * Computes the projection live for the requested cycle from DailyUsage + Weather,
 * so any month renders without a persisted per-cycle tab. Actual cumulative stops
 * at the last metered day; projected cumulative starts there (bridged) and runs to
 * cycle end so the two lines connect.
 * @param {string} [cycleKey] Cycle to render (YYYY-MM). Defaults to the latest.
 * @return {Object} dashboard payload for Index.html.
 */
function getDashboardData(cycleKey) {
  ensureCyclesSeeded_();
  var config = readConfig_();
  var cycles = readCycles_();
  var cycle = findCycleByKey_(cycles, cycleKey) || resolveLatestCycle_(cycles);
  if (!cycle) {
    return {
      labels: [], actualCumulative: [], projectedCumulative: [], tempMin: [], tempMax: [], rain: [],
      threshold: Number(config.threshold_kwh || 1000), creditUsd: Number(config.credit_usd || 125),
      projectedTotal: 0, projectedTotalUsd: 0, daysElapsed: 0, dailyAvgKwh: 0, margin: 0,
      verdict: '', creditSafe: false, lastUpdated: '', cycleKey: '', cycleLabel: ''
    };
  }

  var computed = computeProjectionForWindow_(config, cycle.cycle_start, cycle.next_read);
  var summary = computed.summary;
  var projectionRows = computed.rows;
  var weatherMap = readWeatherMap_();

  // Pivot = last day with a real meter reading. Everything up to and including it
  // is the solid "so far" line (gaps bridged); only days AFTER it are the dashed
  // projection. This keeps the dashed line strictly in the future, even when the
  // early cycle days were never metered (start-of-cycle gap).
  var lastActualIndex = -1;
  projectionRows.forEach(function (row, i) {
    if (String(row.day_type || '') === 'actual') lastActualIndex = i;
  });

  var labels = [], actual = [], projected = [], tempMin = [], tempMax = [], rain = [];
  projectionRows.forEach(function (row, i) {
    var iso = normalizeDateCell_(row.date);
    labels.push(formatLabel_(iso));

    var cumulative = (row.cumulative_kwh === '' || row.cumulative_kwh == null) ? null : Number(row.cumulative_kwh);
    var isMissing = String(row.day_type || '') === 'missing';
    if (i <= lastActualIndex) {
      // Past: one continuous solid line. Missing days are left null so the line
      // bridges straight across them (spanGaps) rather than dropping to a gap.
      actual.push(isMissing ? null : cumulative);
      projected.push(null);
    } else {
      actual.push(null);
      projected.push(cumulative);  // future only
    }

    var w = weatherMap[iso];
    tempMin.push(w ? round1_(w.min) : null);
    tempMax.push(w ? round1_(w.max) : null);
    // Drop a rain marker only on real near-term forecast days. Climatology normals
    // (far-future fill) average to a little rain on almost every humid Houston day,
    // which would smear drops across the whole tail and kill the signal.
    var hasRain = w && w.source === 'forecast' && w.precip >= 0.01;
    rain.push(hasRain ? round2_(w.precip) : null);
  });
  if (lastActualIndex >= 0 && lastActualIndex < projected.length) {
    projected[lastActualIndex] = actual[lastActualIndex];  // bridge the two lines
  }

  var creditSafe = summary.credit_safe === true || String(summary.credit_safe).toLowerCase() === 'true';
  return {
    labels: labels,
    actualCumulative: actual,
    projectedCumulative: projected,
    tempMin: tempMin,
    tempMax: tempMax,
    rain: rain,
    threshold: Number(config.threshold_kwh || 1000),
    creditUsd: Number(config.credit_usd || 125),
    projectedTotal: Number(summary.projected_total_kwh || 0),
    projectedTotalUsd: Number(summary.projected_total_usd || 0),
    daysElapsed: Number(summary.days_elapsed || 0),
    dailyAvgKwh: Number(summary.daily_avg_kwh || 0),
    margin: Number(summary.margin_vs_1000 || 0),
    verdict: String(summary.verdict || ''),
    creditSafe: creditSafe,
    lastUpdated: String(summary.last_updated || ''),
    cycleKey: cycle.cycle_key,
    cycleLabel: cycle.label
  };
}

// ─── Triggers ────────────────────────────────────────────────────────────────

/**
 * Reconcile the recurring trigger for runDailyUpdate: delete any existing ones,
 * then (re)install at REFRESH_INTERVAL_HOURS. Reconciling rather than
 * "create only if missing" means re-running setup() actually changes the
 * interval (e.g. migrating an older once-daily trigger to every few hours).
 */
function installRefreshTrigger_() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'runDailyUpdate') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('runDailyUpdate').timeBased().everyHours(REFRESH_INTERVAL_HOURS).create();
}

// ─── Sheet helpers ────────────────────────────────────────────────────────────

/**
 * Resolve the spreadsheet: the bound container, else a SHEET_ID script property.
 * @return {GoogleAppsScript.Spreadsheet.Spreadsheet}
 */
function getSpreadsheet_() {
  var ss = SpreadsheetApp.getActive();
  if (ss) return ss;
  var id = PropertiesService.getScriptProperties().getProperty('SHEET_ID');
  if (id) return SpreadsheetApp.openById(id);
  throw new Error('No spreadsheet found. Bind the script to a Sheet, or set the SHEET_ID script property.');
}

/**
 * Get a sheet by name, creating it if missing.
 * @param {string} name
 * @return {GoogleAppsScript.Spreadsheet.Sheet}
 */
function getOrCreateSheet_(name) {
  var ss = getSpreadsheet_();
  return ss.getSheetByName(name) || ss.insertSheet(name);
}

/** Create a tab with headers only if it does not already exist. */
function ensureTab_(name, headers) {
  var ss = getSpreadsheet_();
  if (!ss.getSheetByName(name)) {
    ss.insertSheet(name).getRange(1, 1, 1, headers.length).setValues([headers]);
  }
}

/**
 * Force an entire column to plain-text number format ('@') so values written
 * there are never reinterpreted (e.g. ISO date strings into date serials).
 * @param {string} name Sheet/tab name.
 * @param {number} columnIndex 1-based column index.
 */
function forceColumnText_(name, columnIndex) {
  var sheet = getOrCreateSheet_(name);
  sheet.getRange(1, columnIndex, sheet.getMaxRows(), 1).setNumberFormat('@');
}

/**
 * Read a header-row table into objects keyed by column header.
 * @param {string} name
 * @return {{headers: string[], rows: Array<Object>}}
 */
function readTable_(name) {
  var sheet = getOrCreateSheet_(name);
  var values = sheet.getDataRange().getValues();
  if (!values.length) return { headers: [], rows: [] };
  var headers = values[0].map(function (h) { return String(h).trim(); });
  var rows = [];
  for (var i = 1; i < values.length; i++) {
    var obj = {};
    var allBlank = true;
    for (var c = 0; c < headers.length; c++) {
      obj[headers[c]] = values[i][c];
      if (values[i][c] !== '' && values[i][c] !== null) allBlank = false;
    }
    if (!allBlank) rows.push(obj);
  }
  return { headers: headers, rows: rows };
}

/**
 * Replace a tab's contents with a header row + matrix of rows.
 * @param {string} name
 * @param {string[]} headers
 * @param {Array<Array>} matrix
 */
function writeTable_(name, headers, matrix) {
  var sheet = getOrCreateSheet_(name);
  sheet.clearContents();
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  if (matrix.length) {
    sheet.getRange(2, 1, matrix.length, headers.length).setValues(matrix);
  }
}

/**
 * Write an object as a two-column key/value sheet.
 * @param {string} name
 * @param {Object} obj
 */
function writeKeyValues_(name, obj) {
  var sheet = getOrCreateSheet_(name);
  sheet.clearContents();
  var rows = [['key', 'value']];
  Object.keys(obj).forEach(function (key) { rows.push([key, obj[key]]); });
  sheet.getRange(1, 1, rows.length, 2).setValues(rows);
}

/**
 * Append any keys from `defaults` that aren't already present in the Config
 * sheet. Existing keys (and any user-edited values) are left untouched. Writes
 * the header row if the sheet is empty.
 * @param {Object} defaults
 */
function appendMissingConfigKeys_(defaults) {
  var sheet = getOrCreateSheet_('Config');
  var values = sheet.getDataRange().getValues();
  var present = {};
  var hasHeader = false;
  for (var i = 0; i < values.length; i++) {
    var key = String(values[i][0] || '').trim();
    if (!key) continue;
    if (key.toLowerCase() === 'key') { hasHeader = true; continue; }
    present[key] = true;
  }
  var toAppend = [];
  if (!hasHeader && values.length === 0) toAppend.push(['key', 'value']);
  Object.keys(defaults).forEach(function (k) {
    if (!present[k]) toAppend.push([k, defaults[k]]);
  });
  if (!toAppend.length) return;
  var startRow = sheet.getLastRow() + 1;
  sheet.getRange(startRow, 1, toAppend.length, 2).setValues(toAppend);
}

/**
 * Append any cycles whose cycle_key isn't already present in the Cycles tab.
 * Existing rows (and any hand-corrected read dates) are left untouched, so
 * seeding new months in code never clobbers user edits.
 * @param {Array<Array>} defaults Rows of [cycle_key, label, cycle_start, next_read].
 */
function appendMissingCycles_(defaults) {
  var sheet = getOrCreateSheet_('Cycles');
  var values = sheet.getDataRange().getValues();
  var present = {};
  for (var i = 0; i < values.length; i++) {
    var key = String(values[i][0] || '').trim();
    if (!key || key.toLowerCase() === 'cycle_key') continue;
    present[key] = true;
  }
  var toAppend = defaults.filter(function (row) { return !present[String(row[0])]; });
  if (!toAppend.length) return;
  var startRow = sheet.getLastRow() + 1;
  sheet.getRange(startRow, 1, toAppend.length, 4).setValues(toAppend);
}

/**
 * Read a two-column key/value sheet into an object (skips the header row).
 * @param {string} name
 * @return {Object}
 */
function readKeyValues_(name) {
  var sheet = getOrCreateSheet_(name);
  var values = sheet.getDataRange().getValues();
  var out = {};
  for (var i = 0; i < values.length; i++) {
    var key = String(values[i][0] || '').trim();
    if (!key || key.toLowerCase() === 'key') continue;
    out[key] = values[i][1];
  }
  return out;
}

/** @return {Object} the Config tab as an object. */
function readConfig_() { return readKeyValues_('Config'); }

// ─── Cycles (billing periods) ───────────────────────────────────────────────

/**
 * Ensure the Cycles tab exists and is seeded with CYCLE_SEED_ROWS_. Cheap once
 * populated (appendMissingCycles_ writes nothing when all keys are present), so
 * it's safe to call on the web-app read path — this guarantees the month dropdown
 * shows May + June (and defaults to the latest) on the very first dashboard load,
 * before any Refresh/trigger has run.
 */
function ensureCyclesSeeded_() {
  var existed = !!getSpreadsheet_().getSheetByName('Cycles');
  ensureTab_('Cycles', ['cycle_key', 'label', 'cycle_start', 'next_read']);
  if (!existed) {
    // Force the key + date columns to plain text before seeding so ISO strings
    // aren't coerced into date serials (same guard as the other dated tabs).
    forceColumnText_('Cycles', 1);
    forceColumnText_('Cycles', 3);
    forceColumnText_('Cycles', 4);
  }
  appendMissingCycles_(CYCLE_SEED_ROWS_);
  migrateSeededCycleDates_();
}

/**
 * One-time self-heal for the two cycle rows first seeded with ESTIMATED dates,
 * before the 4Change bill confirmed the real read date (Billing Period
 * 05/11–06/09/2026). Updates a row's window ONLY if it still matches the
 * known-stale estimate, so a genuine hand-edit is never overwritten and re-running
 * is a no-op once corrected.
 */
function migrateSeededCycleDates_() {
  var corrections = {
    '2026-05': { from: ['2026-05-10', '2026-06-07'], to: ['2026-05-11', '2026-06-09'] },
    '2026-06': { from: ['2026-06-07', '2026-07-05'], to: ['2026-06-09', '2026-07-07'] }
  };
  var sheet = getOrCreateSheet_('Cycles');
  var values = sheet.getDataRange().getValues();
  for (var i = 1; i < values.length; i++) {  // row 0 = header
    var key = String(values[i][0] || '').trim();
    var corr = corrections[key];
    if (!corr) continue;
    var start = normalizeDateCell_(values[i][2]);     // col C = cycle_start
    var nextRead = normalizeDateCell_(values[i][3]);  // col D = next_read
    if (start === corr.from[0] && nextRead === corr.from[1]) {
      sheet.getRange(i + 1, 3).setValue(corr.to[0]);
      sheet.getRange(i + 1, 4).setValue(corr.to[1]);
      logEvent_('cycle_dates_migrated', { cycle_key: key, from: corr.from.join('..'), to: corr.to.join('..') });
    }
  }
}

/**
 * Read the Cycles tab into a list of billing periods sorted by start date.
 * Falls back to a single cycle synthesized from Config (cycle_start/next_read)
 * when the tab is empty, so the dashboard keeps working pre-migration.
 * @return {Array<{cycle_key: string, label: string, cycle_start: string, next_read: string}>}
 */
function readCycles_() {
  var table = readTable_('Cycles');
  var cycles = table.rows.map(function (row) {
    return {
      cycle_key: String(row.cycle_key || '').trim(),
      label: String(row.label || '').trim(),
      cycle_start: normalizeDateCell_(row.cycle_start),
      next_read: normalizeDateCell_(row.next_read)
    };
  }).filter(function (c) { return c.cycle_key && c.cycle_start && c.next_read; });

  if (!cycles.length) {
    var cfg = readConfig_();
    var startIso = normalizeDateCell_(cfg.cycle_start);
    var nextReadIso = normalizeDateCell_(cfg.next_read);
    if (startIso && nextReadIso) {
      cycles.push({ cycle_key: startIso.slice(0, 7), label: monthLabel_(startIso), cycle_start: startIso, next_read: nextReadIso });
    }
  }

  cycles.sort(function (a, b) { return a.cycle_start < b.cycle_start ? -1 : (a.cycle_start > b.cycle_start ? 1 : 0); });
  return cycles;
}

/** @return {string} full month name for an ISO date, e.g. "June". */
function monthLabel_(iso) {
  var p = parseISO_(iso);
  return Utilities.formatDate(new Date(Date.UTC(p.y, p.m - 1, p.d)), 'UTC', 'MMMM');
}

/**
 * Pick the "latest" cycle for the default view: the one whose window contains
 * today, else the most recent that has already started, else the earliest.
 * @param {Array<Object>} cycles Output of readCycles_().
 * @return {Object|null}
 */
function resolveLatestCycle_(cycles) {
  if (!cycles.length) return null;
  var today = todayIso_();
  for (var i = 0; i < cycles.length; i++) {
    if (cycles[i].cycle_start <= today && today < cycles[i].next_read) return cycles[i];
  }
  for (var j = cycles.length - 1; j >= 0; j--) {
    if (cycles[j].cycle_start <= today) return cycles[j];
  }
  return cycles[0];
}

/** @return {Object|null} cycle with the given key, or null. */
function findCycleByKey_(cycles, cycleKey) {
  if (!cycleKey) return null;
  for (var i = 0; i < cycles.length; i++) {
    if (cycles[i].cycle_key === cycleKey) return cycles[i];
  }
  return null;
}

/**
 * Web-exposed: list cycles for the dashboard's month dropdown + which one is the
 * default (latest). Sorted newest-last in `cycles`; the client reverses to show
 * the latest month at the top.
 * @return {{cycles: Array<{key: string, label: string}>, latestKey: string}}
 */
function listCycles() {
  ensureCyclesSeeded_();
  var cycles = readCycles_();
  var latest = resolveLatestCycle_(cycles);
  return {
    cycles: cycles.map(function (c) { return { key: c.cycle_key, label: c.label }; }),
    latestKey: latest ? latest.cycle_key : ''
  };
}

// ─── Date + number utilities ──────────────────────────────────────────────────

/** @return {{y: number, m: number, d: number}} */
function parseISO_(iso) {
  var p = String(iso).split('-');
  return { y: Number(p[0]), m: Number(p[1]), d: Number(p[2]) };
}

/** Add n days to an ISO date (UTC math, DST-safe). @return {string} */
function isoAddDays_(iso, n) {
  var p = parseISO_(iso);
  var d = new Date(Date.UTC(p.y, p.m - 1, p.d));
  d.setUTCDate(d.getUTCDate() + n);
  return Utilities.formatDate(d, 'UTC', 'yyyy-MM-dd');
}

/** @return {number} whole days from ISO a to ISO b (b - a). */
function isoDiffDays_(a, b) {
  var pa = parseISO_(a), pb = parseISO_(b);
  return Math.round((Date.UTC(pb.y, pb.m - 1, pb.d) - Date.UTC(pa.y, pa.m - 1, pa.d)) / 86400000);
}

/** Cycle days [start, nextRead) as ISO strings. @return {string[]} */
function cycleDatesIso_(startIso, nextReadIso) {
  var n = isoDiffDays_(startIso, nextReadIso);
  var out = [];
  for (var i = 0; i < n; i++) out.push(isoAddDays_(startIso, i));
  return out;
}

/** @return {string} today's ISO date in the script timezone. */
function todayIso_() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
}

/**
 * Normalize a cell value (Date, ISO string, or MM/DD/YYYY) to "YYYY-MM-DD".
 * @param {*} v
 * @return {string}
 */
function normalizeDateCell_(v) {
  if (v === null || v === undefined || v === '') return '';
  if (Object.prototype.toString.call(v) === '[object Date]') {
    return Utilities.formatDate(v, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  }
  var s = String(v).trim();
  var m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return m[1] + '-' + m[2] + '-' + m[3];
  return parseUsageDateIso_(s) || s;
}

/** Format an ISO date as a short chart label, e.g. "May 11". @return {string} */
function formatLabel_(iso) {
  if (!iso) return '';
  var p = parseISO_(iso);
  return Utilities.formatDate(new Date(Date.UTC(p.y, p.m - 1, p.d)), 'UTC', 'MMM d');
}

/** Round to 1 decimal place. @return {number} */
function round1_(x) { return Math.round(Number(x) * 10) / 10; }

/** Round to 2 decimal places (precipitation inches). @return {number} */
function round2_(x) { return Math.round(Number(x) * 100) / 100; }

/** Round to 1 dp, or '' for null. @return {(number|string)} */
function blankOr1_(x) { return x === null || x === undefined ? '' : round1_(x); }

/** @return {string} zero-padded 2-digit. */
function pad2_(n) { return (n < 10 ? '0' : '') + n; }

/** @return {string} zero-padded 4-digit year. */
function pad4_(n) { return ('000' + n).slice(-4); }

/** Structured JSON log to Stackdriver/console. */
function logEvent_(event, fields) {
  var payload = { event: event };
  if (fields) Object.keys(fields).forEach(function (k) { payload[k] = fields[k]; });
  console.log(JSON.stringify(payload));
}
