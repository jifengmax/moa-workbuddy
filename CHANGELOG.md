# Changelog

All notable changes to this WorkBuddy-adapted MoA skill are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

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
