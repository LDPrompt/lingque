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
        console.print("[dim]输入消息开始对话, /status 查看状态, /clear 清空会话, /quit 退出[/dim]\n")

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

            # 处理消息
            console.print("[dim]思考中...[/dim]")
            try:
                response = await self.agent.process_message(user_input)
                console.print()
                console.print(Markdown(response))
                console.print()
            except Exception as e:
                console.print(f"[bold red]错误: {e}[/bold red]")

    async def send_message(self, content: str, **kwargs):
        console.print(Markdown(content))

    async def ask_confirmation(self, description: str) -> bool:
        console.print(f"\n[bold yellow]⚠️  需要确认[/bold yellow]")
        console.print(description)
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: Confirm.ask("是否执行?")
        )
        return result
