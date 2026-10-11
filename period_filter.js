/* Inlined into docs/index.html by compute_perf.py.
   Recomputes the audit from closed FIFO round trips whose exit date
   falls in the selected window. Open marks are not in this payload. */
"use strict";

const HOLD_ORDER = [
  ["same_day", "Same day"],
  ["overnight", "Overnight"],
  ["2_4d", "2–4d"],
  ["5_9d", "5–9d"],
  ["10d_plus", "10d+"],
];

const BUCKET_ORDER = [
  ["le_neg2", "≤ −2R"],
  ["neg2_neg1", "−2 to −1"],
  ["neg1_0", "−1 to 0"],
  ["0_1", "0 to 1"],
  ["1_2", "1 to 2"],
  ["2_3", "2 to 3"],
  ["gt3", "> 3R"],
];

const TIME_ORDER = [
  "Pre-9:30",
  "9:30–10:00",
  "10:00–11:00",
  "11:00–1:00",
  "1:00–2:30",
  "2:30–4:00",
  "After hours",
  "Unknown",
];

const DOW_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];

const REGIME_ORDER = ["Up day", "Range day", "Down day", "Unknown"];

const EDGE_SPECS = [
  ["adr", "ADR", "pct"],
  ["rsi", "RSI", "num"],
  ["ema_dist_pct", "EMA distance", "signed_pct"],
  ["sma_dist_pct", "SMA distance", "signed_pct"],
  ["dollar_volume", "$ volume", "vol"],
  ["gap_pct", "Gap", "signed_pct"],
  ["week52_pct", "52w high distance", "signed_pct"],
];

const LEAK_DEFS = [
  ["Late-day entries", "Filled at or after 2:30pm ET", "late", "has_time"],
  ["Revenge re-entries", "Opened within 60 minutes of a losing exit", "revenge", "fills"],
  ["Held 10+ sessions", "Exit is 10 or more sessions after entry", "hold10", "fills"],
  ["Gap chase", "Opening gap above 8%", "gapChase", "gap_pct"],
  ["Extended RSI", "RSI above 78 at entry", "hotRsi", "rsi"],
  ["Oversized risk", "Dollar risk above 2× the median", "oversized", "fills"],
  ["Friday afternoon", "Friday entry at or after 1:00pm ET", "fridayAfternoon", "has_time"],
];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function formatFixed(value, digits) {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    useGrouping: false,
    roundingMode: "halfEven",
  }).format(value);
}

function formatGrouped(value, digits) {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    useGrouping: true,
    roundingMode: "halfEven",
  }).format(Math.abs(value));
}

function fmtMoney(value, digits) {
  if (digits == null) digits = 0;
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const n = Number(value);
  const sign = n > 0.005 ? "+" : n < -0.005 ? "−" : "";
  return sign + "$" + formatGrouped(Math.abs(n), digits);
}

function fmtPlainMoney(value, digits) {
  if (digits == null) digits = 0;
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const n = Number(value);
  const sign = n < -0.005 ? "−" : "";
  return sign + "$" + formatGrouped(Math.abs(n), digits);
}

function fmtPct(value, digits, signed) {
  if (value == null || !Number.isFinite(Number(value))) return "N/A";
  const n = Number(value);
  if (signed) {
    const sign = n > 0 ? "+" : n < 0 ? "−" : "";
    return sign + formatFixed(Math.abs(n), digits) + "%";
  }
  return formatFixed(n, digits) + "%";
}

function fmtNum(value, digits) {
  if (value == null || !Number.isFinite(Number(value))) return "N/A";
  return formatFixed(Number(value), digits);
}

function fmtVol(value) {
  if (value == null || !Number.isFinite(Number(value))) return "N/A";
  const n = Number(value);
  const abs = Math.abs(n);
  if (abs >= 1e9) return "$" + formatFixed(n / 1e9, 1) + "B";
  if (abs >= 1e6) return "$" + formatFixed(n / 1e6, 0) + "M";
  return "$" + formatGrouped(n, 0);
}

function fmtR(value) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const n = Number(value);
  if (n > 0) return "+" + formatFixed(n, 2) + "R";
  if (n < 0) return "−" + formatFixed(Math.abs(n), 2) + "R";
  return "0.00R";
}

function fmtDate(iso, withYear) {
  if (!iso) return "—";
  const parts = iso.slice(0, 10).split("-").map(Number);
  const month = MONTHS[parts[1] - 1];
  if (withYear === false) return month + " " + parts[2];
  return month + " " + parts[2] + ", " + parts[0];
}

function sessionsLabel(value) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const n = Number(formatFixed(Number(value), 0));
  return n === 1 ? "1 session" : String(n) + " sessions";
}

function moneyClass(value) {
  if (value == null || !Number.isFinite(Number(value))) return "flat";
  const n = Number(value);
  if (n > 0.005) return "pos";
  if (n < -0.005) return "neg";
  return "flat";
}

function esc(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#x27;");
}

function sum(values) {
  let total = 0;
  for (let i = 0; i < values.length; i += 1) total += values[i];
  return total;
}

function median(values) {
  if (!values.length) return null;
  const nums = values.map(Number).slice().sort((a, b) => a - b);
  const mid = Math.floor(nums.length / 2);
  if (nums.length % 2) return nums[mid];
  return (nums[mid - 1] + nums[mid]) / 2;
}

function addDays(iso, days) {
  const parts = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  dt.setUTCDate(dt.getUTCDate() + days);
  return dt.toISOString().slice(0, 10);
}

function weekdayMon0(iso) {
  const parts = iso.split("-").map(Number);
  const sun0 = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2])).getUTCDay();
  return (sun0 + 6) % 7;
}

function daysBetween(start, end) {
  const a = start.split("-").map(Number);
  const b = end.split("-").map(Number);
  const ms = Date.UTC(b[0], b[1] - 1, b[2]) - Date.UTC(a[0], a[1] - 1, a[2]);
  return Math.round(ms / 86400000);
}

function fridayOf(iso) {
  const py = weekdayMon0(iso);
  return addDays(iso, ((4 - py) % 7 + 7) % 7);
}

function rBucket(r) {
  if (r <= -2) return "le_neg2";
  if (r <= -1) return "neg2_neg1";
  if (r <= 0) return "neg1_0";
  if (r <= 1) return "0_1";
  if (r <= 2) return "1_2";
  if (r <= 3) return "2_3";
  return "gt3";
}

function auditPresetBounds(name, today) {
  if (name === "all") return { start: null, end: null };
  if (name === "week") return { start: addDays(today, -weekdayMon0(today)), end: today };
  if (name === "mtd") return { start: today.slice(0, 8) + "01", end: today };
  if (name === "d30") return { start: addDays(today, -29), end: today };
  throw new Error("Unknown preset " + name);
}

function group(trips, keyfn) {
  const map = new Map();
  for (let i = 0; i < trips.length; i += 1) {
    const key = keyfn(trips[i]);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(trips[i]);
  }
  const out = [];
  for (const [key, rows] of map) {
    const pnl = sum(rows.map((row) => row.pnl));
    const wins = rows.filter((row) => row.pnl > 0).length;
    out.push({
      key: key,
      pnl: pnl,
      trades: rows.length,
      win_rate: rows.length ? (100 * wins) / rows.length : 0,
    });
  }
  return out;
}

function setupCard(trips, name) {
  const rows = trips.filter((row) => row.setup === name);
  if (!rows.length) return { trades: 0, pnl: 0, win_rate: null };
  const wins = rows.filter((row) => row.pnl > 0).length;
  return { trades: rows.length, pnl: sum(rows.map((row) => row.pnl)), win_rate: (100 * wins) / rows.length };
}

function maxDrawdown(trips) {
  let equity = 0;
  let peak = 0;
  let worst = 0;
  let trough = null;
  let peakAt = 0;
  for (let i = 0; i < trips.length; i += 1) {
    equity += trips[i].pnl;
    if (equity > peak) peak = equity;
    const dd = peak - equity;
    if (dd > worst) {
      worst = dd;
      trough = trips[i].exitDate;
      peakAt = peak;
    }
  }
  return { dollars: worst, pct: peakAt > 0 ? (100 * worst) / peakAt : null, trough: trough };
}

function weeklyEquity(trips) {
  if (!trips.length) return [];
  const pnlByWeek = new Map();
  for (let i = 0; i < trips.length; i += 1) {
    const friday = fridayOf(trips[i].exitDate);
    pnlByWeek.set(friday, (pnlByWeek.get(friday) || 0) + trips[i].pnl);
  }
  const keys = Array.from(pnlByWeek.keys()).sort();
  let cursor = keys[0];
  const end = keys[keys.length - 1];
  let cumulative = 0;
  const points = [];
  while (cursor <= end) {
    const weekPnl = pnlByWeek.get(cursor) || 0;
    cumulative += weekPnl;
    points.push({ week: cursor, pnl: cumulative, week_pnl: weekPnl });
    cursor = addDays(cursor, 7);
  }
  return points;
}

function medianMetric(rows, key, columns) {
  if (!columns.has(key)) return null;
  const vals = [];
  for (let i = 0; i < rows.length; i += 1) {
    const value = rows[i].extras[key];
    if (value != null) vals.push(value);
  }
  if (!vals.length) return null;
  return median(vals);
}

function auditCompute(payload, start, end) {
  const columns = new Set(payload.columns || []);
  const trips = (payload.trips || []).filter((trip) => {
    if (start && trip.exitDate < start) return false;
    if (end && trip.exitDate > end) return false;
    return true;
  });
  const wins = trips.filter((trip) => trip.pnl > 0);
  const losses = trips.filter((trip) => trip.pnl < 0);
  const net = trips.length ? sum(trips.map((trip) => trip.pnl)) : 0;
  const grossWin = wins.length ? sum(wins.map((trip) => trip.pnl)) : 0;
  const grossLoss = losses.length ? sum(losses.map((trip) => trip.pnl)) : 0;
  let profitFactor = null;
  let profitFactorInfinite = false;
  if (trips.length) {
    if (grossLoss === 0) {
      if (grossWin > 0) profitFactorInfinite = true;
    } else {
      profitFactor = grossWin / Math.abs(grossLoss);
    }
  }
  const expectancy = trips.length ? net / trips.length : null;
  const rValues = trips.filter((trip) => trip.r != null).map((trip) => trip.r);
  const expectancyR = rValues.length ? sum(rValues) / rValues.length : null;
  const winRate = trips.length ? (100 * wins.length) / trips.length : null;
  const drawdown = maxDrawdown(trips);
  const buckets = {};
  for (let i = 0; i < BUCKET_ORDER.length; i += 1) buckets[BUCKET_ORDER[i][0]] = 0;
  for (let i = 0; i < trips.length; i += 1) {
    if (trips[i].r == null) continue;
    buckets[rBucket(trips[i].r)] += 1;
  }
  const focus = setupCard(trips, "focus");
  const avoid = setupCard(trips, "avoid");
  const risks = trips.map((trip) => trip.risk);
  const medianRisk = risks.length ? median(risks) : null;
  const oversizedCut = medianRisk == null ? null : 2 * medianRisk;
  const anyTime = trips.some((trip) => trip.hasTime);
  const leaks = LEAK_DEFS.map((def, order) => {
    const name = def[0];
    const note = def[1];
    const flag = def[2];
    const need = def[3];
    if (need !== "fills" && need !== "has_time" && !columns.has(need)) {
      return { name: name, note: note, trades: null, pnl: null, available: false, order: order };
    }
    if (need === "has_time" && trips.length && !anyTime) {
      return { name: name, note: note, trades: null, pnl: null, available: false, order: order };
    }
    const chosen = trips.filter((trip) => {
      if (flag === "oversized") return oversizedCut != null && trip.risk > oversizedCut + 1e-6;
      return Boolean(trip[flag]);
    });
    return {
      name: name,
      note: note,
      trades: chosen.length,
      pnl: chosen.length ? sum(chosen.map((trip) => trip.pnl)) : 0,
      available: true,
      order: order,
    };
  });
  leaks.sort((a, b) => {
    if (a.pnl == null && b.pnl == null) return a.order - b.order;
    if (a.pnl == null) return 1;
    if (b.pnl == null) return -1;
    if (a.pnl !== b.pnl) return a.pnl - b.pnl;
    return a.order - b.order;
  });

  const bySymbol = group(trips, (trip) => trip.symbol);
  const ranked = bySymbol.slice().sort((a, b) => b.pnl - a.pnl);
  const best = ranked.filter((row) => row.pnl > 0).slice(0, 5);
  const worst = ranked.slice().reverse().filter((row) => row.pnl < 0).slice(0, 5);

  const holdMap = new Map(group(trips, (trip) => trip.holdKey).map((row) => [row.key, row]));
  const holds = HOLD_ORDER.map((pair) => {
    const row = holdMap.get(pair[0]) || { key: pair[0], pnl: 0, trades: 0, win_rate: 0 };
    return { key: row.key, label: pair[1], pnl: row.pnl, trades: row.trades, win_rate: row.win_rate };
  });

  let regimes;
  if (!columns.has("spy_regime")) {
    regimes = [{
      key: "Unknown",
      pnl: net,
      trades: trips.length,
      win_rate: winRate || 0,
      missing: true,
    }];
  } else {
    const regimeMap = new Map(group(trips, (trip) => trip.regime).map((row) => [row.key, row]));
    regimes = [];
    for (let i = 0; i < REGIME_ORDER.length; i += 1) {
      const label = REGIME_ORDER[i];
      const found = regimeMap.get(label);
      if (!found && label === "Unknown") continue;
      regimes.push(found || { key: label, pnl: 0, trades: 0, win_rate: 0 });
    }
  }

  const timeMap = new Map(group(trips, (trip) => trip.timeBucket).map((row) => [row.key, row]));
  const times = TIME_ORDER.filter((label) => timeMap.has(label) && timeMap.get(label).trades).map((label) => timeMap.get(label));
  const dowMap = new Map(group(trips, (trip) => trip.weekday).map((row) => [row.key, row]));
  const dows = DOW_ORDER.filter((label) => dowMap.has(label) && dowMap.get(label).trades).map((label) => dowMap.get(label));

  const edge = EDGE_SPECS.map((spec) => ({
    label: spec[1],
    kind: spec[2],
    winners: medianMetric(wins, spec[0], columns),
    losers: medianMetric(losses, spec[0], columns),
    available: columns.has(spec[0]),
  }));

  const grossSymbols = bySymbol.filter((row) => row.pnl > 0);
  const grossProfitSymbols = grossSymbols.length ? sum(grossSymbols.map((row) => row.pnl)) : 0;
  const topSorted = grossSymbols.slice().sort((a, b) => b.pnl - a.pnl);
  const top1 = topSorted.length ? topSorted[0] : null;
  const top3 = topSorted.slice(0, 3);
  const top3Pnl = top3.length ? sum(top3.map((row) => row.pnl)) : 0;
  const counts = new Map();
  for (let i = 0; i < trips.length; i += 1) {
    counts.set(trips[i].symbol, (counts.get(trips[i].symbol) || 0) + 1);
  }
  let busiestSymbol = null;
  for (const pair of counts) {
    if (!busiestSymbol || pair[1] > busiestSymbol[1]) busiestSymbol = [pair[0], pair[1]];
  }
  const entriesByDay = new Map();
  for (let i = 0; i < trips.length; i += 1) {
    const day = trips[i].entryDate;
    entriesByDay.set(day, (entriesByDay.get(day) || 0) + 1);
  }
  let busiestDay = null;
  for (const pair of entriesByDay) {
    if (!busiestDay || pair[1] > busiestDay[1]) busiestDay = [pair[0], pair[1]];
  }
  let streak = 0;
  let maxStreak = 0;
  for (let i = 0; i < trips.length; i += 1) {
    if (trips[i].pnl < 0) {
      streak += 1;
      if (streak > maxStreak) maxStreak = streak;
    } else {
      streak = 0;
    }
  }
  const loserRespected = losses.filter((trip) => trip.sell + 1e-9 >= trip.stop * 0.998);
  const definedStops = trips.filter((trip) => trip.stopSource !== "fallback");
  let tradesPerWeek = null;
  if (trips.length) {
    const spanDays = daysBetween(trips[0].entryDate, trips[trips.length - 1].exitDate);
    const weeks = Math.max(spanDays / 7, 1 / 7);
    tradesPerWeek = trips.length / weeks;
  }
  const winHold = wins.length ? median(wins.map((trip) => trip.holdSessions)) : null;
  const lossHold = losses.length ? median(losses.map((trip) => trip.holdSessions)) : null;
  const recent = trips.slice(-20).reverse().map((trip) => ({
    exitDate: trip.exitDate,
    symbol: trip.symbol,
    holdLabel: trip.holdLabel,
    pnl: trip.pnl,
    r: trip.r,
    rKey: trip.r == null ? "na" : rBucket(trip.r),
  }));

  return {
    net: net,
    round_trips: trips.length,
    win_rate: winRate,
    profit_factor: profitFactor,
    profit_factor_infinite: profitFactorInfinite,
    expectancy: expectancy,
    expectancy_r: expectancyR,
    drawdown: drawdown,
    buckets: buckets,
    focus: focus,
    avoid: avoid,
    leaks: leaks,
    holds: holds,
    regimes: regimes,
    times: times,
    dows: dows,
    edge: edge,
    best: best,
    worst: worst,
    weekly: weeklyEquity(trips),
    recent: recent,
    median_risk: medianRisk,
    concentration: {
      symbols: counts.size,
      top1_key: top1 ? top1.key : null,
      top1_share: top1 && grossProfitSymbols ? (100 * top1.pnl) / grossProfitSymbols : null,
      top3_share: grossProfitSymbols ? (100 * top3Pnl) / grossProfitSymbols : null,
      busiest_symbol: busiestSymbol,
      busiest_share: busiestSymbol && trips.length ? (100 * busiestSymbol[1]) / trips.length : null,
      busiest_day: busiestDay,
      max_streak: maxStreak,
      stop_coverage: trips.length ? (100 * definedStops.length) / trips.length : null,
      stops_respected: losses.length ? (100 * loserRespected.length) / losses.length : null,
      trades_per_week: tradesPerWeek,
      win_hold: winHold,
      loss_hold: lossHold,
      commissions: trips.length ? sum(trips.map((trip) => trip.commission)) : 0,
    },
  };
}

function divergingHtml(rows, share) {
  const amounts = rows.map((row) => Math.abs(row.pnl || 0));
  let peak = amounts.length ? Math.max.apply(null, amounts) : 1;
  if (!peak) peak = 1;
  return rows.map((row) => {
    const pnl = row.pnl || 0;
    const width = Math.min(100, (Math.abs(pnl) / peak) * 100);
    const bar = '<span class="bar ' + (pnl >= 0 ? "gain" : "loss") + '" style="width:' + width.toFixed(2) + '%"></span>';
    let left = "";
    let right = "";
    if (Math.abs(pnl) >= 0.005) {
      if (pnl > 0) right = bar;
      else left = bar;
    }
    let sub = "";
    if (row.trades != null) {
      const winTxt = row.win_rate != null && row.trades ? " · " + formatFixed(row.win_rate, 0) + "% win" : "";
      const noun = row.trades === 1 ? "trade" : "trades";
      sub = "<small>" + row.trades + " " + noun + winTxt + "</small>";
    }
    const label = row.label != null ? row.label : row.key;
    let amount = fmtMoney(pnl);
    if (share) amount = amount + " / " + fmtPct((100 * pnl) / peak, 1, true);
    const name = share ? ' class="sym"' : "";
    const rowClass = share ? "divbar stock" : "divbar";
    return '<div class="' + rowClass + '"><div class="lbl"><span' + name + '>' + esc(label) + "</span>" + sub + '</div>'
      + '<div class="track"><div class="side left">' + left + '</div><div class="side right">' + right + "</div></div>"
      + '<div class="amt ' + moneyClass(pnl) + '">' + amount + "</div></div>";
  }).join("\n");
}

function fmtEdge(value, kind) {
  if (kind === "pct") return fmtPct(value, 1, false);
  if (kind === "signed_pct") return fmtPct(value, 1, true);
  if (kind === "vol") return fmtVol(value);
  return fmtNum(value, 1);
}

function edgeHtml(rows) {
  return rows.map((row) => {
    let winners = "N/A";
    let losers = "N/A";
    let spread = "N/A";
    if (row.available && row.winners != null && row.losers != null) {
      winners = fmtEdge(row.winners, row.kind);
      losers = fmtEdge(row.losers, row.kind);
      spread = fmtEdge(row.winners - row.losers, row.kind);
    }
    return '<tr><th scope="row">' + esc(row.label) + "</th><td>" + winners + "</td><td>" + losers + "</td><td>" + spread + "</td></tr>";
  }).join("\n");
}

function leaksHtml(leaks) {
  return leaks.map((row) => {
    let money = "N/A";
    let trades = "needs column";
    let tone = "flat";
    if (row.available) {
      money = fmtMoney(row.pnl);
      trades = row.trades + " trade" + (row.trades === 1 ? "" : "s");
      tone = moneyClass(row.pnl);
    }
    return '<div class="leak"><div><div class="leak-name">' + esc(row.name) + '</div>'
      + '<div class="leak-note">' + esc(row.note) + "</div></div>"
      + '<div class="leak-n">' + trades + '</div><div class="leak-pnl ' + tone + '">' + money + "</div></div>";
  }).join("\n");
}

function histogramHtml(buckets) {
  const values = BUCKET_ORDER.map((pair) => buckets[pair[0]] || 0);
  let peak = values.length ? Math.max.apply(null, values) : 1;
  if (!peak) peak = 1;
  return BUCKET_ORDER.map((pair) => {
    const count = buckets[pair[0]] || 0;
    const height = count === 0 ? 8 : Math.max(8, (100 * count) / peak);
    const tone = pair[0] === "le_neg2" || pair[0] === "neg2_neg1" || pair[0] === "neg1_0" ? "loss" : "gain";
    return '<div class="hcol"><div class="hcount">' + count + '</div>'
      + '<div class="htrack"><div class="hbar ' + tone + '" style="height:' + height.toFixed(1) + '%"></div></div>'
      + '<div class="hlabel">' + esc(pair[1]) + "</div></div>";
  }).join("\n");
}

function tradesHtml(rows) {
  if (!rows.length) return '<p class="empty">No closed round trips.</p>';
  const body = rows.map((row) => {
    return "<tr>"
      + "<td>" + esc(fmtDate(row.exitDate)) + "</td>"
      + '<td class="sym">' + esc(row.symbol) + "</td>"
      + "<td>" + esc(row.holdLabel) + "</td>"
      + '<td class="num ' + moneyClass(row.pnl) + '">' + fmtMoney(row.pnl) + "</td>"
      + '<td class="num"><span class="r-pill r-' + row.rKey + '">' + fmtR(row.r) + "</span></td>"
      + "</tr>";
  }).join("\n");
  return '<div class="table-wrap"><table class="trades"><thead><tr>'
    + "<th>Exit</th><th>Symbol</th><th>Hold</th><th>P&amp;L</th><th>R</th>"
    + "</tr></thead><tbody>" + body + "</tbody></table></div>";
}

function ticks(ymin, ymax) {
  const span = ymax - ymin;
  const raw = span ? span / 4 : 1;
  const mag = raw ? 10 ** Math.floor(Math.log10(Math.abs(raw))) : 1;
  let step = mag;
  const candidates = [1, 2, 2.5, 5, 10];
  for (let i = 0; i < candidates.length; i += 1) {
    if (candidates[i] * mag >= raw * 0.75) {
      step = candidates[i] * mag;
      break;
    }
  }
  let value = Math.ceil((ymin - 1e-9) / step) * step;
  const out = [];
  let guard = 0;
  while (value <= ymax + step * 0.01 && guard < 12) {
    if (ymin - 1e-6 <= value && value <= ymax + 1e-6) out.push(value);
    value += step;
    guard += 1;
  }
  return out;
}

function equitySvg(points) {
  if (!points.length) {
    return '<p class="empty">No closed round trips yet. Drop fills into data/trades.csv and rerun compute_perf.py.</p>';
  }
  const values = [0].concat(points.map((point) => point.pnl));
  const width = 960;
  const height = 320;
  const padL = 88;
  const padR = 24;
  const padT = 22;
  const padB = 36;
  let ymin = Math.min.apply(null, [0].concat(values));
  let ymax = Math.max.apply(null, [0].concat(values));
  const span = ymax - ymin || 1;
  ymin -= span * 0.08;
  ymax += span * 0.12;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;
  function xAt(i) {
    if (values.length === 1) return padL;
    return padL + (innerW * i) / (values.length - 1);
  }
  function yAt(v) {
    return padT + innerH * (1 - (v - ymin) / (ymax - ymin));
  }
  const coords = values.map((v, i) => [xAt(i), yAt(v)]);
  const line = coords.map((pair) => pair[0].toFixed(1) + "," + pair[1].toFixed(1)).join(" ");
  const zeroY = yAt(0);
  const area = coords[0][0].toFixed(1) + "," + zeroY.toFixed(1) + " " + line + " " + coords[coords.length - 1][0].toFixed(1) + "," + zeroY.toFixed(1);
  const grid = ticks(ymin, ymax).map((tick) => {
    const y = yAt(tick);
    return '<line x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (width - padR) + '" y2="' + y.toFixed(1) + '" class="grid"/>'
      + '<text x="' + (padL - 8) + '" y="' + (y + 4).toFixed(1) + '" text-anchor="end" class="tick">' + esc(fmtPlainMoney(tick)) + "</text>";
  }).join("");
  const labelStep = Math.max(1, Math.floor(points.length / 6));
  const labels = [];
  for (let i = 0; i < points.length; i += 1) {
    if (i % labelStep !== 0 && i !== points.length - 1) continue;
    labels.push('<text x="' + xAt(i + 1).toFixed(1) + '" y="' + (height - 10) + '" text-anchor="middle" class="tick">' + esc(fmtDate(points[i].week, false)) + "</text>");
  }
  const endX = coords[coords.length - 1][0];
  const endY = coords[coords.length - 1][1];
  const endLabel = fmtMoney(values[values.length - 1]);
  return '<svg viewBox="0 0 ' + width + " " + height + '" role="img" aria-label="Weekly cumulative realized P and L ending at ' + esc(endLabel) + '">'
    + "<defs><linearGradient id=\"eqFill\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">"
    + '<stop offset="0%" stop-color="#00f0ff" stop-opacity="0.22"/>'
    + '<stop offset="100%" stop-color="#00f0ff" stop-opacity="0.02"/>'
    + "</linearGradient></defs>"
    + grid
    + '<line x1="' + padL + '" y1="' + zeroY.toFixed(1) + '" x2="' + (width - padR) + '" y2="' + zeroY.toFixed(1) + '" class="zero"/>'
    + '<polygon points="' + area + '" fill="url(#eqFill)"/>'
    + '<polyline points="' + line + '" class="eq-line"/>'
    + '<circle cx="' + endX.toFixed(1) + '" cy="' + endY.toFixed(1) + '" r="4.5" class="eq-dot"/>'
    + '<text x="' + (endX - 8).toFixed(1) + '" y="' + Math.max(16, endY - 10).toFixed(1) + '" text-anchor="end" class="end-label">' + esc(endLabel) + "</text>"
    + labels.join("")
    + "</svg>";
}

function winPhrase(rate) {
  if (rate == null) return "—";
  return formatFixed(rate, 0) + "% win";
}

function paint(view) {
  const net = document.getElementById("kpi-net");
  net.textContent = fmtMoney(view.net);
  net.className = "v " + moneyClass(view.net);
  document.getElementById("kpi-trips").textContent = String(view.round_trips);
  const win = document.getElementById("kpi-win");
  win.textContent = view.win_rate == null ? "—" : formatFixed(view.win_rate, 1) + "%";
  const pf = document.getElementById("kpi-pf");
  if (view.profit_factor_infinite) pf.textContent = "∞";
  else if (view.profit_factor == null) pf.textContent = "—";
  else pf.textContent = formatFixed(view.profit_factor, 2);
  const exp = document.getElementById("kpi-exp");
  exp.textContent = view.expectancy == null ? "—" : fmtMoney(view.expectancy);
  exp.className = "v " + moneyClass(view.expectancy);
  const expSub = document.getElementById("kpi-exp-sub");
  if (view.expectancy_r == null) expSub.textContent = "per round trip";
  else if (view.expectancy_r > 0) expSub.textContent = "mean +" + formatFixed(view.expectancy_r, 2) + "R";
  else if (view.expectancy_r < 0) expSub.textContent = "mean -" + formatFixed(Math.abs(view.expectancy_r), 2) + "R";
  else expSub.textContent = "mean +0.00R";
  const dd = document.getElementById("kpi-dd");
  const ddSub = document.getElementById("kpi-dd-sub");
  if (!view.round_trips) {
    dd.textContent = "—";
    ddSub.textContent = "from peak";
  } else {
    dd.textContent = fmtMoney(-view.drawdown.dollars);
    ddSub.textContent = view.drawdown.pct == null ? "from peak" : formatFixed(view.drawdown.pct, 1) + "% of peak";
  }
  const note = document.getElementById("equity-note");
  let trough = "";
  if (view.drawdown.trough && view.drawdown.dollars > 0) {
    trough = " Max drawdown trough " + fmtDate(view.drawdown.trough) + ".";
  }
  note.textContent = "Realized round trips whose exit falls in the selected period, summed through each Friday." + trough;
  document.getElementById("equity-chart").innerHTML = equitySvg(view.weekly);

  const focusPnl = document.getElementById("focus-pnl");
  focusPnl.textContent = fmtMoney(view.focus.pnl);
  focusPnl.className = "big " + moneyClass(view.focus.pnl);
  document.getElementById("focus-meta").textContent = view.focus.trades + " trades · " + winPhrase(view.focus.win_rate);
  const avoidPnl = document.getElementById("avoid-pnl");
  avoidPnl.textContent = fmtMoney(view.avoid.pnl);
  avoidPnl.className = "big " + moneyClass(view.avoid.pnl);
  document.getElementById("avoid-meta").textContent = view.avoid.trades + " trades · " + winPhrase(view.avoid.win_rate);

  document.getElementById("leaks").innerHTML = leaksHtml(view.leaks);
  document.getElementById("holds").innerHTML = divergingHtml(view.holds);
  document.getElementById("regimes").innerHTML = divergingHtml(view.regimes);
  document.getElementById("times").innerHTML = view.times.length ? divergingHtml(view.times) : '<p class="empty">No closed round trips.</p>';
  document.getElementById("dows").innerHTML = view.dows.length ? divergingHtml(view.dows) : '<p class="empty">No closed round trips.</p>';
  document.getElementById("edge-body").innerHTML = edgeHtml(view.edge);
  document.getElementById("best").innerHTML = view.best.length ? divergingHtml(view.best, true) : '<p class="empty">No winning symbols.</p>';
  document.getElementById("worst").innerHTML = view.worst.length ? divergingHtml(view.worst, true) : '<p class="empty">No losing symbols.</p>';

  const conc = view.concentration;
  document.getElementById("c-symbols").textContent = String(conc.symbols);
  document.getElementById("c-top1").textContent = conc.top1_key && conc.top1_share != null
    ? conc.top1_key + " · " + formatFixed(conc.top1_share, 0) + "% of gross profit"
    : "—";
  document.getElementById("c-top3").textContent = conc.top3_share == null ? "—" : formatFixed(conc.top3_share, 0) + "%";
  document.getElementById("c-busiest-sym").textContent = conc.busiest_symbol && conc.busiest_share != null
    ? conc.busiest_symbol[0] + " · " + formatFixed(conc.busiest_share, 0) + "% of trades"
    : "—";
  document.getElementById("c-busiest-day").textContent = conc.busiest_day
    ? fmtDate(conc.busiest_day[0]) + " · " + conc.busiest_day[1] + " entries"
    : "—";
  document.getElementById("c-tpw").textContent = conc.trades_per_week == null ? "—" : formatFixed(conc.trades_per_week, 1);
  document.getElementById("c-risk").textContent = fmtPlainMoney(view.median_risk);
  document.getElementById("c-stop").textContent = conc.stop_coverage == null ? "—" : formatFixed(conc.stop_coverage, 0) + "%";
  document.getElementById("c-losers").textContent = conc.stops_respected == null ? "—" : formatFixed(conc.stops_respected, 0) + "%";
  document.getElementById("c-win-hold").textContent = sessionsLabel(conc.win_hold);
  document.getElementById("c-loss-hold").textContent = sessionsLabel(conc.loss_hold);
  document.getElementById("c-streak").textContent = String(conc.max_streak);
  document.getElementById("c-comm").textContent = fmtPlainMoney(conc.commissions, 0);
  document.getElementById("histogram").innerHTML = histogramHtml(view.buckets);
  document.getElementById("recent").innerHTML = tradesHtml(view.recent);
}

function etToday() {
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return fmt.format(new Date());
}

function windowLine(bounds, count, payload) {
  const noun = count === 1 ? "closed exit" : "closed exits";
  let when = bounds.label;
  if (!bounds.start && !bounds.end) {
    if (payload.exitMin && payload.exitMax) {
      when += " · " + fmtDate(payload.exitMin) + " – " + fmtDate(payload.exitMax);
    }
  } else if (bounds.start && bounds.end) {
    when += " · " + fmtDate(bounds.start) + " – " + fmtDate(bounds.end);
  } else if (bounds.start) {
    when += " · from " + fmtDate(bounds.start);
  } else if (bounds.end) {
    when += " · through " + fmtDate(bounds.end);
  }
  return when + " · " + count + " " + noun;
}

function boot() {
  const node = document.getElementById("audit-data");
  if (!node) return;
  const payload = JSON.parse(node.textContent);
  const buttons = Array.prototype.slice.call(document.querySelectorAll("[data-preset]"));
  const from = document.getElementById("period-from");
  const to = document.getElementById("period-to");
  const status = document.getElementById("period-window");

  function boundsFor(name) {
    if (name === "custom") {
      let start = from.value || null;
      let end = to.value || null;
      if (start && end && start > end) {
        const swap = start;
        start = end;
        end = swap;
      }
      return { start: start, end: end, label: "Custom" };
    }
    const today = etToday();
    const bounds = auditPresetBounds(name, today);
    bounds.label = { week: "This week", mtd: "MTD", d30: "Last 30 days", all: "Full CSV range" }[name];
    return bounds;
  }

  function reflect(bounds) {
    if (bounds.start) from.value = bounds.start;
    else if (payload.exitMin) from.value = payload.exitMin;
    if (bounds.end) to.value = bounds.end;
    else if (payload.exitMax) to.value = payload.exitMax;
  }

  function apply(name) {
    buttons.forEach((btn) => {
      btn.setAttribute("aria-pressed", btn.getAttribute("data-preset") === name ? "true" : "false");
    });
    const bounds = boundsFor(name);
    if (name !== "custom") reflect(bounds);
    const view = auditCompute(payload, bounds.start, bounds.end);
    paint(view);
    status.textContent = windowLine(bounds, view.round_trips, payload);
  }

  buttons.forEach((btn) => {
    btn.addEventListener("click", () => apply(btn.getAttribute("data-preset")));
  });
  from.addEventListener("change", () => apply("custom"));
  to.addEventListener("change", () => apply("custom"));
  if (payload.exitMin) from.value = payload.exitMin;
  if (payload.exitMax) to.value = payload.exitMax;
}

globalThis.divergingHtml = divergingHtml;
globalThis.auditCompute = auditCompute;
globalThis.auditPresetBounds = auditPresetBounds;
globalThis.auditFormat = {
  money: fmtMoney,
  plain: fmtPlainMoney,
  pct: fmtPct,
  num: fmtNum,
  vol: fmtVol,
  r: fmtR,
  date: fmtDate,
  sessions: sessionsLabel,
};

if (typeof document !== "undefined") boot();
