#!/usr/bin/env python3
"""Write a deterministic SAMPLE book: ~127 round trips, about +$200k.

Illustrative prices and indicators only. Not a brokerage export and not
anyone's live results. Safe to rerun; it overwrites data/trades.csv,
data/open_positions.json, and data/safety-check-log.jsonl.
"""

from __future__ import annotations

import csv
import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import compute_perf as cp

BASE = {
    "NVDA": 131.0,
    "AAPL": 248.0,
    "MSFT": 448.0,
    "META": 612.0,
    "AMZN": 222.0,
    "TSLA": 392.0,
    "AMD": 158.0,
    "AVGO": 234.0,
    "PLTR": 74.0,
    "SMCI": 42.0,
    "COIN": 268.0,
    "ARM": 152.0,
    "CRWD": 342.0,
    "HOOD": 36.0,
    "APP": 124.0,
    "MSTR": 318.0,
    "NET": 92.0,
    "DDOG": 138.0,
    "PANW": 186.0,
    "SNOW": 164.0,
}
WIN_SYMS = ["NVDA", "NVDA", "AVGO", "META", "APP", "ARM", "AAPL", "MSFT", "PANW", "AMZN"]
LOSE_SYMS = ["SMCI", "COIN", "MSTR", "HOOD", "SNOW", "TSLA", "NET", "PLTR"]
START = date(2026, 1, 2)
END = date(2026, 10, 9)


def trading_days(a: date, b: date) -> list[date]:
    out = []
    cursor = a
    while cursor <= b:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def add_sessions(day: date, n: int) -> date:
    cursor = day
    left = n
    while left > 0:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            left -= 1
    return cursor


def at_clock(day: date, minutes: int, rng: random.Random) -> datetime:
    return datetime(day.year, day.month, day.day, minutes // 60, minutes % 60, rng.randint(3, 48))


class Book:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.days = trading_days(START, END)
        self.busy: list[tuple[str, datetime, datetime]] = []
        self.plans: list[dict] = []

    def overlaps(self, symbol: str, start: datetime, end: datetime) -> bool:
        for sym, a, b in self.busy:
            if sym == symbol and start < b and end > a:
                return True
        return False

    def reserve(self, symbol: str, start: datetime, end: datetime) -> None:
        self.busy.append((symbol, start, end))

    def pick_symbol(self, positive: bool) -> str:
        pool = WIN_SYMS if positive else LOSE_SYMS
        if self.rng.random() < 0.18:
            pool = LOSE_SYMS if positive else WIN_SYMS
        return self.rng.choice(pool)

    def schedule(self, *, symbol, entry, exit_at, hold, r, weight, spy, gap, rsi, revenge_parent=False):
        if self.overlaps(symbol, entry, exit_at):
            return False
        self.reserve(symbol, entry, exit_at)
        self.plans.append(
            {
                "symbol": symbol,
                "entry": entry,
                "exit": exit_at,
                "hold": hold,
                "r": r,
                "weight": weight,
                "spy": spy,
                "gap": gap,
                "rsi": rsi,
                "legs": 1,
                "revenge_parent": revenge_parent,
            }
        )
        return True

    def try_place(self, spec: dict, attempts=60) -> bool:
        rng = self.rng
        for _ in range(attempts):
            symbol = spec.get("symbol") or self.pick_symbol(spec["r"] > 0)
            entry_day = spec.get("entry_day") or rng.choice(
                [d for d in self.days if add_sessions(d, spec["hold"]) <= END]
            )
            if add_sessions(entry_day, spec["hold"]) > END:
                continue
            entry = at_clock(entry_day, spec["minutes"], rng)
            exit_day = add_sessions(entry_day, spec["hold"])
            if spec["hold"] == 0:
                exit_m = min(15 * 60 + 50, spec["minutes"] + rng.randint(80, 280))
                if exit_m <= spec["minutes"] + 25:
                    exit_m = min(15 * 60 + 55, spec["minutes"] + 70)
            else:
                exit_m = rng.randint(9 * 60 + 45, 15 * 60 + 20)
            exit_at = at_clock(exit_day, exit_m, rng)
            if exit_at <= entry:
                continue
            if self.schedule(
                symbol=symbol,
                entry=entry,
                exit_at=exit_at,
                hold=spec["hold"],
                r=spec["r"],
                weight=spec["weight"],
                spy=spec["spy"],
                gap=spec["gap"],
                rsi=spec["rsi"],
                revenge_parent=spec.get("revenge_parent", False),
            ):
                return True
        return False


def sample_r(rng: random.Random, kind: str) -> float:
    if kind == "focus":
        return rng.uniform(0.45, 3.5) if rng.random() < 0.74 else -rng.uniform(0.3, 1.05)
    if kind == "avoid":
        return -rng.uniform(0.45, 2.25) if rng.random() < 0.72 else rng.uniform(0.2, 1.05)
    if kind == "loss":
        return -rng.uniform(0.55, 1.6)
    return rng.uniform(0.25, 2.1) if rng.random() < 0.53 else -rng.uniform(0.25, 1.25)


def feature_pack(rng: random.Random, kind: str) -> dict:
    if kind == "focus":
        return {
            "minutes": rng.randint(9 * 60 + 36, 10 * 60 + 43),
            "hold": rng.choice([0, 0, 0, 2, 3, 4]),
            "spy": "up",
            "gap": round(rng.uniform(0.7, 4.6), 2),
            "rsi": round(rng.uniform(50, 68), 1),
            "r": sample_r(rng, "focus"),
            "weight": rng.uniform(0.85, 1.45),
        }
    if kind == "late":
        return {
            "minutes": rng.randint(14 * 60 + 32, 15 * 60 + 40),
            "hold": rng.choice([0, 1, 2]),
            "spy": rng.choice(["up", "range", "down"]),
            "gap": round(rng.uniform(-0.4, 3.5), 2),
            "rsi": round(rng.uniform(46, 70), 1),
            "r": sample_r(rng, "avoid"),
            "weight": rng.uniform(0.8, 1.3),
        }
    if kind == "gap":
        return {
            "minutes": rng.randint(9 * 60 + 40, 10 * 60 + 30),
            "hold": rng.choice([0, 1, 2]),
            "spy": rng.choice(["up", "range"]),
            "gap": round(rng.uniform(8.3, 14.5), 2),
            "rsi": round(rng.uniform(55, 74), 1),
            "r": sample_r(rng, "avoid"),
            "weight": rng.uniform(0.9, 1.5),
        }
    if kind == "rsi":
        return {
            "minutes": rng.randint(9 * 60 + 50, 11 * 60 + 10),
            "hold": rng.choice([0, 1, 3]),
            "spy": rng.choice(["up", "range"]),
            "gap": round(rng.uniform(1.0, 6.5), 2),
            "rsi": round(rng.uniform(79, 89), 1),
            "r": sample_r(rng, "avoid"),
            "weight": rng.uniform(0.8, 1.35),
        }
    if kind == "long":
        return {
            "minutes": rng.randint(9 * 60 + 45, 11 * 60),
            "hold": rng.randint(10, 14),
            "spy": rng.choice(["range", "down", "up"]),
            "gap": round(rng.uniform(-1.0, 3.0), 2),
            "rsi": round(rng.uniform(44, 66), 1),
            "r": sample_r(rng, "avoid"),
            "weight": rng.uniform(1.0, 1.6),
        }
    if kind == "parent":
        return {
            "minutes": rng.randint(10 * 60 + 48, 11 * 60 + 5),
            "hold": 0,
            "spy": rng.choice(["up", "range"]),
            "gap": round(rng.uniform(0.4, 3.2), 2),
            "rsi": round(rng.uniform(48, 66), 1),
            "r": sample_r(rng, "loss"),
            "weight": rng.uniform(0.7, 1.15),
            "revenge_parent": True,
        }
    if kind == "friday":
        return {
            "minutes": rng.randint(13 * 60 + 5, 14 * 60 + 15),
            "hold": rng.choice([0, 1, 5]),
            "spy": rng.choice(["range", "down"]),
            "gap": round(rng.uniform(-0.5, 3.0), 2),
            "rsi": round(rng.uniform(42, 70), 1),
            "r": -rng.uniform(0.4, 1.5),
            "weight": rng.uniform(0.9, 1.4),
            "weekday": 4,
        }
    return {
        "minutes": rng.randint(10 * 60 + 50, 13 * 60 + 10),
        "hold": rng.choice([1, 1, 5, 6, 7, 8]),
        "spy": rng.choice(["range", "up", "down"]),
        "gap": round(rng.uniform(-1.2, 4.2), 2),
        "rsi": round(rng.uniform(43, 74), 1),
        "r": sample_r(rng, "other"),
        "weight": rng.uniform(0.75, 1.35),
    }


def build_plans(rng: random.Random) -> list[dict]:
    book = Book(rng)
    queue = (
        ["focus"] * 40
        + ["late"] * 8
        + ["gap"] * 7
        + ["rsi"] * 7
        + ["long"] * 6
        + ["parent"] * 8
        + ["friday"] * 6
        + ["other"] * 33
    )
    rng.shuffle(queue)
    for kind in queue:
        spec = feature_pack(rng, kind)
        if kind == "friday":
            fridays = [d for d in book.days if d.weekday() == 4 and add_sessions(d, spec["hold"]) <= END]
            spec["entry_day"] = rng.choice(fridays)
        placed = book.try_place(spec)
        if not placed and kind == "friday":
            spec.pop("entry_day", None)
            book.try_place(spec)

    # Two scale-in winners, counted as four round trips once expanded.
    for symbol, day in (("NVDA", date(2026, 3, 18)), ("AVGO", date(2026, 8, 12))):
        if day.weekday() >= 5:
            day = add_sessions(day, 0)
        e1 = at_clock(day, 9 * 60 + 38, rng)
        e2 = at_clock(day, 10 * 60 + 12, rng)
        exit_at = at_clock(day, 15 * 60 + 5, rng)
        if book.overlaps(symbol, e1, exit_at):
            continue
        book.reserve(symbol, e1, exit_at)
        book.plans.append(
            {
                "symbol": symbol,
                "entry": e1,
                "entry2": e2,
                "exit": exit_at,
                "hold": 0,
                "r": rng.uniform(1.1, 2.4),
                "weight": rng.uniform(1.1, 1.5),
                "spy": "up",
                "gap": round(rng.uniform(1.2, 3.4), 2),
                "rsi": round(rng.uniform(54, 66), 1),
                "legs": 2,
                "revenge_parent": False,
            }
        )

    # Revenge children 40 minutes after each parent exit.
    parents = [p for p in book.plans if p.get("revenge_parent")]
    for parent in parents:
        entry = parent["exit"] + timedelta(minutes=40)
        if entry.hour >= 16:
            continue
        exit_at = at_clock(entry.date(), min(15 * 60 + 45, entry.hour * 60 + entry.minute + 110), rng)
        if exit_at <= entry:
            exit_at = entry + timedelta(minutes=70)
        symbol = parent["symbol"]
        if book.overlaps(symbol, entry, exit_at):
            continue
        book.reserve(symbol, entry, exit_at)
        book.plans.append(
            {
                "symbol": symbol,
                "entry": entry,
                "exit": exit_at,
                "hold": 0,
                "r": -rng.uniform(0.5, 1.7),
                "weight": rng.uniform(0.9, 1.4),
                "spy": rng.choice(["range", "down"]),
                "gap": round(rng.uniform(0.2, 3.0), 2),
                "rsi": round(rng.uniform(52, 70), 1),
                "legs": 1,
                "revenge_parent": False,
            }
        )
    guard = 0
    while _planned_round_trips(book.plans) < 127 and guard < 50:
        guard += 1
        kind = "focus" if guard % 2 == 0 else "other"
        book.try_place(feature_pack(book.rng, kind), attempts=80)
    while _planned_round_trips(book.plans) > 127:
        for index in range(len(book.plans) - 1, -1, -1):
            plan = book.plans[index]
            if plan["legs"] == 1 and not plan.get("revenge_parent"):
                book.plans.pop(index)
                break
        else:
            break
    return book.plans


def _planned_round_trips(plans: list[dict]) -> int:
    return sum(plan.get("legs", 1) for plan in plans)


def indicator_pack(rng: random.Random, positive: bool, gap: float, rsi: float) -> dict:
    if positive:
        return {
            "adr": round(rng.uniform(3.1, 6.4), 2),
            "rsi": rsi,
            "ema_dist_pct": round(rng.uniform(0.4, 3.4), 2),
            "sma_dist_pct": round(rng.uniform(0.8, 5.5), 2),
            "dollar_volume": round(rng.uniform(90e6, 850e6), 0),
            "gap_pct": gap,
            "week52_pct": round(rng.uniform(-6.5, -0.4), 2),
        }
    return {
        "adr": round(rng.uniform(4.8, 11.5), 2),
        "rsi": rsi,
        "ema_dist_pct": round(rng.uniform(-4.2, 0.3), 2),
        "sma_dist_pct": round(rng.uniform(-7.5, 0.6), 2),
        "dollar_volume": round(rng.uniform(18e6, 210e6), 0),
        "gap_pct": gap,
        "week52_pct": round(rng.uniform(-42, -8), 2),
    }


def realize(plans: list[dict], rng: random.Random) -> tuple[list[dict], float]:
    score = sum(p["r"] * p["weight"] * p["legs"] for p in plans)
    # Two legs share one R in the score; the second leg is close. Good enough.
    scale = 210000 / score if score else 1
    fills = []
    for plan in plans:
        symbol = plan["symbol"]
        anchor = BASE[symbol] * rng.uniform(0.88, 1.14)
        risk_pct = rng.uniform(0.009, 0.02)
        buy1 = round(anchor, 2)
        stop1 = round(buy1 * (1 - risk_pct), 2)
        if stop1 >= buy1:
            stop1 = round(buy1 - 0.05, 2)
        per_share = buy1 - stop1
        dollar_risk = plan["weight"] * scale
        qty1 = max(1, int(round(dollar_risk / per_share)))
        sell = round(buy1 + plan["r"] * per_share, 2)
        if sell <= 0.5:
            sell = round(buy1 * 0.9, 2)
        legs = [(plan["entry"], buy1, stop1, qty1)]
        if plan["legs"] == 2:
            buy2 = round(buy1 * rng.uniform(1.002, 1.012), 2)
            stop2 = round(buy2 * (1 - risk_pct), 2)
            qty2 = max(1, int(round(qty1 * rng.uniform(0.45, 0.75))))
            if sell <= max(buy1, buy2):
                sell = round(max(buy1, buy2) + per_share * 1.2, 2)
            legs.append((plan["entry2"], buy2, stop2, qty2))
        pack = indicator_pack(rng, plan["r"] > 0, plan["gap"], plan["rsi"])
        pack["spy_regime"] = plan["spy"]
        for entry, buy, stop, qty in legs:
            fills.append(_fill("BUY", symbol, entry, qty, buy, stop, pack))
        total_qty = sum(qty for _, _, _, qty in legs)
        fills.append(_fill("SELL", symbol, plan["exit"], total_qty, sell, None, {}))
    return fills, scale


def _fill(side, symbol, when, qty, price, stop, pack) -> dict:
    commission = round(max(1.0, qty * 0.005), 2)
    row = {
        "datetime": when.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "price": f"{price:.2f}",
        "commission": f"{commission:.2f}",
        "stop_price": "" if stop is None else f"{stop:.2f}",
        "adr": "",
        "rsi": "",
        "ema_dist_pct": "",
        "sma_dist_pct": "",
        "dollar_volume": "",
        "gap_pct": "",
        "week52_pct": "",
        "spy_regime": "",
    }
    for key, value in pack.items():
        if key == "spy_regime":
            row[key] = value
        elif key == "dollar_volume":
            row[key] = f"{value:.0f}"
        elif value != "":
            row[key] = value
    return row


def balance_closed(fills: list[dict]) -> None:
    """Integer scaling can leave a sell a share short of its buys. Fix that."""
    from collections import defaultdict

    buys = defaultdict(list)
    sells = defaultdict(list)
    for row in fills:
        (buys if row["side"] == "BUY" else sells)[row["symbol"]].append(row)
    for sym, brows in buys.items():
        srows = sells.get(sym, [])
        if not srows:
            continue
        delta = sum(int(r["quantity"]) for r in brows) - sum(int(r["quantity"]) for r in srows)
        if delta == 0:
            continue
        qty = int(srows[-1]["quantity"]) + delta
        if qty < 1:
            continue
        srows[-1]["quantity"] = qty
        srows[-1]["commission"] = f"{max(1.0, round(qty * 0.005, 2)):.2f}"


def calibrate(fills: list[dict], plans_net_target=200000) -> None:
    """Nudge share counts so realized net lands near the target. Prices stay put."""
    root_fills = []
    # Use the audit engine itself so FIFO P&L is the number we scale.
    tmp_rows = fills
    # Convert via a tiny in-memory path: write not required; replicate match by
    # calling load after a temp is unnecessary — build Fill objects directly.
    parsed = []
    for row in tmp_rows:
        when, has_time = cp.parse_datetime(row["datetime"])
        parsed.append(
            cp.Fill(
                dt=when,
                symbol=row["symbol"],
                side=row["side"],
                qty=float(row["quantity"]),
                price=float(row["price"]),
                commission=float(row["commission"]),
                stop_price=cp.parse_number(row["stop_price"]),
                extras={},
                has_time=has_time,
            )
        )
    closed, _, _ = cp.match_fifo(parsed, [])
    net = sum(rt.pnl for rt in closed)
    if abs(net) < 1:
        return
    factor = plans_net_target / net
    for row in fills:
        qty = max(1, int(round(float(row["quantity"]) * factor)))
        row["quantity"] = qty
        row["commission"] = f"{max(1.0, round(qty * 0.005, 2)):.2f}"
    balance_closed(fills)
    # One more pass: add or remove shares on the largest winner so the residual is small.
    parsed = []
    for row in fills:
        when, has_time = cp.parse_datetime(row["datetime"])
        parsed.append(
            cp.Fill(
                dt=when,
                symbol=row["symbol"],
                side=row["side"],
                qty=float(row["quantity"]),
                price=float(row["price"]),
                commission=float(row["commission"] or 0),
                stop_price=cp.parse_number(row["stop_price"]),
                extras={},
                has_time=has_time,
            )
        )
    closed, _, _ = cp.match_fifo(parsed, [])
    net = sum(rt.pnl for rt in closed)
    gap = plans_net_target - net
    winner = max(closed, key=lambda rt: rt.pnl)
    per_share = winner.sell - winner.buy
    if abs(per_share) >= 0.01:
        delta_qty = int(round(gap / per_share))
        _adjust_symbol_qty(fills, winner, delta_qty)
        balance_closed(fills)


def _adjust_symbol_qty(fills: list[dict], winner, delta_qty: int) -> None:
    if delta_qty == 0:
        return
    entry_s = winner.entry.strftime("%Y-%m-%d %H:%M:%S")
    exit_s = winner.exit.strftime("%Y-%m-%d %H:%M:%S")
    for row in fills:
        same_open = row["side"] == "BUY" and row["symbol"] == winner.symbol and row["datetime"] == entry_s
        same_exit = row["side"] == "SELL" and row["symbol"] == winner.symbol and row["datetime"] == exit_s
        if same_open or same_exit:
            qty = max(1, int(row["quantity"]) + delta_qty)
            row["quantity"] = qty
            row["commission"] = f"{max(1.0, round(qty * 0.005, 2)):.2f}"


def boost_losers(fills: list[dict]) -> None:
    parsed = []
    for row in fills:
        when, has_time = cp.parse_datetime(row["datetime"])
        parsed.append(
            cp.Fill(
                dt=when,
                symbol=row["symbol"],
                side=row["side"],
                qty=float(row["quantity"]),
                price=float(row["price"]),
                commission=float(row["commission"] or 0),
                stop_price=cp.parse_number(row["stop_price"]),
                extras={},
                has_time=has_time,
            )
        )
    closed, _, _ = cp.match_fifo(parsed, [])
    losers = sorted((rt for rt in closed if rt.pnl < 0), key=lambda rt: rt.pnl)
    for rt in losers[:4]:
        _adjust_symbol_qty(fills, rt, int(rt.qty))  # double the losing lot


def open_positions(rng: random.Random, fills: list[dict]) -> tuple[list[dict], list[dict]]:
    last_exit: dict[str, datetime] = {}
    for row in fills:
        if row["side"] != "SELL":
            continue
        when, _ = cp.parse_datetime(row["datetime"])
        last_exit[row["symbol"]] = max(last_exit.get(row["symbol"], when), when)
    wanted = [
        ("CRWD", date(2026, 10, 8), 10 * 60 + 6, 1.012),
        ("AMD", date(2026, 10, 8), 10 * 60 + 22, 0.986),
        ("PLTR", date(2026, 10, 9), 9 * 60 + 48, 1.008),
        ("PANW", date(2026, 10, 9), 10 * 60 + 18, 0.991),
        ("DDOG", date(2026, 10, 9), 11 * 60 + 5, 1.004),
    ]
    json_rows = []
    extra_fills = []
    for symbol, day, minutes, mark_mult in wanted:
        entry = datetime(day.year, day.month, day.day, minutes // 60, minutes % 60, 12)
        if symbol in last_exit and entry <= last_exit[symbol]:
            entry = last_exit[symbol] + timedelta(minutes=25)
            if entry.date() > END or entry.hour >= 16:
                continue
        buy = round(BASE[symbol] * rng.uniform(0.96, 1.06), 2)
        stop = round(buy * 0.985, 2)
        qty = int(rng.choice([40, 60, 80, 100, 140]))
        last = round(buy * mark_mult, 2)
        pack = indicator_pack(rng, mark_mult >= 1, round(rng.uniform(0.8, 3.2), 2), round(rng.uniform(52, 64), 1))
        pack["spy_regime"] = "up" if mark_mult >= 1 else "range"
        extra_fills.append(_fill("BUY", symbol, entry, qty, buy, stop, pack))
        json_rows.append(
            {
                "symbol": symbol,
                "quantity": qty,
                "avg_price": buy,
                "stop_price": stop,
                "opened_at": entry.strftime("%Y-%m-%d %H:%M:%S"),
                "last_price": last,
            }
        )
    return extra_fills, json_rows


def main() -> None:
    rng = random.Random(2026)
    plans = build_plans(rng)
    fills, _scale = realize(plans, rng)
    boost_losers(fills)
    calibrate(fills, 200000)
    extra, positions = open_positions(rng, fills)
    fills.extend(extra)
    fills.sort(key=lambda row: (row["datetime"], 0 if row["side"] == "BUY" else 1, row["symbol"]))
    data = ROOT / "data"
    data.mkdir(parents=True, exist_ok=True)
    fields = list(fills[0].keys())
    with (data / "trades.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(fills)
    payload = {"as_of": "2026-10-11T12:10:04Z", "positions": positions}
    (data / "open_positions.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = [
        {
            "timestamp": "2026-10-10T21:05:11Z",
            "status": "ACTIVE",
            "cycle": "post-close",
            "ok": True,
            "detail": "positions marked, stops live",
        },
        {
            "timestamp": "2026-10-11T12:10:04Z",
            "status": "ACTIVE",
            "cycle": "weekend-safety",
            "ok": True,
            "detail": "limits clear, kill switch off, no new orders",
        },
    ]
    with (data / "safety-check-log.jsonl").open("w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")
    print(f"plans={len(plans)} fills={len(fills)} open={len(positions)}")


if __name__ == "__main__":
    main()
