# Changelog

All notable changes to this WorkBuddy-adapted MoA skill are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [1.3.0] - 2026-07-11

### Added
- **Multi-agent installer** `tools/install_skill.py` (stdlib-only, zero deps) so any
  agent runtime can install this skill into its own skill dir. Covers the five
  required aspects (see `docs/MULTI_AGENT_INSTALL.md`):
  - **Entry points**: Python API (`install_skill(InstallRequest)`), CLI, and
    `github:` / `file:` / `registry:` sources.
  - **Permission & security**: source allowlist (default `github:jifengmax/*` +
    local `file:`), manifest-hash check, optional ed25519 signature hook,
    short-lived token that is never persisted.
  - **Consistency verification**: structure / `SKILL.md` frontmatter / compile /
    offline self-test / manifest-hash, all run on a staged copy before commit.
  - **Concurrency**: per-target isolation, advisory lockfile serialization,
    atomic `os.replace`, and idempotency (identical hash → no-op).
  - **Rollback**: staged → backup → atomic replace → restore on failure, with
    explicit error codes (`ERR_UNTRUSTED_SOURCE`, `ERR_VERIFY_FAILED`, …).
- `MANIFEST.json` (publisher-built via `install_skill.py build-manifest`) used by
  the installer for integrity verification.
- `tools/test_install.py` — 4 offline integration tests (install / idempotency /
  corrupt-source rollback / untrusted-source rejection).
- `docs/MULTI_AGENT_INSTALL.md` — full design + interface definitions.

### Changed
- README / SKILL.md now document the multi-agent install path; file tree updated.

## [1.2.0] - 2026-07-11

### Changed
- **Rewrote the two internal system prompts** for stronger model behavior:
  - `AGGREGATOR_SYSTEM_PROMPT` now carries an explicit evaluation rubric
    (cross-check claims, resolve contradictions instead of hedging, discard weak
    candidates, preserve unique correct insights) plus an output contract
    (lead with the answer, match the user's language/depth, use structure,
    admit uncertainty instead of guessing).
  - `LAYER_SYSTEM_PROMPT` (intermediate proposers) now clearly diverges from the
    aggregator: it instructs proposers to correct errors, resolve contradictions,
    and add value the prior candidates missed — refining rather than restating.

### Fixed
- The intermediate-layer prompt was previously near-identical to the aggregator
  prompt, undermining the layered-refinement design; the two now have distinct roles.

## [1.1.0] - 2026-07-11

### Added
- **True parallel reference calls** — blocking HTTP is now off-loaded to a thread
  pool (`asyncio.to_thread`), so the reference models actually run concurrently
  (previously `asyncio.gather` wrapped blocking `requests.post`, running serially).
- **Multi-layer MoA (`--rounds N`)** — matches the paper's layered design; extra
  layers let proposers refine using the previous layer's answers before final
  aggregation.
- **Full CLI** via `argparse`: `--models`, `--aggregator`, `--rounds`,
  `--temperature`, `--agg-temperature`, `--max-tokens`, `--timeout`,
  `--max-retries`, `--min-success`, `--output`, `--text`, `--verbose`,
  `--check`, `--config`, `--list-models`, plus **stdin** support.
- **HTTP 429 handling** — honors the `Retry-After` header during backoff.
- **Input validation** — empty/oversized prompt, empty model list, `rounds >= 1`.
- **Logging configuration** — `--verbose` streams INFO logs to stderr.
- Extra `.env` search paths (`<skill_dir>/.env`, `~/.workbuddy/.env`) with quote
  and inline-comment handling; `export KEY=...` lines are supported.
- New files: `requirements.txt`, `.env.example`, `tools/test_moa.py` (offline
  unit tests), `CHANGELOG.md`.
- Richer result JSON: `rounds`, `successful_references`, `failed_references`,
  and `processing_time` is now reported on failure too.

### Fixed
- Robust `_extract_content` for empty `choices` / non-dict payloads.
- Unified retry logic shared by reference and aggregator calls.

### Changed
- `mixture_of_agents_tool()` gained optional keyword args (rounds, temperatures,
  timeout, retries, min_successful) while staying backward compatible.

## [1.0.0] - 2026-07-10

### Added
- Initial WorkBuddy adaptation of `mantop2010/moa-free-models` (MIT):
  Chinese `SKILL.md`, CLI entrypoint, `README`, MIT `LICENSE`, `install.sh`.

### Fixed
- Removed the missing `tools.debug_helpers` import that crashed the original.
