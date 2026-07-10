#!/usr/bin/env python3
"""
Offline integration tests for tools/install_skill.py (multi-agent installer).

No network, no API key. Exercises the install transaction against a local
(file:) source using temp directories, mirroring docs/MULTI_AGENT_INSTALL.md.

Run:  python tools/test_install.py
"""
import os
import shutil
import tempfile
import unittest

# locate this skill's own directory (parent of tools/)
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SKILL_DIR not in os.sys.path:
    os.sys.path.insert(0, os.path.join(SKILL_DIR, "tools"))

from install_skill import (  # noqa: E402
    InstallRequest,
    install_skill,
    verify_installed,
)


class TestMultiAgentInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="moa-install-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _agent_target(self, name):
        return os.path.join(self.tmp, name, "moa")

    def test_fresh_install_succeeds_and_verifies(self):
        target = self._agent_target("agentA")
        r = install_skill(InstallRequest(source="file:" + SKILL_DIR, target=target,
                                          agent_id="agentA"))
        self.assertTrue(r.success, r.error)
        self.assertFalse(r.already_installed)
        self.assertEqual(r.version, "1.3.0")
        self.assertEqual(verify_installed(target), [])

    def test_idempotent_reinstall_is_noop(self):
        target = self._agent_target("agentB")
        install_skill(InstallRequest(source="file:" + SKILL_DIR, target=target))
        r2 = install_skill(InstallRequest(source="file:" + SKILL_DIR, target=target))
        self.assertTrue(r2.success)
        self.assertTrue(r2.already_installed)

    def test_corrupt_source_rejected_and_existing_preserved(self):
        target = self._agent_target("agentC")
        install_skill(InstallRequest(source="file:" + SKILL_DIR, target=target))
        # build a corrupt source missing a required file
        bad = os.path.join(self.tmp, "broken")
        shutil.copytree(SKILL_DIR, bad, ignore=shutil.ignore_patterns(".git"))
        os.remove(os.path.join(bad, "tools", "mixture_of_agents_tool_free.py"))
        r = install_skill(InstallRequest(source="file:" + bad, target=target))
        self.assertFalse(r.success)
        self.assertEqual(r.error, "ERR_VERIFY_FAILED")
        # original good install must remain intact
        self.assertEqual(verify_installed(target), [])

    def test_untrusted_source_rejected(self):
        target = self._agent_target("agentD")
        r = install_skill(InstallRequest(source="http://evil.example.com/x.git",
                                         target=target))
        self.assertFalse(r.success)
        self.assertEqual(r.error, "ERR_UNTRUSTED_SOURCE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
