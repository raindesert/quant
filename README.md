# 量化股票交易系统

A股量化交易系统，支持回测、实时行情和模拟交易。

## 功能特性

- **数据获取**: 腾讯财经 API 实时行情，无需 API Key
- **回测引擎**: 事件驱动，支持历史数据回测
- **策略框架**: 6种内置策略，支持自定义策略
- **模拟交易**: Paper Trading 模拟真实交易环境
- **策略对比**: 一键测试所有策略并对比收益

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 运行回测

```bash
# 单股票回测
python main.py --mode backtest --symbol 000001.SZ

# 显示交易明细
python main.py --mode backtest --symbol 000001.SZ --verbose

# 测试所有策略
python main.py --mode backtest --symbol 000001.SZ --all-strategies

# 批量回测
python main.py --mode backtest --symbols symbols.txt
```

## 策略列表

| 策略 | 命令 | 说明 |
|------|------|------|
| SMA | `--strategy sma` | 双均线，金叉买死叉卖 |
| RSI | `--strategy rsi` | RSI超卖买，超买卖 |
| MACD | `--strategy macd` | MACD金叉买，死叉卖 |
| Bollinger | `--strategy bollinger` | 布林带，突破下轨买，上轨卖 |
| Momentum | `--strategy momentum` | 动量，追涨杀跌 |
| MeanReversion | `--strategy mean_reversion` | 均值回归，偏离均线太多反向操作 |

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--mode` | 运行模式: backtest / simulate / realtime |
| `--symbol` | 股票代码 |
| `--symbols` | 股票代码文件路径 |
| `--days` | 回测天数（默认250天） |
| `--strategy` | 策略名称（默认sma） |
| `--verbose` | 显示交易明细 |
| `--all-strategies` | 测试所有策略并对比 |

## 项目结构

```
quant/
├── config/          # 配置文件
├── data/            # 数据获取和处理
├── strategy/        # 交易策略
├── backtest/        # 回测引擎
├── broker/          # 模拟券商
├── monitor/         # 实时监控
├── symbols.txt      # 股票代码集
└── main.py          # 主入口
```

## 开发策略

```python
from strategy.base import BaseStrategy, Signal

class MyStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("MyStrategy")

    def on_bar(self, bar: dict) -> str:
        # bar 包含: symbol, date, open, high, low, close, volume
        return Signal.BUY  # 或 SELL / HOLD
```

## 股票代码

- 深圳: `000001.SZ`
- 上海: `600000.SH`
