# Conversion notes (2026-10-11)

Source: Harold StonkJournal CSV attach → IBKR-style fills for Agito522/trading-performance-audit.

- Journal rows: 41
- Fill rows written: 45
- Stock open lots: 24
- Open short options omitted from fills: 12
- Journal Return ($) sum (all rows): 7581.45

Encoding:
- Long stocks: native BUY at Open Date / SELL partials at Date.
- Closed short stocks + closed/expired short options: BUY@exit / SELL@entry so dollar P&L matches journal (long-only FIFO).
- Option prices = premium×100 with Quantity=contracts.
- stop_price copied when StonkJournal Stop ≠ 0.
- Open short options not in trades.csv (listed in open_options_note.json).

Skipped:
- SSO-OPT: open short option omitted from fills (see open_options_note.json)
- DRAM-OPT: open short option omitted from fills (see open_options_note.json)
- DRAM-OPT: open short option omitted from fills (see open_options_note.json)
- META-OPT: open short option omitted from fills (see open_options_note.json)
- NVDA-OPT: open short option omitted from fills (see open_options_note.json)
- NVDL-OPT: open short option omitted from fills (see open_options_note.json)
- DRAM-OPT: open short option omitted from fills (see open_options_note.json)
- UDOW-OPT: open short option omitted from fills (see open_options_note.json)
- P-OPT: open short option omitted from fills (see open_options_note.json)
- MAGS-OPT: open short option omitted from fills (see open_options_note.json)
- TQQQ-OPT: open short option omitted from fills (see open_options_note.json)
- MRVL-OPT: open short option omitted from fills (see open_options_note.json)
