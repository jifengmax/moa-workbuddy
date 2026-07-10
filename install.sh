#!/bin/bash
# MoA (Mixture of Agents) — WorkBuddy 一键安装脚本
# 用法：bash install.sh   或   bash <(curl -fsSL https://raw.githubusercontent.com/jifengmax/moa-workbuddy/master/install.sh)
set -e

SKILL_DIR="$HOME/.workbuddy/skills/moa"
REPO="https://github.com/jifengmax/moa-workbuddy.git"

echo "🤖 MoA — Mixture of Agents 安装"
echo "================================="

if [ -d "$SKILL_DIR/.git" ]; then
  echo "⚠️  已存在，更新中…"
  git -C "$SKILL_DIR" pull --ff-only
else
  echo "📥 克隆到 $SKILL_DIR"
  git clone "$REPO" "$SKILL_DIR"
fi

echo ""
echo "✅ 安装完成：$SKILL_DIR"
echo ""
echo "📌 使用前需配置 OpenCode Zen 免费 API Key："
echo "   export OPENCODE_ZEN_API_KEY=\"你的key\""
echo "   （注册：https://opencode.ai/auth ）"
echo "   并安装依赖：pip install requests"
echo ""
echo "🚀 然后即可：python $SKILL_DIR/tools/mixture_of_agents_tool_free.py \"你的问题\""
