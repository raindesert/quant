"""量化交易系统主入口。"""
import argparse
import time
from datetime import datetime
from pathlib import Path

import yaml

from backtest.engine import BacktestEngine
from broker.simulator import SimulatorBroker
from data.fetcher import DataFetcher
from monitor.realtime import RealtimeMonitor
from strategy.examples import (
    BollingerStrategy,
    MACDStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    RSIStrategy,
    SMAStrategy,
)
from utils.logger import setup_logger


BASE_DIR = Path(__file__).parent
DEFAULT_STRATEGY = "sma"

STRATEGIES = {
    "sma": SMAStrategy,
    "rsi": RSIStrategy,
    "macd": MACDStrategy,
    "bollinger": BollingerStrategy,
    "momentum": MomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
}


def get_strategy(strategy_name: str):
    """根据名称创建策略实例。"""
    strategy_cls = STRATEGIES.get(strategy_name.lower())
    if strategy_cls is None:
        print(f"未知策略: {strategy_name}，将使用默认策略 SMA")
        return SMAStrategy()
    return strategy_cls()


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
    for result in results:
        profit_pct = (result["final_value"] - initial_cash) / initial_cash * 100
        print(f"{result['symbol']}: 收益率 {profit_pct:.2f}%, 交易 {result['trades']} 次")


def run_backtest(args, config, logger):
    """运行回测。"""
    days = args.days or config.get("backtest", {}).get("days", 250)
    initial_cash = config.get("initial_cash", 1_000_000)
    commission = config.get("backtest", {}).get("commission", 0.0003)

    symbols = resolve_symbols(args, config, logger)
    if not symbols:
        return

    verbose = args.verbose
    strategy_name = args.strategy or DEFAULT_STRATEGY

    if args.all_strategies:
        strategy_names = list(STRATEGIES.keys())
        all_results = []

        for symbol in symbols:
            print(f"\n{'=' * 60}")
            print(f"股票: {symbol}")
            print(f"{'=' * 60}")

            strategy_results = []
            for current_strategy_name in strategy_names:
                engine = BacktestEngine(
                    initial_cash=initial_cash,
                    commission=commission,
                    verbose=False,
                )
                summary = engine.run(
                    get_strategy(current_strategy_name),
                    symbol,
                    days=days,
                )
                if summary is None:
                    continue

                strategy_results.append(
                    {
                        "strategy": current_strategy_name,
                        "profit_pct": summary["profit_pct"],
                        "trades": summary["trades"],
                        "final_value": summary["final_value"],
                    }
                )
                print(
                    f"  {current_strategy_name:15s}: "
                    f"{summary['profit_pct']:+7.2f}%  ({summary['trades']}次交易)"
                )

            if not strategy_results:
                logger.warning("股票 %s 没有可用回测结果", symbol)
                continue

            strategy_results.sort(key=lambda item: item["profit_pct"], reverse=True)
            all_results.append(
                {
                    "symbol": symbol,
                    "best_strategy": strategy_results[0]["strategy"],
                    "best_profit": strategy_results[0]["profit_pct"],
                    "results": strategy_results,
                }
            )

        print(f"\n{'=' * 60}")
        print("策略对比汇总")
        print(f"{'=' * 60}")
        print(f"{'股票':<15s} {'最佳策略':<15s} {'收益率':>10s}")
        print("-" * 45)
        for result in all_results:
            print(
                f"{result['symbol']:<15s} "
                f"{result['best_strategy']:<15s} "
                f"{result['best_profit']:+10.2f}%"
            )
        return

    results = []
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
        )
        summary = engine.run(get_strategy(strategy_name), symbol, days=days)
        if summary is None:
            continue

        results.append(summary)
        print()

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
        success = broker.buy(symbol, data["last_price"], 1000, data["timestamp"])
        if success:
            print(f"模拟买入成功: 1000股 @ {data['last_price']}")

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
            broker.buy(current_symbol, bar["last_price"], 1000, bar["timestamp"])
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


def main():
    parser = argparse.ArgumentParser(description="量化交易系统")
    parser.add_argument(
        "--mode",
        choices=["backtest", "simulate", "realtime"],
        default="backtest",
        help="运行模式",
    )
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY, help="策略名称")
    parser.add_argument("--symbol", default=None, help="股票代码")
    parser.add_argument("--symbols", default=None, help="股票代码文件路径")
    parser.add_argument("--days", type=int, default=None, help="回测天数，默认使用配置值")
    parser.add_argument("--verbose", action="store_true", help="显示交易明细")
    parser.add_argument("--all-strategies", action="store_true", help="测试所有策略并对比")

    args = parser.parse_args()
    logger = setup_logger()
    config = load_config()

    if args.mode == "backtest":
        run_backtest(args, config, logger)
    elif args.mode == "simulate":
        run_simulate(args, config, logger)
    elif args.mode == "realtime":
        run_realtime(args, config, logger)


if __name__ == "__main__":
    main()
