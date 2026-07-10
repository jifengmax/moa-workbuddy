---
name: moa
description: "Mixture of Agents (MoA) — 多模型并行提案 + 聚合器综合。把同一个问题同时发给多个免费大模型，收集各自回答后用聚合模型综合出更准的最终答案。基于 Wang et al. (2024) arXiv:2406.04692。需要 OpenCode Zen 免费 API Key。"
version: 3.2.1
author: mantop2010 (adapted for WorkBuddy by 妙妙)
license: MIT
platforms: [linux, macos, windows]
tags: [moa, mixture-of-agents, multi-agent, free-models, opencode-zen]
display_name: "MoA — Mixture of Agents"
display_name_zh: "MoA 多智能体混合"
---

# MoA — Mixture of Agents（WorkBuddy 适配版）

把用户的问题**并行**发给多个参考模型（proposers），收集所有回答后用一个**聚合模型（aggregator）**综合成最终答案。论文证明这种方式比单一模型更准、更稳。

> 原仓库：`github.com/mantop2010/moa-free-models`（MIT）。本 skill 已适配 WorkBuddy，并修复了原版缺失 `debug_helpers` 依赖会导致崩溃的问题。

## 🔑 前置条件（必须）

1. 去 https://opencode.ai/auth 注册，拿到**免费** API Key。
2. 把 Key 放进环境变量或 `.env`：
   ```bash
   # 方式一：环境变量（推荐）
   export OPENCODE_ZEN_API_KEY="你的key"
   # 方式二：写入 ~/.env（脚本会自动读取）
   echo 'OPENCODE_ZEN_API_KEY=你的key' >> ~/.env
   ```
3. 确保 Python 有 `requests`：
   ```bash
   pip install requests
   ```

⚠️ **数据说明**：运行 MoA 时你的问题会发往第三方服务 `opencode.ai`（OpenCode Zen）。这是 MoA 的正常行为，请知悉你的 prompt 会离开本机。

## 🚀 用法

### 方式 A：作为 Python 工具直接跑（最常用）

当用户要求"用 MoA 解决这个问题 / 用多模型混合回答"时：

1. 确认 Key 已设置、已装 `requests`。
2. 用 Bash 运行：
   ```bash
   python "<skill_dir>/tools/mixture_of_agents_tool_free.py" "你的问题"
   ```
   其中 `<skill_dir>` 是本 skill 所在目录。脚本会并行调用 5 个免费参考模型，再用聚合模型综合，输出 JSON（`success / response / models_used / processing_time`）。
3. 把 `response` 字段的内容整理后回复用户。

### 方式 B：作为 Python 模块调用

```python
import asyncio
from tools.mixture_of_agents_tool_free import mixture_of_agents_tool

result = asyncio.run(mixture_of_agents_tool("你的复杂问题"))
print(result)
```

可自定义模型：
```python
result = asyncio.run(mixture_of_agents_tool(
    "你的问题",
    reference_models=["deepseek-v4-flash-free", "nemotron-3-ultra-free"],
    aggregator_model="deepseek-v4-flash-free",
))
```

## 🧠 架构

```
User Question
   │
   ├─→ Reference Model 1 (deepseek-v4-flash-free)
   ├─→ Reference Model 2 (nemotron-3-ultra-free)
   ├─→ Reference Model 3 (north-mini-code-free)
   ├─→ Reference Model 4 (mimo-v2.5-free)
   └─→ Reference Model 5 (big-pickle)
        │  (并行，各自独立回答)
        ▼
   Aggregator (deepseek-v4-flash-free)
   综合所有回答 → 批判性地提炼出最终答案
        ▼
   Final Response
```

## ✅ 默认使用的免费模型

| 模型 | 角色 | 说明 |
|---|---|---|
| `deepseek-v4-flash-free` | Aggregator + Reference | 综合质量最佳 |
| `nemotron-3-ultra-free` | Reference | 快速分析 |
| `north-mini-code-free` | Reference | 编码任务 |
| `mimo-v2.5-free` | Reference | 对话类 |
| `big-pickle` | Reference | 通用 |

> 测试发现 `qwen3.6-plus-free`、`minimax-m3-free` 会 401，**不要用**。

## 🔧 排错

- **缺 `requests`**：`pip install requests`。
- **Key 找不到**：确认 `OPENCODE_ZEN_API_KEY` 在环境变量或 `~/.env` 中；Windows 下 `.env` 内容为 `OPENCODE_ZEN_API_KEY=xxx`（无引号、无空格）。
- **某模型返回空**：部分模型把内容放在 `reasoning` / `reasoning_content` 字段，脚本已自动兼容。
- **5 个模型太慢**：在脚本里把 `REFERENCE_MODELS` 缩减，或设 `MIN_SUCCESSFUL_REFERENCES = 1`。

## 📁 文件结构

| 文件 | 用途 |
|---|---|
| `tools/mixture_of_agents_tool_free.py` | MoA 核心实现（可独立运行） |
| `references/SETUP.md` | 原版快速开始（面向 Hermes，仅供参考） |
| `references/model-test-results.md` | 模型实测结果 |

## 📜 License
MIT — 自由使用、分享。
