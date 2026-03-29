"""
🐦 灵雀 LingQue - 配置管理 (P1 版)
新增: 邮件配置 / 多通道并行开关
"""

from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field

_ENV_CONFIG = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


class LLMConfig(BaseSettings):
    model_config = _ENV_CONFIG
    provider: str = Field("anthropic", alias="LLM_PROVIDER")
    model: str = Field("claude-sonnet-4-20250514", alias="LLM_MODEL")
    anthropic_api_key: Optional[str] = Field(None, alias="ANTHROPIC_API_KEY")
    openai_api_key: Optional[str] = Field(None, alias="OPENAI_API_KEY")
    deepseek_api_key: Optional[str] = Field(None, alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field("https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    doubao_api_key: Optional[str] = Field(None, alias="DOUBAO_API_KEY")
    doubao_base_url: str = Field("https://ark.cn-beijing.volces.com/api/v3", alias="DOUBAO_BASE_URL")
    doubao_model: str = Field("doubao-seed-2-0-pro-260215", alias="DOUBAO_MODEL")

    _BUILTIN_PROVIDERS = {"anthropic", "openai", "deepseek", "doubao"}

    def get_custom_providers(self) -> list[dict]:
        """扫描 LLM_PROVIDER_* 环境变量，解析自定义 OpenAI 兼容 provider。
        格式: LLM_PROVIDER_{NAME}=base_url|api_key|model[|temperature[|extra_json]]
        temperature 可选，用于强制指定（如 Kimi k2.5 思考模式要求 temperature=1）
        extra_json 可选，JSON 格式的额外请求参数（如禁用思考: {"thinking":{"type":"disabled"}}）
        """
        import os
        import json
        import logging
        logger = logging.getLogger("lobster.config")
        results = []
        for key, value in os.environ.items():
            if not key.startswith("LLM_PROVIDER_"):
                continue
            name = key[len("LLM_PROVIDER_"):].lower()
            if not name or name in self._BUILTIN_PROVIDERS:
                continue
            parts = value.split("|", 4)
            if len(parts) < 3:
                logger.warning(
                    f"自定义 provider {name} 格式错误（需要 base_url|api_key|model[|temperature[|extra_json]]）: {key}"
                )
                continue
            base_url, api_key, model = [p.strip() for p in parts[:3]]
            if not all([base_url, api_key, model]):
                logger.warning(f"自定义 provider {name} 有空字段，跳过: {key}")
                continue
            entry = {
                "name": name,
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
            }
            if len(parts) >= 4 and parts[3].strip():
                try:
                    entry["fixed_temperature"] = float(parts[3].strip())
                except ValueError:
                    logger.warning(f"自定义 provider {name} temperature 格式错误，忽略: {parts[3]}")
            if len(parts) >= 5 and parts[4].strip():
                try:
                    entry["extra_body"] = json.loads(parts[4].strip())
                except json.JSONDecodeError:
                    logger.warning(f"自定义 provider {name} extra_json 格式错误，忽略: {parts[4]}")
            results.append(entry)
        return results


class FeishuConfig(BaseSettings):
    model_config = _ENV_CONFIG
    app_id: Optional[str] = Field(None, alias="FEISHU_APP_ID")
    app_secret: Optional[str] = Field(None, alias="FEISHU_APP_SECRET")
    verification_token: Optional[str] = Field(None, alias="FEISHU_VERIFICATION_TOKEN")
    encrypt_key: Optional[str] = Field(None, alias="FEISHU_ENCRYPT_KEY")
    allowed_users: str = Field("", alias="FEISHU_ALLOWED_USERS")
    bot_open_id: str = Field("", alias="FEISHU_BOT_OPEN_ID")
    connection_mode: str = Field("webhook", alias="FEISHU_MODE")


class DingTalkConfig(BaseSettings):
    model_config = _ENV_CONFIG
    app_key: Optional[str] = Field(None, alias="DINGTALK_APP_KEY")
    app_secret: Optional[str] = Field(None, alias="DINGTALK_APP_SECRET")


class EmailConfig(BaseSettings):
    model_config = _ENV_CONFIG
    imap_host: str = Field("", alias="EMAIL_IMAP_HOST")
    imap_port: int = Field(993, alias="EMAIL_IMAP_PORT")
    smtp_host: str = Field("", alias="EMAIL_SMTP_HOST")
    smtp_port: int = Field(465, alias="EMAIL_SMTP_PORT")
    username: str = Field("", alias="EMAIL_USERNAME")
    password: str = Field("", alias="EMAIL_PASSWORD")
    # P2: 邮件监控
    monitor_enabled: bool = Field(False, alias="EMAIL_MONITOR_ENABLED")
    monitor_interval: int = Field(300, alias="EMAIL_MONITOR_INTERVAL")  # 秒
    important_senders: str = Field("", alias="EMAIL_IMPORTANT_SENDERS")  # 逗号分隔
    important_keywords: str = Field("", alias="EMAIL_IMPORTANT_KEYWORDS")  # 逗号分隔


class SchedulerConfig(BaseSettings):
    """P2: 定时任务配置"""
    model_config = _ENV_CONFIG
    enabled: bool = Field(False, alias="SCHEDULER_ENABLED")
    daily_summary_cron: str = Field("0 8 * * *", alias="DAILY_SUMMARY_CRON")  # 每天8点
    daily_summary_enabled: bool = Field(False, alias="DAILY_SUMMARY_ENABLED")


class WebhookConfig(BaseSettings):
    """P2: 外部 Webhook 配置"""
    model_config = _ENV_CONFIG
    enabled: bool = Field(False, alias="WEBHOOK_ENABLED")
    github_secret: str = Field("", alias="GITHUB_WEBHOOK_SECRET")
    sentry_secret: str = Field("", alias="SENTRY_WEBHOOK_SECRET")


class AgentConfig(BaseSettings):
    """Agent 多层超时配置"""
    model_config = _ENV_CONFIG

    # 总任务超时（秒），整个 Agent 循环的硬限制
    task_timeout: int = Field(600, alias="AGENT_TASK_TIMEOUT")
    # LLM 单次响应超时（秒），防止 API 卡住
    llm_timeout: int = Field(120, alias="AGENT_LLM_TIMEOUT")
    # 默认工具执行超时（秒）
    tool_timeout: int = Field(120, alias="AGENT_TOOL_TIMEOUT")
    # 单段最大循环轮次
    max_loops: int = Field(25, alias="AGENT_MAX_LOOPS")
    # 长任务自动续航次数（0=禁用，每次续航自动压缩上下文并继续，总步数=max_loops*(1+auto_continue)）
    auto_continue: int = Field(2, alias="AGENT_AUTO_CONTINUE")
    # LLM 单次最大输出 token 数（0=不限制，按模型自身上限输出；设正整数则显式限制）
    max_output_tokens: int = Field(0, alias="AGENT_MAX_OUTPUT_TOKENS")
    # 工具返回结果最大字符数（0=不限制；默认 15000，防止上下文膨胀）
    max_tool_result_chars: int = Field(15000, alias="AGENT_MAX_TOOL_RESULT_CHARS")
    # 上下文窗口：最大消息条数和 token 数
    max_context_messages: int = Field(80, alias="AGENT_MAX_CONTEXT_MESSAGES")
    max_context_tokens: int = Field(64000, alias="AGENT_MAX_CONTEXT_TOKENS")
    # 长耗时工具的超时覆盖（逗号分隔 name:seconds）
    # 例如: "browser_open:180,run_python:300,sandbox_python:300"
    tool_timeout_overrides: str = Field("", alias="AGENT_TOOL_TIMEOUT_OVERRIDES")

    def get_tool_timeout(self, tool_name: str) -> int:
        """获取指定工具的超时时间"""
        if self.tool_timeout_overrides:
            for item in self.tool_timeout_overrides.split(","):
                item = item.strip()
                if ":" in item:
                    name, seconds = item.split(":", 1)
                    if name.strip() == tool_name:
                        try:
                            return int(seconds.strip())
                        except ValueError:
                            pass
        return self.tool_timeout


class BrowserConfig(BaseSettings):
    """浏览器自动化配置"""
    model_config = _ENV_CONFIG

    # 启动模式: "auto"=优先 CDP 找不到回退内置, "cdp"=强制 CDP, "builtin"=强制内置 Chromium
    mode: str = Field("auto", alias="BROWSER_MODE")
    # CDP 调试端口
    cdp_port: int = Field(9222, alias="BROWSER_CDP_PORT")
    # 手动指定浏览器路径（留空自动检测）
    executable_path: str = Field("", alias="BROWSER_EXECUTABLE_PATH")
    # 无头模式
    headless: bool = Field(True, alias="BROWSER_HEADLESS")
    # 视口大小
    viewport: str = Field("1280,720", alias="BROWSER_VIEWPORT")
    # 下载目录
    downloads_dir: str = Field("./downloads", alias="BROWSER_DOWNLOADS_DIR")
    # Set-of-Mark 视觉标注（截图上显示元素编号，需 LLM 支持 vision）
    som_enabled: bool = Field(True, alias="BROWSER_SOM_ENABLED")
    # 多策略自愈定位（元素定位失败时逐级降级重试）
    selfheal_enabled: bool = Field(True, alias="BROWSER_SELFHEAL_ENABLED")


class SecurityConfig(BaseSettings):
    model_config = _ENV_CONFIG
    require_confirmation: bool = Field(True, alias="REQUIRE_CONFIRMATION")
    max_tool_loops: int = Field(25, alias="MAX_TOOL_LOOPS")
    allowed_paths: str = Field("", alias="ALLOWED_PATHS")

    def get_allowed_paths(self) -> list[str]:
        """获取允许的路径列表，并转换为绝对路径"""
        import json
        import os
        
        if not self.allowed_paths:
            return []
        
        raw = self.allowed_paths.strip()
        paths = []
        
        # 尝试解析 JSON 数组格式: ["./workspaces", "/tmp"]
        if raw.startswith("["):
            try:
                paths = json.loads(raw)
            except json.JSONDecodeError:
                # JSON 解析失败，当作普通字符串处理
                paths = [raw]
        else:
            # 逗号分隔格式: ./workspaces, /tmp
            paths = [p.strip() for p in raw.split(",") if p.strip()]
        
        # 转换为绝对路径
        result = []
        for p in paths:
            if not os.path.isabs(p):
                abs_path = os.path.abspath(p)
            else:
                abs_path = p
            result.append(abs_path)
        
        return result


class MCPConfig(BaseSettings):
    """MCP (Model Context Protocol) 配置"""
    model_config = _ENV_CONFIG
    servers: str = Field("", alias="MCP_SERVERS")


class SessionConfig(BaseSettings):
    """会话管理配置"""
    model_config = _ENV_CONFIG
    idle_timeout_minutes: int = Field(480, alias="SESSION_IDLE_TIMEOUT")
    daily_reset_hour: int = Field(4, alias="SESSION_DAILY_RESET_HOUR")


class Config(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    llm: LLMConfig = Field(default_factory=LLMConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)

    # P2: 定时任务 & Webhook
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)

    # P3: 浏览器自动化
    browser: BrowserConfig = Field(default_factory=BrowserConfig)

    # 会话管理
    session: SessionConfig = Field(default_factory=SessionConfig)

    # MCP 协议
    mcp: MCPConfig = Field(default_factory=MCPConfig)

    memory_dir: Path = Field(Path("./memory"), alias="MEMORY_DIR")
    workspace_dir: Path = Field(Path("./workspaces"), alias="WORKSPACE_DIR")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    # P1: 多通道并行
    channels: str = Field("cli", alias="CHANNELS")  # 逗号分隔, 如 "cli,feishu,dingtalk"

    # 技能移植器
    github_token: str = Field("", alias="GITHUB_TOKEN")  # 可选，提高 API 限额

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 强制转绝对路径，避免工作目录变化导致数据丢失
        self.memory_dir = self.memory_dir.resolve()
        self.workspace_dir = self.workspace_dir.resolve()
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def get_channel_list(self) -> list[str]:
        return [c.strip() for c in self.channels.split(",") if c.strip()]


_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        from dotenv import load_dotenv
        load_dotenv(override=False)

        try:
            from .skills.credential_skills import load_credentials_to_env
            load_credentials_to_env()
        except Exception:
            pass

        _config = Config()
    return _config
