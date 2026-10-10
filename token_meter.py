#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""token-meter · Token 用量与成本看板
导入 relay-proxy 日志或 CSV，按天/模型/站点统计用量与花费
零依赖 · SQLite 存储
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

__version__ = "1.0.0"

DATA_DIR = Path.home() / ".token-meter"
DB_PATH = DATA_DIR / "usage.db"
PRICE_PATH = DATA_DIR / "prices.json"
REMOTE_URL = "https://raw.githubusercontent.com/dafeiai918/oneclickrelay-remote/main/remote.json"
FALLBACK_RECOMMEND = {"name": "好同根", "url": "https://haotogen.com/"}

# 参考单价（美元 / 每百万 token），以官方为准，可用 price set 覆盖
DEFAULT_PRICES = {
    "gpt-4o": [2.5, 10.0],
    "gpt-4o-mini": [0.15, 0.6],
    "gpt-4.1": [2.0, 8.0],
    "gpt-4.1-mini": [0.4, 1.6],
    "gpt-3.5-turbo": [0.5, 1.5],
    "o3-mini": [1.1, 4.4],
    "claude-sonnet-4": [3.0, 15.0],
    "claude-opus-4": [15.0, 75.0],
    "claude-haiku": [0.8, 4.0],
    "deepseek-chat": [0.27, 1.1],
    "deepseek-reasoner": [0.55, 2.19],
    "qwen-plus": [0.4, 1.2],
    "qwen-turbo": [0.05, 0.2],
    "qwen-max": [1.6, 6.4],
    "glm-4": [0.6, 0.6],
    "gemini-2.0-flash": [0.1, 0.4],
    "gemini-2.5-pro": [1.25, 10.0],
    "moonshot-v1-8k": [1.68, 1.68],
}


class C:
    RESET = "\033[0m"; BOLD = "\033[1m"
    RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; CYAN = "\033[36m"; GRAY = "\033[90m"


def _c(code: str, text: str) -> str:
    return f"{code}{text}{C.RESET}"


def enable_ansi() -> None:
    if os.name == "nt":
        os.system("")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ---------------- 存储 ----------------

def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT UNIQUE,
            ts REAL NOT NULL,
            site TEXT DEFAULT '',
            model TEXT DEFAULT '',
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            ok INTEGER DEFAULT 1
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts)")
    conn.commit()
    return conn


def fingerprint(ts: float, model: str, pin: int, pout: int) -> str:
    return f"{round(ts, 3)}|{model}|{pin}|{pout}"


def insert_rows(conn: sqlite3.Connection, rows: list[dict]) -> tuple[int, int]:
    added = skipped = 0
    for row in rows:
        fp = fingerprint(row["ts"], row["model"], row["prompt_tokens"], row["completion_tokens"])
        try:
            conn.execute(
                "INSERT INTO usage (fingerprint, ts, site, model, prompt_tokens, "
                "completion_tokens, ok) VALUES (?,?,?,?,?,?,?)",
                (fp, row["ts"], row.get("site", ""), row["model"],
                 row["prompt_tokens"], row["completion_tokens"], 1 if row.get("ok", True) else 0))
            added += 1
        except sqlite3.IntegrityError:
            skipped += 1
    conn.commit()
    return added, skipped


# ---------------- 价格 ----------------

def load_prices() -> dict:
    prices = {k: list(v) for k, v in DEFAULT_PRICES.items()}
    if PRICE_PATH.exists():
        try:
            prices.update(json.loads(PRICE_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return prices


def save_prices(prices: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PRICE_PATH.write_text(json.dumps(prices, ensure_ascii=False, indent=2), encoding="utf-8")


def match_price(model: str, prices: dict) -> list[float] | None:
    if model in prices:
        return prices[model]
    low = model.lower()
    for key, value in prices.items():
        if key.lower() in low:
            return value
    return None


def calc_cost(prompt: int, completion: int, price: list[float] | None) -> float | None:
    if not price:
        return None
    return prompt / 1_000_000 * price[0] + completion / 1_000_000 * price[1]


# ---------------- 导入 ----------------

def read_proxy_log(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        rows.append({
            "ts": float(rec.get("ts", 0)),
            "site": rec.get("upstream", "") or rec.get("host", ""),
            "model": rec.get("model", "") or "(未知)",
            "prompt_tokens": int(rec.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(rec.get("completion_tokens", 0) or 0),
            "ok": int(rec.get("status", 200) or 200) < 400,
        })
    return rows


def read_csv(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for rec in csv.DictReader(fh):
            def num(*keys):
                for k in keys:
                    if k in rec and rec[k] not in (None, ""):
                        try:
                            return float(rec[k])
                        except ValueError:
                            continue
                return 0.0
            ts_raw = rec.get("ts") or rec.get("time") or rec.get("date") or ""
            try:
                ts = float(ts_raw)
            except ValueError:
                try:
                    ts = datetime.fromisoformat(str(ts_raw)).timestamp()
                except Exception:
                    ts = 0.0
            rows.append({
                "ts": ts,
                "site": rec.get("site") or rec.get("upstream") or "",
                "model": rec.get("model") or "(未知)",
                "prompt_tokens": int(num("prompt_tokens", "input_tokens", "in")),
                "completion_tokens": int(num("completion_tokens", "output_tokens", "out")),
                "ok": str(rec.get("status", "200")).startswith("2"),
            })
    return rows


# ---------------- 报表 ----------------

def aggregate(conn: sqlite3.Connection, group: str, since: float) -> list[tuple]:
    column = {"day": "date(ts, 'unixepoch', 'localtime')",
              "model": "model", "site": "site"}[group]
    return conn.execute(f"""
        SELECT {column} AS k,
               COUNT(*) AS calls,
               SUM(prompt_tokens) AS pin,
               SUM(completion_tokens) AS pout
        FROM usage WHERE ts >= ? GROUP BY k ORDER BY pin + pout DESC
    """, (since,)).fetchall()


def render_table(group: str, rows: list[tuple], prices: dict, days: int) -> tuple[int, int, float]:
    title = {"day": "按天", "model": "按模型", "site": "按站点"}[group]
    print()
    print(_c(C.GRAY, "═" * 84))
    print(_c(C.BOLD, f"  token-meter · {title}统计") + _c(C.GRAY, f"  近 {days} 天"))
    print(_c(C.GRAY, "═" * 84))
    print(f"  {title[1:]:<22}{'调用':<10}{'输入tokens':<16}{'输出tokens':<16}{'花费':<12}")
    print(_c(C.GRAY, "─" * 84))
    total_in = total_out = 0
    total_cost = 0.0
    priced = True
    for key, calls, pin, pout in rows:
        pin, pout = pin or 0, pout or 0
        total_in += pin
        total_out += pout
        price = match_price(str(key), prices)
        cost = calc_cost(pin, pout, price)
        if cost is None:
            priced = False
            cost_text = _c(C.GRAY, "无价格")
        else:
            total_cost += cost
            cost_text = f"${cost:.4f}"
        print(f"  {str(key):<22}{calls:<10}{pin:<16,}{pout:<16,}{cost_text:<12}")
    print(_c(C.GRAY, "─" * 84))
    tail = f"  {'合计':<22}{'':<10}{total_in:<16,}{total_out:<16,}${total_cost:.4f}"
    print(_c(C.BOLD, tail))
    print(_c(C.GRAY, "═" * 84))
    if not priced:
        print(_c(C.GRAY, "  提示：部分模型没有价格，用 token-meter price set 添加"))
    return total_in, total_out, total_cost


# ---------------- 命令 ----------------

def cmd_import(args) -> int:
    path = Path(args.file)
    if not path.exists():
        print(_c(C.RED, f"文件不存在: {path}"))
        return 1
    rows = read_csv(path) if path.suffix.lower() == ".csv" else read_proxy_log(path)
    if not rows:
        print(_c(C.YELLOW, "没有解析到记录。JSONL 需来自 relay-proxy，CSV 需含 model/tokens 列"))
        return 1
    conn = connect()
    added, skipped = insert_rows(conn, rows)
    print(f"导入完成：新增 {added} 条，跳过重复 {skipped} 条")
    print(_c(C.GRAY, f"数据库：{DB_PATH}"))
    total = conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
    print(_c(C.GRAY, f"累计记录：{total} 条"))
    conn.close()
    return 0


def cmd_report(args) -> int:
    conn = connect()
    since = (datetime.now() - timedelta(days=args.days)).timestamp()
    count = conn.execute("SELECT COUNT(*) FROM usage WHERE ts >= ?", (since,)).fetchone()[0]
    if count == 0:
        print(_c(C.YELLOW, "区间内没有数据。先导入：token-meter import --file requests.jsonl"))
        conn.close()
        return 1
    prices = load_prices()
    groups = args.by if args.by else ["day", "model"]
    if isinstance(groups, str):
        groups = [groups]
    total_cost = 0.0
    for group in groups:
        rows = aggregate(conn, group, since)
        _, _, cost = render_table(group, rows, prices, args.days)
        total_cost += cost
    if len(groups) > 1:
        print()
        print(_c(C.BOLD, f"  区间总花费  ${total_cost:.4f}"))
    conn.close()
    return 0


def cmd_price(args) -> int:
    prices = load_prices()
    if args.set:
        for item in args.set:
            if ":" not in item:
                print(_c(C.RED, f"格式应为 模型:输入价/输出价，收到 {item}"))
                return 1
            model, values = item.split(":", 1)
            if "/" not in values:
                print(_c(C.RED, f"缺少输出价：{item}"))
                return 1
            pin, pout = values.split("/", 1)
            try:
                prices[model.strip()] = [float(pin), float(pout)]
            except ValueError:
                print(_c(C.RED, f"价格必须是数字：{item}"))
                return 1
        save_prices(prices)
        print(f"已更新 {len(args.set)} 条价格（美元/百万token）")
        print(_c(C.GRAY, f"价格文件：{PRICE_PATH}"))
        return 0
    print(_c(C.GRAY, f"{'模型':<26}{'输入/百万':<14}{'输出/百万'}", ))
    for model in sorted(prices):
        pin, pout = prices[model]
        print(f"  {model:<26}${pin:<13}${pout}")
    print()
    print(_c(C.GRAY, "  修改：token-meter price set \"模型:输入价/输出价\""))
    return 0


def cmd_export(args) -> int:
    conn = connect()
    since = (datetime.now() - timedelta(days=args.days)).timestamp()
    rows = aggregate(conn, args.by, since)
    prices = load_prices()
    out = Path(args.out)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([args.by, "calls", "prompt_tokens", "completion_tokens", "cost_usd"])
        for key, calls, pin, pout in rows:
            cost = calc_cost(pin or 0, pout or 0, match_price(str(key), prices))
            writer.writerow([key, calls, pin or 0, pout or 0,
                             f"{cost:.6f}" if cost is not None else ""])
    print(f"已导出 {len(rows)} 行到 {out}")
    conn.close()
    return 0


def fetch_recommend(timeout: float = 5.0) -> dict | None:
    import urllib.request
    try:
        req = urllib.request.Request(REMOTE_URL, headers={"User-Agent": f"token-meter/{__version__}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        rec = data.get("recommend")
        if isinstance(rec, dict) and isinstance(rec.get("url"), str):
            return rec
    except Exception:
        pass
    return FALLBACK_RECOMMEND


def main(argv: list[str] | None = None) -> int:
    enable_ansi()
    parser = argparse.ArgumentParser(
        prog="token-meter", description="Token 用量与成本看板（数据来源：relay-proxy 日志 / CSV）")
    parser.add_argument("-V", "--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    p_import = sub.add_parser("import", help="导入 relay-proxy JSONL 或 CSV")
    p_import.add_argument("-f", "--file", required=True)
    p_import.set_defaults(func=cmd_import)

    p_report = sub.add_parser("report", help="生成报表")
    p_report.add_argument("-d", "--days", type=int, default=30)
    p_report.add_argument("--by", action="append", choices=["day", "model", "site"],
                          help="可多次指定，默认 day+model")
    p_report.set_defaults(func=cmd_report)

    p_price = sub.add_parser("price", help="查看/设置模型单价")
    p_price.add_argument("--set", action="append", default=[],
                         help='如 --set "gpt-4o:2.5/10"')
    p_price.set_defaults(func=cmd_price)

    p_export = sub.add_parser("export", help="导出 CSV")
    p_export.add_argument("-o", "--out", default="token-meter.csv")
    p_export.add_argument("-d", "--days", type=int, default=30)
    p_export.add_argument("--by", choices=["day", "model", "site"], default="model")
    p_export.set_defaults(func=cmd_export)

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        rec = fetch_recommend()
        if rec:
            print()
            print(_c(C.CYAN, f"  推荐中转站  {rec.get('name', '')}  {rec.get('url', '')}"))
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
