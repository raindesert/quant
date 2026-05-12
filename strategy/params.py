"""策略参数持久化管理 — 保存/加载优化后的参数。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("quant")

DEFAULT_PARAMS_DIR = Path(__file__).parent.parent / "params"


def save_params(
    strategy_name: str,
    params: dict[str, Any],
    symbol: str = "",
    score: float | None = None,
    metric: str = "",
    path: str | Path | None = None,
) -> Path:
    """保存策略参数到 JSON 文件。

    Args:
        strategy_name: 策略名称
        params: 参数字典
        symbol: 股票代码（用于文件名）
        score: 优化得分
        metric: 优化指标
        path: 自定义保存路径（默认保存到 params/ 目录）
    """
    if path is None:
        DEFAULT_PARAMS_DIR.mkdir(parents=True, exist_ok=True)
        suffix = f"_{symbol}" if symbol else ""
        path = DEFAULT_PARAMS_DIR / f"{strategy_name}{suffix}.json"
    else:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "strategy": strategy_name,
        "symbol": symbol,
        "params": params,
        "score": score,
        "metric": metric,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info("参数已保存: %s", path)
    return path


def load_params(
    strategy_name: str,
    symbol: str = "",
    path: str | Path | None = None,
) -> dict[str, Any] | None:
    """加载策略参数。

    Args:
        strategy_name: 策略名称
        symbol: 股票代码
        path: 自定义加载路径

    Returns:
        参数字典，或 None（文件不存在时）
    """
    if path is None:
        suffix = f"_{symbol}" if symbol else ""
        path = DEFAULT_PARAMS_DIR / f"{strategy_name}{suffix}.json"
    else:
        path = Path(path)

    if not path.exists():
        logger.warning("参数文件不存在: %s", path)
        return None

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    logger.info("参数已加载: %s → %s", path, data.get("params", {}))
    return data.get("params", {})


def list_saved_params() -> list[dict[str, Any]]:
    """列出所有已保存的参数文件。"""
    if not DEFAULT_PARAMS_DIR.exists():
        return []

    results = []
    for file in DEFAULT_PARAMS_DIR.glob("*.json"):
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["file"] = str(file.name)
            results.append(data)
        except Exception:
            pass
    return results
