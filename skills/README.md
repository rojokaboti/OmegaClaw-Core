# Filesystem skills (SKILL.md bundles)

This directory is a default **skill root** scanned by `src/skill_loader.py` (Issue #11).
Drop OpenClaw/Hermes-style skill bundles here — each is a directory containing a
`SKILL.md` with YAML frontmatter plus optional `scripts/`, `references/`, `templates/`
support files:

```
skills/
  my-skill/
    SKILL.md          # --- name: my-skill / description: … --- then Markdown instructions
    scripts/helper.py
    references/notes.md
```

Discovered skills appear in the agent prompt as a compact `LOADED_SKILLS:` catalogue
(name + description). The agent reads a skill's full instructions on demand with the
`use-skill <name>` tool, then follows them using its existing tools. Use `{baseDir}` /
`{skillDir}` in the body to reference support files by absolute path.

Roots, allow/deny lists and prompt knobs are configured in `profile/skills.yaml`.
Bundles that fail validation (missing `name`/`description`, unparseable frontmatter,
duplicate name, or a path that escapes this root) are skipped with an actionable error,
never silently dropped.
