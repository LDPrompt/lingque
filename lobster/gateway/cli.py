"""
🐦 CLI 通道 - 命令行交互，用于本地测试
"""

import asyncio
from rich.console import Console
from rich.markdown import Markdown
from rich.prompt import Confirm
from .base import BaseChannel
from ..agent.core import Agent

console = Console()


class CLIChannel(BaseChannel):
    """命令行交互通道"""

    def __init__(self, agent: Agent):
        super().__init__(agent)
        # 注入确认回调
        self.agent.set_confirm_callback(self.ask_confirmation)

    async def start(self):
        console.print("\n[bold cyan]🐦 灵雀 LingQue[/bold cyan] - 你的私人 AI 助手")
        console.print("[dim]输入消息开始对话, /status 查看状态, /clear 清空会话, /reload 热重载配置, /quit 退出[/dim]\n")

        while True:
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: console.input("[bold green]你> [/bold green]")
                )
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]再见! 🐦[/dim]")
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            # 内置命令
            if user_input == "/quit":
                console.print("[dim]再见! 🐦[/dim]")
                break
            elif user_input == "/status":
                console.print(self.agent.get_status())
                continue
            elif user_input == "/clear":
                self.agent.memory.clear_session()
                console.print("[dim]会话已清空[/dim]")
                continue
            elif user_input == "/reload":
                await self._handle_reload()
                continue

            # 处理消息
            console.print("[dim]思考中...[/dim]")
            try:
                response = await self.agent.process_message(user_input)
                console.print()
                console.print(Markdown(response))
                console.print()
            except Exception as e:
                console.print(f"[bold red]错误: {e}[/bold red]")

    async def _handle_reload(self):
        """热重载 .env 配置"""
        from dotenv import load_dotenv
        from ..config import Config
        from ..skills.file_ops import set_allowed_paths
        import logging

        try:
            load_dotenv(override=True)
            new_config = Config()

            # LLM
            self.agent.llm.config = new_config
            self.agent.llm.providers.clear()
            self.agent.llm._custom_providers.clear()
            self.agent.llm.primary = new_config.llm.provider
            self.agent.llm._init_providers()

            # Agent
            self.agent.llm._llm_timeout = new_config.agent.llm_timeout
            self.agent._task_timeout = new_config.agent.task_timeout
            self.agent._tool_timeout = new_config.agent.tool_timeout
            self.agent.max_loops = new_config.agent.max_loops
            self.agent._auto_continue = new_config.agent.auto_continue
            self.agent.require_confirmation = new_config.security.require_confirmation

            # 安全路径
            allowed = new_config.security.get_allowed_paths()
            set_allowed_paths(allowed or [str(new_config.workspace_dir.resolve())])

            # 日志
            log_level = getattr(logging, new_config.log_level.upper(), logging.INFO)
            logging.getLogger().setLevel(log_level)

            providers = list(self.agent.llm.providers.keys())
            console.print(f"[bold green]✅ 配置已重载[/bold green]")
            console.print(f"[dim]主模型: {new_config.llm.provider}, 可用: {', '.join(providers)}[/dim]")
        except Exception as e:
            console.print(f"[bold red]重载失败: {e}[/bold red]")

    async def send_message(self, content: str, **kwargs):
        console.print(Markdown(content))

    async def ask_confirmation(self, description: str) -> bool:
        console.print(f"\n[bold yellow]⚠️  需要确认[/bold yellow]")
        console.print(description)
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: Confirm.ask("是否执行?")
        )
        return result
