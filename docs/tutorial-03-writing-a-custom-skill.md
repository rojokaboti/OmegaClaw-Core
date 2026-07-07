# Tutorial 03 — Writing a Custom Skill

**Goal:** add a new skill the agent can call, end-to-end.

## Prerequisites

- A local clone of OmegaClaw-Core (so you can edit MeTTa source).
- Familiarity with running the agent — see [Usage](/README.md#usage).

## The anatomy of a skill

A skill is three things:

1. **An entry in the skill catalogue** in `src/skills.metta` (the `getSkills` list) so the LLM learns it exists.
2. **A MeTTa definition** of how the skill executes. Pure-MeTTa skills are written directly; skills that need system access delegate to Python or Prolog.
3. **Optional Python/Prolog glue** imported through `py-call` or `translatePredicate`.

## Example: a `word-count` skill

We'll add `(word-count "some text")` that returns the number of whitespace-separated tokens.

### Step 1 — Declare it in `getSkills`

Open `src/skills.metta` and add a line inside the `getSkills` list:

```metta
"- Count whitespace-separated words in a string: (word-count string_in_quotes)"
```

This text is concatenated into the prompt so the LLM knows the skill is callable.

### Step 2 — Define the implementation

Still in `src/skills.metta`, add:

```metta
(= (word-count $str)
   (progn (translatePredicate (split_string $str " " "" $parts))
          (length $parts)))
```

If you prefer Python, register a function in a `.py` module and call `(py-call (mymodule.word_count $str))`.

### Step 3 — Test

Restart the agent. Ask:

```
how many words are in "the quick brown fox"?
```

The LLM should emit `(word-count "the quick brown fox")` and respond with `4`.

## Conventions

- Skill names are lowercase, hyphen-separated.
- Every argument is a string literal in quotes. Variables are forbidden in LLM-generated skill calls (the loop rejects them in `getContext`).
- Return a value that is safe to render into the `LAST_SKILL_USE_RESULTS` context — the loop runs the result through `helper.normalize_string`.
- If your skill may fail, wrap error-producing subcalls in `catch` or let them fall through to the loop's `HandleError`.

## Verification

- The new skill appears in the prompt (search logs for `word-count`).
- The LLM invokes it without prompting tweaks.
- The return value shows up in `LAST_SKILL_USE_RESULTS` on the next turn.

> **Native tools under the JSON action protocol (Issue #1).** The default action
> protocol (`OMEGACLAW_ACTION_PROTOCOL=json`) validates every tool against
> `helper.LLM_COMMANDS` and `action_protocol.ARG_SPEC`. A new *native* tool therefore
> needs **four** edits, not two: the `getSkills` line, its `(= (my-skill …) …)` MeTTa
> body, an entry in `helper.LLM_COMMANDS`, and an `ARG_SPEC` entry (which also drives
> the generated `OUTPUT_FORMAT` block). Miss the last two and the tool is rejected as
> `unknown_tool`.

## Filesystem skills (SKILL.md bundles) — Issue #11

Most reusable skills are **not** new native tools — they are *procedural playbooks* the
agent follows using the tools it already has (`shell`, `read-file`, `send`, `metta`, …).
OmegaClaw loads OpenClaw/Hermes-style `SKILL.md` bundles from disk with no code edits:

1. Drop a bundle under a configured skill root (default `skills/`, see
   `profile/skills.yaml`):
   ```
   skills/my-skill/
     SKILL.md            # YAML frontmatter (name, description, …) + Markdown instructions
     scripts/ references/ templates/   # optional support files
   ```
2. `src/skill_loader.py` discovers and validates it, then injects a compact
   `LOADED_SKILLS:` catalogue (name + description) into the prompt.
3. The agent calls the single native tool **`use-skill <name>`** to read a skill's full
   instructions on demand (progressive disclosure), with `{baseDir}`/`{skillDir}`
   resolved to the bundle's absolute path so it can reference support files. It then
   carries out the steps using its existing tools.

Bundles that fail validation (missing `name`/`description`, unparseable frontmatter,
duplicate name, unsafe name, or a path escaping the root) are skipped with an
actionable error — never silently. Roots, allow/deny lists and prompt knobs live in
`profile/skills.yaml`. This is how you reuse an existing skill ecosystem without
rewriting each skill as MeTTa.

## Next steps

- [reference-internals-skill-dispatch.md](./reference-internals-skill-dispatch.md) — how dispatch works.
- [reference-internals-extension-points.md](./reference-internals-extension-points.md) — other places to hook in.
- [tutorial-06-remote-agentverse-skills.md](./tutorial-06-remote-agentverse-skills.md) — delegate skills to a remote agent instead of running them locally.
