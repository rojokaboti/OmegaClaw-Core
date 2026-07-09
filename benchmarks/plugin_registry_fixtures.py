"""Toy-plugin corpus for the Issue #15 benchmark: a working plugin, a failing one, and a
duplicate-tool one — all materialized in a temp dir (no core edits needed to add them)."""

from __future__ import annotations

import json
import os
import tempfile
import textwrap

_CALC = '''
    import ast, operator as op
    _OPS={ast.Add:op.add,ast.Sub:op.sub,ast.Mult:op.mul,ast.Div:op.truediv}
    def _ev(n):
        if isinstance(n, ast.Constant): return n.value
        if isinstance(n, ast.BinOp): return _OPS[type(n.op)](_ev(n.left), _ev(n.right))
        raise ValueError("bad")
    def register():
        return [{"name":"calc","description":"evaluate arithmetic","arg":"expression",
                 "handler":lambda e:_ev(ast.parse(e,mode="eval").body)}]
'''

_ECHO = '''
    def register():
        return [{"name":"echo","description":"echo the input","arg":"text","handler":lambda s:s}]
'''


def build_plugins(base=None):
    base = base or tempfile.mkdtemp(prefix="plugin_corpus_")
    root = os.path.join(base, "plugins")
    os.makedirs(root, exist_ok=True)

    def _mk(pid, impl, manifest=None):
        d = os.path.join(root, pid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "plugin_impl.py"), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(impl))
        m = manifest or {"id": pid, "version": "1.0.0", "entrypoint": "plugin_impl.py",
                         "description": pid + " plugin"}
        with open(os.path.join(d, "plugin.json"), "w", encoding="utf-8") as f:
            json.dump(m, f)

    _mk("calculator", _CALC)
    _mk("echoer", _ECHO)
    _mk("broken", "raise RuntimeError('boom')\ndef register():\n    return []\n")
    # duplicate-tool plugin (also declares 'calc') -> colliding tool rejected, plugin kept
    _mk("dupe", _CALC.replace('"echo"', '"calc"'))
    return {"base": base, "root": root,
            "expected_tools": ["calc", "echo"], "expected_plugins": ["calculator", "dupe", "echoer"]}


if __name__ == "__main__":
    info = build_plugins()
    print("root:", info["root"], "| expected tools:", info["expected_tools"])
