#!/usr/bin/env python3
"""Build a static 2026 Trading Performance Audit from fills.

Reads data/trades.csv (a header with zero rows is valid), matches long
BUY/SELL fills FIFO, and writes docs/index.html. This script never places
orders and does not require a live IBKR session.

R-multiple for a closed lot is (sell - buy) / (buy - initial_stop).
The stop price comes from a matching open_positions.json row, else the
stop_price column, else buy * 0.01.
"""

from __future__ import annotations

import csv
import html
import json
import math
import os
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")

ENRICHMENT = (
    "adr",
    "rsi",
    "ema_dist_pct",
    "sma_dist_pct",
    "dollar_volume",
    "gap_pct",
    "week52_pct",
    "spy_regime",
)

ALIASES = {
    "symbol": {"symbol", "ticker"},
    "datetime": {"datetime", "date_time", "trade_datetime"},
    "date": {"date", "trade_date", "tradedate"},
    "time": {"time", "trade_time"},
    "side": {"side", "buy_sell", "buysell", "action"},
    "quantity": {"quantity", "qty", "shares"},
    "price": {"price", "t_price", "trade_price", "tradeprice"},
    "proceeds": {"proceeds", "amount"},
    "commission": {"commission", "comm_fee", "comm", "ibcommission", "fees", "fee"},
    "stop_price": {"stop_price", "stop", "initial_stop", "stopprice"},
    "adr": {"adr", "adr_pct"},
    "rsi": {"rsi"},
    "ema_dist_pct": {"ema_dist_pct", "ema", "ema_dist", "dist_ema"},
    "sma_dist_pct": {"sma_dist_pct", "sma", "sma_dist", "dist_sma"},
    "dollar_volume": {"dollar_volume", "dvol", "dollar_vol", "vol_dollar"},
    "gap_pct": {"gap_pct", "gap", "gap_percent"},
    "week52_pct": {"week52_pct", "w52", "week_52", "dist_52w", "52w", "52w_high_distance"},
    "spy_regime": {"spy_regime", "spy", "regime", "spy_day"},
}

BUCKET_ORDER = (
    ("le_neg2", "≤ −2R"),
    ("neg2_neg1", "−2 to −1"),
    ("neg1_0", "−1 to 0"),
    ("0_1", "0 to 1"),
    ("1_2", "1 to 2"),
    ("2_3", "2 to 3"),
    ("gt3", "> 3R"),
)

HOLD_ORDER = (
    ("same_day", "Same day"),
    ("overnight", "Overnight"),
    ("2_4d", "2–4d"),
    ("5_9d", "5–9d"),
    ("10d_plus", "10d+"),
)

TIME_ORDER = (
    "Pre-9:30",
    "9:30–10:00",
    "10:00–11:00",
    "11:00–1:00",
    "1:00–2:30",
    "2:30–4:00",
    "After hours",
    "Unknown",
)

DOW_ORDER = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

REGIME_LABEL = {"up": "Up day", "down": "Down day", "range": "Range day"}


def to_py(value):
    """Cast numpy scalars/arrays (and nested containers) to Python types."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return float(value)
    if isinstance(value, dict):
        return {str(to_py(k)): to_py(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_py(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    module = type(value).__module__
    if module == "numpy":
        item = getattr(value, "item", None)
        if callable(item) and getattr(value, "shape", None) == ():
            return to_py(item())
        tolist = getattr(value, "tolist", None)
        if callable(tolist):
            return to_py(tolist())
    return value


def _as_float(value):
    value = to_py(value)
    if value is None:
        return None
    return float(value)


def pysum(values):
    nums = [float(v) for v in values]
    try:
        import numpy as np

        return float(to_py(np.sum(np.asarray(nums, dtype=float))))
    except Exception:
        return float(sum(nums))


def pystats_median(values):
    nums = [float(v) for v in values]
    if not nums:
        return None
    try:
        import numpy as np

        return _as_float(np.median(np.asarray(nums, dtype=float)))
    except Exception:
        nums.sort()
        n = len(nums)
        mid = n // 2
        if n % 2:
            return float(nums[mid])
        return float((nums[mid - 1] + nums[mid]) / 2)


def telegram_notify(text: str) -> None:
    """No-op unless both Telegram env vars are set. Never places orders.

    When the env is empty this returns immediately. When both variables are
    present it attempts one best-effort message and swallows every error.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        import urllib.parse
        import urllib.request

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": text[:3500]}
        ).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=8):
            return
    except Exception:
        return


def repo_root() -> Path:
    override = os.environ.get("AUDIT_ROOT", "").strip()
    if override:
        return Path(override)
    return ROOT


def norm_key(cell: str) -> str:
    text = cell.strip().lower().replace("%", " pct")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _header_index(cells: list[str]) -> dict[int, str]:
    mapped: dict[int, str] = {}
    used = set()
    for i, cell in enumerate(cells):
        key = norm_key(cell)
        for canonical, names in ALIASES.items():
            if key in names and canonical not in used:
                mapped[i] = canonical
                used.add(canonical)
                break
    return mapped


def _is_header(cells: list[str]) -> bool:
    mapped = _header_index(cells)
    cols = set(mapped.values())
    return "symbol" in cols and ("price" in cols or "quantity" in cols)


def _blank(value: str | None) -> bool:
    return value is None or str(value).strip().lower() in {"", "na", "n/a", "null", "none", "-"}


def parse_number(value) -> float | None:
    if value is None or _blank(str(value)):
        return None
    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def parse_datetime(value: str) -> tuple[datetime | None, bool]:
    text = (value or "").strip().strip('"')
    if not text:
        return None, False
    text = re.sub(r"(Z|[+-]\d{2}:?\d{2})$", "", text.strip())
    text = re.sub(r"(\d{2}:\d{2}:\d{2})\.\d+", r"\1", text)
    text = text.replace("T", " ")
    if "," in text:
        text = re.sub(r"\s+", " ", text.replace(",", " ", 1)).strip()
    matched = re.fullmatch(r"(\d{8});(\d{6})", text)
    if matched:
        text = f"{matched.group(1)} {matched.group(2)}"
    formats = (
        ("%Y-%m-%d %H:%M:%S", True),
        ("%Y-%m-%d %H:%M", True),
        ("%Y/%m/%d %H:%M:%S", True),
        ("%Y/%m/%d %H:%M", True),
        ("%m/%d/%Y %H:%M:%S", True),
        ("%m/%d/%Y %H:%M", True),
        ("%Y%m%d %H%M%S", True),
        ("%Y-%m-%d", False),
        ("%m/%d/%Y", False),
        ("%Y%m%d", False),
    )
    for fmt, has_time in formats:
        try:
            return datetime.strptime(text, fmt), has_time
        except ValueError:
            continue
    return None, False


def normalize_side(raw, qty: float) -> tuple[str | None, float]:
    text = (raw or "").strip().upper()
    buy = {"BUY", "BOT", "B", "BUY TO OPEN", "BOT TO OPEN"}
    sell = {"SELL", "SLD", "S", "SELL TO CLOSE", "SLD TO CLOSE"}
    if text in buy:
        return "BUY", abs(qty)
    if text in sell:
        return "SELL", abs(qty)
    if qty < 0:
        return "SELL", abs(qty)
    if qty > 0:
        return "BUY", qty
    return None, 0.0


@dataclass
class Fill:
    dt: datetime
    symbol: str
    side: str
    qty: float
    price: float
    commission: float
    stop_price: float | None
    extras: dict
    has_time: bool


@dataclass
class RoundTrip:
    symbol: str
    qty: float
    entry: datetime
    exit: datetime
    buy: float
    sell: float
    stop: float
    stop_source: str
    pnl: float
    r: float | None
    commission: float
    extras: dict
    has_time: bool
    revenge: bool = False
    hold_sessions: int = 0
    setup: str = "other"


@dataclass
class OpenLot:
    symbol: str
    qty: float
    entry: datetime
    buy: float
    stop: float
    stop_source: str
    extras: dict


def load_fills(path: Path) -> tuple[list[Fill], set[str]]:
    if not path.exists():
        return [], set()
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return [], set()
    rows = list(csv.reader(text.splitlines()))
    header: list[str] | None = None
    header_map: dict[int, str] | None = None
    columns: set[str] = set()
    fills: list[Fill] = []
    for raw in rows:
        cells = [c.strip() for c in raw]
        if not any(cells):
            continue
        if _is_header(cells):
            header = cells
            header_map = _header_index(cells)
            columns = set(header_map.values())
            continue
        if header is None or header_map is None:
            continue
        if len(cells) < len(header):
            cells = cells + [""] * (len(header) - len(cells))
        rec = {header_map[i]: cells[i] for i in range(len(header)) if i in header_map}
        symbol = (rec.get("symbol") or "").strip().upper()
        if not symbol or symbol in {"TOTAL", "SUBTOTAL"} or symbol.startswith("TOTAL"):
            continue
        fill = _row_to_fill(rec, symbol)
        if fill is not None:
            fills.append(fill)
    fills.sort(key=lambda f: (f.dt, 0 if f.side == "BUY" else 1, f.symbol))
    return fills, columns


def _row_to_fill(rec: dict, symbol: str) -> Fill | None:
    qty = parse_number(rec.get("quantity"))
    if qty is None:
        return None
    side, qty = normalize_side(rec.get("side"), qty)
    if side is None or qty <= 0:
        return None
    price = parse_number(rec.get("price"))
    if price is None:
        proceeds = parse_number(rec.get("proceeds"))
        if proceeds is not None and qty:
            price = abs(proceeds) / qty
    # Zero is a real print: an expired option is closed at 0 in the StonkJournal encoding.
    if price is None or price < 0:
        return None
    dt_value = rec.get("datetime") or ""
    when, has_time = parse_datetime(dt_value)
    if when is None and rec.get("date"):
        when, has_time = parse_datetime(rec.get("date") or "")
        time_value = (rec.get("time") or "").strip()
        if when is not None and time_value:
            clock, clock_ok = parse_datetime(f"2000-01-01 {time_value}")
            if clock_ok and clock is not None:
                when = when.replace(hour=clock.hour, minute=clock.minute, second=clock.second)
                has_time = True
    if when is None:
        return None
    commission = parse_number(rec.get("commission"))
    commission = abs(commission) if commission is not None else 0.0
    extras = {}
    for key in ENRICHMENT:
        if key == "spy_regime":
            raw = (rec.get(key) or "").strip().lower()
            extras[key] = raw or None
        else:
            extras[key] = parse_number(rec.get(key))
    return Fill(
        dt=when,
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        commission=commission,
        stop_price=parse_number(rec.get("stop_price")),
        extras=extras,
        has_time=has_time,
    )


def load_positions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    payload = json.loads(text)
    if isinstance(payload, dict):
        rows = payload.get("positions", payload.get("open_positions", []))
    elif isinstance(payload, list):
        rows = payload
    else:
        return []
    positions = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        qty = parse_number(row.get("quantity", row.get("qty")))
        price = parse_number(row.get("avg_price", row.get("price", row.get("avgPrice"))))
        if not symbol or qty is None or price is None:
            continue
        opened, _ = parse_datetime(str(row.get("opened_at") or row.get("datetime") or ""))
        positions.append(
            {
                "symbol": symbol,
                "qty": abs(qty),
                "avg_price": price,
                "stop": parse_number(row.get("stop_price", row.get("stop"))),
                "opened_at": opened,
                "last": parse_number(row.get("last_price", row.get("last", row.get("mark")))),
            }
        )
    return positions


def load_safety(root: Path) -> dict | None:
    jsonl = root / "data" / "safety-check-log.jsonl"
    js = root / "data" / "safety-check-log.json"
    if jsonl.exists():
        lines = [ln.strip() for ln in jsonl.read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
        if lines:
            try:
                payload = json.loads(lines[-1])
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                return _safety_record(payload)
    if js.exists():
        text = js.read_text(encoding="utf-8-sig").strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            # A .json path may still be one object per line.
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            payload = json.loads(lines[-1]) if lines else None
        if isinstance(payload, list) and payload:
            payload = payload[-1]
        if isinstance(payload, dict) and "records" in payload and payload["records"]:
            payload = payload["records"][-1]
        if isinstance(payload, dict):
            return _safety_record(payload)
    return None


def _safety_record(payload: dict) -> dict:
    stamp = payload.get("timestamp") or payload.get("ts") or payload.get("time") or payload.get("as_of")
    when, _ = parse_datetime(str(stamp or ""))
    status = str(payload.get("status") or payload.get("state") or "UNKNOWN").strip() or "UNKNOWN"
    cycle = str(payload.get("cycle") or payload.get("check") or payload.get("name") or "safety-check")
    detail = str(payload.get("detail") or payload.get("message") or payload.get("note") or "").strip()
    return {"status": status, "cycle": cycle, "detail": detail, "timestamp": when, "raw_ts": stamp}


def session_gap(start: date, end: date) -> int:
    if end <= start:
        return 0
    gap = 0
    cursor = start
    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            gap += 1
    return gap


def hold_bucket(sessions: int) -> str:
    if sessions <= 0:
        return "same_day"
    if sessions == 1:
        return "overnight"
    if sessions <= 4:
        return "2_4d"
    if sessions <= 9:
        return "5_9d"
    return "10d_plus"


def r_bucket(r: float) -> str:
    if r <= -2:
        return "le_neg2"
    if r <= -1:
        return "neg2_neg1"
    if r <= 0:
        return "neg1_0"
    if r <= 1:
        return "0_1"
    if r <= 2:
        return "1_2"
    if r <= 3:
        return "2_3"
    return "gt3"


def entry_minutes(rt: RoundTrip) -> int | None:
    if not rt.has_time:
        return None
    return rt.entry.hour * 60 + rt.entry.minute


def time_bucket(rt: RoundTrip) -> str:
    minutes = entry_minutes(rt)
    if minutes is None:
        return "Unknown"
    if minutes < 9 * 60 + 30:
        return "Pre-9:30"
    if minutes < 10 * 60:
        return "9:30–10:00"
    if minutes < 11 * 60:
        return "10:00–11:00"
    if minutes < 13 * 60:
        return "11:00–1:00"
    if minutes < 14 * 60 + 30:
        return "1:00–2:30"
    if minutes < 16 * 60:
        return "2:30–4:00"
    return "After hours"


def classify_setup(rt: RoundTrip, columns: set[str]) -> str:
    """Focus, avoid, or other. Missing enrichment does not fail a rule."""
    minutes = entry_minutes(rt)
    gap = rt.extras.get("gap_pct") if "gap_pct" in columns else None
    rsi = rt.extras.get("rsi") if "rsi" in columns else None
    spy = rt.extras.get("spy_regime") if "spy_regime" in columns else None
    if rt.revenge:
        return "avoid"
    if minutes is not None and minutes >= 14 * 60 + 30:
        return "avoid"
    if gap is not None and gap > 8:
        return "avoid"
    if rsi is not None and rsi > 78:
        return "avoid"
    if rt.hold_sessions >= 10:
        return "avoid"
    focus_time = minutes is not None and (9 * 60 + 35) <= minutes < (10 * 60 + 45)
    focus_hold = rt.hold_sessions in (0, 2, 3, 4)
    focus_spy = spy is None or spy == "up"
    focus_gap = gap is None or 0.5 <= gap <= 5
    focus_rsi = rsi is None or 48 <= rsi <= 70
    if focus_time and focus_hold and focus_spy and focus_gap and focus_rsi:
        return "focus"
    return "other"


def resolve_stop(buy: float, stop_col, symbol: str, entry: datetime, positions: list[dict]):
    for pos in positions:
        if pos["symbol"] != symbol or pos.get("stop") is None:
            continue
        opened = pos.get("opened_at")
        if opened is None:
            continue
        if abs((opened - entry).total_seconds()) > 120:
            continue
        if pos["stop"] < buy:
            return float(pos["stop"]), "open_positions"
    if stop_col is not None and stop_col < buy:
        return float(stop_col), "column"
    return float(buy) * 0.01, "fallback"


def match_fifo(fills: list[Fill], positions: list[dict]) -> tuple[list[RoundTrip], list[OpenLot], float]:
    books: dict[str, deque] = defaultdict(deque)
    closed: list[RoundTrip] = []
    unmatched_sell_qty = 0.0
    for fill in fills:
        book = books[fill.symbol]
        if fill.side == "BUY":
            book.append(
                {
                    "qty_left": fill.qty,
                    "qty": fill.qty,
                    "price": fill.price,
                    "entry": fill.dt,
                    "stop_col": fill.stop_price,
                    "commission": fill.commission,
                    "sell_notional": 0.0,
                    "sell_comm": 0.0,
                    "exit": fill.dt,
                    "extras": dict(fill.extras),
                    "has_time": fill.has_time,
                }
            )
            continue
        remaining = fill.qty
        while remaining > 1e-9 and book:
            lot = book[0]
            take = min(remaining, lot["qty_left"])
            lot["qty_left"] -= take
            lot["sell_notional"] += take * fill.price
            lot["sell_comm"] += fill.commission * (take / fill.qty)
            lot["exit"] = fill.dt
            remaining -= take
            if lot["qty_left"] <= 1e-9:
                book.popleft()
                closed.append(_close_lot(fill.symbol, lot, positions))
        if remaining > 1e-6:
            unmatched_sell_qty += remaining
    open_lots: list[OpenLot] = []
    for symbol, book in books.items():
        for lot in book:
            if lot["qty_left"] <= 1e-9:
                continue
            stop, source = resolve_stop(lot["price"], lot["stop_col"], symbol, lot["entry"], positions)
            open_lots.append(
                OpenLot(
                    symbol=symbol,
                    qty=lot["qty_left"],
                    entry=lot["entry"],
                    buy=lot["price"],
                    stop=stop,
                    stop_source=source,
                    extras=lot["extras"],
                )
            )
    closed.sort(key=lambda rt: (rt.exit, rt.entry, rt.symbol))
    _mark_revenge(closed)
    return closed, open_lots, unmatched_sell_qty


def _close_lot(symbol: str, lot: dict, positions: list[dict]) -> RoundTrip:
    qty = lot["qty"]
    sell = lot["sell_notional"] / qty
    stop, source = resolve_stop(lot["price"], lot["stop_col"], symbol, lot["entry"], positions)
    risk = lot["price"] - stop
    r_value = (sell - lot["price"]) / risk if abs(risk) > 1e-12 else None
    pnl = lot["sell_notional"] - lot["price"] * qty - lot["commission"] - lot["sell_comm"]
    return RoundTrip(
        symbol=symbol,
        qty=qty,
        entry=lot["entry"],
        exit=lot["exit"],
        buy=lot["price"],
        sell=sell,
        stop=stop,
        stop_source=source,
        pnl=pnl,
        r=r_value,
        commission=lot["commission"] + lot["sell_comm"],
        extras=lot["extras"],
        has_time=lot["has_time"],
        hold_sessions=session_gap(lot["entry"].date(), lot["exit"].date()),
    )


def _mark_revenge(closed: list[RoundTrip]) -> None:
    prior: dict[str, list[RoundTrip]] = defaultdict(list)
    for rt in sorted(closed, key=lambda row: (row.entry, row.exit)):
        rt.revenge = False
        for prev in prior[rt.symbol]:
            gap = (rt.entry - prev.exit).total_seconds()
            if 0 <= gap <= 3600 and prev.pnl < 0:
                rt.revenge = True
                break
        prior[rt.symbol].append(rt)


def dollar_risk(rt: RoundTrip) -> float:
    return rt.qty * max(rt.buy - rt.stop, 0.0)


def _group(rows: list[RoundTrip], keyfn) -> list[dict]:
    buckets: dict[str, list[RoundTrip]] = defaultdict(list)
    for rt in rows:
        buckets[keyfn(rt)].append(rt)
    out = []
    for key, group in buckets.items():
        pnl = pysum(rt.pnl for rt in group)
        wins = sum(1 for rt in group if rt.pnl > 0)
        out.append(
            {
                "key": key,
                "pnl": pnl,
                "trades": len(group),
                "win_rate": (100.0 * wins / len(group)) if group else 0.0,
            }
        )
    return out


def max_drawdown(rows: list[RoundTrip]) -> dict:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    trough = None
    peak_at_trough = 0.0
    for rt in rows:
        equity += rt.pnl
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > worst:
            worst = dd
            trough = rt.exit
            peak_at_trough = peak
    pct = (100.0 * worst / peak_at_trough) if peak_at_trough > 0 else None
    return {"dollars": worst, "pct": pct, "trough": trough}


def weekly_equity(rows: list[RoundTrip]) -> list[dict]:
    if not rows:
        return []
    pnl_by_week: dict[date, float] = defaultdict(float)
    for rt in rows:
        friday = rt.exit.date() + timedelta(days=(4 - rt.exit.weekday()) % 7)
        pnl_by_week[friday] += rt.pnl
    start = min(pnl_by_week)
    end = max(pnl_by_week)
    cursor = start
    cumulative = 0.0
    points = []
    while cursor <= end:
        cumulative += pnl_by_week.get(cursor, 0.0)
        points.append({"week": cursor, "pnl": cumulative, "week_pnl": pnl_by_week.get(cursor, 0.0)})
        cursor += timedelta(days=7)
    return points


def _median_metric(rows: list[RoundTrip], key: str, columns: set[str]):
    if key not in columns:
        return None
    vals = [rt.extras.get(key) for rt in rows if rt.extras.get(key) is not None]
    if not vals:
        return None
    return pystats_median(vals)


def assemble(fills: list[Fill], columns: set[str], positions: list[dict], safety: dict | None, today: date | None = None) -> dict:
    closed, open_lots, unmatched = match_fifo(fills, positions)
    for rt in closed:
        rt.setup = classify_setup(rt, columns)
    today = today or datetime.now(ET).date()
    risks = [dollar_risk(rt) for rt in closed]
    median_risk = pystats_median(risks) if risks else None
    oversized_cut = (2 * median_risk) if median_risk else None

    def is_late(rt: RoundTrip) -> bool:
        minutes = entry_minutes(rt)
        return minutes is not None and minutes >= 14 * 60 + 30

    def is_friday_afternoon(rt: RoundTrip) -> bool:
        minutes = entry_minutes(rt)
        return rt.entry.weekday() == 4 and minutes is not None and minutes >= 13 * 60

    def is_gap_chase(rt: RoundTrip) -> bool:
        gap = rt.extras.get("gap_pct")
        return gap is not None and gap > 8

    def is_hot_rsi(rt: RoundTrip) -> bool:
        rsi = rt.extras.get("rsi")
        return rsi is not None and rsi > 78

    def is_oversized(rt: RoundTrip) -> bool:
        return oversized_cut is not None and dollar_risk(rt) > oversized_cut + 1e-6

    leak_defs = [
        ("Late-day entries", "Filled at or after 2:30pm ET", is_late, "has_time"),
        ("Revenge re-entries", "Opened within 60 minutes of a losing exit", lambda rt: rt.revenge, "fills"),
        ("Held 10+ sessions", "Exit is 10 or more sessions after entry", lambda rt: rt.hold_sessions >= 10, "fills"),
        ("Gap chase", "Opening gap above 8%", is_gap_chase, "gap_pct"),
        ("Extended RSI", "RSI above 78 at entry", is_hot_rsi, "rsi"),
        ("Oversized risk", "Dollar risk above 2× the median", is_oversized, "fills"),
        ("Friday afternoon", "Friday entry at or after 1:00pm ET", is_friday_afternoon, "has_time"),
    ]
    leaks = []
    for name, note, pred, need in leak_defs:
        if need not in {"fills", "has_time"} and need not in columns:
            leaks.append({"name": name, "note": note, "trades": None, "pnl": None, "available": False})
            continue
        if need == "has_time" and closed and not any(rt.has_time for rt in closed):
            leaks.append({"name": name, "note": note, "trades": None, "pnl": None, "available": False})
            continue
        chosen = [rt for rt in closed if pred(rt)]
        leaks.append(
            {
                "name": name,
                "note": note,
                "trades": len(chosen),
                "pnl": pysum(rt.pnl for rt in chosen) if chosen else 0.0,
                "available": True,
            }
        )

    wins = [rt for rt in closed if rt.pnl > 0]
    losses = [rt for rt in closed if rt.pnl < 0]
    net = pysum(rt.pnl for rt in closed) if closed else 0.0
    gross_win = pysum(rt.pnl for rt in wins) if wins else 0.0
    gross_loss = pysum(rt.pnl for rt in losses) if losses else 0.0
    if not closed:
        profit_factor = None
    elif gross_loss == 0:
        profit_factor = math.inf if gross_win > 0 else None
    else:
        profit_factor = gross_win / abs(gross_loss)
    expectancy = (net / len(closed)) if closed else None
    r_values = [rt.r for rt in closed if rt.r is not None]
    expectancy_r = (pysum(r_values) / len(r_values)) if r_values else None
    win_rate = (100.0 * len(wins) / len(closed)) if closed else None
    drawdown = max_drawdown(closed)
    buckets = {key: 0 for key, _label in BUCKET_ORDER}
    for rt in closed:
        if rt.r is None:
            continue
        buckets[r_bucket(rt.r)] += 1

    def setup_card(name: str) -> dict:
        group = [rt for rt in closed if rt.setup == name]
        group_wins = sum(1 for rt in group if rt.pnl > 0)
        return {
            "trades": len(group),
            "pnl": pysum(rt.pnl for rt in group) if group else 0.0,
            "win_rate": (100.0 * group_wins / len(group)) if group else None,
        }

    by_symbol = _group(closed, lambda rt: rt.symbol)
    ranked = sorted(by_symbol, key=lambda row: row["pnl"], reverse=True)
    best = [row for row in ranked if row["pnl"] > 0][:5]
    worst = [row for row in reversed(ranked) if row["pnl"] < 0][:5]

    hold_rows = {key: {"key": key, "pnl": 0.0, "trades": 0, "win_rate": 0.0} for key, _ in HOLD_ORDER}
    for row in _group(closed, lambda rt: hold_bucket(rt.hold_sessions)):
        hold_rows[row["key"]] = row
    holds = [hold_rows[key] | {"label": label} for key, label in HOLD_ORDER]

    regime_rows = _group(
        closed,
        lambda rt: REGIME_LABEL.get(rt.extras.get("spy_regime") or "", "Unknown"),
    )
    regime_order = ["Up day", "Range day", "Down day", "Unknown"]
    regimes = []
    for label in regime_order:
        found = next((row for row in regime_rows if row["key"] == label), None)
        if found is None and label == "Unknown" and "spy_regime" in columns:
            continue
        if found is None and label != "Unknown" and "spy_regime" not in columns:
            continue
        regimes.append(
            found
            or {"key": label, "pnl": 0.0, "trades": 0, "win_rate": 0.0}
        )
    if "spy_regime" not in columns:
        regimes = [{"key": "Unknown", "pnl": net, "trades": len(closed), "win_rate": win_rate or 0.0, "missing": True}]

    time_rows = {label: {"key": label, "pnl": 0.0, "trades": 0, "win_rate": 0.0} for label in TIME_ORDER}
    for row in _group(closed, time_bucket):
        time_rows[row["key"]] = row
    times = [time_rows[label] for label in TIME_ORDER if time_rows[label]["trades"]]

    dow_rows = {label: {"key": label, "pnl": 0.0, "trades": 0, "win_rate": 0.0} for label in DOW_ORDER}
    for row in _group(closed, lambda rt: rt.entry.strftime("%A")):
        if row["key"] in dow_rows:
            dow_rows[row["key"]] = row
    dows = [dow_rows[label] for label in DOW_ORDER if dow_rows[label]["trades"]]

    edge = []
    edge_specs = (
        ("adr", "ADR", "pct"),
        ("rsi", "RSI", "num"),
        ("ema_dist_pct", "EMA distance", "signed_pct"),
        ("sma_dist_pct", "SMA distance", "signed_pct"),
        ("dollar_volume", "$ volume", "vol"),
        ("gap_pct", "Gap", "signed_pct"),
        ("week52_pct", "52w high distance", "signed_pct"),
    )
    for key, label, kind in edge_specs:
        edge.append(
            {
                "label": label,
                "kind": kind,
                "winners": _median_metric(wins, key, columns),
                "losers": _median_metric(losses, key, columns),
                "available": key in columns,
            }
        )

    gross_by_symbol = [row for row in by_symbol if row["pnl"] > 0]
    gross_profit_symbols = pysum(row["pnl"] for row in gross_by_symbol) if gross_by_symbol else 0.0
    top_sorted = sorted(gross_by_symbol, key=lambda row: row["pnl"], reverse=True)
    top1 = top_sorted[0] if top_sorted else None
    top3 = top_sorted[:3]
    top3_pnl = pysum(row["pnl"] for row in top3) if top3 else 0.0
    counts: dict[str, int] = defaultdict(int)
    for rt in closed:
        counts[rt.symbol] += 1
    busiest_symbol = max(counts.items(), key=lambda kv: kv[1]) if counts else None
    entries_by_day: dict[date, int] = defaultdict(int)
    for rt in closed:
        entries_by_day[rt.entry.date()] += 1
    busiest_day = max(entries_by_day.items(), key=lambda kv: kv[1]) if entries_by_day else None
    streak = max_streak = 0
    for rt in closed:
        if rt.pnl < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    loser_respected = [rt for rt in losses if rt.sell + 1e-9 >= rt.stop * 0.998]
    defined_stops = [rt for rt in closed if rt.stop_source != "fallback"]
    if closed:
        span_days = (closed[-1].exit.date() - closed[0].entry.date()).days
        weeks = max(span_days / 7.0, 1 / 7)
        trades_per_week = len(closed) / weeks
    else:
        trades_per_week = None
    win_hold = pystats_median([rt.hold_sessions for rt in wins]) if wins else None
    loss_hold = pystats_median([rt.hold_sessions for rt in losses]) if losses else None

    today_rows = [rt for rt in closed if rt.exit.date() == today]
    last_day = closed[-1].exit.date() if closed else None
    last_rows = [rt for rt in closed if last_day and rt.exit.date() == last_day]

    open_rows = []
    for pos in positions:
        last = pos.get("last")
        unreal = ((last - pos["avg_price"]) * pos["qty"]) if last is not None else None
        risk = None
        if pos.get("stop") is not None and pos["stop"] < pos["avg_price"]:
            risk = (pos["avg_price"] - pos["stop"]) * pos["qty"]
        open_rows.append({**pos, "unreal": unreal, "risk": risk, "source": "open_positions"})
    if not open_rows:
        for lot in open_lots:
            open_rows.append(
                {
                    "symbol": lot.symbol,
                    "qty": lot.qty,
                    "avg_price": lot.buy,
                    "stop": lot.stop,
                    "opened_at": lot.entry,
                    "last": None,
                    "unreal": None,
                    "risk": (lot.buy - lot.stop) * lot.qty if lot.stop < lot.buy else None,
                    "source": "fills",
                }
            )

    recent = list(reversed(closed[-20:]))
    audit = {
        "closed": closed,
        "open_lots": open_lots,
        "open_rows": open_rows,
        "columns": columns,
        "safety": safety,
        "today": today,
        "unmatched_sell_qty": unmatched,
        "net": net,
        "round_trips": len(closed),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "expectancy_r": expectancy_r,
        "drawdown": drawdown,
        "buckets": buckets,
        "focus": setup_card("focus"),
        "avoid": setup_card("avoid"),
        "leaks": leaks,
        "holds": holds,
        "regimes": regimes,
        "times": times,
        "dows": dows,
        "edge": edge,
        "best": best,
        "worst": worst,
        "weekly": weekly_equity(closed),
        "recent": recent,
        "median_risk": median_risk,
        "concentration": {
            "symbols": len(counts),
            "top1": top1,
            "top1_share": (100.0 * top1["pnl"] / gross_profit_symbols) if top1 and gross_profit_symbols else None,
            "top3_share": (100.0 * top3_pnl / gross_profit_symbols) if gross_profit_symbols else None,
            "busiest_symbol": busiest_symbol,
            "busiest_share": (100.0 * busiest_symbol[1] / len(closed)) if busiest_symbol and closed else None,
            "busiest_day": busiest_day,
            "max_streak": max_streak,
            "stop_coverage": (100.0 * len(defined_stops) / len(closed)) if closed else None,
            "stops_respected": (100.0 * len(loser_respected) / len(losses)) if losses else None,
            "trades_per_week": trades_per_week,
            "win_hold": win_hold,
            "loss_hold": loss_hold,
            "commissions": pysum(rt.commission for rt in closed) if closed else 0.0,
        },
        "today_pnl": pysum(rt.pnl for rt in today_rows) if today_rows else 0.0,
        "today_trades": len(today_rows),
        "last_day": last_day,
        "last_pnl": pysum(rt.pnl for rt in last_rows) if last_rows else 0.0,
        "last_trades": len(last_rows),
        "first_day": closed[0].entry.date() if closed else None,
        "last_exit": closed[-1].exit.date() if closed else None,
    }
    return audit


def fmt_money(value, digits=0) -> str:
    value = _as_float(value)
    if value is None:
        return "—"
    sign = "+" if value > 0.005 else ("−" if value < -0.005 else "")
    return f"{sign}${abs(value):,.{digits}f}"


def fmt_plain_money(value, digits=0) -> str:
    value = _as_float(value)
    if value is None:
        return "—"
    sign = "−" if value < -0.005 else ""
    return f"{sign}${abs(value):,.{digits}f}"


def fmt_pct(value, digits=1, signed=False) -> str:
    value = _as_float(value)
    if value is None:
        return "N/A"
    if signed:
        sign = "+" if value > 0 else ("−" if value < 0 else "")
        return f"{sign}{abs(value):.{digits}f}%"
    return f"{value:.{digits}f}%"


def fmt_num(value, digits=1) -> str:
    value = _as_float(value)
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}"


def fmt_vol(value) -> str:
    value = _as_float(value)
    if value is None:
        return "N/A"
    if abs(value) >= 1e9:
        return f"${value / 1e9:.1f}B"
    if abs(value) >= 1e6:
        return f"${value / 1e6:.0f}M"
    return f"${value:,.0f}"


def fmt_r(value) -> str:
    value = _as_float(value)
    if value is None:
        return "—"
    if value > 0:
        return f"+{value:.2f}R"
    if value < 0:
        return f"−{abs(value):.2f}R"
    return "0.00R"


def fmt_date(value: date | datetime | None, with_year=True) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        value = value.date()
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    if with_year:
        return f"{months[value.month - 1]} {value.day}, {value.year}"
    return f"{months[value.month - 1]} {value.day}"


def _sessions(value) -> str:
    value = _as_float(value)
    if value is None:
        return "—"
    n = int(round(value))
    return f"{n} session" if n == 1 else f"{n} sessions"


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def _cls_money(value) -> str:
    value = _as_float(value) or 0.0
    if value > 0.005:
        return "pos"
    if value < -0.005:
        return "neg"
    return "flat"


def diverging_html(rows: list[dict], label_key="key") -> str:
    amounts = [abs(_as_float(row["pnl"]) or 0.0) for row in rows]
    peak = max(amounts) if amounts else 1.0
    peak = peak or 1.0
    parts = []
    for row in rows:
        pnl = _as_float(row["pnl"]) or 0.0
        width = min(100.0, abs(pnl) / peak * 100.0)
        bar = f'<span class="bar {"gain" if pnl >= 0 else "loss"}" style="width:{width:.2f}%"></span>'
        if abs(pnl) < 0.005:
            left = right = ""
        elif pnl > 0:
            left, right = "", bar
        else:
            left, right = bar, ""
        trades = row.get("trades")
        win = row.get("win_rate")
        sub = ""
        if trades is not None:
            win_txt = f" · {win:.0f}% win" if win is not None and trades else ""
            sub = f'<small>{trades} trade{"s" if trades != 1 else ""}{win_txt}</small>'
        parts.append(
            f'<div class="divbar"><div class="lbl"><span>{esc(row.get("label", row.get(label_key)))}</span>{sub}</div>'
            f'<div class="track"><div class="side left">{left}</div><div class="side right">{right}</div></div>'
            f'<div class="amt {_cls_money(pnl)}">{fmt_money(pnl)}</div></div>'
        )
    return "\n".join(parts)


def equity_svg(points: list[dict]) -> str:
    if not points:
        return '<p class="empty">No closed round trips yet. Drop fills into data/trades.csv and rerun compute_perf.py.</p>'
    values = [0.0] + [float(p["pnl"]) for p in points]
    width, height = 960, 320
    pad_l, pad_r, pad_t, pad_b = 88, 24, 22, 36
    ymin = min(0.0, min(values))
    ymax = max(0.0, max(values))
    span = ymax - ymin or 1.0
    ymin -= span * 0.08
    ymax += span * 0.12
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b

    def x_at(i: int) -> float:
        if len(values) == 1:
            return pad_l
        return pad_l + inner_w * i / (len(values) - 1)

    def y_at(v: float) -> float:
        return pad_t + inner_h * (1 - (v - ymin) / (ymax - ymin))

    coords = [(x_at(i), y_at(v)) for i, v in enumerate(values)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    zero_y = y_at(0.0)
    area = f"{coords[0][0]:.1f},{zero_y:.1f} " + line + f" {coords[-1][0]:.1f},{zero_y:.1f}"
    ticks = _ticks(ymin, ymax)
    grid = []
    for tick in ticks:
        y = y_at(tick)
        grid.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" class="grid"/>'
            f'<text x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end" class="tick">{esc(fmt_plain_money(tick))}</text>'
        )
    label_step = max(1, len(points) // 6)
    labels = []
    for i, point in enumerate(points):
        if i % label_step != 0 and i != len(points) - 1:
            continue
        labels.append(
            f'<text x="{x_at(i + 1):.1f}" y="{height - 10}" text-anchor="middle" class="tick">{esc(fmt_date(point["week"], with_year=False))}</text>'
        )
    end_x, end_y = coords[-1]
    end_label = fmt_money(values[-1])
    return f'''<svg viewBox="0 0 {width} {height}" role="img" aria-label="Weekly cumulative realized P and L ending at {esc(end_label)}">
      <defs>
        <linearGradient id="eqFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#00f0ff" stop-opacity="0.22"/>
          <stop offset="100%" stop-color="#00f0ff" stop-opacity="0.02"/>
        </linearGradient>
      </defs>
      {''.join(grid)}
      <line x1="{pad_l}" y1="{zero_y:.1f}" x2="{width - pad_r}" y2="{zero_y:.1f}" class="zero"/>
      <polygon points="{area}" fill="url(#eqFill)"/>
      <polyline points="{line}" class="eq-line"/>
      <circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="4.5" class="eq-dot"/>
      <text x="{end_x - 8:.1f}" y="{max(16, end_y - 10):.1f}" text-anchor="end" class="end-label">{esc(end_label)}</text>
      {''.join(labels)}
    </svg>'''


def _ticks(ymin: float, ymax: float) -> list[float]:
    span = ymax - ymin
    raw = span / 4 if span else 1
    mag = 10 ** math.floor(math.log10(abs(raw))) if raw else 1
    step = mag
    for candidate in (1, 2, 2.5, 5, 10):
        if candidate * mag >= raw * 0.75:
            step = candidate * mag
            break
    start = math.ceil((ymin - 1e-9) / step) * step
    ticks = []
    value = start
    guard = 0
    while value <= ymax + step * 0.01 and guard < 12:
        if ymin - 1e-6 <= value <= ymax + 1e-6:
            ticks.append(value)
        value += step
        guard += 1
    return ticks


def histogram_html(buckets: dict) -> str:
    peak = max(buckets.values()) if buckets else 1
    peak = peak or 1
    parts = []
    for key, label in BUCKET_ORDER:
        count = buckets.get(key, 0)
        height = 8 if count == 0 else max(8, 100 * count / peak)
        tone = "loss" if key in {"le_neg2", "neg2_neg1", "neg1_0"} else "gain"
        parts.append(
            f'<div class="hcol"><div class="hcount">{count}</div>'
            f'<div class="htrack"><div class="hbar {tone}" style="height:{height:.1f}%"></div></div>'
            f'<div class="hlabel">{esc(label)}</div></div>'
        )
    return "\n".join(parts)


def edge_html(rows: list[dict]) -> str:
    body = []
    for row in rows:
        if not row["available"] or row["winners"] is None or row["losers"] is None:
            winners = losers = spread = "N/A"
        else:
            winners = _fmt_edge(row["winners"], row["kind"])
            losers = _fmt_edge(row["losers"], row["kind"])
            delta = row["winners"] - row["losers"]
            spread = _fmt_edge(delta, row["kind"])
        body.append(
            f"<tr><th scope=\"row\">{esc(row['label'])}</th><td>{winners}</td><td>{losers}</td><td>{spread}</td></tr>"
        )
    return "\n".join(body)


def _fmt_edge(value, kind: str) -> str:
    if kind == "pct":
        return fmt_pct(value, 1)
    if kind == "signed_pct":
        return fmt_pct(value, 1, signed=True)
    if kind == "vol":
        return fmt_vol(value)
    return fmt_num(value, 1)


def leaks_html(leaks: list[dict]) -> str:
    ranked = sorted(
        leaks,
        key=lambda row: (row["pnl"] is None, row["pnl"] if row["pnl"] is not None else 0),
    )
    parts = []
    for row in ranked:
        if not row["available"]:
            money = "N/A"
            trades = "needs column"
            tone = "flat"
        else:
            money = fmt_money(row["pnl"])
            trades = f'{row["trades"]} trade{"s" if row["trades"] != 1 else ""}'
            tone = _cls_money(row["pnl"])
        parts.append(
            f'<div class="leak"><div><div class="leak-name">{esc(row["name"])}</div>'
            f'<div class="leak-note">{esc(row["note"])}</div></div>'
            f'<div class="leak-n">{trades}</div><div class="leak-pnl {tone}">{money}</div></div>'
        )
    return "\n".join(parts)


def trades_table(rows: list[RoundTrip]) -> str:
    if not rows:
        return '<p class="empty">No closed round trips.</p>'
    body = []
    for rt in rows:
        key = r_bucket(rt.r) if rt.r is not None else "na"
        hold = dict(HOLD_ORDER).get(hold_bucket(rt.hold_sessions), "")
        body.append(
            "<tr>"
            f"<td>{esc(fmt_date(rt.exit))}</td>"
            f"<td class=\"sym\">{esc(rt.symbol)}</td>"
            f"<td>{esc(hold)}</td>"
            f"<td class=\"num {_cls_money(rt.pnl)}\">{fmt_money(rt.pnl)}</td>"
            f"<td class=\"num\"><span class=\"r-pill r-{key}\">{fmt_r(rt.r)}</span></td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table class="trades"><thead><tr>'
        "<th>Exit</th><th>Symbol</th><th>Hold</th><th>P&amp;L</th><th>R</th>"
        "</tr></thead><tbody>"
        + "\n".join(body)
        + "</tbody></table></div>"
    )


def open_table(rows: list[dict]) -> str:
    if not rows:
        return '<p class="empty">No open positions in open_positions.json.</p>'
    body = []
    for row in rows:
        opened = row.get("opened_at")
        opened_s = fmt_date(opened) if opened else "—"
        stop = row.get("stop")
        last = row.get("last")
        body.append(
            "<tr>"
            f"<td class=\"sym\">{esc(row['symbol'])}</td>"
            f"<td class=\"num\">{row['qty']:,.0f}</td>"
            f"<td class=\"num\">{row['avg_price']:,.2f}</td>"
            f"<td class=\"num\">{'—' if stop is None else f'{stop:,.2f}'}</td>"
            f"<td class=\"num\">{'—' if last is None else f'{last:,.2f}'}</td>"
            f"<td class=\"num {_cls_money(row.get('unreal'))}\">{fmt_money(row.get('unreal'))}</td>"
            f"<td class=\"num\">{fmt_plain_money(row.get('risk'))}</td>"
            f"<td>{esc(opened_s)}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table class="trades"><thead><tr>'
        "<th>Symbol</th><th>Qty</th><th>Avg</th><th>Stop</th><th>Last</th><th>Unrealized</th><th>Risk $</th><th>Opened</th>"
        "</tr></thead><tbody>"
        + "\n".join(body)
        + "</tbody></table></div>"
    )


def bot_html(safety: dict | None) -> str:
    if not safety:
        return (
            '<div class="bot"><span class="dot idle"></span><strong>NO CYCLE</strong>'
            "<span>No safety-check-log.jsonl yet. The last line of that file drives this strip.</span></div>"
        )
    status = safety["status"].upper()
    alive = status in {"ACTIVE", "OK", "RUNNING", "GREEN"}
    when = safety.get("timestamp")
    when_s = "—"
    if isinstance(when, datetime):
        suffix = " UTC" if str(safety.get("raw_ts") or "").endswith("Z") else ""
        when_s = when.strftime("%b %d, %Y · %H:%M") + suffix
    detail = safety.get("detail") or "cycle recorded"
    return (
        f'<div class="bot"><span class="dot {"live" if alive else "idle"}"></span>'
        f"<strong>{esc(status)}</strong>"
        f"<span>Last cycle · {esc(safety['cycle'])} · {esc(when_s)}</span>"
        f'<span class="bot-detail">{esc(detail)}</span></div>'
    )


PAGE_CSS = """
:root {
  --bg: #070b12;
  --ink: #e8eef6;
  --muted: #93a4b8;
  --card: #10151f;
  --line: rgba(0, 240, 255, 0.18);
  --yellow: #fcee0a;
  --cyan: #00f0ff;
  --magenta: #ff2a6d;
  --green: #3dff9a;
  --green-bar: #3dff9a;
  --red: #ff5c7a;
  --red-bar: #ff5c7a;
  --shadow: 0 16px 36px rgba(0, 0, 0, 0.45);
  --cut: 14px;
  --mono: "Share Tech Mono", ui-monospace, "Cascadia Code", "Segoe UI Mono", monospace;
  --display: Rajdhani, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; color-scheme: dark; }
body {
  margin: 0;
  background-color: var(--bg);
  background-image:
    repeating-linear-gradient(0deg, rgba(255, 255, 255, 0.015) 0 1px, transparent 1px 4px),
    linear-gradient(rgba(0, 240, 255, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 240, 255, 0.035) 1px, transparent 1px),
    radial-gradient(820px 420px at 100% -10%, rgba(255, 42, 109, 0.16), transparent 55%),
    radial-gradient(720px 380px at 0% 0%, rgba(252, 238, 10, 0.07), transparent 50%);
  background-size: auto, 56px 56px, 56px 56px, auto, auto;
  color: var(--ink);
  font-family: var(--display);
  font-size: 16px;
  line-height: 1.45;
  -webkit-font-smoothing: antialiased;
}
.banner {
  background: var(--yellow);
  color: #0a0c10;
  text-align: center;
  font-family: var(--mono);
  font-weight: 700;
  letter-spacing: 0.16em;
  font-size: 0.78rem;
  padding: 0.62rem 1rem;
}
.goldbar {
  height: 3px;
  background: linear-gradient(90deg, var(--yellow) 0%, var(--cyan) 52%, var(--magenta) 100%);
}
.wrap { max-width: 1120px; margin: 0 auto; padding: 1.6rem 1.15rem 3rem; }
.mast { margin: 0.4rem 0 1rem; }
.eyebrow {
  margin: 0 0 0.35rem;
  color: var(--yellow);
  letter-spacing: 0.22em;
  font-size: 0.74rem;
  font-weight: 700;
  font-family: var(--mono);
  text-transform: uppercase;
}
h1 {
  font-family: var(--display);
  font-weight: 700;
  font-size: clamp(2.05rem, 4.4vw, 3.35rem);
  letter-spacing: 0.02em;
  line-height: 1.02;
  margin: 0 0 0.45rem;
  text-transform: uppercase;
  color: #f4f7fb;
}
.sub { margin: 0; color: var(--muted); font-size: 1.02rem; font-family: var(--mono); }
.bot {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem 0.8rem;
  align-items: center;
  background: #10151f;
  border: 1px solid rgba(252, 238, 10, 0.55);
  border-radius: 0;
  box-shadow: var(--shadow);
  clip-path: polygon(0 0, calc(100% - 12px) 0, 100% 12px, 100% 100%, 12px 100%, 0 calc(100% - 12px));
  padding: 0.55rem 0.95rem;
  margin: 1rem 0 1.1rem;
  font-size: 0.92rem;
  font-family: var(--mono);
}
.bot strong { letter-spacing: 0.12em; font-size: 0.82rem; color: var(--yellow); }
.bot-detail { color: var(--muted); }
.dot {
  width: 0.7rem; height: 0.7rem; border-radius: 50%;
  background: #8d8880; display: inline-block;
}
.dot.live { background: var(--green); box-shadow: 0 0 0 0 rgba(61, 255, 154, 0.55); animation: pulse 1.8s infinite; }
.dot.idle { background: var(--red); box-shadow: 0 0 8px rgba(255, 92, 122, 0.45); }
@keyframes pulse {
  70% { box-shadow: 0 0 0 8px rgba(61, 255, 154, 0); }
  100% { box-shadow: 0 0 0 0 rgba(61, 255, 154, 0); }
}
.kpi-strip {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 0.75rem;
  margin-bottom: 0.9rem;
}
.card, .kpi {
  background: #10151f;
  border: 1px solid var(--line);
  border-radius: 0;
  box-shadow: var(--shadow);
  display: block;
  clip-path: polygon(0 0, calc(100% - var(--cut)) 0, 100% var(--cut), 100% 100%, var(--cut) 100%, 0 calc(100% - var(--cut)));
}
.kpi { padding: 0.9rem 0.95rem 0.8rem; border-top: 2px solid var(--yellow); }
.kpi .k, .section-k {
  margin: 0;
  color: var(--cyan);
  font-size: 0.68rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  font-weight: 700;
  font-family: var(--mono);
}
.kpi .v {
  font-family: var(--display);
  font-weight: 700;
  font-size: clamp(1.45rem, 2.1vw, 1.9rem);
  margin: 0.28rem 0 0.1rem;
  letter-spacing: 0;
  color: #f4f7fb;
  font-variant-numeric: tabular-nums;
  text-shadow: none;
}
.kpi .s, .note { margin: 0; color: var(--muted); font-size: 0.82rem; }
.pos { color: var(--green); text-shadow: none; }
.neg { color: var(--red); text-shadow: none; }
.flat { color: #f4f7fb; }
.card { padding: 1.05rem 1.15rem 1.1rem; margin: 0.85rem 0; }
.card h2 {
  font-family: var(--display);
  font-size: 1.45rem;
  font-weight: 700;
  margin: 0 0 0.2rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #f4f7fb;
}
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 0.85rem; }
.triple { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0.85rem; }
.split { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(16rem, 0.9fr); gap: 0.85rem; align-items: start; }
.focus {
  background: linear-gradient(180deg, #152833, #10151f);
  border-color: rgba(0, 240, 255, 0.55);
  box-shadow: var(--shadow), inset 0 3px 0 var(--cyan);
}
.avoid {
  background: linear-gradient(180deg, #2a1520, #10151f);
  border-color: rgba(255, 42, 109, 0.6);
  box-shadow: var(--shadow), inset 0 3px 0 var(--magenta);
}
.big, .today-num {
  font-family: var(--display);
  font-weight: 700;
  letter-spacing: 0;
  font-variant-numeric: tabular-nums;
  text-shadow: none;
}
.big {
  font-size: 2rem;
  margin: 0.35rem 0 0.15rem;
}
ul.criteria { margin: 0.7rem 0 0; padding-left: 1.1rem; color: var(--ink); }
ul.criteria li { margin: 0.18rem 0; }
svg { width: 100%; height: auto; display: block; }
.eq-line { fill: none; stroke: var(--cyan); stroke-width: 2.4; stroke-linejoin: round; stroke-linecap: round; }
.eq-dot { fill: var(--yellow); }
.grid { stroke: rgba(0, 240, 255, 0.14); stroke-width: 1; }
.zero { stroke: rgba(252, 238, 10, 0.55); stroke-width: 1; stroke-dasharray: 4 4; }
.tick { fill: #93a4b8; font-size: 11px; font-family: var(--mono); }
.end-label { fill: var(--green); font-size: 13px; font-weight: 700; font-family: var(--display); }
.divbar {
  display: grid;
  grid-template-columns: minmax(6.2rem, 9.2rem) minmax(0, 1fr) 6.3rem;
  gap: 0.65rem;
  align-items: center;
  margin: 0.42rem 0;
}
.lbl { display: flex; flex-direction: column; line-height: 1.15; }
.lbl span { font-weight: 600; font-size: 0.92rem; }
.lbl small { color: var(--muted); font-size: 0.75rem; font-family: var(--mono); }
.track { display: grid; grid-template-columns: 1fr 1fr; height: 14px; position: relative; }
.track::before {
  content: "";
  position: absolute;
  left: 50%;
  top: -3px;
  bottom: -3px;
  width: 1px;
  background: rgba(252, 238, 10, 0.45);
}
.side { display: flex; align-items: center; height: 14px; }
.side.left { justify-content: flex-end; padding-right: 3px; }
.side.right { justify-content: flex-start; padding-left: 3px; }
.bar { display: block; height: 10px; border-radius: 0; min-width: 6px; }
.bar.gain { background: var(--green-bar); }
.bar.loss { background: var(--red-bar); }
.amt { text-align: right; font-variant-numeric: tabular-nums; font-weight: 700; font-size: 0.92rem; font-family: var(--display); }
.leak {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 0.8rem;
  align-items: center;
  padding: 0.62rem 0;
  border-top: 1px solid var(--line);
}
.leak:first-of-type { border-top: 0; }
.leak-name { font-weight: 700; letter-spacing: 0.02em; }
.leak-note { color: var(--muted); font-size: 0.82rem; }
.leak-n { color: var(--muted); font-size: 0.85rem; white-space: nowrap; font-family: var(--mono); }
.leak-pnl { font-weight: 700; font-variant-numeric: tabular-nums; min-width: 6.2rem; text-align: right; font-family: var(--display); }
.edge { width: 100%; border-collapse: collapse; margin-top: 0.4rem; }
.edge th, .edge td { text-align: right; padding: 0.55rem 0.4rem; border-bottom: 1px solid var(--line); font-variant-numeric: tabular-nums; }
.edge th:first-child, .edge td:first-child, .edge thead th:first-child { text-align: left; }
.edge thead th { color: var(--cyan); font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; font-weight: 700; font-family: var(--mono); }
.stats { display: grid; gap: 0.55rem; margin-top: 0.35rem; }
.stat { display: flex; justify-content: space-between; gap: 0.8rem; border-top: 1px solid var(--line); padding-top: 0.45rem; }
.stat span { color: var(--muted); }
.stat strong { font-variant-numeric: tabular-nums; text-align: right; }
.today-num {
  font-size: 2.4rem;
  margin: 0.2rem 0;
}
.hrow { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 0.45rem; align-items: end; min-height: 180px; margin-top: 0.6rem; }
.hcol { display: flex; flex-direction: column; align-items: center; height: 180px; }
.hcount { font-size: 0.8rem; color: var(--muted); font-variant-numeric: tabular-nums; font-family: var(--mono); }
.htrack { flex: 1; width: 100%; display: flex; align-items: flex-end; justify-content: center; }
.hbar { width: 62%; border-radius: 0; }
.hbar.gain { background: var(--green-bar); }
.hbar.loss { background: var(--red-bar); }
.hlabel { font-size: 0.72rem; color: var(--muted); text-align: center; margin-top: 0.35rem; line-height: 1.2; font-family: var(--mono); }
.table-wrap { overflow-x: auto; }
table.trades { width: 100%; border-collapse: collapse; color: var(--ink); }
table.trades th, table.trades td { padding: 0.48rem 0.4rem; border-bottom: 1px solid var(--line); text-align: left; white-space: nowrap; }
table.trades th { color: var(--cyan); font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; font-family: var(--mono); font-weight: 400; }
table.trades td.num { text-align: right; font-variant-numeric: tabular-nums; }
.sym { font-weight: 400; letter-spacing: 0.06em; font-family: var(--mono); color: var(--yellow); }
.r-pill {
  display: inline-block;
  min-width: 4.6rem;
  text-align: center;
  border-radius: 0;
  padding: 0.12rem 0.45rem;
  font-weight: 400;
  font-size: 0.82rem;
  font-family: var(--mono);
}
.r-le_neg2 { background: #3a1020; color: #ffb3c2; }
.r-neg2_neg1 { background: #2c1420; color: #ff8fa3; }
.r-neg1_0 { background: #261820; color: #ff5c7a; }
.r-0_1 { background: #0e2a22; color: #9dffd0; }
.r-1_2 { background: #0c3328; color: #3dff9a; }
.r-2_3 { background: #0a3d2c; color: #b8ffdf; }
.r-gt3 { background: #3dff9a; color: #04140c; }
.r-na { background: #1a1e28; color: var(--muted); }
.empty { color: var(--muted); margin: 0.4rem 0; }
.foot { color: var(--muted); font-size: 0.85rem; margin-top: 1.2rem; font-family: var(--mono); }
::selection { background: var(--yellow); color: #0a0c10; }
@media (max-width: 980px) {
  .kpi-strip { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .pair, .triple, .split { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  .kpi-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .leak { grid-template-columns: 1fr auto; }
  .leak-n { grid-column: 1; }
  .divbar { grid-template-columns: 1fr; }
  .amt { text-align: left; }
}
@media (prefers-reduced-motion: reduce) {
  .dot.live { animation: none; }
}
"""


def render_html(audit: dict) -> str:
    end_candidates = []
    if audit["last_exit"]:
        end_candidates.append(audit["last_exit"])
    for row in audit["open_rows"]:
        opened = row.get("opened_at")
        if isinstance(opened, datetime):
            end_candidates.append(opened.date())
    last_seen = max(end_candidates) if end_candidates else None
    if audit["first_day"] and last_seen:
        span = f"{fmt_date(audit['first_day'])} – {fmt_date(last_seen)}"
    elif audit["first_day"]:
        span = fmt_date(audit["first_day"])
    else:
        span = "No round trips in data/trades.csv"
    pf = audit["profit_factor"]
    if pf is None:
        pf_txt = "—"
    elif math.isinf(pf):
        pf_txt = "∞"
    else:
        pf_txt = f"{pf:.2f}"
    wr = "—" if audit["win_rate"] is None else f"{audit['win_rate']:.1f}%"
    exp = "—" if audit["expectancy"] is None else fmt_money(audit["expectancy"])
    exp_r = ""
    if audit["expectancy_r"] is not None:
        exp_r = f"mean {audit['expectancy_r']:+.2f}R"
    dd = audit["drawdown"]
    dd_txt = "—" if not audit["round_trips"] else fmt_money(-dd["dollars"])
    dd_sub = "from peak"
    if dd["pct"] is not None:
        dd_sub = f"{dd['pct']:.1f}% of peak"
    conc = audit["concentration"]
    top1 = conc["top1"]
    top1_txt = "—"
    if top1 and conc["top1_share"] is not None:
        top1_txt = f"{top1['key']} · {conc['top1_share']:.0f}% of gross profit"
    busiest_sym = "—"
    if conc["busiest_symbol"] and conc["busiest_share"] is not None:
        busiest_sym = f"{conc['busiest_symbol'][0]} · {conc['busiest_share']:.0f}% of trades"
    busiest_day = "—"
    if conc["busiest_day"]:
        busiest_day = f"{fmt_date(conc['busiest_day'][0])} · {conc['busiest_day'][1]} entries"
    trow = conc["trades_per_week"]
    today_note = fmt_date(audit["today"])
    if audit["today_trades"] == 0:
        today_extra = f"No round trips closed {today_note}."
    else:
        today_extra = f"{audit['today_trades']} round trip{'s' if audit['today_trades'] != 1 else ''} closed {today_note}."
    last_extra = ""
    if audit["last_day"] and audit["last_day"] != audit["today"]:
        last_n = audit["last_trades"]
        last_extra = (
            f"Last session {fmt_date(audit['last_day'])}: {fmt_money(audit['last_pnl'])} "
            f"across {last_n} round trip{'s' if last_n != 1 else ''}."
        )
    unmatched = ""
    if audit["unmatched_sell_qty"] > 1e-6:
        unmatched = f"<p class=\"note\">Unmatched sell quantity {audit['unmatched_sell_qty']:.2f} was ignored (no older buy under FIFO).</p>"
    trough = ""
    if dd["trough"] is not None and dd["dollars"] > 0:
        trough = f" Max drawdown trough {fmt_date(dd['trough'])}."
    focus_wr = "—" if audit["focus"]["win_rate"] is None else f"{audit['focus']['win_rate']:.0f}% win"
    avoid_wr = "—" if audit["avoid"]["win_rate"] is None else f"{audit['avoid']['win_rate']:.0f}% win"
    cols = audit["columns"]

    def maybe(col: str, sentence: str) -> str:
        if col in cols:
            return sentence
        return sentence + " — column missing, rule skipped"

    marks = [row.get("unreal") for row in audit["open_rows"]]
    if marks and all(mark is not None for mark in marks):
        unreal_total = pysum(marks)
        unreal_line = f'<p class="today-num {_cls_money(unreal_total)}">{fmt_money(unreal_total)}</p><p class="note">Unrealized on the open book, from the last marks. Not included in net P&amp;L.</p>'
    else:
        unreal_line = '<p class="note">No last prices on the open book, so unrealized P&amp;L is blank.</p>'
    generated = datetime.now(ET).strftime("%b %d, %Y %H:%M ET")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>2026 Trading Performance Audit</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&family=Share+Tech+Mono&display=swap" rel="stylesheet">
  <meta name="theme-color" content="#070b12">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>{PAGE_CSS}</style>
</head>
<body>
  <div class="goldbar"></div>
  <div class="banner">STONKJOURNAL · REAL BOOK</div>
  <main class="wrap">
    <header class="mast">
      <p class="eyebrow">Night City // HUD</p>
      <h1>2026 Trading Performance Audit</h1>
      <p class="sub">{esc(span)} · US equities · FIFO round trips · StonkJournal</p>
    </header>
    {bot_html(audit["safety"])}
    <section class="kpi-strip" aria-label="Key results">
      <article class="kpi"><p class="k">Net P&amp;L</p><p class="v {_cls_money(audit["net"])}">{fmt_money(audit["net"])}</p><p class="s">after commissions</p></article>
      <article class="kpi"><p class="k">Round Trips</p><p class="v">{audit["round_trips"]}</p><p class="s">FIFO closed lots</p></article>
      <article class="kpi"><p class="k">Win Rate</p><p class="v">{wr}</p><p class="s">P&amp;L above zero</p></article>
      <article class="kpi"><p class="k">Profit Factor</p><p class="v">{pf_txt}</p><p class="s">gross win / gross loss</p></article>
      <article class="kpi"><p class="k">Expectancy</p><p class="v {_cls_money(audit["expectancy"])}">{exp}</p><p class="s">{esc(exp_r or "per round trip")}</p></article>
      <article class="kpi"><p class="k">Max Drawdown</p><p class="v neg">{dd_txt}</p><p class="s">{esc(dd_sub)}</p></article>
    </section>
    <section class="card">
      <p class="section-k">Equity curve</p>
      <h2>Weekly cumulative P&amp;L</h2>
      <p class="note">Realized round trips, summed through each Friday.{esc(trough)}</p>
      {equity_svg(audit["weekly"])}
    </section>
    <section class="pair">
      <article class="card focus">
        <p class="section-k">Focus</p>
        <h2>Keep taking these</h2>
        <p class="big pos">{fmt_money(audit["focus"]["pnl"])}</p>
        <p class="note">{audit["focus"]["trades"]} trades · {focus_wr}</p>
        <ul class="criteria">
          <li>Entry 9:35–10:45 ET</li>
          <li>{maybe("spy_regime", "SPY day regime is up")}</li>
          <li>{maybe("gap_pct", "Gap between +0.5% and +5%")}</li>
          <li>{maybe("rsi", "RSI between 48 and 70")}</li>
          <li>Held same day or 2–4 sessions</li>
        </ul>
      </article>
      <article class="card avoid">
        <p class="section-k">Avoid</p>
        <h2>These paid for the lesson</h2>
        <p class="big neg">{fmt_money(audit["avoid"]["pnl"])}</p>
        <p class="note">{audit["avoid"]["trades"]} trades · {avoid_wr}</p>
        <ul class="criteria">
          <li>Entry at or after 2:30pm ET</li>
          <li>{maybe("gap_pct", "Opening gap above 8%")}</li>
          <li>{maybe("rsi", "RSI above 78")}</li>
          <li>Held 10 sessions or longer</li>
          <li>Re-entry within 60 minutes of a loss</li>
        </ul>
      </article>
    </section>
    <section class="card">
      <p class="section-k">Hidden leaks</p>
      <h2>Where the book gave it back</h2>
      <p class="note">A round trip can sit in more than one leak. Dollars are realized P&amp;L.</p>
      {leaks_html(audit["leaks"])}
    </section>
    <section class="card">
      <p class="section-k">Holding period</p>
      <h2>P&amp;L by Holding Period</h2>
      <p class="note">Sessions are weekdays from entry to exit. Profit extends right, loss extends left.</p>
      {diverging_html(audit["holds"], "label")}
    </section>
    <section>
      <div class="card" style="margin-bottom:0.4rem">
        <p class="section-k">Entries &amp; exits</p>
        <h2>When the trade was opened</h2>
        <p class="note">SPY-day regime, entry clock, and weekday. Regime is N/A without a spy_regime column — it is not inferred from fills.</p>
      </div>
      <div class="triple">
        <article class="card"><h2 style="font-size:1.15rem">SPY-day regime</h2>{diverging_html(audit["regimes"])}</article>
        <article class="card"><h2 style="font-size:1.15rem">Entry time</h2>{diverging_html(audit["times"])}</article>
        <article class="card"><h2 style="font-size:1.15rem">Day of week</h2>{diverging_html(audit["dows"])}</article>
      </div>
    </section>
    <section class="card">
      <p class="section-k">Edge profile</p>
      <h2>Winners vs losers at entry</h2>
      <p class="note">Median of the entry print. N/A means that column is not in the file.</p>
      <div class="table-wrap"><table class="edge"><thead><tr><th>Measure</th><th>Winners</th><th>Losers</th><th>Spread</th></tr></thead><tbody>
      {edge_html(audit["edge"])}
      </tbody></table></div>
    </section>
    <section class="split">
      <div>
        <article class="card">
          <p class="section-k">P&amp;L by stock</p>
          <h2>Best 5</h2>
          {diverging_html(audit["best"]) if audit["best"] else '<p class="empty">No winning symbols.</p>'}
        </article>
        <article class="card">
          <h2>Worst 5</h2>
          {diverging_html(audit["worst"]) if audit["worst"] else '<p class="empty">No losing symbols.</p>'}
        </article>
      </div>
      <aside class="card">
        <p class="section-k">From the fills</p>
        <h2>Concentration &amp; discipline</h2>
        <div class="stats">
          <div class="stat"><span>Symbols traded</span><strong>{conc["symbols"]}</strong></div>
          <div class="stat"><span>Top symbol</span><strong>{esc(top1_txt)}</strong></div>
          <div class="stat"><span>Top 3 share of gross</span><strong>{"—" if conc["top3_share"] is None else f'{conc["top3_share"]:.0f}%'}</strong></div>
          <div class="stat"><span>Busiest symbol</span><strong>{esc(busiest_sym)}</strong></div>
          <div class="stat"><span>Busiest entry day</span><strong>{esc(busiest_day)}</strong></div>
          <div class="stat"><span>Trades per week</span><strong>{"—" if trow is None else f"{trow:.1f}"}</strong></div>
          <div class="stat"><span>Median dollar risk</span><strong>{fmt_plain_money(audit["median_risk"])}</strong></div>
          <div class="stat"><span>Stop on the fill</span><strong>{"—" if conc["stop_coverage"] is None else f'{conc["stop_coverage"]:.0f}%'}</strong></div>
          <div class="stat"><span>Losers held to stop</span><strong>{"—" if conc["stops_respected"] is None else f'{conc["stops_respected"]:.0f}%'}</strong></div>
          <div class="stat"><span>Median hold, winners</span><strong>{_sessions(conc["win_hold"])}</strong></div>
          <div class="stat"><span>Median hold, losers</span><strong>{_sessions(conc["loss_hold"])}</strong></div>
          <div class="stat"><span>Max loss streak</span><strong>{conc["max_streak"]}</strong></div>
          <div class="stat"><span>Commissions</span><strong>{fmt_plain_money(conc["commissions"], 0)}</strong></div>
        </div>
      </aside>
    </section>
    <section class="pair">
      <article class="card">
        <p class="section-k">Session</p>
        <h2>Today's P&amp;L</h2>
        <p class="today-num {_cls_money(audit["today_pnl"])}">{fmt_money(audit["today_pnl"])}</p>
        <p class="note">{esc(today_extra)}</p>
        <p class="note">{esc(last_extra)}</p>
        <p class="note">Realized only. Open marks are in the positions table and are not added here.</p>
      </article>
      <article class="card">
        <p class="section-k">Open book</p>
        <h2>{len(audit["open_rows"])} open position{"s" if len(audit["open_rows"]) != 1 else ""}</h2>
        {unreal_line}
      </article>
    </section>
    <section class="card">
      <p class="section-k">Distribution</p>
      <h2>R-multiple histogram</h2>
      <p class="note">R = (sell − buy) / (buy − initial stop). Buckets are (−∞, −2], (−2, −1], (−1, 0], (0, 1], (1, 2], (2, 3], (3, ∞).</p>
      <div class="hrow">{histogram_html(audit["buckets"])}</div>
    </section>
    <section class="card">
      <p class="section-k">Open positions</p>
      <h2>Still on the book</h2>
      {open_table(audit["open_rows"])}
    </section>
    <section class="card">
      <p class="section-k">Tape</p>
      <h2>Last 20 closed trades</h2>
      <p class="note">Color follows the R bucket: coral for losses, green for winners.</p>
      {trades_table(audit["recent"])}
      {unmatched}
    </section>
    <p class="foot">Generated {esc(generated)} by compute_perf.py. StonkJournal real book. This page does not connect to a broker and does not place trades.</p>
  </main>
</body>
</html>
"""


def public_summary(audit: dict) -> dict:
    summary = {
        "net_pnl": float(audit["net"]),
        "round_trips": int(audit["round_trips"]),
        "win_rate": None if audit["win_rate"] is None else float(audit["win_rate"]),
        "profit_factor": None
        if audit["profit_factor"] is None or math.isinf(audit["profit_factor"])
        else float(audit["profit_factor"]),
        "expectancy": None if audit["expectancy"] is None else float(audit["expectancy"]),
        "max_drawdown": float(audit["drawdown"]["dollars"]),
        "buckets": {key: int(value) for key, value in audit["buckets"].items()},
        "focus_pnl": float(audit["focus"]["pnl"]),
        "avoid_pnl": float(audit["avoid"]["pnl"]),
        "today_pnl": float(audit["today_pnl"]),
        "open_positions": len(audit["open_rows"]),
    }
    return to_py(summary)


def build_page(root: Path | None = None, today: date | None = None) -> dict:
    root = root or repo_root()
    fills, columns = load_fills(root / "data" / "trades.csv")
    positions = load_positions(root / "data" / "open_positions.json")
    safety = load_safety(root)
    audit = assemble(fills, columns, positions, safety, today=today)
    html_text = render_html(audit)
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "index.html").write_text(html_text, encoding="utf-8")
    (docs / ".nojekyll").write_text("", encoding="utf-8")
    summary = public_summary(audit)
    telegram_notify(
        f"Audit ready. Net {summary['net_pnl']:.0f}. Round trips {summary['round_trips']}. StonkJournal."
    )
    return summary


def main() -> int:
    summary = build_page()
    print(
        f"Net P&L {summary['net_pnl']:.2f} | round trips {summary['round_trips']} | "
        f"win rate {summary['win_rate']} | open {summary['open_positions']}"
    )
    print("READY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
