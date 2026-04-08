"""Static model provider registry for user-configurable chat models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderModelDefinition:
    """A concrete model made available under a provider."""

    name: str
    display_name: str
    description: str = ""
    supports_vision: bool = False
    supports_thinking: bool = False
    supports_reasoning_effort: bool = False
    runtime_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderDefinition:
    """Static provider metadata used by backend and frontend configuration flows."""

    code: str
    display_name: str
    description: str
    icon_key: str
    use: str
    base_url: str
    api_key_label: str = "API Key"
    models_api_path: str = "/models"
    remote_models_format: str = "openai_compatible"
    models: tuple[ProviderModelDefinition, ...] = field(default_factory=tuple)
    default_settings: dict[str, object] = field(default_factory=dict)


def _openai_compatible_provider(
    *,
    code: str,
    display_name: str,
    description: str,
    icon_key: str,
    base_url: str,
    models: tuple[ProviderModelDefinition, ...] = (),
) -> ProviderDefinition:
    return ProviderDefinition(
        code=code,
        display_name=display_name,
        description=description,
        icon_key=icon_key,
        use="langchain_openai:ChatOpenAI",
        base_url=base_url,
        models=models,
        default_settings={"timeout": 240},
    )


PROVIDER_REGISTRY: tuple[ProviderDefinition, ...] = (
    _openai_compatible_provider(
        code="custom",
        display_name="Custom",
        description="自定义 OpenAI-compatible 供应商。",
        icon_key="custom",
        base_url="",
    ),
    ProviderDefinition(
        code="openai",
        display_name="OpenAI",
        description="OpenAI 官方模型与兼容 OpenAI 协议的 GPT 系列。",
        icon_key="openai",
        use="langchain_openai:ChatOpenAI",
        base_url="https://api.openai.com/v1",
        models=(
            ProviderModelDefinition("gpt-4.1", "GPT-4.1", "通用高质量模型。", supports_vision=True),
            ProviderModelDefinition("gpt-4.1-mini", "GPT-4.1 Mini", "更快更经济的通用模型。", supports_vision=True),
            ProviderModelDefinition("gpt-4o", "GPT-4o", "多模态通用模型。", supports_vision=True),
            ProviderModelDefinition("gpt-4o-mini", "GPT-4o Mini", "轻量多模态模型。", supports_vision=True),
            ProviderModelDefinition("o3", "o3", "偏推理型模型。", supports_reasoning_effort=True),
            ProviderModelDefinition("o4-mini", "o4-mini", "轻量推理模型。", supports_reasoning_effort=True),
            ProviderModelDefinition(
                "gpt-5.3-codex",
                "GPT-5.3 Codex",
                "系统当前默认可用的编码模型。",
                supports_vision=True,
                supports_reasoning_effort=True,
                runtime_aliases=("gpt-5.3-codex",),
            ),
            ProviderModelDefinition(
                "gpt-5.4",
                "GPT-5.4",
                "系统当前默认可用的通用模型。",
                supports_vision=True,
                supports_reasoning_effort=True,
                runtime_aliases=("gpt-5.4",),
            ),
        ),
        default_settings={"timeout": 240},
    ),
    ProviderDefinition(
        code="anthropic",
        display_name="Anthropic",
        description="Claude 系列模型。",
        icon_key="anthropic",
        use="langchain_anthropic:ChatAnthropic",
        base_url="https://api.anthropic.com",
        models_api_path="/v1/models",
        remote_models_format="anthropic",
        models=(
            ProviderModelDefinition("claude-3-7-sonnet-20250219", "Claude 3.7 Sonnet", "Claude 推理/通用模型。"),
            ProviderModelDefinition("claude-3-5-haiku-20241022", "Claude 3.5 Haiku", "轻量快速模型。"),
            ProviderModelDefinition("claude-sonnet-4-20250514", "Claude Sonnet 4", "新一代通用模型。"),
        ),
        default_settings={"timeout": 240},
    ),
    ProviderDefinition(
        code="gemini",
        display_name="Gemini",
        description="Google Gemini 系列模型。",
        icon_key="gemini",
        use="langchain_google_genai:ChatGoogleGenerativeAI",
        base_url="https://generativelanguage.googleapis.com",
        models_api_path="/v1beta/models",
        remote_models_format="gemini",
        models=(
            ProviderModelDefinition("gemini-2.5-pro", "Gemini 2.5 Pro", "高质量通用模型。", supports_vision=True),
            ProviderModelDefinition("gemini-2.5-flash", "Gemini 2.5 Flash", "快速多模态模型。", supports_vision=True),
        ),
        default_settings={"timeout": 240},
    ),
    ProviderDefinition(
        code="deepseek",
        display_name="DeepSeek",
        description="DeepSeek 官方 OpenAI-compatible 模型。",
        icon_key="deepseek",
        use="langchain_openai:ChatOpenAI",
        base_url="https://api.deepseek.com/v1",
        models=(
            ProviderModelDefinition("deepseek-chat", "DeepSeek Chat", "通用对话模型。"),
            ProviderModelDefinition("deepseek-reasoner", "DeepSeek Reasoner", "偏推理型模型。", supports_reasoning_effort=True),
        ),
        default_settings={"timeout": 240},
    ),
    _openai_compatible_provider(
        code="minimax",
        display_name="MiniMax",
        description="MiniMax 开放平台，采用兼容 OpenAI 的接入方式。",
        icon_key="minimax",
        base_url="https://api.minimaxi.com/v1",
        models=(
            ProviderModelDefinition("MiniMax-M2.7", "MiniMax-M2.7", "MiniMax 通用模型。"),
            ProviderModelDefinition("MiniMax-M2.5", "MiniMax-M2.5", "MiniMax 通用模型。"),
            ProviderModelDefinition("MiniMax-M2.1", "MiniMax-M2.1", "MiniMax 推理/通用模型。"),
            ProviderModelDefinition("MiniMax-M2.1-lightning", "MiniMax-M2.1 Lightning", "更快的 MiniMax 模型。"),
            ProviderModelDefinition("MiniMax-M2", "MiniMax-M2", "长上下文通用模型。"),
            ProviderModelDefinition("M2-her", "M2-her", "角色扮演与角色对话模型。"),
        ),
    ),
    _openai_compatible_provider(
        code="novita",
        display_name="Novita",
        description="Novita AI 开放平台。",
        icon_key="novita",
        base_url="https://api.novita.ai/openai/v1",
    ),
    ProviderDefinition(
        code="dashscope",
        display_name="DashScope",
        description="阿里云百炼 / 通义千问模型。",
        icon_key="qwen",
        use="langchain_openai:ChatOpenAI",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        models=(
            ProviderModelDefinition(
                "qwen3.5-flash",
                "Qwen 3.5 Flash",
                "系统当前默认可用的通义模型。",
                supports_vision=True,
                runtime_aliases=("qwen3.5-flash",),
            ),
            ProviderModelDefinition("qwen-plus", "Qwen Plus", "通用中文模型。"),
            ProviderModelDefinition("qwen-max", "Qwen Max", "更强通用模型。"),
            ProviderModelDefinition("qwen-vl-max", "Qwen VL Max", "视觉模型。", supports_vision=True),
        ),
        default_settings={"timeout": 240},
    ),
    _openai_compatible_provider(
        code="dashscope-coding",
        display_name="DashScope Coding",
        description="阿里云百炼 Coding 接入点。",
        icon_key="qwen",
        base_url="https://coding.dashscope.aliyuncs.com/v1",
        models=(
            ProviderModelDefinition("qwen3-coder-plus", "Qwen3 Coder Plus", "通义代码模型。"),
            ProviderModelDefinition("qwen3-coder-next", "Qwen3 Coder Next", "通义代码模型。"),
            ProviderModelDefinition("qwen3.5-plus", "Qwen3.5 Plus", "通用模型。"),
            ProviderModelDefinition("qwen3-max-2026-01-23", "Qwen3 Max", "更强通用模型。"),
            ProviderModelDefinition("glm-4.7", "GLM 4.7", "通用模型。"),
            ProviderModelDefinition("glm-5", "GLM 5", "新一代通用模型。"),
            ProviderModelDefinition("MiniMax-M2.5", "MiniMax-M2.5", "兼容接入的 MiniMax 模型。"),
            ProviderModelDefinition("kimi-k2.5", "Kimi K2.5", "兼容接入的 Moonshot 模型。"),
        ),
    ),
    _openai_compatible_provider(
        code="siliconflow-cn",
        display_name="SiliconFlow-CN",
        description="SiliconFlow 中国站 OpenAI-compatible 接入点。",
        icon_key="siliconflow",
        base_url="https://api.siliconflow.cn/v1",
    ),
    _openai_compatible_provider(
        code="siliconflow",
        display_name="SiliconFlow",
        description="SiliconFlow 国际站 OpenAI-compatible 接入点。",
        icon_key="siliconflow",
        base_url="https://api.siliconflow.com/v1",
    ),
    ProviderDefinition(
        code="zhipu",
        display_name="Zhipu",
        description="智谱 GLM 系列模型。",
        icon_key="zhipu",
        use="langchain_openai:ChatOpenAI",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        models=(
            ProviderModelDefinition("glm-4.5", "GLM 4.5", "通用模型。"),
            ProviderModelDefinition("glm-4v-plus", "GLM 4V Plus", "视觉模型。", supports_vision=True),
        ),
        default_settings={"timeout": 240},
    ),
    _openai_compatible_provider(
        code="moonshot",
        display_name="Moonshot (China)",
        description="Moonshot 中国区开放平台。",
        icon_key="kimi",
        base_url="https://api.moonshot.cn/v1",
    ),
    _openai_compatible_provider(
        code="moonshot-global",
        display_name="Moonshot (Global)",
        description="Moonshot 国际开放平台。",
        icon_key="kimi",
        base_url="https://api.moonshot.ai/v1",
    ),
    _openai_compatible_provider(
        code="xai",
        display_name="xAI",
        description="xAI 开放平台。",
        icon_key="xai",
        base_url="https://api.x.ai/v1",
    ),
    _openai_compatible_provider(
        code="ark",
        display_name="Ark",
        description="火山引擎 Ark 开放平台。",
        icon_key="volcengine",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
    ),
    _openai_compatible_provider(
        code="qianfan",
        display_name="Qianfan",
        description="百度千帆模型平台。",
        icon_key="baidu",
        base_url="https://qianfan.baidubce.com/v2",
    ),
    _openai_compatible_provider(
        code="hunyuan",
        display_name="Hunyuan",
        description="腾讯混元模型平台。",
        icon_key="tencent",
        base_url="https://api.hunyuan.cloud.tencent.com/v1",
    ),
    _openai_compatible_provider(
        code="lingyi",
        display_name="Lingyi",
        description="零一万物开放平台。",
        icon_key="lingyi",
        base_url="https://api.lingyiwanwu.com/v1",
    ),
    ProviderDefinition(
        code="openrouter",
        display_name="OpenRouter",
        description="OpenRouter 聚合平台。",
        icon_key="openrouter",
        use="langchain_openai:ChatOpenAI",
        base_url="https://openrouter.ai/api/v1",
        models=(
            ProviderModelDefinition("openai/gpt-4.1-mini", "OpenRouter / GPT-4.1 Mini", "通过 OpenRouter 访问。", supports_vision=True),
            ProviderModelDefinition("anthropic/claude-3.7-sonnet", "OpenRouter / Claude 3.7 Sonnet", "通过 OpenRouter 访问。"),
            ProviderModelDefinition("google/gemini-2.5-pro", "OpenRouter / Gemini 2.5 Pro", "通过 OpenRouter 访问。", supports_vision=True),
            ProviderModelDefinition("deepseek/deepseek-chat-v3-0324", "OpenRouter / DeepSeek V3", "通过 OpenRouter 访问。"),
        ),
        default_settings={"timeout": 240},
    ),
    _openai_compatible_provider(
        code="poe",
        display_name="Poe",
        description="Poe 开放平台。",
        icon_key="poe",
        base_url="https://api.poe.com/v1",
    ),
    _openai_compatible_provider(
        code="ppio",
        display_name="PPIO",
        description="PPIO 模型平台。",
        icon_key="ppio",
        base_url="https://api.ppinfra.com/v3/openai",
    ),
    _openai_compatible_provider(
        code="modelscope",
        display_name="ModelScope",
        description="魔搭 ModelScope 开放平台。",
        icon_key="modelscope",
        base_url="https://api-inference.modelscope.cn/v1",
    ),
    _openai_compatible_provider(
        code="infiniai",
        display_name="InfiniAI",
        description="InfiniAI 模型平台。",
        icon_key="infiniai",
        base_url="https://cloud.infini-ai.com/maas/v1",
    ),
    _openai_compatible_provider(
        code="ctyun",
        display_name="Ctyun",
        description="天翼云模型平台。",
        icon_key="ctyun",
        base_url="https://wishub-x1.ctyun.cn/v1",
    ),
    _openai_compatible_provider(
        code="stepfun",
        display_name="StepFun",
        description="阶跃星辰开放平台。",
        icon_key="stepfun",
        base_url="https://api.stepfun.com/v1",
    ),
)


_PROVIDER_BY_CODE = {provider.code: provider for provider in PROVIDER_REGISTRY}
_RUNTIME_ALIAS_TO_PROVIDER_MODEL: dict[str, tuple[ProviderDefinition, ProviderModelDefinition]] = {}

for _provider in PROVIDER_REGISTRY:
    for _model in _provider.models:
        for _alias in _model.runtime_aliases:
            _RUNTIME_ALIAS_TO_PROVIDER_MODEL[_alias] = (_provider, _model)


def list_provider_definitions() -> list[ProviderDefinition]:
    return list(PROVIDER_REGISTRY)


def get_provider_definition(provider_code: str) -> ProviderDefinition | None:
    normalized = str(provider_code or "").strip().lower()
    return _PROVIDER_BY_CODE.get(normalized)


def get_provider_model_definition(provider_code: str, model_name: str) -> ProviderModelDefinition | None:
    provider = get_provider_definition(provider_code)
    if provider is None:
        return None
    normalized_model_name = str(model_name or "").strip()
    for item in provider.models:
        if item.name == normalized_model_name:
            return item
    return None


def find_provider_for_runtime_model(runtime_model_name: str) -> tuple[ProviderDefinition, ProviderModelDefinition] | None:
    normalized = str(runtime_model_name or "").strip()
    if not normalized:
        return None

    direct = _RUNTIME_ALIAS_TO_PROVIDER_MODEL.get(normalized)
    if direct is not None:
        return direct

    for provider in PROVIDER_REGISTRY:
        for model in provider.models:
            if model.name == normalized:
                return provider, model
    return None
