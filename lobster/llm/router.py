"""
🐦 LLM 路由器 - 模型选择 + 动态切换 + 自动降级 + 响应超时
"""

import asyncio
import logging
from .base import BaseLLMProvider, LLMResponse, Message
from .anthropic_provider import AnthropicProvider
from .openai_provider import OpenAIProvider

logger = logging.getLogger("lobster.llm")

PROVIDERS = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "deepseek": OpenAIProvider,
    "doubao": OpenAIProvider,
}

MODEL_DISPLAY_NAMES = {
    "deepseek": "DeepSeek (日常对话)",
    "doubao": "豆包 (多模态·可看图)",
    "anthropic": "Claude (高质量·可看图)",
    "openai": "GPT (通用·可看图)",
}

MULTIMODAL_PROVIDERS = {"doubao", "anthropic", "openai"}

DEFAULT_FALLBACK_CHAIN = ["anthropic", "openai", "deepseek", "doubao"]


class LLMRouter:
    """
    LLM 路由器
    - 根据配置选择主 Provider
    - 支持运行时动态切换模型（全局 + 按会话）
    - 调用失败时自动降级到备用 Provider
    """

    MODEL_DISPLAY_NAMES = MODEL_DISPLAY_NAMES

    def __init__(self, config):
        self.config = config
        self.providers: dict[str, BaseLLMProvider] = {}
        self.primary = config.llm.provider
        self.total_usage = {"input_tokens": 0, "output_tokens": 0}
        self.task_usage = {"input_tokens": 0, "output_tokens": 0}
        self._session_models: dict[str, str] = {}
        self._custom_providers: list[str] = []
        agent_cfg = getattr(config, "agent", None)
        self._llm_timeout = getattr(agent_cfg, "llm_timeout", 120) if agent_cfg else 120
        self._init_providers()

    def _init_providers(self):
        llm = self.config.llm

        if llm.anthropic_api_key:
            self.providers["anthropic"] = AnthropicProvider(
                model=llm.model if llm.provider == "anthropic" else "claude-sonnet-4-20250514",
                api_key=llm.anthropic_api_key,
            )

        if llm.openai_api_key:
            self.providers["openai"] = OpenAIProvider(
                model=llm.model if llm.provider == "openai" else "gpt-4o",
                api_key=llm.openai_api_key,
            )

        if llm.deepseek_api_key:
            self.providers["deepseek"] = OpenAIProvider(
                model=llm.model if llm.provider == "deepseek" else "deepseek-chat",
                api_key=llm.deepseek_api_key,
                base_url=llm.deepseek_base_url,
            )

        if llm.doubao_api_key:
            self.providers["doubao"] = OpenAIProvider(
                model=llm.doubao_model,
                api_key=llm.doubao_api_key,
                base_url=llm.doubao_base_url,
            )

        for cp in llm.get_custom_providers():
            name = cp["name"]
            if name in self.providers:
                logger.warning(f"自定义 provider {name} 与内置同名，跳过")
                continue
            self.providers[name] = OpenAIProvider(
                model=cp["model"],
                api_key=cp["api_key"],
                base_url=cp["base_url"],
                fixed_temperature=cp.get("fixed_temperature"),
                extra_body=cp.get("extra_body"),
            )
            self._custom_providers.append(name)
            extra_info = f", fixed_temp={cp.get('fixed_temperature')}, extra_body={cp.get('extra_body')}" if cp.get("fixed_temperature") or cp.get("extra_body") else ""
            logger.info(f"已注册自定义 provider: {name} (model={cp['model']}, base_url={cp['base_url']}{extra_info})")

        if not self.providers:
            raise ValueError("至少需要配置一个 LLM Provider 的 API Key!")

        agent_cfg = getattr(self.config, "agent", None)
        max_out = getattr(agent_cfg, "max_output_tokens", 16384) if agent_cfg else 16384
        for p in self.providers.values():
            p.max_output_tokens = max_out

        logger.info(f"已初始化 LLM Providers: {list(self.providers.keys())} (max_output_tokens={max_out})")
        logger.info(f"主 Provider: {self.primary}")

    def set_session_model(self, session_id: str, provider_name: str) -> bool:
        """为指定会话设置模型。返回 False 表示该 provider 不可用。"""
        if provider_name not in self.providers:
            return False
        self._session_models[session_id] = provider_name
        logger.info(f"会话 {session_id} 切换模型 → {provider_name}")
        return True

    def clear_session_model(self, session_id: str):
        """清除会话的模型覆盖，回退到全局默认。"""
        self._session_models.pop(session_id, None)

    def get_active_provider(self, session_id: str = "") -> str:
        """获取指定会话当前生效的 provider 名称。"""
        if session_id and session_id in self._session_models:
            return self._session_models[session_id]
        return self.primary

    def list_models(self) -> list[dict]:
        """列出所有可用模型，返回 [{name, model_id, description, available}]"""
        result = []
        for name in ["deepseek", "doubao", "anthropic", "openai"]:
            available = name in self.providers
            model_id = ""
            if available:
                model_id = self.providers[name].model
            result.append({
                "name": name,
                "model_id": model_id,
                "description": MODEL_DISPLAY_NAMES.get(name, name),
                "available": available,
            })
        for name in self._custom_providers:
            if name in self.providers:
                result.append({
                    "name": name,
                    "model_id": self.providers[name].model,
                    "description": f"{name} (自定义)",
                    "available": True,
                })
        return result

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        system_prompt: str = "",
        temperature: float | None = None,
        session_id: str = "",
    ) -> LLMResponse:
        """
        发送对话请求。
        优先使用会话级模型 → 全局主模型 → 降级链。
        消息含图片时自动路由到多模态模型。
        """
        active = self.get_active_provider(session_id)

        has_images = any(msg.images for msg in messages)
        if has_images and active not in MULTIMODAL_PROVIDERS:
            for mm in ["doubao", "anthropic", "openai"]:
                if mm in self.providers:
                    logger.info(f"消息含图片，自动路由 {active} → {mm}")
                    active = mm
                    break

        fallback = DEFAULT_FALLBACK_CHAIN + [
            p for p in self._custom_providers if p not in DEFAULT_FALLBACK_CHAIN
        ]
        chain = [active] + [p for p in fallback if p != active]

        last_error = None
        for provider_name in chain:
            if provider_name not in self.providers:
                continue
            provider = self.providers[provider_name]
            try:
                response = await asyncio.wait_for(
                    provider.chat(
                        messages=messages,
                        tools=tools,
                        system_prompt=system_prompt,
                        temperature=temperature,
                    ),
                    timeout=self._llm_timeout,
                )
                inp = response.usage.get("input_tokens", 0)
                out = response.usage.get("output_tokens", 0)
                self.total_usage["input_tokens"] += inp
                self.total_usage["output_tokens"] += out
                self.task_usage["input_tokens"] += inp
                self.task_usage["output_tokens"] += out

                if provider_name != active:
                    logger.warning(f"已降级到备用 Provider: {provider_name}")

                return response

            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"{provider_name} 响应超时 ({self._llm_timeout}s)"
                )
                logger.error(f"Provider {provider_name} 响应超时 ({self._llm_timeout}s)，尝试降级")
                continue

            except Exception as e:
                error_str = str(e)
                last_error = e
                logger.error(f"Provider {provider_name} 调用失败: {e}")

                # tool_calls 配对错误属于消息格式问题，降级到其他 Provider 也可能失败
                # 应该立即抛出让 Agent 修复消息，而不是浪费时间降级
                if "tool_calls" in error_str and "tool messages" in error_str:
                    logger.warning("检测到 tool_calls 配对错误，跳过降级直接上报")
                    raise
                continue

        raise RuntimeError(f"所有 LLM Provider 均失败! 最后错误: {last_error}")

    def reset_task_usage(self) -> None:
        """每次任务开始时重置单次用量"""
        self.task_usage = {"input_tokens": 0, "output_tokens": 0}

    def get_usage_summary(self) -> str:
        return (
            f"Token 用量 - 本次: 输入 {self.task_usage['input_tokens']:,} / 输出 {self.task_usage['output_tokens']:,}"
            f" | 累计: 输入 {self.total_usage['input_tokens']:,} / 输出 {self.total_usage['output_tokens']:,}"
        )
