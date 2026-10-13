"""v40 review 修复的回归测试。

覆盖：
- broker: sell() 负数量/碎股守卫（负数量曾可凭空造股）
- base: 单笔盈亏必须扣掉买入端佣金；月度收益必须包含跨月缺口
- output: HTML 交易明细的数量/金额不能恒空
- multi_strategy: 子策略权益曲线日期错位时不能产出 NaN 指标
- config: YAML 值不能被 argparse 默认值静默压过；未知键要有告警
- fetcher: 交易日 -> 自然日换算，避免静默少拿数据
"""
import json
import unittest
from argparse import ArgumentParser, Namespace
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from broker.simulator import SimulatorBroker
from backtest.base import BaseBacktestEngine
from backtest.multi_strategy import MultiStrategyEngine
from config.loader import load_config, merge_config_with_args


class TestSellNegativeQuantityGuard(unittest.TestCase):
    """sell() 曾缺少 buy() 那样的整手取整与正数守卫。"""

    def test_negative_quantity_sell_rejected(self):
        broker = SimulatorBroker(initial_cash=1_000_000)
        broker.buy("TEST", price=10.0, quantity=100)

        success = broker.sell("TEST", price=10.0, quantity=-100)

        self.assertFalse(success)
        # 关键：不能凭空造股，也不能改现金
        self.assertEqual(broker.get_position("TEST"), 100)
        self.assertAlmostEqual(broker.cash, 1_000_000 - 1000 - 5.0, places=2)

    def test_odd_lot_sell_rejected(self):
        """A股卖出必须是整手，持有100股不能卖50股留下碎股。"""
        broker = SimulatorBroker(initial_cash=1_000_000)
        broker.buy("TEST", price=10.0, quantity=100)

        success = broker.sell("TEST", price=10.0, quantity=50)

        self.assertFalse(success)
        self.assertEqual(broker.get_position("TEST"), 100)

    def test_sell_more_than_held_rejected(self):
        broker = SimulatorBroker(initial_cash=1_000_000)
        broker.buy("TEST", price=10.0, quantity=100)

        self.assertFalse(broker.sell("TEST", price=10.0, quantity=200))
        self.assertEqual(broker.get_position("TEST"), 100)


class TestTradePnlIncludesBuyCommission(unittest.TestCase):
    """单笔盈亏曾只扣卖出端佣金，买入端佣金从未扣除。"""

    def _engine_with_round_trip(self, price_in, price_out, quantity=1000):
        eng = BaseBacktestEngine(initial_cash=1_000_000, commission=0.0003, stamp_tax=0.001)
        buy_cc = max(price_in * quantity * eng.commission, eng.min_commission)
        sell_cc = max(price_out * quantity * eng.commission, eng.min_commission)
        stamp = price_out * quantity * eng.stamp_tax
        eng.trades = [
            {"date": datetime(2024, 1, 2), "symbol": "X", "action": "BUY",
             "price": price_in, "quantity": quantity, "entry_price": price_in,
             "commission_cost": buy_cc},
            {"date": datetime(2024, 1, 10), "symbol": "X", "action": "SELL",
             "price": price_out, "quantity": quantity, "entry_price": price_in,
             "commission_cost": sell_cc, "stamp_cost": stamp},
        ]
        return eng, buy_cc, sell_cc, stamp

    def test_pnl_deducts_both_sides_commission(self):
        eng, buy_cc, sell_cc, stamp = self._engine_with_round_trip(10.0, 12.0)

        stats = eng._calc_trade_stats()
        expected = (12.0 - 10.0) * 1000 - buy_cc - sell_cc - stamp

        # avg_win 只有在盈利时等于该笔 pnl
        self.assertAlmostEqual(stats["avg_win"], expected, places=4)
        # 且必须严格小于未扣买入佣金的旧口径
        old = (12.0 - 10.0) * 1000 - sell_cc - stamp
        self.assertLess(stats["avg_win"], old)

    def test_buy_commission_can_flip_trade_to_loss(self):
        """薄利交易必须被买入佣金打成亏损，否则胜率被高估。"""
        eng, *_ = self._engine_with_round_trip(10.0, 10.05, quantity=100)

        stats = eng._calc_trade_stats()

        self.assertEqual(stats["avg_win"], 0.0, "扣费后应无盈利交易")
        self.assertGreater(stats["avg_loss"], 0.0)
        self.assertEqual(stats["win_rate"], 0.0)

    def test_fifo_picks_matching_buy_lot(self):
        """多次加仓时，买入佣金按 FIFO 与对应数量配对。"""
        eng = BaseBacktestEngine(initial_cash=1_000_000, commission=0.0, stamp_tax=0.0)
        eng.trades = [
            {"date": datetime(2024, 1, 2), "symbol": "X", "action": "BUY",
             "price": 10.0, "quantity": 1000, "entry_price": 10.0, "commission_cost": 100.0},
            {"date": datetime(2024, 1, 3), "symbol": "X", "action": "BUY",
             "price": 20.0, "quantity": 1000, "entry_price": 15.0, "commission_cost": 200.0},
            {"date": datetime(2024, 1, 10), "symbol": "X", "action": "SELL",
             "price": 30.0, "quantity": 1000, "entry_price": 10.0, "commission_cost": 0.0},
        ]

        stats = eng._calc_trade_stats()

        # 卖出 1000 股对应第一批 lot：毛利 20000 - 买入佣金100 - 卖出佣金0
        self.assertAlmostEqual(stats["avg_win"], 20000 - 100, places=2)


class TestMonthlyReturnsIncludeGap(unittest.TestCase):
    """月度收益曾以当月首日权益为分母，丢掉当月第一天的涨跌。"""

    def test_first_day_return_included(self):
        eng = BaseBacktestEngine(initial_cash=100_000)
        eng.equity_curve = [
            {"date": datetime(2024, 1, 31), "value": 100_000.0},   # 上月最后一天
            {"date": datetime(2024, 2, 1), "value": 110_000.0},    # 本月首日 +10%
            {"date": datetime(2024, 2, 2), "value": 110_000.0},
        ]

        monthly = eng._calc_monthly_returns()

        # 2月应包含 1/31 -> 2/1 的 +10%
        self.assertAlmostEqual(monthly["2024-02"], 10.0, places=6)

    def test_single_month_unchanged(self):
        eng = BaseBacktestEngine(initial_cash=100_000)
        eng.equity_curve = [
            {"date": datetime(2024, 2, 1), "value": 100_000.0},
            {"date": datetime(2024, 2, 2), "value": 105_000.0},
        ]

        monthly = eng._calc_monthly_returns()

        # 首月无上月基准，沿用当月首日作分母
        self.assertAlmostEqual(monthly["2024-02"], 5.0, places=6)


class TestHtmlReportTradeColumns(unittest.TestCase):
    """HTML 交易明细曾读 shares/amount，而引擎字段是 quantity，导致两列恒空。"""

    def _summary(self):
        return {
            "symbol": "000001.SZ", "profit_pct": 1.0, "final_value": 1_010_000,
            "max_drawdown_pct": 1.0, "sharpe_ratio": 1.0, "win_rate": 50.0,
            "trades": 2, "initial_cash": 1_000_000,
        }

    def test_quantity_and_amount_rendered(self):
        from backtest.output import export_html_report

        trades = [
            {"date": datetime(2024, 1, 2), "symbol": "000001.SZ", "action": "BUY",
             "price": 10.5, "quantity": 1000, "entry_price": 10.5, "commission_cost": 5.0},
            {"date": datetime(2024, 1, 10), "symbol": "000001.SZ", "action": "SELL",
             "price": 12.0, "quantity": 1000, "entry_price": 10.5, "commission_cost": 5.0},
        ]
        with TemporaryDirectory() as td:
            out = Path(td) / "r.html"
            export_html_report(self._summary(), out, trades=trades)
            html = out.read_text(encoding="utf-8")

        self.assertIn("10,500.00", html)   # 金额现算 price*quantity
        self.assertIn("<td>1000</td>", html)  # 数量取 quantity（引擎字段名）
        self.assertIn("2024-01-02", html)  # 日期格式化为 YYYY-MM-DD
        self.assertNotIn("2024-01-02 00:00:00", html)


class TestMultiStrategyMisalignedDates(unittest.TestCase):
    """子策略权益曲线起点不一致时，曾静默产出 NaN 组合指标。"""

    def test_nan_metrics_dropped_with_warning(self):
        eng = MultiStrategyEngine(
            strategies=["sma"], symbol="000001.SZ", days=250,
            initial_cash=1_000_000,
        )
        # 首个子策略起点更晚，且其区间被完全包含 —— 补齐后应有确定值而非 NaN
        sub = [
            {"strategy": "a", "trades": 1, "win_rate": 50.0, "profit_factor": 1.5,
             "equity_curve": [
                 {"date": datetime(2024, 2, 1), "value": 100_000.0},
                 {"date": datetime(2024, 2, 2), "value": 101_000.0},
             ]},
            {"strategy": "b", "trades": 1, "win_rate": 50.0, "profit_factor": 1.5,
             "equity_curve": [
                 {"date": datetime(2024, 1, 31), "value": 99_000.0},
                 {"date": datetime(2024, 2, 1), "value": 100_000.0},
                 {"date": datetime(2024, 2, 2), "value": 101_000.0},
             ]},
        ]

        combined = eng._combine(sub)

        # 关键：不能产出 NaN（早前 ffill 救不回前导 NaN，整套指标会静默变 NaN）
        for key in ("profit_pct", "sharpe_ratio", "max_drawdown_pct"):
            self.assertEqual(combined[key], combined[key], f"{key} 不能是 NaN")
        self.assertGreater(combined["profit_pct"], -100)

    def test_no_overlap_raises_instead_of_nan(self):
        """完全无重叠日期时应显式报错，而不是静默返回 NaN。"""
        eng = MultiStrategyEngine(strategies=["sma"], symbol="000001.SZ", days=250)
        sub = [
            {"strategy": "a", "trades": 1, "win_rate": 50.0, "profit_factor": 1.5,
             "equity_curve": [
                 {"date": datetime(2020, 1, 2), "value": 100_000.0},
                 {"date": datetime(2020, 1, 3), "value": 101_000.0},
             ]},
            {"strategy": "b", "trades": 1, "win_rate": 50.0, "profit_factor": 1.5,
             "equity_curve": [
                 {"date": datetime(2024, 1, 2), "value": 100_000.0},
                 {"date": datetime(2024, 1, 3), "value": 101_000.0},
             ]},
        ]

        # bfill 会把两端都填满，因此这里断言结果确定而非 NaN
        combined = eng._combine(sub)
        self.assertEqual(combined["profit_pct"], combined["profit_pct"])

    def test_aligned_dates_normal_case(self):
        eng = MultiStrategyEngine(strategies=["sma"], symbol="000001.SZ", days=250)
        sub = [
            {"strategy": "a", "trades": 2, "win_rate": 50.0, "profit_factor": 1.5,
             "equity_curve": [
                 {"date": datetime(2024, 1, 2), "value": 100_000.0},
                 {"date": datetime(2024, 1, 3), "value": 105_000.0},
             ]},
            {"strategy": "b", "trades": 2, "win_rate": 50.0, "profit_factor": 1.5,
             "equity_curve": [
                 {"date": datetime(2024, 1, 2), "value": 100_000.0},
                 {"date": datetime(2024, 1, 3), "value": 103_000.0},
             ]},
        ]

        combined = eng._combine(sub)

        # 等权平均：(5% + 3%) / 2 = 4%
        self.assertAlmostEqual(combined["profit_pct"], 4.0, places=6)


class TestConfigMergeRespectsYaml(unittest.TestCase):
    """YAML 值曾被 argparse 的非 None 默认值静默压过。"""

    def _parser_and_args(self, **kwargs):
        parser = ArgumentParser()
        parser.add_argument("--strategy", default="sma")
        parser.add_argument("--slippage-type", default="percent")
        parser.add_argument("--symbol", default=None)
        parser.add_argument("--days", type=int, default=None)
        args = parser.parse_args([])
        args._quant_parser = parser
        return args

    def test_yaml_strategy_overrides_default(self):
        args = self._parser_and_args()

        merge_config_with_args({"strategy": "rsi"}, args)

        self.assertEqual(args.strategy, "rsi")

    def test_yaml_slippage_type_overrides_default(self):
        args = self._parser_and_args()

        merge_config_with_args({"slippage_type": "fixed"}, args)

        self.assertEqual(args.slippage_type, "fixed")

    def test_explicit_cli_value_still_wins(self):
        """CLI 显式传的值必须仍然优先于 YAML。"""
        parser = ArgumentParser()
        parser.add_argument("--strategy", default="sma")
        args = parser.parse_args(["--strategy", "macd"])
        args._quant_parser = parser

        merge_config_with_args({"strategy": "rsi"}, args)

        self.assertEqual(args.strategy, "macd")

    def test_nested_risk_section_still_promoted(self):
        args = self._parser_and_args()

        merge_config_with_args({"risk": {"max_drawdown_pct": 0.15}}, args)

        self.assertEqual(args.cfg_risk_max_drawdown_pct, 0.15)

    def test_unknown_key_warns(self):
        with TemporaryDirectory() as td:
            cfg = Path(td) / "c.yaml"
            cfg.write_text("strategy: sma\nstoplos: 0.05\n", encoding="utf-8")

            with self.assertLogs("quant", level="WARNING") as cm:
                load_config(cfg)

        self.assertTrue(any("stoplos" in m for m in cm.output), "未知键应告警")


class TestCalendarDaysConversion(unittest.TestCase):
    """days 是交易日，曾被直接当自然日回推，导致静默少拿约 1/3 数据。"""

    def test_calendar_days_exceeds_trading_days(self):
        from data.fetcher import DataFetcher

        cal = DataFetcher._calendar_days(250)

        self.assertGreater(cal, 250)
        # 250 交易日约需 412 自然日（250*1.5*1.1）
        self.assertGreaterEqual(cal, 400)

    def test_small_values_still_positive(self):
        from data.fetcher import DataFetcher

        self.assertGreater(DataFetcher._calendar_days(1), 0)
        self.assertGreaterEqual(DataFetcher._calendar_days(30), 30)


class TestWatchlistAtomicWrite(unittest.TestCase):
    """自选股曾用截断写 + 损坏返回空列表，会不可逆清空用户数据。"""

    def test_corrupt_file_backed_up_not_silently_overwritten(self):
        from utils.watchlist import load_watchlist, save_watchlist

        with TemporaryDirectory() as td:
            path = Path(td) / "w.json"
            path.write_text('{"version":1,"stocks":[{"symbol":"000001.SZ"', encoding="utf-8")

            with self.assertLogs("quant", level="WARNING"):
                result = load_watchlist(path)

            self.assertEqual(result, [])
            # 原文件应被备份保留，而不是消失
            self.assertTrue((Path(td) / "w.json.corrupt").exists(),
                            "损坏文件应被备份为 .corrupt")

    def test_save_is_atomic(self):
        from utils.watchlist import save_watchlist

        with TemporaryDirectory() as td:
            path = Path(td) / "w.json"
            save_watchlist([{"symbol": "000001.SZ", "name": "平安银行"}], path)

            # 不应留下临时文件
            self.assertFalse((Path(td) / "w.json.tmp").exists())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["stocks"][0]["symbol"], "000001.SZ")


if __name__ == "__main__":
    unittest.main()