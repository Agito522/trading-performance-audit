# 2026 Trading Performance Audit

A one-page night-city HUD of a stock book. It reads fills, matches them FIFO, and writes a static page. The committed book is Harold’s StonkJournal (a real book, not a sample): 5 closed round trips, about +$3,865 realized, and 24 open stock lots. Open short options are in `data/open_options_note.json` and are not fills. It is not a brokerage connection, not a recommendation, and this repo never places orders or logs into IBKR.

Page: [https://agito522.github.io/trading-performance-audit/](https://agito522.github.io/trading-performance-audit/)

## Regenerate

```bash
python compute_perf.py
```

The script prints `READY` and writes `docs/index.html`. A CSV that is empty, or that contains only a header, still produces the page. Install the quote dependency once with `pip install -r requirements.txt`.

On that run the script asks Yahoo Finance for the latest daily **close** of each symbol in `data/open_positions.json` and writes it into the open-book **Last** column. **Unrealized** is `(Last − entry) × shares`. **Risk $** is `(entry − stop) × shares` when the stop is below the entry. **No. of R** is `(Last − entry) / (entry − stop)` when the stop is below the entry, and blank when the stop is missing or not below the entry. All three belong to the open book only. Net P&L, today’s P&L, and every period-filter stat stay realized closes. The browser does not call Yahoo; GitHub Pages serves the static snapshot from the last `python compute_perf.py` run. If Yahoo or `yfinance` is unavailable, Last stays blank and the page still builds.

Best 5 and Worst 5 print each ticker in the same bright yellow as the open book. Next to the bar, the label is realized dollars and that bar’s length as a percent of the longest bar in the same list, for example `+$2,007 / +100.0%`. Holding-period and entry charts stay dollar-only.

Weekly P&L is a bar chart of realized closes, summed through each Friday. It is not NAV. The NAV chart is a line of USD net asset value. Its horizontal axis is one calendar year, January 1 through December 31, so a point stays on its date. The yellow label is the gap to a **$1,000,000 USD** goal as a percent of the goal: `(1,000,000 − NAV) / 1,000,000`. A dashed line marks that goal. NAV comes from `data/nav_history.csv`. USD amounts are used as USD. HKD amounts convert at that row’s `fx_to_usd`. The committed series is a placeholder until a real history replaces it. The period control uses NAV dates inside the window and does not change the year axis. The percent is not added to Net P&L.

The page opens on **Full CSV range**. The control under the title — This week, MTD, Last 30 days, Full CSV range, and Custom from–to — recomputes the audit in the browser from closed FIFO round trips whose **exit** falls in that window. Net P&L stays realized; open marks are not added. This week and MTD run through today in America/New_York. Last 30 days is the 30 calendar days ending today.

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
- Weekly realized P&L bars
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

The bot strip is the safety log, not the fills. Today’s P&L is realized only. Open-book Unrealized, from the Yahoo Last versus entry, is not added to it.

## Rebuild the sample

```bash
python scripts/make_sample.py
python compute_perf.py
```

`scripts/make_sample.py` overwrites `data/trades.csv`, `data/open_positions.json`, and the safety log. The committed book is Harold’s StonkJournal. Do not run the sample script against it.

## GitHub Pages

The workflow `.github/workflows/pages.yml` tests, rebuilds `docs/`, and deploys on every push to `main`.

If the site is not live yet, turn on Pages once:

**Settings → Pages → Build and deployment → Source: GitHub Actions**

The project URL is `https://agito522.github.io/trading-performance-audit/`.
