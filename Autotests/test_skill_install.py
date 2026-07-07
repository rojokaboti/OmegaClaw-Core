"""Unit tests for the skill install/update lifecycle (Issue #12).

Pure-Python; imports src/skill_install.py directly. Git sources are tested against a TEMP
LOCAL git repo (real `git`, no network); ClawHub against a localhost http.server fixture
(real HTTP, no external network). Runs under pytest and standalone
(`python3 Autotests/test_skill_install.py`).
"""
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import skill_install as si  # noqa: E402
import skill_loader as sl  # noqa: E402


def _bundle(root, name, desc="a skill", version="1.0.0"):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: {}\ndescription: {}\nversion: {}\n---\n# {}\nbody\n".format(name, desc, version, name))
    return d


def _cfg(tmp):
    return {"version": 1, "roots": [os.path.join(tmp, "installed")]}


def test_local_install_idempotent_and_locked():
    tmp = tempfile.mkdtemp(prefix="si_local_")
    cfg = _cfg(tmp)
    src = os.path.join(tmp, "src")
    _bundle(src, "alpha")
    r = si.install("local:" + src, cfg)
    assert r["ok"] and r["installed"][0]["name"] == "alpha"
    root = si.install_root(cfg)
    lock = si._load_lock(root)
    e = lock["skills"]["alpha"]
    assert e["source_type"] == "local" and e["content_hash"] and e["version"] == "1.0.0"
    assert e["trust"] == "unverified" and os.path.exists(os.path.join(root, "alpha", si._ORIGIN_NAME))
    # reinstall -> idempotent, single dir + single lock entry
    si.install("local:" + src, cfg)
    assert [d for d in os.listdir(root) if d == "alpha"] == ["alpha"]
    assert list(si._load_lock(root)["skills"]) == ["alpha"]


def test_rollback_on_invalid_source_leaves_root_untouched():
    tmp = tempfile.mkdtemp(prefix="si_rb_")
    cfg = _cfg(tmp)
    _bundle(os.path.join(tmp, "good"), "good")
    si.install("local:" + os.path.join(tmp, "good"), cfg)
    root = si.install_root(cfg)
    before = sorted(os.listdir(root))
    bad = os.path.join(tmp, "bad", "bad")
    os.makedirs(bad)
    with open(os.path.join(bad, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("# no frontmatter\n")
    r = si.install("local:" + os.path.join(tmp, "bad"), cfg)
    assert not r["ok"] and sorted(os.listdir(root)) == before


def test_verify_detects_tamper():
    tmp = tempfile.mkdtemp(prefix="si_vf_")
    cfg = _cfg(tmp)
    _bundle(os.path.join(tmp, "s"), "s")
    si.install("local:" + os.path.join(tmp, "s"), cfg)
    assert si.verify(cfg=cfg)["ok"]
    with open(os.path.join(si.install_root(cfg), "s", "SKILL.md"), "a", encoding="utf-8") as f:
        f.write("\nmutated\n")
    assert si.verify("s", cfg)["skills"][0]["status"] == "tampered"


def test_pin_protects_from_update_all_but_named_update_works():
    tmp = tempfile.mkdtemp(prefix="si_pin_")
    cfg = _cfg(tmp)
    _bundle(os.path.join(tmp, "p"), "p")
    si.install("local:" + os.path.join(tmp, "p"), cfg)
    si.pin("p", cfg)
    up = si.update(cfg=cfg, all_skills=True)
    assert {u["name"]: u["status"] for u in up["updated"]}["p"] == "skipped_pinned"
    # explicit named update of a pinned skill still runs
    up2 = si.update("p", cfg)
    assert up2["updated"][0]["status"] == "updated"


def test_remove():
    tmp = tempfile.mkdtemp(prefix="si_rm_")
    cfg = _cfg(tmp)
    _bundle(os.path.join(tmp, "z"), "z")
    si.install("local:" + os.path.join(tmp, "z"), cfg)
    assert si.remove("z", cfg)["ok"]
    assert not os.path.isdir(os.path.join(si.install_root(cfg), "z"))
    assert si.remove("z", cfg)["ok"] is False   # already gone


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, capture_output=True, text=True)


def test_git_install_from_local_repo():
    if shutil.which("git") is None:
        return  # git not available on this host
    tmp = tempfile.mkdtemp(prefix="si_git_")
    repo = os.path.join(tmp, "repo")
    _bundle(repo, "gitskill", desc="from git")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    cfg = _cfg(tmp)
    r = si.install("git+" + repo, cfg)
    assert r["ok"], r
    root = si.install_root(cfg)
    assert os.path.isdir(os.path.join(root, "gitskill"))
    assert not os.path.exists(os.path.join(root, "gitskill", ".git"))   # VCS metadata dropped
    assert si._load_lock(root)["skills"]["gitskill"]["source_type"] == "git"


def _serve_registry(directory):
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    import functools
    handler = functools.partial(SimpleHTTPRequestHandler, directory=directory)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def test_clawhub_install_from_http_fixture():
    import json
    tmp = tempfile.mkdtemp(prefix="si_clawhub_")
    registry = os.path.join(tmp, "registry")
    os.makedirs(registry)
    # build an archive tar.gz containing the bundle dir
    bundle_root = os.path.join(tmp, "bundle")
    _bundle(bundle_root, "hubskill", desc="from clawhub", version="2.1.0")
    with tarfile.open(os.path.join(registry, "hubskill.tar.gz"), "w:gz") as tar:
        tar.add(os.path.join(bundle_root, "hubskill"), arcname="hubskill")
    with open(os.path.join(registry, "hubskill.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "hubskill", "version": "2.1.0", "archive": "hubskill.tar.gz"}, f)

    httpd = _serve_registry(registry)
    try:
        os.environ["OMEGACLAW_CLAWHUB_URL"] = "http://127.0.0.1:{}".format(httpd.server_address[1])
        cfg = _cfg(tmp)
        r = si.install("clawhub:hubskill", cfg)
        assert r["ok"], r
        root = si.install_root(cfg)
        assert os.path.isdir(os.path.join(root, "hubskill"))
        e = si._load_lock(root)["skills"]["hubskill"]
        assert e["source_type"] == "clawhub" and e["version"] == "2.1.0" and e["content_hash"]
    finally:
        httpd.shutdown()
        os.environ.pop("OMEGACLAW_CLAWHUB_URL", None)


def test_source_parsing():
    assert si.parse_source("git:owner/repo@v1") == ("git", "https://github.com/owner/repo.git", "v1")
    assert si.parse_source("clawhub:slug@3.0") == ("clawhub", "slug", "3.0")
    assert si.parse_source("local:/a/b") == ("local", "/a/b", None)
    assert si.parse_source("https://h/x.git@main") == ("git", "https://h/x.git", "main")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok:", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL:", fn.__name__, e)
    if failed:
        sys.exit(1)
    print(f"\nAll {len(fns)} skill_install tests passed")
