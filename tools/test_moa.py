#!/usr/bin/env python3
"""
Offline unit tests for the MoA tool.

These tests DO NOT hit the network — every HTTP call is mocked. Run with:
    python tools/test_moa.py
or:
    python -m unittest tools.test_moa
"""

import asyncio
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mixture_of_agents_tool_free as moa  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class ExtractContentTests(unittest.TestCase):
    def test_content_field(self):
        r = {"choices": [{"message": {"content": "hello"}}]}
        self.assertEqual(moa._extract_content(r), "hello")

    def test_reasoning_fallback(self):
        r = {"choices": [{"message": {"reasoning": "thought"}}]}
        self.assertEqual(moa._extract_content(r), "thought")

    def test_reasoning_content_fallback(self):
        r = {"choices": [{"message": {"reasoning_content": "rc"}}]}
        self.assertEqual(moa._extract_content(r), "rc")

    def test_empty_choices(self):
        self.assertEqual(moa._extract_content({"choices": []}), "")

    def test_non_dict(self):
        self.assertEqual(moa._extract_content(None), "")
        self.assertEqual(moa._extract_content("nope"), "")


class EnvParsingTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(moa._parse_env_value("abc123"), "abc123")

    def test_double_quotes(self):
        self.assertEqual(moa._parse_env_value('"abc123"'), "abc123")

    def test_single_quotes(self):
        self.assertEqual(moa._parse_env_value("'abc123'"), "abc123")

    def test_inline_comment(self):
        self.assertEqual(moa._parse_env_value("abc123 # my key"), "abc123")


class HelperTests(unittest.TestCase):
    def test_construct_prompt_numbers_responses(self):
        out = moa._construct_aggregator_prompt("SYS", ["a", "b"])
        self.assertIn("SYS", out)
        self.assertIn("1. a", out)
        self.assertIn("2. b", out)

    def test_config_keys(self):
        cfg = moa.get_moa_configuration()
        for key in ("reference_models", "aggregator_model", "default_rounds", "api_base"):
            self.assertIn(key, cfg)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        os.environ[moa.OPENCODE_ZEN_API_KEY_ENV] = "test-key"

    def tearDown(self):
        os.environ.pop(moa.OPENCODE_ZEN_API_KEY_ENV, None)

    def test_empty_prompt_fails_gracefully(self):
        res = json.loads(run(moa.mixture_of_agents_tool("   ")))
        self.assertFalse(res["success"])
        self.assertIn("empty", res["error"])

    def test_bad_rounds(self):
        res = json.loads(run(moa.mixture_of_agents_tool("hi", rounds=0)))
        self.assertFalse(res["success"])

    def test_happy_path_mocked(self):
        def fake_post(model, messages, temperature, max_tokens, timeout):
            return f"answer from {model}"

        with mock.patch.object(moa, "_post_chat_blocking", side_effect=fake_post):
            res = json.loads(run(moa.mixture_of_agents_tool(
                "What is 2+2?",
                reference_models=["m1", "m2"],
                aggregator_model="agg",
            )))
        self.assertTrue(res["success"])
        self.assertIn("answer from agg", res["response"])
        self.assertEqual(res["successful_references"], 2)
        self.assertEqual(res["rounds"], 1)

    def test_multi_round_mocked(self):
        calls = {"n": 0}

        def fake_post(model, messages, temperature, max_tokens, timeout):
            calls["n"] += 1
            return f"r{calls['n']} from {model}"

        with mock.patch.object(moa, "_post_chat_blocking", side_effect=fake_post):
            res = json.loads(run(moa.mixture_of_agents_tool(
                "Design something",
                reference_models=["m1", "m2"],
                aggregator_model="agg",
                rounds=2,
            )))
        self.assertTrue(res["success"])
        # 2 refs * 2 rounds + 1 aggregator = 5 calls
        self.assertEqual(calls["n"], 5)

    def test_all_models_fail(self):
        def boom(model, messages, temperature, max_tokens, timeout):
            raise RuntimeError("network down")

        with mock.patch.object(moa, "_post_chat_blocking", side_effect=boom):
            res = json.loads(run(moa.mixture_of_agents_tool(
                "hi",
                reference_models=["m1"],
                aggregator_model="agg",
                max_retries=1,
            )))
        self.assertFalse(res["success"])


class CliTests(unittest.TestCase):
    def test_list_models(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = moa.main(["--list-models"])
        self.assertEqual(code, 0)
        self.assertIn("aggregator_model", buf.getvalue())

    def test_config(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = moa.main(["--config"])
        self.assertEqual(code, 0)
        self.assertIn("reference_models", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
