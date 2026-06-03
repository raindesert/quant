"""自选股票列表 - 持久化 + 跨 session 加载。

存储位置：~/.quant_watchlist.json（用户级，跨 session 保留）

数据格式：
{
  "version": 1,
  "stocks": [
    {"symbol": "000001.SZ", "name": "平安银行", "tags": ["银行", "核心"],
     "added": "2026-06-03 10:00:00", "enabled": true},
    ...
  ]
}
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# 默认存储位置（用户 home 目录）
DEFAULT_PATH = Path.home() / ".quant_watchlist.json"

# A股代码格式校验（深圳 6 开头 .SZ / 上海 6 开头 .SH）
_SYMBOL_RE = re.compile(r"^(?:\d{6})\.(?:SZ|SH)$")


def _normalize_symbol(symbol: str) -> str:
    """规范化股票代码：去空白，转大写。

    支持用户输入 '000001.sz'/'000001'/'sz000001' 等多种形式，统一成 '000001.SZ'。
    """
    s = symbol.strip().upper()
    if not s:
        return ""
    # 如果带 'sz'/'sh' 前缀，去掉
    if s.startswith("SZ") and len(s) == 8:
        s = s[2:] + ".SZ"
    elif s.startswith("SH") and len(s) == 8:
        s = s[2:] + ".SH"
    # 如果没后缀，6 位纯数字默认深市（000/002/003）vs 沪市（600/601/603）
    elif "." not in s and len(s) == 6 and s.isdigit():
        if s.startswith(("600", "601", "603", "605", "688")):
            s = s + ".SH"
        else:
            s = s + ".SZ"
    return s


def is_valid_symbol(symbol: str) -> bool:
    """校验股票代码格式。"""
    return bool(_SYMBOL_RE.match(symbol))


def load_watchlist(path: Path | str = DEFAULT_PATH) -> list[dict[str, Any]]:
    """加载自选列表。如果文件不存在或损坏，返回空列表。

    自动过滤掉无效/禁用项（默认启用）。调用方可自行决定如何处理 enabled 字段。
    """
    path = Path(path)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    stocks = data.get("stocks", [])
    if not isinstance(stocks, list):
        return []
    # 过滤无效项
    valid = []
    for s in stocks:
        if not isinstance(s, dict):
            continue
        sym = s.get("symbol", "")
        if not is_valid_symbol(sym):
            continue
        # 补默认字段
        s.setdefault("name", "")
        s.setdefault("tags", [])
        s.setdefault("added", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        s.setdefault("enabled", True)
        valid.append(s)
    return valid


def save_watchlist(stocks: list[dict[str, Any]],
                   path: Path | str = DEFAULT_PATH) -> None:
    """保存自选列表到文件。

    自动确保父目录存在。保存时校验每条记录。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 过滤无效项
    clean = []
    for s in stocks:
        if not isinstance(s, dict):
            continue
        sym = s.get("symbol", "")
        if not is_valid_symbol(sym):
            continue
        clean.append(s)
    data = {"version": 1, "stocks": clean, "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_stock(symbol: str, name: str = "", tags: list[str] | None = None,
              path: Path | str = DEFAULT_PATH) -> dict[str, Any] | None:
    """添加股票到自选。返回新条目，如果已存在或无效则返回 None。"""
    sym = _normalize_symbol(symbol)
    if not is_valid_symbol(sym):
        return None
    stocks = load_watchlist(path)
    # 查重
    for s in stocks:
        if s["symbol"] == sym:
            return None  # 已存在
    entry = {
        "symbol": sym,
        "name": name.strip(),
        "tags": list(tags or []),
        "added": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "enabled": True,
    }
    stocks.append(entry)
    save_watchlist(stocks, path)
    return entry


def remove_stock(symbol: str, path: Path | str = DEFAULT_PATH) -> bool:
    """从自选删除股票。返回是否成功删除。"""
    sym = _normalize_symbol(symbol)
    stocks = load_watchlist(path)
    new_stocks = [s for s in stocks if s["symbol"] != sym]
    if len(new_stocks) == len(stocks):
        return False
    save_watchlist(new_stocks, path)
    return True


def update_stock(symbol: str, path: Path | str = DEFAULT_PATH,
                  **fields) -> bool:
    """更新股票字段。返回是否成功。

    注意：symbol 和 added 字段不可改。
    """
    sym = _normalize_symbol(symbol)
    stocks = load_watchlist(path)
    found = False
    for s in stocks:
        if s["symbol"] == sym:
            for k, v in fields.items():
                if k in ("symbol", "added"):  # 不可改
                    continue
                s[k] = v
            found = True
            break
    if found:
        save_watchlist(stocks, path)
    return found


def get_enabled_symbols(path: Path | str = DEFAULT_PATH) -> list[str]:
    """获取所有启用的股票代码（按添加顺序）。"""
    return [s["symbol"] for s in load_watchlist(path) if s.get("enabled", True)]


def export_csv(path: Path | str = DEFAULT_PATH,
               output: Path | str | None = None) -> str:
    """导出为 CSV 字符串。"""
    stocks = load_watchlist(path)
    import csv
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["symbol", "name", "tags", "enabled", "added"])
    writer.writeheader()
    for s in stocks:
        writer.writerow({
            "symbol": s["symbol"],
            "name": s.get("name", ""),
            "tags": ",".join(s.get("tags", [])),
            "enabled": s.get("enabled", True),
            "added": s.get("added", ""),
        })
    return buf.getvalue()


def import_csv(content: str, path: Path | str = DEFAULT_PATH) -> int:
    """从 CSV 字符串导入。返回新添加的条目数（已存在不算）。"""
    import csv
    import io
    reader = csv.DictReader(io.StringIO(content))
    stocks = load_watchlist(path)
    existing = {s["symbol"] for s in stocks}
    added = 0
    for row in reader:
        sym = _normalize_symbol(row.get("symbol", ""))
        if not is_valid_symbol(sym) or sym in existing:
            continue
        tags_str = row.get("tags", "") or ""
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        entry = {
            "symbol": sym,
            "name": (row.get("name", "") or "").strip(),
            "tags": tags,
            "added": row.get("added", "") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "enabled": str(row.get("enabled", "True")).strip().lower() not in ("false", "0", "no", ""),
        }
        stocks.append(entry)
        existing.add(sym)
        added += 1
    if added:
        save_watchlist(stocks, path)
    return added
