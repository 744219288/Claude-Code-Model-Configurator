"""Curated Anthropic-compatible providers and model mappings.

Only providers with a documented direct Anthropic endpoint belong here.  The
installer intentionally does not run a local protocol-conversion proxy.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelProfile:
    id: str
    api_model: str
    label: str
    description: str
    context_tokens: int
    opus_model: str
    sonnet_model: str
    haiku_model: str
    subagent_model: str
    auto_compact_window: int = 0
    effort_level: str = ""


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    label: str
    short_label: str
    base_url: str
    key_label: str
    docs_url: str
    models: tuple[ModelProfile, ...]
    default_model: str
    extra_environment: dict[str, str] = field(default_factory=dict)


def _single_model(
    model_id: str,
    *,
    api_model: str | None = None,
    label: str,
    description: str,
    context_tokens: int,
    auto_compact_window: int = 0,
    effort_level: str = "",
) -> ModelProfile:
    return ModelProfile(
        id=model_id,
        api_model=api_model or model_id.removesuffix("[1m]"),
        label=label,
        description=description,
        context_tokens=context_tokens,
        opus_model=model_id,
        sonnet_model=model_id,
        haiku_model=model_id,
        subagent_model=model_id,
        auto_compact_window=auto_compact_window,
        effort_level=effort_level,
    )


DEEPSEEK_FLASH = ModelProfile(
    id="deepseek-v4-flash",
    api_model="deepseek-v4-flash",
    label="DeepSeek V4 Flash",
    description="快速 · 日常开发",
    context_tokens=1_000_000,
    opus_model="deepseek-v4-pro[1m]",
    sonnet_model="deepseek-v4-pro[1m]",
    haiku_model="deepseek-v4-flash",
    subagent_model="deepseek-v4-flash",
    auto_compact_window=786_432,
    effort_level="max",
)
DEEPSEEK_PRO = ModelProfile(
    id="deepseek-v4-pro[1m]",
    api_model="deepseek-v4-pro",
    label="DeepSeek V4 Pro 1M",
    description="推理 · 复杂任务",
    context_tokens=1_000_000,
    opus_model="deepseek-v4-pro[1m]",
    sonnet_model="deepseek-v4-pro[1m]",
    haiku_model="deepseek-v4-flash",
    subagent_model="deepseek-v4-flash",
    auto_compact_window=786_432,
    effort_level="max",
)

GLM_47 = _single_model(
    "glm-4.7",
    label="GLM-4.7",
    description="均衡 · 稳定编程",
    context_tokens=200_000,
    effort_level="max",
)
GLM_52 = ModelProfile(
    id="glm-5.2[1m]",
    api_model="glm-5.2",
    label="GLM-5.2 1M",
    description="旗舰 · 长程工程",
    context_tokens=1_000_000,
    opus_model="glm-5.2[1m]",
    sonnet_model="glm-5.2[1m]",
    haiku_model="glm-4.7",
    subagent_model="glm-4.7",
    auto_compact_window=1_000_000,
    effort_level="max",
)

MINIMAX_M3 = _single_model(
    "MiniMax-M3[1m]",
    api_model="MiniMax-M3",
    label="MiniMax M3 1M",
    description="长上下文 · 扩展思考",
    context_tokens=1_000_000,
    auto_compact_window=1_000_000,
)

QWEN_CODING = _single_model(
    "qwen3.7-plus",
    label="Qwen3.7 Plus",
    description="百炼 Coding Plan",
    context_tokens=1_000_000,
    auto_compact_window=983_616,
)
QWEN_TOKEN_MAX = ModelProfile(
    id="qwen3.8-max",
    api_model="qwen3.8-max",
    label="Qwen3.8 Max",
    description="百炼 Token Plan · 强力",
    context_tokens=1_000_000,
    opus_model="qwen3.8-max",
    sonnet_model="qwen3.8-max",
    haiku_model="qwen3.6-flash",
    subagent_model="qwen3.7-max",
    auto_compact_window=983_616,
)
QWEN_TOKEN_FLASH = _single_model(
    "qwen3.6-flash",
    label="Qwen3.6 Flash",
    description="百炼 Token Plan · 快速",
    context_tokens=1_000_000,
    auto_compact_window=983_616,
)
QWEN_PAYG_MAX = ModelProfile(
    id="qwen3.7-max",
    api_model="qwen3.7-max",
    label="Qwen3.7 Max",
    description="百炼按量付费 · 旗舰",
    context_tokens=1_000_000,
    opus_model="qwen3.7-max",
    sonnet_model="qwen3.7-max",
    haiku_model="qwen3.6-flash",
    subagent_model="qwen3.7-max",
    auto_compact_window=1_000_000,
)


PROVIDERS: dict[str, ProviderProfile] = {
    "deepseek": ProviderProfile(
        id="deepseek",
        label="DeepSeek",
        short_label="DeepSeek",
        base_url="https://api.deepseek.com/anthropic",
        key_label="DeepSeek API Key",
        docs_url="https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/claude_code/",
        models=(DEEPSEEK_FLASH, DEEPSEEK_PRO),
        default_model=DEEPSEEK_FLASH.id,
    ),
    "zhipu": ProviderProfile(
        id="zhipu",
        label="智谱 GLM",
        short_label="GLM",
        base_url="https://open.bigmodel.cn/api/anthropic",
        key_label="智谱 API Key / Coding Plan Key",
        docs_url="https://docs.bigmodel.cn/cn/guide/develop/claude",
        models=(GLM_47, GLM_52),
        default_model=GLM_52.id,
        extra_environment={
            "API_TIMEOUT_MS": "3000000",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
    ),
    "minimax": ProviderProfile(
        id="minimax",
        label="MiniMax",
        short_label="MiniMax",
        base_url="https://api.minimaxi.com/anthropic",
        key_label="MiniMax API Key",
        docs_url="https://platform.minimaxi.com/docs/token-plan/claude-code",
        models=(MINIMAX_M3,),
        default_model=MINIMAX_M3.id,
        extra_environment={
            "API_TIMEOUT_MS": "3000000",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
    ),
    "aliyun-coding": ProviderProfile(
        id="aliyun-coding",
        label="阿里云百炼 · Coding Plan",
        short_label="百炼 Coding",
        base_url="https://coding.dashscope.aliyuncs.com/apps/anthropic",
        key_label="百炼 Coding Plan 专用 Key",
        docs_url="https://help.aliyun.com/zh/model-studio/claude-code",
        models=(QWEN_CODING,),
        default_model=QWEN_CODING.id,
        extra_environment={"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
    ),
    "aliyun-token": ProviderProfile(
        id="aliyun-token",
        label="阿里云百炼 · Token Plan",
        short_label="百炼 Token",
        base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic",
        key_label="百炼 Token Plan 专用 Key",
        docs_url="https://help.aliyun.com/zh/model-studio/claude-code",
        models=(QWEN_TOKEN_MAX, QWEN_TOKEN_FLASH),
        default_model=QWEN_TOKEN_MAX.id,
        extra_environment={"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
    ),
    "aliyun-payg": ProviderProfile(
        id="aliyun-payg",
        label="阿里云百炼 · 按量付费",
        short_label="百炼按量",
        base_url="https://dashscope.aliyuncs.com/apps/anthropic",
        key_label="百炼 Model Studio API Key",
        docs_url="https://help.aliyun.com/zh/model-studio/claude-code",
        models=(QWEN_PAYG_MAX,),
        default_model=QWEN_PAYG_MAX.id,
        extra_environment={"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
    ),
}

DEFAULT_PROVIDER_ID = "deepseek"


def get_provider(provider_id: str) -> ProviderProfile:
    try:
        return PROVIDERS[provider_id]
    except KeyError as exc:
        raise ValueError(f"不支持的模型厂商：{provider_id}") from exc


def get_model(provider_id: str, model_id: str) -> ModelProfile:
    provider = get_provider(provider_id)
    for model in provider.models:
        if model.id == model_id:
            return model
    raise ValueError(f"{provider.label} 不支持的模型：{model_id}")


def provider_labels() -> dict[str, str]:
    return {provider_id: provider.label for provider_id, provider in PROVIDERS.items()}
