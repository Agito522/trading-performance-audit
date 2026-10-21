# Conversion notes (2026-10-21)

Source: Harold StonkJournal CSV attach → IBKR-style fills for Agito522/trading-performance-audit.

- Journal rows: 50
- Fill rows written: 65
- Stock open lots: 22
- Open short options omitted from fills: 11
- Journal Return ($) sum (all rows, including open partials): 5840.86
- FIFO Net P&L (fully closed lots only): 3376.35 across 17 round trips

Encoding (same path as the 2026-10-11 book):
- Long stocks: native BUY at Open Date / Entry for journal Qty. SELL the closed shares (Qty − Position) at Date / Exit.
- OPEN stock with Position > 0: that Position is the open lot (Entry as avg, Stop when Stop ≠ 0, Opened from Open Date). Partial scale-out P&L stays out of Net P&L until the buy lot is fully closed.
- Closed short stocks + closed short options: BUY at the exit print / SELL at the entry print so long-only FIFO matches the journal dollar direction. Option prices are premium × 100, quantity is contracts, symbol is SYMBOL-OPT.
- Commission 0. The export has no fee column.
- Open short options are not fills (listed in open_options_note.json).

Not in this export (left out of the book; not invented):
- PURR and GDX were open stock lots in the 2026-10-10 journal and have no row here, including no close.
- No HSBC names.

Journal quirks (Return ($) vs (Exit − Entry) × closed shares, commission 0). Partials do not enter Net P&L:
- BMNR OPEN: journal −119.73 vs price −137.75
- HOOD OPEN: journal −1109.35 vs price −842.00
- SPCX OPEN: journal −1438.48 vs price −972.00
- PLTR OPEN: journal 1742.62 vs price 1800.00
- BE OPEN: journal 2134.02 vs price 1729.43
- SOXL OPEN: journal 570.17 vs price 916.66
- MU OPEN: journal 1597.76 vs price 634.30
- CRCL OPEN: journal −536.16 vs price −313.09
- Smaller gaps on closed or nearly-flat rows: MSTR, MRNA, STX, META, LITE, SNDK (under a few dollars)

Scale-ins are one average lot at the original Open Date. The journal does not list the add prints:
- MRVL 40 → 50 @ 266.62
- DELL 10 → 30 @ 503.838333 (Stop 534.33 is above the new average, so open Risk $ stays blank)
- TQQQ 150 → 180 @ 77.421111
- SPCX 120 → 160 @ 163.9

Cumulative exits replace earlier partial timestamps (BMNR, MSTR, MRNA, PLTR, SPCX, HOOD, STX). One row is one blended exit at Date.

Skipped (open short options):
- TQQQ-OPT: open short option omitted from fills (see open_options_note.json)
- DRAM-OPT: open short option omitted from fills (see open_options_note.json)
- DRAM-OPT: open short option omitted from fills (see open_options_note.json)
- DRAM-OPT: open short option omitted from fills (see open_options_note.json)
- META-OPT: open short option omitted from fills (see open_options_note.json)
- NVDA-OPT: open short option omitted from fills (see open_options_note.json)
- NVDL-OPT: open short option omitted from fills (see open_options_note.json)
- DRAM-OPT: open short option omitted from fills (see open_options_note.json)
- P-OPT: open short option omitted from fills (see open_options_note.json)
- MAGS-OPT: open short option omitted from fills (see open_options_note.json)
- MRVL-OPT: open short option omitted from fills (see open_options_note.json)
