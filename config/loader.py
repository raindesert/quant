"""回测/优化配置加载器 — 支持 YAML 和 JSON 预设。

用法：
    # 写一个 presets/sma_btc.yaml
    mode: backtest
    strategy: sma
    days: 250
    stop_loss: 0.05
    take_profit: 0.10
    optimize_method: bayesian
    optimize_trials: 100
    risk:
      max_position_pct: 0.20
      max_drawdown_pct: 0.15

    # CLI 覆盖
    python main.py --config presets/sma_btc.yaml
    python main.py --config presets/sma_btc.yaml --days 500  # 覆盖 days
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SUPPORTED_FORMATS = ("yaml", "yml", "json")

# 允许的顶层字段（其他字段警告但不报错）
ALLOWED_KEYS = {
    "mode", "strategy", "symbol", "symbols", "symbols_file",
    "days", "frequency", "start_date", "end_date",
    "initial_cash", "commission", "stamp_tax", "min_commission",
    "stop_loss", "take_profit", "position_size",
    "slippage", "slippage_type",
    "enforce_t_plus_1", "check_limit", "no_t1", "no_limit",
    "no_risk", "risk_enabled",
    "optimize_method", "optimize_metric", "optimize_trials",
    "optimize_top", "optimize_workers",
    "walk_forward", "portfolio", "max_positions",
    "all_strategies", "verbose",
    "no_parallel",
    "output_json", "output_csv", "chart",
    "load_params",
    "risk",  # 嵌套 dict
    "optimize",  # 嵌套 dict
    "param",  # 列表
}


def detect_format(path: str | Path) -> str:
    """从文件后缀推断格式。"""
    suffix = Path(path).suffix.lower().lstrip(".")
    if suffix in SUPPORTED_FORMATS:
        return suffix
    raise ValueError(
        f"无法识别配置文件格式: {suffix!r}。支持: {SUPPORTED_FORMATS}"
    )


def load_config(path: str | Path) -> dict[str, Any]:
    """从 YAML/JSON 文件加载配置。

    Args:
        path: 配置文件路径

    Returns:
        顶层 dict；缺失/空返回 {}

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 文件格式不支持或内容不是 dict
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")

    fmt = detect_format(p)
    text = p.read_text(encoding="utf-8")

    if fmt == "json":
        data = json.loads(text) if text.strip() else {}
    else:
        # yaml
        try:
            import yaml
        except ImportError:
            raise ImportError(
                "YAML 配置需要 PyYAML：pip install pyyaml"
            )
        data = yaml.safe_load(text) if text.strip() else {}

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(
            f"配置根必须是 dict，实际是 {type(data).__name__}"
        )

    # 警告未知字段（不阻断）
    _check_unknown_keys(data)

    return data


def _check_unknown_keys(data: dict) -> list[str]:
    """返回未知键列表（供 main.py 日志告警）。"""
    unknown = []
    for k in data.keys():
        if k not in ALLOWED_KEYS:
            unknown.append(k)
    return unknown


def merge_config_with_args(
    config: dict[str, Any], args: Any
) -> Any:
    """把 YAML 配置填入 argparse Namespace，只在 args 里是 None 的字段填。

    即：CLI 显式传的参数优先级 > YAML 默认值。

    Args:
        config: 来自 YAML 的 dict
        args: argparse.Namespace

    Returns:
        修改后的 Namespace（同对象）
    """
    if not config:
        return args

    for key, value in config.items():
        # 嵌套 dict（risk/optimize）单独处理
        if key in ("risk", "optimize") and isinstance(value, dict):
            # 把嵌套字段提升到顶层（用 _ 前缀避免和 CLI 冲突）
            for sub_k, sub_v in value.items():
                setattr(args, f"cfg_{key}_{sub_k}", sub_v)
            continue

        # 列表字段（param / symbols）
        if key == "symbols_file":
            key = "symbols"  # 别名

        # 已经是 args 的属性吗？
        if not hasattr(args, key):
            # 不认识的键：跳过（已经 warning 过）
            continue

        current = getattr(args, key, None)
        # 只在 CLI 未设置时填（即 current 是 None 或空 list）
        if current is None:
            setattr(args, key, value)
        elif isinstance(current, list) and not current and isinstance(value, list):
            setattr(args, key, value)

    return args


def example_config() -> str:
    """返回示例 YAML 字符串（用于 --help 文档）。"""
    return """\
# 回测配置预设示例（保存为 presets/my_strategy.yaml）
mode: backtest                  # backtest / portfolio / optimize / walk_forward / simulate
strategy: sma                   # 策略名
symbol: "000001.SZ"             # 股票代码
days: 250                       # 回测天数
frequency: day                  # day / m1 / m5 / m15 / m30 / m60
stop_loss: 0.05                 # 5% 止损
take_profit: 0.10               # 10% 止盈
position_size: 1.0              # 满仓
slippage: 0.001                 # 0.1% 滑点
risk:                           # 风控嵌套配置
  max_position_pct: 0.20
  max_drawdown_pct: 0.15
  max_daily_loss_pct: 0.03
output_json: "result.json"      # 导出 JSON
chart: "charts"                 # 图表目录

# 优化任务示例
# mode: optimize
# optimize_method: bayesian    # grid / random / bayesian
# optimize_trials: 100
# param:
#   - "fast=5,10,15,20"
#   - "slow=30,60,120"
"""


# 序列化为 YAML 时不写的字段（CLI 内部状态）
_SAVE_SKIP = {
    "config", "save_config", "no_t1", "no_limit", "no_risk",
    "no_parallel", "portfolio", "all_strategies", "verbose", "load_params",
}


def save_args_as_config(args: Any, path: str | Path) -> Path:
    """把 argparse Namespace 保存为 YAML 配置预设。

    只写非 None 字段；跳过 bool flag / 路径类型元数据。
    """
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {}
    if not hasattr(args, "__dict__"):
        raise TypeError("args 必须是 argparse.Namespace 或类似对象")

    for key, value in vars(args).items():
        # 跳过内部 / 派生字段
        if key in _SAVE_SKIP:
            continue
        if key.startswith("_"):
            continue
        if key.startswith("cfg_"):
            # 把 cfg_risk_xxx 折回 risk dict
            continue
        if value is None:
            continue
        # bool flag (store_true) 在没显式设时是 False，false 写出来会反转
        # 不存默认值 False，避免混淆
        if value is False and isinstance(value, bool):
            continue
        data[key] = value

    if out_path.suffix.lower() in (".yaml", ".yml"):
        import yaml
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.dump(
                data, f, allow_unicode=True, default_flow_style=False, sort_keys=False
            )
    else:
        import json
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return out_path
