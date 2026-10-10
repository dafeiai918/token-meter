# -*- coding: utf-8 -*-
"""token-meter 测试：临时库 + 本地样本文件，不发外网请求"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import token_meter as tm  # noqa: E402


class TokenMeterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self._orig = (tm.DB_PATH, tm.PRICE_PATH, tm.DATA_DIR)
        tm.DATA_DIR = base
        tm.DB_PATH = base / "usage.db"
        tm.PRICE_PATH = base / "prices.json"

    def tearDown(self):
        tm.DB_PATH, tm.PRICE_PATH, tm.DATA_DIR = self._orig
        self.tmp.cleanup()

    # ---------- 解析 ----------

    def test_read_proxy_log(self):
        path = Path(self.tmp.name) / "requests.jsonl"
        now = time.time()
        lines = [
            {"ts": now, "model": "gpt-4o-mini", "status": 200,
             "prompt_tokens": 100, "completion_tokens": 50},
            {"ts": now + 1, "model": "deepseek-chat", "status": 401,
             "prompt_tokens": 0, "completion_tokens": 0},
            {"ts": now + 2, "model": "gpt-4o", "status": 200,
             "prompt_tokens": 2000, "completion_tokens": 800},
        ]
        path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        rows = tm.read_proxy_log(path)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["prompt_tokens"], 100)
        self.assertFalse(rows[1]["ok"])

    def test_read_proxy_log_ignores_bad_lines(self):
        path = Path(self.tmp.name) / "bad.jsonl"
        path.write_text('{"ts":1,"model":"a","prompt_tokens":1,"completion_tokens":1}\nnot json\n',
                        encoding="utf-8")
        self.assertEqual(len(tm.read_proxy_log(path)), 1)

    def test_read_csv(self):
        path = Path(self.tmp.name) / "u.csv"
        path.write_text("date,model,input_tokens,output_tokens\n"
                        "1700000000,gpt-4o,1000,500\n", encoding="utf-8")
        rows = tm.read_csv(path)
        self.assertEqual(rows[0]["prompt_tokens"], 1000)
        self.assertEqual(rows[0]["completion_tokens"], 500)

    # ---------- 价格 ----------

    def test_match_price_exact_and_fuzzy(self):
        prices = {"gpt-4o": [2.5, 10.0], "deepseek-chat": [0.27, 1.1]}
        self.assertEqual(tm.match_price("gpt-4o", prices), [2.5, 10.0])
        self.assertEqual(tm.match_price("gpt-4o-2026-01-01", prices), [2.5, 10.0])
        self.assertIsNone(tm.match_price("unknown-model", prices))

    def test_calc_cost(self):
        cost = tm.calc_cost(1_000_000, 1_000_000, [2.5, 10.0])
        self.assertAlmostEqual(cost, 12.5)
        self.assertIsNone(tm.calc_cost(100, 100, None))

    def test_price_set_via_cli(self):
        self.assertEqual(tm.main(["price", "--set", "my-model:1/2"]), 0)
        self.assertEqual(tm.load_prices()["my-model"], [1.0, 2.0])

    def test_price_set_bad_format(self):
        self.assertEqual(tm.main(["price", "--set", "broken"]), 1)
        self.assertEqual(tm.main(["price", "--set", "m:1"]), 1)

    # ---------- 存储 ----------

    def test_insert_dedupes(self):
        conn = tm.connect()
        rows = [{"ts": 1000.5, "model": "m", "prompt_tokens": 10, "completion_tokens": 5}]
        added, skipped = tm.insert_rows(conn, rows)
        self.assertEqual((added, skipped), (1, 0))
        added, skipped = tm.insert_rows(conn, rows)
        self.assertEqual((added, skipped), (0, 1))
        conn.close()

    def test_aggregate_by_model(self):
        conn = tm.connect()
        now = time.time()
        tm.insert_rows(conn, [
            {"ts": now, "model": "a", "prompt_tokens": 100, "completion_tokens": 50},
            {"ts": now + 1, "model": "a", "prompt_tokens": 200, "completion_tokens": 20},
            {"ts": now + 2, "model": "b", "prompt_tokens": 10, "completion_tokens": 5},
        ])
        rows = dict((r[0], r) for r in tm.aggregate(conn, "model", now - 10))
        self.assertEqual(rows["a"][1], 2)
        self.assertEqual(rows["a"][2], 300)
        conn.close()

    # ---------- 端到端 ----------

    def test_import_then_report(self):
        log = Path(self.tmp.name) / "requests.jsonl"
        now = time.time()
        log.write_text("\n".join(json.dumps({
            "ts": now - i, "model": "gpt-4o-mini", "status": 200,
            "prompt_tokens": 1000, "completion_tokens": 500}) for i in range(5)),
            encoding="utf-8")
        self.assertEqual(tm.main(["import", "--file", str(log)]), 0)
        self.assertEqual(tm.main(["report", "--days", "7"]), 0)

    def test_export_csv(self):
        conn = tm.connect()
        tm.insert_rows(conn, [{"ts": time.time(), "model": "gpt-4o",
                               "prompt_tokens": 500, "completion_tokens": 100}])
        conn.close()
        out = Path(self.tmp.name) / "out.csv"
        self.assertEqual(tm.main(["export", "-o", str(out)]), 0)
        text = out.read_text(encoding="utf-8-sig")
        self.assertIn("gpt-4o", text)
        self.assertIn("cost_usd", text)

    def test_report_without_data(self):
        self.assertEqual(tm.main(["report"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
