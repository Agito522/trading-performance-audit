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
        summary = cp.build_page(ROOT, today=date(2026, 10, 11))
        self.assertEqual(summary["round_trips"], 5)
        self.assertAlmostEqual(summary["net_pnl"], 3865.05, places=2)
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
            "Weekly cumulative",
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


def math_isinf(value) -> bool:
    return value is not None and value == float("inf")


if __name__ == "__main__":
    unittest.main()
