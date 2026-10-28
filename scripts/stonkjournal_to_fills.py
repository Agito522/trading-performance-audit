#!/usr/bin/env python3
"""Convert a StonkJournal CSV into the audit book's fills and open lots.

Same encoding as the 2026-10-11 and 2026-10-21 loads (see data/conversion_notes.md):

- Long stocks: BUY journal Qty at Open Date / Entry. SELL (Qty − Position) at Date / Exit.
- An OPEN stock with Position > 0 is an open lot (Entry as avg, Stop when Stop ≠ 0).
- Closed shorts (stock or option): BUY at the exit print and SELL at the entry print,
  so long-only FIFO keeps the journal's dollar direction. Option prices are premium × 100,
  quantity is contracts, symbol is SYMBOL-OPT.
- Open options (short or long) are not fills. They are listed in open_options_note.json.
- Commission is 0. The export has no fee column.
- One journal row is one lot. A scale-in is the new average at the original Open Date.
  Cumulative exits replace earlier partials: one blended exit at Date.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path


HEADER = ["Symbol", "Date/Time", "Buy/Sell", "Quantity", "T. Price", "Comm/Fee", "stop_price"]


def parse_float(text: str) -> float | None:
    raw = (text or "").strip().replace(",", "")
    if raw == "":
        return None
    return float(raw)


def fmt_num(value: float) -> str:
    """Match the committed trades.csv: 6 decimal places, integers as N.0."""
    rounded = round(float(value), 6)
    if abs(rounded) < 5e-7:
        rounded = 0.0
    if abs(rounded - round(rounded)) < 1e-8:
        return f"{rounded:.1f}"
    return f"{rounded:.6f}".rstrip("0").rstrip(".")


def fmt_when(iso: str) -> tuple[datetime, str]:
    stamp = iso.strip()
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).replace(tzinfo=None)
    return when, when.strftime("%Y-%m-%d, %H:%M:%S")


def load_journal(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    return list(csv.DictReader(text.splitlines()))


def convert(rows: list[dict]) -> tuple[list[list[str]], list[dict], list[dict], list[str]]:
    fills: list[tuple] = []
    positions: list[dict] = []
    options: list[dict] = []
    skipped: list[str] = []

    for index, row in enumerate(rows):
        symbol = (row.get("Symbol") or "").strip().upper()
        status = (row.get("Status") or "").strip().upper()
        market = (row.get("Market") or "").strip().upper()
        side = (row.get("Side") or "").strip().upper()
        qty = parse_float(row.get("Qty") or "")
        entry = parse_float(row.get("Entry") or "")
        exit_px = parse_float(row.get("Exit") or "")
        position = parse_float(row.get("Position") or "")
        stop = parse_float(row.get("Stop") or "")
        open_iso = (row.get("Open Date") or "").strip()
        close_iso = (row.get("Date") or "").strip()
        if not symbol or qty is None or entry is None or position is None or not open_iso:
            raise SystemExit(f"Journal row {index + 2} is missing symbol, qty, entry, position, or open date")
        exit_px = 0.0 if exit_px is None else exit_px
        still_open = status == "OPEN" and position > 1e-9
        closed_qty = max(qty - position, 0.0) if still_open else qty
        multiplier = 100.0 if market == "OPTION" else 1.0
        trade_symbol = f"{symbol}-OPT" if market == "OPTION" else symbol
        stop_out = None if stop is None or abs(stop) < 1e-12 else stop

        if market == "OPTION" and still_open and closed_qty <= 1e-9:
            note = {
                "symbol": trade_symbol,
                "qty": float(position),
                "avg_price": float(entry) * multiplier,
                "opened_at": open_iso,
                "side": side.lower(),
                "asset": "option",
            }
            options.append(note)
            skipped.append(f"{trade_symbol}: open {side.lower()} option omitted from fills (see open_options_note.json)")
            continue

        if market == "OPTION" and still_open and closed_qty > 1e-9:
            raise SystemExit(
                f"{trade_symbol} is an open option with a partial close. "
                "The 2026-10-11 encoding only covers fully open options and fully closed options."
            )

        if side == "SHORT" and still_open:
            raise SystemExit(f"{symbol} is an open short {market}. That shape is not in the encoding.")

        open_at, open_text = fmt_when(open_iso)
        close_at, close_text = fmt_when(close_iso or open_iso)

        if side == "LONG":
            buy_px = entry * multiplier
            sell_px = exit_px * multiplier
        elif side == "SHORT":
            # Closed short: buy the exit print, sell the entry print.
            buy_px = exit_px * multiplier
            sell_px = entry * multiplier
        else:
            raise SystemExit(f"{symbol} has side {side!r}")

        buy = [trade_symbol, open_text, "BUY", fmt_num(qty if side == "LONG" else closed_qty), fmt_num(buy_px), "0", fmt_num(stop_out) if stop_out is not None else ""]
        fills.append((open_at, 0, index, buy))
        if closed_qty > 1e-9:
            sell_qty = closed_qty
            sell = [trade_symbol, close_text, "SELL", fmt_num(sell_qty), fmt_num(sell_px), "0", ""]
            fills.append((close_at, 1, index, sell))

        if market == "STOCK" and side == "LONG" and position > 1e-9:
            lot = {
                "symbol": symbol,
                "qty": float(position),
                "avg_price": float(entry),
                "opened_at": open_iso,
            }
            if stop_out is not None:
                lot["stop_price"] = float(stop_out)
            positions.append(lot)

    fills.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in fills], positions, options, skipped


def write_outputs(out_dir: Path, fills: list[list[str]], positions: list[dict], options: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    trades = out_dir / "trades.csv"
    # The committed blotter is CRLF, matching the 2026-10-11 and 2026-10-21 loads.
    with trades.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\r\n")
        writer.writerow(HEADER)
        writer.writerows(fills)
    (out_dir / "open_positions.json").write_text(json.dumps(positions, indent=2) + "\n", encoding="utf-8")
    (out_dir / "open_options_note.json").write_text(json.dumps(options, indent=2) + "\n", encoding="utf-8")


def price_pnl(row: dict) -> float | None:
    status = (row.get("Status") or "").strip().upper()
    market = (row.get("Market") or "").strip().upper()
    side = (row.get("Side") or "").strip().upper()
    qty = parse_float(row.get("Qty") or "")
    entry = parse_float(row.get("Entry") or "")
    exit_px = parse_float(row.get("Exit") or "")
    position = parse_float(row.get("Position") or "")
    if qty is None or entry is None or position is None:
        return None
    exit_px = 0.0 if exit_px is None else exit_px
    still_open = status == "OPEN" and position > 1e-9
    closed_qty = max(qty - position, 0.0) if still_open else qty
    if closed_qty <= 1e-9:
        return 0.0
    multiplier = 100.0 if market == "OPTION" else 1.0
    if side == "LONG":
        return (exit_px - entry) * closed_qty * multiplier
    if side == "SHORT":
        return (entry - exit_px) * closed_qty * multiplier
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", type=Path, help="StonkJournal CSV")
    parser.add_argument("--out-dir", type=Path, required=True, help="Directory for trades.csv, open_positions.json, open_options_note.json")
    args = parser.parse_args()
    rows = load_journal(args.journal)
    fills, positions, options, skipped = convert(rows)
    write_outputs(args.out_dir, fills, positions, options)
    returns = [parse_float(row.get("Return ($)") or "") or 0.0 for row in rows]
    open_rows = [row for row in rows if (row.get("Status") or "").strip().upper() == "OPEN"]
    closed_rows = [row for row in rows if (row.get("Status") or "").strip().upper() in {"WIN", "LOSS"}]
    print(f"Journal rows: {len(rows)}")
    print(f"OPEN rows: {len(open_rows)}")
    print(f"WIN/LOSS rows: {len(closed_rows)}")
    print(f"Fill rows written: {len(fills)}")
    print(f"Stock open lots: {len(positions)}")
    print(f"Open options omitted from fills: {len(options)}")
    print(f"Journal Return ($) sum: {sum(returns):.2f}")
    gaps = []
    for row in rows:
        journal = parse_float(row.get("Return ($)") or "") or 0.0
        modeled = price_pnl(row)
        if modeled is None:
            continue
        if abs(journal - modeled) >= 0.5:
            gaps.append((abs(journal - modeled), row.get("Symbol"), row.get("Status"), journal, modeled))
    gaps.sort(reverse=True)
    print("Return gaps (|journal − price model| ≥ 0.50):")
    for _gap, symbol, status, journal, modeled in gaps:
        print(f"  {symbol} {status}: journal {journal:.2f} vs price {modeled:.2f}")
    print("Skipped:")
    for line in skipped:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
