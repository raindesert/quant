"""回测结果导出模块：JSON/CSV 导出 + 权益曲线图表生成。"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "FangSong",
    "STSong",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False

_COLORS = {
    "strategy": "#2196F3",
    "benchmark": "#9E9E9E",
    "buy": "#4CAF50",
    "sell": "#F44336",
    "drawdown": "#F44336",
    "grid": "#BDBDBD",
    "bg": "#FAFAFA",
}


def export_trades_csv(trades: list[dict], path: str | Path):
    if not trades:
        return

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["date", "symbol", "action", "price", "quantity", "entry_price", "commission_cost"]
    rows = []
    for t in trades:
        date_str = t["date"].strftime("%Y-%m-%d") if hasattr(t["date"], "strftime") else str(t["date"])
        rows.append({
            "date": date_str,
            "symbol": t.get("symbol", ""),
            "action": t.get("action", ""),
            "price": f"{t.get('price', 0):.4f}",
            "quantity": t.get("quantity", 0),
            "entry_price": f"{t.get('entry_price', 0):.4f}",
            "commission_cost": f"{t.get('commission_cost', 0):.4f}",
        })

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"交易记录已导出: {path}")


def export_summary_json(summary: dict, equity_curve: list[dict], benchmark_curve: list[dict], path: str | Path):
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
        try:
            return obj.strftime("%Y-%m-%d")
        except Exception:
            return str(obj)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=json_fallback)

    print(f"回测结果已导出: {path}")


def _find_max_drawdown_point(values: list[float]) -> tuple[int, float]:
    peak = values[0]
    max_dd_idx = 0
    max_dd = 0.0
    for i, v in enumerate(values):
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            max_dd_idx = i
    return max_dd_idx, max_dd


def plot_equity_curve(
    equity_curve: list[dict],
    benchmark_curve: list[dict],
    symbol: str,
    output_path: str | Path,
    title: str | None = None,
    summary: dict | None = None,
):
    if not equity_curve:
        print("无可用权益数据，跳过绘图")
        return

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dates = [e["date"] for e in equity_curve]
    values = [e["value"] for e in equity_curve]
    bm_dates = [b["date"] for b in benchmark_curve] if benchmark_curve else []
    bm_values = [b["value"] for b in benchmark_curve] if benchmark_curve else []

    start_value = values[0]
    norm_values = [v / start_value * 100 for v in values]
    norm_bm = [v / bm_values[0] * 100 for v in bm_values] if bm_values and bm_values[0] > 0 else []

    fig, ax = plt.subplots(figsize=(14, 7), facecolor=_COLORS["bg"])
    ax.set_facecolor(_COLORS["bg"])

    ax.plot(dates, norm_values, label="策略", color=_COLORS["strategy"], linewidth=1.8)
    if norm_bm:
        ax.plot(bm_dates, norm_bm, label="基准 (买入持有)", color=_COLORS["benchmark"], linewidth=1.2, linestyle="--")

    buy_dates = [e["date"] for e in equity_curve if e.get("action") == "buy"]
    buy_values = [e["value"] / start_value * 100 for e in equity_curve if e.get("action") == "buy"]
    if buy_dates:
        ax.scatter(buy_dates, buy_values, marker="^", color=_COLORS["buy"], s=50, label="买入", zorder=5, edgecolors="white", linewidths=0.5)

    sell_dates = [e["date"] for e in equity_curve if e.get("action") == "sell"]
    sell_values = [e["value"] / start_value * 100 for e in equity_curve if e.get("action") == "sell"]
    if sell_dates:
        ax.scatter(sell_dates, sell_values, marker="v", color=_COLORS["sell"], s=50, label="卖出", zorder=5, edgecolors="white", linewidths=0.5)

    ax.axhline(y=100, color=_COLORS["grid"], linestyle=":", linewidth=1)

    if summary:
        final_pct = summary.get("profit_pct", 0)
        dd_pct = summary.get("max_drawdown_pct", 0)
        sharpe = summary.get("sharpe_ratio", 0)
        win_rate = summary.get("win_rate", 0)
        text = f"收益: {final_pct:+.2f}%  回撤: {dd_pct:.2f}%  夏普: {sharpe:.2f}  胜率: {win_rate:.0f}%"
        ax.text(
            0.02, 0.97, text,
            transform=ax.transAxes,
            fontsize=10,
            verticalalignment="top",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="white", alpha=0.85, edgecolor="#E0E0E0"),
        )

    dd_idx, dd_val = _find_max_drawdown_point(values)
    if dd_val > 1.0 and dd_idx < len(dates):
        ax.annotate(
            f"-{dd_val:.1f}%",
            xy=(dates[dd_idx], norm_values[dd_idx]),
            xytext=(0, 15),
            textcoords="offset points",
            fontsize=9,
            color=_COLORS["sell"],
            fontweight="bold",
            arrowprops=dict(arrowstyle="->", color=_COLORS["sell"], lw=1.2),
        )

    ax.set_title(title or f"{symbol} 权益曲线", fontsize=14, fontweight="bold")
    ax.set_xlabel("日期", fontsize=11)
    ax.set_ylabel("归一化收益 (起始=100)", fontsize=11)
    ax.legend(loc="lower left", framealpha=0.9)
    ax.grid(True, alpha=0.2)

    fig.autofmt_xdate()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"权益曲线图已保存: {output_path}")


def plot_drawdown_curve(
    equity_curve: list[dict],
    symbol: str,
    output_path: str | Path,
):
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

    fig, ax = plt.subplots(figsize=(14, 4), facecolor=_COLORS["bg"])
    ax.set_facecolor(_COLORS["bg"])

    ax.fill_between(dates, drawdowns, color=_COLORS["drawdown"], alpha=0.25)
    ax.plot(dates, drawdowns, color=_COLORS["drawdown"], linewidth=1)

    max_dd = max(drawdowns) if drawdowns else 0
    if max_dd > 0:
        max_dd_idx = drawdowns.index(max_dd)
        ax.annotate(
            f"-{max_dd:.1f}%",
            xy=(dates[max_dd_idx], max_dd),
            xytext=(0, 10),
            textcoords="offset points",
            fontsize=10,
            color=_COLORS["sell"],
            fontweight="bold",
            arrowprops=dict(arrowstyle="->", color=_COLORS["sell"], lw=1.2),
        )

    ax.set_title(f"{symbol} 回撤曲线", fontsize=14, fontweight="bold")
    ax.set_xlabel("日期", fontsize=11)
    ax.set_ylabel("回撤 (%)", fontsize=11)
    ax.grid(True, alpha=0.2)

    fig.autofmt_xdate()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"回撤曲线图已保存: {output_path}")
