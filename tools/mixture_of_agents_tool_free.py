#!/usr/bin/env python3
"""
Mixture-of-Agents Tool Module (Portable — works on any system)
Adapted for WorkBuddy. Original: github.com/mantop2010/moa-free-models (MIT)

Uses FREE models from OpenCode Zen for cost-free multi-model reasoning.
To use: set OPENCODE_ZEN_API_KEY in your environment or .env file.

Based on: "Mixture-of-Agents Enhances Large Language Model Capabilities"
by Junlin Wang et al. (arXiv:2406.04692v1)

Features
--------
- True parallel reference calls (blocking HTTP off-loaded to a thread pool)
- Multi-layer MoA (``--rounds N``), matching the paper's layered design
- Robust retries with exponential backoff + HTTP 429 ``Retry-After`` handling
- Input validation, configurable models/temperature/timeout
- Rich CLI (argparse) with stdin support, text/JSON output and file export
"""

import argparse
import asyncio
import datetime
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════
# CONFIGURATION — Edit these to customize
# ═══════════════════════════════════════════════════

# FREE models from OpenCode Zen
REFERENCE_MODELS = [
    "deepseek-v4-flash-free",      # DeepSeek - excellent for reasoning
    "nemotron-3-ultra-free",       # NVIDIA Nemotron - strong analysis
    "north-mini-code-free",        # North - good for coding
    "mimo-v2.5-free",              # Xiaomi MiMo - conversational
    "big-pickle",                  # DeepSeek - general purpose
]

AGGREGATOR_MODEL = "deepseek-v4-flash-free"

# API Configuration
OPENCODE_ZEN_API_BASE = "https://opencode.ai/zen/v1"
OPENCODE_ZEN_API_KEY_ENV = "OPENCODE_ZEN_API_KEY"

# Temperature settings
REFERENCE_TEMPERATURE = 0.6
AGGREGATOR_TEMPERATURE = 0.4
MIN_SUCCESSFUL_REFERENCES = 1

# Runtime defaults
DEFAULT_ROUNDS = 1               # number of MoA layers (>=1)
DEFAULT_MAX_TOKENS = 32000
DEFAULT_TIMEOUT = 120.0          # seconds per HTTP request
DEFAULT_MAX_RETRIES = 6
MAX_PROMPT_CHARS = 500_000       # guard against pathological input

# System prompt for the aggregator (final synthesis across candidate answers)
AGGREGATOR_SYSTEM_PROMPT = """You are the final synthesizer in a Mixture-of-Agents pipeline. Several independent models have each answered the user's question. Combine their candidate answers into ONE definitive response that is better than any single candidate.

Evaluation rules:
1. Cross-check factual claims. Agreement across candidates signals reliability; conflict requires you to weigh internal consistency and evidence, then commit to the most defensible position — resolve contradictions, do not hedge.
2. Discard incorrect, outdated, or off-topic content. Do not let a weak candidate degrade the result.
3. Preserve unique correct insights from any one candidate — a valid point survives even if others missed it.
4. Drop meta-commentary such as "As an AI…". Speak in one authoritative voice.

Output contract:
- Open with a direct answer, then give supporting detail.
- Match the user's language and required depth. Be thorough, not padded — cut fluff.
- Use structure (sections, bullets, steps) when it helps clarity.
- If the candidates cannot answer confidently, say so plainly instead of guessing.

Candidate answers:"""

# System prompt for intermediate MoA layers (proposers refining prior answers)
LAYER_SYSTEM_PROMPT = """You are a proposer in a Mixture-of-Agents pipeline. Prior models produced candidate answers to the user's question (shown below). Produce ONE improved candidate that builds on them.

Improvement rules:
1. Find errors, gaps, or weak reasoning in the prior candidates and correct or fill them.
2. Resolve contradictions in favor of the best-supported reasoning.
3. Add value the priors missed (clearer explanation, missing step, sharper conclusion) — do not merely restate them.
4. Keep what is already correct and well-stated; refine, do not rewrite from scratch.

Output contract:
- Write only the improved answer, in the user's language. No preamble such as "based on the previous answers".
- Be self-contained and ready for the final synthesizer to merge.
- Prioritize correctness and clarity over length; match the task's depth.

Prior candidate answers:"""


class RateLimitError(Exception):
    """Raised on HTTP 429 so callers can honor ``Retry-After``."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


# ═══════════════════════════════════════════════════
# API KEY LOADING — Works across all systems
# ═══════════════════════════════════════════════════

def _script_dir() -> str:
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:  # pragma: no cover - __file__ always defined here
        return os.getcwd()


def _parse_env_value(raw: str) -> str:
    """Strip surrounding quotes and trailing inline comments from a .env value."""
    value = raw.strip()
    if value and value[0] in ("'", '"') and value[-1] == value[0] and len(value) >= 2:
        return value[1:-1]
    # drop inline comment only when it is not inside quotes
    if " #" in value:
        value = value.split(" #", 1)[0].strip()
    return value


def _load_api_key() -> Optional[str]:
    """Load API key from environment or a .env file (cross-platform)."""
    # 1. Try environment variable
    api_key = os.getenv(OPENCODE_ZEN_API_KEY_ENV)
    if api_key:
        return api_key.strip()

    # 2. Try common .env locations (most specific first)
    home = os.path.expanduser("~")
    env_paths = [
        os.path.join(_script_dir(), ".env"),                 # alongside the tool
        os.path.join(home, ".workbuddy", ".env"),            # WorkBuddy config dir
        os.path.join(home, ".hermes", ".env"),               # legacy Hermes dir
        os.path.join(home, ".env"),
        ".env",                                              # cwd
    ]
    prefix = f"{OPENCODE_ZEN_API_KEY_ENV}="
    for env_path in env_paths:
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("#") or not stripped:
                        continue
                    if stripped.startswith("export "):
                        stripped = stripped[len("export "):].strip()
                    if stripped.startswith(prefix):
                        value = _parse_env_value(stripped.split("=", 1)[1])
                        if value:
                            return value
        except (OSError, UnicodeDecodeError):
            continue

    return None


# ═══════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════

def _construct_aggregator_prompt(system_prompt: str, responses: List[str]) -> str:
    response_text = "\n".join(
        [f"{i + 1}. {response}" for i, response in enumerate(responses)]
    )
    return f"{system_prompt}\n\n{response_text}"


def _extract_content(result: dict) -> str:
    """Extract content from a response, handling all field names (cross-model)."""
    if not isinstance(result, dict):
        return ""
    choices = result.get("choices") or []
    if not choices:
        return ""
    message = (choices[0] or {}).get("message", {}) or {}
    return (
        message.get("content")
        or message.get("reasoning")
        or message.get("reasoning_content")
        or ""
    )


def _parse_retry_after(response: "requests.Response") -> Optional[float]:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _post_chat_blocking(
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: Optional[int],
    timeout: float,
) -> str:
    """Blocking single HTTP call. Raises RateLimitError on 429, requests errors otherwise."""
    api_key = _load_api_key()
    if not api_key:
        raise ValueError(f"{OPENCODE_ZEN_API_KEY_ENV} not set")

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    response = requests.post(
        f"{OPENCODE_ZEN_API_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )

    if response.status_code == 429:
        raise RateLimitError(
            f"{model} rate limited (429)", retry_after=_parse_retry_after(response)
        )
    response.raise_for_status()
    return _extract_content(response.json())


async def _chat_with_retries(
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: Optional[int],
    timeout: float,
    max_retries: int,
    label: str,
) -> str:
    """Call the chat endpoint in a worker thread with backoff. Returns content or raises."""
    last_error: Optional[str] = None
    for attempt in range(max_retries):
        try:
            logger.info("Querying %s [%s] (attempt %s/%s)", model, label, attempt + 1, max_retries)
            content = await asyncio.to_thread(
                _post_chat_blocking, model, messages, temperature, max_tokens, timeout
            )
            if content:
                logger.info("%s [%s] responded (%s chars)", model, label, len(content))
                return content
            last_error = "empty response"
            logger.warning("%s [%s] empty (attempt %s/%s)", model, label, attempt + 1, max_retries)
        except RateLimitError as e:
            last_error = str(e)
            wait = e.retry_after if e.retry_after is not None else min(2 ** (attempt + 1), 60)
            logger.warning("%s [%s] 429, waiting %.1fs", model, label, wait)
            if attempt < max_retries - 1:
                await asyncio.sleep(wait)
                continue
        except Exception as e:  # network / HTTP / parsing
            last_error = str(e)
            logger.warning("%s [%s] error (attempt %s): %s", model, label, attempt + 1, last_error)

        if attempt < max_retries - 1:
            await asyncio.sleep(min(2 ** (attempt + 1), 60))

    raise RuntimeError(f"{model} failed after {max_retries} attempts: {last_error}")


# ═══════════════════════════════════════════════════
# REFERENCE MODELS (parallel execution)
# ═══════════════════════════════════════════════════

async def _run_reference_model_safe(
    model: str,
    user_prompt: str,
    temperature: float = REFERENCE_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: float = DEFAULT_TIMEOUT,
    system_prompt: Optional[str] = None,
) -> Tuple[str, str, bool]:
    """Run one reference model; never raises — returns (model, content_or_error, success)."""
    messages: List[Dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    try:
        content = await _chat_with_retries(
            model, messages, temperature, max_tokens, timeout, max_retries, label="ref"
        )
        return model, content, True
    except Exception as e:
        return model, f"{model} failed: {e}", False


# ═══════════════════════════════════════════════════
# AGGREGATOR MODEL
# ═══════════════════════════════════════════════════

async def _run_aggregator_model(
    system_prompt: str,
    user_prompt: str,
    aggregator_model: str = AGGREGATOR_MODEL,
    temperature: float = AGGREGATOR_TEMPERATURE,
    max_tokens: Optional[int] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    logger.info("Running aggregator: %s", aggregator_model)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return await _chat_with_retries(
        aggregator_model, messages, temperature, max_tokens, timeout, max_retries, label="agg"
    )


# ═══════════════════════════════════════════════════
# MAIN TOOL
# ═══════════════════════════════════════════════════

async def mixture_of_agents_tool(
    user_prompt: str,
    reference_models: Optional[List[str]] = None,
    aggregator_model: Optional[str] = None,
    rounds: int = DEFAULT_ROUNDS,
    reference_temperature: float = REFERENCE_TEMPERATURE,
    aggregator_temperature: float = AGGREGATOR_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    min_successful: int = MIN_SUCCESSFUL_REFERENCES,
) -> str:
    """
    Process a complex query using Mixture-of-Agents with FREE models.

    Args:
        user_prompt: The complex query to solve.
        reference_models: Custom reference models (defaults to REFERENCE_MODELS).
        aggregator_model: Custom aggregator (defaults to AGGREGATOR_MODEL).
        rounds: Number of MoA layers (>=1). Extra layers let proposers refine
            using the previous layer's answers before final aggregation.
        reference_temperature / aggregator_temperature: Sampling temperatures.
        max_tokens: Max tokens per reference call.
        timeout: Per-request timeout in seconds.
        max_retries: Retry attempts per model call.
        min_successful: Minimum successful references required to aggregate.

    Returns:
        JSON string with {success, response, models_used, rounds,
        successful_references, failed_references, processing_time}.
    """
    start = datetime.datetime.now()

    try:
        # ── Input validation ──────────────────────────────
        if user_prompt is None or not str(user_prompt).strip():
            raise ValueError("user_prompt is empty")
        user_prompt = str(user_prompt)
        if len(user_prompt) > MAX_PROMPT_CHARS:
            raise ValueError(
                f"user_prompt too long ({len(user_prompt)} > {MAX_PROMPT_CHARS} chars)"
            )
        if rounds < 1:
            raise ValueError("rounds must be >= 1")
        if not _load_api_key():
            raise ValueError(f"{OPENCODE_ZEN_API_KEY_ENV} not set")

        ref_models = list(reference_models or REFERENCE_MODELS)
        if not ref_models:
            raise ValueError("reference_models is empty")
        agg_model = aggregator_model or AGGREGATOR_MODEL
        min_needed = max(1, min(min_successful, len(ref_models)))

        logger.info("MoA starting: %d models, %d round(s)", len(ref_models), rounds)

        successes: List[str] = []
        failed: List[str] = []

        # ── Layered proposer stage ────────────────────────
        # Round 1 answers the raw question; later rounds refine using prior answers.
        prev_responses: List[str] = []
        for layer in range(rounds):
            if layer == 0:
                layer_prompt = user_prompt
                layer_system = None
            else:
                context = _construct_aggregator_prompt(LAYER_SYSTEM_PROMPT, prev_responses)
                layer_prompt = f"{context}\n\nUser instruction:\n{user_prompt}"
                layer_system = None

            results = await asyncio.gather(*[
                _run_reference_model_safe(
                    m, layer_prompt,
                    temperature=reference_temperature,
                    max_tokens=max_tokens,
                    max_retries=max_retries,
                    timeout=timeout,
                    system_prompt=layer_system,
                )
                for m in ref_models
            ])

            successes = [c for _, c, s in results if s]
            failed = [m for m, _, s in results if not s]
            logger.info("Round %d: %d ok, %d failed", layer + 1, len(successes), len(failed))

            if len(successes) < min_needed:
                raise ValueError(
                    f"Need {min_needed} successful models, got {len(successes)} "
                    f"in round {layer + 1} (failed: {', '.join(failed) or 'none'})"
                )
            prev_responses = successes

        # ── Aggregation stage ─────────────────────────────
        agg_prompt = _construct_aggregator_prompt(AGGREGATOR_SYSTEM_PROMPT, successes)
        final = await _run_aggregator_model(
            agg_prompt, user_prompt,
            aggregator_model=agg_model,
            temperature=aggregator_temperature,
            max_retries=max_retries,
            timeout=timeout,
        )
        if not final:
            raise ValueError("aggregator returned empty response")

        elapsed = (datetime.datetime.now() - start).total_seconds()

        return json.dumps({
            "success": True,
            "response": final,
            "models_used": {
                "reference_models": ref_models,
                "aggregator_model": agg_model,
            },
            "rounds": rounds,
            "successful_references": len(successes),
            "failed_references": failed,
            "processing_time": elapsed,
        }, indent=2, ensure_ascii=False)

    except Exception as e:
        elapsed = (datetime.datetime.now() - start).total_seconds()
        logger.error("MoA failed: %s", e)
        return json.dumps({
            "success": False,
            "response": "MoA processing failed.",
            "error": str(e),
            "processing_time": elapsed,
        }, indent=2, ensure_ascii=False)


def check_moa_requirements() -> bool:
    """Check if API key is configured."""
    return bool(_load_api_key())


def get_moa_configuration() -> Dict[str, Any]:
    """Get current MoA configuration."""
    return {
        "reference_models": REFERENCE_MODELS,
        "aggregator_model": AGGREGATOR_MODEL,
        "total_reference_models": len(REFERENCE_MODELS),
        "default_rounds": DEFAULT_ROUNDS,
        "api_base": OPENCODE_ZEN_API_BASE,
        "api_key_configured": check_moa_requirements(),
        "cost": "FREE (all models from OpenCode Zen free tier)",
    }


# ═══════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mixture_of_agents_tool_free.py",
        description="Mixture of Agents — multi free-model reasoning (WorkBuddy-adapted).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python mixture_of_agents_tool_free.py \"What is 2+2?\"\n"
            "  python mixture_of_agents_tool_free.py -r 2 --text \"Design a rate limiter\"\n"
            "  echo \"Summarize MoA\" | python mixture_of_agents_tool_free.py --text\n"
        ),
    )
    parser.add_argument("prompt", nargs="*", help="The question (if omitted, read from stdin)")
    parser.add_argument("-m", "--models", nargs="+", metavar="MODEL",
                        help="Reference models (override defaults)")
    parser.add_argument("-a", "--aggregator", metavar="MODEL", help="Aggregator model")
    parser.add_argument("-r", "--rounds", type=int, default=DEFAULT_ROUNDS,
                        help=f"Number of MoA layers (default {DEFAULT_ROUNDS})")
    parser.add_argument("-t", "--temperature", type=float, default=REFERENCE_TEMPERATURE,
                        help=f"Reference temperature (default {REFERENCE_TEMPERATURE})")
    parser.add_argument("--agg-temperature", type=float, default=AGGREGATOR_TEMPERATURE,
                        help=f"Aggregator temperature (default {AGGREGATOR_TEMPERATURE})")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                        help=f"Max tokens per reference call (default {DEFAULT_MAX_TOKENS})")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help=f"Per-request timeout seconds (default {DEFAULT_TIMEOUT})")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES,
                        help=f"Retries per model (default {DEFAULT_MAX_RETRIES})")
    parser.add_argument("--min-success", type=int, default=MIN_SUCCESSFUL_REFERENCES,
                        help=f"Min successful references (default {MIN_SUCCESSFUL_REFERENCES})")
    parser.add_argument("-o", "--output", metavar="FILE", help="Write result to FILE")
    parser.add_argument("--text", action="store_true",
                        help="Print only the final answer (not full JSON)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable INFO logging to stderr")
    parser.add_argument("--check", action="store_true",
                        help="Check API key & configuration, then exit")
    parser.add_argument("--config", action="store_true",
                        help="Print current configuration as JSON, then exit")
    parser.add_argument("--list-models", action="store_true",
                        help="List the default models, then exit")
    return parser


def _read_prompt(args: argparse.Namespace) -> str:
    if args.prompt:
        return " ".join(args.prompt)
    # fall back to stdin when piped
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.list_models:
        print(json.dumps({
            "reference_models": REFERENCE_MODELS,
            "aggregator_model": AGGREGATOR_MODEL,
        }, indent=2, ensure_ascii=False))
        return 0

    if args.config:
        print(json.dumps(get_moa_configuration(), indent=2, ensure_ascii=False))
        return 0

    if args.check:
        ok = check_moa_requirements()
        print("🤖 MoA Free Models — Portable Edition (WorkBuddy-adapted)")
        print(f"✅ API Key: {'available' if ok else 'MISSING'}")
        print(f"📋 Models: {len(REFERENCE_MODELS)} free reference + 1 aggregator")
        print(f"🔁 Default rounds: {DEFAULT_ROUNDS}")
        return 0 if ok else 1

    prompt = _read_prompt(args)
    if not prompt:
        parser.print_help()
        print("\n❌ No prompt provided (pass it as an argument or via stdin).", file=sys.stderr)
        return 2

    result = asyncio.run(mixture_of_agents_tool(
        prompt,
        reference_models=args.models,
        aggregator_model=args.aggregator,
        rounds=args.rounds,
        reference_temperature=args.temperature,
        aggregator_temperature=args.agg_temperature,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        max_retries=args.max_retries,
        min_successful=args.min_success,
    ))

    try:
        parsed = json.loads(result)
    except json.JSONDecodeError:
        parsed = {"success": False, "response": result}

    output = parsed.get("response", "") if args.text and parsed.get("success") else result

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"✅ Result written to {args.output}", file=sys.stderr)
        except OSError as e:
            print(f"❌ Could not write {args.output}: {e}", file=sys.stderr)
            print(output)
            return 1
    else:
        print(output)

    return 0 if parsed.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
