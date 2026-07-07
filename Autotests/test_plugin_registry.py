"""Unit tests for the plugin & tool registry (Issue #15).

Pure-Python; imports src/plugin_registry.py directly. Plugins are materialized in temp dirs.
Runs under pytest and standalone (`python3 Autotests/test_plugin_registry.py`).
"""
import os
import sys
import tempfile
import textwrap

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import plugin_registry as pr  # noqa: E402
import skill_loader as sl  # noqa: E402

_CALC_IMPL = '''
    def _add(x):
        a, b = x.split(",")
        return int(a) + int(b)
    def register():
        return [{"name": "add", "description": "add two comma-separated ints",
                 "arg": "a,b", "handler": _add}]
'''


def _plugin(root, pid, impl=_CALC_IMPL, manifest=None, entrypoint="plugin_impl.py"):
    d = os.path.join(root, pid)
    os.makedirs(d, exist_ok=True)
    if impl is not None:
        with open(os.path.join(d, entrypoint), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(impl))
    import json
    m = manifest if manifest is not None else {
        "id": pid, "version": "1.0.0", "entrypoint": entrypoint, "description": pid + " plugin"}
    with open(os.path.join(d, "plugin.json"), "w", encoding="utf-8") as f:
        json.dump(m, f)
    return d


def _root():
    return os.path.join(tempfile.mkdtemp(prefix="plug_"), "plugins")


def test_discover_and_invoke_tool():
    pr.reset()
    root = _root()
    _plugin(root, "adder")
    cfg = {"version": 1, "roots": [root]}
    assert pr.list_plugins(cfg) == ["adder"]
    assert pr.list_tools(cfg) == ["add"]
    _p, tools, _e = pr.ensure_loaded(cfg)
    assert tools["add"].handler("2,3") == 5


def test_disabled_plugin_contributes_nothing():
    pr.reset()
    root = _root()
    _plugin(root, "adder")
    cfg = {"version": 1, "roots": [root], "disabled": ["adder"]}
    assert pr.list_plugins(cfg) == []
    assert pr.list_tools(cfg) == []


def test_bad_manifest_is_isolated():
    pr.reset()
    root = _root()
    d = os.path.join(root, "broken")
    os.makedirs(d)
    with open(os.path.join(d, "plugin.json"), "w", encoding="utf-8") as f:
        f.write("{ not valid json ")
    cfg = {"version": 1, "roots": [root]}
    assert pr.list_plugins(cfg) == []
    assert any(e.plugin == "broken" or "broken" in e.plugin for e in pr.errors(cfg))


def test_failing_import_is_isolated():
    pr.reset()
    root = _root()
    _plugin(root, "boom", impl="raise RuntimeError('nope')\ndef register():\n    return []\n")
    _plugin(root, "good")
    cfg = {"version": 1, "roots": [root]}
    # the good plugin still loads; the failing one is reported, not fatal
    assert pr.list_plugins(cfg) == ["good"]
    assert any(e.plugin == "boom" and "load failed" in e.message for e in pr.errors(cfg))


def test_duplicate_tool_name_rejected():
    pr.reset()
    root = _root()
    _plugin(root, "p1")
    _plugin(root, "p2")   # also registers a tool named "add"
    cfg = {"version": 1, "roots": [root]}
    tools = pr.list_tools(cfg)
    assert tools.count("add") == 1                       # only one 'add' survives
    assert any("duplicate tool name" in e.message for e in pr.errors(cfg))


def test_requirements_gate_skips_plugin_with_reason():
    pr.reset()
    root = _root()
    _plugin(root, "needsenv", manifest={
        "id": "needsenv", "version": "1.0.0", "entrypoint": "plugin_impl.py",
        "requires": {"env": ["OMEGACLAW_DEFINITELY_UNSET_VAR"]}})
    cfg = {"version": 1, "roots": [root]}
    assert pr.list_plugins(cfg) == []
    assert any("requirements not met" in e.message for e in pr.errors(cfg))


def test_entrypoint_escape_is_rejected():
    pr.reset()
    root = _root()
    _plugin(root, "escaper", impl=None, manifest={
        "id": "escaper", "version": "1.0.0", "entrypoint": "../../../etc/passwd"})
    cfg = {"version": 1, "roots": [root]}
    assert pr.list_plugins(cfg) == []
    assert any("escapes" in e.message or "not found" in e.message for e in pr.errors(cfg))


def test_catalogue_block_and_errors():
    pr.reset()
    root = _root()
    _plugin(root, "adder")
    _plugin(root, "boom", impl="raise RuntimeError('x')\ndef register():\n    return []\n")
    block = pr.catalogue_block({"version": 1, "roots": [root]})
    assert block.startswith("PLUGIN_TOOLS:")
    assert "- add (a,b): add two comma-separated ints [adder]" in block
    assert "PLUGIN_LOAD_ERRORS:" in block and "boom" in block


def test_plugin_skill_dirs_feed_the_loader():
    pr.reset(); sl.reset_cache()
    root = _root()
    d = _plugin(root, "withskills", manifest={
        "id": "withskills", "version": "1.0.0", "entrypoint": "plugin_impl.py",
        "skill_dirs": ["skills"]})
    sk = os.path.join(d, "skills", "greet")
    os.makedirs(sk)
    with open(os.path.join(sk, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: greet\ndescription: greet the user\n---\nsay hi\n")
    cfg = {"version": 1, "roots": [root]}
    assert "skills" == os.path.basename(pr.skill_roots(cfg)[0])
    # the loader picks the plugin's skill up when the plugin registry is pointed at this root
    import yaml
    pcfg = os.path.join(os.path.dirname(root), "plugins.yaml")
    with open(pcfg, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f)
    os.environ["OMEGACLAW_PLUGINS_CONFIG_PATH"] = pcfg
    pr.reset(); sl.reset_cache()
    try:
        skills, _errs = sl.load_skills({"version": 1, "roots": []})
        assert "greet" in skills
    finally:
        os.environ.pop("OMEGACLAW_PLUGINS_CONFIG_PATH", None)
        pr.reset(); sl.reset_cache()


def test_requirement_cache_invalidates_on_env_flip():
    """Regression (PR #37 review): a required-env flip must not return a stale load decision,
    and removing the env after load must re-gate on reset."""
    pr.reset()
    root = _root()
    _plugin(root, "needsenv", manifest={
        "id": "needsenv", "version": "1.0.0", "entrypoint": "plugin_impl.py",
        "requires": {"env": ["OMEGACLAW_PR37_TESTVAR"]}})
    cfg = {"version": 1, "roots": [root]}
    os.environ.pop("OMEGACLAW_PR37_TESTVAR", None)
    try:
        assert pr.list_plugins(cfg) == []                 # unset -> blocked
        os.environ["OMEGACLAW_PR37_TESTVAR"] = "1"
        assert pr.list_plugins(cfg) == ["needsenv"]       # set, SAME process -> now allowed
        os.environ.pop("OMEGACLAW_PR37_TESTVAR", None)
        assert pr.list_plugins(cfg) == []                 # removed -> blocked again
    finally:
        os.environ.pop("OMEGACLAW_PR37_TESTVAR", None)
        pr.reset()


def test_requirement_cache_invalidates_on_config_flip():
    pr.reset()
    root = _root()
    _plugin(root, "needsconf", manifest={
        "id": "needsconf", "version": "1.0.0", "entrypoint": "plugin_impl.py",
        "requires": {"config": ["FLAG"]}})
    assert pr.list_plugins({"version": 1, "roots": [root], "config": {"FLAG": False}}) == []
    assert pr.list_plugins({"version": 1, "roots": [root], "config": {"FLAG": True}}) == ["needsconf"]


def test_skill_dir_escape_rejected():
    """Regression (PR #37 review): a plugin may not contribute a skill_dir outside its dir."""
    pr.reset()
    base = tempfile.mkdtemp(prefix="plug_esc_")
    root = os.path.join(base, "plugins")
    d = _plugin(root, "p", manifest={
        "id": "p", "version": "1.0.0", "entrypoint": "plugin_impl.py",
        "skill_dirs": ["../../outside_skills"]})
    os.makedirs(os.path.join(base, "outside_skills"), exist_ok=True)
    cfg = {"version": 1, "roots": [root]}
    assert pr.skill_roots(cfg) == []
    assert any("escapes the plugin dir" in e.message for e in pr.errors(cfg))


def test_symlinked_plugin_dir_rejected():
    """Regression (PR #37 review): a symlink under the root must not load an out-of-root plugin."""
    pr.reset()
    base = tempfile.mkdtemp(prefix="plug_link_")
    root = os.path.join(base, "plugins")
    os.makedirs(root)
    outside = os.path.join(base, "outside_plugin")
    _plugin(os.path.dirname(outside), os.path.basename(outside))  # build the outside plugin
    try:
        os.symlink(outside, os.path.join(root, "link"))
    except OSError:
        return  # platform without symlink support
    cfg = {"version": 1, "roots": [root]}
    assert pr.list_plugins(cfg) == [] and pr.list_tools(cfg) == []
    assert any("escapes its root" in e.message for e in pr.errors(cfg))


def test_intra_plugin_duplicate_tool_rejected():
    """Regression (PR #37 review, non-blocking): a plugin returning two specs with the same
    name must keep the FIRST and report the collision, not silently keep the second."""
    pr.reset()
    root = _root()
    _plugin(root, "dup", impl='''
        def register():
            return [{"name":"same","description":"a","arg":"x","handler":lambda s:"first"},
                    {"name":"same","description":"b","arg":"x","handler":lambda s:"second"}]
    ''')
    cfg = {"version": 1, "roots": [root]}
    _p, tools, errs = pr.ensure_loaded(cfg)
    assert tools["same"].handler("") == "first"
    assert any("duplicate tool name" in e.message for e in errs)


def test_empty_config_noop():
    pr.reset()
    assert pr.catalogue_block({"version": 1, "roots": []}) == ""
    assert pr.list_tools({"version": 1, "roots": []}) == []
    assert pr.skill_roots({"version": 1, "roots": []}) == []


def test_example_plugin_ships_and_works():
    """The committed reference plugin loads and its calc tool computes."""
    pr.reset()
    cfg = {"version": 1, "roots": [os.path.join(_REPO_ROOT, "plugins", "example-calculator")]}
    _p, tools, _e = pr.ensure_loaded(cfg)
    assert "calc" in tools and tools["calc"].handler("2+3*4") == 14
    assert os.path.basename(pr.skill_roots(cfg)[0]) == "skills"


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
    print(f"\nAll {len(fns)} plugin_registry tests passed")
