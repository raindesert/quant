"""A股量化交易系统 - Streamlit图形化界面"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yaml

from strategy.registry import STRATEGY_REGISTRY, create_strategy, get_strategy_class, list_strategies
from backtest.engine import BacktestEngine
from backtest.optimizer import StrategyOptimizer, DEFAULT_GRIDS, OPTIMIZE_METRICS
from backtest.walk_forward import WalkForwardValidator
from data.fetcher import DataFetcher
from data.processor import DataProcessor
from risk.manager import RiskManager


def load_config():
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


CONFIG = load_config()

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


def render_sidebar():
    with st.sidebar:
        st.title("📈 A股量化交易系统")
        st.markdown("---")
        page = st.radio(
            "功能导航",
            ["📊 单策略回测", "⚔️ 策略对比", "🔧 参数优化", "🔄 Walk-Forward", "📡 实时行情"],
            label_visibility="collapsed",
        )
        st.markdown("---")
        st.caption("v4.0 | A股量化交易系统")
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
        colorscale=[["#388e3c", "#fff"], ["#fff", "#d32f2f"]],
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
        symbol = st.text_input("股票代码", value=CONFIG.get("default_symbol", "000001.SZ"))
        strategy_name = st.selectbox("策略", list_strategies(), format_func=lambda x: x.upper())
        days = st.slider("回测天数", 30, 500, CONFIG.get("backtest", {}).get("days", 250))

        st.markdown("#### 交易参数")
        col1, col2 = st.columns(2)
        with col1:
            stop_loss = st.number_input("止损比例", 0.0, 0.5, 0.0, 0.01, format="%.2f")
            position_size = st.number_input("仓位比例", 0.1, 1.0, 1.0, 0.1, format="%.1f")
        with col2:
            take_profit = st.number_input("止盈比例", 0.0, 1.0, 0.0, 0.05, format="%.2f")
            slippage = st.number_input("滑点(%)", 0.0, 1.0, 0.1, 0.01, format="%.2f")

        st.markdown("#### 风控设置")
        risk_enabled = st.checkbox("启用风控", value=CONFIG.get("risk", {}).get("enabled", True))
        if risk_enabled:
            max_position_pct = st.slider("单股仓位上限", 0.05, 0.5, 0.25, 0.05)
            max_positions = st.slider("最大持仓数", 1, 20, 10)
            max_drawdown_pct = st.slider("最大回撤熔断", 0.05, 0.5, 0.2, 0.05)
            max_daily_loss_pct = st.slider("日亏损上限", 0.01, 0.1, 0.03, 0.01)
            max_stock_loss_pct = st.slider("个股亏损上限", 0.05, 0.3, 0.1, 0.05)
        else:
            max_position_pct = 0.25
            max_positions = 10
            max_drawdown_pct = 0.2
            max_daily_loss_pct = 0.03
            max_stock_loss_pct = 0.1

    if st.button("🚀 开始回测", type="primary", use_container_width=True):
        with st.spinner(f"正在回测 {symbol} ..."):
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
            summary = engine.run(strategy, symbol, days=days)

        if summary is None:
            st.error(f"无法获取 {symbol} 的数据，请检查股票代码或网络连接")
            return

        st.session_state["last_summary"] = summary
        st.session_state["last_symbol"] = symbol
        st.session_state["last_strategy"] = strategy_name

    if "last_summary" in st.session_state:
        summary = st.session_state["last_summary"]
        symbol = st.session_state.get("last_symbol", "")
        strategy_name = st.session_state.get("last_strategy", "")

        st.subheader(f"{symbol} | {strategy_name.upper()} 策略回测结果")
        display_summary_metrics(summary)

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
        symbol = st.text_input("股票代码", value="000001.SZ", key="opt_symbol")
        strategy_name = st.selectbox("策略", list_strategies(), key="opt_strategy")
        days = st.slider("回测天数", 60, 500, 250, key="opt_days")
        metric = st.selectbox("优化指标", list(OPTIMIZE_METRICS.keys()),
                              format_func=lambda x: OPTIMIZE_METRICS[x])

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

    if st.button("🚀 开始优化", type="primary", use_container_width=True):
        optimizer = StrategyOptimizer(
            strategy_name=strategy_name,
            symbol=symbol,
            days=days,
            metric=metric,
        )

        with st.spinner("优化中，请稍候..."):
            result = optimizer.optimize(custom_grid)

        st.session_state["opt_result"] = result

    if "opt_result" not in st.session_state:
        return

    result = st.session_state["opt_result"]

    st.subheader("优化结果")

    best = result.get("best_params", {})
    best_score = result.get("best_score", 0)
    st.success(f"最优参数: {best} | {OPTIMIZE_METRICS.get(metric, metric)}: {best_score:.4f}")

    all_results = result.get("all_results", [])
    if all_results:
        rows = []
        for r in all_results:
            row = {"参数": str(r.get("params", {}))}
            for m in ["profit_pct", "sharpe_ratio", "max_drawdown_pct", "win_rate", "trades"]:
                row[OPTIMIZE_METRICS.get(m, m)] = r.get(m, 0)
            rows.append(row)

        df = pd.DataFrame(rows)
        sort_col = OPTIMIZE_METRICS.get(metric, metric)
        if sort_col in df.columns:
            ascending = metric == "max_drawdown_pct"
            df = df.sort_values(sort_col, ascending=ascending).reset_index(drop=True)
        st.dataframe(df, use_container_width=True, height=400)


def page_walk_forward():
    st.header("🔄 Walk-Forward 验证")

    with st.sidebar:
        st.markdown("### WF 参数")
        symbol = st.text_input("股票代码", value="000001.SZ", key="wf_symbol")
        strategy_name = st.selectbox("策略", list_strategies(), key="wf_strategy")
        train_days = st.slider("训练期(天)", 60, 250, 120, key="wf_train")
        test_days = st.slider("测试期(天)", 20, 120, 60, key="wf_test")
        step_days = st.slider("步进(天)", 20, 120, 60, key="wf_step")

    if st.button("🚀 开始验证", type="primary", use_container_width=True):
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


def main():
    page = render_sidebar()

    if "📊" in page:
        page_backtest()
    elif "⚔️" in page:
        page_strategy_comparison()
    elif "🔧" in page:
        page_optimize()
    elif "🔄" in page:
        page_walk_forward()
    elif "📡" in page:
        page_realtime()


if __name__ == "__main__":
    main()
