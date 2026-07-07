"""Skill install / update lifecycle with lock metadata (Issue #12).

A loader (#11) + eligibility (#13) still leaves users hand-copying skills and tracking
versions. This adds the install lifecycle OpenClaw has: install a `SKILL.md` bundle from a
**local path**, a **Git repo** (pinned ref), or a **ClawHub-compatible HTTP registry** (slug),
recording origin + lock metadata so updates are idempotent and pinned skills are never
clobbered.

Safety seam (never corrupt the active skill root):

    fetch  -> a temp staging dir (local copy / git clone / registry archive)
    validate -> skill_loader.load_skills over staging (must yield >=1 valid SKILL.md)
    commit -> atomically replace <root>/<name> ONLY after validation passes
    lock   -> write per-skill origin.json + a workspace lockfile entry

A validation/fetch failure raises before anything under the active root is touched, so a bad
source is a no-op (rollback). Idempotent: reinstalling a source replaces its dir in place (no
duplicates) and rewrites its lock entry. Pinned skills are skipped by ``update --all``.

Trust is recorded as ``unverified`` (the full install trust boundary — sandboxing, approvals,
static scanning — is Issue #19). Stdlib only (git via subprocess, registry via urllib).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import skill_loader
except ImportError:  # pragma: no cover
    from src import skill_loader

try:
    import install_policy
except ImportError:  # pragma: no cover
    from src import install_policy

_LOCK_NAME = ".omegaclaw-skills.lock.json"
_ORIGIN_NAME = ".omegaclaw-origin.json"
_GIT_TIMEOUT = int(os.environ.get("OMEGACLAW_GIT_TIMEOUT", "60"))
_HTTP_TIMEOUT = int(os.environ.get("OMEGACLAW_HTTP_TIMEOUT", "30"))


class SkillInstallError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- source specs

def parse_source(spec: str) -> Tuple[str, str, Optional[str]]:
    """Parse an install spec into ``(source_type, location, ref)``.

    Forms: ``local:<path>`` / a bare existing path; ``git:owner/repo@ref`` /
    ``git+<url>@ref`` / ``<url>.git@ref``; ``clawhub:<slug>@<version>`` / a bare slug.
    """
    s = (spec or "").strip()
    if not s:
        raise SkillInstallError("empty install source")
    if s.startswith("local:"):
        return "local", s[len("local:"):], None
    if s.startswith("git:"):
        loc, ref = _split_ref(s[len("git:"):])
        if "://" not in loc and "@" not in loc and loc.count("/") == 1:
            loc = "https://github.com/{}.git".format(loc)          # owner/repo shorthand
        return "git", loc, ref
    if s.startswith("git+"):
        loc, ref = _split_ref(s[len("git+"):])
        return "git", loc, ref
    if s.startswith("clawhub:"):
        loc, ref = _split_ref(s[len("clawhub:"):])
        return "clawhub", loc, ref
    if s.endswith(".git") or "@" in s and s.rsplit("@", 1)[0].endswith(".git"):
        loc, ref = _split_ref(s)
        return "git", loc, ref
    if os.path.exists(s):
        return "local", s, None
    loc, ref = _split_ref(s)
    return "clawhub", loc, ref


def _split_ref(s: str) -> Tuple[str, Optional[str]]:
    """Split a trailing ``@ref`` (only when the tail is ref-like: no ``/`` or ``:``)."""
    if "@" in s:
        head, tail = s.rsplit("@", 1)
        if head and "/" not in tail and ":" not in tail:
            return head, tail
    return s, None


# --------------------------------------------------------------------------- fetch adapters

def _fetch(source_type: str, location: str, ref: Optional[str], dest: str) -> Optional[str]:
    """Populate ``dest`` with the fetched source tree. Returns an optional version string."""
    if source_type == "local":
        src = location if os.path.isabs(location) else os.path.abspath(location)
        if not os.path.isdir(src):
            raise SkillInstallError("local source is not a directory: {}".format(location))
        # symlinks=True: copy links AS links (never dereference), so a symlinked payload
        # cannot smuggle outside content into staging. Such bundles are rejected at commit.
        shutil.copytree(src, dest, symlinks=True)
        return None
    if source_type == "git":
        return _fetch_git(location, ref, dest)
    if source_type == "clawhub":
        return _fetch_clawhub(location, ref, dest)
    raise SkillInstallError("unknown source type: {}".format(source_type))


def _fetch_git(url: str, ref: Optional[str], dest: str) -> Optional[str]:
    if shutil.which("git") is None:
        raise SkillInstallError("git not found on PATH (required for git sources)")
    try:
        subprocess.run(["git", "clone", "--quiet", url, dest],
                       check=True, capture_output=True, text=True, timeout=_GIT_TIMEOUT)
        if ref:
            subprocess.run(["git", "-C", dest, "checkout", "--quiet", ref],
                           check=True, capture_output=True, text=True, timeout=_GIT_TIMEOUT)
    except subprocess.CalledProcessError as e:
        raise SkillInstallError("git fetch failed: {}".format((e.stderr or e.stdout or str(e)).strip()))
    except subprocess.TimeoutExpired:
        raise SkillInstallError("git fetch timed out after {}s".format(_GIT_TIMEOUT))
    shutil.rmtree(os.path.join(dest, ".git"), ignore_errors=True)   # drop VCS metadata
    return ref


def _fetch_clawhub(slug: str, ref: Optional[str], dest: str) -> Optional[str]:
    base = os.environ.get("OMEGACLAW_CLAWHUB_URL")
    if not base:
        raise SkillInstallError("OMEGACLAW_CLAWHUB_URL is not set (required for clawhub sources)")
    meta_url = "{}/{}.json".format(base.rstrip("/"), slug)
    try:
        with urllib.request.urlopen(meta_url, timeout=_HTTP_TIMEOUT) as r:
            meta = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise SkillInstallError("clawhub metadata fetch failed ({}): {}".format(meta_url, e))
    archive = meta.get("archive")
    if not archive:
        raise SkillInstallError("clawhub metadata for {!r} has no 'archive' url".format(slug))
    if "://" not in archive:
        archive = "{}/{}".format(base.rstrip("/"), archive.lstrip("/"))
    os.makedirs(dest, exist_ok=True)
    tmp_tar = dest + ".tar.gz"
    try:
        with urllib.request.urlopen(archive, timeout=_HTTP_TIMEOUT) as r, open(tmp_tar, "wb") as f:
            shutil.copyfileobj(r, f)
        with tarfile.open(tmp_tar, "r:gz") as tar:
            _safe_extract(tar, dest)
    except SkillInstallError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SkillInstallError("clawhub archive fetch failed ({}): {}".format(archive, e))
    finally:
        if os.path.exists(tmp_tar):
            os.remove(tmp_tar)
    return meta.get("version") or ref


def _safe_extract(tar: tarfile.TarFile, dest: str) -> None:
    """Extract with path-traversal protection (uses the stdlib 'data' filter when available)."""
    try:
        tar.extractall(dest, filter="data")   # Python 3.12+: rejects traversal/absolute paths
        return
    except TypeError:  # pragma: no cover - older Python without the filter kwarg
        dest_real = os.path.realpath(dest)
        for m in tar.getmembers():
            target = os.path.realpath(os.path.join(dest, m.name))
            if not (target == dest_real or target.startswith(dest_real + os.sep)):
                raise SkillInstallError("archive member escapes extraction dir: {}".format(m.name))
        tar.extractall(dest)


# --------------------------------------------------------------------------- lock / origin / hash

def install_root(cfg: Optional[Dict[str, Any]] = None) -> str:
    cfg = cfg if cfg is not None else skill_loader.load_config()
    roots = skill_loader._roots(cfg)
    if not roots:
        raise SkillInstallError("no skill roots configured (profile/skills.yaml 'roots')")
    root = roots[0]
    os.makedirs(root, exist_ok=True)
    return root


def _safe_dest(root: str, name: str) -> str:
    """Resolve ``<root>/<name>`` and prove it stays inside ``root`` before any write/delete.

    Two independent guards (Issue #12 review — a destructive path-traversal fix): reject an
    unsafe name (``..`` / separators / absolute / empty), then realpath-confirm containment.
    """
    if not skill_loader.is_safe_skill_name(name):
        raise SkillInstallError("unsafe skill name: {!r}".format(name))
    dest = os.path.join(root, name)
    root_real = os.path.realpath(root)
    dest_real = os.path.realpath(dest)
    try:
        contained = os.path.commonpath([root_real, dest_real]) == root_real
    except ValueError:  # different drives / mixed abs-rel
        contained = False
    if not contained:
        raise SkillInstallError("skill path escapes the install root: {!r}".format(name))
    return dest


def _lock_path(root: str) -> str:
    return os.path.join(root, _LOCK_NAME)


def _load_lock(root: str) -> Dict[str, Any]:
    path = _lock_path(root)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("skills"), dict):
                return data
        except Exception:  # noqa: BLE001 - a corrupt lock starts fresh (entries are re-derivable)
            pass
    return {"version": 1, "skills": {}}


def _save_lock(root: str, lock: Dict[str, Any]) -> None:
    tmp = _lock_path(root) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lock, f, indent=2, sort_keys=True)
    os.replace(tmp, _lock_path(root))


def _hash_dir(path: str) -> str:
    """Deterministic content hash of a bundle dir, excluding our own origin metadata."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames.sort()
        for fn in sorted(filenames):
            if fn == _ORIGIN_NAME:
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, path).replace(os.sep, "/")
            h.update(rel.encode("utf-8") + b"\0")
            with open(fp, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
    return h.hexdigest()


def _write_origin(dest: str, meta: Dict[str, Any]) -> None:
    with open(os.path.join(dest, _ORIGIN_NAME), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)


def _contains_symlink(root: str) -> bool:
    """True if ``root`` itself or anything within it is a symlink (not followed)."""
    if os.path.islink(root):
        return True
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for nm in dirnames + filenames:
            if os.path.islink(os.path.join(dirpath, nm)):
                return True
    return False


def _replace_dir(dest: str, src: str) -> None:
    """Replace ``dest`` with a copy of ``src`` (idempotent — no duplicate dirs).

    ``symlinks=True`` so links are never dereferenced during commit (defensive — bundles
    containing symlinks are already rejected before this is called)."""
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest, symlinks=True)


# --------------------------------------------------------------------------- install / update

def install(source: str, cfg: Optional[Dict[str, Any]] = None, *,
            pin: bool = False, force: bool = False, trust: str = "unverified") -> Dict[str, Any]:
    """Install skill bundle(s) from ``source``. Returns a structured result dict.

    Fetch->validate->commit->lock with rollback: nothing under the active root changes
    unless staging validates. Idempotent; a pinned skill is skipped unless ``force``.
    """
    cfg = cfg if cfg is not None else skill_loader.load_config()
    try:
        source_type, location, ref = parse_source(source)
        root = install_root(cfg)
    except SkillInstallError as e:
        return {"ok": False, "error": str(e)}

    staging = tempfile.mkdtemp(prefix="omegaclaw-skill-stage-")
    fetch_dir = os.path.join(staging, "fetch")
    try:
        version = _fetch(source_type, location, ref, fetch_dir)
        # validate the staged tree — must yield at least one valid SKILL.md bundle
        skill_loader.reset_cache()
        skills, errors = skill_loader.load_skills({"version": 1, "roots": [fetch_dir]})
        if not skills:
            detail = ("; " + errors[0].message) if errors else ""
            raise SkillInstallError("no valid SKILL.md bundle in source{}".format(detail))

        lock = _load_lock(root)
        installed: List[Dict[str, Any]] = []
        for name in sorted(skills):
            sk = skills[name]
            prev = lock["skills"].get(name)
            if prev and prev.get("pinned") and not force:
                installed.append({"name": name, "status": "skipped_pinned"})
                continue
            # Reject a bundle that carries a symlink: it could dereference to content
            # outside the source (exfiltration / smuggling). Fail-closed, skip commit.
            if _contains_symlink(sk.base_dir):
                installed.append({"name": name, "status": "rejected_symlink"})
                continue
            try:
                dest = _safe_dest(root, name)                     # name/containment guard
            except SkillInstallError as e:
                installed.append({"name": name, "status": "rejected_unsafe_name", "error": str(e)})
                continue
            # Static trust scan of the staged bundle (Issue #19). Fail-closed: a HIGH finding
            # (exfil / destructive / credential / suspicious-exec) blocks the install in the
            # default non-interactive mode; MEDIUM (undeclared env) only flags. The scan verdict
            # is recorded as the skill's trust (replacing the old blanket "unverified").
            report = install_policy.scan_bundle(sk.base_dir, declared_env=sk.required_environment_variables)
            decision = install_policy.decide(report)
            if decision.action != "allow":
                installed.append({"name": name, "status": "rejected_policy",
                                  "reasons": decision.reasons})
                continue
            content_hash = _hash_dir(sk.base_dir)
            meta = {
                "name": name, "source_type": source_type, "source": location, "ref": ref,
                "version": sk.version or version or "", "content_hash": content_hash,
                "installed_at": _now(), "trust": trust if trust != "unverified" else decision.trust,
                "pinned": bool(pin or (prev.get("pinned") if prev else False)),
            }
            _replace_dir(dest, sk.base_dir)                       # commit AFTER validation
            _write_origin(dest, meta)
            lock["skills"][name] = meta
            installed.append({"name": name, "status": "installed",
                              "version": meta["version"], "content_hash": content_hash})
        _save_lock(root, lock)
        skill_loader.reset_cache()
        rejected = [i for i in installed if str(i["status"]).startswith("rejected")]
        result = {"ok": not rejected, "root": root, "source_type": source_type, "installed": installed}
        if rejected:
            result["error"] = "{} bundle(s) rejected: ".format(len(rejected)) + ", ".join(
                "{} ({})".format(i["name"], i["status"]) for i in rejected)
        return result
    except SkillInstallError as e:
        return {"ok": False, "error": str(e)}          # active root untouched
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "install failed: {}".format(e)}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _entry_source_spec(entry: Dict[str, Any]) -> str:
    st, loc, ref = entry.get("source_type"), entry.get("source"), entry.get("ref")
    spec = {"local": "local:", "git": "git+", "clawhub": "clawhub:"}.get(st, "") + str(loc)
    return spec + ("@" + ref if ref else "")


def update(name: Optional[str] = None, cfg: Optional[Dict[str, Any]] = None, *,
           all_skills: bool = False) -> Dict[str, Any]:
    """Reinstall from the recorded source. ``name`` updates one (even if pinned);
    ``all_skills`` updates every non-pinned skill (pinned are skipped)."""
    cfg = cfg if cfg is not None else skill_loader.load_config()
    root = install_root(cfg)
    lock = _load_lock(root)
    if name:
        targets = [name] if name in lock["skills"] else []
        if not targets:
            return {"ok": False, "error": "not installed: {}".format(name)}
    elif all_skills:
        targets = sorted(lock["skills"])
    else:
        return {"ok": False, "error": "specify a skill name or all_skills=True"}

    results = []
    for n in targets:
        entry = lock["skills"][n]
        if entry.get("pinned") and not name:
            results.append({"name": n, "status": "skipped_pinned"})
            continue
        # force=True when updating a single named pinned skill (explicit intent)
        r = install(_entry_source_spec(entry), cfg, force=bool(name and entry.get("pinned")))
        # Preserve the inner install outcome (e.g. rejected_symlink) rather than flattening
        # every non-ok into a generic "updated"/"error" — the lifecycle must not misreport a
        # rejected reinstall as a success.
        inner = next((i for i in r.get("installed", []) if i["name"] == n), None)
        if inner:
            status = "updated" if inner["status"] == "installed" else inner["status"]
        else:
            status = "updated" if r.get("ok") else "error"
        results.append({"name": n, "status": status, "error": r.get("error")})
    ok_statuses = {"updated", "skipped_pinned"}
    return {"ok": all(x["status"] in ok_statuses for x in results), "updated": results}


def remove(name: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg if cfg is not None else skill_loader.load_config()
    root = install_root(cfg)
    try:
        dest = _safe_dest(root, name)      # reject traversal BEFORE any rmtree
    except SkillInstallError as e:
        return {"ok": False, "error": str(e)}
    lock = _load_lock(root)
    existed = name in lock["skills"] or os.path.isdir(dest)
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    lock["skills"].pop(name, None)
    _save_lock(root, lock)
    skill_loader.reset_cache()
    return {"ok": existed, "removed": name} if existed else {"ok": False, "error": "not installed: {}".format(name)}


def list_installed(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    root = install_root(cfg if cfg is not None else skill_loader.load_config())
    lock = _load_lock(root)
    return [dict(v) for _, v in sorted(lock["skills"].items())]


def verify(name: Optional[str] = None, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Recompute each installed skill's content hash and compare to the lock. Reports
    ``ok`` / ``tampered`` / ``missing`` per skill."""
    cfg = cfg if cfg is not None else skill_loader.load_config()
    root = install_root(cfg)
    lock = _load_lock(root)
    names = [name] if name else sorted(lock["skills"])
    report = []
    for n in names:
        entry = lock["skills"].get(n)
        if not entry:
            report.append({"name": n, "status": "not_in_lock"})
            continue
        try:
            dest = _safe_dest(root, n)
        except SkillInstallError:
            report.append({"name": n, "status": "unsafe_name"})
            continue
        if not os.path.isdir(dest):
            report.append({"name": n, "status": "missing"})
            continue
        status = "ok" if _hash_dir(dest) == entry.get("content_hash") else "tampered"
        report.append({"name": n, "status": status})
    return {"ok": all(r["status"] == "ok" for r in report) if report else True, "skills": report}


def _set_pin(name: str, pinned: bool, cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = cfg if cfg is not None else skill_loader.load_config()
    root = install_root(cfg)
    lock = _load_lock(root)
    entry = lock["skills"].get(name)
    if not entry:
        return {"ok": False, "error": "not installed: {}".format(name)}
    try:
        dest = _safe_dest(root, name)
    except SkillInstallError as e:
        return {"ok": False, "error": str(e)}
    entry["pinned"] = pinned
    if os.path.isdir(dest):
        _write_origin(dest, entry)
    _save_lock(root, lock)
    return {"ok": True, "name": name, "pinned": pinned}


def pin(name: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _set_pin(name, True, cfg)


def unpin(name: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _set_pin(name, False, cfg)


# --------------------------------------------------------------------------- selftest

def _selftest() -> None:
    import yaml

    tmp = tempfile.mkdtemp(prefix="skill_install_selftest_")
    root = os.path.join(tmp, "installed")
    cfg = {"version": 1, "roots": [root]}

    def _src(name, desc="a skill", extra=""):
        d = os.path.join(tmp, "src-" + name, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: {}\ndescription: {}\nversion: 1.0.0\n---\n# {}\n{}\n".format(name, desc, name, extra))
        return os.path.join(tmp, "src-" + name)

    # local install
    r = install("local:" + _src("alpha"), cfg)
    assert r["ok"] and r["installed"][0]["name"] == "alpha", r
    assert os.path.isdir(os.path.join(root, "alpha"))
    assert os.path.exists(os.path.join(root, "alpha", _ORIGIN_NAME))
    lock = _load_lock(root)
    assert lock["skills"]["alpha"]["content_hash"] and lock["skills"]["alpha"]["source_type"] == "local"

    # idempotent reinstall — no duplicate dir, lock still single entry
    r2 = install("local:" + _src("alpha"), cfg)
    assert r2["ok"] and len([d for d in os.listdir(root) if d == "alpha"]) == 1
    assert list(_load_lock(root)["skills"]) == ["alpha"]

    # verify OK, then tamper -> tampered
    assert verify(cfg=cfg)["ok"]
    with open(os.path.join(root, "alpha", "SKILL.md"), "a", encoding="utf-8") as f:
        f.write("\ntampered\n")
    assert verify("alpha", cfg)["skills"][0]["status"] == "tampered"

    # pin protects against update --all
    install("local:" + _src("beta"), cfg)
    pin("beta", cfg)
    assert _load_lock(root)["skills"]["beta"]["pinned"] is True
    up = update(cfg=cfg, all_skills=True)
    statuses = {x["name"]: x["status"] for x in up["updated"]}
    assert statuses["beta"] == "skipped_pinned", statuses

    # rollback: an invalid source commits nothing and leaves the root unchanged
    bad = os.path.join(tmp, "bad-src", "bad")
    os.makedirs(bad, exist_ok=True)
    with open(os.path.join(bad, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("# no frontmatter\n")
    before = sorted(os.listdir(root))
    rb = install("local:" + os.path.join(tmp, "bad-src"), cfg)
    assert not rb["ok"] and sorted(os.listdir(root)) == before, (rb, before)

    # remove
    assert remove("alpha", cfg)["ok"] and not os.path.isdir(os.path.join(root, "alpha"))
    assert "alpha" not in _load_lock(root)["skills"]

    # source parsing
    assert parse_source("git:owner/repo@v1") == ("git", "https://github.com/owner/repo.git", "v1")
    assert parse_source("clawhub:my-skill@2.0") == ("clawhub", "my-skill", "2.0")
    assert parse_source("local:/x/y")[0] == "local"

    skill_loader.reset_cache()
    print("skill_install self-tests passed")


if __name__ == "__main__":
    _selftest()
