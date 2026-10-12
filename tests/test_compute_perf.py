#!/usr/bin/env python3
"""FIFO, R buckets, empty files, and the StonkJournal book."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import compute_perf as cp


def fill(when, symbol, side, qty, price, stop=None, **extras):
    when_dt = when if isinstance(when, datetime) else datetime.fromisoformat(when)
    base = {key: None for key in cp.ENRICHMENT}
    base.update(extras)
    return cp.Fill(
        dt=when_dt,
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        commission=0.0,
        stop_price=stop,
        extras=base,
        has_time=True,
    )


class BucketTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(cp.r_bucket(-2.01), "le_neg2")
        self.assertEqual(cp.r_bucket(-2), "le_neg2")
        self.assertEqual(cp.r_bucket(-1.5), "neg2_neg1")
        self.assertEqual(cp.r_bucket(-1), "neg2_neg1")
        self.assertEqual(cp.r_bucket(-0.01), "neg1_0")
        self.assertEqual(cp.r_bucket(0), "neg1_0")
        self.assertEqual(cp.r_bucket(0.01), "0_1")
        self.assertEqual(cp.r_bucket(1), "0_1")
        self.assertEqual(cp.r_bucket(1.01), "1_2")
        self.assertEqual(cp.r_bucket(2), "1_2")
        self.assertEqual(cp.r_bucket(2.5), "2_3")
        self.assertEqual(cp.r_bucket(3), "2_3")
        self.assertEqual(cp.r_bucket(3.01), "gt3")

    def test_hold_sessions(self):
        self.assertEqual(cp.session_gap(date(2026, 10, 5), date(2026, 10, 5)), 0)
        self.assertEqual(cp.hold_bucket(0), "same_day")
        self.assertEqual(cp.session_gap(date(2026, 10, 9), date(2026, 10, 12)), 1)
        self.assertEqual(cp.hold_bucket(1), "overnight")
        self.assertEqual(cp.hold_bucket(4), "2_4d")
        self.assertEqual(cp.hold_bucket(9), "5_9d")
        self.assertEqual(cp.hold_bucket(10), "10d_plus")


class FifoTests(unittest.TestCase):
    def test_partial_sells_are_one_round_trip(self):
        rows = [
            fill("2026-02-02 10:00:00", "AAPL", "BUY", 10, 100, stop=99),
            fill("2026-02-02 12:00:00", "AAPL", "SELL", 4, 110),
            fill("2026-02-02 15:00:00", "AAPL", "SELL", 6, 120),
        ]
        closed, open_lots, unmatched = cp.match_fifo(rows, [])
        self.assertEqual(len(closed), 1)
        self.assertEqual(open_lots, [])
        self.assertEqual(unmatched, 0)
        self.assertAlmostEqual(closed[0].sell, 116)
        self.assertAlmostEqual(closed[0].pnl, 160)
        self.assertAlmostEqual(closed[0].r, 16)

    def test_scale_in_is_two_round_trips(self):
        rows = [
            fill("2026-02-03 09:40:00", "NVDA", "BUY", 50, 100, stop=99),
            fill("2026-02-03 10:05:00", "NVDA", "BUY", 50, 102, stop=101),
            fill("2026-02-03 15:00:00", "NVDA", "SELL", 100, 110),
        ]
        closed, open_lots, _ = cp.match_fifo(rows, [])
        self.assertEqual(len(closed), 2)
        self.assertEqual(open_lots, [])
        self.assertAlmostEqual(closed[0].pnl, 500)
        self.assertAlmostEqual(closed[1].pnl, 400)

    def test_stop_column_then_fallback(self):
        rows = [
            fill("2026-02-04 10:00:00", "MSFT", "BUY", 10, 100),
            fill("2026-02-04 15:00:00", "MSFT", "SELL", 10, 102),
        ]
        closed, _, _ = cp.match_fifo(rows, [])
        self.assertEqual(closed[0].stop_source, "fallback")
        self.assertAlmostEqual(closed[0].stop, 1.0)
        self.assertAlmostEqual(closed[0].r, 2 / 99)

    def test_open_positions_stop_beats_column(self):
        rows = [
            fill("2026-02-05 10:00:00", "AMD", "BUY", 10, 100, stop=98),
            fill("2026-02-05 15:00:00", "AMD", "SELL", 10, 101),
        ]
        positions = [
            {
                "symbol": "AMD",
                "qty": 10,
                "avg_price": 100,
                "stop": 99,
                "opened_at": datetime(2026, 2, 5, 10, 0, 0),
                "last": None,
            }
        ]
        closed, _, _ = cp.match_fifo(rows, positions)
        self.assertEqual(closed[0].stop_source, "open_positions")
        self.assertAlmostEqual(closed[0].r, 1.0)

    def test_revenge_is_avoid(self):
        rows = [
            fill("2026-03-02 10:50:00", "SMCI", "BUY", 10, 40, stop=39, spy_regime="range", gap_pct=1, rsi=55),
            fill("2026-03-02 12:00:00", "SMCI", "SELL", 10, 39),
            fill("2026-03-02 12:30:00", "SMCI", "BUY", 10, 39.5, stop=38.5, spy_regime="range", gap_pct=1, rsi=60),
            fill("2026-03-02 15:00:00", "SMCI", "SELL", 10, 39),
        ]
        audit = cp.assemble(rows, set(cp.ENRICHMENT), [], None, today=date(2026, 10, 11))
        self.assertTrue(audit["closed"][1].revenge)
        self.assertEqual(audit["closed"][1].setup, "avoid")


class FileTests(unittest.TestCase):
    def test_header_only_and_ibkr_signed_qty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "data" / "trades.csv").write_text(
                "datetime,symbol,side,quantity,price,commission,stop_price\n",
                encoding="utf-8",
            )
            summary = cp.build_page(root, today=date(2026, 10, 11))
            self.assertEqual(summary["round_trips"], 0)
            html = (root / "docs" / "index.html").read_text(encoding="utf-8")
            self.assertIn("STONKJOURNAL", html)
            self.assertNotIn("SAMPLE", html)
            self.assertIn("N/A", html)
            self.assertIn("2026 Trading Performance Audit", html)

            (root / "data" / "trades.csv").write_text(
                "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,Comm/Fee\n"
                'Trades,Data,Order,Stocks,USD,AAPL,"2026-01-05, 09:35:12",100,100.00,-1.00\n'
                'Trades,Data,Order,Stocks,USD,AAPL,"2026-01-05, 15:10:00",-100,103.00,-1.00\n',
                encoding="utf-8",
            )
            summary = cp.build_page(root, today=date(2026, 10, 11))
            self.assertEqual(summary["round_trips"], 1)
            self.assertAlmostEqual(summary["net_pnl"], 298.0)

    def test_safety_last_line_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "data" / "trades.csv").write_text("datetime,symbol,side,quantity,price\n", encoding="utf-8")
            (root / "data" / "safety-check-log.jsonl").write_text(
                json.dumps({"timestamp": "2026-01-01T00:00:00Z", "status": "IDLE", "cycle": "old"})
                + "\n"
                + json.dumps(
                    {
                        "timestamp": "2026-10-11T12:10:04Z",
                        "status": "ACTIVE",
                        "cycle": "weekend-safety",
                        "detail": "limits clear",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            record = cp.load_safety(root)
            self.assertEqual(record["status"], "ACTIVE")
            self.assertEqual(record["cycle"], "weekend-safety")
            self.assertEqual(record["timestamp"], datetime(2026, 10, 11, 12, 10, 4))

    def test_prints_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "data" / "trades.csv").write_text("symbol,datetime,side,quantity,price\n", encoding="utf-8")
            env = os.environ.copy()
            env["AUDIT_ROOT"] = str(root)
            env.pop("TELEGRAM_BOT_TOKEN", None)
            env.pop("TELEGRAM_CHAT_ID", None)
            proc = subprocess.run(
                [sys.executable, str(ROOT / "compute_perf.py")],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("READY", proc.stdout.splitlines()[-1])


class CastTests(unittest.TestCase):
    def test_numpy_to_python(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        self.assertEqual(cp.to_py(np.float64(1.25)), 1.25)
        self.assertIs(type(cp.to_py(np.float64(1.25))), float)
        self.assertEqual(cp.to_py(np.int64(4)), 4)
        self.assertIs(type(cp.to_py(np.int64(4))), int)
        self.assertEqual(cp.to_py({"r": np.array([1, 2])}), {"r": [1, 2]})
        self.assertIsInstance(cp.pysum([np.float64(1.5), np.float64(2.5)]), float)


class SampleTests(unittest.TestCase):
    def test_stonkjournal_book_shape(self):
        summary = cp.build_page(ROOT, today=date(2026, 10, 11), fetch_quotes=False)
        self.assertEqual(summary["round_trips"], 5)
        self.assertAlmostEqual(summary["net_pnl"], 3865.05, places=2)
        self.assertAlmostEqual(summary["nav_usd"], 340000.0)
        self.assertNotAlmostEqual(summary["nav_usd"], summary["net_pnl"], places=0)
        self.assertEqual(summary["open_positions"], 24)
        self.assertEqual(summary["today_pnl"], 0)
        self.assertAlmostEqual(summary["win_rate"], 100.0)
        self.assertIs(type(summary["net_pnl"]), float)
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("SAMPLE", html)
        for phrase in (
            "STONKJOURNAL",
            "2026 Trading Performance Audit",
            "Net P&amp;L",
            "Round Trips",
            "Win Rate",
            "Profit Factor",
            "Expectancy",
            "Max Drawdown",
            "Realized by week",
            "Focus",
            "Avoid",
            "Hidden leaks",
            "P&amp;L by Holding Period",
            "Entries",
            "Edge profile",
            "Best 5",
            "Worst 5",
            "Concentration",
            "Today's P&amp;L",
            "R-multiple",
            "Open positions",
            "Last 20 closed trades",
            "ACTIVE",
            "This week",
            "MTD",
            "Last 30 days",
            "Full CSV range",
            "66.0% gap to $1,000,000 USD",
            ">1/1<",
            ">12/31<",
            "Yahoo Finance",
            "Same day",
            "Overnight",
            "2–4d",
            "5–9d",
            "10d+",
        ):
            self.assertIn(phrase, html, phrase)


class PeriodFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.today = date(2026, 10, 11)
        fills, cls.columns = cp.load_fills(ROOT / "data" / "trades.csv")
        positions = cp.load_positions(ROOT / "data" / "open_positions.json")
        cls.audit = cp.assemble(fills, cls.columns, positions, cp.load_safety(ROOT), today=cls.today)
        cls.payload = json.loads(cp.audit_payload(cls.audit))
        cls.html = cp.render_html(cls.audit)

    def test_payload_is_closed_trips_only(self):
        self.assertEqual(len(self.payload["trips"]), self.audit["round_trips"])
        self.assertAlmostEqual(sum(row["pnl"] for row in self.payload["trips"]), self.audit["net"], places=4)
        self.assertNotIn("unreal", self.payload["trips"][0])
        self.assertEqual(self.payload["exitMin"], self.audit["closed"][0].exit.date().isoformat())
        self.assertEqual(self.payload["exitMax"], self.audit["closed"][-1].exit.date().isoformat())
        self.assertTrue(self.audit["open_rows"])
        self.assertTrue(all(row.get("unreal") is None for row in self.audit["open_rows"]))
        self.assertEqual(self.audit["round_trips"], 5)

    def test_markup_defaults_to_full_range(self):
        html = self.html
        self.assertIn('data-preset="all" aria-pressed="true"', html)
        self.assertIn('data-preset="week" aria-pressed="false"', html)
        self.assertIn('data-preset="mtd" aria-pressed="false"', html)
        self.assertIn('data-preset="d30" aria-pressed="false"', html)
        self.assertIn('data-preset="custom" aria-pressed="false"', html)
        self.assertIn('id="period-from"', html)
        self.assertIn('id="period-to"', html)
        self.assertIn("Unsettled open marks are not included", html)
        self.assertIn('id="audit-data"', html)
        self.assertIn("function auditCompute", html)
        self.assertNotIn("</" + "script>", (ROOT / "period_filter.js").read_text(encoding="utf-8").lower())
        data = html.split('id="audit-data">', 1)[1].split("</script>", 1)[0]
        self.assertEqual(len(json.loads(data)["trips"]), self.audit["round_trips"])

    def test_presets_and_windows_match_python(self):
        script = (ROOT / "period_filter.js").read_text(encoding="utf-8")
        for name in ("all", "week", "mtd", "d30"):
            start, end = cp.preset_bounds(name, self.today)
            got = self._bounds(script, name, self.today.isoformat())
            self.assertEqual(got["start"], None if start is None else start.isoformat())
            self.assertEqual(got["end"], None if end is None else end.isoformat())
            self._assert_window(script, start, end)
        self._assert_window(script, date(2026, 3, 1), date(2026, 3, 31))
        self._assert_window(script, date(2020, 1, 1), date(2020, 1, 2))
        week_start, week_end = cp.preset_bounds("week", self.today)
        week = cp.filter_exits(self.audit["closed"], week_start, week_end)
        self.assertTrue(week)
        self.assertTrue(all(week_start <= rt.exit.date() <= week_end for rt in week))
        self.assertLess(len(week), len(self.audit["closed"]))
        self.assertNotAlmostEqual(
            cp.metrics_from_closed(week, self.columns)["net"],
            self.audit["net"],
            places=0,
        )

    def test_money_format_matches_python(self):
        script = (ROOT / "period_filter.js").read_text(encoding="utf-8")
        values = [row["pnl"] for row in self.payload["trips"]]
        values.extend([self.audit["net"], self.audit["expectancy"], -self.audit["drawdown"]["dollars"]])
        runner = script + "\nconst nums = " + json.dumps(values) + ";\nprocess.stdout.write(JSON.stringify(nums.map((n) => auditFormat.money(n))));\n"
        got = json.loads(self._node(runner))
        for value, formatted in zip(values, got):
            self.assertEqual(formatted, cp.fmt_money(value), value)

    def _bounds(self, script, name, today):
        runner = (
            script
            + f"\nprocess.stdout.write(JSON.stringify(auditPresetBounds({json.dumps(name)}, {json.dumps(today)})));\n"
        )
        return json.loads(self._node(runner))

    def _assert_window(self, script, start, end):
        rows = cp.filter_exits(self.audit["closed"], start, end)
        metrics = cp.metrics_from_closed(rows, self.columns)
        view = json.loads(
            self._node(
                script
                + "\nprocess.stdout.write(JSON.stringify(auditCompute("
                + json.dumps(self.payload)
                + ", "
                + json.dumps(None if start is None else start.isoformat())
                + ", "
                + json.dumps(None if end is None else end.isoformat())
                + ")));\n"
            )
        )
        self.assertEqual(view["round_trips"], metrics["round_trips"])
        self.assertAlmostEqual(view["net"], metrics["net"], places=4)
        self._near(view["win_rate"], metrics["win_rate"])
        if metrics["profit_factor"] is None:
            self.assertIsNone(view["profit_factor"])
            self.assertFalse(view["profit_factor_infinite"])
        elif math_isinf(metrics["profit_factor"]):
            self.assertTrue(view["profit_factor_infinite"])
        else:
            self.assertAlmostEqual(view["profit_factor"], metrics["profit_factor"], places=6)
        self._near(view["expectancy"], metrics["expectancy"])
        self._near(view["expectancy_r"], metrics["expectancy_r"])
        self.assertAlmostEqual(view["drawdown"]["dollars"], metrics["drawdown"]["dollars"], places=4)
        self._near(view["drawdown"]["pct"], metrics["drawdown"]["pct"])
        trough = metrics["drawdown"]["trough"]
        self.assertEqual(view["drawdown"]["trough"], None if trough is None else trough.date().isoformat())
        self.assertEqual(view["focus"]["trades"], metrics["focus"]["trades"])
        self.assertEqual(view["avoid"]["trades"], metrics["avoid"]["trades"])
        self.assertAlmostEqual(view["focus"]["pnl"], metrics["focus"]["pnl"], places=4)
        self.assertAlmostEqual(view["avoid"]["pnl"], metrics["avoid"]["pnl"], places=4)
        self.assertEqual(view["buckets"], metrics["buckets"])
        ranked = sorted(
            metrics["leaks"],
            key=lambda row: (row["pnl"] is None, 0 if row["pnl"] is None else row["pnl"]),
        )
        self.assertEqual([row["name"] for row in view["leaks"]], [row["name"] for row in ranked])
        for got, exp in zip(view["leaks"], ranked):
            self.assertEqual(got["trades"], exp["trades"])
            self._near(got["pnl"], exp["pnl"])
        self.assertEqual([row["key"] for row in view["best"]], [row["key"] for row in metrics["best"]])
        self.assertEqual([row["key"] for row in view["worst"]], [row["key"] for row in metrics["worst"]])
        self.assertEqual([row["key"] for row in view["holds"]], [row["key"] for row in metrics["holds"]])
        for got, exp in zip(view["holds"], metrics["holds"]):
            self.assertAlmostEqual(got["pnl"], exp["pnl"], places=4)
            self.assertEqual(got["trades"], exp["trades"])
        self.assertEqual([row["symbol"] for row in view["recent"]], [rt.symbol for rt in metrics["recent"]])
        self.assertEqual(view["concentration"]["symbols"], metrics["concentration"]["symbols"])
        self.assertEqual(view["concentration"]["max_streak"], metrics["concentration"]["max_streak"])
        self._near(view["concentration"]["top3_share"], metrics["concentration"]["top3_share"])
        self._near(view["median_risk"], metrics["median_risk"])
        py_week = [point["week"].isoformat() for point in metrics["weekly"]]
        js_week = [point["week"] for point in view["weekly"]]
        self.assertEqual(js_week, py_week)

    def _near(self, got, exp):
        if exp is None:
            self.assertIsNone(got)
        else:
            self.assertAlmostEqual(got, exp, places=4)

    def _node(self, source: str) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
            handle.write(source)
            path = handle.name
        proc = subprocess.run(["node", path], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout


class OpenMarkTests(unittest.TestCase):
    def test_unrealized_risk_and_r_formulas(self):
        self.assertIsNone(cp.open_unrealized(50, 5, None))
        self.assertAlmostEqual(cp.open_unrealized(50, 5, 60), 50)
        self.assertAlmostEqual(cp.open_unrealized(50, 5, 40), -50)
        self.assertIsNone(cp.open_risk(50, 5, None))
        self.assertIsNone(cp.open_risk(534.3, 10, 534.33))
        self.assertAlmostEqual(cp.open_risk(50, 5, 40), 50)
        self.assertAlmostEqual(cp.open_risk(145.4, 50, 142), 170)
        self.assertIsNone(cp.open_r(50, None, 40))
        self.assertIsNone(cp.open_r(50, 60, None))
        self.assertIsNone(cp.open_r(534.3, 586.06, 534.33))
        self.assertAlmostEqual(cp.open_r(50, 60, 40), 1.0)
        self.assertAlmostEqual(cp.open_r(145.4, 139.67, 142), (139.67 - 145.4) / (145.4 - 142))

    def test_parser_uses_close_not_adjusted(self):
        try:
            import pandas as pd
        except ImportError:
            self.skipTest("pandas not installed")
        index = pd.to_datetime(["2026-10-08", "2026-10-09"])
        columns = pd.MultiIndex.from_tuples(
            [
                ("Adj Close", "BBB"),
                ("Close", "BBB"),
                ("Adj Close", "CCC"),
                ("Close", "CCC"),
                ("Close", "ZERO"),
            ],
            names=["Price", "Ticker"],
        )
        frame = pd.DataFrame(
            [
                [1.0, 10.0, 3.0, 30.0, 1.0],
                [2.0, 12.5, 4.0, 40.0, 0.0],
            ],
            index=index,
            columns=columns,
        )
        prices, bar = cp.closes_from_yahoo_frame(frame, ["BBB", "CCC", "ZERO"])
        self.assertEqual(prices["BBB"], 12.5)
        self.assertEqual(prices["CCC"], 40.0)
        self.assertNotIn("ZERO", prices)
        self.assertEqual(bar, date(2026, 10, 9))
        self.assertEqual(cp.closes_from_yahoo_frame(frame.iloc[0:0], ["BBB"]), ({}, None))

    def test_yahoo_marks_stay_out_of_net_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "data" / "trades.csv").write_text(
                "symbol,datetime,side,quantity,price,stop_price\n"
                "AAA,2026-10-01 10:00:00,BUY,10,20,10\n"
                "AAA,2026-10-02 10:00:00,SELL,10,23,\n",
                encoding="utf-8",
            )
            book = [{"symbol": "BBB", "qty": 5, "avg_price": 50, "stop_price": 40, "opened_at": "2026-10-09T16:00:00"}]
            (root / "data" / "open_positions.json").write_text(json.dumps(book), encoding="utf-8")
            snap = {
                "prices": {"BBB": 60.0},
                "bar_date": date(2026, 10, 9),
                "fetched_at": datetime(2026, 10, 11, 11, 0, tzinfo=cp.ET),
                "error": None,
                "missing": [],
            }
            with mock.patch.object(cp, "fetch_yahoo_last", return_value=snap) as fetch:
                summary = cp.build_page(root, today=date(2026, 10, 11))
            fetch.assert_called_once()
            self.assertEqual(json.loads((root / "data" / "open_positions.json").read_text(encoding="utf-8")), book)
            self.assertAlmostEqual(summary["net_pnl"], 30)
            self.assertEqual(summary["round_trips"], 1)
            self.assertAlmostEqual(summary["open_unreal"], 50)
            self.assertEqual(summary["today_pnl"], 0)
            html = (root / "docs" / "index.html").read_text(encoding="utf-8")
            self.assertIn('id="kpi-net">+$30', html)
            self.assertIn('id="open-unreal">+$50', html)
            self.assertIn(">60.00<", html)
            self.assertIn(">$50<", html)
            self.assertIn("Yahoo Finance daily close through Oct 9, 2026", html)
            self.assertIn("Open marks are not included in Net P&amp;L", html)
            self.assertIn("No. of R", html)
            self.assertIn("+1.00R", html)
            self.assertIn("Not included in net P&amp;L", html)
            payload = json.loads(html.split('id="audit-data">', 1)[1].split("</script>", 1)[0])
            self.assertEqual(len(payload["trips"]), 1)
            self.assertAlmostEqual(payload["trips"][0]["pnl"], 30)
            self.assertNotIn("unreal", payload["trips"][0])
            self.assertNotIn("last", payload["trips"][0])
            self.assertNotIn("open_unreal", payload)
            self.assertNotIn("open_r", payload)

    def test_missing_print_keeps_existing_last(self):
        positions = [{"symbol": "BBB", "qty": 5, "avg_price": 50, "stop": 40, "last": 11}]
        snap = {
            "prices": {},
            "bar_date": None,
            "fetched_at": datetime(2026, 10, 11, 11, 0, tzinfo=cp.ET),
            "error": None,
            "missing": ["BBB"],
        }
        with mock.patch.object(cp, "fetch_yahoo_last", return_value=snap):
            got = cp.apply_yahoo_last(positions)
        self.assertEqual(positions[0]["last"], 11)
        self.assertNotIn("last_source", positions[0])
        self.assertEqual(got["missing"], ["BBB"])

    def test_fetch_quotes_false_never_calls_yahoo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "data" / "trades.csv").write_text(
                "symbol,datetime,side,quantity,price\nAAA,2026-10-01 10:00:00,BUY,1,10\n",
                encoding="utf-8",
            )
            (root / "data" / "open_positions.json").write_text(
                json.dumps([{"symbol": "BBB", "qty": 1, "avg_price": 10}]),
                encoding="utf-8",
            )
            with mock.patch.object(cp, "fetch_yahoo_last") as fetch:
                summary = cp.build_page(root, today=date(2026, 10, 11), fetch_quotes=False)
            fetch.assert_not_called()
            self.assertIsNone(summary["open_unreal"])
            self.assertAlmostEqual(summary["net_pnl"], 0)
            html = (root / "docs" / "index.html").read_text(encoding="utf-8")
            self.assertIn("so unrealized P&amp;L is blank", html)


class StockBarTests(unittest.TestCase):
    def test_best_labels_are_dollars_and_bar_share(self):
        fills, columns = cp.load_fills(ROOT / "data" / "trades.csv")
        positions = cp.load_positions(ROOT / "data" / "open_positions.json")
        audit = cp.assemble(fills, columns, positions, None, today=date(2026, 10, 11))
        html = cp.diverging_html(audit["best"], share=True)
        self.assertIn('class="sym">LITE', html)
        self.assertIn("+$2,007 / +100.0%", html)
        self.assertIn('class="sym">NTAP', html)
        self.assertIn("+$1,229 / +61.2%", html)
        self.assertIn("divbar stock", html)
        holds = cp.diverging_html(audit["holds"], "label")
        self.assertNotIn(" / ", holds)
        self.assertNotIn('class="sym"', holds)
        self.assertIn('<p class="empty">No losing symbols.</p>', cp.render_html(audit))
        losers = cp.diverging_html(
            [
                {"key": "ZZZ", "pnl": -80, "trades": 1, "win_rate": 0},
                {"key": "YYY", "pnl": -20, "trades": 2, "win_rate": 0},
            ],
            share=True,
        )
        self.assertIn('class="sym">ZZZ', losers)
        self.assertIn("−$80 / −100.0%", losers)
        self.assertIn("−$20 / −25.0%", losers)

    def test_js_stock_labels_match_python(self):
        rows = [
            {"key": "LITE", "pnl": 2007.25, "trades": 1, "win_rate": 100},
            {"key": "NBIS", "pnl": 49, "trades": 1, "win_rate": 100},
        ]
        script = (ROOT / "period_filter.js").read_text(encoding="utf-8")
        proc = subprocess.run(
            ["node", "-e", script + "\nprocess.stdout.write(divergingHtml(" + json.dumps(rows) + ", true));"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, cp.diverging_html(rows, share=True))

    def test_open_table_r_column(self):
        html = cp.open_table(
            [
                {
                    "symbol": "BBB",
                    "qty": 5,
                    "avg_price": 50,
                    "stop": 40,
                    "last": 60,
                    "unreal": 50,
                    "risk": 50,
                    "r": 1.0,
                    "opened_at": None,
                },
                {
                    "symbol": "DELL",
                    "qty": 10,
                    "avg_price": 534.3,
                    "stop": 534.33,
                    "last": 586.06,
                    "unreal": 517.6,
                    "risk": None,
                    "r": None,
                    "opened_at": None,
                },
            ]
        )
        self.assertIn("No. of R", html)
        self.assertIn('class="num pos">+1.00R', html)
        self.assertIn(">DELL<", html)
        dell = html.split(">DELL<", 1)[1]
        self.assertIn('class="num flat">—', dell)


class PoolTests(unittest.TestCase):
    def test_placeholder_usd_series_and_window(self):
        points = cp.load_nav(ROOT / "data" / "nav_history.csv")
        self.assertEqual([point["date"] for point in points], [
            date(2026, 7, 3),
            date(2026, 7, 31),
            date(2026, 8, 28),
            date(2026, 9, 25),
            date(2026, 10, 9),
        ])
        self.assertTrue(all(point["currency"] == "USD" and point["source"] == "placeholder" for point in points))
        summary = cp.nav_summary(points)
        self.assertAlmostEqual(summary["latest"], 340000)
        self.assertAlmostEqual(summary["pct"], 34.0)
        self.assertAlmostEqual(summary["gap"], 660000)
        self.assertAlmostEqual(summary["gap_pct"], 66.0)
        self.assertTrue(summary["placeholder"])
        self.assertFalse(summary["hkd"])
        week = cp.nav_in_window(points, *cp.preset_bounds("week", date(2026, 10, 12)))
        self.assertEqual(week, [])
        month = cp.nav_in_window(points, *cp.preset_bounds("mtd", date(2026, 10, 12)))
        self.assertEqual([point["date"] for point in month], [date(2026, 10, 9)])
        trail = cp.nav_in_window(points, *cp.preset_bounds("d30", date(2026, 10, 12)))
        self.assertEqual([point["date"] for point in trail], [date(2026, 9, 25), date(2026, 10, 9)])
        label = cp.gap_caption(summary)
        self.assertEqual(label, "66.0% gap to $1,000,000 USD · placeholder")
        bars = cp.weekly_bars([
            {"week": date(2026, 10, 2), "pnl": 5000, "week_pnl": 2007.25},
            {"week": date(2026, 10, 9), "pnl": 3865.05, "week_pnl": -49},
        ])
        self.assertIn('class="wk-up"', bars)
        self.assertIn('class="wk-dn"', bars)
        self.assertIn("+$2,007", bars)
        self.assertNotIn("1/1", bars)
        self.assertNotIn("gap to $1,000,000", bars)
        self.assertIn("No closed round trips in this period", cp.weekly_bars([]))
        chart = cp.nav_svg(points, label, year=2026)
        self.assertIn("66.0% gap to $1,000,000 USD · placeholder", chart)
        self.assertIn(">1/1<", chart)
        self.assertIn(">12/31<", chart)
        self.assertIn("January 1 through December 31", chart)
        self.assertIn('class="nav-line"', chart)
        self.assertIn('class="nav-goal"', chart)
        self.assertNotIn("Swimming pool", chart)
        self.assertNotIn("SAMPLE", chart)
        span = (date(2026, 12, 31) - date(2026, 1, 1)).days
        inner = 960 - 88 - 24
        x_oct = 88 + inner * (date(2026, 10, 9) - date(2026, 1, 1)).days / span
        x_jul = 88 + inner * (date(2026, 7, 3) - date(2026, 1, 1)).days / span
        self.assertIn(f'cx="{x_oct:.1f}"', chart)
        self.assertIn(f'cx="{x_jul:.1f}"', chart)
        self.assertLess(x_jul + 40, x_oct)
        self.assertLess(x_oct, 88 + inner * 0.9)
        empty = cp.nav_svg([], "No NAV in this period", year=2026)
        self.assertIn(">1/1<", empty)
        self.assertIn(">12/31<", empty)
        self.assertIn("No NAV in this period", empty)
        self.assertNotIn("gap-label", empty)
        self.assertEqual(cp.chart_year([{"week": date(2026, 10, 9)}], [{"date": date(2026, 7, 3)}]), 2026)

    def test_hkd_converts_and_bad_rows_drop(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nav_history.csv"
            path.write_text(
                "date,amount,currency,fx_to_usd,source\n"
                "2026-08-01,800000,HKD,0.128,account\n"
                "2026-08-02,100,HKD,,account\n"
                "2026-08-03,50,EUR,1.1,account\n",
                encoding="utf-8",
            )
            points = cp.load_nav(path)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["currency"], "HKD")
        self.assertAlmostEqual(points[0]["nav"], 102400)
        summary = cp.nav_summary(points)
        self.assertAlmostEqual(summary["pct"], 10.24)
        self.assertAlmostEqual(summary["gap"], 897600)
        self.assertAlmostEqual(summary["gap_pct"], 89.76)
        self.assertTrue(summary["hkd"])
        self.assertFalse(summary["placeholder"])
        self.assertEqual(cp.gap_caption(summary), "89.8% gap to $1,000,000 USD · from HKD")

    def test_js_pool_matches_python(self):
        points = cp.load_nav(ROOT / "data" / "nav_history.csv")
        payload = [
            {
                "date": point["date"].isoformat(),
                "nav": point["nav"],
                "currency": point["currency"],
                "fx": point["fx"],
                "source": point["source"],
            }
            for point in points
        ]
        script = (ROOT / "period_filter.js").read_text(encoding="utf-8")
        runner = (
            script
            + "\nconst rows = "
            + json.dumps(payload)
            + ";\nconst week = navInWindow(rows, '2026-10-12', '2026-10-12');\n"
            + "const bars = weeklyBars([{week: '2026-10-02', pnl: 5000, week_pnl: 2007.25}, {week: '2026-10-09', pnl: 3865.05, week_pnl: -49}]);\n"
            + "const chart = navSvg(rows, gapCaption(navSummary(rows, 1000000)), 2026, 1000000);\n"
            + "process.stdout.write(JSON.stringify({all: navSummary(rows, 1000000), week: gapCaption(navSummary(week, 1000000)), mtd: gapCaption(navSummary(navInWindow(rows, '2026-10-01', '2026-10-12'), 1000000)), bars: bars, chart: chart}));"
        )
        proc = subprocess.run(["node", "-e", runner], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout)
        summary = cp.nav_summary(points)
        self.assertAlmostEqual(got["all"]["latest"], summary["latest"])
        self.assertAlmostEqual(got["all"]["gap_pct"], summary["gap_pct"])
        self.assertTrue(got["all"]["placeholder"])
        self.assertEqual(got["week"], "No NAV in this period")
        self.assertEqual(got["mtd"], cp.gap_caption(cp.nav_summary(cp.nav_in_window(points, date(2026, 10, 1), date(2026, 10, 12)))))
        self.assertIsNone(cp.nav_summary([])["gap_pct"])
        py_chart = cp.nav_svg(points, cp.gap_caption(summary), year=2026)
        span = (date(2026, 12, 31) - date(2026, 1, 1)).days
        x_oct = 88 + (960 - 88 - 24) * (date(2026, 10, 9) - date(2026, 1, 1)).days / span
        marker = f'cx="{x_oct:.1f}"'
        self.assertIn(marker, py_chart)
        self.assertIn(marker, got["chart"])
        self.assertIn(">1/1<", got["chart"])
        self.assertIn(">12/31<", got["chart"])
        self.assertIn('class="nav-goal"', got["chart"])
        self.assertIn("66.0% gap to $1,000,000 USD · placeholder", got["chart"])
        py_bars = cp.weekly_bars([
            {"week": date(2026, 10, 2), "pnl": 5000, "week_pnl": 2007.25},
            {"week": date(2026, 10, 9), "pnl": 3865.05, "week_pnl": -49},
        ])
        self.assertIn('class="wk-up"', got["bars"])
        self.assertIn('class="wk-dn"', got["bars"])
        self.assertIn('x="272.0"', py_bars)
        self.assertIn('x="272.0"', got["bars"])
        self.assertNotIn("1/1", got["bars"])


def math_isinf(value) -> bool:
    return value is not None and value == float("inf")


if __name__ == "__main__":
    unittest.main()
