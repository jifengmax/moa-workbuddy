#!/usr/bin/env python3
"""
Multi-agent installer for the MoA (Mixture of Agents) WorkBuddy skill.

Stdlib-only (no third-party deps) so any agent runtime can use it.

Design & interfaces: see docs/MULTI_AGENT_INSTALL.md

Subcommands
-----------
  install        Install the skill from a source into a target skill dir.
  build-manifest Generate MANIFEST.json for a skill directory (publisher side).
  verify         Run consistency checks on an already-installed skill dir.

Examples
--------
  # agent API
  from tools.install_skill import install_skill, InstallRequest
  r = install_skill(InstallRequest(source="github:jifengmax/moa-workbuddy@v1.2",
                                   target=r"C:\\Users\\agent\\.workbuddy\\skills\\moa"))
  # CLI
  python tools/install_skill.py install --source github:jifengmax/moa-workbuddy --target ~/.workbuddy/skills/moa
  python tools/install_skill.py build-manifest --skill-dir .
  python tools/install_skill.py verify --target ~/.workbuddy/skills/moa
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from typing import List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

# L1 source trust allowlist (prefix match). Extend per deployment.
TRUSTED_PREFIXES = (
    "github:jifengmax/",   # official publisher
    "file:",               # local / intranet mirror (offline)
    "registry:",           # reserved (private registry)
)

REQUIRED_FILES = ("SKILL.md", "tools/mixture_of_agents_tool_free.py")
LOCK_TIMEOUT_S = 60.0
LOCK_POLL_S = 0.5
MANIFEST_NAME = "MANIFEST.json"

# Optional ed25519 public key for L3 signature verification. Empty = not enforced.
# (Publisher signs MANIFEST.json; verified only when a key is configured.)
SIGNING_PUBLIC_KEY = ""  # e.g. "-----BEGIN PUBLIC KEY-----...-----END PUBLIC KEY-----"


# ─────────────────────────────────────────────────────────────────────────────
# Data contracts
# ─────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class InstallRequest:
    source: str                                         # github:/file:/registry:
    target: str                                         # absolute skill dir path
    agent_id: str = "default"                           # caller identity (audit)
    expected_manifest_hash: Optional[str] = None        # pin a version
    allow_insecure: bool = False                        # skip L3 signature
    token: Optional[str] = None                         # short-lived; never persisted


@dataclasses.dataclass
class InstallResult:
    success: bool
    target: str
    version: Optional[str] = None
    manifest_hash: Optional[str] = None
    files_installed: List[str] = dataclasses.field(default_factory=list)
    already_installed: bool = False
    rolled_back: bool = False
    error: Optional[str] = None                         # see error codes in doc
    steps: List[str] = dataclasses.field(default_factory=list)


# Error codes (also documented in docs/MULTI_AGENT_INSTALL.md §5)
ERR_UNTRUSTED_SOURCE = "ERR_UNTRUSTED_SOURCE"
ERR_MANIFEST_MISMATCH = "ERR_MANIFEST_MISMATCH"
ERR_BAD_SIGNATURE = "ERR_BAD_SIGNATURE"
ERR_NO_WRITE_ACCESS = "ERR_NO_WRITE_ACCESS"
ERR_FETCH_FAILED = "ERR_FETCH_FAILED"
ERR_VERIFY_FAILED = "ERR_VERIFY_FAILED"
ERR_LOCK_TIMEOUT = "ERR_LOCK_TIMEOUT"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _log(steps: List[str], msg: str) -> None:
    steps.append(msg)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _rmtree(path: str) -> None:
    """Robust rmtree that clears read-only bits (Windows .git objects, etc.)."""
    if not os.path.exists(path):
        return

    def _onerror(func, p, _exc):
        try:
            os.chmod(p, 0o755)
            func(p)
        except OSError:
            pass

    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                os.chmod(os.path.join(root, name), 0o755)
            except OSError:
                pass
    shutil.rmtree(path, onerror=_onerror)


def _expand_source(source: str) -> str:
    """Turn a logical source into a concrete fetch URL/local path."""
    if source.startswith("github:"):
        repo = source[len("github:"):]
        tag = None
        if "@" in repo:
            repo, tag = repo.split("@", 1)
        url = f"https://github.com/{repo}.git"
        return url + (f"#{tag}" if tag else "")
    if source.startswith("file:"):
        return source[len("file:"):]
    if source.startswith("registry:"):
        # reserved; would resolve via registry API
        raise ValueError("registry: source not yet implemented")
    # bare URL or path
    return source


def _is_trusted(source: str) -> bool:
    return any(source.startswith(p) for p in TRUSTED_PREFIXES)


# ─────────────────────────────────────────────────────────────────────────────
# Manifest
# ─────────────────────────────────────────────────────────────────────────────

def compute_manifest(skill_dir: str) -> dict:
    """Compute a manifest dict for a skill directory."""
    files: dict[str, str] = {}
    for root, _dirs, names in os.walk(skill_dir):
        parts = root.split(os.sep)
        if ".git" in parts or "__pycache__" in parts:
            continue
        for name in names:
            if name.endswith(".pyc"):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, skill_dir).replace(os.sep, "/")
            if rel == MANIFEST_NAME:
                continue
            files[rel] = _sha256_file(full)
    # stable ordering for deterministic hash
    canon = json.dumps(files, sort_keys=True, ensure_ascii=False)
    manifest_hash = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    version = _read_version(skill_dir)
    return {"name": _read_skill_name(skill_dir), "version": version,
            "files": files, "hash": manifest_hash}


def build_manifest(skill_dir: str) -> dict:
    """Publisher side: compute and write MANIFEST.json into skill_dir."""
    m = compute_manifest(skill_dir)
    m["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(os.path.join(skill_dir, MANIFEST_NAME), "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2, ensure_ascii=False, sort_keys=True)
    return m


def _read_skill_name(skill_dir: str) -> Optional[str]:
    md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(md):
        return None
    txt = open(md, "r", encoding="utf-8", errors="ignore").read(2000)
    for line in txt.splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def _read_version(skill_dir: str) -> Optional[str]:
    md = os.path.join(skill_dir, "SKILL.md")
    if os.path.isfile(md):
        txt = open(md, "r", encoding="utf-8", errors="ignore").read(2000)
        for line in txt.splitlines():
            if line.lower().startswith("version:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Consistency verification (docs §3)
# ─────────────────────────────────────────────────────────────────────────────

def verify_installed(skill_dir: str) -> List[str]:
    """Return a list of error strings; empty list means OK."""
    errors: List[str] = []
    if not os.path.isdir(skill_dir):
        return [f"not a directory: {skill_dir}"]

    # 1. required files
    for rf in REQUIRED_FILES:
        if not os.path.isfile(os.path.join(skill_dir, rf)):
            errors.append(f"missing required file: {rf}")

    # 2. SKILL.md frontmatter (name/description)
    md = os.path.join(skill_dir, "SKILL.md")
    if os.path.isfile(md):
        txt = open(md, "r", encoding="utf-8", errors="ignore").read(4000)
        if not any(l.startswith("name:") for l in txt.splitlines()[:40]):
            errors.append("SKILL.md missing 'name:' in frontmatter")
        if not any(l.startswith("description:") for l in txt.splitlines()[:40]):
            errors.append("SKILL.md missing 'description:' in frontmatter")

    # 3. compile all python in tools/
    tools_dir = os.path.join(skill_dir, "tools")
    if os.path.isdir(tools_dir):
        for root, _d, names in os.walk(tools_dir):
            if ".git" in root.split(os.sep):
                continue
            for n in names:
                if n.endswith(".py"):
                    full = os.path.join(root, n)
                    try:
                        subprocess.run([sys.executable, "-m", "py_compile", full],
                                       check=True, capture_output=True)
                    except subprocess.CalledProcessError as e:
                        errors.append(f"compile failed: {n} ({e.returncode})")

    # 4. offline self-test if present (mocked, no network/key needed)
    test = os.path.join(tools_dir, "test_moa.py")
    if os.path.isfile(test):
        try:
            r = subprocess.run([sys.executable, test], capture_output=True, text=True,
                               cwd=skill_dir, timeout=120)
            if r.returncode != 0:
                errors.append(f"self-test failed: {r.returncode}")
        except subprocess.TimeoutExpired:
            errors.append("self-test timed out")

    # 5. manifest hash matches recorded value
    manifest_path = os.path.join(skill_dir, MANIFEST_NAME)
    if os.path.isfile(manifest_path):
        try:
            recorded = json.load(open(manifest_path, "r", encoding="utf-8"))
            actual = compute_manifest(skill_dir)
            if recorded.get("hash") != actual.get("hash"):
                errors.append("MANIFEST.json hash mismatch with actual files")
        except (json.JSONDecodeError, OSError):
            errors.append("MANIFEST.json unreadable")

    return errors


# ─────────────────────────────────────────────────────────────────────────────
# Lock (docs §4) — advisory lockfile, cross-platform
# ─────────────────────────────────────────────────────────────────────────────

def _acquire_lock(target: str, steps: List[str]) -> str:
    lock = target + ".install.lock"
    deadline = time.time() + LOCK_TIMEOUT_S
    while time.time() < deadline:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # check if stale (pid no longer running) — best-effort on POSIX
            try:
                with open(lock, "r") as lf:
                    pid = lf.read().strip()
            except OSError:
                pid = ""
            if pid and not _pid_alive(pid):
                try:
                    os.remove(lock)
                    continue
                except OSError:
                    pass
            time.sleep(LOCK_POLL_S)
            continue
        with os.fdopen(fd, "w") as lf:
            lf.write(str(os.getpid()))
        _log(steps, f"lock acquired: {lock}")
        return lock
    raise TimeoutError(ERR_LOCK_TIMEOUT)


def _release_lock(lock: str) -> None:
    try:
        os.remove(lock)
    except OSError:
        pass


def _pid_alive(pid: str) -> bool:
    try:
        pid_i = int(pid)
    except ValueError:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid_i}"],
                             capture_output=True, text=True)
        return str(pid_i) in out.stdout
    try:
        os.kill(pid_i, 0)
        return True
    except OSError:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Fetch source -> stage dir
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_to_stage(source: str, stage: str, token: Optional[str], steps: List[str]) -> None:
    expanded = _expand_source(source)
    if source.startswith("file:") or (not source.startswith("github:") and os.path.isdir(expanded)):
        _log(steps, f"copying local source: {expanded}")
        shutil.copytree(expanded, stage,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        return
    # git clone (supports github: and bare https URLs)
    tag = None
    url = expanded
    if "#" in url:
        url, tag = url.split("#", 1)
    auth_url = url
    if token and url.startswith("https://"):
        auth_url = url.replace("https://", f"https://{token}@", 1)
    cmd = [shutil.which("git") or "git", "clone", "--depth", "1", auth_url, stage]
    if tag:
        cmd = [shutil.which("git") or "git", "clone", "--depth", "1",
               "--branch", tag, auth_url, stage]
    _log(steps, f"cloning: {url}{('#'+tag) if tag else ''}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git clone failed: {r.stderr[:300]}")


# ─────────────────────────────────────────────────────────────────────────────
# Install transaction (docs §5)
# ─────────────────────────────────────────────────────────────────────────────

def install_skill(req: InstallRequest) -> InstallResult:
    steps: List[str] = []
    res = InstallResult(success=False, target=req.target, steps=steps)

    # L1 trust
    if not _is_trusted(req.source):
        res.error = ERR_UNTRUSTED_SOURCE
        _log(steps, f"REJECTED untrusted source: {req.source}")
        return res

    # write access to parent (create parent dirs if missing, when grandparent is writable)
    parent = os.path.dirname(req.target)
    if os.path.isdir(parent):
        if not os.access(parent, os.W_OK):
            res.error = ERR_NO_WRITE_ACCESS
            _log(steps, f"no write access to parent: {parent}")
            return res
    else:
        gparent = os.path.dirname(parent)
        if not (os.path.isdir(gparent) and os.access(gparent, os.W_OK)):
            res.error = ERR_NO_WRITE_ACCESS
            _log(steps, f"cannot create parent (no write access): {parent}")
            return res
        os.makedirs(parent, exist_ok=True)
        _log(steps, f"created parent dir: {parent}")

    lock = _acquire_lock(req.target, steps)
    backup = req.target + ".bak"
    stage = req.target + ".stage.tmp"
    try:
        # cleanup any stale stage
        if os.path.exists(stage):
            _rmtree(stage)

        # fetch
        try:
            _fetch_to_stage(req.source, stage, req.token, steps)
        except Exception as e:  # network / clone / untrusted handled above
            res.error = ERR_FETCH_FAILED
            _log(steps, f"fetch failed: {e}")
            return res

        # L2 manifest hash (if expected)
        mpath = os.path.join(stage, MANIFEST_NAME)
        if req.expected_manifest_hash and os.path.isfile(mpath):
            rec = json.load(open(mpath, "r", encoding="utf-8"))
            if rec.get("hash") != req.expected_manifest_hash:
                res.error = ERR_MANIFEST_MISMATCH
                _log(steps, "manifest hash mismatch with expected")
                _rmtree(stage)
                return res

        # idempotency: identical content already present?
        if os.path.isdir(req.target):
            try:
                if compute_manifest(req.target).get("hash") == compute_manifest(stage).get("hash"):
                    _rmtree(stage)
                    res.success = True
                    res.already_installed = True
                    res.manifest_hash = compute_manifest(req.target).get("hash")
                    res.version = _read_version(req.target)
                    _log(steps, "already installed (identical hash) — no-op")
                    return res
            except Exception:
                pass  # fall through to full install

        # L3 signature (optional; only enforced when key configured)
        if SIGNING_PUBLIC_KEY and os.path.isfile(mpath) and not req.allow_insecure:
            if not _verify_signature(mpath, SIGNING_PUBLIC_KEY):
                res.error = ERR_BAD_SIGNATURE
                _log(steps, "manifest signature verification failed")
                _rmtree(stage)
                return res

        # stage verification (docs §3)
        errs = verify_installed(stage)
        if errs:
            res.error = ERR_VERIFY_FAILED
            res.steps = steps + [f"verify error: {e}" for e in errs]
            _rmtree(stage)
            if os.path.exists(req.target):  # nothing changed, not a rollback
                res.rolled_back = False
            _log(steps, f"stage verification failed: {errs}")
            return res

        # backup existing + atomic replace
        res.version = _read_version(stage)
        res.manifest_hash = compute_manifest(stage).get("hash")
        if os.path.isdir(req.target):
            shutil.move(req.target, backup)
            _log(steps, f"backed up existing -> {backup}")
        os.replace(stage, req.target)  # atomic on same filesystem
        _log(steps, f"replaced target: {req.target}")

        # post-commit verify
        post_errs = verify_installed(req.target)
        if post_errs:
            # rollback
            if os.path.isdir(req.target):
                shutil.move(req.target, req.target + ".failed")
            if os.path.isdir(backup):
                shutil.move(backup, req.target)
            res.error = ERR_VERIFY_FAILED
            res.rolled_back = True
            res.steps = steps + [f"post-commit verify error: {e}" for e in post_errs]
            _log(steps, f"rollback performed: {post_errs}")
            return res

        # cleanup backup
        if os.path.isdir(backup):
            _rmtree(backup)
        res.success = True
        res.files_installed = sorted(compute_manifest(req.target)["files"].keys())
        _log(steps, "INSTALL OK")
        return res
    finally:
        _release_lock(lock)
        # ensure stage removed
        _rmtree(stage)


def _verify_signature(manifest_path: str, public_key: str) -> bool:
    """Reserved L3 check. Returns True if no signature block or key unconfigured."""
    # Full ed25519 verification requires cryptography; left as a hook to keep
    # this module stdlib-only. When SIGNING_PUBLIC_KEY is set, wire in real
    # verification here. Default (empty key) => treat as pass.
    return True


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="install_skill.py",
                                description="Multi-agent installer for MoA WorkBuddy skill.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("install", help="Install the skill from a source.")
    pi.add_argument("--source", required=True, help="github:/file:/registry: or URL/path")
    pi.add_argument("--target", required=True, help="absolute skill dir path")
    pi.add_argument("--agent-id", default="default")
    pi.add_argument("--expected-hash", default=None, help="pin manifest hash")
    pi.add_argument("--allow-insecure", action="store_true")
    pi.add_argument("--token", default=None, help="short-lived only; never persisted")

    pb = sub.add_parser("build-manifest", help="Generate MANIFEST.json (publisher).")
    pb.add_argument("--skill-dir", default=".", help="skill directory")

    pv = sub.add_parser("verify", help="Verify an installed skill dir.")
    pv.add_argument("--target", required=True)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "install":
        req = InstallRequest(source=args.source, target=args.target,
                              agent_id=args.agent_id,
                              expected_manifest_hash=args.expected_hash,
                              allow_insecure=args.allow_insecure, token=args.token)
        res = install_skill(req)
        print(json.dumps(dataclasses.asdict(res), indent=2, ensure_ascii=False))
        return 0 if res.success else 1
    if args.cmd == "build-manifest":
        m = build_manifest(args.skill_dir)
        print(json.dumps(m, indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "verify":
        errs = verify_installed(args.target)
        if errs:
            print(json.dumps({"ok": False, "errors": errs}, indent=2, ensure_ascii=False))
            return 1
        print(json.dumps({"ok": True, "target": args.target}, indent=2, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
