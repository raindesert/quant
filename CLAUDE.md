# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 完整项目说明、命令、策略列表、架构见 [AGENTS.md](./AGENTS.md)。本文件仅补充 Claude Code 协作时需要的额外约定。

## Claude Code 协作约定

- **优先复用现有模块**：加新指标 → `backtest/base.py`；新策略 → `strategy/examples/`；新数据源 → `data/fetcher.py`。
- **测试约定**：项目 pytest + pandas，但本地若无依赖可用 `python3 -m unittest tests.test_optimization_fixes tests.test_cache_v2` 跑零依赖测试。
- **写中文注释**：代码注释和 docstring 用中文（与现有风格一致）。
- **修改前先 git log/blame 看历史**：项目有 21 次 commit，按 `测试适配 → 重构 → 新功能` 节奏演进，避免大改破坏 61 个测试。
- **Streamlit UI 入口**：`app.py`（`streamlit run app.py`）；CLI 入口：`main.py`。
- **本地缓存路径**：`~/.quant/cache/market.db`（v2 单表 kdata）。
