"""量化交易系统主入口。"""
import argparse
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import yaml

from backtest.engine import BacktestEngine
from backtest.output import (
    export_summary_json,
    export_trades_csv,
    plot_drawdown_curve,
    plot_equity_curve,
    plot_monthly_heatmap,
    plot_strategy_comparison,
)
from backtest.portfolio import PortfolioBacktestEngine
from backtest.optimizer import StrategyOptimizer, DEFAULT_GRIDS, OPTIMIZE_METRICS
from backtest.walk_forward import WalkForwardValidator
from broker.simulator import SimulatorBroker
from data.fetcher import DataFetcher
from monitor.realtime import RealtimeMonitor
from risk.manager import RiskManager
from strategy.registry import STRATEGY_REGISTRY, create_strategy, get_strategy_class, list_strategies
from utils.logger import setup_logger


def _run_single_backtest(args_tuple):
    """独立函数，用于并发回测（必须是模块级以支持 pickle）。"""
    strategy_name, symbol, days, initial_cash, commission, stop_loss, take_profit, position_size, start_date, end_date, verbose, slippage, slippage_type, enforce_t1, check_limit, risk_params = args_tuple
    strategy_cls = get_strategy_class(strategy_name)
    if strategy_cls is None:
        strategy_cls = get_strategy_class("sma")
    strategy = strategy_cls()

    risk_manager = None
    if risk_params and risk_params.get("enabled"):
        risk_manager = RiskManager(
            max_position_pct=risk_params.get("max_position_pct", 0.25),
            max_positions=risk_params.get("max_positions", 10),
            max_drawdown_pct=risk_params.get("max_drawdown_pct", 0.20),
            max_daily_loss_pct=risk_params.get("max_daily_loss_pct", 0.03),
            max_stock_loss_pct=risk_params.get("max_stock_loss_pct", 0.10),
            enabled=True,
        )

    engine = BacktestEngine(
        initial_cash=initial_cash,
        commission=commission,
        verbose=False,
        stop_loss=stop_loss,
        take_profit=take_profit,
        position_size=position_size,
        slippage=slippage,
        slippage_type=slippage_type,
        enforce_t_plus_1=enforce_t1,
        check_limit=check_limit,
        risk_manager=risk_manager,
    )
    summary = engine.run(
        strategy,
        symbol,
        days=days,
        start_date=start_date,
        end_date=end_date,
    )
    if summary is None:
        return None
    summary["strategy"] = strategy_name
    return summary


BASE_DIR = Path(__file__).parent
DEFAULT_STRATEGY = "sma"


def get_strategy(strategy_name: str, symbol: str = "", load_params: bool = False):
    """根据名称创建策略实例，支持加载已保存的参数。"""
    strategy_cls = get_strategy_class(strategy_name)
    if strategy_cls is None:
        print(f"未知策略: {strategy_name}，将使用默认策略 SMA")
        strategy_cls = get_strategy_class("sma")

    if load_params and symbol:
        from strategy.params import load_params as _load_params
        saved = _load_params(strategy_name, symbol=symbol)
        if saved:
            try:
                return strategy_cls(**saved)
            except Exception:
                pass

    return strategy_cls()


def build_risk_manager(args, config) -> RiskManager | None:
    """根据命令行参数和配置创建风控管理器。"""
    risk_config = config.get("risk", {})
    enabled = risk_config.get("enabled", True)
    if hasattr(args, "no_risk") and args.no_risk:
        enabled = False
    if hasattr(args, "risk_enabled") and args.risk_enabled:
        enabled = True

    if not enabled:
        return None

    return RiskManager(
        max_position_pct=args.max_position_pct if hasattr(args, "max_position_pct") and args.max_position_pct is not None else risk_config.get("max_position_pct", 0.25),
        max_positions=args.risk_max_positions if hasattr(args, "risk_max_positions") and args.risk_max_positions is not None else risk_config.get("max_positions", 10),
        max_drawdown_pct=args.max_drawdown_pct if hasattr(args, "max_drawdown_pct") and args.max_drawdown_pct is not None else risk_config.get("max_drawdown_pct", 0.20),
        max_daily_loss_pct=args.max_daily_loss_pct if hasattr(args, "max_daily_loss_pct") and args.max_daily_loss_pct is not None else risk_config.get("max_daily_loss_pct", 0.03),
        max_stock_loss_pct=args.max_stock_loss_pct if hasattr(args, "max_stock_loss_pct") and args.max_stock_loss_pct is not None else risk_config.get("max_stock_loss_pct", 0.10),
        enabled=True,
    )


def load_config():
    """加载配置文件。"""
    config_path = BASE_DIR / "config" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def load_symbols(file_path: Path) -> list[str]:
    """从文件加载股票代码列表。"""
    symbols = []
    with open(file_path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line and not line.startswith("#"):
                symbols.append(line)
    return symbols


def resolve_symbols(args, config, logger) -> list[str]:
    """解析命令行中的单个或批量股票代码。"""
    if args.symbols:
        symbols_file = BASE_DIR / args.symbols
        if not symbols_file.exists():
            logger.error("股票代码文件不存在: %s", args.symbols)
            return []

        symbols = load_symbols(symbols_file)
        logger.info("从文件加载 %s 个股票代码", len(symbols))
        return symbols

    if args.symbol:
        return [args.symbol]

    return [config.get("default_symbol", "000001.SZ")]


def print_batch_summary(results: list[dict], initial_cash: float):
    """打印批量回测汇总。"""
    if len(results) <= 1:
        return

    print("\n===== 批量回测汇总 =====")
    print(f"{'股票':<15s} {'收益率':>10s} {'基准':>10s} {'Alpha':>10s} {'最大回撤':>10s} {'夏普':>8s} {'交易':>6s}")
    print("-" * 80)
    for result in results:
        profit_pct = (result["final_value"] - initial_cash) / initial_cash * 100
        benchmark_return = result.get("benchmark_return", 0)
        alpha = result.get("alpha", 0)
        max_dd = result.get("max_drawdown_pct", 0)
        sharpe = result.get("sharpe_ratio", 0)
        trades = result.get("trades", 0)
        print(f"{result['symbol']:<15s} {profit_pct:+10.2f}% {benchmark_return:+10.2f}% {alpha:+10.2f}% {max_dd:>9.2f}% {sharpe:>8.2f} {trades:>6d}")


def export_results(args, summary: dict, symbol: str):
    """根据命令行参数导出结果。"""
    equity_curve = summary.get("equity_curve", [])
    benchmark_curve = summary.get("benchmark_curve", [])
    trades = summary.get("trades_list", [])

    if args.output_json:
        export_summary_json(summary, equity_curve, benchmark_curve, args.output_json)

    if args.output_csv:
        export_trades_csv(trades, args.output_csv)

    if args.chart:
        chart_dir = Path(args.chart)
        equity_path = chart_dir / f"{symbol}_equity.png"
        drawdown_path = chart_dir / f"{symbol}_drawdown.png"
        heatmap_path = chart_dir / f"{symbol}_monthly.png"
        plot_equity_curve(equity_curve, benchmark_curve, symbol, equity_path, summary=summary)
        plot_drawdown_curve(equity_curve, symbol, drawdown_path)
        monthly = summary.get("monthly_returns", {})
        if monthly:
            plot_monthly_heatmap(monthly, symbol, heatmap_path)



def run_optimize(args, config, logger):
    """运行策略参数 Grid Search 优化。"""
    backtest_config = config.get("backtest", {})
    days = args.days or backtest_config.get("days", 250)
    commission = backtest_config.get("commission", 0.0003)
    stop_loss = args.stop_loss if args.stop_loss is not None else backtest_config.get("stop_loss", 0.0)
    take_profit = args.take_profit if args.take_profit is not None else backtest_config.get("take_profit", 0.0)
    position_size = args.position_size if args.position_size is not None else backtest_config.get("position_size", 1.0)
    slippage = args.slippage if args.slippage is not None else backtest_config.get("slippage", 0.001)
    slippage_type = args.slippage_type if args.slippage_type else backtest_config.get("slippage_type", "percent")
    enforce_t1 = args.enforce_t1 if hasattr(args, "enforce_t1") else backtest_config.get("enforce_t_plus_1", True)
    check_limit = args.check_limit if hasattr(args, "check_limit") else backtest_config.get("check_limit", True)
    start_date = backtest_config.get("start_date")
    end_date = backtest_config.get("end_date")

    strategy_name = args.strategy or DEFAULT_STRATEGY
    symbol = args.symbol or config.get("default_symbol", "000001.SZ")
    metric = args.optimize_metric or "sharpe_ratio"
    workers = args.optimize_workers or 4

    def _auto_type(v):
        try:
            return __import__("ast").literal_eval(v)
        except Exception:
            return v.strip()

    if args.param:
        param_grid = {}
        for item in args.param:
            if "=" not in item:
                continue
            key, vals = item.split("=", 1)
            param_grid[key.strip()] = [_auto_type(v) for v in vals.split(",")]
    else:
        param_grid = DEFAULT_GRIDS.get(strategy_name.lower(), {})

    if not param_grid:
        logger.error("无法确定参数网格，请使用 --param 指定，如: --param fast=5,10 --param slow=30,60")
        return

    risk_manager = build_risk_manager(args, config)
    risk_params = None
    if risk_manager is not None:
        risk_params = risk_manager.get_status()

    optimizer = StrategyOptimizer(
        strategy_name=strategy_name,
        symbol=symbol,
        days=days,
        commission=commission,
        stop_loss=stop_loss,
        take_profit=take_profit,
        position_size=position_size,
        start_date=start_date,
        end_date=end_date,
        metric=metric,
        workers=workers,
        risk_params=risk_params,
    )

    result = optimizer.optimize(param_grid)
    if result["all_results"]:
        optimizer.print_leaderboard(result["all_results"], top=args.optimize_top or 10)

    if result.get("best_params"):
        from strategy.params import save_params
        save_params(
            strategy_name=strategy_name,
            params=result["best_params"],
            symbol=symbol,
            score=result.get("best_score"),
            metric=metric,
        )
        print(f"\n最优参数已自动保存到 params/{strategy_name}_{symbol}.json")

    if args.output_json:
        import json
        leaderboard = [
            {k: v for k, v in r.items() if k != "summary"}
            for r in result["all_results"] if "error" not in r
        ]
        output = {
            "best_params": result["best_params"],
            "best_score": result["best_score"],
            "metric": metric,
            "leaderboard": leaderboard,
        }
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n优化结果已导出: {args.output_json}")



def run_portfolio(args, config, logger):
    """运行组合回测（多股票同时持仓）。"""
    backtest_config = config.get("backtest", {})
    days = args.days or backtest_config.get("days", 250)
    initial_cash = config.get("initial_cash", 1_000_000)
    commission = backtest_config.get("commission", 0.0003)
    stop_loss = args.stop_loss if args.stop_loss is not None else backtest_config.get("stop_loss", 0.0)
    take_profit = args.take_profit if args.take_profit is not None else backtest_config.get("take_profit", 0.0)
    max_positions = getattr(args, "max_positions", 5)
    slippage = args.slippage if args.slippage is not None else backtest_config.get("slippage", 0.001)
    slippage_type = args.slippage_type if args.slippage_type else backtest_config.get("slippage_type", "percent")
    enforce_t1 = args.enforce_t1 if hasattr(args, "enforce_t1") else backtest_config.get("enforce_t_plus_1", True)
    check_limit = args.check_limit if hasattr(args, "check_limit") else backtest_config.get("check_limit", True)

    risk_manager = build_risk_manager(args, config)

    symbols = resolve_symbols(args, config, logger)
    if not symbols:
        logger.error("没有可用的股票代码")
        return
    if len(symbols) < 2:
        logger.warning("组合回测至少需要2只股票，当前只有 %d 只，自动降级为单股票回测", len(symbols))
        run_backtest(args, config, logger)
        return

    logger.info("启动组合回测: %s", symbols)

    def make_strategy():
        return get_strategy(args.strategy or DEFAULT_STRATEGY)

    engine = PortfolioBacktestEngine(
        initial_cash=initial_cash,
        commission=commission,
        verbose=args.verbose,
        stop_loss=stop_loss,
        take_profit=take_profit,
        max_positions=max_positions,
        slippage=slippage,
        slippage_type=slippage_type,
        enforce_t_plus_1=enforce_t1,
        check_limit=check_limit,
        risk_manager=risk_manager,
    )
    summary = engine.run(make_strategy, symbols, days=days)
    if summary is None:
        return

    if args.output_json:
        export_summary_json(summary, summary["equity_curve"], summary["benchmark_curve"], args.output_json)
    if args.output_csv:
        export_trades_csv(summary.get("trades_list", []), args.output_csv)
    if args.chart:
        chart_dir = Path(args.chart)
        plot_equity_curve(summary["equity_curve"], summary["benchmark_curve"], ",".join(symbols), chart_dir / "portfolio_equity.png", summary=summary)
        plot_drawdown_curve(summary["equity_curve"], ",".join(symbols), chart_dir / "portfolio_drawdown.png")


def run_backtest(args, config, logger):
    """运行回测。"""
    backtest_config = config.get("backtest", {})
    days = args.days or backtest_config.get("days", 250)
    initial_cash = config.get("initial_cash", 1_000_000)
    commission = backtest_config.get("commission", 0.0003)
    stop_loss = args.stop_loss if args.stop_loss is not None else backtest_config.get("stop_loss", 0.0)
    take_profit = args.take_profit if args.take_profit is not None else backtest_config.get("take_profit", 0.0)
    position_size = args.position_size if args.position_size is not None else backtest_config.get("position_size", 1.0)
    slippage = args.slippage if args.slippage is not None else backtest_config.get("slippage", 0.001)
    slippage_type = args.slippage_type if args.slippage_type else backtest_config.get("slippage_type", "percent")
    enforce_t1 = args.enforce_t1 if hasattr(args, "enforce_t1") else backtest_config.get("enforce_t_plus_1", True)
    check_limit = args.check_limit if hasattr(args, "check_limit") else backtest_config.get("check_limit", True)
    start_date = backtest_config.get("start_date")
    end_date = backtest_config.get("end_date")
    parallel = getattr(args, "parallel", True)

    risk_manager = build_risk_manager(args, config)
    risk_params = None
    if risk_manager is not None:
        risk_params = risk_manager.get_status()

    symbols = resolve_symbols(args, config, logger)
    if not symbols:
        return

    verbose = args.verbose
    strategy_name = args.strategy or DEFAULT_STRATEGY

    if risk_manager is not None:
        logger.info("风控已启用: 最大仓位%.0f%%, 最大持仓%d, 最大回撤%.0f%%, 日亏损%.0f%%, 个股亏损%.0f%%",
                     risk_manager.max_position_pct * 100, risk_manager.max_positions,
                     risk_manager.max_drawdown_pct * 100, risk_manager.max_daily_loss_pct * 100,
                     risk_manager.max_stock_loss_pct * 100)

    if args.all_strategies:
        strategy_names = list_strategies()
        all_results = []

        tasks = []
        for symbol in symbols:
            for s in strategy_names:
                tasks.append((s, symbol, days, initial_cash, commission, stop_loss, take_profit, position_size, start_date, end_date, verbose, slippage, slippage_type, enforce_t1, check_limit, risk_params))

        print(f"\n{'=' * 60}")
        print(f"批量回测: {len(symbols)} 只股票 x {len(strategy_names)} 种策略 = {len(tasks)} 个任务 (并发执行)")
        print(f"{'=' * 60}")

        t0 = time.time()
        results_map = {}
        with ProcessPoolExecutor(max_workers=min(8, len(tasks))) as executor:
            futures = {executor.submit(_run_single_backtest, t): t for t in tasks}
            done = 0
            for future in as_completed(futures):
                done += 1
                args_tuple = futures[future]
                symbol, s = args_tuple[1], args_tuple[0]
                try:
                    result = future.result()
                    if result is not None:
                        results_map[(symbol, s)] = result
                except Exception as exc:
                    pass
                if done % 10 == 0 or done == len(tasks):
                    print(f"  进度: {done}/{len(tasks)}", flush=True)
        elapsed = time.time() - t0
        print(f"  完成，耗时 {elapsed:.1f}s")

        for symbol in symbols:
            strategy_results = []
            for s in strategy_names:
                result = results_map.get((symbol, s))
                if result:
                    strategy_results.append({
                        "strategy": result["strategy"],
                        "profit_pct": result["profit_pct"],
                        "trades": result["trades"],
                        "final_value": result["final_value"],
                        "benchmark_return": result.get("benchmark_return", 0),
                        "alpha": result.get("alpha", 0),
                        "max_drawdown_pct": result.get("max_drawdown_pct", 0),
                        "sharpe_ratio": result.get("sharpe_ratio", 0),
                        "win_rate": result.get("win_rate", 0),
                    })
            if not strategy_results:
                logger.warning("股票 %s 没有可用回测结果", symbol)
                continue
            strategy_results.sort(key=lambda item: item["profit_pct"], reverse=True)
            all_results.append({"symbol": symbol, "results": strategy_results})

        print(f"\n{'=' * 60}")
        print("策略对比汇总")
        print(f"{'=' * 60}")

        header = f"{'股票':<15s}"
        for s in strategy_names:
            header += f"{s:>12s}"
        print(header)
        print("-" * (15 + 12 * len(strategy_names)))

        for result in all_results:
            row = f"{result['symbol']:<15s}"
            strategy_result_map = {r["strategy"]: r for r in result["results"]}
            for s in strategy_names:
                if s in strategy_result_map:
                    row += f"{strategy_result_map[s]['profit_pct']:+12.2f}%"
                else:
                    row += f"{'N/A':>12s}"
            print(row)

        print(f"\n{'=' * 60}")
        print("各策略统计")
        print(f"{'策略':<15s} {'平均收益':>10s} {'平均Alpha':>10s} {'平均最大回撤':>13s} {'平均夏普':>10s} {'胜率':>8s}")
        print("-" * 75)
        for s in strategy_names:
            profits = []
            alphas = []
            max_dds = []
            sharpes = []
            for r in all_results:
                for x in r["results"]:
                    if x["strategy"] == s:
                        profits.append(x["profit_pct"])
                        alphas.append(x.get("alpha", 0))
                        max_dds.append(x.get("max_drawdown_pct", 0))
                        sharpes.append(x.get("sharpe_ratio", 0))
                        break
            if profits:
                avg = sum(profits) / len(profits)
                avg_alpha = sum(alphas) / len(alphas)
                avg_dd = sum(max_dds) / len(max_dds)
                avg_sharpe = sum(sharpes) / len(sharpes)
                wins = sum(1 for p in profits if p > 0)
                win_rate = wins / len(profits) * 100
                print(f"{s:<15s} {avg:+10.2f}% {avg_alpha:+10.2f}% {avg_dd:>12.2f}% {avg_sharpe:>10.2f} {win_rate:>7.1f}%")

        if args.chart and all_results:
            for r in all_results:
                if r["results"]:
                    chart_dir = Path(args.chart)
                    plot_strategy_comparison(
                        r["results"],
                        chart_dir / f"{r['symbol']}_strategy_comparison.png",
                    )
        return

    results = []

    if parallel and len(symbols) > 1:
        print(f"\n{'=' * 50}")
        print(f"批量回测: {len(symbols)} 只股票 (并发执行)")
        print(f"{'=' * 50}")
        t0 = time.time()
        tasks = [(strategy_name, s, days, initial_cash, commission, stop_loss, take_profit, position_size, start_date, end_date, verbose, slippage, slippage_type, enforce_t1, check_limit, risk_params) for s in symbols]
        with ProcessPoolExecutor(max_workers=min(8, len(symbols))) as executor:
            futures = [executor.submit(_run_single_backtest, t) for t in tasks]
            for future in as_completed(futures):
                try:
                    summary = future.result()
                    if summary:
                        results.append(summary)
                except Exception:
                    pass
        elapsed = time.time() - t0
        print(f"  完成，耗时 {elapsed:.1f}s\n")
    else:
        for symbol in symbols:
            if verbose:
                print(f"\n{'=' * 50}")
                print(f"回测: {symbol} (策略: {strategy_name})")
            else:
                logger.info("回测: %s", symbol)

            engine = BacktestEngine(
                initial_cash=initial_cash,
                commission=commission,
                verbose=verbose,
                stop_loss=stop_loss,
                take_profit=take_profit,
                position_size=position_size,
                slippage=slippage,
                slippage_type=slippage_type,
                enforce_t_plus_1=enforce_t1,
                check_limit=check_limit,
                risk_manager=risk_manager,
            )
            strategy = get_strategy(
                strategy_name,
                symbol=symbol,
                load_params=getattr(args, "load_params", False),
            )
            summary = engine.run(
                strategy,
                symbol,
                days=days,
                start_date=start_date,
                end_date=end_date,
            )
            if summary is None:
                continue

            results.append(summary)
            print()
            export_results(args, summary, symbol)

    print_batch_summary(results, initial_cash)


def run_simulate(args, config, logger):
    """运行模拟交易。"""
    logger.info("启动模拟交易模式")

    strategy = get_strategy(args.strategy or DEFAULT_STRATEGY)
    broker = SimulatorBroker(
        initial_cash=config.get("initial_cash", 1_000_000),
        commission=config.get("backtest", {}).get("commission", 0.0003),
    )
    fetcher = DataFetcher()
    symbol = args.symbol or config.get("default_symbol", "000001.SZ")

    print(f"开始模拟交易: {symbol}")

    data = fetcher.get_realtime(symbol)
    if not data:
        logger.info("实时行情不可用，使用模拟实时数据")
        mock_price = 15.0 if "000001" in symbol else 50.0
        data = {
            "symbol": symbol,
            "open": mock_price * 0.99,
            "high": mock_price * 1.02,
            "low": mock_price * 0.98,
            "close": mock_price,
            "last_price": mock_price,
            "volume": 1e6,
            "date": datetime.now(),
            "timestamp": datetime.now(),
        }

    prices = {symbol: data["last_price"]}
    print(f"当前价格: {data['last_price']}")
    print(f"账户现金: {broker.get_cash():,.2f}")

    signal = strategy.on_bar(data)
    print(f"策略信号: {signal}")

    if signal == "buy":
        lot_size = 100
        price = data["last_price"]
        affordable = int(broker.get_cash() * 0.5 / (price * (1 + broker.commission)) / lot_size) * lot_size
        if affordable > 0:
            success = broker.buy(symbol, price, affordable, data["timestamp"])
            if success:
                print(f"模拟买入成功: {affordable}股 @ {price:.2f}")

    broker.print_status()
    print(f"总资产: {broker.get_total_value(prices):,.2f}")


def run_realtime(args, config, logger):
    """运行实时盯盘。"""
    logger.info("启动实时盯盘模式")

    strategy = get_strategy(args.strategy or DEFAULT_STRATEGY)
    broker = SimulatorBroker(initial_cash=config.get("initial_cash", 1_000_000))
    symbol = args.symbol or config.get("default_symbol", "000001.SZ")

    def on_bar(current_symbol: str, bar: dict):
        signal = strategy.on_bar(bar)
        print(f"[{bar['timestamp']}] {current_symbol}: {bar['last_price']}, 信号: {signal}")

        if signal == "buy" and broker.get_position(current_symbol) == 0:
            lot_size = 100
            price = bar["last_price"]
            affordable = int(broker.get_cash() * 0.5 / (price * (1 + broker.commission)) / lot_size) * lot_size
            if affordable > 0:
                broker.buy(current_symbol, price, affordable, bar["timestamp"])
        elif signal == "sell" and broker.get_position(current_symbol) > 0:
            broker.sell(
                current_symbol,
                bar["last_price"],
                broker.get_position(current_symbol),
                bar["timestamp"],
            )

    monitor = RealtimeMonitor(
        [symbol],
        interval=config.get("data", {}).get("fetch_interval", 60),
    )
    monitor.add_callback(on_bar)

    print(f"开始实时盯盘: {symbol}")
    monitor.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        monitor.stop()


def run_walk_forward(args, config, logger):
    """运行 Walk-Forward 验证。"""
    backtest_config = config.get("backtest", {})
    wf_config = config.get("walk_forward", {})
    strategy_name = args.strategy or DEFAULT_STRATEGY
    symbol = args.symbol or config.get("default_symbol", "000001.SZ")
    commission = backtest_config.get("commission", 0.0003)
    stop_loss = args.stop_loss if args.stop_loss is not None else backtest_config.get("stop_loss", 0.0)
    take_profit = args.take_profit if args.take_profit is not None else backtest_config.get("take_profit", 0.0)
    position_size = args.position_size if args.position_size is not None else backtest_config.get("position_size", 1.0)
    slippage = args.slippage if args.slippage is not None else backtest_config.get("slippage", 0.001)
    slippage_type = args.slippage_type if args.slippage_type else backtest_config.get("slippage_type", "percent")
    enforce_t1 = args.enforce_t1 if hasattr(args, "enforce_t1") else backtest_config.get("enforce_t_plus_1", True)
    check_limit = args.check_limit if hasattr(args, "check_limit") else backtest_config.get("check_limit", True)

    train_days = args.wf_train_days if hasattr(args, "wf_train_days") and args.wf_train_days else wf_config.get("train_days", 120)
    test_days = args.wf_test_days if hasattr(args, "wf_test_days") and args.wf_test_days else wf_config.get("test_days", 60)
    step_days = args.wf_step_days if hasattr(args, "wf_step_days") and args.wf_step_days else wf_config.get("step_days", 60)
    overfit_threshold = wf_config.get("overfit_threshold", 0.5)

    logger.info("启动 Walk-Forward 验证: %s / %s", strategy_name, symbol)
    logger.info("参数: 训练%d天, 验证%d天, 步进%d天", train_days, test_days, step_days)

    validator = WalkForwardValidator(
        strategy_name=strategy_name,
        symbol=symbol,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        commission=commission,
        stop_loss=stop_loss,
        take_profit=take_profit,
        position_size=position_size,
        slippage=slippage,
        slippage_type=slippage_type,
        enforce_t_plus_1=enforce_t1,
        check_limit=check_limit,
        overfit_threshold=overfit_threshold,
    )

    result = validator.validate()
    print(result.summary())

    if args.output_json:
        import json
        output = {
            "strategy": result.strategy_name,
            "symbol": result.symbol,
            "avg_train_return": result.avg_train_return,
            "avg_test_return": result.avg_test_return,
            "avg_train_sharpe": result.avg_train_sharpe,
            "avg_test_sharpe": result.avg_test_sharpe,
            "degradation_ratio": result.degradation_ratio,
            "is_overfit": result.is_overfit,
            "windows": [
                {
                    "window_id": w.window_id,
                    "train_start": w.train_start,
                    "train_end": w.train_end,
                    "test_start": w.test_start,
                    "test_end": w.test_end,
                    "train_return": w.train_result.get("profit_pct", 0),
                    "test_return": w.test_result.get("profit_pct", 0),
                    "train_sharpe": w.train_result.get("sharpe_ratio", 0),
                    "test_sharpe": w.test_result.get("sharpe_ratio", 0),
                }
                for w in result.windows
            ],
        }
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\nWalk-Forward 结果已导出: {args.output_json}")


def main():
    parser = argparse.ArgumentParser(description="量化交易系统")
    parser.add_argument(
        "--mode",
        choices=["backtest", "simulate", "realtime", "optimize", "walkforward"],
        default="backtest",
        help="运行模式",
    )
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY, help="策略名称")
    parser.add_argument("--symbol", default=None, help="股票代码")
    parser.add_argument("--symbols", default=None, help="股票代码文件路径")
    parser.add_argument("--days", type=int, default=None, help="回测天数，默认使用配置值")
    parser.add_argument("--verbose", action="store_true", help="显示交易明细")
    parser.add_argument("--all-strategies", action="store_true", help="测试所有策略并对比")
    parser.add_argument("--stop-loss", type=float, default=None, help="止损比例 (如 0.05 表示 5%%)")
    parser.add_argument("--take-profit", type=float, default=None, help="止盈比例 (如 0.10 表示 10%%)")
    parser.add_argument("--position-size", type=float, default=None, help="仓位比例 0.0~1.0 (默认 1.0)")
    parser.add_argument("--slippage", type=float, default=None, help="滑点比例 (如0.001=0.1%%) 或固定金额")
    parser.add_argument("--slippage-type", choices=["percent", "fixed"], default="percent", help="滑点类型: percent(百分比) 或 fixed(固定金额)")
    parser.add_argument("--no-t1", dest="enforce_t1", action="store_false", help="禁用T+1约束（允许当日买卖）")
    parser.add_argument("--no-limit", dest="check_limit", action="store_false", help="禁用涨跌停判断")
    parser.add_argument("--no-parallel", dest="parallel", action="store_false", help="禁用并发批量回测")
    parser.add_argument("--output-json", metavar="PATH", help="导出回测结果为 JSON 文件")
    parser.add_argument("--output-csv", metavar="PATH", help="导出交易记录为 CSV 文件")
    parser.add_argument("--chart", metavar="DIR", help="保存权益曲线图到指定目录")
    parser.add_argument("--portfolio", action="store_true", help="启用组合回测模式（多股票同时持仓）")
    parser.add_argument("--max-positions", type=int, default=5, help="组合最大同时持仓数（默认5）")
    parser.add_argument("--param", action="append", dest="param", metavar="KEY=VAL1,VAL2...", help="优化参数范围，如 --param fast=5,10,20 --param slow=30,60")
    parser.add_argument("--optimize-metric", default="sharpe_ratio", choices=["profit_pct", "sharpe_ratio", "profit_factor", "max_drawdown_pct", "win_rate"], help="优化目标指标")
    parser.add_argument("--optimize-top", type=int, default=10, help="排行榜显示前N名（默认10）")
    parser.add_argument("--optimize-workers", type=int, default=4, help="并发进程数（默认4）")
    parser.add_argument("--load-params", action="store_true", help="加载已保存的最优参数（回测时自动加载 params/ 目录下对应参数）")

    risk_group = parser.add_argument_group("风控参数")
    risk_group.add_argument("--no-risk", action="store_true", help="禁用风控模块")
    risk_group.add_argument("--risk-enabled", action="store_true", help="强制启用风控模块")
    risk_group.add_argument("--max-position-pct", type=float, default=None, help="单股最大仓位占比 (如0.25=25%%)")
    risk_group.add_argument("--risk-max-positions", type=int, default=None, help="最大持仓数量")
    risk_group.add_argument("--max-drawdown-pct", type=float, default=None, help="最大回撤熔断阈值 (如0.20=20%%)")
    risk_group.add_argument("--max-daily-loss-pct", type=float, default=None, help="单日最大亏损阈值 (如0.03=3%%)")
    risk_group.add_argument("--max-stock-loss-pct", type=float, default=None, help="单股最大亏损阈值 (如0.10=10%%)")

    wf_group = parser.add_argument_group("Walk-Forward 参数")
    wf_group.add_argument("--wf-train-days", type=int, default=None, help="Walk-Forward 训练期天数 (默认120)")
    wf_group.add_argument("--wf-test-days", type=int, default=None, help="Walk-Forward 验证期天数 (默认60)")
    wf_group.add_argument("--wf-step-days", type=int, default=None, help="Walk-Forward 步进天数 (默认60)")

    args = parser.parse_args()
    logger = setup_logger()
    config = load_config()

    if args.mode == "backtest":
        if args.portfolio:
            run_portfolio(args, config, logger)
        else:
            run_backtest(args, config, logger)
    elif args.mode == "simulate":
        run_simulate(args, config, logger)
    elif args.mode == "realtime":
        run_realtime(args, config, logger)
    elif args.mode == "optimize":
        run_optimize(args, config, logger)
    elif args.mode == "walkforward":
        run_walk_forward(args, config, logger)


if __name__ == "__main__":
    main()
