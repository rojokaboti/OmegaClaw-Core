![OmegaClaw banner](/docs/assets/banner.png)

# Meet Oma

Oma is the first Telegram agent built on the OmegaClaw framework. Interacting
with Oma is the fastest way to experience what we’re building with OmegaClaw.

<p align="center">
  <a href="https://t.me/ASI_Alliance">
    <img src="/docs/assets/tg-button.png" width="25%" alt="Chat with Oma">
  </a>
</p>

---

## Overview

OmegaClaw is a neural-symbolic agent framework built on the Hyperon AGI stack.
It unifies large language models with a formal symbolic layer to create a
stateful cognitive architecture capable of auditable inference, autonomous
self-improvement, and long-term persistence.

Unlike reactive, session-based agents, OmegaClaw operates in a continuous
execution loop, managing its own goals and providing auditable proof trails for
its reasoning.

The primary design criteria for OmegaClaw were simplicity, ease of extension,
and transparent implementation. This results in a minimalist MeTTa-based core
of approximately 200 lines of code.

---

## Installation

Prerequisites: Git, Python 3.10 or later including dev headers, Pip and [venv](https://docs.python.org/3/library/venv.html) library, C compiler (for building [janus-swi](https://pypi.org/project/janus-swi/) library)

Under Ubuntu one can use the following command to install prerequisites:
```
sudo apt-get install git python3 python3-dev python3-pip python3-venv build-essential
```

Get [SWI-Prolog 10.0.2 or later](https://www.swi-prolog.org/).

Install OmegaClaw:
```
git clone https://github.com/trueagi-io/PeTTa
cd PeTTa
mkdir -p repos
git clone https://github.com/asi-alliance/OmegaClaw-Core.git repos/OmegaClaw-Core
git clone https://github.com/patham9/petta_lib_chromadb.git repos/petta_lib_chromadb
cp repos/OmegaClaw-Core/run.metta ./
```

Setup Python virtual environment (or use your own):
```
python3 -m venv ./.venv
source ./.venv/bin/activate
```

If you have CPU only machine or don't want calculate embeddings on GPU:
```
python3 -m pip install --index-url https://download.pytorch.org/whl/cpu torch
```

Install Python dependencies:
```
python3 -m pip install -r ./repos/OmegaClaw-Core/requirements.txt
```
---

## Run OmegaClaw in Docker

Ensure that you have [Docker installed](https://docs.docker.com/engine/install/)

Run OmegaClaw using the next command:
```
curl -fsSL https://raw.githubusercontent.com/asi-alliance/OmegaClaw-Core/refs/heads/main/scripts/omegaclaw | bash -s -- singularitynet/omegaclaw:latest
```

To run a specific version of OmegaClaw set version in `TAG` environment variable and run the following command:
```
export TAG=v0.1.15; curl -fsSL  https://github.com/asi-alliance/OmegaClaw-Core/raw/refs/tags/$TAG/scripts/omegaclaw | bash -s -- singularitynet/omegaclaw:$TAG
```

To stop the OmegaClaw Docker container:
```
docker stop omegaclaw
```

To restart the OmegaClaw Docker container:
```
docker start omegaclaw
```

To reset OmegaClaw's memory:
```
docker volume rm omegaclaw-memory
```

---

## Usage

Before running the system you need to choose your LLM API provider and export the API key as the environment variable.
| Provider | Env var name | Notes |
|---|---|---|
| `Anthropic` (default) | `ANTHROPIC_API_KEY` | Claude models via the Anthropic API. |
| `OpenAI` | `OPENAI_API_KEY` | GPT models. Also reused by the OpenAI embedding provider below. |
| `ASICloud` | `ASI_API_KEY` |  MiniMax models via ASI Alliance inference endpoint (`inference.asicloud.cudos.org`). |
| `ASIOne` | `ASIONE_API_KEY` |  ASI1 Ultra model via ASI:One inference endpoint (`https://api.asi1.ai/v1`). |
| `Ollama-local` | `OLLAMA_API_KEY` |  Ollama model via local inference endpoint. API endpoint is set via `LLM_SERVER_LOCAL_URL` environment variables. |
| `OpenRouter` | `OPENROUTER_API_KEY` |  GLM model via OpenRouter inference endpoint. |
| `MiniMaxM3` | `OPENROUTER_API_KEY` |  MiniMax M3 model via OpenRouter inference endpoint. |

Run the system via the following command which ensures the system is started from the root folder of PeTTa:
```
OMEGACLAW_AUTH_SECRET=<channel-secret> sh run.sh run.metta IRC_channel="<irc-channel>"
```
After start go to https://webchat.quakenet.org/ to communicate with the agent. Join `<irc-channel>` and after agent is joined send `auth <channel-secret>` message to authenticate yourself as an agent owner. Please replace `<irc-channel>` and `<channel-secret>` by your own values.

### Import Knowledge

If you are running OmegaClaw without Docker and would like to load it with preset knowledge, follow these steps:

1. Set EMBEDDING_PROVIDER in your environment. It can be set to either OpenAI or Local. OpenAI embeddings also require OPENAI_API_KEY to be set in your environment.

2. Run:
```
  sh ./import_knowledge.sh
```
After the script finishes, your OmegaClaw bot will have the preset knowledge stored in its long-term memory (LTM).

If you want to skip preloading the knowledge then run `export IMPORT_KB_ON_START=0`

## Reference — Configuration Options

### General

| Parameter | Default | Meaning |
|---|---|---|
| `maxNewInputLoops` | 50 | Turns the agent keeps running after a new human message before idling (seconds) |
| `maxWakeLoops` | 1 | Extra turns granted on each scheduled wake-up |
| `sleepInterval` | 1 | Delay between loop iterations (seconds) |
| `wakeupInterval` | 600 | How long idle before the next scheduled wake-up (seconds) |
| `LLM` | `gpt-5.4` | Model identifier passed to the provider (used with OpenAI provider only) |
| `provider` | `Anthropic` | LLM provider, see the table of the providers above |
| `maxOutputToken` | 6000 | Output cap passed to the provider |
| `reasoningMode` | `medium` | Reasoning-effort hint passed to the provider (OpenAI only) |
| `securityPolicyPath` | ./repos/OmegaClaw-Core/profile/policy.yaml | Path to the security profile written using [OpenShell YAML](https://docs.nvidia.com/openshell/reference/policy-schema#filesystem-policy). See [./profile/policy.yaml](./profile/policy.yaml) as an example. Empty value disables restrictions. |

### Memory (`src/memory.metta`)

| Parameter | Default | Meaning |
|---|---|---|
| `maxFeedback` | 50000 | Ceiling on `LAST_SKILL_USE_RESULTS` text fed back into the prompt (chars) |
| `maxRecallItems` | 20 | Items returned by `query` |
| `maxEpisodeRecallLines` | 20 | Lines returned by `episodes` |
| `maxHistory` | 30000 | Tail of `memory/history.metta` included in the prompt (chars) |
| `embeddingprovider` | `Local` | `Local` (Python-side model) or `OpenAI` (requires `OPENAI_API_KEY`) |

### Channels (`src/channels.metta`)

| Parameter | Default | Meaning |
|---|---|---|
| `commchannel` | `irc` | Type of the communication channel for agent to use - `irc`, `telegram`, `mattermost` or `slack` |
| `IRC_channel` | `##omegaclaw` | IRC channel to join |
| `IRC_server` | `irc.quakenet.org` | IRC server hostname |
| `IRC_port` | 6667 | IRC port |
| `IRC_user` | `omegaclaw` | IRC nickname |
| `TG_CHAT_ID` |  | Optional Telegram chat ID. If empty, OmegaClaw auto-binds after first valid inbound auth/message. |
| `TG_POLL_TIMEOUT` | 20 | Telegram polling timeout in seconds. |
| `SL_CHANNEL_ID` |  | Optional Slack channel ID (for example `C0123456789`). If empty, OmegaClaw auto-binds on first successful auth message. |
| `SL_POLL_INTERVAL` | 60 | Slack polling interval in seconds (minimum effective value is 60). |
| `MM_URL` | `https://chat.singularitynet.io` | Mattermost base URL. |
| `MM_CHANNEL_ID` | `8fjrmabjx7gupy7e5kjznpt5qh` | Mattermost channel ID. |

| Environment variable | Meaning |
|---|---|
| `TG_BOT_TOKEN` | Telegram bot token. |
| `MM_BOT_TOKEN` | Mattermost bot token. |
| `SL_BOT_TOKEN` | Slack bot token (`xoxb-...`). |
| `OMEGACLAW_ACTION_PROTOCOL` | LLM tool-call parsing mode: `json` (default, strict JSON action protocol), `auto` (JSON with legacy text fallback), or `legacy` (original `balance_parentheses` heuristic parser). |
| `OMEGACLAW_MAX_ACTIONS` | Max tool actions accepted per turn under the JSON protocol (default `5`). Exceeding it rejects the whole batch. |
| `OMEGACLAW_DISABLED_TOOLS` | Comma-separated tool names to refuse (default none = allow all). Use to gate high-risk escape hatches such as `shell` and `metta` in restricted deployments. A batch containing a disabled tool is rejected. |
| `OMEGACLAW_TOOL_POLICY_PATH` | Path to the tool/action policy YAML (default `profile/tool_policy.yaml`). Set to `profile/tool_policy.hardened.yaml` for a strict `default: deny` posture. A **relative** value is resolved against the install root (the repo dir), not the process CWD, so it works regardless of where the agent is launched. |
| `OMEGACLAW_DEBUG_LLM_RAW` | `1`/`true` to log raw model responses (for debugging). Default off — raw bodies are **not** logged; only metadata (provider, model, timestamp, char count, trace id). When enabled, common secret/token formats are best-effort redacted (not a guarantee). |
| `OMEGACLAW_LLM_LOG_PATH` | Optional path to append per-response JSONL log records (metadata always; redacted raw only when `OMEGACLAW_DEBUG_LLM_RAW` is set). |
| `OMEGACLAW_LLM_CONFIG_PATH` | Path to the provider/model config YAML (default `profile/llm_providers.yaml`). Relative values resolve against the install root. **Failure model:** an absent *shipped default* (no env set) fails open to built-in defaults; an **explicit** path that is missing/invalid **fails closed** (no external provider registered) so prompts can't silently route to a cloud default. |
| `OMEGACLAW_LLM_CONFIG_FAIL_OPEN` | `1`/`true` to opt an explicit `OMEGACLAW_LLM_CONFIG_PATH` back into fail-open (fall back to built-in defaults instead of failing closed). |
| `OMEGACLAW_SKILLS_CONFIG_PATH` | Path to the filesystem-skill loader config YAML (default `profile/skills.yaml`) listing skill roots + allow/deny lists. Relative values resolve against the install root. Fails open to a safe empty set, so a missing/invalid file simply loads no external skills. |
| `OMEGACLAW_SKILL_BODY_MAX_CHARS` | Max characters of a skill's instructions returned by `use-skill` (default `20000`); longer bodies are truncated. |
| `OMEGACLAW_SKILLS_DEBUG` | `1`/`true` to advertise ALL loaded skills in the prompt, including those blocked by eligibility gates (Issue #13). Default off — only eligible skills are advertised; blocked ones show as a concise `SKILL_UNAVAILABLE:` note. |

---

## Provider / model configuration

Which providers exist, and each one's model, base URL, API style, and reasoning
settings, are declared in [`profile/llm_providers.yaml`](./profile/llm_providers.yaml)
— **not** hardcoded in Python. To switch model or provider, edit that file (or point
`OMEGACLAW_LLM_CONFIG_PATH` at another one) and set the active `provider` in
`src/loop.metta` (`Anthropic` by default); no Python edit is required.

The MeTTa `LLM` value now reflects the model actually resolved for the active
provider, and at startup the agent logs the effective configuration, e.g.:

```
[llm_config] provider=Anthropic model=claude-opus-4-6 base_url=https://api.anthropic.com/v1/ class=AIProvider available=True
```

**Failure model.** If no config is configured and the shipped default is absent, the
gate fails **open** to built-in defaults (mirroring the shipped YAML) with a warning, so
the out-of-box agent never bricks. But if `OMEGACLAW_LLM_CONFIG_PATH` is **explicitly set**
and the file is missing/invalid, the gate fails **closed** — no external provider is
registered and a `[provider_config] SECURITY …` line is logged — so an operator pointing at
a private/local provider can never have prompts silently routed to a built-in cloud default.
Set `OMEGACLAW_LLM_CONFIG_FAIL_OPEN=1` to opt an explicit config back into fail-open. The
`Test` provider (mock) is registered in code, not via this file.

---

## Debugging LLM responses (privacy)

Raw model output can contain private user text, tool results, or accidentally echoed
secrets, and agent stdout is captured by `docker logs`. By default OmegaClaw logs only
**metadata** per response:

```
[LLM_RAW] ts=… trace=ab12cd34 provider=OpenAI model=… chars=1234 raw=<redacted; set OMEGACLAW_DEBUG_LLM_RAW=1>
```

To see raw content while debugging, set `OMEGACLAW_DEBUG_LLM_RAW=1`. Even then the body is
passed through a **best-effort** secret redactor for common token formats
(OpenAI/Anthropic keys, GitHub/AWS tokens, bearer tokens, long base64-ish secrets →
`[REDACTED:<kind>]`). This reduces — but does not guarantee elimination of — secret
leakage, so treat debug logs as potentially sensitive. Set
`OMEGACLAW_LLM_LOG_PATH=/path/log.jsonl` to additionally append structured records.

---

## Error recovery events

When an action fails, OmegaClaw does not just feed the model an opaque string. Each
failure is classified into one of five **machine-readable categories** — `parse_error`,
`unknown_tool`, `schema_validation_error`, `tool_policy_denied`, `tool_runtime_error` —
and recorded as a structured event (`src/errors.py`) carrying the failed action, a
`retryable` flag, and a **concise, category-specific repair hint** that is what the model
sees on its next turn (instead of the old `…NOTHING_WAS_DONE…` token). Events are emitted
into the reasoning trace under the current iteration's `trace_id` (no new env var — they
ride the existing `OMEGACLAW_TRACE_*` file), so `scripts/omegaclaw-trace-summary` reports
error counts **by category** across a run. This makes recovery reliable and error rates
comparable across benchmark runs.

---

## Filesystem skills (SKILL.md bundles)

Beyond the built-in MeTTa skills, OmegaClaw loads portable **OpenClaw/Hermes-style
`SKILL.md` bundles** from disk with no code edits (`src/skill_loader.py`). A bundle is a
directory with a `SKILL.md` (YAML frontmatter — `name`, `description`, `version`,
`metadata`, `platforms`, `required_environment_variables` — plus Markdown instructions)
and optional `scripts/` / `references/` / `templates/` support files. A `SKILL.md` is a
*procedural playbook the agent follows using its existing tools*, not a new native tool.

Discovered skills appear in the prompt as a compact `LOADED_SKILLS:` catalogue
(name + description); the agent reads a skill's full instructions on demand with the
`use-skill <name>` tool (progressive disclosure), with `{baseDir}`/`{skillDir}` resolved
so it can reference support files. Drop bundles under a root listed in
`profile/skills.yaml` (default `skills/`). Validation is fail-safe: a malformed, unsafe,
duplicate, or root-escaping bundle is skipped with an **actionable error**, never
silently, and secret-shaped tokens in a skill body are redacted before they reach the
prompt.

**Eligibility gates & readiness (`src/skill_policy.py`).** Only skills that can actually run
here are advertised, so the agent never tries a skill whose prerequisites are missing. A
bundle declares requirements in its frontmatter — OpenClaw `metadata.openclaw.requires`
(`env` / `bins` / `anyBins` / `config`), `os`, `always`; Hermes `platforms`,
`required_environment_variables`, `metadata.hermes.requires_toolsets` — normalized into one
schema and checked against the current OS, environment, `PATH`, config, and tool policy.
Blocked skills are not advertised (they appear as a concise `SKILL_UNAVAILABLE:` note that
never prints secret values); set `OMEGACLAW_SKILLS_DEBUG=1` to advertise all. Run
`scripts/omegaclaw-skills doctor` for a full readiness report with remediation for every
blocked bundle. Per-skill allow/deny and overrides live in `profile/skills.yaml`
(`enabled` / `disabled` / `entries` / `config`).

**Install lifecycle (`src/skill_install.py`).** Rather than hand-copying skills, install them
from a **local path**, a **Git repo** (pinned ref), or a **ClawHub-compatible HTTP registry**
(slug):

```
scripts/omegaclaw-skills install local:/path/to/skill
scripts/omegaclaw-skills install git:owner/repo@v1.2.0
scripts/omegaclaw-skills install clawhub:my-skill@2.0      # OMEGACLAW_CLAWHUB_URL
scripts/omegaclaw-skills list | update [--all] | verify | pin <name> | remove <name>
```

Each install fetches into a temp staging dir, **validates** it with the loader, and only then
commits into the skill root — a bad source is a no-op (rollback), never a corrupted root.
Reinstalls are idempotent (no duplicate directories). A workspace lockfile
(`<root>/.omegaclaw-skills.lock.json`) plus a per-skill `.omegaclaw-origin.json` record the
source, ref/version, a content hash, install time, and trust status; `verify` re-hashes an
installed skill against its lock to detect tampering, and `pin` protects a skill from
`update --all`. (Sandboxing/approvals for untrusted sources are Issue #19; installs are
recorded as `trust: unverified` today.)

---

## Security: two layers

OmegaClaw applies defense-in-depth around tool use:

1. **Filesystem sandbox (Landlock)** — `profile/policy.yaml`, applied at startup
   via `applySecurityPolicy`. This is an **OS-level** guard: it constrains which
   paths the process can read/write, enforced by the kernel *after* a syscall is
   attempted. It cannot reason about *which command* a `shell` action runs.

2. **Tool/action policy** — `profile/tool_policy.yaml`, enforced by
   `src/tool_policy.py` at the action-protocol gate (`authorize_actions`),
   **before** an action becomes a MeTTa skill call. It decides, per tool:
   enabled/disabled, `allowed_roots` for file reads/writes (path-resolved to block
   `../` escapes), and shell `allow`/`deny` glob lists. Denials are structured and
   logged (`[tool_policy] POLICY_DENIAL …`) and reject the whole action batch.

The shipped default (`tool_policy.yaml`) is **permissive** (preserves normal
behavior). A strict, opt-in example lives in `tool_policy.hardened.yaml`
(`default: deny`, shell disabled); select it with `OMEGACLAW_TOOL_POLICY_PATH`
(relative values resolve against the install root).

**Failure model.** If no policy is configured and the shipped default file is
somehow absent, the gate **fails open** (allow-all, with a warning) so the
out-of-box agent never bricks. But if `OMEGACLAW_TOOL_POLICY_PATH` is **explicitly
set** and the file cannot be loaded (missing path, bad YAML), the gate **fails
closed** — every action is denied and a prominent `[tool_policy] SECURITY …`
error is logged — because a misconfigured security control must be loud, never a
silent allow-all.

> Channel-specific restrictions and an interactive approval workflow are modeled
> in the policy decision (`risk`, `requires_approval`) but not yet enforced; a
> tool marked `requires_approval: true` is currently denied.

---

## Documentation

Full documentation lives in [`docs/`](./docs/README.md): introduction,
tutorials, and API reference as a flat set of markdown files.

---

### Disclaimer

<sub>OmegaClaw is experimental, open-source software developed by SingularityNET Foundation, a Swiss foundation, and distributed and promoted by Superintelligence Alliance Ltd., a Singapore company (collectively, the "Parties"), and is provided "AS IS" and "AS AVAILABLE," without warranty of any kind, express or implied, including but not limited to the implied warranties of merchantability, fitness for a particular purpose, and non-infringement. OmegaClaw is an autonomous AI agent that is designed to independently set goals, make decisions, and take actions (including actions that the user did not specifically request or anticipate) and whose behavior is influenced by large language models provided by third parties, the outputs of which are inherently non-deterministic. Depending on its configuration and the permissions granted to it, OmegaClaw may execute operating-system shell commands, read, write, modify, or delete files, access network resources, send and receive messages through connected communication channels, and modify its own skills, memory, and operational logic at runtime. OmegaClaw may also be susceptible to prompt injection and other adversarial manipulation techniques whereby malicious content embedded in data sources consumed by the agent could influence its behavior in unintended ways. OmegaClaw supports third-party skills and extensions that have not necessarily been reviewed, audited, or endorsed by either of the Parties and that may introduce security vulnerabilities, cause data loss, or result in unintended behavior including data exfiltration. OmegaClaw relies on third-party services, including large language model providers, whose availability, accuracy, cost, and conduct are outside the control of the Parties and whose use is subject to their respective terms, conditions, and privacy policies. The user is solely responsible for configuring appropriate access controls, sandboxing, and permission boundaries, for monitoring, supervising, and constraining OmegaClaw's actions, for ensuring that no sensitive personal data is exposed to the agent without adequate safeguards, and for all actions taken by OmegaClaw on the user's systems or on the user's behalf, including communications sent and files modified. The user is strongly advised to run OmegaClaw in an isolated environment with the minimum permissions necessary for the intended use case. To the maximum extent permitted by applicable law, in no event shall the Parties, their respective board members, directors, contributors, employees, or affiliates be liable for any direct, indirect, incidental, special, consequential, or exemplary damages (including but not limited to damages for loss of data, loss of profits, business interruption, unauthorized transactions, reputational harm, or any damages arising from the autonomous actions taken by OmegaClaw) however caused and on any theory of liability, whether in contract, strict liability, or tort (including negligence or otherwise), even if advised of the possibility of such damages. By downloading, installing, running, or otherwise using OmegaClaw, the user acknowledges that they have read, understood, and agreed to this disclaimer in its entirety. This disclaimer supplements but does not replace the terms of the MIT License under which OmegaClaw is released.</sub>
