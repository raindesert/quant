# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目概述

A股量化股票交易系统，支持回测、实时行情和模拟交易。

## 常用命令

```bash
# 回测（默认250天）
python main.py --mode backtest --symbol 000001.SZ

# 指定策略和天数
python main.py --mode backtest --symbol 000001.SZ --days 120 --strategy sma

# 显示交易明细
python main.py --mode backtest --symbol 000001.SZ --verbose

# 测试所有策略并对比（并发）
python main.py --mode backtest --symbol 000001.SZ --all-strategies

# 批量回测（从文件读取股票代码）
python main.py --mode backtest --symbols symbols.txt --strategy rsi

# 批量测试所有策略
python main.py --mode backtest --symbols symbols.txt --all-strategies

# 止损+止盈
python main.py --mode backtest --symbol 000001.SZ --stop-loss 0.05 --take-profit 0.10

# 半仓+并发
python main.py --mode backtest --symbols symbols.txt --strategy sma --position-size 0.5

# 模拟交易
python main.py --mode simulate --symbol 000001.SZ --strategy bollinger

# 实时盯盘
python main.py --mode realtime --symbol 000001.SZ
```

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--mode` | 运行模式: backtest / simulate / realtime |
| `--symbol` | 股票代码，如 000001.SZ |
| `--symbols` | 股票代码文件路径 |
| `--days` | 回测天数，默认250天 |
| `--strategy` | 策略名称，默认 sma |
| `--verbose` | 显示交易明细 |
| `--all-strategies` | 测试所有策略并对比（并发） |
| `--stop-loss` | 止损比例（如0.05=5%） |
| `--take-profit` | 止盈比例（如0.10=10%） |
| `--position-size` | 仓位比例 0.0~1.0（默认1.0） |
| `--no-parallel` | 禁用并发批量回测 |

## 策略列表

| 策略 | 命令 | 说明 |
|------|------|------|
| SMA | `--strategy sma` | 双均线，金叉买死叉卖 |
| RSI | `--strategy rsi` | RSI超卖买，超买卖 |
| MACD | `--strategy macd` | MACD金叉买，死叉卖 |
| Bollinger | `--strategy bollinger` | 布林带，突破下轨买，上轨卖 |
| Momentum | `--strategy momentum` | 动量，追涨杀跌 |
| MeanReversion | `--strategy mean_reversion` | 均值回归，偏离均线太多反向操作 |

## 数据来源

- **主数据源**: 腾讯财经 API（`web.ifzq.gtimg.cn`）
- **实时行情**: `qt.gtimg.cn`
- **降级**: 模拟数据（网络不可用时）

无需 API Key。

## 架构说明

```
quant/
├── config/settings.yaml    # 全局配置
├── data/
│   ├── fetcher.py         # 数据获取
│   └── processor.py       # 数据处理（MA、Bollinger、RSI）
├── strategy/examples/     # 策略实现
│   ├── sma.py            # 双均线
│   ├── rsi.py            # RSI
│   ├── macd.py           # MACD
│   ├── bollinger.py      # 布林带
│   ├── momentum.py       # 动量
│   └── mean_reversion.py # 均值回归
├── backtest/engine.py     # 回测引擎
├── broker/simulator.py    # 模拟券商
├── monitor/realtime.py    # 实时监控
├── symbols.txt           # 股票代码集
└── main.py               # 主入口
```

## 配置说明

`config/settings.yaml`:

```yaml
market: cn
default_symbol: "000001.SZ"
initial_cash: 1000000

backtest:
  commission: 0.0003
  days: 250
```

## 股票代码格式

- 深圳: `000001.SZ`
- 上海: `600000.SH`
