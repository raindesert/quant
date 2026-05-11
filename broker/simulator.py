"""模拟券商 - 模拟账户和订单执行，支持A股真实佣金。"""
from datetime import datetime
from typing import Dict


class SimulatorBroker:
    """模拟券商

    佣金模型:
    - 买入: 佣金 = max(成交金额 × 佣金率, 最低佣金)
    - 卖出: 佣金 + 印花税(千一)
    """

    def __init__(
        self,
        initial_cash: float = 1000000,
        commission: float = 0.0003,
        stamp_tax: float = 0.001,
        min_commission: float = 5.0,
    ):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.commission = commission
        self.stamp_tax = stamp_tax
        self.min_commission = min_commission
        self.positions = {}
        self.orders = []
        self.trades = []

    def buy(self, symbol: str, price: float, quantity: int, timestamp: datetime = None) -> bool:
        quantity = (quantity // 100) * 100
        if quantity <= 0:
            return False

        trade_value = price * quantity
        commission_fee = max(trade_value * self.commission, self.min_commission)
        total_cost = trade_value + commission_fee
        if total_cost > self.cash:
            return False

        self.cash -= total_cost
        self.positions[symbol] = self.positions.get(symbol, 0) + quantity

        self.trades.append({
            "timestamp": timestamp or datetime.now(),
            "symbol": symbol,
            "action": "BUY",
            "price": price,
            "quantity": quantity,
            "commission": commission_fee,
            "cost": total_cost,
        })
        return True

    def sell(self, symbol: str, price: float, quantity: int, timestamp: datetime = None) -> bool:
        if self.positions.get(symbol, 0) < quantity:
            return False

        trade_value = price * quantity
        commission_fee = max(trade_value * self.commission, self.min_commission)
        stamp_fee = trade_value * self.stamp_tax
        proceeds = trade_value - commission_fee - stamp_fee

        self.cash += proceeds
        self.positions[symbol] -= quantity
        if self.positions[symbol] <= 0:
            del self.positions[symbol]

        self.trades.append({
            "timestamp": timestamp or datetime.now(),
            "symbol": symbol,
            "action": "SELL",
            "price": price,
            "quantity": quantity,
            "commission": commission_fee,
            "stamp_tax": stamp_fee,
            "proceeds": proceeds,
        })
        return True

    def get_position(self, symbol: str) -> int:
        return self.positions.get(symbol, 0)

    def get_cash(self) -> float:
        return self.cash

    def get_total_value(self, prices: Dict[str, float]) -> float:
        positions_value = sum(
            self.positions.get(s, 0) * prices.get(s, 0)
            for s in self.positions
        )
        return self.cash + positions_value

    def print_status(self):
        print(f"\n===== 账户状态 =====")
        print(f"现金: {self.cash:,.2f}")
        print(f"持仓: {self.positions}")
        print(f"交易次数: {len(self.trades)}")
