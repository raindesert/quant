"""风控模块 — 独立的风险管理器，在信号执行前进行风控检查。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("quant")


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: str = ""
    adjusted_quantity: int = 0


class RiskManager:
    """独立风控管理器。

    在信号执行前进行风控检查，支持:
    - 单股最大仓位限制（占总资产比例）
    - 最大持仓数量限制
    - 组合最大回撤熔断
    - 单日最大亏损限制
    - 单股最大亏损限制
    """

    def __init__(
        self,
        max_position_pct: float = 0.25,
        max_positions: int = 10,
        max_drawdown_pct: float = 0.20,
        max_daily_loss_pct: float = 0.03,
        max_stock_loss_pct: float = 0.10,
        enabled: bool = True,
    ):
        self.max_position_pct = max_position_pct
        self.max_positions = max_positions
        self.max_drawdown_pct = max_drawdown_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_stock_loss_pct = max_stock_loss_pct
        self.enabled = enabled

        self._peak_value: float = 0.0
        self._prev_day_value: float = 0.0
        self._stock_entry_values: dict[str, float] = {}
        self._circuit_breaker: bool = False
        self._circuit_breaker_reason: str = ""

    def reset(self):
        self._peak_value = 0.0
        self._prev_day_value = 0.0
        self._stock_entry_values = {}
        self._circuit_breaker = False
        self._circuit_breaker_reason = ""

    def update_portfolio_state(self, total_value: float, positions: dict, last_prices: dict, date=None):
        if total_value > self._peak_value:
            self._peak_value = total_value

        if self._prev_day_value <= 0:
            self._prev_day_value = total_value

        if self._peak_value > 0:
            drawdown = (self._peak_value - total_value) / self._peak_value
            if drawdown >= self.max_drawdown_pct:
                if not self._circuit_breaker:
                    self._circuit_breaker = True
                    self._circuit_breaker_reason = f"组合回撤{drawdown:.1%}超过阈值{self.max_drawdown_pct:.1%}"
                    logger.warning("熔断触发: %s", self._circuit_breaker_reason)

        daily_loss = (self._prev_day_value - total_value) / self._prev_day_value if self._prev_day_value > 0 else 0
        if daily_loss >= self.max_daily_loss_pct:
            if not self._circuit_breaker:
                self._circuit_breaker = True
                self._circuit_breaker_reason = f"单日亏损{daily_loss:.1%}超过阈值{self.max_daily_loss_pct:.1%}"
                logger.warning("熔断触发: %s", self._circuit_breaker_reason)

        self._prev_day_value = total_value

    def on_new_day(self, date=None):
        self._circuit_breaker = False
        self._circuit_breaker_reason = ""

    def check_buy(
        self,
        symbol: str,
        price: float,
        quantity: int,
        total_value: float,
        cash: float,
        positions: dict,
        last_prices: dict,
    ) -> RiskCheckResult:
        if not self.enabled:
            return RiskCheckResult(allowed=True, adjusted_quantity=quantity)

        if self._circuit_breaker:
            return RiskCheckResult(allowed=False, reason=f"熔断中: {self._circuit_breaker_reason}", adjusted_quantity=0)

        current_holdings = len(positions)
        if symbol not in positions and current_holdings >= self.max_positions:
            return RiskCheckResult(
                allowed=False,
                reason=f"持仓数{current_holdings}已达上限{self.max_positions}",
                adjusted_quantity=0,
            )

        order_value = price * quantity
        if total_value > 0:
            position_pct = order_value / total_value
            if position_pct > self.max_position_pct:
                max_qty = int(total_value * self.max_position_pct / price / 100) * 100
                if max_qty <= 0:
                    return RiskCheckResult(
                        allowed=False,
                        reason=f"单股仓位{position_pct:.1%}超限{self.max_position_pct:.1%}",
                        adjusted_quantity=0,
                    )
                logger.info("风控调整: %s 仓位从%d调整为%d(上限%.0f%%)", symbol, quantity, max_qty, self.max_position_pct * 100)
                quantity = max_qty

        existing_value = positions.get(symbol, 0) * last_prices.get(symbol, price)
        new_total = existing_value + price * quantity
        if total_value > 0 and new_total / total_value > self.max_position_pct:
            max_total = total_value * self.max_position_pct
            allowed_new = max(0, int((max_total - existing_value) / price / 100) * 100)
            if allowed_new <= 0:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"加仓后{symbol}仓位将超限{self.max_position_pct:.1%}",
                    adjusted_quantity=0,
                )
            quantity = allowed_new

        return RiskCheckResult(allowed=True, adjusted_quantity=quantity)

    def check_sell(
        self,
        symbol: str,
        price: float,
        quantity: int,
        entry_price: float,
        total_value: float,
    ) -> RiskCheckResult:
        if not self.enabled:
            return RiskCheckResult(allowed=True, adjusted_quantity=quantity)

        if entry_price > 0 and price < entry_price:
            loss_pct = (entry_price - price) / entry_price
            if loss_pct >= self.max_stock_loss_pct:
                logger.warning("风控强平: %s 亏损%.1f%%超过阈值%.1f%%", symbol, loss_pct * 100, self.max_stock_loss_pct * 100)
                return RiskCheckResult(allowed=True, adjusted_quantity=quantity, reason=f"风控强平: 亏损{loss_pct:.1%}")

        return RiskCheckResult(allowed=True, adjusted_quantity=quantity)

    def get_status(self) -> dict:
        return {
            "enabled": self.enabled,
            "circuit_breaker": self._circuit_breaker,
            "circuit_breaker_reason": self._circuit_breaker_reason,
            "peak_value": self._peak_value,
            "max_position_pct": self.max_position_pct,
            "max_positions": self.max_positions,
            "max_drawdown_pct": self.max_drawdown_pct,
            "max_daily_loss_pct": self.max_daily_loss_pct,
            "max_stock_loss_pct": self.max_stock_loss_pct,
        }
