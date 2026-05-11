"""回测结果导出模块：JSON/CSV 导出 + 权益曲线图表生成。"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

# 中文字体支持 - 按优先级尝试可用字体
plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "FangSong",
    "STSong",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


def export_trades_csv(trades: list[dict], path: str | Path):
    """将交易记录导出为 CSV 文件。"""
    if not trades:
        return

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["date", "symbol", "action", "price", "quantity", "commission"]
    rows = []
    for t in trades:
        date_str = t["date"].strftime("%Y-%m-%d") if hasattr(t["date"], "strftime") else str(t["date"])
        rows.append({
            "date": date_str,
            "symbol": t.get("symbol", ""),
            "action": t.get("action", ""),
            "price": f"{t.get('price', 0):.4f}",
            "quantity": t.get("quantity", 0),
            "commission": f"{t.get('commission', 0):.4f}",
        })

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"交易记录已导出: {path}")


def export_summary_json(summary: dict, equity_curve: list[dict], benchmark_curve: list[dict], path: str | Path):
    """将回测汇总和曲线数据导出为 JSON 文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def serialize(obj):
        if isinstance(obj, dict):
            return {k: serialize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [serialize(i) for i in obj]
        if isinstance(obj, pd.Timestamp):
            return obj.strftime("%Y-%m-%d")
        if hasattr(obj, "strftime"):
            return obj.strftime("%Y-%m-%d")
        return obj

    output = {
        "summary": {k: v for k, v in summary.items() if k not in ("equity_curve", "benchmark_curve")},
        "equity_curve": serialize(equity_curve),
        "benchmark_curve": serialize(benchmark_curve),
    }

    def json_fallback(obj):
        """处理无法序列化的对象（Timestamp等），降级为字符串"""
        try:
            return obj.strftime("%Y-%m-%d")
        except Exception:
            return str(obj)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=json_fallback)

    print(f"回测结果已导出: {path}")


def plot_equity_curve(
    equity_curve: list[dict],
    benchmark_curve: list[dict],
    symbol: str,
    output_path: str | Path,
    title: str | None = None,
):
    """绘制并保存权益曲线对比图（策略 vs 基准）。"""
    if not equity_curve:
        print("无可用权益数据，跳过绘图")
        return

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 提取数据
    dates = [e["date"] for e in equity_curve]
    values = [e["value"] for e in equity_curve]
    bm_dates = [b["date"] for b in benchmark_curve] if benchmark_curve else []
    bm_values = [b["value"] for b in benchmark_curve] if benchmark_curve else []

    # 计算归一化（从 1 开始，方便对比）
    start_value = values[0]
    norm_values = [v / start_value * 100 for v in values]
    norm_bm = [v / bm_values[0] * 100 for v in bm_values] if bm_values and bm_values[0] > 0 else []

    fig, ax = plt.subplots(figsize=(12, 6))

    ax.plot(dates, norm_values, label="策略", color="#2196F3", linewidth=1.5)
    if norm_bm:
        ax.plot(bm_dates, norm_bm, label="基准 (买入持有)", color="#9E9E9E", linewidth=1.2, linestyle="--")

    # 标记买卖点
    buy_dates = [e["date"] for e in equity_curve if e.get("action") == "buy"]
    buy_values = [e["value"] / start_value * 100 for e in equity_curve if e.get("action") == "buy"]
    if buy_dates:
        ax.scatter(buy_dates, buy_values, marker="^", color="#4CAF50", s=40, label="买入", zorder=5)

    sell_dates = [e["date"] for e in equity_curve if e.get("action") == "sell"]
    sell_values = [e["value"] / start_value * 100 for e in equity_curve if e.get("action") == "sell"]
    if sell_dates:
        ax.scatter(sell_dates, sell_values, marker="v", color="#F44336", s=40, label="卖出", zorder=5)

    ax.axhline(y=100, color="#BDBDBD", linestyle=":", linewidth=1)
    ax.set_title(title or f"{symbol} 权益曲线")
    ax.set_xlabel("日期")
    ax.set_ylabel("归一化收益 (起始=100)")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)

    # x轴日期格式
    fig.autofmt_xdate()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"权益曲线图已保存: {output_path}")


def plot_drawdown_curve(
    equity_curve: list[dict],
    symbol: str,
    output_path: str | Path,
):
    """绘制回撤曲线。"""
    if not equity_curve:
        return

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    values = [e["value"] for e in equity_curve]
    dates = [e["date"] for e in equity_curve]

    peak = values[0]
    drawdowns = []
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        drawdowns.append(dd)

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.fill_between(dates, drawdowns, color="#F44336", alpha=0.3)
    ax.plot(dates, drawdowns, color="#F44336", linewidth=1)
    ax.set_title(f"{symbol} 回撤曲线")
    ax.set_xlabel("日期")
    ax.set_ylabel("回撤 (%)")
    ax.grid(True, alpha=0.3)

    fig.autofmt_xdate()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"回撤曲线图已保存: {output_path}")
