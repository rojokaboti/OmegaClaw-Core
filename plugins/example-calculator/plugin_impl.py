"""Example plugin entrypoint (Issue #15): a safe arithmetic evaluator.

`register()` returns tool specs. Each handler takes one string argument and returns a result;
the agent calls it via `plugin-invoke calc "<expression>"`. This is a reference example — the
plugin is disabled by default (not listed in profile/plugins.yaml roots).
"""

import ast
import operator

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported expression")


def calc(expression):
    """Safely evaluate an arithmetic expression (no names/calls — AST-whitelisted)."""
    tree = ast.parse(str(expression), mode="eval")
    return _eval(tree.body)


def register():
    return [
        {"name": "calc", "description": "evaluate a basic arithmetic expression",
         "arg": "expression", "handler": calc},
    ]
