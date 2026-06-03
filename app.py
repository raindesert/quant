"""A股量化交易系统 - Streamlit 图形化界面 (v6)

页面:
  - 📊 单策略回测 (含分钟级 + HTML 报告下载)
  - ⚔️ 策略对比
  - 🔧 参数优化 (含贝叶斯/随机搜索)
  - 🔀 多策略组合
  - 📁 YAML 预设
  - 🔄 Walk-Forward
  - 📡 实时行情
  - 🗂️ 回测历史 (新 v6)
"""
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yaml

from strategy.registry import STRATEGY_REGISTRY, create_strategy, get_strategy_class, list_strategies
from backtest.engine import BacktestEngine
from backtest.optimizer import StrategyOptimizer, DEFAULT_GRIDS, OPTIMIZE_METRICS, OptimizeMethod
from backtest.multi_strategy import MultiStrategyEngine
from backtest.output import export_html_report
from backtest.walk_forward import WalkForwardValidator
from config.loader import load_config as load_user_config, merge_config_with_args
from data.fetcher import DataFetcher
from data.processor import DataProcessor
from risk.manager import RiskManager


def load_config():
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


CONFIG = load_config()

# 页面常量 — 用枚举避免字符串匹配
PAGE_BACKTEST = "📊 单策略回测"
PAGE_COMPARISON = "⚔️ 策略对比"
PAGE_OPTIMIZE = "🔧 参数优化"
PAGE_MULTI_STRATEGY = "🔀 多策略组合"
PAGE_YAML = "📁 YAML 预设"
PAGE_WALK_FORWARD = "🔄 Walk-Forward"
PAGE_REALTIME = "📡 实时行情"
PAGE_HISTORY = "🗂️ 回测历史"

ALL_PAGES = [
    PAGE_BACKTEST, PAGE_COMPARISON, PAGE_OPTIMIZE, PAGE_MULTI_STRATEGY,
    PAGE_YAML, PAGE_WALK_FORWARD, PAGE_REALTIME, PAGE_HISTORY,
]


st.set_page_config(
    page_title="A股量化交易系统",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .metric-card { background: #f0f2f6; border-radius: 8px; padding: 16px; text-align: center; }
    .metric-value { font-size: 1.5rem; font-weight: 700; }
    .metric-label { font-size: 0.85rem; color: #666; }
    .positive { color: #d32f2f; }
    .negative { color: #388e3c; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=3600, show_spinner="正在获取行情...")
def _fetch_cached(symbol: str, days: int, frequency: str = "day"):
    """缓存数据获取，避免重复请求。"""
    fetcher = DataFetcher()
    return fetcher.get_history(symbol, days=days, frequency=frequency)


def render_sidebar() -> str:
    """渲染侧边栏 + 共享的全局参数。

    返回选中的页面常量。
    """
    with st.sidebar:
        st.title("📈 A股量化交易系统")
        st.markdown("---")
        page = st.radio("功能导航", ALL_PAGES, label_visibility="collapsed")

        st.markdown("---")
        st.markdown("### 全局参数")
        # 共享参数（所有 page 都能访问）
        st.session_state.setdefault("global_symbol", CONFIG.get("default_symbol", "000001.SZ"))
        st.session_state["global_symbol"] = st.text_input(
            "默认股票代码", value=st.session_state["global_symbol"],
            help="各页面默认使用此代码",
        )
        st.session_state.setdefault("global_days", CONFIG.get("backtest", {}).get("days", 250))
        st.session_state["global_days"] = st.slider(
            "默认回测天数", 30, 500, st.session_state["global_days"],
        )

        st.markdown("---")
        st.caption("v5.0 | A股量化交易系统")
    return page


def metric_card(col, label, value, fmt=".2f", suffix="", is_pct=False):
    with col:
        if isinstance(value, (int, float)):
            formatted = f"{value:{fmt}}{suffix}"
        else:
            formatted = str(value)
        color = ""
        if is_pct and isinstance(value, (int, float)):
            color = "positive" if value > 0 else "negative"
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value {color}">{formatted}</div>
            <div class="metric-label">{label}</div>
        </div>
        """, unsafe_allow_html=True)


# ============== 回测历史 (v6 新增) ==============

HISTORY_KEY = "backtest_history"
HISTORY_MAX = 50  # 最多保存多少条


def _history_add(summary: dict, mode: str = "backtest", extra: dict | None = None):
    """把一次回测结果追加到 session_state 历史。

    每条记录 = {
        "id": int (递增),
        "timestamp": ISO 字符串,
        "mode": str,
        "symbol": str,
        "strategy": str,
        "profit_pct": float,
        "sharpe_ratio": float,
        "max_drawdown_pct": float,
        "win_rate": float,
        "trades": int,
        "summary": dict (完整),
        "extra": dict (子策略列表等),
    }
    """
    history = st.session_state.setdefault(HISTORY_KEY, [])
    next_id = max((h["id"] for h in history), default=0) + 1
    record = {
        "id": next_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "symbol": summary.get("symbol", "?"),
        "strategy": summary.get("strategy", "?"),
        "profit_pct": summary.get("profit_pct", 0.0),
        "sharpe_ratio": summary.get("sharpe_ratio", 0.0),
        "max_drawdown_pct": summary.get("max_drawdown_pct", 0.0),
        "win_rate": summary.get("win_rate", 0.0),
        "trades": summary.get("trades", 0),
        "summary": summary,
        "extra": extra or {},
    }
    history.append(record)
    # 超过上限，删最旧（FIFO）
    if len(history) > HISTORY_MAX:
        history.pop(0)


def _history_remove(record_id: int):
    history = st.session_state.get(HISTORY_KEY, [])
    st.session_state[HISTORY_KEY] = [h for h in history if h["id"] != record_id]


def _history_clear():
    st.session_state[HISTORY_KEY] = []


def build_risk_manager(risk_enabled, max_position_pct, max_positions, max_drawdown_pct, max_daily_loss_pct, max_stock_loss_pct):
    if not risk_enabled:
        return None
    return RiskManager(
        max_position_pct=max_position_pct,
        max_positions=max_positions,
        max_drawdown_pct=max_drawdown_pct,
        max_daily_loss_pct=max_daily_loss_pct,
        max_stock_loss_pct=max_stock_loss_pct,
        enabled=True,
    )


def plot_equity_curve(summary):
    equity_curve = summary.get("equity_curve", [])
    benchmark_curve = summary.get("benchmark_curve", [])
    if not equity_curve:
        return None

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.7, 0.3], vertical_spacing=0.05,
                        subplot_titles=("权益曲线", "回撤"))

    dates = [e["date"] for e in equity_curve]
    values = [e["value"] for e in equity_curve]

    fig.add_trace(go.Scatter(x=dates, y=values, name="策略净值",
                             line=dict(color="#1f77b4", width=2)), row=1, col=1)

    if benchmark_curve:
        bm_dates = [e["date"] for e in benchmark_curve]
        bm_values = [e["value"] for e in benchmark_curve]
        fig.add_trace(go.Scatter(x=bm_dates, y=bm_values, name="基准(买入持有)",
                                 line=dict(color="#aaa", width=1, dash="dash")), row=1, col=1)

    peak = np.maximum.accumulate(values)
    drawdown = [(v - p) / p * 100 if p > 0 else 0 for v, p in zip(values, peak)]
    fig.add_trace(go.Scatter(x=dates, y=drawdown, name="回撤%",
                             fill="tozeroy", fillcolor="rgba(255,0,0,0.1)",
                             line=dict(color="#d32f2f", width=1)), row=2, col=1)

    fig.update_layout(height=500, showlegend=True, legend=dict(orientation="h", y=1.02),
                      margin=dict(l=50, r=20, t=40, b=30))
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="回撤%", row=2, col=1)
    return fig


def plot_monthly_heatmap(summary):
    monthly = summary.get("monthly_returns", {})
    if not monthly:
        return None

    records = []
    for key, ret in monthly.items():
        parts = key.split("-")
        if len(parts) >= 2:
            records.append({"年": int(parts[0]), "月": int(parts[1]), "收益率%": ret})

    if not records:
        return None

    df = pd.DataFrame(records)
    pivot = df.pivot(index="年", columns="月", values="收益率%")
    pivot.columns = [f"{m}月" for m in pivot.columns]

    fig = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=pivot.columns,
        y=[str(y) for y in pivot.index],
        colorscale=[[0, "#388e3c"], [0.5, "#fff"], [1, "#d32f2f"]],
        zmid=0,
        text=np.round(pivot.values, 2),
        texttemplate="%{text}%",
        colorbar=dict(title="收益率%"),
    ))
    fig.update_layout(title="月度收益热力图", height=300, margin=dict(l=50, r=20, t=40, b=30))
    return fig


def display_summary_metrics(summary):
    c1, c2, c3, c4, c5 = st.columns(5)
    metric_card(c1, "收益率", summary.get("profit_pct", 0), fmt="+.2f", suffix="%", is_pct=True)
    metric_card(c2, "年化收益", summary.get("annual_return", 0), fmt="+.2f", suffix="%", is_pct=True)
    metric_card(c3, "夏普比率", summary.get("sharpe_ratio", 0), fmt=".2f")
    metric_card(c4, "最大回撤", summary.get("max_drawdown_pct", 0), fmt=".2f", suffix="%")
    metric_card(c5, "交易次数", summary.get("trades", 0), fmt="d")

    c6, c7, c8, c9, c10 = st.columns(5)
    metric_card(c6, "Alpha", summary.get("alpha", 0), fmt="+.2f", suffix="%")
    metric_card(c7, "Beta", summary.get("beta", 0), fmt=".2f")
    metric_card(c8, "索提诺比", summary.get("sortino_ratio", 0), fmt=".2f")
    metric_card(c9, "信息比率", summary.get("information_ratio", 0), fmt=".2f")
    metric_card(c10, "Calmar比", summary.get("calmar_ratio", 0), fmt=".2f")

    c11, c12, c13, c14, c15 = st.columns(5)
    metric_card(c11, "胜率", summary.get("win_rate", 0), fmt=".1f", suffix="%")
    metric_card(c12, "盈利因子", summary.get("profit_factor", 0), fmt=".2f")
    metric_card(c13, "年化波动", summary.get("annual_volatility", 0), fmt=".2f", suffix="%")
    metric_card(c14, "基准收益", summary.get("benchmark_return", 0), fmt="+.2f", suffix="%")
    metric_card(c15, "最终价值", summary.get("final_value", 0), fmt=",.0f")


def display_trades_table(summary):
    trades = summary.get("trades_list", [])
    if not trades:
        st.info("暂无交易记录")
        return

    rows = []
    for t in trades:
        rows.append({
            "日期": t.get("date", ""),
            "操作": "买入 🔴" if t.get("action") == "buy" else "卖出 🟢",
            "价格": f"{t.get('price', 0):.2f}",
            "数量": t.get("quantity", 0),
            "金额": f"{t.get('total', 0):,.2f}",
            "佣金": f"{t.get('commission', 0):.2f}",
            "印花税": f"{t.get('stamp_tax', 0):.2f}",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, height=300)


def page_backtest():
    st.header("📊 单策略回测")

    with st.sidebar:
        st.markdown("### 回测参数")
        # 用全局参数作默认值
        symbol = st.text_input(
            "股票代码", value=st.session_state.get("global_symbol", "000001.SZ"),
            key="bt_symbol",
        )
        strategy_name = st.selectbox(
            "策略", list_strategies(), format_func=lambda x: x.upper(),
            key="bt_strategy",
        )
        days = st.slider(
            "回测天数", 30, 500,
            value=st.session_state.get("global_days", 250),
            key="bt_days",
        )
        # 新功能: 分钟级回测
        frequency = st.selectbox(
            "K线频率",
            ["day", "m1", "m5", "m15", "m30", "m60"],
            index=0,
            format_func=lambda x: {"day": "日线", "m1": "1分", "m5": "5分",
                                    "m15": "15分", "m30": "30分", "m60": "60分"}.get(x, x),
            help="分钟级回测时 T+1 和涨跌停检查自动禁用",
            key="bt_freq",
        )

        st.markdown("#### 交易参数")
        col1, col2 = st.columns(2)
        with col1:
            stop_loss = st.number_input("止损比例", 0.0, 0.5, 0.0, 0.01, format="%.2f", key="bt_sl")
            position_size = st.number_input("仓位比例", 0.1, 1.0, 1.0, 0.1, format="%.1f", key="bt_ps")
        with col2:
            take_profit = st.number_input("止盈比例", 0.0, 1.0, 0.0, 0.05, format="%.2f", key="bt_tp")
            slippage = st.number_input("滑点(%)", 0.0, 1.0, 0.1, 0.01, format="%.2f", key="bt_slip")

        st.markdown("#### 风控设置")
        risk_enabled = st.checkbox("启用风控", value=CONFIG.get("risk", {}).get("enabled", True), key="bt_risk")
        if risk_enabled:
            max_position_pct = st.slider("单股仓位上限", 0.05, 0.5, 0.25, 0.05, key="bt_mp")
            max_positions = st.slider("最大持仓数", 1, 20, 10, key="bt_mps")
            max_drawdown_pct = st.slider("最大回撤熔断", 0.05, 0.5, 0.2, 0.05, key="bt_md")
            max_daily_loss_pct = st.slider("日亏损上限", 0.01, 0.1, 0.03, 0.01, key="bt_dl")
            max_stock_loss_pct = st.slider("个股亏损上限", 0.05, 0.3, 0.1, 0.05, key="bt_slmax")
        else:
            max_position_pct = 0.25
            max_positions = 10
            max_drawdown_pct = 0.2
            max_daily_loss_pct = 0.03
            max_stock_loss_pct = 0.1

    if st.button("🚀 开始回测", type="primary", use_container_width=True, key="bt_go"):
        with st.spinner(f"正在回测 {symbol} ({frequency})..."):
            try:
                strategy = create_strategy(strategy_name)
                risk_manager = build_risk_manager(
                    risk_enabled, max_position_pct, max_positions,
                    max_drawdown_pct, max_daily_loss_pct, max_stock_loss_pct,
                )
                engine = BacktestEngine(
                    initial_cash=CONFIG.get("initial_cash", 1_000_000),
                    commission=CONFIG.get("backtest", {}).get("commission", 0.0003),
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    position_size=position_size,
                    slippage=slippage / 100,
                    slippage_type="percent",
                    enforce_t_plus_1=True,
                    check_limit=True,
                    risk_manager=risk_manager,
                )
                summary = engine.run(strategy, symbol, days=days, frequency=frequency)
            except Exception as exc:
                st.error(f"回测失败: {exc}")
                return

        if summary is None:
            st.error(f"无法获取 {symbol} 的数据，请检查股票代码或网络连接")
            return

        # 加 strategy 字段到 summary（让 HTML 报告标题能正确显示）
        summary["strategy"] = strategy_name
        summary["symbol"] = symbol
        # 写回 session_state + 追加到历史
        st.session_state["last_summary"] = summary
        st.session_state["last_symbol"] = symbol
        st.session_state["last_strategy"] = strategy_name
        _history_add(summary, mode="backtest")

    if "last_summary" in st.session_state:
        summary = st.session_state["last_summary"]
        symbol = st.session_state.get("last_symbol", "")
        strategy_name = st.session_state.get("last_strategy", "")

        st.subheader(f"{symbol} | {strategy_name.upper()} 策略回测结果")
        display_summary_metrics(summary)

        # 新功能: HTML 报告下载按钮
        col1, col2 = st.columns([3, 1])
        with col2:
            try:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
                    html_path = Path(f.name)
                export_html_report(summary, html_path)
                html_bytes = html_path.read_bytes()
                html_path.unlink()
                st.download_button(
                    "📥 下载 HTML 报告",
                    data=html_bytes,
                    file_name=f"{symbol}_{strategy_name}_report.html",
                    mime="text/html",
                    use_container_width=True,
                )
            except Exception as exc:
                st.warning(f"HTML 报告生成失败: {exc}")

        tab1, tab2, tab3, tab4 = st.tabs(["📈 权益曲线", "🗓️ 月度热力图", "📋 交易明细", "📊 详细指标"])

        with tab1:
            fig = plot_equity_curve(summary)
            if fig:
                st.plotly_chart(fig, use_container_width=True)

        with tab2:
            fig = plot_monthly_heatmap(summary)
            if fig:
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("月度数据不足")

        with tab3:
            display_trades_table(summary)

        with tab4:
            qs = summary.get("quantile_stats", {})
            if qs:
                st.markdown("#### 日收益分位数")
                c1, c2, c3 = st.columns(3)
                c1.metric("最佳日收益", f"{qs.get('best_day', 0):+.2f}%")
                c2.metric("中位数", f"{qs.get('p50', 0):+.2f}%")
                c3.metric("最差日收益", f"{qs.get('worst_day', 0):+.2f}%")

            st.markdown("#### 费用统计")
            c1, c2, c3 = st.columns(3)
            c1.metric("总佣金", f"{summary.get('total_commission', 0):,.2f}")
            c2.metric("总印花税", f"{summary.get('total_stamp_tax', 0):,.2f}")
            c3.metric("总滑点成本", f"{summary.get('total_slippage_cost', 0):,.2f}")


def page_strategy_comparison():
    st.header("⚔️ 策略对比")

    with st.sidebar:
        st.markdown("### 对比参数")
        symbol = st.text_input("股票代码", value="000001.SZ", key="cmp_symbol")
        days = st.slider("回测天数", 30, 500, 250, key="cmp_days")
        selected_strategies = st.multiselect("选择策略", list_strategies(), default=list_strategies())

    if not selected_strategies:
        st.warning("请至少选择一个策略")
        return

    if st.button("🚀 开始对比", type="primary", use_container_width=True):
        results = {}
        progress = st.progress(0)

        for i, sname in enumerate(selected_strategies):
            with st.spinner(f"回测 {sname.upper()} ..."):
                strategy = create_strategy(sname)
                engine = BacktestEngine(
                    initial_cash=CONFIG.get("initial_cash", 1_000_000),
                    commission=CONFIG.get("backtest", {}).get("commission", 0.0003),
                )
                summary = engine.run(strategy, symbol, days=days)
                if summary:
                    results[sname] = summary
            progress.progress((i + 1) / len(selected_strategies))

        st.session_state["cmp_results"] = results
        st.session_state["cmp_results_symbol"] = symbol

    if "cmp_results" not in st.session_state:
        return

    results = st.session_state["cmp_results"]
    if not results:
        st.error("所有策略回测失败")
        return

    st.subheader(f"{st.session_state.get('cmp_results_symbol', '')} 策略对比")

    comp_data = []
    for sname, s in results.items():
        comp_data.append({
            "策略": sname.upper(),
            "收益率%": f"{s.get('profit_pct', 0):+.2f}",
            "年化%": f"{s.get('annual_return', 0):+.2f}",
            "夏普": f"{s.get('sharpe_ratio', 0):.2f}",
            "回撤%": f"{s.get('max_drawdown_pct', 0):.2f}",
            "胜率%": f"{s.get('win_rate', 0):.1f}",
            "交易数": s.get("trades", 0),
            "Alpha%": f"{s.get('alpha', 0):+.2f}",
            "Beta": f"{s.get('beta', 0):.2f}",
            "索提诺": f"{s.get('sortino_ratio', 0):.2f}",
        })
    st.dataframe(pd.DataFrame(comp_data), use_container_width=True, hide_index=True)

    tab1, tab2 = st.tabs(["📈 净值对比", "📊 指标对比"])

    with tab1:
        fig = go.Figure()
        for sname, s in results.items():
            ec = s.get("equity_curve", [])
            if ec:
                fig.add_trace(go.Scatter(
                    x=[e["date"] for e in ec],
                    y=[e["value"] for e in ec],
                    name=sname.upper(),
                    mode="lines",
                ))
        fig.update_layout(title="策略净值对比", height=500,
                          yaxis_title="净值", legend=dict(orientation="h", y=1.02))
        st.plotly_chart(fig, use_container_width=True)

    with tab2:
        metrics_to_plot = ["profit_pct", "sharpe_ratio", "max_drawdown_pct", "win_rate"]
        metric_labels = ["收益率%", "夏普比率", "最大回撤%", "胜率%"]

        fig = make_subplots(rows=2, cols=2, subplot_titles=metric_labels)
        for idx, (metric, label) in enumerate(zip(metrics_to_plot, metric_labels)):
            row, col = idx // 2 + 1, idx % 2 + 1
            names = [s.upper() for s in results.keys()]
            vals = [results[s].get(metric, 0) for s in results.keys()]
            colors = ["#d32f2f" if v > 0 else "#388e3c" for v in vals]
            fig.add_trace(go.Bar(x=names, y=vals, marker_color=colors, showlegend=False),
                          row=row, col=col)

        fig.update_layout(height=500, margin=dict(l=50, r=20, t=40, b=30))
        st.plotly_chart(fig, use_container_width=True)


def page_optimize():
    st.header("🔧 参数优化")

    with st.sidebar:
        st.markdown("### 优化参数")
        symbol = st.text_input(
            "股票代码", value=st.session_state.get("global_symbol", "000001.SZ"),
            key="opt_symbol",
        )
        strategy_name = st.selectbox(
            "策略", list_strategies(), key="opt_strategy",
        )
        days = st.slider(
            "回测天数", 60, 500,
            value=st.session_state.get("global_days", 250),
            key="opt_days",
        )
        metric = st.selectbox(
            "优化指标", list(OPTIMIZE_METRICS.keys()),
            format_func=lambda x: OPTIMIZE_METRICS[x],
            key="opt_metric",
        )
        # 新功能: 优化方法（grid / random / bayesian）
        method_label = st.selectbox(
            "优化方法",
            ["grid", "random", "bayesian"],
            index=0,
            format_func=lambda x: {
                "grid": "Grid 暴力搜索",
                "random": "Random 随机搜索",
                "bayesian": "Bayesian 贝叶斯 (TPE)",
            }.get(x),
            help="贝叶斯在大范围参数时显著优于 Grid",
            key="opt_method",
        )
        n_trials = st.slider(
            "采样次数 (random/bayesian)", 10, 200, 50,
            disabled=(method_label == "grid"),
            key="opt_trials",
        )

    strategy_cls = get_strategy_class(strategy_name)
    if strategy_cls is None:
        st.error("策略不存在")
        return

    param_grid = DEFAULT_GRIDS.get(strategy_name, strategy_cls.get_param_grid())

    st.subheader(f"{strategy_name.upper()} 参数范围")
    custom_grid = {}
    for param_name, values in param_grid.items():
        min_val = min(values) if values else 0
        max_val = max(values) if values else 100
        step = values[1] - values[0] if len(values) > 1 else 1

        if isinstance(values[0], int):
            selected = st.slider(f"{param_name}", int(min_val), int(max_val),
                                 (int(min_val), int(max_val)), step=int(step), key=f"opt_{param_name}")
            custom_grid[param_name] = list(range(selected[0], selected[1] + int(step), int(step)))
        else:
            selected = st.slider(f"{param_name}", float(min_val), float(max_val),
                                 (float(min_val), float(max_val)),
                                 step=float(step) if step > 0 else 0.1, key=f"opt_{param_name}")
            step_f = float(step) if step > 0 else 0.1
            vals = []
            v = selected[0]
            while v <= selected[1] + 0.001:
                vals.append(round(v, 4))
                v += step_f
            custom_grid[param_name] = vals

    if st.button("🚀 开始优化", type="primary", use_container_width=True, key="opt_go"):
        optimizer = StrategyOptimizer(
            strategy_name=strategy_name,
            symbol=symbol,
            days=days,
            metric=metric,
        )

        with st.spinner(f"优化中 ({method_label})，请稍候..."):
            result = optimizer.optimize(
                custom_grid, method=method_label, n_trials=n_trials,
            )

        st.session_state["opt_result"] = result
        st.session_state["opt_metric_used"] = metric

    if "opt_result" not in st.session_state:
        return

    result = st.session_state["opt_result"]
    metric_used = st.session_state.get("opt_metric_used", metric)

    st.subheader("优化结果")

    best = result.get("best_params", {})
    best_score = result.get("best_score", 0)
    st.success(f"最优参数: {best} | {OPTIMIZE_METRICS.get(metric_used, metric_used)}: {best_score:.4f}")

    # 新功能: 显示 trials 数
    n_trials_done = result.get("n_trials")
    if n_trials_done is not None:
        st.caption(f"完成 {n_trials_done} trials（{method_label}）")

    all_results = result.get("all_results", [])
    if all_results:
        rows = []
        for r in all_results:
            row = {"参数": str(r.get("params", {}))}
            for m in ["profit_pct", "sharpe_ratio", "max_drawdown_pct", "win_rate", "trades"]:
                row[OPTIMIZE_METRICS.get(m, m)] = r.get(m, 0)
            rows.append(row)

        df = pd.DataFrame(rows)
        sort_col = OPTIMIZE_METRICS.get(metric_used, metric_used)
        if sort_col in df.columns:
            ascending = metric_used == "max_drawdown_pct"
            df = df.sort_values(sort_col, ascending=ascending).reset_index(drop=True)
        st.dataframe(df, use_container_width=True, height=400)


def page_multi_strategy():
    """多策略并行组合回测 (P2-2)."""
    st.header("🔀 多策略并行组合")

    with st.sidebar:
        st.markdown("### 组合参数")
        symbol = st.text_input(
            "股票代码", value=st.session_state.get("global_symbol", "000001.SZ"),
            key="ms_symbol",
        )
        days = st.slider(
            "回测天数", 30, 500,
            value=st.session_state.get("global_days", 250),
            key="ms_days",
        )
        strategies_text = st.text_input(
            "策略列表 (逗号分隔)",
            value="sma,rsi,bollinger",
            help="如 sma,rsi,bollinger,kdj",
            key="ms_strategies",
        )
        weights_text = st.text_input(
            "权重 (逗号分隔, 总和=1)",
            value="0.4,0.3,0.3",
            help="如 0.4,0.3,0.3；留空则等分",
            key="ms_weights",
        )

    if st.button("🚀 开始组合回测", type="primary", use_container_width=True, key="ms_go"):
        strategies = [s.strip() for s in strategies_text.split(",") if s.strip()]
        if not strategies:
            st.error("请输入至少一个策略")
            return

        weights = None
        if weights_text.strip():
            try:
                weights = [float(w.strip()) for w in weights_text.split(",") if w.strip()]
            except ValueError as exc:
                st.error(f"权重解析失败: {exc}")
                return

        with st.spinner(f"并发回测 {len(strategies)} 个策略..."):
            try:
                engine = MultiStrategyEngine(
                    strategies=strategies,
                    symbol=symbol,
                    days=days,
                    initial_cash=CONFIG.get("initial_cash", 1_000_000),
                    commission=CONFIG.get("backtest", {}).get("commission", 0.0003),
                    weights=weights,
                )
                result = engine.run()
            except Exception as exc:
                st.error(f"组合回测失败: {exc}")
                return

        st.session_state["ms_result"] = result
        # 入历史（组合 + 子策略）
        if isinstance(result, dict) and "combined" in result:
            combined = dict(result["combined"])
            combined["strategy"] = f"multi({len(result.get('strategies', []))})"
            combined["symbol"] = symbol
            _history_add(
                combined, mode="multi_strategy",
                extra={"sub_strategies": result.get("strategies", [])},
            )

    if "ms_result" not in st.session_state:
        return

    result = st.session_state["ms_result"]
    sub_results = result.get("strategies", [])
    combined = result.get("combined", {})

    st.subheader(f"{symbol} 多策略组合结果")

    # 子策略表格
    rows = []
    for r in sub_results:
        if "error" in r:
            rows.append({"策略": r.get("strategy", "?"), "状态": f"❌ {r['error']}"})
        else:
            rows.append({
                "策略": r.get("strategy", "?").upper(),
                "收益率%": f"{r.get('profit_pct', 0):+.2f}",
                "夏普": f"{r.get('sharpe_ratio', 0):.2f}",
                "回撤%": f"{r.get('max_drawdown_pct', 0):.2f}",
                "交易数": r.get("trades", 0),
            })
    rows.append({
        "策略": "【组合】",
        "收益率%": f"{combined.get('profit_pct', 0):+.2f}",
        "夏普": f"{combined.get('sharpe_ratio', 0):.2f}",
        "回撤%": f"{combined.get('max_drawdown_pct', 0):.2f}",
        "交易数": combined.get("trades", 0),
    })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # 组合权益曲线
    eq = combined.get("equity_curve", [])
    if eq:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=[e["date"] for e in eq],
            y=[e["value"] for e in eq],
            name="组合", line=dict(color="#58a6ff", width=2.5),
        ))
        # 也画每个子策略
        for r in sub_results:
            if "error" in r:
                continue
            sub_eq = r.get("equity_curve", [])
            if sub_eq:
                fig.add_trace(go.Scatter(
                    x=[e["date"] for e in sub_eq],
                    y=[e["value"] for e in sub_eq],
                    name=r.get("strategy", "?").upper(),
                    line=dict(width=1, dash="dash"), opacity=0.5,
                ))
        fig.update_layout(
            title="组合 vs 子策略权益曲线",
            height=500, yaxis_title="权益",
            legend=dict(orientation="h", y=1.02),
        )
        st.plotly_chart(fig, use_container_width=True)

    # HTML 报告下载
    if eq:
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
                html_path = Path(f.name)
            combined["strategy"] = "【组合】"
            combined["symbol"] = symbol
            export_html_report(combined, html_path)
            html_bytes = html_path.read_bytes()
            html_path.unlink()
            st.download_button(
                "📥 下载组合 HTML 报告",
                data=html_bytes,
                file_name=f"{symbol}_multi_strategy_report.html",
                mime="text/html",
            )
        except Exception as exc:
            st.warning(f"HTML 报告生成失败: {exc}")


def page_yaml_preset():
    """YAML 预设加载/保存 (P1-5)."""
    st.header("📁 YAML 配置预设")

    st.markdown("""
    预设可保存常用回测配置，复用时 `--config presets/xxx.yaml` 一行启动。
    """)

    presets_dir = Path(__file__).parent / "presets"
    presets_dir.mkdir(exist_ok=True)

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("加载现有预设")
        yaml_files = sorted(presets_dir.glob("*.yaml")) + sorted(presets_dir.glob("*.yml"))
        yaml_files = [f for f in yaml_files if f.is_file()]
        if not yaml_files:
            st.info("暂无预设")
        else:
            selected = st.selectbox(
                "选择预设",
                yaml_files,
                format_func=lambda p: p.name,
                key="yaml_load_select",
            )
            if selected:
                try:
                    cfg = load_user_config(selected)
                    st.success(f"已加载 {selected.name}（{len(cfg)} 字段）")
                    st.json(cfg)

                    st.markdown("#### 用此预设启动回测")
                    if st.button("🚀 用此配置回测", key="yaml_run"):
                        symbol = cfg.get("symbol", "000001.SZ")
                        strategy = cfg.get("strategy", "sma")
                        try:
                            strat_obj = create_strategy(strategy)
                            eng = BacktestEngine(
                                initial_cash=cfg.get("initial_cash", 1_000_000),
                                commission=cfg.get("commission", 0.0003),
                                stop_loss=cfg.get("stop_loss", 0.0),
                                take_profit=cfg.get("take_profit", 0.0),
                                position_size=cfg.get("position_size", 1.0),
                            )
                            summary = eng.run(strat_obj, symbol, days=cfg.get("days", 250))
                            if summary:
                                summary["strategy"] = strategy
                                summary["symbol"] = symbol
                                st.session_state["last_summary"] = summary
                                st.success("回测完成")
                            else:
                                st.error("回测失败")
                        except Exception as exc:
                            st.error(f"运行失败: {exc}")
                except Exception as exc:
                    st.error(f"加载失败: {exc}")

    with col2:
        st.subheader("快速创建预设")
        with st.form("yaml_create"):
            name = st.text_input("预设名 (不含后缀)", value="my_strategy")
            mode = st.selectbox("mode", ["backtest", "optimize", "walkforward", "multi_strategy"])
            strategy = st.selectbox("strategy", list_strategies())
            symbol = st.text_input("symbol", value="000001.SZ")
            days = st.number_input("days", 30, 500, 250)
            stop_loss = st.number_input("stop_loss", 0.0, 0.5, 0.0, 0.01, format="%.2f")
            take_profit = st.number_input("take_profit", 0.0, 1.0, 0.0, 0.05, format="%.2f")
            position_size = st.number_input("position_size", 0.1, 1.0, 1.0, 0.1, format="%.1f")
            submitted = st.form_submit_button("💾 保存预设")
            if submitted:
                preset_data = {
                    "mode": mode, "strategy": strategy, "symbol": symbol,
                    "days": days, "stop_loss": stop_loss, "take_profit": take_profit,
                    "position_size": position_size,
                }
                target = presets_dir / f"{name}.yaml"
                try:
                    with open(target, "w", encoding="utf-8") as f:
                        yaml.dump(preset_data, f, allow_unicode=True, default_flow_style=False)
                    st.success(f"已保存: {target}")
                except Exception as exc:
                    st.error(f"保存失败: {exc}")


def page_walk_forward():
    st.header("🔄 Walk-Forward 验证")

    with st.sidebar:
        st.markdown("### WF 参数")
        symbol = st.text_input(
            "股票代码", value=st.session_state.get("global_symbol", "000001.SZ"),
            key="wf_symbol",
        )
        strategy_name = st.selectbox("策略", list_strategies(), key="wf_strategy")
        train_days = st.slider("训练期(天)", 60, 250, 120, key="wf_train")
        test_days = st.slider("测试期(天)", 20, 120, 60, key="wf_test")
        step_days = st.slider("步进(天)", 20, 120, 60, key="wf_step")

    if st.button("🚀 开始验证", type="primary", use_container_width=True, key="wf_go"):
        validator = WalkForwardValidator(
            strategy_name=strategy_name,
            symbol=symbol,
            train_days=train_days,
            test_days=test_days,
            step_days=step_days,
        )

        with st.spinner("Walk-Forward 验证中..."):
            result = validator.validate()

        st.session_state["wf_result"] = result

    if "wf_result" not in st.session_state:
        return

    result = st.session_state["wf_result"]

    st.subheader(f"{result.strategy_name.upper()} | {result.symbol}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("窗口数", len(result.windows))
    c2.metric("训练期平均收益", f"{result.avg_train_return:+.2f}%")
    c3.metric("测试期平均收益", f"{result.avg_test_return:+.2f}%")
    c4.metric("衰减比", f"{result.degradation_ratio:.2f}")

    if result.degradation_ratio < 0.5:
        st.success("策略稳健性较好，过拟合风险低")
    elif result.degradation_ratio < 1.0:
        st.warning("策略存在一定过拟合风险")
    else:
        st.error("策略过拟合严重，样本外表现大幅下降")

    windows = result.windows
    if windows:
        rows = []
        for w in windows:
            rows.append({
                "窗口": w.window_id,
                "训练期": f"{w.train_start} ~ {w.train_end}",
                "测试期": f"{w.test_start} ~ {w.test_end}",
                "训练收益%": f"{w.train_result.get('profit_pct', 0):+.2f}",
                "测试收益%": f"{w.test_result.get('profit_pct', 0):+.2f}",
                "夏普(训练)": f"{w.train_result.get('sharpe_ratio', 0):.2f}",
                "夏普(测试)": f"{w.test_result.get('sharpe_ratio', 0):.2f}",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, height=300)

        fig = go.Figure()
        train_returns = [w.train_result.get('profit_pct', 0) for w in windows]
        test_returns = [w.test_result.get('profit_pct', 0) for w in windows]
        x_labels = [f"W{w.window_id}" for w in windows]

        fig.add_trace(go.Bar(x=x_labels, y=train_returns, name="训练期", marker_color="#1f77b4"))
        fig.add_trace(go.Bar(x=x_labels, y=test_returns, name="测试期", marker_color="#ff7f0e"))

        fig.update_layout(title="Walk-Forward 各窗口收益", barmode="group",
                          height=400, yaxis_title="收益率%")
        st.plotly_chart(fig, use_container_width=True)


def page_realtime():
    st.header("📡 实时行情")

    with st.sidebar:
        st.markdown("### 行情参数")
        symbols_text = st.text_area("股票代码（逗号分隔）", value="000001.SZ,600000.SH")
        auto_refresh = st.checkbox("自动刷新", value=False)
        refresh_interval = st.slider("刷新间隔(秒)", 5, 60, 15, key="rt_interval")

    symbols = [s.strip() for s in symbols_text.split(",") if s.strip()]

    if st.button("🔄 获取行情", use_container_width=True) or auto_refresh:
        fetcher = DataFetcher()
        rows = []
        for sym in symbols:
            try:
                data = fetcher.get_realtime(sym)
                if data:
                    change = data.get("change_pct", 0)
                    rows.append({
                        "代码": sym,
                        "名称": data.get("name", ""),
                        "现价": data.get("price", 0),
                        "涨跌幅%": f"{change:+.2f}",
                        "成交量": f"{data.get('volume', 0):,}",
                        "成交额": f"{data.get('amount', 0):,.0f}",
                        "今开": data.get("open", 0),
                        "最高": data.get("high", 0),
                        "最低": data.get("low", 0),
                    })
            except Exception:
                rows.append({"代码": sym, "名称": "获取失败"})

        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, height=400)
        else:
            st.warning("无法获取行情数据")

        if auto_refresh:
            import time
            time.sleep(refresh_interval)
            st.rerun()


def page_history():
    """回测历史 — 表格/趋势图/加载/导出。"""
    st.header("🗂️ 回测历史")
    st.caption("所有在当前 session 跑过的回测结果都会自动入库")

    history = st.session_state.get(HISTORY_KEY, [])

    if not history:
        st.info("暂无历史记录。先到「单策略回测」或「多策略组合」跑几次。")
        return

    # ============== 概览 ==============
    n = len(history)
    profitable = sum(1 for h in history if h["profit_pct"] > 0)
    avg_p = sum(h["profit_pct"] for h in history) / n
    best = max(history, key=lambda h: h["profit_pct"])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("总回测数", n)
    c2.metric("盈利次数", f"{profitable}/{n}")
    c3.metric("平均收益率", f"{avg_p:+.2f}%")
    c4.metric("最佳回测", f"{best['profit_pct']:+.2f}%", help=f"{best['symbol']} {best['strategy']}")

    st.markdown("---")

    # ============== 趋势图 ==============
    st.subheader("收益趋势")
    fig = go.Figure()
    symbols_seen = set()
    for h in history:
        if h["symbol"] in symbols_seen:
            continue
        symbols_seen.add(h["symbol"])
        same_symbol = [r for r in history if r["symbol"] == h["symbol"]]
        same_symbol.sort(key=lambda r: r["timestamp"])
        fig.add_trace(go.Scatter(
            x=[r["timestamp"] for r in same_symbol],
            y=[r["profit_pct"] for r in same_symbol],
            name=h["symbol"],
            mode="lines+markers",
        ))
    fig.update_layout(
        height=300, yaxis_title="收益率%",
        legend=dict(orientation="h", y=1.02),
        xaxis_title="时间",
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # ============== 表格 ==============
    st.subheader(f"回测列表 (按时间倒序)")

    rows = []
    for h in reversed(history):
        rows.append({
            "id": h["id"],
            "时间": h["timestamp"],
            "模式": h["mode"],
            "股票": h["symbol"],
            "策略": h["strategy"],
            "收益率%": f"{h['profit_pct']:+.2f}",
            "夏普": f"{h['sharpe_ratio']:.2f}",
            "回撤%": f"{h['max_drawdown_pct']:.2f}",
            "胜率%": f"{h['win_rate']:.1f}",
            "交易数": h["trades"],
        })
    df = pd.DataFrame(rows)
    st.dataframe(df.drop(columns=["id"]), use_container_width=True, hide_index=True, height=300)

    # ============== 操作 ==============
    st.markdown("---")
    st.subheader("操作")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.caption("加载到单策略回测主视图")
        selected_id_l = st.selectbox(
            "加载",
            options=[h["id"] for h in reversed(history)],
            format_func=lambda rid: next(
                f"#{rid} {h['symbol']}/{h['strategy']} ({h['timestamp']})"
                for h in history if h["id"] == rid
            ),
            key="hist_load_select",
            label_visibility="collapsed",
        )
        if st.button("📂 加载", key="hist_load", use_container_width=True):
            if selected_id_l is not None:
                rec = next((h for h in history if h["id"] == selected_id_l), None)
                if rec:
                    st.session_state["last_summary"] = rec["summary"]
                    st.session_state["last_symbol"] = rec["symbol"]
                    st.session_state["last_strategy"] = rec["strategy"]
                    st.success(f"已加载 #{rec['id']} {rec['symbol']}/{rec['strategy']} 到 📊 单策略回测")

    with col2:
        st.caption("导出回测为 JSON")
        selected_id_e = st.selectbox(
            "导出",
            options=[h["id"] for h in reversed(history)],
            format_func=lambda rid: next(
                f"#{rid} {h['symbol']}/{h['strategy']}" for h in history if h["id"] == rid
            ),
            key="hist_export_select",
            label_visibility="collapsed",
        )
        if st.button("📥 下载 JSON", key="hist_export", use_container_width=True):
            if selected_id_e is not None:
                import json
                rec = next((h for h in history if h["id"] == selected_id_e), None)
                if rec:
                    st.download_button(
                        "下载",
                        data=json.dumps(rec["summary"], ensure_ascii=False, indent=2, default=str),
                        file_name=f"{rec['symbol']}_{rec['strategy']}_{rec['timestamp'].replace(' ', '_').replace(':', '')}.json",
                        mime="application/json",
                        key="hist_download_btn",
                    )

    with col3:
        st.caption("删除单条记录")
        selected_id_d = st.selectbox(
            "删除",
            options=[h["id"] for h in reversed(history)],
            format_func=lambda rid: next(
                f"#{rid} {h['symbol']}/{h['strategy']}" for h in history if h["id"] == rid
            ),
            key="hist_delete_select",
            label_visibility="collapsed",
        )
        if st.button("🗑️ 删除", key="hist_delete", use_container_width=True):
            if selected_id_d is not None:
                _history_remove(selected_id_d)
                st.success(f"已删除 #{selected_id_d}")
                st.rerun()

    st.markdown("---")
    if st.button("🗑️ 清空所有历史", key="hist_clear", type="secondary"):
        _history_clear()
        st.success("已清空")
        st.rerun()


# 页面路由表 — 精确匹配常量，避免字符串包含误判
_PAGE_ROUTER = {
    PAGE_BACKTEST: page_backtest,
    PAGE_COMPARISON: page_strategy_comparison,
    PAGE_OPTIMIZE: page_optimize,
    PAGE_MULTI_STRATEGY: page_multi_strategy,
    PAGE_YAML: page_yaml_preset,
    PAGE_WALK_FORWARD: page_walk_forward,
    PAGE_REALTIME: page_realtime,
    PAGE_HISTORY: page_history,
}


def main():
    page = render_sidebar()
    handler = _PAGE_ROUTER.get(page)
    if handler is None:
        st.error(f"未知页面: {page}")
        return
    handler()


if __name__ == "__main__":
    main()
