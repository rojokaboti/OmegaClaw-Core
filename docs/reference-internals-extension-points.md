# Internals — Extension Points

Where to plug in new behavior, in order of increasing depth.

## Add a skill

Most common extension. Two edits:

1. A line in `getSkills` (`src/skills.metta`) so the LLM knows the skill exists.
2. A `(= (my-skill $arg) ...)` definition, either pure MeTTa or a `py-call` / `translatePredicate`.

Full walkthrough: [tutorial-03-writing-a-custom-skill.md](./tutorial-03-writing-a-custom-skill.md).

## Add a remote skill

Same as above, but the body delegates to `src/agentverse.py`:

```metta
(= (my-remote-skill $arg)
   (py-call (agentverse.my_remote_skill $arg)))
```

Full walkthrough: [tutorial-06-remote-agentverse-skills.md](./tutorial-06-remote-agentverse-skills.md).

## Add a channel

Channels are plugins implementing `pluginapi.CommChannel` (loaded by `src/plugin.py::initPlugins`):

1. New Python module `channels/myadapter.py` defining a `CommChannel` subclass with `config(dict)`,
   `receive() -> str`, and `send(str)`, plus a module-level `loadOmegaClawPlugin()` that calls
   `pluginapi.registerCommChannel("myname", MyChannel())`.
2. Add a record to [`config/plugins.yaml`](../config/plugins.yaml) (`name: myadapter`,
   `loader: python`, `location: "{REPO}/channels"`) so `initPlugins` loads it on start.
3. Select it at runtime with `commchannel=myname` (or the `configure commchannel …` default in
   `src/channels.metta`). Per-channel config arrives as CLI `<key>=<value>` args handed to `config()`.

Full walkthrough: [tutorial-04-adding-a-channel.md](./tutorial-04-adding-a-channel.md).

## Add an LLM provider

Providers are plugins implementing `pluginapi.LLMProvider`. The loop calls the selected provider
through `llmProviderChat` (`src/plugin.py`) — there is no per-provider `if` chain in
`src/loop.metta` any more.

To add a provider:

1. In `providers/`, define an `LLMProvider` subclass with `config(dict)` and
   `chat(prompt, max_tokens, reasoning_mode) -> str`. OpenAI-compatible endpoints can subclass the
   shared `AIProvider` in `providers/lib_llm_ext.py`, or just reuse `OpenAIAPIPreconfigured`
   (see the `SNET` / `ASICloud` / `Anthropic` registrations in `providers/openaiapi.py`).
2. Add a `loadOmegaClawPlugin()` calling `pluginapi.registerLLMProvider("MyProvider", MyProvider(…))`,
   and list the module in `config/plugins.yaml` under `{REPO}/providers`.
3. Select it with `configure provider MyProvider` in `src/loop.metta`, or `provider=MyProvider` on
   the command line.

## Change the prompt

The agent's identity and values are in `memory/prompt.txt`. The run-time prompt template that sandwiches it is in `getContext` in `src/loop.metta`. Edit carefully — the output-format instruction is what keeps the LLM producing valid skill s-expressions.

## Change the embedding model

In `src/memory.metta`, the `embed` function dispatches on `embeddingprovider`:

```metta
(= (embed $str)
   (if (== (embeddingprovider) Local)
       (py-call (lib_llm_ext.useLocalEmbedding (string-safe $str)))
       (useGPTEmbedding (string-safe $str))))
```

To add a new backend, add a branch and implement the Python function.

## Change the reasoning library

`lib_nal.metta` and `lib_pln.metta` are plain MeTTa files loaded by `lib_omegaclaw.metta`. Add new rule definitions directly, or swap in a different logic library entirely — the only required surface is whatever operator the LLM invokes through `(metta ...)`.

## See also

- [reference-internals-loop.md](./reference-internals-loop.md) — the loop is the host for all of the above.
- [reference-python-bridges.md](./reference-python-bridges.md) — bridge conventions.
