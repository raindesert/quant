"""回测结果导出模块：JSON/CSV 导出 + 权益曲线图表生成。"""
from __future__ import annotations

import base64
import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

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


def plot_monthly_heatmap(
    monthly_returns: dict[str, float],
    symbol: str,
    output_path: str | Path,
):
    if not monthly_returns:
        print("无月度收益数据，跳过热力图")
        return

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(
        [(k, v) for k, v in monthly_returns.items()],
        columns=["month", "return"]
    )
    df["year"] = df["month"].str[:4].astype(int)
    df["month_num"] = df["month"].str[5:7].astype(int)

    pivot = df.pivot(index="year", columns="month_num", values="return")
    pivot = pivot.reindex(columns=range(1, 13))
    pivot.columns = ["1月", "2月", "3月", "4月", "5月", "6月",
                     "7月", "8月", "9月", "10月", "11月", "12月"]

    fig, ax = plt.subplots(figsize=(14, max(3, len(pivot) * 0.6)), facecolor=_COLORS["bg"])
    ax.set_facecolor(_COLORS["bg"])

    vmax = max(abs(pivot.min().min()), abs(pivot.max().max()), 1.0)
    im = ax.imshow(pivot.values, cmap="RdYlGn", aspect="auto", vmin=-vmax, vmax=vmax)

    for i in range(len(pivot)):
        for j in range(12):
            val = pivot.iloc[i, j]
            if not pd.isna(val):
                color = "white" if abs(val) > vmax * 0.5 else "black"
                ax.text(j, i, f"{val:+.1f}%", ha="center", va="center", fontsize=9, color=color)

    ax.set_xticks(range(12))
    ax.set_xticklabels(pivot.columns)
    ax.set_yticks(range(len(pivot)))
    ax.set_yticklabels(pivot.index)

    ax.set_title(f"{symbol} 月度收益热力图", fontsize=14, fontweight="bold")
    cbar = plt.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("收益率 (%)", fontsize=10)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"月度收益热力图已保存: {output_path}")


def plot_strategy_comparison(
    results: list[dict],
    output_path: str | Path,
):
    if not results:
        return

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 2, figsize=(16, 12), facecolor=_COLORS["bg"])

    names = [r.get("strategy", "") for r in results]
    profits = [r.get("profit_pct", 0) for r in results]
    sharpes = [r.get("sharpe_ratio", 0) for r in results]
    max_dds = [r.get("max_drawdown_pct", 0) for r in results]
    trades = [r.get("trades", 0) for r in results]
    win_rates = [r.get("win_rate", 0) for r in results]

    colors = plt.cm.tab10(range(len(names)))

    ax = axes[0, 0]
    bars = ax.bar(names, profits, color=colors, edgecolor="white", linewidth=0.5)
    ax.axhline(y=0, color="black", linewidth=0.5)
    ax.set_title("收益率对比", fontsize=12, fontweight="bold")
    ax.set_ylabel("收益率 (%)")
    for bar, val in zip(bars, profits):
        ax.text(bar.get_x() + bar.get_width() / 2, val, f"{val:+.1f}%",
                ha="center", va="bottom" if val >= 0 else "top", fontsize=9)

    ax = axes[0, 1]
    bars = ax.bar(names, sharpes, color=colors, edgecolor="white", linewidth=0.5)
    ax.axhline(y=0, color="black", linewidth=0.5)
    ax.set_title("夏普比率对比", fontsize=12, fontweight="bold")
    ax.set_ylabel("夏普比率")
    for bar, val in zip(bars, sharpes):
        ax.text(bar.get_x() + bar.get_width() / 2, val, f"{val:.2f}",
                ha="center", va="bottom" if val >= 0 else "top", fontsize=9)

    ax = axes[1, 0]
    bars = ax.bar(names, max_dds, color=colors, edgecolor="white", linewidth=0.5)
    ax.set_title("最大回撤对比", fontsize=12, fontweight="bold")
    ax.set_ylabel("回撤 (%)")
    for bar, val in zip(bars, max_dds):
        ax.text(bar.get_x() + bar.get_width() / 2, val, f"{val:.1f}%",
                ha="center", va="bottom", fontsize=9)

    ax = axes[1, 1]
    x = range(len(names))
    width = 0.35
    ax.bar([i - width / 2 for i in x], trades, width, label="交易次数", color=colors, alpha=0.7)
    ax2 = ax.twinx()
    ax2.bar([i + width / 2 for i in x], win_rates, width, label="胜率", color=colors, alpha=0.4)
    ax.set_xticks(x)
    ax.set_xticklabels(names)
    ax.set_title("交易次数与胜率", fontsize=12, fontweight="bold")
    ax.set_ylabel("交易次数")
    ax2.set_ylabel("胜率 (%)")
    ax.legend(loc="upper left")
    ax2.legend(loc="upper right")

    for ax in axes.flat:
        ax.set_facecolor(_COLORS["bg"])
        ax.grid(True, alpha=0.2)

    plt.suptitle("策略对比分析", fontsize=16, fontweight="bold", y=1.02)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"策略对比图已保存: {output_path}")


# ================== HTML 报告 ==================

_HTML_CSS = """
:root {
  --bg: #0f1419; --fg: #e6e6e6; --muted: #8b949e;
  --accent: #58a6ff; --green: #3fb950; --red: #f85149;
  --yellow: #d29922; --card: #161b22; --border: #30363d;
}
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: var(--bg); color: var(--fg); margin: 0; padding: 20px; line-height: 1.5; }
h1, h2, h3 { color: var(--fg); margin-top: 1.5em; }
h1 { border-bottom: 1px solid var(--border); padding-bottom: 0.3em; }
h2 { color: var(--accent); }
.container { max-width: 1200px; margin: 0 auto; }
.meta { color: var(--muted); font-size: 0.9em; margin-bottom: 1em; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
         gap: 12px; margin: 16px 0; }
.card { background: var(--card); border: 1px solid var(--border);
        border-radius: 8px; padding: 12px 16px; }
.card .label { color: var(--muted); font-size: 0.85em; margin-bottom: 4px; }
.card .value { font-size: 1.5em; font-weight: 600; }
.pos { color: var(--green); }
.neg { color: var(--red); }
.neu { color: var(--yellow); }
table { border-collapse: collapse; width: 100%; margin: 12px 0;
        background: var(--card); }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); }
th { background: rgba(255,255,255,0.05); font-weight: 600; }
tr:hover { background: rgba(255,255,255,0.02); }
.chart { background: var(--card); border: 1px solid var(--border);
         border-radius: 8px; padding: 12px; margin: 16px 0; }
.chart img { width: 100%; height: auto; display: block; }
.footer { color: var(--muted); font-size: 0.85em; text-align: center;
          margin-top: 2em; padding-top: 1em; border-top: 1px solid var(--border); }
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px;
         font-size: 0.8em; background: var(--border); color: var(--fg); }
"""


def _fig_to_base64(fig) -> str:
    """matplotlib figure → base64 PNG（嵌入 HTML）。"""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _format_pct(value, signed: bool = True) -> str:
    """格式化为带颜色 class 的百分比。"""
    if value is None or not isinstance(value, (int, float)):
        return '<span class="neu">N/A</span>'
    css = "pos" if value > 0 else ("neg" if value < 0 else "neu")
    sign = "+" if signed and value > 0 else ""
    return f'<span class="{css}">{sign}{value:.2f}%</span>'


def _safe_metric(summary: dict, key: str, default: Any = 0) -> Any:
    return summary.get(key, default) if isinstance(summary, dict) else default


def export_html_report(
    summary: dict,
    output_path: str | Path,
    title: str | None = None,
    trades: list[dict] | None = None,
    include_charts: bool = True,
) -> Path:
    """生成单文件 HTML 回测报告（自包含：CSS 内嵌 + 图表 base64 嵌入）。

    Args:
        summary: 回测结果 dict（含 profit_pct, sharpe_ratio, max_drawdown_pct 等）
        output_path: 输出 .html 路径
        title: 报告标题（默认用 symbol + strategy）
        trades: 交易记录列表（可选，会渲染明细表）
        include_charts: 是否生成权益曲线 + 回撤曲线（需要 matplotlib）

    Returns:
        output_path
    """
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not title:
        symbol = summary.get("symbol", "未知")
        strategy = summary.get("strategy", "未知策略")
        title = f"{symbol} · {strategy} · 回测报告"

    # 关键指标
    cards = [
        ("总收益率", _format_pct(_safe_metric(summary, "profit_pct"))),
        ("年化收益", _format_pct(_safe_metric(summary, "annual_return"))),
        ("夏普比率", f'{_safe_metric(summary, "sharpe_ratio", 0):.2f}'),
        ("最大回撤", _format_pct(_safe_metric(summary, "max_drawdown_pct"), signed=False)),
        ("胜率", _format_pct(_safe_metric(summary, "win_rate"))),
        ("交易次数", f'{_safe_metric(summary, "trades", 0)}'),
        ("盈利因子", f'{_safe_metric(summary, "profit_factor", 0):.2f}'),
        ("最终权益", f'{_safe_metric(summary, "final_value", 0):,.0f}'),
    ]

    cards_html = "\n".join(
        f'<div class="card"><div class="label">{label}</div><div class="value">{val}</div></div>'
        for label, val in cards
    )

    # 图表（用 matplotlib 生成）
    charts_html = ""
    if include_charts:
        charts_html = _build_charts_html(summary)

    # 交易明细表
    trades_html = ""
    if trades:
        # 取前 50 条
        rows = trades[:50]
        rows_html = "\n".join(
            f"<tr><td>{t.get('date', '')}</td>"
            f"<td>{t.get('action', '')}</td>"
            f"<td>{t.get('price', '')}</td>"
            f"<td>{t.get('shares', '')}</td>"
            f"<td>{t.get('amount', '')}</td></tr>"
            for t in rows
        )
        more = f'<p class="meta">（仅显示前 50 条，共 {len(trades)} 条）</p>' if len(trades) > 50 else ""
        trades_html = f"""
<h2>交易明细</h2>
<table>
<thead><tr><th>日期</th><th>动作</th><th>价格</th><th>数量</th><th>金额</th></tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
{more}
"""

    meta = f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    if "symbol" in summary:
        meta = f"标的: {summary['symbol']} · {meta}"
    if "start_date" in summary and "end_date" in summary:
        meta += f" · 区间: {summary['start_date']} ~ {summary['end_date']}"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{_HTML_CSS}</style>
</head>
<body>
<div class="container">
<h1>{title}</h1>
<p class="meta">{meta}</p>

<h2>关键指标</h2>
<div class="cards">
{cards_html}
</div>

{charts_html}

{trades_html}

<p class="footer">Generated by quant · {datetime.now().year}</p>
</div>
</body>
</html>
"""

    out_path.write_text(html, encoding="utf-8")
    print(f"HTML 报告已保存: {out_path}")
    return out_path


def _build_charts_html(summary: dict) -> str:
    """从 summary 的 equity_curve / benchmark_curve 生成 2 张图，返回 HTML。"""
    equity_curve = summary.get("equity_curve")
    benchmark_curve = summary.get("benchmark_curve")
    if not equity_curve:
        return ""

    df = pd.DataFrame(equity_curve)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")

    # 权益曲线
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(df.index, df["value"], label="策略", linewidth=2, color="#58a6ff")
    if benchmark_curve:
        bench_df = pd.DataFrame(benchmark_curve)
        if "date" in bench_df.columns:
            bench_df["date"] = pd.to_datetime(bench_df["date"])
            bench_df = bench_df.set_index("date")
        ax.plot(bench_df.index, bench_df["value"], label="基准",
                linewidth=1.5, color="#8b949e", linestyle="--", alpha=0.7)
    ax.set_title("权益曲线", fontsize=14, fontweight="bold")
    ax.set_xlabel("日期")
    ax.set_ylabel("权益")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    equity_b64 = _fig_to_base64(fig)

    # 回撤曲线
    values = df["value"].astype(float)
    running_max = values.cummax()
    drawdown = (values - running_max) / running_max * 100
    fig2, ax2 = plt.subplots(figsize=(10, 3))
    ax2.fill_between(df.index, drawdown, 0, color="#f85149", alpha=0.4)
    ax2.plot(df.index, drawdown, color="#f85149", linewidth=1)
    ax2.set_title("回撤曲线", fontsize=14, fontweight="bold")
    ax2.set_xlabel("日期")
    ax2.set_ylabel("回撤 (%)")
    ax2.grid(True, alpha=0.3)
    fig2.autofmt_xdate()
    fig2.tight_layout()
    dd_b64 = _fig_to_base64(fig2)

    return f"""
<h2>图表</h2>
<div class="chart"><img src="data:image/png;base64,{equity_b64}" alt="权益曲线"></div>
<div class="chart"><img src="data:image/png;base64,{dd_b64}" alt="回撤曲线"></div>
"""


def export_html_comparison(
    results: list[dict],
    output_path: str | Path,
    title: str = "策略对比报告",
) -> Path:
    """多策略对比 HTML 报告。

    Args:
        results: list[dict], 每个 dict 包含 strategy / symbol + 指标
        output_path: 输出 .html 路径
    """
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not results:
        raise ValueError("results 不能为空")

    # 表格行
    rows_html = "\n".join(
        f"<tr><td>{r.get('strategy', '')}</td>"
        f"<td>{r.get('symbol', '')}</td>"
        f"<td>{_format_pct(r.get('profit_pct'))}</td>"
        f"<td>{_format_pct(r.get('annual_return'))}</td>"
        f"<td>{r.get('sharpe_ratio', 0):.2f}</td>"
        f"<td>{_format_pct(r.get('max_drawdown_pct'), signed=False)}</td>"
        f"<td>{_format_pct(r.get('win_rate'))}</td>"
        f"<td>{r.get('trades', 0)}</td>"
        f"<td>{r.get('profit_factor', 0):.2f}</td></tr>"
        for r in results
    )

    meta = f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{_HTML_CSS}</style>
</head>
<body>
<div class="container">
<h1>{title}</h1>
<p class="meta">{meta} · 共 {len(results)} 个策略结果</p>

<h2>策略对比</h2>
<table>
<thead><tr>
<th>策略</th><th>标的</th><th>收益率</th><th>年化</th><th>夏普</th>
<th>回撤</th><th>胜率</th><th>交易数</th><th>盈利因子</th>
</tr></thead>
<tbody>
{rows_html}
</tbody>
</table>

<p class="footer">Generated by quant · {datetime.now().year}</p>
</div>
</body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")
    print(f"对比 HTML 报告已保存: {out_path}")
    return out_path

