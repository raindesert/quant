# 量化股票交易系统

A股量化交易系统，支持回测、实时行情和模拟交易。

## 功能特性

- **数据获取**: AKShare + 腾讯财经（自动降级），无需 API Key
- **回测引擎**: 事件驱动，支持历史数据回测，消除 look-ahead bias
- **策略框架**: 7种内置策略，支持自定义策略
- **增强指标**: 年化收益、最大回撤、夏普比率、Alpha、Beta、索提诺、Calmar、信息比率
- **风险管理**: 止损、止盈、仓位管理、组合熔断、强平机制
- **高级回测**: Walk-Forward 验证、Grid Search 参数优化、多股票组合回测
- **可视化**: matplotlib equity/drawdown 曲线 + Plotly 交互式图表 + Streamlit Web UI
- **本地缓存**: SQLite 单表 kdata(symbol,date) 持久化历史数据，支持增量更新
- **模拟交易**: Paper Trading 模拟真实交易环境
- **策略对比**: 并发批量回测，一键对比所有策略
- **并发执行**: 多股票批量回测自动并行加速

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

### 3. 风险参数

```bash
# 止损（跌5%自动卖出）
python main.py --mode backtest --symbol 000001.SZ --stop-loss 0.05

# 止盈（涨10%自动卖出）
python main.py --mode backtest --symbol 000001.SZ --take-profit 0.10

# 半仓操作
python main.py --mode backtest --symbol 000001.SZ --position-size 0.5
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
| KDJ | `--strategy kdj` | KDJ 随机指标，K<20 金叉买，K>80 死叉卖 |

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--mode` | 运行模式: backtest / portfolio / optimize / walk_forward / simulate / realtime |
| `--symbol` | 股票代码 |
| `--symbols` | 股票代码文件路径 |
| `--days` | 回测天数（默认250天） |
| `--strategy` | 策略名称（默认sma） |
| `--verbose` | 显示交易明细 |
| `--all-strategies` | 测试所有策略并对比 |
| `--stop-loss` | 止损比例（如0.05=5%） |
| `--take-profit` | 止盈比例（如0.10=10%） |
| `--position-size` | 仓位比例（0.0~1.0） |
| `--no-parallel` | 禁用并发批量回测 |
| `--output-json` | 导出汇总 JSON |
| `--output-csv` | 导出交易明细 CSV |
| `--chart` | 图表输出目录 |
| `--no-risk` | 禁用风控 |

## 回测指标说明

```
收益率      : 策略总收益率
年化收益    : 几何年化收益率
基准收益    : 买入持有策略收益
Alpha       : 策略相对基准的超额收益
Beta        : 策略相对基准的波动系数（>1 高波动，<1 低波动）
信息比率    : Alpha / 跟踪误差，衡量主动管理能力
最大回撤    : 历史最大亏损金额和比例
夏普比率    : 风险调整后收益（越大越好）
索提诺比    : 只用下行波动率，类似夏普（越大越好）
Calmar比    : 年化收益 / 最大回撤（越大越好）
年化波动    : 收益年化标准差
日收益分位  : P25/P50/P75 最佳/最差日收益
胜率        : 盈利交易占比
盈亏比      : 平均盈利 / 平均亏损
盈利因子    : 总盈利 / 总亏损（>1为好）
平均持仓    : 平均持有天数
```

## 项目结构

```
quant/
├── config/
│   └── settings.yaml       # 全局配置（佣金、印花税、风控阈值、Walk-Forward）
├── data/
│   ├── fetcher.py          # 数据获取（baostock → 腾讯 API → 本地缓存三级降级）
│   ├── cache.py            # SQLite 本地缓存（单表 kdata(symbol,date)）
│   └── processor.py        # 数据清洗 + 指标计算（MA/Bollinger/RSI/MACD/ATR/KDJ）
├── strategy/
│   ├── base.py             # 策略基类 + Signal 枚举
│   ├── registry.py         # 策略注册表
│   ├── params.py           # 参数持久化（保存/加载最优参数）
│   └── examples/           # 7 种内置策略
│       ├── sma.py / rsi.py / macd.py / bollinger.py
│       ├── momentum.py / mean_reversion.py / kdj.py
├── backtest/
│   ├── base.py             # 回测引擎基类（账户管理、指标计算）
│   ├── engine.py           # 单股事件驱动回测（止损/止盈/T+1/涨跌停）
│   ├── portfolio.py        # 多股组合回测
│   ├── walk_forward.py     # Walk-Forward 验证
│   ├── optimizer.py        # Grid Search 参数优化
│   └── output.py           # JSON/CSV 导出 + 图表
├── risk/
│   └── manager.py          # 风控（仓位/回撤/日亏/个股熔断）
├── broker/
│   └── simulator.py        # 模拟券商
├── monitor/
│   └── realtime.py         # 实时盯盘
├── utils/
│   └── logger.py           # 日志
├── tests/                  # 单元测试（含本迭代的 optimization_fixes + cache_v2）
├── charts/                 # 图表输出
├── app.py                  # Streamlit Web UI
├── main.py                 # CLI 主入口
└── symbols.txt             # 默认股票代码集
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
