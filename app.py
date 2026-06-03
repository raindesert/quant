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
from utils.watchlist import (
    load_watchlist as _wl_load,
    add_stock as _wl_add,
    remove_stock as _wl_remove,
    get_enabled_symbols as _wl_enabled,
    DEFAULT_PATH as _WATCHLIST_PATH,
)
from utils.risk import (
    value_at_risk, conditional_var, max_drawdown,
    max_consecutive_losses, rolling_sharpe, rolling_volatility,
    monte_carlo_simulation, summary_risk_metrics,
)


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
PAGE_WATCHLIST = "⭐ 自选股票"
PAGE_RISK = "📉 风险分析"

ALL_PAGES = [
    PAGE_BACKTEST, PAGE_COMPARISON, PAGE_OPTIMIZE, PAGE_MULTI_STRATEGY,
    PAGE_YAML, PAGE_WALK_FORWARD, PAGE_REALTIME, PAGE_HISTORY, PAGE_WATCHLIST, PAGE_RISK,
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

        # 自选股票下拉 + 手动输入
        watchlist = _wl_load()
        enabled_syms = _wl_enabled()
        # 始终包含当前 session 的 global_symbol（如果没在自选里）
        st.session_state.setdefault("global_symbol", CONFIG.get("default_symbol", "000001.SZ"))
        if st.session_state["global_symbol"] not in enabled_syms:
            enabled_syms = [st.session_state["global_symbol"]] + enabled_syms

        if enabled_syms:
            current_idx = enabled_syms.index(
                st.session_state["global_symbol"]
            ) if st.session_state["global_symbol"] in enabled_syms else 0
            chosen = st.selectbox(
                "自选股票",
                options=enabled_syms,
                index=current_idx,
                help="自选股票列表来自 ~/.quant_watchlist.json，可到 ⭐ 自选股票页管理",
                key="sb_wl_chosen",
            )
            # 如果下拉变了，更新 global_symbol
            if chosen != st.session_state["global_symbol"]:
                st.session_state["global_symbol"] = chosen
                st.session_state["wl_last_switch"] = chosen

        # 手动输入（如果不在自选里也能用）
        manual = st.text_input(
            "或手动输入",
            value="",
            placeholder="000001.SZ",
            help="可填 SZ 前缀或纯数字，自动规范化",
            key="sb_manual_sym",
        )
        if manual.strip():
            from utils.watchlist import _normalize_symbol
            norm = _normalize_symbol(manual)
            if norm and norm != st.session_state["global_symbol"]:
                st.session_state["global_symbol"] = norm

        st.session_state.setdefault("global_days", CONFIG.get("backtest", {}).get("days", 250))
        st.session_state["global_days"] = st.slider(
            "默认回测天数", 30, 500, st.session_state["global_days"],
        )

        # 快捷：加当前股票到自选
        with st.expander("➕➖ 自选管理", expanded=False):
            cur_name = ""
            cur_tags = ""
            for s in watchlist:
                if s["symbol"] == st.session_state["global_symbol"]:
                    cur_name = s.get("name", "")
                    cur_tags = ",".join(s.get("tags", []))
                    break
            name_in = st.text_input("名称", value=cur_name, key="sb_add_name")
            tags_in = st.text_input("标签 (逗号)", value=cur_tags, key="sb_add_tags")
            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("➕ 添加/更新", key="sb_add_btn", use_container_width=True):
                    if not st.session_state["global_symbol"]:
                        st.error("股票代码为空")
                    else:
                        tags_list = [t.strip() for t in tags_in.split(",") if t.strip()]
                        # 用 add_stock; 已存在会返回 None — 走 update_stock
                        result = _wl_add(st.session_state["global_symbol"], name_in, tags_list)
                        if result is None:
                            # 已存在 — 更新名称/tags
                            from utils.watchlist import update_stock as _wl_update
                            _wl_update(
                                st.session_state["global_symbol"],
                                name=name_in,
                                tags=tags_list,
                            )
                            st.success("已更新")
                        else:
                            st.success(f"已添加 {result['symbol']}")
                        st.rerun()
            with col_b:
                # 删除当前 global_symbol（如果在自选里）
                in_wl = any(
                    s["symbol"] == st.session_state["global_symbol"] for s in watchlist
                )
                if st.button(
                    "🗑️ 删除当前",
                    key="sb_del_btn",
                    use_container_width=True,
                    disabled=not in_wl,
                ):
                    ok = _wl_remove(st.session_state["global_symbol"])
                    if ok:
                        st.success(f"已删除 {st.session_state['global_symbol']}")
                    else:
                        st.warning("该股票不在自选中")
                    st.rerun()

        st.markdown("---")
        st.caption(f"v6.0 | A股量化交易系统 | 自选 {len(watchlist)} 只")
    return page


def symbol_input(label: str, default: str, key: str, help_text: str | None = None) -> str:
    """股票代码输入：自选下拉 + 手动输入二选一。

    - 如果自选非空，先显示下拉（"⭐ 从自选选"），再用 text_input 输入临时值
    - 返回最终股票代码（已规范化）
    """
    from utils.watchlist import _normalize_symbol as _norm

    enabled_syms = _wl_enabled()
    default_sym = st.session_state.get("global_symbol", default)

    if enabled_syms:
        wl_labels = {sym: sym for sym in enabled_syms}
        # 把 default 也加进去（如果不在自选里）
        options = list(enabled_syms)
        if default_sym and default_sym not in options:
            options = [default_sym] + options
        if default_sym not in options:
            default_sym = options[0]
        idx = options.index(default_sym) if default_sym in options else 0
        chosen = st.selectbox(
            "⭐ 从自选选",
            options=options,
            index=idx,
            format_func=lambda x: wl_labels.get(x, x),
            key=f"{key}_sel",
            help=help_text or "优先从自选选；下方可手动输入覆盖",
        )
    else:
        chosen = default_sym

    manual = st.text_input(
        label, value="", placeholder=default,
        help=help_text or "可填 SZ 前缀或纯数字，自动规范化",
        key=key,
    )
    if manual.strip():
        n = _norm(manual)
        if n:
            return n
    return chosen


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
        action = t.get("action", "")
        # 买入🟢（绿色入场） / 卖出🔴（红色出场）— 与 A 股 UI 惯例一致
        if action == "buy":
            op_label = "🟢 买入"
        elif action == "sell":
            op_label = "🔴 卖出"
        else:
            op_label = f"⚪ {action}"
        rows.append({
            "日期": t.get("date", ""),
            "操作": op_label,
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
        symbol = symbol_input(
            "股票代码（可手动覆盖）",
            default=st.session_state.get("global_symbol", "000001.SZ"),
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
        symbol = symbol_input(
            "股票代码（可手动覆盖）",
            default=st.session_state.get("global_symbol", "000001.SZ"),
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


def _fetch_realtime_one(symbol: str) -> dict | None:
    """抓单只股票实时行情（带错误处理）。"""
    try:
        fetcher = DataFetcher()
        data = fetcher.get_realtime(symbol)
        return data
    except Exception as exc:
        return {"error": str(exc), "symbol": symbol}


@st.cache_data(ttl=30, show_spinner="获取实时行情...")
def _fetch_realtime_batch(symbols: tuple[str, ...]) -> dict[str, dict]:
    """批量抓取多只股票实时行情，30 秒缓存。

    返回: {symbol: {name, price, change_pct, ...} 或 {"error": str}}
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    result = {}
    if not symbols:
        return result
    with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as pool:
        futures = {pool.submit(_fetch_realtime_one, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                r = fut.result(timeout=15)
            except Exception as exc:
                r = {"error": str(exc)}
            result[sym] = r or {"error": "no data", "symbol": sym}
    return result


def page_realtime():
    """实时行情（v7 升级：自选 + 手动 + 详情 + 加/删自选 + 30s 缓存）。"""
    st.header("📡 实时行情")

    with st.sidebar:
        st.markdown("### 行情参数")
        auto_refresh = st.checkbox("自动刷新", value=False, key="rt_auto")
        refresh_interval = st.slider("刷新间隔(秒)", 10, 120, 30, key="rt_interval",
                                     help="缓存 30 秒，太短可能拿不到新数据")

    # 三个 tab
    tab_wl, tab_manual, tab_detail = st.tabs(["⭐ 自选股票", "✍️ 手动输入", "🔍 详情"])

    # ============ Tab 1: 自选股票 ============
    with tab_wl:
        watchlist = _wl_load()
        enabled = [s for s in watchlist if s.get("enabled", True)]
        if not enabled:
            st.info("自选为空。先到 ⭐ 自选股票页添加。")
        else:
            st.caption(f"自选 {len(enabled)} 只（30s 缓存）")
            symbols = tuple(s["symbol"] for s in enabled)
            # 强制刷新按钮
            cols = st.columns([3, 1])
            with cols[1]:
                if st.button("🔄 立即刷新", key="rt_wl_refresh", use_container_width=True):
                    _fetch_realtime_batch.clear()
                    st.rerun()
            with cols[0]:
                pass  # 占位对齐
            with st.spinner(f"获取 {len(symbols)} 只股票..."):
                data = _fetch_realtime_batch(symbols)
            # 渲染
            for s in enabled:
                sym = s["symbol"]
                quote = data.get(sym, {})
                _render_quote_card(s, quote, key_prefix=f"rt_wl_{sym}")

    # ============ Tab 2: 手动输入 ============
    with tab_manual:
        manual_text = st.text_area(
            "股票代码（逗号分隔）",
            value="000001.SZ,600000.SH",
            key="rt_manual_text",
            height=80,
        )
        cols = st.columns([1, 1])
        with cols[0]:
            refresh_now = st.button("🔄 获取行情", key="rt_manual_refresh",
                                     use_container_width=True)
        with cols[1]:
            if st.button("➕ 全部加入自选", key="rt_manual_add_all",
                         use_container_width=True):
                from utils.watchlist import _normalize_symbol as _norm
                added = 0
                for raw in manual_text.split(","):
                    sym = _norm(raw)
                    if sym and _wl_add(sym):
                        added += 1
                st.success(f"已添加 {added} 只")

        if refresh_now:
            from utils.watchlist import _normalize_symbol as _norm
            symbols = tuple(filter(None, [_norm(s) for s in manual_text.split(",")]))
            _fetch_realtime_batch.clear()
            with st.spinner(f"获取 {len(symbols)} 只..."):
                data = _fetch_realtime_batch(symbols)
            # 显示
            rows = []
            for sym in symbols:
                q = data.get(sym, {})
                if "error" in q:
                    rows.append({"代码": sym, "状态": f"❌ {q['error']}"})
                else:
                    rows.append({
                        "代码": sym,
                        "名称": q.get("name", ""),
                        "现价": f"{q.get('price', 0):.2f}",
                        "涨跌幅%": f"{q.get('change_pct', 0):+.2f}",
                        "今开": f"{q.get('open', 0):.2f}",
                        "最高": f"{q.get('high', 0):.2f}",
                        "最低": f"{q.get('low', 0):.2f}",
                    })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        elif not refresh_now:
            st.caption("点上面按钮获取行情")

    # ============ Tab 3: 详情 ============
    with tab_detail:
        watchlist = _wl_load()
        all_syms = [s["symbol"] for s in watchlist]
        if not all_syms:
            st.info("自选为空，先添加股票")
        else:
            sel = st.selectbox(
                "选择股票",
                options=all_syms,
                format_func=lambda x: next(
                    f"{s['symbol']} — {s.get('name', '') or '无'}" for s in watchlist
                    if s["symbol"] == x
                ),
                key="rt_detail_sel",
            )
            if st.button("📊 加载详情", key="rt_detail_load", use_container_width=True):
                if not sel:
                    st.error("请先选择股票")
                else:
                    quote = _fetch_realtime_batch((sel,)).get(sel, {})
                    if "error" in quote:
                        st.error(f"获取失败: {quote['error']}")
                    else:
                        st.session_state["rt_detail_quote"] = quote

            quote = st.session_state.get("rt_detail_quote")
            if quote and quote.get("symbol") == sel:
                _render_quote_detail(quote)

    # 自动刷新
    if auto_refresh:
        import time
        time.sleep(refresh_interval)
        st.rerun()


def _render_quote_card(stock: dict, quote: dict, key_prefix: str = ""):
    """渲染单只股票的行情卡片（含加/删自选按钮）。"""
    sym = stock["symbol"]
    name = stock.get("name", "") or quote.get("name", "")
    if "error" in quote:
        with st.container(border=True):
            cols = st.columns([3, 1])
            with cols[0]:
                st.markdown(f"**{sym}** — {name}")
                st.error(f"❌ {quote['error']}")
            with cols[1]:
                st.write("")
            return

    price = quote.get("price", 0)
    change_pct = quote.get("change_pct", 0)
    prev_close = quote.get("prev_close", 0)
    is_up = change_pct > 0
    is_down = change_pct < 0
    color = "#d32f2f" if is_up else ("#388e3c" if is_down else "#666666")
    arrow = "🔴" if is_up else ("🟢" if is_down else "⚪")

    with st.container(border=True):
        cols = st.columns([3, 2, 2, 1])
        with cols[0]:
            st.markdown(f"**{sym}** — {name}")
            st.caption(f"今开 {quote.get('open', 0):.2f} | 昨收 {prev_close:.2f}")
        with cols[1]:
            st.markdown(
                f"<div style='font-size:1.5em; color:{color}; font-weight:bold;'>"
                f"{arrow} {price:.2f}"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='color:{color};'>{change_pct:+.2f}%</div>",
                unsafe_allow_html=True,
            )
        with cols[2]:
            st.metric("最高", f"{quote.get('high', 0):.2f}", delta=None)
            st.metric("最低", f"{quote.get('low', 0):.2f}", delta=None)
        with cols[3]:
            # 详情按钮 — 把 quote 存到 session_state
            if st.button("🔍", key=f"{key_prefix}_detail", use_container_width=True,
                         help="看详情"):
                st.session_state["rt_detail_quote"] = quote
                st.session_state["rt_detail_page"] = True
            # 删自选
            if st.button("➖", key=f"{key_prefix}_remove", use_container_width=True,
                         help="从自选移除"):
                from utils.watchlist import remove_stock
                remove_stock(sym)
                st.rerun()


def _render_quote_detail(quote: dict):
    """渲染股票详情卡片。"""
    sym = quote.get("symbol", "")
    name = quote.get("name", "")
    price = quote.get("price", 0)
    change_pct = quote.get("change_pct", 0)
    color = "#d32f2f" if change_pct > 0 else ("#388e3c" if change_pct < 0 else "#666")

    st.markdown(
        f"### {sym} — {name}  "
        f"<span style='color:{color}; font-size:1.3em; font-weight:bold;'>"
        f"{price:.2f} ({change_pct:+.2f}%)</span>",
        unsafe_allow_html=True,
    )
    st.caption(f"更新时间: {quote.get('timestamp', '')}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("今开", f"{quote.get('open', 0):.2f}")
    c2.metric("昨收", f"{quote.get('prev_close', 0):.2f}")
    c3.metric("最高", f"{quote.get('high', 0):.2f}")
    c4.metric("最低", f"{quote.get('low', 0):.2f}")

    c1, c2 = st.columns(2)
    c1.metric("成交量", f"{quote.get('volume', 0):,.0f}")
    c2.metric("成交额", f"{quote.get('amount', 0):,.0f}")

    st.markdown("---")
    cols = st.columns(3)
    with cols[0]:
        # 加自选 / 已在自选
        from utils.watchlist import get_enabled_symbols
        if sym in get_enabled_symbols():
            st.success("✅ 已在自选")
        else:
            if st.button("➕ 加到自选", key="rt_detail_add", use_container_width=True):
                _wl_add(sym, name)
                st.success(f"已添加 {sym}")
                st.rerun()
    with cols[1]:
        if st.button("📊 跳到回测", key="rt_detail_goto_bt",
                     use_container_width=True, help="跳到单策略回测"):
            st.session_state["global_symbol"] = sym
            st.info(f"已设置 global_symbol={sym}，切到 📊 单策略回测")
    with cols[2]:
        if st.button("🔄 刷新", key="rt_detail_refresh", use_container_width=True):
            _fetch_realtime_batch.clear()
            st.rerun()

    # ============== 历史 K 线图表 (v22 新增) ==============
    st.markdown("---")
    st.subheader("📈 历史 K 线")
    _render_kline_section(sym, key_prefix="rt_detail_kl")


@st.cache_data(ttl=1800, show_spinner="加载 K 线...")
def _fetch_kline_cached(symbol: str, days: int, frequency: str = "day"):
    """缓存 K 线数据（30 分钟 TTL）。"""
    fetcher = DataFetcher()
    return fetcher.get_history(symbol, days=days, frequency=frequency)


def _render_kline_section(symbol: str, key_prefix: str = ""):
    """渲染 K 线图表 section：频率选择 + 蜡烛 + MA + 成交量。"""
    if not symbol:
        st.info("请先选择股票")
        return

    # 频率选择 + 天数
    col1, col2, col3 = st.columns([1, 1, 2])
    freq_label_map = {
        "day": "日线", "m1": "1分", "m5": "5分",
        "m15": "15分", "m30": "30分", "m60": "60分",
    }
    with col1:
        freq = st.selectbox(
            "频率",
            ["day", "m60", "m30", "m15", "m5", "m1"],
            index=0,
            format_func=lambda x: freq_label_map.get(x) or x,
            key=f"{key_prefix}_freq",
        )
    with col2:
        # 频率对应最大天数
        max_days = {"day": 1500, "m60": 30, "m30": 30, "m15": 15, "m5": 10, "m1": 5}
        days = st.number_input(
            "天数", 5, max_days.get(freq, 500),
            min(120, max_days.get(freq, 120)),
            key=f"{key_prefix}_days",
        )
    with col3:
        # MA 周期
        ma_periods_text = st.text_input(
            "MA 周期 (逗号分隔)", value="5,10,20,60",
            key=f"{key_prefix}_ma",
            help="例: 5,10,20,60",
        )
        try:
            ma_periods = [int(x.strip()) for x in ma_periods_text.split(",") if x.strip().isdigit()]
            if not ma_periods:
                ma_periods = [5, 10, 20, 60]
        except Exception:
            ma_periods = [5, 10, 20, 60]

    # 拉数据
    try:
        df = _fetch_kline_cached(symbol, days=days, frequency=freq)
    except Exception as exc:
        st.error(f"❌ K 线数据获取失败: {exc}")
        return

    if df is None or len(df) == 0:
        st.warning(f"⚠️ {symbol} 在 {freq} 频率下无数据")
        return

    # 验证必需列
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        st.error(f"数据缺列: {missing}")
        return

    # 计算 MA
    for p in ma_periods:
        df[f"ma{p}"] = df["close"].rolling(window=p, min_periods=1).mean()

    # 画图
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.03,
    )
    # K 线
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name="K线",
        increasing_line_color="#d32f2f", decreasing_line_color="#388e3c",
    ), row=1, col=1)
    # MA 线
    ma_colors = ["#FFA726", "#29B6F6", "#AB47BC", "#66BB6A", "#FFCA28", "#26C6DA"]
    for i, p in enumerate(ma_periods):
        if f"ma{p}" in df.columns:
            fig.add_trace(go.Scatter(
                x=df.index, y=df[f"ma{p}"], name=f"MA{p}",
                line=dict(width=1.2, color=ma_colors[i % len(ma_colors)]),
            ), row=1, col=1)
    # 成交量
    colors = ["#d32f2f" if c >= o else "#388e3c" for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(
        x=df.index, y=df["volume"], name="成交量",
        marker_color=colors, showlegend=False,
    ), row=2, col=1)

    fig.update_layout(
        height=550,
        title=f"{symbol} K线 ({freq}, {len(df)} bar)",
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", y=1.02),
        hovermode="x unified",
    )
    fig.update_xaxes(title_text="日期", row=2, col=1)
    fig.update_yaxes(title_text="价格", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)
    st.plotly_chart(fig, use_container_width=True)

    # ============== 技术指标副图 (v25 新增) ==============
    _render_indicator_section(df)

    # ============== 策略信号线 (v29 新增) ==============
    _render_strategy_overlay_section(df, symbol, freq)

    # ============== 买卖点标记 (v26 新增) ==============
    _render_buy_sell_section(df, symbol, freq)


def _render_indicator_section(df):
    """渲染 3 个技术指标副图 (MACD / RSI / KDJ)。

    每个指标:
    - expander 默认折叠
    - plotly 单图/多 line 展示
    - 关键参考线 (RSI 30/70, MACD 0, KDJ 20/80)
    """
    if df is None or len(df) < 5:
        return

    cols = st.columns(3)
    with cols[0]:
        show_macd = st.checkbox("MACD", value=True, key="ind_macd_toggle")
    with cols[1]:
        show_rsi = st.checkbox("RSI", value=False, key="ind_rsi_toggle")
    with cols[2]:
        show_kdj = st.checkbox("KDJ", value=False, key="ind_kdj_toggle")

    # MACD
    if show_macd:
        with st.expander("📊 MACD (异同移动平均线)", expanded=True):
            try:
                df_macd = DataProcessor.add_macd(df.copy())
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=df_macd.index, y=df_macd["macd_dif"],
                    name="DIF (快线)", line=dict(color="#FFA726", width=1.5),
                ))
                fig.add_trace(go.Scatter(
                    x=df_macd.index, y=df_macd["macd_dea"],
                    name="DEA (慢线)", line=dict(color="#29B6F6", width=1.5),
                ))
                # MACD 柱
                hist_colors = ["#d32f2f" if v >= 0 else "#388e3c" for v in df_macd["macd_hist"]]
                fig.add_trace(go.Bar(
                    x=df_macd.index, y=df_macd["macd_hist"],
                    name="MACD", marker_color=hist_colors,
                ))
                # 0 轴
                fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
                fig.update_layout(
                    height=350, hovermode="x unified",
                    legend=dict(orientation="h", y=1.05),
                    yaxis_title="MACD",
                )
                st.plotly_chart(fig, use_container_width=True)
                # 解释
                last_dif = df_macd["macd_hist"].iloc[-1] if len(df_macd) else 0
                state = "金叉区域" if last_dif > 0 else "死叉区域"
                st.caption(f"当前状态: **{state}** (DIF-DEA={last_dif:+.4f})")
            except Exception as exc:
                st.warning(f"MACD 渲染失败: {exc}")

    # RSI
    if show_rsi:
        with st.expander("📈 RSI (相对强弱指标)", expanded=False):
            try:
                periods = [6, 12, 24]
                fig = go.Figure()
                colors = ["#FFA726", "#29B6F6", "#AB47BC"]
                for p, c in zip(periods, colors):
                    df_rsi = DataProcessor.add_rsi(df.copy(), period=p)
                    fig.add_trace(go.Scatter(
                        x=df_rsi.index, y=df_rsi["rsi"],
                        name=f"RSI({p})", line=dict(color=c, width=1.2),
                    ))
                # 30/70 参考线
                fig.add_hline(y=70, line_dash="dash", line_color="#d32f2f",
                               annotation_text="超买 70", annotation_position="right",
                               opacity=0.5)
                fig.add_hline(y=30, line_dash="dash", line_color="#388e3c",
                               annotation_text="超卖 30", annotation_position="right",
                               opacity=0.5)
                fig.add_hrect(y0=30, y1=70, fillcolor="gray", opacity=0.05)
                fig.update_layout(
                    height=350, hovermode="x unified",
                    legend=dict(orientation="h", y=1.05),
                    yaxis_title="RSI", yaxis_range=[0, 100],
                )
                st.plotly_chart(fig, use_container_width=True)
                # 当前 RSI(14) 状态
                df_rsi_14 = DataProcessor.add_rsi(df.copy(), period=14)
                last_rsi = df_rsi_14["rsi"].iloc[-1] if len(df_rsi_14) else 50
                if last_rsi >= 70:
                    state = "🔴 超买"
                elif last_rsi <= 30:
                    state = "🟢 超卖"
                else:
                    state = "⚪ 中性"
                st.caption(f"当前 RSI(14): **{last_rsi:.2f}** {state}")
            except Exception as exc:
                st.warning(f"RSI 渲染失败: {exc}")

    # KDJ
    if show_kdj:
        with st.expander("📉 KDJ (随机指标)", expanded=False):
            try:
                df_kdj = DataProcessor.add_kdj(df.copy())
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=df_kdj.index, y=df_kdj["k"], name="K",
                    line=dict(color="#FFA726", width=1.5),
                ))
                fig.add_trace(go.Scatter(
                    x=df_kdj.index, y=df_kdj["d"], name="D",
                    line=dict(color="#29B6F6", width=1.5),
                ))
                fig.add_trace(go.Scatter(
                    x=df_kdj.index, y=df_kdj["j"], name="J",
                    line=dict(color="#AB47BC", width=1.5, dash="dot"),
                ))
                fig.add_hline(y=80, line_dash="dash", line_color="#d32f2f",
                               annotation_text="超买 80", annotation_position="right",
                               opacity=0.5)
                fig.add_hline(y=20, line_dash="dash", line_color="#388e3c",
                               annotation_text="超卖 20", annotation_position="right",
                               opacity=0.5)
                fig.update_layout(
                    height=350, hovermode="x unified",
                    legend=dict(orientation="h", y=1.05),
                    yaxis_title="KDJ", yaxis_range=[0, 100],
                )
                st.plotly_chart(fig, use_container_width=True)
                last_k = df_kdj["k"].iloc[-1] if len(df_kdj) else 50
                last_d = df_kdj["d"].iloc[-1] if len(df_kdj) else 50
                if last_k > last_d and last_k < 20:
                    state = "🟢 K 上穿 D（金叉，超卖区）"
                elif last_k > last_d and last_k < 50:
                    state = "🟢 K 上穿 D（金叉）"
                elif last_k < last_d and last_k > 80:
                    state = "🔴 K 下穿 D（死叉，超买区）"
                elif last_k < last_d and last_k > 50:
                    state = "🔴 K 下穿 D（死叉）"
                else:
                    state = "⚪ 中性"
                st.caption(f"当前 K={last_k:.2f}, D={last_d:.2f} — **{state}**")
            except Exception as exc:
                st.warning(f"KDJ 渲染失败: {exc}")

    # 简表
    with st.expander("📋 原始数据", expanded=False):
        st.dataframe(df.tail(20), use_container_width=True)


def _render_strategy_overlay_section(df, symbol: str, freq: str):
    """在 K 线上叠加策略信号 (SMA 通道 / Bollinger 通道 / MA 交叉)。

    - SMA 通道: 快/慢 2 条均线 + 金叉/死叉标记
    - Bollinger 通道: 上/中/下轨 + 突破点标记
    - 多种策略可叠加, 自由开关
    """
    with st.expander("📐 策略信号线叠加", expanded=False):
        st.caption("在 K 线上叠加 SMA / Bollinger 等通道线，标记关键交易信号")

        # ============== 参数 ==============
        col1, col2, col3 = st.columns(3)
        with col1:
            show_sma = st.checkbox("SMA 通道", value=True, key="sma_toggle")
        with col2:
            show_bb = st.checkbox("Bollinger 通道", value=False, key="bb_toggle")
        with col3:
            show_cross = st.checkbox("MA 交叉标记", value=True, key="cross_toggle")

        if not (show_sma or show_bb or show_cross):
            st.info("请至少勾选一个信号类型")
            return

        # SMA 周期
        sma_fast = 5
        sma_slow = 20
        if show_sma or show_cross:
            with st.container():
                cs1, cs2, cs3 = st.columns(3)
                with cs1:
                    sma_fast = st.number_input(
                        "SMA 快线", 2, 60, 5, 1, key="sma_fast",
                        help="默认 5",
                    )
                with cs2:
                    sma_slow = st.number_input(
                        "SMA 慢线", 5, 250, 20, 1, key="sma_slow",
                        help="默认 20",
                    )
                with cs3:
                    if sma_fast >= sma_slow:
                        st.warning("⚠️ 快线 ≥ 慢线，交叉不会发生")

        # Bollinger 参数
        bb_period = 20
        bb_std = 2.0
        if show_bb:
            with st.container():
                cb1, cb2 = st.columns(2)
                with cb1:
                    bb_period = st.number_input("BB 周期", 5, 100, 20, 1, key="bb_period")
                with cb2:
                    bb_std = st.number_input("BB 标准差倍数", 0.5, 4.0, 2.0, 0.1,
                                              key="bb_std", format="%.1f")

        # ============== 画图 ==============
        fig = _build_strategy_overlay_fig(
            df, symbol, freq,
            sma_fast=sma_fast, sma_slow=sma_slow,
            bb_period=bb_period, bb_std=bb_std,
            show_sma=show_sma, show_bb=show_bb, show_cross=show_cross,
        )
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True)

        # 解释 / 摘要
        _render_overlay_summary(df, sma_fast, sma_slow, bb_period, bb_std,
                                show_sma, show_bb, show_cross)


def _build_strategy_overlay_fig(
    df, symbol: str, freq: str,
    sma_fast: int = 5, sma_slow: int = 20,
    bb_period: int = 20, bb_std: float = 2.0,
    show_sma: bool = True, show_bb: bool = False, show_cross: bool = True,
):
    """构建带策略信号的 K 线图。"""
    if df is None or len(df) == 0:
        return None

    fig = go.Figure()
    # K 线
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name="K线",
        increasing_line_color="#d32f2f", decreasing_line_color="#388e3c",
    ))

    sma_fast_series = None
    sma_slow_series = None
    golden_x, golden_y = [], []
    death_x, death_y = [], []

    # SMA
    if show_sma or show_cross:
        df_sma = DataProcessor.add_ma(df.copy(), periods=[sma_fast, sma_slow])
        sma_fast_series = df_sma[f"ma{sma_fast}"]
        sma_slow_series = df_sma[f"ma{sma_slow}"]
        if show_sma:
            fig.add_trace(go.Scatter(
                x=df_sma.index, y=sma_fast_series,
                name=f"SMA{sma_fast}", line=dict(color="#FFA726", width=1.2),
            ))
            fig.add_trace(go.Scatter(
                x=df_sma.index, y=sma_slow_series,
                name=f"SMA{sma_slow}", line=dict(color="#29B6F6", width=1.5),
            ))

        # 找金叉/死叉
        if show_cross and sma_fast < sma_slow:
            diff = sma_fast_series - sma_slow_series
            for i in range(1, len(diff)):
                if diff.iloc[i - 1] <= 0 and diff.iloc[i] > 0:
                    golden_x.append(diff.index[i])
                    golden_y.append(sma_fast_series.iloc[i])
                elif diff.iloc[i - 1] >= 0 and diff.iloc[i] < 0:
                    death_x.append(diff.index[i])
                    death_y.append(sma_fast_series.iloc[i])

            if golden_x:
                fig.add_trace(go.Scatter(
                    x=golden_x, y=golden_y, mode="markers",
                    marker=dict(symbol="triangle-up", size=12, color="#388e3c",
                                line=dict(color="white", width=1)),
                    name=f"金叉 ({len(golden_x)})",
                    hovertemplate="金叉<br>%{x}<br>价: %{y:.2f}<extra></extra>",
                ))
            if death_x:
                fig.add_trace(go.Scatter(
                    x=death_x, y=death_y, mode="markers",
                    marker=dict(symbol="triangle-down", size=12, color="#d32f2f",
                                line=dict(color="white", width=1)),
                    name=f"死叉 ({len(death_x)})",
                    hovertemplate="死叉<br>%{x}<br>价: %{y:.2f}<extra></extra>",
                ))

    # Bollinger
    if show_bb:
        df_bb = DataProcessor.add_bollinger(df.copy(), period=bb_period, std=bb_std)
        fig.add_trace(go.Scatter(
            x=df_bb.index, y=df_bb["bb_upper"],
            name=f"BB上轨", line=dict(color="#AB47BC", width=1, dash="dot"),
        ))
        fig.add_trace(go.Scatter(
            x=df_bb.index, y=df_bb["bb_mid"],
            name=f"BB中轨", line=dict(color="#AB47BC", width=0.8),
        ))
        fig.add_trace(go.Scatter(
            x=df_bb.index, y=df_bb["bb_lower"],
            name=f"BB下轨", line=dict(color="#AB47BC", width=1, dash="dot"),
            fill="tonexty", fillcolor="rgba(171, 71, 188, 0.08)",
        ))

        # 突破点
        if sma_fast_series is not None and sma_slow_series is not None:
            close = df["close"]
        else:
            close = df["close"]
        upper_break = df.index[close > df_bb["bb_upper"]]
        lower_break = df.index[close < df_bb["bb_lower"]]
        if len(upper_break) > 0:
            fig.add_trace(go.Scatter(
                x=upper_break, y=close.loc[upper_break],
                mode="markers", marker=dict(symbol="x", size=8, color="#FF6F00"),
                name=f"突破上轨 ({len(upper_break)})",
            ))
        if len(lower_break) > 0:
            fig.add_trace(go.Scatter(
                x=lower_break, y=close.loc[lower_break],
                mode="markers", marker=dict(symbol="x", size=8, color="#1976D2"),
                name=f"突破下轨 ({len(lower_break)})",
            ))

    fig.update_layout(
        height=550,
        title=f"{symbol} 策略叠加 (SMA{sma_fast}/{sma_slow}, BB{bb_period}±{bb_std}σ)",
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        legend=dict(orientation="h", y=1.02),
    )
    return fig


def _render_overlay_summary(df, sma_fast, sma_slow, bb_period, bb_std,
                            show_sma, show_bb, show_cross):
    """显示当前策略状态摘要。"""
    if df is None or len(df) < max(sma_slow, bb_period):
        return

    import pandas as pd
    last = df["close"].iloc[-1]
    cols = st.columns(3)

    if show_sma or show_cross:
        df_sma = DataProcessor.add_ma(df.copy(), periods=[sma_fast, sma_slow])
        fast = df_sma[f"ma{sma_fast}"].iloc[-1]
        slow = df_sma[f"ma{sma_slow}"].iloc[-1]
        with cols[0]:
            trend = "🟢 多头 (快>慢)" if fast > slow else "🔴 空头 (快<慢)"
            st.metric(
                f"SMA{sma_fast} vs SMA{sma_slow}",
                f"{fast:.2f} / {slow:.2f}",
                delta=f"{fast - slow:+.2f}",
                help=trend,
            )

    if show_bb:
        df_bb = DataProcessor.add_bollinger(df.copy(), period=bb_period, std=bb_std)
        upper = df_bb["bb_upper"].iloc[-1]
        mid = df_bb["bb_mid"].iloc[-1]
        lower = df_bb["bb_lower"].iloc[-1]
        with cols[1]:
            position = (last - lower) / (upper - lower) * 100 if upper > lower else 50
            st.metric(
                f"BB 位置 ({bb_period}±{bb_std}σ)",
                f"{position:.0f}%",
                help=f"上 {upper:.2f} / 中 {mid:.2f} / 下 {lower:.2f}",
            )
        with cols[2]:
            if last > upper:
                state = "🔴 突破上轨（超买）"
            elif last < lower:
                state = "🟢 突破下轨（超卖）"
            else:
                state = "⚪ 通道内"
            st.metric("BB 状态", state)

    # 当前趋势简易总结
    st.caption(
        f"💡 最后价 {last:.2f} — "
        f"{'看多信号占优' if (show_sma and (cols[0].delta_value if hasattr(cols[0], 'delta_value') else 0) > 0) else '看空信号占优'}"
    )


def _render_buy_sell_section(df, symbol: str, freq: str):
    """渲染买卖点标记 section。

    三种数据源:
    1. 🚀 跑回测 — 实时跑一次 BacktestEngine，结果叠在 K 线上
    2. 📤 上传 CSV — 读用户上传的 trades.csv
    3. ✍️ 手动输入 — 通过 data_editor 输买卖点

    trades 格式: [{date, action ('buy'/'sell'), price, quantity, ...}, ...]
    """
    from utils.watchlist import _normalize_symbol as _norm

    with st.expander("🎯 买卖点标记", expanded=False):
        st.caption("在 K 线上叠加 ▲ 买 / ▼ 卖标记，便于回测验证和策略调试")

        # 选数据源
        source = st.radio(
            "数据源",
            ["🚀 跑一次回测", "📤 上传 CSV", "✍️ 手动输入"],
            key=f"bs_source_{symbol}_{freq}",
            horizontal=True,
        )

        trades = []

        if source == "🚀 跑一次回测":
            trades = _run_quick_backtest_for_chart(symbol, freq, key_prefix=f"bs_bt_{symbol}_{freq}")
        elif source == "📤 上传 CSV":
            trades = _load_trades_from_csv_upload(key=f"bs_upload_{symbol}_{freq}")
        elif source == "✍️ 手动输入":
            trades = _manual_trade_input(key_prefix=f"bs_manual_{symbol}_{freq}")

        if not trades:
            st.info("没有可显示的买卖点")
            return

        # 在 K 线上画标记
        st.markdown(f"#### 共 {len(trades)} 个买卖点")
        _display_trades_table(trades)

        # 算胜负
        wins, losses, total = _calc_trade_pnl(trades)
        if total > 0:
            cols = st.columns(3)
            cols[0].metric("总交易", total)
            cols[1].metric("盈利", wins, help="盈亏>0 的回合")
            cols[2].metric("亏损", losses, help="盈亏<0 的回合")

        # 给 df 加 _has_trade 列
        df_marked = _mark_df_with_trades(df, trades)
        if df_marked is not None:
            fig_with_markers = _build_kline_with_markers(df_marked, trades, symbol, freq)
            st.plotly_chart(fig_with_markers, use_container_width=True)


def _run_quick_backtest_for_chart(symbol: str, freq: str, key_prefix: str = "") -> list:
    """跑一次回测，返 trades 列表（仅支持日线/分钟线）。"""
    from strategy.registry import list_strategies

    cols = st.columns(2)
    with cols[0]:
        strategies = list_strategies()
        strat = st.selectbox(
            "策略", strategies, index=0,
            format_func=lambda x: x.upper(),
            key=f"{key_prefix}_strat",
        )
    with cols[1]:
        days = st.number_input("回测天数", 30, 500, 120, 30, key=f"{key_prefix}_days")

    trades = st.session_state.get(f"{key_prefix}_trades")
    last_run = st.session_state.get(f"{key_prefix}_last_run", "")

    if st.button("▶️ 跑回测", key=f"{key_prefix}_run", use_container_width=True):
        try:
            from backtest.engine import BacktestEngine
            from strategy.registry import create_strategy
            engine = BacktestEngine(initial_cash=1_000_000, commission=0.0003)
            strategy = create_strategy(strat)
            with st.spinner(f"回测 {symbol} {strat} {days} 天..."):
                summary = engine.run(strategy, symbol, days=days)
            trades = summary.get("trades_list", []) if summary else []
            st.session_state[f"{key_prefix}_trades"] = trades
            st.session_state[f"{key_prefix}_last_run"] = f"{symbol}/{strat}/{days}d"
            if not trades:
                st.info("回测未产生交易（可能条件未触发）")
            else:
                st.success(f"产生 {len(trades)} 笔交易")
        except Exception as exc:
            st.error(f"回测失败: {exc}")
            return []
    elif trades and last_run:
        st.caption(f"上次回测: {last_run}（{len(trades)} 笔交易）")

    return trades or []


def _load_trades_from_csv_upload(key: str) -> list:
    """读取用户上传的 CSV 转换为 trades 列表。

    CSV 必含列: date, action, price
    可选: quantity, entry_price, commission_cost
    """
    uploaded = st.file_uploader(
        "上传 trades.csv (列: date, action, price, [quantity, entry_price])",
        type=["csv"], key=key,
    )
    if uploaded is None:
        st.caption("CSV 示例: date,action,price,quantity\n2026-01-15,buy,10.5,1000")
        return []

    try:
        import pandas as pd
        df = pd.read_csv(uploaded)
        required = {"date", "action", "price"}
        if not required.issubset(df.columns):
            st.error(f"CSV 缺必填列: {required - set(df.columns)}")
            return []
        trades = []
        for _, row in df.iterrows():
            trades.append({
                "date": str(row["date"]),
                "action": str(row["action"]).strip().lower(),
                "price": float(row["price"]),
                "quantity": int(row.get("quantity", 0)) if pd.notna(row.get("quantity")) else 0,
            })
        st.success(f"加载 {len(trades)} 笔交易")
        return trades
    except Exception as exc:
        st.error(f"CSV 解析失败: {exc}")
        return []


def _manual_trade_input(key_prefix: str = "") -> list:
    """通过 data_editor 让用户手动输入买卖点。"""
    import pandas as pd
    if f"{key_prefix}_editor" not in st.session_state:
        st.session_state[f"{key_prefix}_editor"] = pd.DataFrame({
            "date": ["2026-01-15", "2026-02-20"],
            "action": ["buy", "sell"],
            "price": [10.5, 11.2],
            "quantity": [1000, 1000],
        })
    edited = st.data_editor(
        st.session_state[f"{key_prefix}_editor"],
        num_rows="dynamic",
        key=f"{key_prefix}_data_editor",
        use_container_width=True,
        column_config={
            "date": st.column_config.TextColumn("日期 (YYYY-MM-DD)"),
            "action": st.column_config.SelectboxColumn(
                "方向", options=["buy", "sell"], required=True
            ),
            "price": st.column_config.NumberColumn("价格", min_value=0.0, format="%.2f"),
            "quantity": st.column_config.NumberColumn("数量", min_value=0, step=100),
        },
    )
    st.session_state[f"{key_prefix}_editor"] = edited
    if st.button("✅ 应用", key=f"{key_prefix}_apply", use_container_width=True):
        trades = []
        for _, row in edited.iterrows():
            try:
                trades.append({
                    "date": str(row["date"]),
                    "action": str(row["action"]).strip().lower(),
                    "price": float(row["price"]),
                    "quantity": int(row.get("quantity", 0)) if pd.notna(row.get("quantity")) else 0,
                })
            except Exception:
                continue
        return trades
    return []


def _display_trades_table(trades: list):
    """显示买卖点表格。"""
    import pandas as pd
    rows = []
    for t in trades[:50]:  # 限前 50
        date_raw = t.get("date", "")
        date_str = date_raw.strftime("%Y-%m-%d") if hasattr(date_raw, "strftime") else str(date_raw)
        action = t.get("action", "")
        side_emoji = "🟢" if action == "buy" else "🔴" if action == "sell" else "⚪"
        qty = t.get("quantity", 0) or 0
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            qty = 0
        price = t.get("price", 0) or 0
        try:
            price = float(price)
        except (TypeError, ValueError):
            price = 0.0
        rows.append({
            "日期": date_str,
            "方向": f"{side_emoji} {action}",
            "价格": f"{price:.2f}",
            "数量": qty,
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    if len(trades) > 50:
        st.caption(f"（仅显示前 50 条，共 {len(trades)} 条）")


def _calc_trade_pnl(trades: list) -> tuple[int, int, int]:
    """用 buy-sell 配对算胜负。返 (wins, losses, total_pairs)。"""
    open_pos = None
    wins, losses, total = 0, 0, 0
    for t in trades:
        action = t.get("action", "")
        price = t.get("price", 0)
        if action == "buy":
            open_pos = price
        elif action == "sell" and open_pos is not None:
            pnl = price - open_pos
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
            total += 1
            open_pos = None
    return wins, losses, total


def _mark_df_with_trades(df, trades: list):
    """把 trades 按 date 对齐到 df.index，标出 buy/sell 在哪几行。"""
    if df is None or len(df) == 0 or not trades:
        return None
    df = df.copy()
    df["_trade_marker"] = None  # None / "buy" / "sell"
    df["_trade_price"] = None
    for t in trades:
        date = t.get("date")
        if hasattr(date, "strftime"):
            date_str = date.strftime("%Y-%m-%d")
        else:
            date_str = str(date)[:10]
        # 在 df 中找对应日期（取最近一根）
        try:
            import pandas as pd
            target_ts = pd.Timestamp(date_str)
            # 取 date_str 当天或之后的最近一行
            mask = df.index >= target_ts
            if mask.any():
                idx = df.index[mask][0]
                df.at[idx, "_trade_marker"] = t.get("action", "")
                df.at[idx, "_trade_price"] = t.get("price", 0)
        except Exception:
            continue
    return df


def _build_kline_with_markers(df, trades: list, symbol: str, freq: str):
    """构建带买卖点标记的 K 线图。"""
    # 在 df.index 上筛选 buy/sell 行
    buy_x, buy_y = [], []
    sell_x, sell_y = [], []
    for idx, row in df.iterrows():
        marker = row.get("_trade_marker")
        price = row.get("_trade_price")
        if marker == "buy" and price is not None and not _is_nan(price):
            buy_x.append(idx)
            buy_y.append(price)
        elif marker == "sell" and price is not None and not _is_nan(price):
            sell_x.append(idx)
            sell_y.append(price)

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name="K线",
        increasing_line_color="#d32f2f", decreasing_line_color="#388e3c",
    ))
    # 买: 绿色上箭头
    if buy_x:
        fig.add_trace(go.Scatter(
            x=buy_x, y=buy_y, mode="markers+text",
            marker=dict(symbol="triangle-up", size=14, color="#388e3c",
                        line=dict(color="white", width=1)),
            text=["B"] * len(buy_x), textposition="top center",
            textfont=dict(color="white", size=9),
            name="买入", hovertemplate="买入<br>日期: %{x}<br>价: %{y:.2f}<extra></extra>",
        ))
    # 卖: 红色下箭头
    if sell_x:
        fig.add_trace(go.Scatter(
            x=sell_x, y=sell_y, mode="markers+text",
            marker=dict(symbol="triangle-down", size=14, color="#d32f2f",
                        line=dict(color="white", width=1)),
            text=["S"] * len(sell_x), textposition="bottom center",
            textfont=dict(color="white", size=9),
            name="卖出", hovertemplate="卖出<br>日期: %{x}<br>价: %{y:.2f}<extra></extra>",
        ))
    fig.update_layout(
        height=500,
        title=f"{symbol} K线 + 买卖点 ({freq}, {len(trades)} 笔交易)",
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        legend=dict(orientation="h", y=1.02),
    )
    return fig


def _is_nan(x) -> bool:
    """判 None / NaN (不依赖 pandas)。"""
    if x is None:
        return True
    if isinstance(x, float):
        return x != x  # NaN check
    return False


def page_risk_metrics():
    """风险分析 - 从历史回测 equity_curve 算 VaR/CVaR/最大回撤/连续亏损/蒙特卡洛。

    4 块:
    1. 选数据源 (历史记录里的某条 / 上传 equity CSV)
    2. 4 风险指标卡 (VaR 95/99 + CVaR 95/99 + 最大回撤 + 连续亏损)
    3. 回撤曲线图 + 收益分布直方图 + 滚动夏普
    4. 蒙特卡洛模拟 (N 条路径 + 置信带)
    """
    import pandas as pd
    st.header("📉 风险分析")
    st.caption("从历史回测的 equity_curve 计算 VaR / CVaR / 最大回撤 / 连续亏损")

    history = st.session_state.get(HISTORY_KEY, [])

    # 1. 数据源选择
    st.subheader("1️⃣ 数据源")
    if not history:
        st.info("📭 暂无回测历史。先到 📊 单策略回测或 🔀 多策略组合跑一次。")
        return

    # 列出所有有 equity_curve 的历史记录
    candidates = [
        (i, h) for i, h in enumerate(history)
        if h.get("equity_curve") and len(h["equity_curve"]) > 1
    ]
    if not candidates:
        st.warning("历史记录里没有 equity_curve 字段。请用带 summary 的回测结果。")
        return

    options_labels = [
        f"#{i} — {h.get('symbol', '?')} {h.get('strategy', '?').upper()} "
        f"({h.get('profit_pct', 0):+.2f}%)"
        for i, h in candidates
    ]
    sel_idx = st.selectbox(
        "选历史记录", range(len(candidates)),
        format_func=lambda i: options_labels[i],
        key="risk_history_sel",
    )
    rec = candidates[sel_idx][1]
    equity = rec["equity_curve"]

    # 2. 风险指标卡
    st.subheader("2️⃣ 核心风险指标")
    var95 = value_at_risk(equity, 0.95)
    var99 = value_at_risk(equity, 0.99)
    cvar95 = conditional_var(equity, 0.95)
    cvar99 = conditional_var(equity, 0.99)
    mdd = max_drawdown(equity)
    mcl = max_consecutive_losses(equity)

    cols = st.columns(6)
    cols[0].metric("VaR 95%", f"{var95 * 100:.2f}%", help="95% 置信度下最大单日损失",
                    delta=f"损失" if var95 > 0 else None, delta_color="inverse")
    cols[1].metric("VaR 99%", f"{var99 * 100:.2f}%", help="99% 置信度下最大单日损失",
                    delta="损失", delta_color="inverse")
    cols[2].metric("CVaR 95%", f"{cvar95 * 100:.2f}%",
                    help="VaR 之外最差 5% 的平均损失（更严格）", delta_color="inverse")
    cols[3].metric("CVaR 99%", f"{cvar99 * 100:.2f}%",
                    help="VaR 之外最差 1% 的平均损失", delta_color="inverse")
    cols[4].metric("最大回撤", mdd["max_drawdown_pct"],
                    help=f"从 peak{trough_to_text(mdd['peak_idx'])}到 trough{trough_to_text(mdd['trough_idx'])}",
                    delta_color="inverse")
    cols[5].metric("最大连续亏损", f"{mcl['max_count']} 天",
                    help=f"累计 {mcl['max_loss_pct']}")

    st.caption(
        f"📍 回撤期: peak {mdd['drawdown_duration']} 天 → "
        f"recovery: {mdd['recovery_duration'] if mdd['recovery_duration'] is not None else '❌ 未恢复'} 天"
    )

    # 3. 图表：回撤曲线 + 收益分布 + 滚动夏普
    st.subheader("3️⃣ 风险可视化")
    _render_risk_charts(equity, rec)

    # 4. 蒙特卡洛
    st.subheader("4️⃣ 蒙特卡洛模拟")
    _render_monte_carlo(equity)


def trough_to_text(idx: int) -> str:
    """trough 索引转成 #N 文字。"""
    return f"#{idx}" if idx >= 0 else "?"


def _render_risk_charts(equity, rec):
    """画回撤曲线 + 收益分布 + 滚动夏普 3 张图。"""
    import pandas as pd
    if isinstance(equity[0], dict):
        values = [e["value"] for e in equity]
    else:
        values = list(equity)

    # 1. 权益曲线 + 回撤 (双面板)
    peak = values[0]
    drawdowns = []
    for v in values:
        if v > peak:
            peak = v
        dd = (v - peak) / peak if peak > 0 else 0
        drawdowns.append(dd)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.6, 0.4], vertical_spacing=0.05,
    )
    fig.add_trace(go.Scatter(
        x=list(range(len(values))), y=values, name="权益",
        line=dict(color="#29B6F6", width=1.5),
        fill="tozeroy", fillcolor="rgba(41, 182, 246, 0.1)",
    ), row=1, col=1)
    # 回撤用 area 染红
    fig.add_trace(go.Scatter(
        x=list(range(len(values))), y=[d * 100 for d in drawdowns], name="回撤%",
        line=dict(color="#d32f2f", width=1.2),
        fill="tozeroy", fillcolor="rgba(211, 47, 47, 0.2)",
    ), row=2, col=1)
    fig.update_layout(
        height=450,
        title=f"权益曲线 & 回撤 (峰值 {max(values):,.0f})",
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="权益", row=1, col=1)
    fig.update_yaxes(title_text="回撤 %", row=2, col=1)
    st.plotly_chart(fig, use_container_width=True)

    # 2. 收益分布直方图 + 正态叠加
    rets = [(values[i] - values[i - 1]) / values[i - 1]
            for i in range(1, len(values)) if values[i - 1] > 0]
    if rets:
        import statistics
        mu = statistics.mean(rets)
        sigma = statistics.stdev(rets) if len(rets) > 1 else 0
        fig2 = go.Figure()
        fig2.add_trace(go.Histogram(
            x=[r * 100 for r in rets], name="收益分布",
            nbinsx=30, marker_color="#66BB6A", opacity=0.7,
        ))
        # 正态叠加
        if sigma > 0:
            import numpy as np
            x = np.linspace(mu * 100 - 4 * sigma * 100, mu * 100 + 4 * sigma * 100, 100)
            from math import exp, sqrt, pi
            y_norm = [exp(-((xi / 100 - mu) ** 2) / (2 * sigma ** 2)) /
                      (sigma * sqrt(2 * pi)) * len(rets) *
                      (max(rets) - min(rets)) / 30
                      for xi in x]
            fig2.add_trace(go.Scatter(
                x=x, y=y_norm, name="正态拟合",
                line=dict(color="#d32f2f", width=2, dash="dash"),
            ))
        fig2.update_layout(
            title=f"日收益分布 (μ={mu * 100:.3f}%, σ={sigma * 100:.2f}%)",
            xaxis_title="日收益 %", yaxis_title="频次",
            height=350,
        )
        st.plotly_chart(fig2, use_container_width=True)

    # 3. 滚动夏普 + 滚动波动
    if len(values) > 20:
        col1, col2 = st.columns(2)
        sharpe = rolling_sharpe(equity, window=20)
        vol = rolling_volatility(equity, window=20)
        with col1:
            fig3 = go.Figure()
            fig3.add_trace(go.Scatter(
                x=list(range(len(sharpe))), y=sharpe, name="滚动夏普(20)",
                line=dict(color="#FFA726", width=1.5),
            ))
            fig3.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
            fig3.update_layout(
                title="滚动夏普 (20天, 年化)",
                height=300, hovermode="x unified",
            )
            st.plotly_chart(fig3, use_container_width=True)
        with col2:
            fig4 = go.Figure()
            fig4.add_trace(go.Scatter(
                x=list(range(len(vol))), y=vol, name="滚动波动率(20)",
                line=dict(color="#AB47BC", width=1.5),
                fill="tozeroy", fillcolor="rgba(171, 71, 188, 0.1)",
            ))
            fig4.update_layout(
                title="滚动波动率 (20天, 年化 %)",
                height=300, hovermode="x unified",
            )
            st.plotly_chart(fig4, use_container_width=True)


def _render_monte_carlo(equity):
    """蒙特卡洛模拟 + 置信带图。"""
    col1, col2, col3 = st.columns(3)
    with col1:
        n_sims = st.number_input("模拟次数", 100, 5000, 1000, 100, key="mc_n")
    with col2:
        n_days = st.number_input("预测天数", 30, 500, 252, 30, key="mc_d")
    with col3:
        seed = st.number_input("随机种子", 0, 99999, 42, 1, key="mc_seed")

    if st.button("🎲 运行模拟", key="mc_run", use_container_width=True, type="primary"):
        with st.spinner(f"生成 {n_sims} 条 {n_days} 天路径..."):
            paths = monte_carlo_simulation(equity, n_sims=n_sims, n_days=n_days, seed=seed)
        if not paths:
            st.error("equity_curve 数据不足")
            return
        st.session_state["mc_paths"] = paths
        st.success(f"生成 {len(paths)} 条路径")

    paths = st.session_state.get("mc_paths")
    if not paths:
        st.caption("点上面按钮开始模拟")
        return

    # 5/50/95 分位 + 中位数 + 全部
    import numpy as np
    arr = np.array(paths)  # (n_sims, n_days+1)
    p5 = np.percentile(arr, 5, axis=0)
    p50 = np.percentile(arr, 50, axis=0)
    p95 = np.percentile(arr, 95, axis=0)
    mean = np.mean(arr, axis=0)

    fig = go.Figure()
    # 5%-95% 置信带
    fig.add_trace(go.Scatter(
        x=list(range(len(p5))), y=p95, name="95% 分位",
        line=dict(color="rgba(0,0,0,0)"),
    ))
    fig.add_trace(go.Scatter(
        x=list(range(len(p5))), y=p5, name="5% 分位",
        line=dict(color="rgba(0,0,0,0)"),
        fill="tonexty", fillcolor="rgba(102, 187, 90, 0.2)",
    ))
    # 中位数
    fig.add_trace(go.Scatter(
        x=list(range(len(p50))), y=p50, name="中位数",
        line=dict(color="#388e3c", width=2),
    ))
    # 均值
    fig.add_trace(go.Scatter(
        x=list(range(len(mean))), y=mean, name="均值",
        line=dict(color="#FFA726", width=1.5, dash="dash"),
    ))
    # 抽 50 条随机路径
    import random
    rng = random.Random(42)
    sample = rng.sample(range(len(paths)), min(50, len(paths)))
    for i in sample:
        fig.add_trace(go.Scatter(
            x=list(range(len(paths[i]))), y=paths[i],
            mode="lines", showlegend=False,
            line=dict(color="rgba(171, 71, 188, 0.1)", width=0.5),
        ))
    fig.update_layout(
        title=f"蒙特卡洛 {n_sims} 次模拟 × {n_days} 天",
        xaxis_title="天数", yaxis_title="权益",
        height=500, hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)

    # 终值分布
    final_values = arr[:, -1]
    last_real = paths[0][0] if paths else 0
    final_p5, final_p50, final_p95 = np.percentile(final_values, [5, 50, 95])
    cols = st.columns(4)
    cols[0].metric("起始权益", f"{last_real:,.0f}")
    cols[1].metric("终值中位数", f"{final_p50:,.0f}",
                   delta=f"{(final_p50 / last_real - 1) * 100:+.2f}%" if last_real > 0 else None)
    cols[2].metric("5% 最差情况", f"{final_p5:,.0f}",
                   delta=f"{(final_p5 / last_real - 1) * 100:+.2f}%" if last_real > 0 else None,
                   delta_color="inverse")
    cols[3].metric("5% 最好情况", f"{final_p95:,.0f}",
                   delta=f"{(final_p95 / last_real - 1) * 100:+.2f}%" if last_real > 0 else None)


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


def page_watchlist():
    """自选股票管理 — 增/删/启用/标签/导入导出。"""
    from utils.watchlist import (
        load_watchlist, save_watchlist, add_stock, remove_stock,
        update_stock, export_csv, import_csv, is_valid_symbol, _normalize_symbol,
    )

    st.header("⭐ 自选股票")
    st.caption(f"持久化到: `{_WATCHLIST_PATH}`")

    stocks = load_watchlist()

    # ============== 添加新股票 ==============
    st.subheader("➕ 添加股票")
    with st.form("wl_add"):
        col1, col2, col3 = st.columns([2, 2, 3])
        with col1:
            new_sym = st.text_input("代码", placeholder="000001.SZ 或 sz000001")
        with col2:
            new_name = st.text_input("名称 (可选)", placeholder="平安银行")
        with col3:
            new_tags = st.text_input("标签 (逗号分隔, 可选)", placeholder="银行,核心")
        submitted = st.form_submit_button("➕ 添加", use_container_width=True)
        if submitted:
            norm = _normalize_symbol(new_sym)
            if not norm or not is_valid_symbol(norm):
                st.error(f"代码无效: {new_sym!r} (规范化后={norm!r})")
            else:
                tags_list = [t.strip() for t in new_tags.split(",") if t.strip()]
                result = add_stock(norm, new_name, tags_list)
                if result is None:
                    # 已存在
                    update_stock(norm, name=new_name, tags=tags_list)
                    st.success(f"已存在 — 已更新 {norm} 的名称/标签")
                else:
                    st.success(f"已添加 {result['symbol']} ({result['name']})")
                st.rerun()

    st.markdown("---")

    # ============== 当前列表 ==============
    st.subheader(f"📋 我的自选 ({len(stocks)} 只)")

    if not stocks:
        st.info("自选为空，先到上面添加几个股票吧")
        return

    # 表格
    rows = []
    for s in stocks:
        rows.append({
            "代码": s["symbol"],
            "名称": s.get("name", "") or "—",
            "标签": ", ".join(s.get("tags", [])) or "—",
            "启用": "✅" if s.get("enabled", True) else "⛔",
            "添加时间": s.get("added", ""),
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown("---")

    # ============== 编辑/删除 ==============
    st.subheader("操作")
    sym_options = [s["symbol"] for s in stocks]
    sym_labels = [
        f"{s['symbol']} — {s.get('name', '') or '无名称'}" for s in stocks
    ]
    col1, col2, col3 = st.columns(3)

    with col1:
        st.caption("启用/禁用")
        sym_map = dict(zip(sym_options, sym_labels))
        sel_e = st.selectbox(
            "选择股票", options=sym_options,
            format_func=lambda x: sym_map.get(x) or x,
            key="wl_edit_sel", label_visibility="collapsed",
        )
        cur = next((s for s in stocks if s["symbol"] == sel_e), None)
        if cur:
            new_name = st.text_input("名称", value=cur.get("name", ""), key="wl_edit_name")
            new_tags = st.text_input(
                "标签", value=",".join(cur.get("tags", [])),
                key="wl_edit_tags",
            )
            new_enabled = st.checkbox(
                "启用", value=cur.get("enabled", True), key="wl_edit_enabled",
            )
            if st.button("💾 保存修改", key="wl_save", use_container_width=True):
                tags_list = [t.strip() for t in new_tags.split(",") if t.strip()]
                update_stock(
                    sel_e, name=new_name, tags=tags_list, enabled=new_enabled,
                )
                st.success(f"已保存 {sel_e}")
                st.rerun()

    with col2:
        st.caption("删除单条")
        sel_d = st.selectbox(
            "选择股票", options=sym_options,
            format_func=lambda x: sym_map.get(x) or x,
            key="wl_del_sel", label_visibility="collapsed",
        )
        if st.button("🗑️ 删除", key="wl_delete", use_container_width=True, type="secondary"):
            if sel_d:
                remove_stock(sel_d)
                st.success(f"已删除 {sel_d}")
                st.rerun()

    with col3:
        st.caption("批量操作")
        st.write("")  # 占位对齐
        if st.button("🗑️ 清空所有", key="wl_clear", use_container_width=True, type="secondary"):
            save_watchlist([])
            st.success("已清空")
            st.rerun()

    st.markdown("---")

    # ============== 导入导出 ==============
    st.subheader("📥📤 导入 / 导出")
    col1, col2 = st.columns(2)
    with col1:
        st.caption("导出 CSV (可邮件分享)")
        if st.button("📤 导出", key="wl_export", use_container_width=True):
            csv_content = export_csv()
            st.download_button(
                "下载 CSV",
                data=csv_content,
                file_name=f"watchlist_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
                use_container_width=True,
                key="wl_download",
            )
        # 示例格式
        st.code(
            "symbol,name,tags,enabled,added\n"
            "000001.SZ,平安银行,银行;核心,True,2026-06-03 10:00",
            language="csv",
        )

    with col2:
        st.caption("导入 CSV (粘贴或上传)")
        uploaded = st.file_uploader("上传 CSV", type=["csv"], key="wl_upload")
        if uploaded is not None:
            try:
                content = uploaded.read().decode("utf-8")
                n = import_csv(content)
                st.success(f"导入 {n} 个新股票")
                st.rerun()
            except Exception as exc:
                st.error(f"导入失败: {exc}")
        pasted = st.text_area("或粘贴 CSV 内容", key="wl_paste", height=100)
        if st.button("📥 从粘贴导入", key="wl_paste_btn", use_container_width=True):
            if pasted.strip():
                n = import_csv(pasted)
                st.success(f"导入 {n} 个新股票")
                st.rerun()

    # ============== 历史 K 线 (v22 新增) ==============
    # 注意：page_watchlist 在 line 1624 已 return（空自选），所以此处 stocks 必有
    enabled = [s for s in stocks if s.get("enabled", True)]
    if enabled:
        st.markdown("---")
        st.subheader("📈 单只 K 线")
        col1, col2 = st.columns([3, 1])
        with col1:
            sym_options = [s["symbol"] for s in enabled]
            sym_labels = [
                f"{s['symbol']} — {s.get('name', '') or '无'}" for s in enabled
            ]
            default_idx = 0
            cur = st.session_state.get("global_symbol", "")
            if cur in sym_options:
                default_idx = sym_options.index(cur)
            kline_sym = st.selectbox(
                "选股票", options=sym_options,
                index=default_idx,
                format_func=lambda x: dict(zip(sym_options, sym_labels)).get(x) or x,
                key="wl_kline_sel",
                label_visibility="collapsed",
            )
        with col2:
            if kline_sym and st.button("📊 跳到详情页", key="wl_kline_goto",
                                       use_container_width=True,
                                       help="切到实时行情页看更多详情"):
                st.session_state["global_symbol"] = kline_sym
                st.info(f"已设置 global_symbol={kline_sym}，切到 📡 实时行情 → 🔍 详情")
        if kline_sym:
            _render_kline_section(kline_sym, key_prefix="wl_kl")

    # ============== 快速批量回测 (v20 新增) ==============
    _render_batch_backtest_section(stocks)


def _render_batch_backtest_section(stocks: list[dict]):
    """在自选页底部渲染"快速批量回测"section。"""
    from utils.watchlist import batch_backtest, rank_batch_results

    st.markdown("---")
    st.subheader("🚀 快速批量回测")
    st.caption("选几只自选股票 + 一个策略，一键并发回测（结果自动入历史）")

    if not stocks:
        st.info("自选为空，先到上面添加股票")
        return

    enabled = [s for s in stocks if s.get("enabled", True)]
    if not enabled:
        st.info("没有启用的自选股票")
        return

    # 参数表单
    with st.form("wl_batch_bt"):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            # 多选 enabled 自选
            sym_options = [s["symbol"] for s in enabled]
            sym_labels = [
                f"{s['symbol']} — {s.get('name', '') or '无'}" for s in enabled
            ]
            sel_syms = st.multiselect(
                "选择股票", options=sym_options,
                default=sym_options[: min(3, len(sym_options))],
                format_func=lambda x: dict(zip(sym_options, sym_labels)).get(x) or x,
            )
        with col2:
            strat = st.selectbox(
                "策略", options=list_strategies(),
                index=list_strategies().index("sma") if "sma" in list_strategies() else 0,
                format_func=lambda x: x.upper(),
            )
        with col3:
            days = st.number_input("回测天数", 30, 500, 120, 30)
        with col4:
            position_size = st.number_input("仓位", 0.1, 1.0, 1.0, 0.1, format="%.1f")

        col5, col6 = st.columns(2)
        with col5:
            stop_loss = st.number_input("止损", 0.0, 0.5, 0.0, 0.01, format="%.2f")
        with col6:
            take_profit = st.number_input("止盈", 0.0, 1.0, 0.0, 0.05, format="%.2f")

        submitted = st.form_submit_button("🚀 开始批量回测", use_container_width=True, type="primary")

    if not submitted:
        return

    if not sel_syms:
        st.warning("请至少选一只股票")
        return

    progress = st.progress(0)
    status = st.empty()

    def _engine_factory():
        return BacktestEngine(
            initial_cash=CONFIG.get("initial_cash", 1_000_000),
            commission=CONFIG.get("backtest", {}).get("commission", 0.0003),
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size=position_size,
        )

    # 进度条模拟（实际并发，不可预知每个完成时间）
    status.text(f"⏳ 开始并发回测 {len(sel_syms)} 只股票...")

    try:
        results = batch_backtest(
            symbols=sel_syms,
            strategy_name=strat,
            days=days,
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size=position_size,
            engine_factory=_engine_factory,
        )
    except Exception as exc:
        st.error(f"批量回测失败: {exc}")
        return

    progress.progress(100)
    status.text("✅ 完成")

    # 排序
    ranked = rank_batch_results(results, metric="profit_pct", descending=True)

    # 入历史
    for r in ranked:
        if "summary" in r:
            _history_add(r["summary"], mode="batch_backtest")

    # 概览
    successful = [r for r in ranked if "summary" in r]
    failed = [r for r in ranked if "error" in r]
    avg_p = (
        sum(r["summary"].get("profit_pct", 0) for r in successful) / len(successful)
        if successful else 0
    )
    best = successful[0] if successful else None
    worst = successful[-1] if successful else None

    st.markdown("#### 📊 概览")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("完成", f"{len(successful)}/{len(ranked)}")
    c2.metric("失败", len(failed))
    c3.metric("平均收益", f"{avg_p:+.2f}%")
    c4.metric(
        "最佳",
        f"{best['summary']['profit_pct']:+.2f}%" if best else "—",
        help=best["symbol"] if best else None,
    )

    st.markdown("---")
    st.markdown("#### 🏆 排行榜")

    # 表格
    rows = []
    for r in ranked:
        if "error" in r:
            rows.append({
                "排名": r["rank"],
                "代码": r["symbol"],
                "状态": f"❌ {r['error']}",
            })
        else:
            s = r["summary"]
            rows.append({
                "排名": r["rank"],
                "代码": r["symbol"],
                "收益率%": f"{s.get('profit_pct', 0):+.2f}",
                "夏普": f"{s.get('sharpe_ratio', 0):.2f}",
                "回撤%": f"{s.get('max_drawdown_pct', 0):.2f}",
                "胜率%": f"{s.get('win_rate', 0):.1f}",
                "交易数": s.get("trades", 0),
            })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # 散点：收益 vs 回撤
    if successful and len(successful) >= 2:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=[r["summary"].get("max_drawdown_pct", 0) for r in successful],
            y=[r["summary"].get("profit_pct", 0) for r in successful],
            mode="markers+text",
            text=[r["symbol"] for r in successful],
            textposition="top center",
            marker=dict(size=14, color="#58a6ff"),
        ))
        fig.update_layout(
            xaxis_title="最大回撤 %",
            yaxis_title="收益率 %",
            title="收益 vs 回撤",
            height=400,
        )
        st.plotly_chart(fig, use_container_width=True)

    if failed:
        with st.expander(f"❌ 失败明细 ({len(failed)} 只)", expanded=False):
            for r in failed:
                st.text(f"  {r['symbol']}: {r['error']}")


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
    PAGE_WATCHLIST: page_watchlist,
    PAGE_RISK: page_risk_metrics,
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
