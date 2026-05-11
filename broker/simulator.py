"""模拟券商 - 模拟账户和订单执行"""
from datetime import datetime
from typing import Dict, List


class SimulatorBroker:
    """模拟券商"""

    def __init__(self, initial_cash: float = 1000000, commission: float = 0.0003):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.commission = commission
        self.positions = {}
        self.orders = []
        self.trades = []

    def buy(self, symbol: str, price: float, quantity: int, timestamp: datetime = None) -> bool:
        quantity = (quantity // 100) * 100
        if quantity <= 0:
            return False
        total_cost = price * quantity * (1 + self.commission)
        if total_cost > self.cash:
            return False

        self.cash -= total_cost
        self.positions[symbol] = self.positions.get(symbol, 0) + quantity

        trade = {
            "timestamp": timestamp or datetime.now(),
            "symbol": symbol,
            "action": "BUY",
            "price": price,
            "quantity": quantity,
            "cost": total_cost
        }
        self.trades.append(trade)
        return True

    def sell(self, symbol: str, price: float, quantity: int, timestamp: datetime = None) -> bool:
        if self.positions.get(symbol, 0) < quantity:
            return False

        proceeds = price * quantity * (1 - self.commission)
        self.cash += proceeds
        self.positions[symbol] -= quantity
        if self.positions[symbol] <= 0:
            del self.positions[symbol]

        trade = {
            "timestamp": timestamp or datetime.now(),
            "symbol": symbol,
            "action": "SELL",
            "price": price,
            "quantity": quantity,
            "proceeds": proceeds
        }
        self.trades.append(trade)
        return True

    def get_position(self, symbol: str) -> int:
        """获取持仓"""
        return self.positions.get(symbol, 0)

    def get_cash(self) -> float:
        """获取现金"""
        return self.cash

    def get_total_value(self, prices: Dict[str, float]) -> float:
        """获取总资产"""
        positions_value = sum(
            self.positions.get(s, 0) * prices.get(s, 0)
            for s in self.positions
        )
        return self.cash + positions_value

    def print_status(self):
        """打印账户状态"""
        print(f"\n===== 账户状态 =====")
        print(f"现金: {self.cash:,.2f}")
        print(f"持仓: {self.positions}")
        print(f"交易次数: {len(self.trades)}")
