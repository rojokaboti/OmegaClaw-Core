# Plugins (tool + skill packages)

A plugin extends OmegaClaw with **tools** (and optionally **skills**) through a manifest —
no core edits (Issue #15). Point `profile/plugins.yaml` `roots:` at a directory containing
plugin subdirectories (or at a single plugin dir's parent) to enable them.

Layout:

```
plugins/my-plugin/
  plugin.json          # { id, version, entrypoint, description?, permissions?, skill_dirs?, requires? }
  plugin_impl.py       # exposes register() -> [ {name, description, arg, handler}, ... ]
  skills/              # optional: SKILL.md bundles surfaced via the loader (skill_dirs)
```

The entrypoint's `register()` returns tool specs; each `handler(arg_str)` is called when the
agent runs `plugin-invoke <name> <arg>`. Discovered tools are advertised in the prompt as a
`PLUGIN_TOOLS:` catalogue. A manifest may declare `requires` (`env` / `bins` / `config`); a
plugin whose requirements are unmet, whose manifest is invalid, or whose entrypoint fails to
import is skipped with an actionable error (`PLUGIN_LOAD_ERRORS`), never crashing the agent.

`plugin-invoke` runs plugin-defined code, so it is classified **high-risk** in the tool policy
(gate/deny it in hardened deployments). See `plugins/example-calculator/` for a worked example
(disabled by default — add its root to `profile/plugins.yaml` to try it).
