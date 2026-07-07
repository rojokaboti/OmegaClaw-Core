"""Plugin & MCP-style tool registry (Issue #15).

Skills (#11) describe *how* to use capabilities; plugins provide the capabilities. Without a
registry every new integration is a core edit (e.g. `tavily-search`/`technical-analysis` were
wired directly into `src/agentverse.py` + the four protocol surfaces). This adds a discovery +
registration layer so a plugin ships tools **and** skills through a manifest, with **zero core
runtime edits**.

Design (mirrors `channel_registry` for the table + lazy import, and `skill_loader` for config /
mtime cache / catalogue / `_selftest`):

- A plugin is a directory with a manifest (`plugin.json` / `plugin.yaml`): ``id``, ``version``,
  ``entrypoint`` (a Python module in the dir exposing ``register() -> [tool spec, …]``),
  optional ``description`` / ``permissions`` / ``skill_dirs`` / ``requires`` (env/bins/config).
- Plugin **tools** are invoked through ONE generic static tool, ``plugin-invoke <name> <arg>``
  (routed to :func:`invoke`) — the same generic-dispatch pattern as ``use-skill``, so the
  strict-JSON action protocol's core tool set stays stable and no per-plugin MeTTa/ARG_SPEC
  edits are needed. This is also how MCP exposes tools (a generic call interface).
- Plugin **skills** are surfaced through the existing `skill_loader` via :func:`skill_roots`.
- **Failure isolation:** a bad manifest / failing import / duplicate tool name is skipped with
  an actionable error (`errors()`), never crashing the agent. **Disabled** plugins contribute
  neither tools nor skills.

Stdlib only; import-light (entrypoints are imported lazily at load). Default
`profile/plugins.yaml` has no roots, so the out-of-box runtime is unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import yaml

try:
    from redaction import redact_secrets
except ImportError:  # pragma: no cover
    from src.redaction import redact_secrets

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CONFIG_PATH = os.path.join(_REPO_ROOT, "profile", "plugins.yaml")
_MANIFEST_NAMES = ("plugin.json", "plugin.yaml", "plugin.yml")
_MAX_PROMPT_ERRORS = 5

_BUILTIN_DEFAULTS: Dict[str, Any] = {
    "version": 1,
    "roots": [],          # dirs scanned for plugin manifests (none out-of-box)
    "disabled": [],       # plugin ids to skip
    "config": {},         # per-plugin config presence values for requires.config
}

_CACHE: Dict[Any, Any] = {}
_WARNED: set = set()


def _warn_once(msg: str) -> None:
    if msg not in _WARNED:
        print("[plugin_registry] " + msg, flush=True)
        _WARNED.add(msg)


# --------------------------------------------------------------------------- model

@dataclass
class PluginTool:
    name: str
    description: str
    arg: str                       # human name of the single string argument
    handler: Callable[[str], Any]  # (arg_str) -> result
    plugin_id: str


@dataclass
class Plugin:
    id: str
    version: str
    description: str
    dir: str
    tools: Dict[str, PluginTool] = field(default_factory=dict)
    skill_dirs: List[str] = field(default_factory=list)   # absolute
    permissions: List[str] = field(default_factory=list)


@dataclass
class PluginError:
    plugin: str
    message: str

    def __str__(self) -> str:
        return "{}: {}".format(self.plugin, self.message)


# --------------------------------------------------------------------------- config

def config_path() -> str:
    env = os.environ.get("OMEGACLAW_PLUGINS_CONFIG_PATH")
    if not env:
        return _DEFAULT_CONFIG_PATH
    return env if os.path.isabs(env) else os.path.join(_REPO_ROOT, env)


def builtin_defaults() -> Dict[str, Any]:
    import copy
    return copy.deepcopy(_BUILTIN_DEFAULTS)


def validate_config(cfg: Any) -> Optional[str]:
    if not isinstance(cfg, dict):
        return "config root is not a mapping"
    if cfg.get("roots") is not None and not isinstance(cfg.get("roots"), list):
        return "'roots' must be a list"
    if cfg.get("disabled") is not None and not isinstance(cfg.get("disabled"), list):
        return "'disabled' must be a list"
    return None


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or config_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return builtin_defaults()
    key = ("config", path, mtime)
    if key in _CACHE:
        return _CACHE[key]
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:  # noqa: BLE001
        _warn_once("WARNING failed to parse {}: {}; using defaults".format(path, exc))
        _CACHE[key] = builtin_defaults()
        return _CACHE[key]
    if data is None:
        data = builtin_defaults()
    err = validate_config(data)
    if err:
        _warn_once("WARNING invalid config {}: {}; using defaults".format(path, err))
        _CACHE[key] = builtin_defaults()
        return _CACHE[key]
    merged = builtin_defaults()
    merged.update({k: v for k, v in data.items() if v is not None or k in data})
    _CACHE[key] = merged
    return merged


# --------------------------------------------------------------------------- discovery / load

def _roots(cfg: Dict[str, Any]) -> List[str]:
    out = []
    for r in (cfg.get("roots") or []):
        if isinstance(r, str) and r.strip():
            out.append(r if os.path.isabs(r) else os.path.join(_REPO_ROOT, r))
    return out


def _contained(child: str, parent: str) -> bool:
    """True iff ``child`` realpath-resolves to a location at/under ``parent`` (rejects
    symlink escapes and ``..`` traversal)."""
    try:
        c = os.path.realpath(child)
        p = os.path.realpath(parent)
        return os.path.commonpath([c, p]) == p
    except (ValueError, OSError):
        return False


def _manifest_in(d: str) -> Optional[str]:
    for mn in _MANIFEST_NAMES:
        mp = os.path.join(d, mn)
        if os.path.isfile(mp):
            return mp
    return None


def _find_manifests(root_abs: str) -> List[str]:
    """A root may itself be a plugin dir (manifest directly inside) OR a parent containing
    plugin subdirs — both are supported."""
    if not os.path.isdir(root_abs):
        return []
    direct = _manifest_in(root_abs)
    if direct:
        return [direct]
    found = []
    for entry in sorted(os.listdir(root_abs)):
        d = os.path.join(root_abs, entry)
        if os.path.isdir(d):
            mp = _manifest_in(d)
            if mp:
                found.append(mp)
    return found


def _read_manifest(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        if path.endswith(".json"):
            import json
            return json.load(f)
        return yaml.safe_load(f)


def _requirements_met(manifest: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
    """Return a reason string if a declared requirement is unmet, else None."""
    import shutil
    req = manifest.get("requires") or {}
    for var in (req.get("env") or []):
        if not os.environ.get(var):
            return "missing env var {}".format(var)
    for b in (req.get("bins") or []):
        if shutil.which(b) is None:
            return "missing binary {}".format(b)
    conf = cfg.get("config") if isinstance(cfg.get("config"), dict) else {}
    for k in (req.get("config") or []):
        if not conf.get(k):
            return "missing config {}".format(k)
    return None


def _load_entrypoint_tools(plugin_id: str, plugin_dir: str, entrypoint: str) -> List[PluginTool]:
    """Import the entrypoint module (isolated) and collect its tool specs."""
    from importlib.machinery import SourceFileLoader
    from importlib.util import spec_from_loader, module_from_spec

    ep_path = entrypoint if os.path.isabs(entrypoint) else os.path.join(plugin_dir, entrypoint)
    ep_real = os.path.realpath(ep_path)
    if not (ep_real == os.path.realpath(plugin_dir) or
            ep_real.startswith(os.path.realpath(plugin_dir) + os.sep)):
        raise ValueError("entrypoint escapes the plugin dir: {}".format(entrypoint))
    if not os.path.isfile(ep_real):
        raise ValueError("entrypoint not found: {}".format(entrypoint))

    loader = SourceFileLoader("omegaclaw_plugin_" + plugin_id, ep_real)
    mod = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    if not hasattr(mod, "register"):
        raise ValueError("entrypoint has no register() function")
    specs = mod.register()
    if not isinstance(specs, list):
        raise ValueError("register() must return a list of tool specs")

    tools = []
    for sp in specs:
        if not isinstance(sp, dict):
            raise ValueError("tool spec is not a mapping: {!r}".format(sp))
        name = sp.get("name")
        handler = sp.get("handler")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("tool spec missing 'name'")
        if not callable(handler):
            raise ValueError("tool {!r} spec missing callable 'handler'".format(name))
        tools.append(PluginTool(
            name=name.strip(), description=str(sp.get("description", "")).strip(),
            arg=str(sp.get("arg", "input")).strip() or "input", handler=handler, plugin_id=plugin_id))
    return tools


def _requirement_fingerprint(cfg: Dict[str, Any]) -> tuple:
    """Decision-relevant requirement state (only booleans -> secret-safe), so the load cache
    invalidates when a required env var / binary / config value flips (the #13 lesson,
    applied to plugins)."""
    import shutil
    conf = cfg.get("config") if isinstance(cfg.get("config"), dict) else {}
    fp = set()
    for root_abs in _roots(cfg):
        for mp in _find_manifests(root_abs):
            try:
                req = (_read_manifest(mp) or {}).get("requires") or {}
            except Exception:  # noqa: BLE001 - a bad manifest is handled in _load
                continue
            for v in (req.get("env") or []):
                fp.add(("env", str(v), bool(os.environ.get(str(v)))))
            for b in (req.get("bins") or []):
                fp.add(("bin", str(b), shutil.which(str(b)) is not None))
            for k in (req.get("config") or []):
                fp.add(("config", str(k), bool(conf.get(k))))
    return tuple(sorted(fp))


def _signature(cfg: Dict[str, Any]) -> tuple:
    sig: List[Any] = [("disabled", tuple(sorted(cfg.get("disabled") or [])))]
    try:
        sig.append(("cfg", config_path(), os.path.getmtime(config_path())))
    except OSError:
        sig.append(("cfg", config_path(), None))
    for root_abs in _roots(cfg):
        for mp in _find_manifests(root_abs):
            try:
                sig.append((mp, os.path.getmtime(mp)))
            except OSError:
                sig.append((mp, None))
    sig.append(("req", _requirement_fingerprint(cfg)))
    return tuple(sig)


def _load(cfg: Dict[str, Any]):
    disabled = set(cfg.get("disabled") or [])
    plugins: Dict[str, Plugin] = {}
    tools: Dict[str, PluginTool] = {}
    errors: List[PluginError] = []

    for root_abs in _roots(cfg):
        for mp in _find_manifests(root_abs):
            pdir = os.path.dirname(mp)
            rel = os.path.relpath(pdir, root_abs)
            # Containment: a symlinked plugin dir (or one otherwise resolving outside the
            # configured root) is rejected — a symlink under the root must not smuggle in an
            # out-of-root plugin.
            if not _contained(pdir, root_abs):
                errors.append(PluginError(rel, "plugin dir escapes its root (symlink/traversal) — rejected"))
                continue
            try:
                manifest = _read_manifest(mp)
            except Exception as exc:  # noqa: BLE001
                errors.append(PluginError(rel, "unreadable manifest: {}".format(exc)))
                continue
            if not isinstance(manifest, dict):
                errors.append(PluginError(rel, "manifest is not a mapping"))
                continue
            pid = manifest.get("id")
            if not isinstance(pid, str) or not pid.strip():
                errors.append(PluginError(rel, "manifest missing required 'id'"))
                continue
            pid = pid.strip()
            entrypoint = manifest.get("entrypoint")
            if not isinstance(entrypoint, str) or not entrypoint.strip():
                errors.append(PluginError(pid, "manifest missing 'entrypoint'"))
                continue
            if pid in disabled:
                continue
            if pid in plugins:
                errors.append(PluginError(pid, "duplicate plugin id (first-wins)"))
                continue
            unmet = _requirements_met(manifest, cfg)
            if unmet:
                errors.append(PluginError(pid, "requirements not met: {} (plugin skipped)".format(unmet)))
                continue
            try:
                ptools = _load_entrypoint_tools(pid, pdir, entrypoint)
            except Exception as exc:  # noqa: BLE001 - a failing plugin must not crash the agent
                errors.append(PluginError(pid, "load failed: {}".format(exc)))
                continue

            # duplicate tool names across plugins -> reject the colliding tool, keep going
            accepted = {}
            for t in ptools:
                if t.name in tools:
                    errors.append(PluginError(pid, "duplicate tool name {!r} (kept {}'s)".format(
                        t.name, tools[t.name].plugin_id)))
                    continue
                accepted[t.name] = t
            skill_dirs = []
            for sd in (manifest.get("skill_dirs") or []):
                if not (isinstance(sd, str) and sd.strip()):
                    continue
                abs_sd = sd if os.path.isabs(sd) else os.path.join(pdir, sd)
                # A skill_dir must stay inside the plugin dir — a plugin may not contribute an
                # arbitrary outside directory to the skill loader.
                if not _contained(abs_sd, pdir):
                    errors.append(PluginError(pid, "skill_dir {!r} escapes the plugin dir — rejected".format(sd)))
                    continue
                if os.path.isdir(abs_sd):
                    skill_dirs.append(os.path.realpath(abs_sd))
            plug = Plugin(
                id=pid, version=str(manifest.get("version", "")).strip(),
                description=str(manifest.get("description", "")).strip(), dir=pdir,
                tools=accepted, skill_dirs=skill_dirs,
                permissions=[str(p) for p in (manifest.get("permissions") or [])])
            plugins[pid] = plug
            tools.update(accepted)
    return plugins, tools, errors


def ensure_loaded(cfg: Optional[Dict[str, Any]] = None):
    cfg = cfg if cfg is not None else load_config()
    key = ("loaded", _signature(cfg))
    if key in _CACHE:
        return _CACHE[key]
    result = _load(cfg)
    _CACHE[key] = result
    return result


# --------------------------------------------------------------------------- public API

def list_plugins(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    return sorted(ensure_loaded(cfg)[0])


def list_tools(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    return sorted(ensure_loaded(cfg)[1])


def errors(cfg: Optional[Dict[str, Any]] = None) -> List[PluginError]:
    return list(ensure_loaded(cfg)[2])


def skill_roots(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """Absolute skill dirs contributed by enabled plugins (fed to the SKILL.md loader)."""
    try:
        plugins, _tools, _errs = ensure_loaded(cfg)
    except Exception:  # noqa: BLE001
        return []
    roots = []
    for p in plugins.values():
        roots.extend(p.skill_dirs)
    return roots


def invoke(name: str, arg: str = "") -> str:
    """MeTTa bridge for the ``plugin-invoke`` tool: call a plugin tool by name.

    Best-effort — returns an actionable ``PLUGIN-INVOKE-ERROR:`` string on unknown tool or a
    handler exception, never raising into the loop. Output is redacted before it reaches the
    prompt (as LAST_SKILL_USE_RESULTS)."""
    try:
        _plugins, tools, _errs = ensure_loaded()
        tool = tools.get((name or "").strip())
        if tool is None:
            avail = ", ".join(sorted(tools)) or "(none loaded)"
            return "PLUGIN-INVOKE-ERROR: unknown tool {!r}. Available: {}".format(name, avail)
        result = tool.handler(arg)
        return redact_secrets(str(result))
    except Exception as exc:  # noqa: BLE001
        return "PLUGIN-INVOKE-ERROR: {}".format(exc)


def _error_segment(errs: List[PluginError]) -> str:
    shown = errs[:_MAX_PROMPT_ERRORS]
    parts = [redact_secrets(str(e)) for e in shown]
    extra = len(errs) - len(shown)
    if extra > 0:
        parts.append("(+{} more; see logs)".format(extra))
    return "PLUGIN_LOAD_ERRORS: {} plugin(s) skipped — ".format(len(errs)) + "; ".join(parts)


def catalogue_block(cfg: Optional[Dict[str, Any]] = None) -> str:
    """Render the ``PLUGIN_TOOLS`` (+ ``PLUGIN_LOAD_ERRORS``) prompt segment (or "")."""
    try:
        cfg = cfg if cfg is not None else load_config()
        plugins, tools, errs = ensure_loaded(cfg)
        for e in errs:
            _warn_once("PLUGIN_LOAD_ERROR " + redact_secrets(str(e)))
        segments = []
        if tools:
            header = ("PLUGIN_TOOLS: external tools provided by plugins. Call one with: "
                      "plugin-invoke <name> <arg>.")
            lines = []
            for name in sorted(tools):
                t = tools[name]
                desc = t.description or "(no description)"
                lines.append(redact_secrets("- {} ({}): {} [{}]".format(t.name, t.arg, desc, t.plugin_id)))
            segments.append(header + " " + " ".join(lines))
        if errs:
            segments.append(_error_segment(errs))
        return "  ".join(segments)
    except Exception as exc:  # noqa: BLE001
        _warn_once("WARNING catalogue_block failed: {}".format(exc))
        return ""


def reset() -> None:
    _CACHE.clear()
    _WARNED.clear()


# --------------------------------------------------------------------------- selftest

def _selftest() -> None:
    import tempfile
    import textwrap

    reset()
    tmp = tempfile.mkdtemp(prefix="plugin_registry_selftest_")
    root = os.path.join(tmp, "plugins")

    def _plugin(pid, impl, manifest_extra=""):
        d = os.path.join(root, pid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "plugin_impl.py"), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(impl))
        with open(os.path.join(d, "plugin.json"), "w", encoding="utf-8") as f:
            f.write('{{"id":"{}","version":"1.0.0","description":"{} plugin",'
                    '"entrypoint":"plugin_impl.py"{}}}'.format(pid, pid, manifest_extra))
        return d

    # a valid calculator plugin
    _plugin("calculator", '''
        def _calc(expr):
            import ast, operator as op
            ops={ast.Add:op.add,ast.Sub:op.sub,ast.Mult:op.mul,ast.Div:op.truediv}
            def ev(n):
                if isinstance(n, ast.Constant): return n.value
                if isinstance(n, ast.BinOp): return ops[type(n.op)](ev(n.left), ev(n.right))
                raise ValueError("unsupported")
            return ev(ast.parse(expr, mode="eval").body)
        def register():
            return [{"name":"calc","description":"evaluate arithmetic","arg":"expression","handler":_calc}]
    ''')
    # a plugin whose entrypoint import fails -> isolated error
    _plugin("broken", "raise RuntimeError('boom')\ndef register():\n    return []\n")

    cfg = {"version": 1, "roots": [root], "disabled": [], "config": {}}
    plugins, tools, errs = ensure_loaded(cfg)
    assert "calculator" in plugins and "calc" in tools, (list(plugins), list(tools))
    assert any(e.plugin == "broken" for e in errs), errs           # failing import isolated

    # invoke end-to-end
    os.environ["OMEGACLAW_PLUGINS_CONFIG_PATH"] = os.path.join(tmp, "plugins.yaml")
    with open(os.environ["OMEGACLAW_PLUGINS_CONFIG_PATH"], "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f)
    reset()
    assert invoke("calc", "2+3*4") == "14", invoke("calc", "2+3*4")
    assert invoke("nope").startswith("PLUGIN-INVOKE-ERROR:")

    # catalogue advertises the tool + surfaces the broken plugin
    block = catalogue_block(cfg)
    assert "- calc (expression): evaluate arithmetic [calculator]" in block, block
    assert "PLUGIN_LOAD_ERRORS:" in block and "broken" in block

    # disabled plugin contributes nothing
    reset()
    _p2, tools2, _e2 = ensure_loaded({"version": 1, "roots": [root], "disabled": ["calculator"]})
    assert "calc" not in tools2

    del os.environ["OMEGACLAW_PLUGINS_CONFIG_PATH"]
    reset()

    # empty config is a safe no-op
    assert catalogue_block({"version": 1, "roots": []}) == ""
    assert skill_roots({"version": 1, "roots": []}) == []
    reset()
    print("plugin_registry self-tests passed")


if __name__ == "__main__":
    _selftest()
