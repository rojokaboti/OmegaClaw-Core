import os, time
import json
import re
import sys
import uuid
import openai
from typing import Optional

# Ensure the sibling src/ dir is importable regardless of MeTTa import order, so
# `import provider_config` resolves whether or not src is already on sys.path.
_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
import provider_config  # noqa: E402 (declarative provider/model config, Issue #4)

# --- Raw LLM response logging (Issue #3): gated + redacted ------------------
#
# Raw model output can contain private user text, tool outputs, or accidentally
# echoed secrets, and it ends up in stdout -> `docker logs`. So by default we log
# metadata only (provider, model, timestamp, char count, trace id) and NOT the raw
# body. Raw context is logged only when OMEGACLAW_DEBUG_LLM_RAW is explicitly set,
# and even then it is passed through redact_secrets() first, which does best-effort
# redaction of common secret/token formats. This is not a guarantee that every
# possible secret is caught -- OMEGACLAW_DEBUG_LLM_RAW is an explicit opt-in for
# debugging, so callers should treat debug logs as potentially sensitive.

_REDACTION_PATTERNS = [
    # Anthropic keys (check before the generic sk- rule).
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")),
    # OpenAI keys, incl. project keys (sk-, sk-proj-).
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{16,}")),
    # GitHub tokens: ghp_/gho_/ghu_/ghs_/ghr_ and fine-grained github_pat_.
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    # AWS access key id.
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    # HTTP bearer tokens (redact the token, keep the scheme).
    ("bearer", re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}")),
    # Long high-entropy-ish base64/hex runs (>=40 chars). Conservative: requires a
    # contiguous token of base64 alphabet, so normal prose is not mangled.
    ("secret", re.compile(r"\b[A-Za-z0-9+/_\-]{40,}={0,2}\b")),
]


def redact_secrets(text: str) -> str:
    """Replace secret-looking substrings with a typed ``[REDACTED:<kind>]`` marker.

    Best-effort: detects common secret/token formats (known key/token shapes plus
    long base64-ish runs) and leaves ordinary text intact. It does not guarantee
    that every possible secret is caught.
    """
    if not text:
        return text
    out = text
    for kind, pattern in _REDACTION_PATTERNS:
        if kind == "bearer":
            out = pattern.sub(lambda m: f"{m.group(1)}[REDACTED:bearer]", out)
        else:
            out = pattern.sub(f"[REDACTED:{kind}]", out)
    return out


def _raw_logging_enabled() -> bool:
    return (os.environ.get("OMEGACLAW_DEBUG_LLM_RAW") or "").strip().lower() in {"1", "true", "yes", "on"}


def _log_raw(provider: str, model: str, raw: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    raw = raw or ""
    chars = len(raw)
    trace = uuid.uuid4().hex[:8]
    debug = _raw_logging_enabled()

    if debug:
        body = repr(redact_secrets(raw))
    else:
        body = "<redacted; set OMEGACLAW_DEBUG_LLM_RAW=1>"
    print(f"[LLM_RAW] ts={ts} trace={trace} provider={provider} model={model} chars={chars} raw={body}")

    log_path = os.environ.get("OMEGACLAW_LLM_LOG_PATH")
    if log_path:
        record = {"ts": ts, "trace": trace, "provider": provider, "model": model, "chars": chars}
        if debug:
            record["raw"] = redact_secrets(raw)
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:  # best-effort; never break the chat path
            print(f"[LLM_RAW] WARNING could not write OMEGACLAW_LLM_LOG_PATH ({log_path}): {exc}")


_PROMPT_SEPARATOR = ":-:-:-:"


def split_system_user(content: str):
    """Parse the agent prompt into ``(system, user)`` around the ``:-:-:-:``
    separator. The single place this delimiter is parsed (Issue #4) -- providers
    then decide how to send the two parts. No separator -> ``("", content)``."""
    if content is None:
        return "", ""
    if _PROMPT_SEPARATOR in content:
        system, user = content.split(_PROMPT_SEPARATOR, 1)
        return system, user
    return "", content


class AbstractAIProvider:
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def chat(self, content: str, max_tokens: int = 6000, reasoning: str = "medium", **kwargs) -> str:
        raise NotImplementedError

    @property
    def is_available(self) -> bool:
        raise NotImplementedError

class AIProvider(AbstractAIProvider):
    """Lazy AI provider with on-demand initialization."""

    def __init__(self, name: str, var_name: str, model_name: str, base_url: str):
        super().__init__(name)
        self._var_name = var_name
        self._model_name = model_name
        self._base_url = base_url
        self._client = None  # lazy initialization

    def _ensure_client(self):
        """Initialize client on first use."""
        if self._client is None:
            self._client = self._create_client()

    def _create_client(self) -> Optional[openai.OpenAI]:
        """Create OpenAI client from environment."""
        proxy_url = os.environ.get("GATEWAY_URL")
        if proxy_url:
            prefix = self._name.lower()
            base_url = f"{proxy_url.rstrip('/')}/{prefix}/"
            print(f"[lib_llm_ext.AIProvider._create_client] Connecting via proxy: {base_url}")
            return openai.OpenAI(
                    api_key="proxy",
                    base_url=base_url,
                    )
        if self._var_name in os.environ:
            if self._var_name == "OLLAMA_API_KEY":
                llm_server_local_url = os.environ.get("LLM_SERVER_LOCAL_URL")
                if llm_server_local_url:
                    self._base_url = llm_server_local_url.rstrip("/") + "/v1"
                elif not self._base_url.endswith("/v1"):
                    self._base_url = self._base_url.rstrip("/") + "/v1"

            return openai.OpenAI(api_key=os.environ.get(self._var_name), base_url=self._base_url)

        return None

    @property
    def is_available(self) -> bool:
        """Check if provider is configured (without initializing)."""
        return bool(os.environ.get("GATEWAY_URL")) or bool(os.environ.get(self._var_name))

    def chat(self, content: str, max_tokens: int = 6000, reasoning: str = "medium", **kwargs) -> str:
        """Send chat request, initializing client if needed."""
        self._ensure_client()

        if self._client is None:
            raise RuntimeError(f"{self.name} not configured (set {self._var_name})")

        sysmsg, usermsg = split_system_user(content)
        content = f"{sysmsg} {usermsg}" if sysmsg else usermsg
        try:
            response = self._client.chat.completions.create(
                model=self._model_name,
                messages=[{"role": "user", "content": content}],
                max_tokens=max_tokens,
                **kwargs
            )

            raw = response.choices[0].message.content or ""
            _log_raw(self._name, self._model_name, raw)
            return self._clean_text(raw)
        except Exception as e:
            print(f"[lib_llm_ext.AIProvider.chat] Exception while communicating with LLM: {e}")
            return ""

    def _clean_text(self, text: str) -> str:
        """Unescape special characters."""
        return text.replace("_quote_", '"').replace("_apostrophe_", "'")

class OpenRouterProvider(AIProvider):
    """OpenRouter provider with reasoning mode enabled (reasoning tokens excluded from the response).

    The reasoning block is config-driven (provider entry's ``reasoning:``)."""

    def __init__(self, name: str, var_name: str, model_name: str, base_url: str, reasoning: dict = None):
        super().__init__(name, var_name, model_name, base_url)
        self._reasoning = reasoning or {"enabled": True, "max_tokens": 6000, "exclude": True}

    def chat(self, content: str, max_tokens: int = 6000, reasoning: str = "medium", **kwargs) -> str:
        return super().chat(content, max_tokens, reasoning, extra_body={"reasoning": self._reasoning}, **kwargs)

class AsiOneProvider(AIProvider):
    """Lazy AI provider with on-demand initialization."""

    def __init__(self, name: str, var_name: str, model_name: str, base_url: str):
        super().__init__(name, var_name, model_name, base_url)

    def chat(self, content: str, max_tokens: int = 6000, reasoning: str = "medium", **kwargs) -> str:
        """Send chat request, initializing client if needed."""
        self._ensure_client()

        if self._client is None:
            raise RuntimeError(f"{self.name} not configured (set {self._var_name})")

        sysmsg, usermsg = split_system_user(content)
        try:
            response = self._client.chat.completions.create(
                model=self._model_name,
                messages=[{"role": "system", "content": sysmsg},
                          {"role": "user", "content": usermsg}],
                max_tokens=max_tokens,
                extra_body={
                    "enable_thinking": True,
                    "thinking_budget": 6000 
                },
                **kwargs
            )

            raw = response.choices[0].message.content
            _log_raw(self._name, self._model_name, raw)
            resp = self._clean_text(raw)
            resp = resp.replace("</arg_value>", " ").replace("</tool_call>", " ").replace("<arg_value>", " ").replace("<tool_call>", " ")
            return resp
        except Exception as e:
            print(f"[lib_llm_ext.ASIOneProvider.chat] Exception while communicating with LLM: {e}")
            return ""


class OpenAIProvider(AIProvider):
    """OpenAI provider using the Responses API (reasoning models)."""

    def chat(self, content: str, max_tokens: int = 6000, reasoning: str = "medium", **kwargs) -> str:
        """Send chat request via the Responses API, initializing client if needed."""
        self._ensure_client()

        if self._client is None:
            raise RuntimeError(f"{self.name} not configured (set {self._var_name})")

        sysmsg, usermsg = split_system_user(content)
        try:
            response = self._client.responses.create(
                model=self._model_name,
                instructions=sysmsg,
                input=usermsg,
                max_output_tokens=max_tokens,
                reasoning={"effort": reasoning},
                **kwargs
            )

            raw = response.output_text
            _log_raw(self._name, self._model_name, raw)
            return self._clean_text(raw)
        except Exception as e:
            print(f"[lib_llm_ext.OpenAIProvider.chat] Exception while communicating with LLM: {e}")
            return ""


class TestProvider(AbstractAIProvider):
    """Test provider for mocking LLM output"""

    def __init__(self):
        super().__init__("Test")
        self._mock = None
        self._controller_ip = os.environ.get("TEST_SERVER_IP")

    def _llm_mock(self):
        if not self._mock:
            from Autotests.mock.llm import LlmMockAgent, LLM_MOCK_PORT
            self._mock = LlmMockAgent((self._controller_ip, LLM_MOCK_PORT))
        return self._mock

    @property
    def is_available(self) -> bool:
        return self._controller_ip is not None

    def chat(self, content: str, max_tokens: int = 6000, reasoning: str = "medium", **kwargs) -> str:
        return self._llm_mock().chat(content)

# Provider registry - lazy, no initialization yet
_provider_registry = {}


def _register_provider(name: str, var_name: str, model_name: str, base_url: str):
    """Register a provider configuration (no instantiation yet)."""
    _register_provider_instance(AIProvider(name, var_name, model_name, base_url))

def _register_provider_instance(provider: AbstractAIProvider):
    """Register a pre-initialized provider configuration (no instantiation yet)."""
    _provider_registry[provider.name] = provider

def _get_provider(name: str) -> Optional[AIProvider]:
    """Get or create provider instance on demand."""
    return _provider_registry.get(name)


def _build_provider(name: str, entry: dict) -> AbstractAIProvider:
    """Construct the right provider class from a config entry (Issue #4)."""
    var_name = entry["api_key_env"]
    model = entry["model"]
    base_url = entry["base_url"]
    style = entry.get("api_style", "chat_completions")
    if style == "responses":
        return OpenAIProvider(name, var_name, model, base_url)
    if style == "asione":
        return AsiOneProvider(name, var_name, model, base_url)
    if entry.get("reasoning"):
        return OpenRouterProvider(name, var_name, model, base_url, reasoning=entry["reasoning"])
    return AIProvider(name, var_name, model, base_url)


def _register_from_config():
    """Register all providers from the declarative config, then the Test provider."""
    cfg = provider_config.load_config()
    for name, entry in (cfg.get("providers") or {}).items():
        try:
            _register_provider_instance(_build_provider(name, entry))
        except Exception as exc:  # pragma: no cover - defensive; skip a bad entry
            print(f"[lib_llm_ext] WARNING could not register provider {name!r}: {exc}", flush=True)
    # The Test provider is environment-driven and always registered (mock harness).
    _register_provider_instance(TestProvider())


_register_from_config()


def effective_model(provider_name: str) -> str:
    """The model the given provider will actually use (read from the registry)."""
    provider = _get_provider(provider_name)
    return getattr(provider, "_model_name", "") if provider else ""


def describe_effective_config(provider_name: str) -> str:
    """One-line startup summary of the effective provider/model (Issue #4)."""
    provider = _get_provider(provider_name)
    if provider is None:
        return f"[llm_config] provider={provider_name} UNKNOWN (not registered) -- check `provider` config"
    model = getattr(provider, "_model_name", "(n/a)")
    base = getattr(provider, "_base_url", "(n/a)")
    reasoning = getattr(provider, "_reasoning", None)
    extra = f" reasoning={reasoning}" if reasoning else ""
    return (
        f"[llm_config] provider={provider_name} model={model} base_url={base} "
        f"class={type(provider).__name__} available={provider.is_available}{extra}"
    )


def callProvider(provider_name: str, content: str, max_tokens: int = 6000, reasoning: str = "medium") -> str:
    """Generic dispatcher for MeTTa."""
    provider = _get_provider(provider_name)
    if not provider or not provider.is_available:
        raise RuntimeError(f"Provider '{provider_name}' not available")
    return provider.chat(content=content, max_tokens=max_tokens, reasoning=reasoning)



_embedding_model = None

def initLocalEmbedding():
    model_name="intfloat/e5-large-v2"
    global _embedding_model
    os.environ["HF_HUB_OFFLINE"] = "1"
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer(model_name)
    return _embedding_model

def useLocalEmbedding(atom):
    global _embedding_model
    if _embedding_model is None:
        raise RuntimeError("Call initLocalEmbedding() first.")
    return _embedding_model.encode(
        atom,
        normalize_embeddings=True
    ).tolist()


