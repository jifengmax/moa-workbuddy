#!/usr/bin/env bash
# =============================================================================
# release.sh — WorkBuddy MoA 技能 自动化发布脚本
# -----------------------------------------------------------------------------
# 一条龙：build-manifest → 一致性校验 → 离线测试 → 改版本号 → commit → tag →
#         push → 建 GitHub Release。
#
# 用法:
#   bash release.sh <VERSION> [选项]
#     <VERSION>          版本号，形如 1.4.0（脚本自动加 v 前缀打 tag）
#   选项:
#     --notes "说明"        发布说明（覆盖默认文案）
#     --notes-file FILE     从文件读取发布说明
#     --skip-changelog      跳过 CHANGELOG 顶部版本号检查（默认强制）
#     --dry-run             只打印将执行的步骤，不写任何文件 / 不 commit / 不 push
#
# 安全约定（与历史提交一致）:
#   - GITHUB_TOKEN 只通过 Authorization 头传给 git/curl，绝不写入 remote URL
#   - 脚本结束即消失，不在本机留下任何凭证
#   - 没有 GITHUB_TOKEN 时，脚本照常完成本地 commit + tag，并提示手动 push
#
# 幂等: 若 tag 已存在，跳过 commit/tag，只做 push + 建 Release（便于补发）
# =============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -W)"
cd "$REPO_DIR"

# ---- 参数解析 ---------------------------------------------------------------
VER="${1:-}"
NOTES=""
NOTES_FILE=""
SKIP_CHANGELOG=0
DRY_RUN=0
PROXY="${HTTPS_PROXY:-${HTTP_PROXY:-http://127.0.0.1:7897}}"
REMOTE="https://github.com/jifengmax/moa-workbuddy.git"
GH_REPO="jifengmax/moa-workbuddy"

shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --notes)        NOTES="$2"; shift 2;;
    --notes-file)   NOTES_FILE="$2"; shift 2;;
    --skip-changelog) SKIP_CHANGELOG=1; shift;;
    --dry-run)      DRY_RUN=1; shift;;
    *) echo "❌ 未知参数: $1" >&2; exit 2;;
  esac
done

# ---- 参数校验 ---------------------------------------------------------------
[[ -z "$VER" ]] && {
  echo "用法: bash release.sh <VERSION> [--notes '...' | --notes-file FILE] [--skip-changelog] [--dry-run]" >&2
  exit 2
}
VER="${VER#v}"   # 去掉可能的 v 前缀
if [[ ! "$VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "❌ 版本号格式应为 X.Y.Z，收到: $VER" >&2; exit 2
fi
TAG="v$VER"

# 探测可用的 Python：优先带 requests 的（核心模块 import requests）
PY=""
for cand in "${PYTHON:-}" \
            "C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe" \
            "$(command -v python3)" "$(command -v python)"; do
  [[ -z "$cand" ]] && continue
  if "$cand" -c "import requests" >/dev/null 2>&1; then PY="$cand"; break; fi
done
[[ -z "$PY" ]] && { echo "❌ 找不到带 requests 的 python（核心模块需要）。可设 PYTHON 环境变量指定。" >&2; exit 1; }
echo "🐍 使用 Python: $PY"

echo "📦 发布目标: $GH_REPO @ $TAG  (repo: $REPO_DIR)"

# ---- 0. 工作区检查（软检查：发版前开发者通常已写好 CHANGELOG 等改动）------
# 清掉 __pycache__（避免清单把 .pyc 算进去）；其余未提交改动会被一并打包
find "$REPO_DIR" -type d -name __pycache__ -not -path '*/.git/*' -exec rm -rf {} + 2>/dev/null || true
DIRTY="$(git status --porcelain | grep -v '__pycache__' || true)"
if [[ -n "$DIRTY" ]]; then
  echo "ℹ️  工作区存在未提交改动，将被一并打包进本次发布:"
  echo "$DIRTY"
fi

# ---- 1. 版本号落地到源码（先于 build-manifest）----------------------------
if [[ "$DRY_RUN" -eq 0 ]]; then
  echo "✏️  更新 SKILL.md version -> $VER"
  sed -i -E "s/^version: .*/version: $VER/" SKILL.md
  echo "✏️  更新 tools/test_install.py 版本断言 -> $VER"
  sed -i -E "s/(self\.assertEqual\(r\.version, \")[^\"]*(\"\))/\1$VER\2/" tools/test_install.py
else
  echo "🔍 [dry-run] 将更新 SKILL.md version -> $VER"
  echo "🔍 [dry-run] 将更新 tools/test_install.py 版本断言 -> $VER"
fi

# ---- 2. CHANGELOG 顶部版本号检查（默认强制）--------------------------------
if [[ "$SKIP_CHANGELOG" -eq 0 ]]; then
  if ! grep -q "^## \[$VER\]" CHANGELOG.md; then
    echo "❌ CHANGELOG.md 顶部缺少 '## [$VER]' 段，请先写好本次变更说明（或加 --skip-changelog）。" >&2
    exit 1
  fi
  echo "✅ CHANGELOG 含本次版本段: ## [$VER]"
fi

# ---- 3. 重建 MANIFEST（必须在所有源码改动之后！关键）----------------------
if [[ "$DRY_RUN" -eq 0 ]]; then
  echo "🧮 重建 MANIFEST.json（反映最新文件 + 版本号）"
  "$PY" tools/install_skill.py build-manifest --skill-dir "$REPO_DIR" >/dev/null
else
  echo "🔍 [dry-run] 将重建 MANIFEST.json"
fi

# ---- 4. 离线测试（全过才继续）---------------------------------------------
echo "🧪 运行离线测试..."
if [[ "$DRY_RUN" -eq 0 ]]; then
  "$PY" tools/test_moa.py >/dev/null 2>&1 || { echo "❌ test_moa.py 失败"; exit 1; }
  "$PY" tools/test_install.py >/dev/null 2>&1 || { echo "❌ test_install.py 失败"; exit 1; }
  echo "✅ 离线测试全过 (test_moa + test_install)"
else
  echo "🔍 [dry-run] 将运行 test_moa.py + test_install.py"
fi

# ---- 5. 收集发布说明 --------------------------------------------------------
if [[ -n "$NOTES_FILE" ]]; then
  NOTES="$(cat "$NOTES_FILE")"
elif [[ -z "$NOTES" ]]; then
  NOTES="MoA (Mixture of Agents) — WorkBuddy 专属技能版 v$VER

自动发布（release.sh）。主要变更见 CHANGELOG。
- 安装：git clone https://github.com/jifengmax/moa-workbuddy.git ~/.workbuddy/skills/moa
- 需自备 OPENCODE_ZEN_API_KEY（opencode.ai 免费）
- MIT License"
fi

# ---- 6. commit + tag（tag 已存在则跳过，便于补发）-------------------------
TAG_EXISTS=0
git rev-parse "$TAG" >/dev/null 2>&1 && TAG_EXISTS=1

if [[ "$TAG_EXISTS" -eq 1 ]]; then
  echo "ℹ️  tag $TAG 已存在，跳过 commit/tag，仅做 push + Release"
else
  if [[ "$DRY_RUN" -eq 0 ]]; then
    git add -A
    git commit -q -m "release: v$VER"
    git tag -a "$TAG" -m "v$VER"
    echo "✅ 已 commit + tag $TAG"
  else
    echo "🔍 [dry-run] 将 commit 'release: v$VER' + tag $TAG"
  fi
fi

# ---- 7. push + 建 Release（需要 GITHUB_TOKEN）-----------------------------
if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "⚠️  未设置 GITHUB_TOKEN —— 已完成的本地 commit/tag 如下："
  git log --oneline -1 2>/dev/null || true
  echo "   请手动 push 或重跑: GITHUB_TOKEN=xxx bash release.sh $VER"
  exit 0
fi

# 确保 remote 是干净的 https URL（不带凭证）
git remote set-url origin "$REMOTE" 2>/dev/null || git remote add origin "$REMOTE"

B64=$(printf '%s' "$GITHUB_TOKEN:" | base64 | tr -d '\n')

if [[ "$DRY_RUN" -eq 0 ]]; then
  echo "🚀 推送 master + tag（token 走 header，URL 不含凭证）"
  git -c http.extraHeader="Authorization: Basic $B64" push -u origin master 2>&1 | sed "s/$GITHUB_TOKEN/***/g"
  git -c http.extraHeader="Authorization: Basic $B64" push origin "$TAG" 2>&1 | sed "s/$GITHUB_TOKEN/***/g"
  echo "🏷️  创建 GitHub Release $TAG"
  "$PY" - "$GITHUB_TOKEN" "$PROXY" "$GH_REPO" "$TAG" "$NOTES" <<'PYEOF'
import sys, json, urllib.request, urllib.error
token, proxy, repo, tag, notes = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
data = json.dumps({
    "tag_name": tag,
    "name": f"{tag} — MoA (WorkBuddy)",
    "body": notes,
    "target_commitish": "master",
}).encode()
req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/releases",
    data=data, method="POST",
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "wb"},
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
try:
    resp = opener.open(req)
    print("✅ RELEASE:", resp.status, json.load(resp).get("html_url"))
except urllib.error.HTTPError as e:
    print("❌ RELEASE_ERR:", e.code, e.read().decode()[:300])
PYEOF
  # 推完再次确保 remote 不含凭证（本来就没写，双重保险）
  git remote set-url origin "$REMOTE"
  echo "🔒 本地 remote 已确认不含凭证: $(git remote get-url origin)"
else
  echo "🔍 [dry-run] 将推送 master + $TAG 并创建 GitHub Release（需 GITHUB_TOKEN）"
fi

echo "🎉 完成: $TAG"
