# Conversion notes (2026-10-28)

Source: Harold StonkJournal CSV attach → IBKR-style fills for Agito522/trading-performance-audit.

- Journal rows: 59
- Fill rows written: 83
- Stock open lots: 28
- Open options omitted from fills: 11 (10 short, 1 long)
- Journal Return ($) sum (all rows, including open partials): 458.71
- FIFO Net P&L (fully closed lots only): 2755.35 across 20 round trips

`scripts/stonkjournal_to_fills.py` is this encoding. Run against the 2026-10-21 source, it rewrites that book's `trades.csv`, `open_positions.json`, and `open_options_note.json` byte for byte. `data/nav_history.csv` is not part of the journal and was left as the 2026-10-21 Portfolio_NAV_Log series.

Encoding (same path as the 2026-10-21 book):
- Long stocks: native BUY at Open Date / Entry for journal Qty. SELL the closed shares (Qty − Position) at Date / Exit.
- OPEN stock with Position > 0: that Position is the open lot (Entry as avg, Stop when Stop ≠ 0, Opened from Open Date). Partial scale-out P&L stays out of Net P&L until the buy lot is fully closed.
- Closed short stocks + closed short options: BUY at the exit print / SELL at the entry print so long-only FIFO matches the journal dollar direction. Option prices are premium × 100, quantity is contracts, symbol is SYMBOL-OPT.
- Commission 0. The export has no fee column.
- Open options, short or long, are not fills (listed in open_options_note.json). An open long option is not a stock lot, so it is not quoted on the open book.

New open stock lots in this export:
- META 2 @ 624.81, opened 2026-10-27
- SPGI 3 @ 449.10, opened 2026-10-27
- GDE 20 @ 54.00, opened 2026-10-27
- EWZ 400 @ 31.70, stop 30, opened 2026-10-26
- TSLA 40 @ 427.58, opened 2026-05-12, 5 shares already scaled out (Position 35). That partial stays out of Net P&L.
- PURR 500 @ 12.04 and GDX 150 @ 98.40 are in this export again. They were open in the 2026-10-10 journal and absent from the 2026-10-21 file, so the prior load left them out. They are open stock lots here.

Newly closed, so they are round trips now:
- SPCX LOSS, all 160 @ 156.90 on 2026-10-21 (FIFO −$1,120.00). The 2026-10-15 partial of 60 @ 147.70 is gone; one row is one blended exit.
- MRVL-OPT WIN, closed 2026-10-23 (FIFO +$539.00). Was an open short option.
- MAGS-OPT LOSS, closed 2026-10-23 (FIFO −$40.00). Was an open short option.

No 2026-10-21 lot disappeared without a row. The other 17 round trips are unchanged, so Net P&L moved by −$621.00 (SPCX −$1,120, MAGS-OPT −$40, MRVL-OPT +$539).

Journal quirks (Return ($) vs (Exit − Entry) × closed shares, commission 0). Partials do not enter Net P&L:
- BE OPEN: journal 827.00 vs price 1875.89
- MU OPEN: journal 1317.38 vs price 416.62
- SOXL OPEN: journal −1901.17 vs price −1338.72
- DELL OPEN: journal −370.96 vs price −10.77
- HOOD OPEN: journal −1109.35 vs price −842.00
- CRCL OPEN: journal −536.16 vs price −313.09
- CRWD OPEN: journal −176.28 vs price −37.75
- TSLA OPEN: journal −166.59 vs price −107.95
- PLTR OPEN: journal 1742.62 vs price 1800.00
- BMNR OPEN: journal −119.73 vs price −137.75
- AMD OPEN: journal −191.49 vs price −178.38
- Smaller gaps, under a few dollars, on closed rows and on SMTC, VICR, TEM, SNDK

Scale-ins are one average lot at the original Open Date. The journal does not list the add prints:
- BE 95 → 115 @ 248.61695652173913 (Open Date stays 2026-09-05, Position stays 20). The 2026-10-09 partial is replaced by one blended exit of 95 shares on 2026-10-27.
- Unchanged from the prior book: MRVL 50 @ 266.62, DELL 30 @ 503.838333 (Stop 534.33 is still above the average, so open Risk $ stays blank), TQQQ 180 @ 77.421111.

Cumulative exits replace earlier partial timestamps. One row is one blended exit at Date:
- SPCX, SOXL, BE, and MU, as above
- New partials still open, so still outside Net P&L: DELL 20 shares, SOXL down to Position 80, AMD 5, MU 2 more (Position 18), VICR 5, TEM 40, SMTC 10, CRWD 10, TSLA 5

Skipped (open options):
- AAPU-OPT: open short option omitted from fills (see open_options_note.json)
- DVN-OPT: open long option omitted from fills (see open_options_note.json)
- TQQQ-OPT: open short option omitted from fills (see open_options_note.json)
- DRAM-OPT: open short option omitted from fills (see open_options_note.json)
- DRAM-OPT: open short option omitted from fills (see open_options_note.json)
- DRAM-OPT: open short option omitted from fills (see open_options_note.json)
- META-OPT: open short option omitted from fills (see open_options_note.json)
- NVDA-OPT: open short option omitted from fills (see open_options_note.json)
- NVDL-OPT: open short option omitted from fills (see open_options_note.json)
- DRAM-OPT: open short option omitted from fills (see open_options_note.json)
- P-OPT: open short option omitted from fills (see open_options_note.json)
