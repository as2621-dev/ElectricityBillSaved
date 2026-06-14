/**
 * Projection.gs — weather-driven cycle kWh projection + the 1000 kWh credit cliff.
 *
 * Faithful port of project_usage.py:40-150. Model:
 *   kWh/day ≈ baseload + beta · CDD,  where CDD = max(0, (tmin+tmax)/2 − cdd_base_f)
 * Fit by ordinary-least-squares on days that have BOTH metered usage and weather.
 * For each cycle day: use the metered value if present, else predict from weather.
 * Sum to a projected total, compare to threshold_kwh (1000), classify the verdict.
 */

/**
 * Ordinary-least-squares fit of kWh = baseload + beta * CDD.
 *
 * @param {number[]} cdds Cooling-degree-days per day (regressor).
 * @param {number[]} kwhs Metered kWh per day (response), aligned with cdds.
 * @return {{baseload: number, beta: number, r2: number}} beta is kWh per CDD.
 */
function fitUsageModel_(cdds, kwhs) {
  var n = cdds.length;
  var sumX = 0, sumY = 0, sumXX = 0, sumXY = 0;
  for (var i = 0; i < n; i++) {
    sumX += cdds[i];
    sumY += kwhs[i];
    sumXX += cdds[i] * cdds[i];
    sumXY += cdds[i] * kwhs[i];
  }
  var denom = n * sumXX - sumX * sumX;
  if (n < 2 || denom === 0) {
    return { baseload: n ? sumY / n : 0, beta: 0, r2: 0 };
  }
  var beta = (n * sumXY - sumX * sumY) / denom;
  var baseload = (sumY - beta * sumX) / n;
  var meanY = sumY / n;
  var ssTot = 0, ssRes = 0;
  for (var j = 0; j < n; j++) {
    ssTot += Math.pow(kwhs[j] - meanY, 2);
    var predicted = baseload + beta * cdds[j];
    ssRes += Math.pow(kwhs[j] - predicted, 2);
  }
  var r2 = ssTot > 0 ? 1 - ssRes / ssTot : 0;
  return { baseload: baseload, beta: beta, r2: r2 };
}

/**
 * Read DailyUsage tab into a map of service_date (ISO) -> total_kwh.
 * @return {Object<string, number>}
 */
function readUsageMap_() {
  var table = readTable_('DailyUsage');
  var map = {};
  table.rows.forEach(function (row) {
    var iso = normalizeDateCell_(row.service_date);
    if (!iso) return;
    var kwh = Number(row.total_kwh);
    if (!isNaN(kwh)) map[iso] = kwh;
  });
  return map;
}

/**
 * Read Weather tab into a map of date (ISO) -> {min, max, precip, source}.
 * @return {Object<string, {min: number, max: number, precip: number, source: string}>}
 */
function readWeatherMap_() {
  var table = readTable_('Weather');
  var map = {};
  table.rows.forEach(function (row) {
    var iso = normalizeDateCell_(row.date);
    if (!iso) return;
    var min = Number(row.temp_min_f);
    var max = Number(row.temp_max_f);
    if (isNaN(min) || isNaN(max)) return;
    var precip = Number(row.precip_in);
    map[iso] = { min: min, max: max, precip: isNaN(precip) ? 0 : precip, source: String(row.source || 'forecast') };
  });
  return map;
}

/**
 * Project a full-cycle bill in USD for a given total kWh. Faithful port of
 * bill/calculator.py:compute_bill — REP energy + TDU (fixed + per-kWh)
 * − bill credit (cliff at threshold) + misc fees + sales tax.
 *
 * @param {number} totalKwh Projected cycle kWh.
 * @param {Object} cfg Config map (already read).
 * @return {number} Projected bill total, USD.
 */
function projectBillUsd_(totalKwh, cfg) {
  var energyRate = Number(cfg.energy_charge_usd_per_kwh || 0.14611);
  var tduBase = Number(cfg.tdu_base_usd_per_cycle || 4.39);
  var tduRate = Number(cfg.tdu_charge_usd_per_kwh || 0.0506);
  var creditUsd = Number(cfg.credit_usd || 125);
  var threshold = Number(cfg.threshold_kwh || 1000);
  var miscFactor = Number(cfg.misc_fee_factor || 0.0205);
  var taxFactor = Number(cfg.sales_tax_factor || 0.01);

  var energy = energyRate * totalKwh;
  var credit = totalKwh >= threshold ? -creditUsd : 0;
  var tdu = tduBase + tduRate * totalKwh;
  var preMisc = energy + credit + tdu;
  var misc = preMisc * miscFactor;
  var tax = (preMisc + misc) * taxFactor;
  return preMisc + misc + tax;
}

/**
 * Average of the most recent up-to-n metered days. Safety fallback when the
 * temperature model is unreliable (too few points or a non-physical slope).
 * @param {Object<string, number>} usageMap
 * @param {string[]} meteredDatesIso
 * @param {number} n
 * @return {number}
 */
function trailingAverage_(usageMap, meteredDatesIso, n) {
  if (!meteredDatesIso.length) return 0;
  var sorted = meteredDatesIso.slice().sort();
  var recent = sorted.slice(-n);
  var sum = 0;
  recent.forEach(function (iso) { sum += usageMap[iso]; });
  return sum / recent.length;
}

/**
 * Compute the projection for one billing window WITHOUT writing any tabs. Pure
 * given Config + DailyUsage + Weather, so the web app can render any month on
 * demand (getDashboardData) while computeProjection() persists the latest one.
 *
 * @param {Object} cfg Config map (already read).
 * @param {string} startIso Cycle start, ISO "YYYY-MM-DD" (inclusive).
 * @param {string} nextReadIso Estimated next read, ISO (exclusive end).
 * @return {{summary: Object, rows: Array<Object>}} Summary key/values + one row
 *   object per cycle day, keyed by the Projection headers.
 */
function computeProjectionForWindow_(cfg, startIso, nextReadIso) {
  var cddBase = Number(cfg.cdd_base_f || 65);
  var threshold = Number(cfg.threshold_kwh || 1000);

  var dates = cycleDatesIso_(startIso, nextReadIso);
  var usageMap = readUsageMap_();
  var weatherMap = readWeatherMap_();

  function meanTemp(iso) {
    var w = weatherMap[iso];
    return w ? (w.min + w.max) / 2 : null;
  }
  function cdd(iso) {
    var t = meanTemp(iso);
    return t === null ? null : Math.max(0, t - cddBase);
  }

  // Fit on days with BOTH metered usage and weather (matches project_usage.py).
  var fitCdds = [], fitKwhs = [];
  dates.forEach(function (iso) {
    if (usageMap[iso] != null && cdd(iso) !== null) {
      fitCdds.push(cdd(iso));
      fitKwhs.push(usageMap[iso]);
    }
  });
  var model = fitUsageModel_(fitCdds, fitKwhs);

  var meteredDates = dates.filter(function (iso) { return usageMap[iso] != null; });
  var fallbackAvg = trailingAverage_(usageMap, meteredDates, 7);
  var useFallback = fitCdds.length < 3 || model.beta <= 0;

  function predict(iso) {
    if (useFallback) return fallbackAvg;
    var c = cdd(iso);
    return c === null ? null : Math.max(0, model.baseload + model.beta * c);
  }

  // Walk the cycle: metered where known, else modeled.
  var rows = [];
  var actualSum = 0, modeledSum = 0, cumulative = 0;
  var lastActualIso = null;
  var actualDays = 0;

  dates.forEach(function (iso) {
    var t = meanTemp(iso);
    var c = cdd(iso);
    var actual = usageMap[iso];

    if (actual != null) {
      actualSum += actual;
      cumulative += actual;
      lastActualIso = iso;
      actualDays += 1;
      rows.push({ date: iso, mean_temp_f: blankOr1_(t), cdd: blankOr1_(c), actual_kwh: round1_(actual),
        predicted_kwh: '', used_kwh: round1_(actual), cumulative_kwh: round1_(cumulative), day_type: 'actual' });
      return;
    }

    var predicted = predict(iso);
    if (predicted === null) {
      // No weather for this day and not in fallback mode — exclude from the total.
      rows.push({ date: iso, mean_temp_f: blankOr1_(t), cdd: blankOr1_(c), actual_kwh: '',
        predicted_kwh: '', used_kwh: '', cumulative_kwh: round1_(cumulative), day_type: 'missing' });
      return;
    }
    modeledSum += predicted;
    cumulative += predicted;
    var dayType = weatherMap[iso] ? weatherMap[iso].source : 'forecast';
    rows.push({ date: iso, mean_temp_f: blankOr1_(t), cdd: blankOr1_(c), actual_kwh: '',
      predicted_kwh: round1_(predicted), used_kwh: round1_(predicted), cumulative_kwh: round1_(cumulative), day_type: dayType });
  });

  var total = Math.round(actualSum + modeledSum);
  var margin = Math.round(total - threshold);
  var creditSafe = total >= threshold;
  var verdict;
  if (total >= threshold + 60) verdict = 'LIKELY CLEAR';
  else if (total >= threshold) verdict = 'CLOSE';
  else if (total >= threshold - 75) verdict = 'AT RISK';
  else verdict = 'MISS';

  var projectedBillUsd = projectBillUsd_(total, cfg);
  var dailyAvgKwh = actualDays > 0 ? actualSum / actualDays : 0;

  var summary = {
    projected_total_kwh: total,
    projected_total_usd: Math.round(projectedBillUsd * 100) / 100,
    margin_vs_1000: margin,
    verdict: verdict,
    credit_safe: creditSafe,
    last_actual_date: lastActualIso || '',
    days_elapsed: actualDays,
    daily_avg_kwh: round1_(dailyAvgKwh),
    baseload: round1_(model.baseload),
    beta: Math.round(model.beta * 100) / 100,
    r_squared: Math.round(model.r2 * 100) / 100,
    fit_days: fitCdds.length,
    model_note: useFallback ? 'trailing-7-day average (model unreliable)' : 'OLS baseload + beta*CDD',
    metered_kwh: round1_(actualSum),
    modeled_kwh: round1_(modeledSum),
    last_updated: Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyy-MM-dd HH:mm 'CT'")
  };

  return { summary: summary, rows: rows };
}

/**
 * Compute + persist the projection for the LATEST billing cycle. Trigger + menu
 * entry point. Writes the Projection + Summary tabs so the sheet view shows the
 * current month; the web app computes other months on demand via
 * getDashboardData. Idempotent — safe to re-run.
 * @return {Object} the summary object that was written.
 */
function computeProjection() {
  var cfg = readConfig_();
  var cycle = resolveLatestCycle_(readCycles_());
  if (!cycle) {
    throw new Error('No billing cycle configured. Add a row to the Cycles tab (cycle_key, label, cycle_start, next_read).');
  }

  var computed = computeProjectionForWindow_(cfg, cycle.cycle_start, cycle.next_read);
  var headers = ['date', 'mean_temp_f', 'cdd', 'actual_kwh', 'predicted_kwh', 'used_kwh', 'cumulative_kwh', 'day_type'];
  var matrix = computed.rows.map(function (row) {
    return headers.map(function (header) { return row[header]; });
  });
  writeTable_('Projection', headers, matrix);
  writeKeyValues_('Summary', computed.summary);

  logEvent_('compute_projection_completed', {
    cycle_key: cycle.cycle_key, projected_total_kwh: computed.summary.projected_total_kwh,
    margin: computed.summary.margin_vs_1000, verdict: computed.summary.verdict,
    fit_days: computed.summary.fit_days, beta: computed.summary.beta, r2: computed.summary.r_squared
  });
  return computed.summary;
}
