# 量化股票交易系统

A股量化交易系统，支持回测、实时行情、模拟交易、参数优化、Web UI。

## 功能特性

### 核心
- **数据获取**: baostock + 腾讯财经（自动降级），无需 API Key
- **回测引擎**: 事件驱动，支持日线/分钟级，消除 look-ahead bias
- **策略框架**: 7 种内置策略，支持自定义
- **增强指标**: 年化收益、最大回撤、夏普、Alpha、Beta、索提诺、Calmar、信息比率
- **风险管理**: 止损、止盈、仓位管理、组合熔断、强平
- **本地缓存**: SQLite 单表 kdata(symbol,date) 增量更新

### 高级回测
- **Walk-Forward 验证**: 防过拟合
- **参数优化**: Grid 暴力 / Random 随机 / Bayesian 贝叶斯 (TPE) 三种方法
- **多策略组合**: N 个子策略加权汇总 + 组合级指标
- **多股票组合回测**: 并发批量

### 报告 & UI
- **HTML 报告**: 单文件自包含（CSS + base64 图表），可邮件分享
- **JSON/CSV 导出**: 完整数据导出
- **Streamlit Web UI**: 7 个页面（单策略/对比/优化/多策略/YAML/WF/实时）
- **可视化**: matplotlib + Plotly 交互式图表

### 工程
- **YAML 配置预设**: 复用常用回测配置（`--config/--save-config`）
- **CLI 优先于 YAML**: 命令行参数覆盖 YAML 字段

## 快速开始

### 1. 安装

```bash
pip install -r requirements.txt
```

### 2. 基础回测

```bash
# 单策略回测
python main.py --mode backtest --symbol 000001.SZ --strategy sma --days 250

# 显示交易明细
python main.py --mode backtest --symbol 000001.SZ --verbose

# 批量回测
python main.py --mode backtest --symbols symbols.txt

# 风控参数
python main.py --mode backtest --symbol 000001.SZ --stop-loss 0.05 --take-profit 0.10 --position-size 0.5
```

### 3. Web UI

```bash
streamlit run app.py
# 浏览器打开 http://localhost:8501
```

## 高级功能

### 分钟级回测 (P1-1)

```bash
# 5 分钟线，自动禁用 T+1 和涨跌停检查
python main.py --mode backtest --symbol 000001.SZ --frequency m5 --strategy sma

# 支持的频率: day / m1 / m5 / m15 / m30 / m60
# 注意：腾讯分钟接口只能拉当天数据，历史分钟需 baostock 分钟接口
```

### 贝叶斯/随机参数优化 (P1-3)

```bash
# Grid 暴力搜索
python main.py --mode optimize --symbol 000001.SZ --strategy sma --optimize-method grid

# Bayesian 贝叶斯（TPE 采样，50 trials）
python main.py --mode optimize --symbol 000001.SZ --strategy sma \
    --optimize-method bayesian --optimize-trials 50

# Random 随机搜索
python main.py --mode optimize --symbol 000001.SZ --strategy sma \
    --optimize-method random --optimize-trials 100
```

贝叶斯在大范围参数时显著优于 Grid。需 `pip install optuna`。

### YAML 配置预设 (P1-5)

```bash
# 用预设启动
python main.py --config presets/my_strategy.yaml

# 把当前 CLI 参数保存为预设
python main.py --symbol 000001.SZ --strategy sma --days 120 --stop-loss 0.05 \
    --save-config presets/sma_btc.yaml
```

YAML 文件格式（`presets/sma_btc.yaml`）：

```yaml
mode: backtest
strategy: sma
symbol: "000001.SZ"
days: 250
stop_loss: 0.05
take_profit: 0.10
position_size: 1.0
risk:
  max_position_pct: 0.20
  max_drawdown_pct: 0.15
```

CLI 优先级 > YAML。

### HTML 报告 (P2-4)

```bash
# 单策略 HTML 报告
python main.py --mode backtest --symbol 000001.SZ --output-html reports/

# 生成的 reports/000001.SZ_report.html 是自包含文件（CSS + 图表内嵌）
# 可直接用浏览器打开、邮件分享
```

包含：关键指标卡片 + 权益曲线 + 回撤曲线 + 交易明细表。

### 多策略并行组合 (P2-2)

```bash
# 3 个子策略加权组合
python main.py --mode multi_strategy --symbol 000001.SZ \
    --strategies sma,rsi,bollinger --weights 0.4,0.3,0.3

# 加 HTML 报告
python main.py --mode multi_strategy --symbol 000001.SZ \
    --strategies sma,rsi,bollinger --weights 0.4,0.3,0.3 \
    --output-html reports/multi.html
```

权重总和必须 = 1.0；省略 `--weights` 则等分。

### 并发批量回测

```bash
# 批量回测 5 股票 × 2 策略 = 10 个任务（自动并发）
python main.py --mode backtest --symbols symbols.txt --strategy sma --strategy rsi
```

## 策略列表

| 策略 | 命令 | 说明 |
|------|------|------|
| SMA | `--strategy sma` | 双均线，金叉买死叉卖 |
| RSI | `--strategy rsi` | RSI 超卖买，超买卖 |
| MACD | `--strategy macd` | MACD 金叉买，死叉卖 |
| Bollinger | `--strategy bollinger` | 布林带，突破下轨买，上轨卖 |
| Momentum | `--strategy momentum` | 动量，追涨杀跌 |
| MeanReversion | `--strategy mean_reversion` | 均值回归，偏离均线反向操作 |
| KDJ | `--strategy kdj` | 随机指标，K<20 金叉买，K>80 死叉卖 |

## 命令行参数

### 基础

| 参数 | 说明 |
|------|------|
| `--mode` | backtest / optimize / walkforward / multi_strategy / simulate / realtime |
| `--symbol` | 股票代码（000001.SZ） |
| `--symbols` | 股票代码文件路径 |
| `--days` | 回测天数（默认 250） |
| `--strategy` | 策略名（默认 sma） |
| `--verbose` | 显示交易明细 |
| `--all-strategies` | 测试所有策略并对比 |

### 交易参数

| 参数 | 说明 |
|------|------|
| `--stop-loss` | 止损比例（0.05 = 5%） |
| `--take-profit` | 止盈比例（0.10 = 10%） |
| `--position-size` | 仓位比例 0.0~1.0（默认 1.0） |
| `--slippage` | 滑点（百分比或固定金额） |
| `--no-t1` | 禁用 T+1（允许当日买卖） |
| `--no-limit` | 禁用涨跌停判断 |
| `--no-risk` | 禁用风控模块 |

### 数据 & 频率

| 参数 | 说明 |
|------|------|
| `--frequency` | day / m1 / m5 / m15 / m30 / m60 |
| `--start-date` / `--end-date` | 日期范围过滤 |

### 优化

| 参数 | 说明 |
|------|------|
| `--optimize-method` | grid / random / bayesian |
| `--optimize-trials` | 随机/贝叶斯采样次数（默认 50） |
| `--optimize-metric` | 优化目标：profit_pct / sharpe_ratio / profit_factor / max_drawdown_pct / win_rate |
| `--optimize-top` | 排行榜显示前 N 名 |
| `--optimize-workers` | 并发进程数（默认 4） |

### 多策略组合

| 参数 | 说明 |
|------|------|
| `--strategies` | 策略列表（逗号分隔）`sma,rsi,bollinger` |
| `--weights` | 权重（逗号分隔，总和 = 1.0）`0.4,0.3,0.3` |

### 输出

| 参数 | 说明 |
|------|------|
| `--output-json` | 导出汇总 JSON 文件 |
| `--output-csv` | 导出交易明细 CSV |
| `--output-html` | 导出单文件 HTML 报告（目录或 .html 路径） |
| `--chart` | 图表输出目录 |
| `--no-html-trades` | HTML 报告不含交易明细（更小） |

### 配置

| 参数 | 说明 |
|------|------|
| `--config PATH` | 加载 YAML/JSON 预设 |
| `--save-config PATH` | 把当前 CLI 参数保存为 YAML |

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
盈利因子    : 总盈利 / 总亏损（>1 为好）
平均持仓    : 平均持有天数
```

## Web UI

`streamlit run app.py` 启动 7 个页面：

| 页面 | 说明 |
|------|------|
| 📊 单策略回测 | 含分钟级 + HTML 报告下载 |
| ⚔️ 策略对比 | 并发回测多个策略 |
| 🔧 参数优化 | grid / random / bayesian |
| 🔀 多策略组合 | 多策略加权 + 组合级指标 |
| 📁 YAML 预设 | 加载/创建/一键回测 |
| 🔄 Walk-Forward | 滚动窗口验证 |
| 📡 实时行情 | 盯盘 + 自动刷新 |

## 项目结构

```
quant/
├── config/
│   ├── settings.yaml       # 全局配置（佣金/印花税/风控阈值/Walk-Forward）
│   └── loader.py           # 用户配置加载/合并/保存（--config/--save-config）
├── data/
│   ├── fetcher.py          # 数据获取（baostock → 腾讯 → 本地缓存三级降级）
│   │                        # 支持 day/m1/m5/m15/m30/m60 频率
│   ├── cache.py            # SQLite 本地缓存（单表 kdata）
│   └── processor.py        # 数据清洗 + 指标（MA/Bollinger/RSI/MACD/ATR/KDJ）
├── strategy/
│   ├── base.py             # 策略基类 + Signal 枚举
│   ├── registry.py         # 策略注册表
│   ├── params.py           # 参数持久化（保存/加载最优参数）
│   └── examples/           # 7 种内置策略
│       ├── sma.py / rsi.py / macd.py / bollinger.py
│       └── momentum.py / mean_reversion.py / kdj.py
├── backtest/
│   ├── base.py             # 回测引擎基类（账户管理、指标计算 O(N)）
│   ├── engine.py           # 单股事件驱动回测（止损/止盈/T+1/涨跌停/分钟级）
│   ├── portfolio.py        # 多股组合回测
│   ├── walk_forward.py     # Walk-Forward 验证
│   ├── optimizer.py        # Grid / Random / Bayesian (TPE) 优化
│   ├── multi_strategy.py   # 多策略并行组合 + 加权汇总
│   └── output.py           # JSON/CSV/HTML 导出 + matplotlib/plotly 图表
├── risk/
│   └── manager.py          # 风控（仓位/回撤/日亏/个股熔断）
├── broker/
│   └── simulator.py        # 模拟券商
├── monitor/
│   └── realtime.py         # 实时盯盘
├── utils/
│   └── logger.py           # 日志
├── tests/                  # 单元测试 + 端到端测试 (235 个测试)
│   ├── test_minute_freq.py       # P1-1 分钟级
│   ├── test_optimizer_bayes.py   # P1-3 贝叶斯
│   ├── test_config_loader.py     # P1-5 YAML
│   ├── test_html_report.py       # P2-4 HTML
│   ├── test_multi_strategy.py    # P2-2 多策略
│   └── test_app_e2e.py           # Streamlit 端到端
├── presets/                # YAML 预设目录
├── params/                 # 优化保存的最优参数
├── reports/                # HTML 报告输出
├── app.py                  # Streamlit Web UI (7 pages)
├── main.py                 # CLI 主入口
├── symbols.txt             # 默认股票代码集
└── requirements.txt        # 依赖
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

## 测试

```bash
# 跑全套测试（基线 235/235 通过）
python3 -m pytest tests/

# 跑特定模块
python3 -m pytest tests/test_minute_freq.py
python3 -m pytest tests/test_optimizer_bayes.py
python3 -m pytest tests/test_html_report.py
python3 -m pytest tests/test_multi_strategy.py
python3 -m pytest tests/test_app_e2e.py
```

## 股票代码

- 深圳: `000001.SZ`
- 上海: `600000.SH`
