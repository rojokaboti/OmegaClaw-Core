---
name: arithmetic-helper
description: Compute arithmetic for the user with the calculator plugin's calc tool
version: 1.0.0
---

# Arithmetic helper

When the user asks for an arithmetic result:

1. Call the calculator plugin tool: `plugin-invoke calc "<expression>"` (e.g. `2 + 3 * 4`).
2. Report the returned value back to the user with `send`.

This skill is a worked example (Issue #15) of a plugin shipping both a tool and a skill.
