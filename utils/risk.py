"""风险指标工具 — VaR / CVaR / 最大回撤 / 连续亏损。

输入: equity_curve = [{"date": str/datetime, "value": float}, ...]
      或简单 list[float] (纯值)

所有函数纯函数, 无副作用, 可测。
"""
from typing import Sequence, Union
from datetime import datetime
import math

Number = Union[int, float]


def _to_returns(equity_curve) -> list[float]:
    """把 equity_curve 转成简单收益率序列 r_t = (v_t - v_{t-1}) / v_{t-1}。

    支持两种输入:
    - [{"date": ..., "value": ...}, ...]  →  提取 value
    - [v0, v1, v2, ...]                   →  直接用
    """
    if not equity_curve:
        return []
    if isinstance(equity_curve[0], dict):
        values = [e["value"] for e in equity_curve]
    else:
        values = list(equity_curve)
    if len(values) < 2:
        return []
    returns = []
    for i in range(1, len(values)):
        prev, cur = values[i - 1], values[i]
        if prev > 0:
            returns.append((cur - prev) / prev)
        else:
            returns.append(0.0)
    return returns


def value_at_risk(
    equity_curve,
    confidence: float = 0.95,
    method: str = "historical",
) -> float:
    """历史 VaR (Value at Risk) — 在给定置信度下最差的可能损失。

    例: 95% VaR = -0.02 表示 95% 置信度下最大单日损失不会超过 2%。
    返回负数或 0（损失）。

    method: 'historical' | 'parametric' (假设正态分布)
    """
    rets = _to_returns(equity_curve)
    if not rets:
        return 0.0
    if method == "parametric":
        import statistics
        mu = statistics.mean(rets)
        sigma = statistics.stdev(rets) if len(rets) > 1 else 0.0
        # z-score: 95% = -1.645, 99% = -2.326
        z_table = {0.90: -1.282, 0.95: -1.645, 0.99: -2.326}
        z = z_table.get(confidence, -1.645)
        return mu + z * sigma
    # historical: 取分位数
    sorted_rets = sorted(rets)
    idx = max(0, int(len(sorted_rets) * (1 - confidence)) - 1)
    return -sorted_rets[idx]  # 负数表示损失


def conditional_var(
    equity_curve,
    confidence: float = 0.95,
) -> float:
    """CVaR / Expected Shortfall — VaR 之外的平均损失（更严格）。

    返回负数。
    """
    rets = _to_returns(equity_curve)
    if not rets:
        return 0.0
    sorted_rets = sorted(rets)
    cutoff = max(1, int(len(sorted_rets) * (1 - confidence)))
    tail = sorted_rets[:cutoff]
    if not tail:
        return 0.0
    return -sum(tail) / len(tail)


def max_drawdown(equity_curve) -> dict:
    """最大回撤 + 持续期 + 恢复期。

    返回: {
      "max_drawdown": float (负数, 比例),
      "max_drawdown_pct": str (e.g. "-12.34%"),
      "peak_idx": int,
      "trough_idx": int,
      "recovery_idx": int | None,  # None 还没恢复
      "drawdown_duration": int (peak→trough 天数),
      "recovery_duration": int | None,
    }
    """
    if not equity_curve:
        return {
            "max_drawdown": 0.0, "max_drawdown_pct": "0.00%",
            "peak_idx": -1, "trough_idx": -1, "recovery_idx": None,
            "drawdown_duration": 0, "recovery_duration": None,
        }
    if isinstance(equity_curve[0], dict):
        values = [e["value"] for e in equity_curve]
    else:
        values = list(equity_curve)

    peak = values[0]
    peak_idx = 0
    max_dd = 0.0
    max_dd_peak = 0
    max_dd_trough = 0
    for i, v in enumerate(values):
        if v > peak:
            peak = v
            peak_idx = i
        dd = (v - peak) / peak if peak > 0 else 0
        if dd < max_dd:
            max_dd = dd
            max_dd_peak = peak_idx
            max_dd_trough = i

    # 找恢复点
    recovery_idx = None
    for i in range(max_dd_trough + 1, len(values)):
        if values[i] >= values[max_dd_peak]:
            recovery_idx = i
            break

    return {
        "max_drawdown": max_dd,
        "max_drawdown_pct": f"{max_dd * 100:.2f}%",
        "peak_idx": max_dd_peak,
        "trough_idx": max_dd_trough,
        "recovery_idx": recovery_idx,
        "drawdown_duration": max_dd_trough - max_dd_peak,
        "recovery_duration": (recovery_idx - max_dd_trough) if recovery_idx is not None else None,
    }


def max_consecutive_losses(equity_curve) -> dict:
    """最大连续亏损回合 + 次数。

    1. 从 equity_curve 算日收益率
    2. 找连续 < 0 的最长段
    """
    rets = _to_returns(equity_curve)
    if not rets:
        return {"max_count": 0, "max_loss_sum": 0.0, "max_loss_pct": "0.00%"}

    max_count = 0
    max_loss_sum = 0.0
    cur_count = 0
    cur_loss = 0.0
    for r in rets:
        if r < 0:
            cur_count += 1
            cur_loss += r
        else:
            if cur_count > max_count:
                max_count = cur_count
                max_loss_sum = cur_loss
            cur_count = 0
            cur_loss = 0.0
    # 收尾
    if cur_count > max_count:
        max_count = cur_count
        max_loss_sum = cur_loss

    return {
        "max_count": max_count,
        "max_loss_sum": max_loss_sum,
        "max_loss_pct": f"{max_loss_sum * 100:.2f}%",
    }


def rolling_sharpe(equity_curve, window: int = 20) -> list:
    """滚动夏普（年化）。返回 0/0 填充开窗前期。"""
    rets = _to_returns(equity_curve)
    if len(rets) < 2:
        return [0.0] * len(rets)
    import statistics
    out = [0.0] * len(rets)
    for i in range(window, len(rets) + 1):
        win = rets[i - window:i]
        mu = statistics.mean(win)
        sigma = statistics.stdev(win) if len(win) > 1 else 0.0
        sharpe = (mu / sigma * math.sqrt(252)) if sigma > 0 else 0.0
        out[i - 1] = sharpe
    return out


def rolling_volatility(equity_curve, window: int = 20) -> list:
    """滚动波动率（年化 %）。"""
    rets = _to_returns(equity_curve)
    if len(rets) < 2:
        return [0.0] * len(rets)
    import statistics
    out = [0.0] * len(rets)
    for i in range(window, len(rets) + 1):
        win = rets[i - window:i]
        sigma = statistics.stdev(win) if len(win) > 1 else 0.0
        out[i - 1] = sigma * math.sqrt(252) * 100  # 年化 %
    return out


def monte_carlo_simulation(
    equity_curve,
    n_sims: int = 1000,
    n_days: int = 252,
    seed: int = 42,
) -> list:
    """蒙特卡洛模拟 — 基于历史均值/波动率生成 n_sims 条路径。

    每条路径: n_days + 1 个点（起始=最后值）
    返回: list[list[float]], len=n_sims, 每条 len=n_days+1
    """
    import random
    rets = _to_returns(equity_curve)
    if not rets or len(rets) < 2:
        return []

    import statistics
    mu = statistics.mean(rets)
    sigma = statistics.stdev(rets)

    last_value = (
        equity_curve[-1]["value"] if isinstance(equity_curve[-1], dict)
        else equity_curve[-1]
    )

    rng = random.Random(seed)
    paths = []
    for _ in range(n_sims):
        path = [last_value]
        v = last_value
        for _ in range(n_days):
            r = rng.gauss(mu, sigma)
            v = v * (1 + r)
            path.append(v)
        paths.append(path)
    return paths


def summary_risk_metrics(equity_curve) -> dict:
    """一键算所有风险指标。"""
    var95 = value_at_risk(equity_curve, 0.95)
    var99 = value_at_risk(equity_curve, 0.99)
    cvar95 = conditional_var(equity_curve, 0.95)
    cvar99 = conditional_var(equity_curve, 0.99)
    mdd = max_drawdown(equity_curve)
    mcl = max_consecutive_losses(equity_curve)
    return {
        "VaR 95%": var95,
        "VaR 99%": var99,
        "CVaR 95%": cvar95,
        "CVaR 99%": cvar99,
        "最大回撤": mdd,
        "最大连续亏损": mcl,
    }
