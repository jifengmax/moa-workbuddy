# MoA — Mixture of Agents（WorkBuddy 专属版）

把同一个问题**并行**发给多个免费大模型（proposers），收集所有回答后用一个**聚合模型（aggregator）**综合成最终答案。基于 Together AI / Wang et al. (2024) 的论文 [Mixture-of-Agents Enhances Large Language Model Capabilities](https://arxiv.org/abs/2406.04692)。

本仓库是 **WorkBuddy** 的技能（skill）版本，改自 [mantop2010/moa-free-models](https://github.com/mantop2010/moa-free-models)（MIT），并做了以下适配：

- ✅ 重写为 WorkBuddy 技能格式（中文 `SKILL.md`）
- ✅ 修复了原版缺失 `tools.debug_helpers` 依赖会导致崩溃的问题
- ✅ 给核心脚本补上命令行入口，可直接 `python` 运行
- ✅ 所有模型均使用 OpenCode Zen **免费**层，零成本

## 🔑 前置条件

1. 到 https://opencode.ai/auth 注册并获取**免费** API Key。
2. 设置环境变量（或写入 `~/.env`）：
   ```bash
   export OPENCODE_ZEN_API_KEY="你的key"
   ```
3. 安装依赖：
   ```bash
   pip install requests
   ```

> ⚠️ **数据说明**：运行 MoA 时，你的问题会发往第三方服务 `opencode.ai`（OpenCode Zen）。这是 MoA 机制本身决定的，请知悉你的 prompt 会离开本机，涉密内容慎用。

## 📦 安装到 WorkBuddy

### 一键安装（推荐）

```bash
# 方式一：直接克隆
git clone https://github.com/jifengmax/moa-workbuddy.git ~/.workbuddy/skills/moa

# 方式二：用仓库自带的 install.sh（自动克隆/更新 + 提示配置 Key）
bash <(curl -fsSL https://raw.githubusercontent.com/jifengmax/moa-workbuddy/master/install.sh)
```

或在 WorkBuddy 对话里说「用 MoA 解决这个问题：……」，由妙妙调用技能。

## 🚀 用法

### 直接运行（最常用）

```bash
python tools/mixture_of_agents_tool_free.py "你的问题"
```

输出 JSON：`{ success, response, models_used, processing_time }`，其中 `response` 即综合后的最终答案。

### 作为 Python 模块调用

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

- **缺 `requests`**：`pip install requests`
- **Key 找不到**：确认 `OPENCODE_ZEN_API_KEY` 在环境变量或 `~/.env` 中
- **某模型返回空**：部分模型把内容放在 `reasoning` / `reasoning_content` 字段，已自动兼容
- **5 个模型太慢**：缩减 `REFERENCE_MODELS`，或设 `MIN_SUCCESSFUL_REFERENCES = 1`

## 📁 文件结构

```
moa-workbuddy/
├── SKILL.md                              # WorkBuddy 技能定义（中文）
├── LICENSE                               # MIT
├── README.md                            # 本文件
├── install.sh                           # 一键安装脚本
├── tools/
│   └── mixture_of_agents_tool_free.py    # MoA 核心实现（可独立运行）
└── references/
    ├── SETUP.md                          # 原版快速开始（Hermes，仅供参考）
    └── model-test-results.md             # 模型实测结果
```

## 📜 License

MIT — 自由使用、修改、分享。原始实现 © mantop2010，WorkBuddy 适配 © 妙妙。
