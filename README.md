# 2026 Trading Performance Audit

A one-page, cream-paper audit of a stock book. It reads fills, matches them FIFO, and writes a static page. The committed book is **SAMPLE** data (about +$200k over 127 round trips). It is not a live account, not a recommendation, and this repo never places orders or logs into IBKR.

Page: [https://agito522.github.io/trading-performance-audit/](https://agito522.github.io/trading-performance-audit/)

## Regenerate

```bash
python compute_perf.py
```

The script prints `READY` and writes `docs/index.html`. A CSV that is empty, or that contains only a header, still produces the page.

Telegram is a stub. If `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` is unset, nothing is sent.

## Drop an IBKR CSV

1. In IBKR, export trades to CSV. A Flex query or an Activity Statement trade blotter both work. Use the trades section, not positions-only or cash reports.
2. Replace `data/trades.csv` with that file. You can leave the sample header and zero rows if you only want the empty state.
3. Recognized columns (any header casing; extra IBKR columns are ignored):

| You might see | Used as |
| --- | --- |
| `Symbol` | symbol |
| `Date/Time`, or `Date` + `Time` | fill time. A date with no clock still counts; entry-time sections then say Unknown |
| `Buy/Sell` (`BUY`/`SELL` or `BOT`/`SLD`) | side. If this column is missing, a negative quantity is a sell |
| `Quantity` | shares |
| `T. Price` or `Price` | fill price. If price is blank and `Proceeds` is present, price is proceeds / quantity |
| `Comm/Fee` | commission, taken as a cost either sign |
| `stop_price` | initial stop for R |

4. Optionally replace `data/open_positions.json` with the current book (see the sample file). If you omit it, open lots are whatever buys FIFO has not closed.
5. Optionally append a line to `data/safety-check-log.jsonl` (or put an array in `data/safety-check-log.json`). The **last** record drives the bot strip.
6. Run `python compute_perf.py` and open `docs/index.html`.

Long-only FIFO: a sell with no older buy is ignored. Scale-ins close oldest shares first. Several sells that finish one buy are one round trip, at the average sell price.

### R

`R = (sell − buy) / (buy − initial stop)`

Stop price, in order:

1. `open_positions.json`, when `opened_at` is within two minutes of that buy
2. `stop_price` on the buy row
3. else `buy * 0.01`

That last fallback is the price `0.01 × buy`, not a stop 1% under the entry. Without a real stop, R collapses toward the raw return and the histogram is not useful. Put `stop_price` on the buy, or a matching open-position row.

R buckets are `(−∞, −2]`, `(−2, −1]`, `(−1, 0]`, `(0, 1]`, `(1, 2]`, `(2, 3]`, `(3, ∞)`.

## What fills can answer, and what they cannot

Fills alone (symbol, time, side, quantity, price, commission) are enough for:

- Net P&L, round trips, win rate, profit factor, expectancy, max drawdown
- Weekly equity curve
- P&L by holding period (same day, overnight, 2–4 sessions, 5–9, 10+)
- Entry time and day of week
- Best 5 / worst 5 symbols
- Concentration and discipline: symbol share, busiest day, trades per week, loss streak, commissions, median dollar risk
- Today’s realized P&L (round trips whose exit date is today in America/New_York)
- Last 20 closed trades
- Leaks that are only about the tape: late-day entries (2:30pm or later), Friday afternoon, holds of 10+ sessions, revenge re-entries (a new buy within 60 minutes of a losing exit), oversized risk versus the median

These stay **N/A** or skip the rule until you add columns:

| Column | Unlocks |
| --- | --- |
| `spy_regime` (`up`, `down`, or `range`) | SPY-day regime bars, and the “up day” focus rule |
| `gap_pct` | Gap row in the edge table, gap-chase leak, focus gap rule |
| `rsi` | RSI row, extended-RSI leak, focus RSI rule |
| `adr` | ADR row |
| `ema_dist_pct` | EMA distance row (percent above/below the EMA) |
| `sma_dist_pct` | SMA distance row |
| `dollar_volume` | $ volume row |
| `week52_pct` | distance from the 52-week high |

Edge Profile medians are computed from the **buy**. If a column is not in the header, that row is N/A. Focus still uses the clock and the hold when those enrichment fields are absent.

`stop_price` (or the open-position stop) is what makes R, the histogram, “stop on the fill,” and “losers held to the stop” meaningful.

The bot strip is the safety log, not the fills. Today’s P&L is realized only; marks on open positions are not added to it.

## Rebuild the sample

```bash
python scripts/make_sample.py
python compute_perf.py
```

`scripts/make_sample.py` overwrites the three files in `data/`. Do not run it against a CSV you care about.

## GitHub Pages

The workflow `.github/workflows/pages.yml` tests, rebuilds `docs/`, and deploys on every push to `main`.

If the site is not live yet, turn on Pages once:

**Settings → Pages → Build and deployment → Source: GitHub Actions**

The project URL is `https://agito522.github.io/trading-performance-audit/`.
