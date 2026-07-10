# 多 Agent 安装机制设计（MoA WorkBuddy Skill）

> 目标：让任意 agent（含 WorkBuddy 子 agent、外部多 agent 平台中的 agent、CI 机器人）都能**安全、可验证、可并发、可回滚**地安装本技能，且完全兼容现有 WorkBuddy 技能架构。

---

## 0. 设计前提与现有架构约束

现有机制（不可破坏）：

| 项 | 现状 |
|---|---|
| 技能位置 | 用户级 `~/.workbuddy/skills/<name>/` 或项目级 `<workspace>/.workbuddy/skills/<name>/` |
| 技能描述 | `SKILL.md`，含 YAML frontmatter（`name` / `description` 等） |
| 已装技能 | `mantop2010/moa-free-models` 适配版，本仓库即其发布源 |
| 现有安装方式 | ① 市场安装器 `workbuddy_marketplace_skill`；② `git clone`；③ `install.sh` 一键脚本 |
| 运行依赖 | Python + `requests`（仅运行时需要；安装器本身 **stdlib-only**，零外部依赖） |

**核心结论**：安装器只是"把本仓库的合规副本，原子地放到某个 agent 的技能目录"，并对副本做一致性校验。它不引入新的运行时依赖，不改变 `SKILL.md` 格式。

---

## 1. 其他 agent 发起安装请求的入口和方式

### 1.1 入口（按推荐度排序）

| 入口 | 触发方式 | 适用场景 |
|---|---|---|
| **A. Python API**（首选，可嵌入任何 agent 运行时） | `from tools.install_skill import install_skill, InstallRequest` | agent 用代码调用，易编排 |
| **B. CLI** | `python tools/install_skill.py install --source <SRC> --target <DIR>` | 脚本/SSH/容器启动、CI |
| **C. Skill 自举入口** | 任何 agent 调用本技能时传 `action=install-for-agent`，由本 skill 内部转调安装器 | 已在 WorkBuddy 内、想"借 moa 装 moa" |
| **D. HTTP 网关**（可选扩展） | POST `/v1/skills/install`，body=InstallRequest(JSON) | 跨主机、中心化 registry 场景 |

### 1.2 来源的三种写法（`source` 字段）

```
github:<owner>/<repo>[@<tag>]     # 展开为 https://github.com/<owner>/<repo>.git
file:/abs/path                      # 本地仓库/缓存目录（离线、内网）
registry:<name>[@<version>]        # 预留：对接 WorkBuddy 市场或私有 registry
```

> 多 agent 场景下，**`github:` 是默认、最通用**的入口；内网/离线环境用 `file:` 指向已同步的镜像目录。

### 1.3 一个 InstallRequest 的最小示例

```python
req = InstallRequest(
    source="github:jifengmax/moa-workbuddy@v1.2",
    target=r"C:\Users\some_agent\.workbuddy\skills\moa",
    agent_id="analyst-07",
)
result = install_skill(req)
```

---

## 2. 安装过程中的权限校验与安全检查

分层校验，**任一失败立即中止并回滚**（见 §5）。

| 层级 | 检查项 | 失败动作 |
|---|---|---|
| **L1 来源可信** | `source` 必须在**允许清单**（默认仅 `github:jifengmax/*` + 本地 `file:`）；禁止任意 URL | 拒绝，返回 `ERR_UNTRUSTED_SOURCE` |
| **L2 传输完整性** | 仓库根 `MANIFEST.json` 的 `manifest_hash`（sha256）应与请求中 `expected_manifest_hash` 一致；未指定则仅警告不阻断 | 不匹配返回 `ERR_MANIFEST_MISMATCH` |
| **L3 内容签名（可选强化）** | `MANIFEST.json` 可附带 `signature`（ed25519，由发布者私钥签）；安装器用内置公钥验签 | 验签失败 `ERR_BAD_SIGNATURE` |
| **L4 目标写权限** | 目标父目录可写；在 WorkBuddy 内需通过沙箱权限提示（bypassPermissions / 用户确认） | 无权限 `ERR_NO_WRITE_ACCESS` |
| **L5 敏感信息** | 安装器**绝不持久化** `token`；仅用于本次私有源克隆，用完即焚（等同 push 时抹 token 的做法） | — |

> **密钥原则**：公开技能用 `github:` 免 token 克隆；私有源才需短时效 token，且只允许通过 `InstallRequest.token` 传入内存，任何环节都不写盘。

---

## 3. 安装完成后的一致性验证

安装到临时舞台目录后、正式落盘前，执行 `verify_installed(staged_dir)`：

1. **结构校验**：必须存在 `SKILL.md`、`tools/mixture_of_agents_tool_free.py`。
2. **SKILL.md frontmatter**：能解析出 `name` / `description`（正则提取，无需 PyYAML）。
3. **编译校验**：`tools/*.py` 全部 `py_compile` 通过。
4. **离线自测**：若含 `tools/test_moa.py`，以**离线 mock** 模式运行（不触网、不需 key），必须全过。
5. **清单比对**：`compute_manifest(staged_dir)` 的 sha256 必须等于 `MANIFEST.json` 记录值。
6. **版本声明**：`SKILL.md` 或 `MANIFEST.json` 的 `version` 字段可读。

> 全部通过才执行"舞台 → 正式目标"的原子替换；任一失败→丢弃舞台、回滚（§5）。

---

## 4. 多 agent 同时安装的并发处理

| 策略 | 实现 |
|---|---|
| **按目标隔离** | 不同 agent 装到**不同 target 目录**（各自沙箱），天然并行，互不干扰 |
| **同目标串行化** | 同一 target 用**建议锁文件** `<target>.install.lock`（写 pid），获取不到则忙等/超时失败，避免两 agent 互覆盖 |
| **原子替换** | 先装到 `<target>.stage.tmp`，校验通过后 `os.replace()` 一次性改名落盘（同文件系统内原子） |
| **幂等** | 若目标已存在且 `compute_manifest(target).hash == expected`，直接返回 `already_installed=True`，不做任何写操作 |
| **只读竞争** | 并发读取方（agent 直接调用技能）不受影响——旧版本在替换瞬间仍可读，替换是原子的 |

> Windows 上 `os.replace` 是原子的；锁用 `os.open(..., O_CREAT|O_EXCL)` 创建锁文件实现（已存在则视为被占用）。

---

## 5. 安装失败时的回滚与错误处理

采用**暂存 + 备份 + 事务日志**模式：

```
开始
 ├─ acquire_lock(target)
 ├─ snapshot: 若 target 已存在 → 备份到 <target>.bak
 ├─ stage: 拉取/复制到 <target>.stage.tmp
 ├─ verify_installed(<target>.stage.tmp)
 │     └─ 失败 → 删舞台 → restore backup（若有）→ release_lock → 返回 rolled_back=True
 ├─ commit: os.replace(stage, target)
 │     └─ 替换后若 verify_installed(target) 仍失败 → restore backup → rolled_back=True
 ├─ cleanup: 删 .bak、删舞台
 └─ release_lock → 返回 success / already_installed
```

**错误码**（写入 `InstallResult.error` 与 `steps` 审计日志）：

| code | 含义 |
|---|---|
| `ERR_UNTRUSTED_SOURCE` | 来源不在允许清单 |
| `ERR_MANIFEST_MISMATCH` | 清单哈希与预期不符 |
| `ERR_BAD_SIGNATURE` | 签名验签失败 |
| `ERR_NO_WRITE_ACCESS` | 目标不可写 / 权限未授权 |
| `ERR_FETCH_FAILED` | 拉取源失败（网络/克隆） |
| `ERR_VERIFY_FAILED` | 一致性校验未通过，已回滚 |
| `ERR_LOCK_TIMEOUT` | 同目标并发锁超时 |

---

## 6. 接口定义（清晰契约）

### 6.1 数据结构

```python
@dataclass
class InstallRequest:
    source: str                       # github:/file:/registry:
    target: str                       # 绝对路径技能目录
    agent_id: str = "default"         # 调用方 agent 标识（审计用）
    expected_manifest_hash: str | None = None   # 版本钉死
    allow_insecure: bool = False      # 跳过 L3 签名（仅内网/测试）
    token: str | None = None          # 短时效，绝不持久化

@dataclass
class InstallResult:
    success: bool
    target: str
    version: str | None
    manifest_hash: str | None
    files_installed: list[str]
    already_installed: bool
    rolled_back: bool
    error: str | None                 # 见 §5 错误码
    steps: list[str]                  # 审计日志
```

### 6.2 核心函数

```python
def install_skill(req: InstallRequest) -> InstallResult: ...
def verify_installed(skill_dir: str) -> list[str]: ...      # 返回错误列表，空=通过
def compute_manifest(skill_dir: str) -> dict: ...           # {version, files:{path:sha256}, hash}
def build_manifest(skill_dir: str) -> dict: ...             # 发布者用：生成 MANIFEST.json
```

### 6.3 MANIFEST.json 模式

```json
{
  "name": "moa",
  "version": "1.2.0",
  "generated_at": "2026-07-11T00:26:00+08:00",
  "files": { "SKILL.md": "<sha256>", "tools/mixture_of_agents_tool_free.py": "<sha256>", "...": "..." },
  "hash": "<sha256 of the concatenated file hashes>"
}
```

### 6.4 HTTP 网关（可选扩展，D 入口）

```
POST /v1/skills/install
Content-Type: application/json
Body: InstallRequest   (JSON)
→ 200 InstallResult(JSON)
→ 4xx/5xx { "error": "<code>", "detail": "..." }
```

---

## 7. 与现有架构的兼容性清单

- ✅ 不改动 `SKILL.md` 格式、不放任任何外部依赖
- ✅ 安装产物 = 现有仓库目录结构的合规副本，现有运行时（`requests` + CLI）原样可用
- ✅ `install.sh` 保留作为"人类一键装"入口；`install_skill.py` 作为"agent 编程式安装"入口，二者共存
- ✅ 现有 `--config/--check/--list-models` 等 CLI 不受影响
- ✅ 发布流程不变：仍 `git tag` + GitHub Release；新增一步 `build-manifest` 生成 `MANIFEST.json` 一并提交

---

## 8. 后续可选增强

- **签名（L3）**：发布时用 ed25519 私钥签 `MANIFEST.json`，安装器内置公钥验签，彻底防篡改。
- **registry 入口**：把 `registry:moa@1.2` 接到 WorkBuddy 市场或私有 registry API。
- **HTTP 网关**：跨主机中心化安装服务，审计所有 agent 的安装行为。
