"""
🐦 灵雀 - MCP stdio 客户端 & 包管理器

功能:
- stdio 传输（启动子进程，通过 stdin/stdout JSON-RPC 通信）
- initialize → tools/list → tools/call 核心方法
- 自动将 MCP tools 注册为灵雀技能（category="mcp"）
- MCP 包管理器：服务注册表、一键安装/卸载、状态监控
"""

import asyncio
import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..skills.registry import registry as skill_registry, SkillResult

logger = logging.getLogger("lingque.mcp")

_MCP_PROTOCOL_VERSION = "2024-11-05"


# ==================== MCP 服务注册表 ====================

@dataclass
class MCPServerInfo:
    """MCP 服务信息"""
    id: str                    # 服务 ID
    name: str                  # 显示名称
    description: str           # 服务描述
    command: str               # 启动命令
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    category: str = "general"  # 分类: general, web, data, ai, etc.
    source: str = ""           # 来源 (npm, pip, github, builtin)
    install_cmd: str = ""      # 安装命令
    homepage: str = ""         # 主页链接
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: dict) -> "MCPServerInfo":
        return cls(**{k: v for k, v in d.items() if k in cls.__annotations__})


# 内置 MCP 服务注册表
_BUILTIN_MCP_REGISTRY: list[MCPServerInfo] = [
    MCPServerInfo(
        id="filesystem",
        name="Filesystem",
        description="文件系统操作：读取、写入、搜索文件",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        category="general",
        source="npm",
        install_cmd="npm install -g @modelcontextprotocol/server-filesystem",
    ),
    MCPServerInfo(
        id="brave-search",
        name="Brave Search",
        description="Brave 搜索引擎 API",
        command="npx",
        args=["-y", "@anthropic-ai/mcp-server-brave-search"],
        category="web",
        source="npm",
        install_cmd="npm install -g @anthropic-ai/mcp-server-brave-search",
        env={"BRAVE_API_KEY": ""},
    ),
    MCPServerInfo(
        id="puppeteer",
        name="Puppeteer",
        description="浏览器自动化（Chrome）",
        command="npx",
        args=["-y", "@anthropic-ai/mcp-server-puppeteer"],
        category="web",
        source="npm",
        install_cmd="npm install -g @anthropic-ai/mcp-server-puppeteer",
    ),
    MCPServerInfo(
        id="github",
        name="GitHub",
        description="GitHub API 操作",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        category="general",
        source="npm",
        install_cmd="npm install -g @modelcontextprotocol/server-github",
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": ""},
    ),
    MCPServerInfo(
        id="sqlite",
        name="SQLite",
        description="SQLite 数据库操作",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-sqlite", "memory"],
        category="data",
        source="npm",
        install_cmd="npm install -g @modelcontextprotocol/server-sqlite",
    ),
    MCPServerInfo(
        id="postgres",
        name="PostgreSQL",
        description="PostgreSQL 数据库操作",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-postgres"],
        category="data",
        source="npm",
        install_cmd="npm install -g @modelcontextprotocol/server-postgres",
        env={"POSTGRES_CONNECTION_STRING": ""},
    ),
    MCPServerInfo(
        id="fetch",
        name="Fetch",
        description="HTTP 请求（GET/POST）",
        command="uvx",
        args=["mcp-server-fetch"],
        category="web",
        source="pip",
        install_cmd="pip install mcp-server-fetch",
    ),
    MCPServerInfo(
        id="time",
        name="Time",
        description="时间和时区操作",
        command="uvx",
        args=["mcp-server-time"],
        category="general",
        source="pip",
        install_cmd="pip install mcp-server-time",
    ),
    MCPServerInfo(
        id="bb-browser",
        name="bb-browser",
        description="用你的真实浏览器操作 36 个平台（知乎、微博、B站、推特、GitHub 等），天然携带登录态",
        command="npx",
        args=["-y", "bb-browser", "--mcp"],
        category="web",
        source="npm",
        install_cmd="npm install -g bb-browser",
        homepage="https://github.com/epiral/bb-browser",
    ),
]


class MCPClient:
    """
    单个 MCP 服务器的 stdio 客户端 v2.0
    
    升级特性：
    - 健康检查：定期检测服务状态
    - 自动重连：服务断开后自动重连
    - 连接状态追踪
    """

    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None,
                 auto_reconnect: bool = True,
                 health_check_interval: int = 60):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env
        self.auto_reconnect = auto_reconnect
        self.health_check_interval = health_check_interval
        
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._read_task: asyncio.Task | None = None
        self._health_task: asyncio.Task | None = None
        self._server_info: dict = {}
        self._tools: list[dict] = []
        
        # 连接状态追踪
        self._connected = False
        self._last_health_check: Optional[datetime] = None
        self._consecutive_failures = 0
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5

    @property
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._connected and self._process is not None

    async def start(self) -> bool:
        """启动 MCP 服务器子进程并完成握手"""
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.command, *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.env,
            )
            self._read_task = asyncio.create_task(self._read_loop())

            init_result = await self._request("initialize", {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "lingque", "version": "2.0.0"},
            })
            self._server_info = init_result or {}
            await self._notify("notifications/initialized", {})

            tools_result = await self._request("tools/list", {})
            self._tools = (tools_result or {}).get("tools", [])
            
            self._connected = True
            self._consecutive_failures = 0
            self._reconnect_attempts = 0
            self._last_health_check = datetime.now()
            
            # 启动健康检查任务
            if self.health_check_interval > 0:
                self._health_task = asyncio.create_task(self._health_check_loop())
            
            logger.info(f"MCP [{self.name}] 已连接, 发现 {len(self._tools)} 个工具")
            return True
        except Exception as e:
            logger.error(f"MCP [{self.name}] 启动失败: {e}")
            self._connected = False
            await self.stop()
            return False

    async def stop(self):
        """停止子进程"""
        self._connected = False
        
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        
        if self._process:
            try:
                self._process.stdin.close()
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
    
    async def _health_check_loop(self):
        """健康检查循环"""
        while self._connected:
            try:
                await asyncio.sleep(self.health_check_interval)
                
                if not self._connected:
                    break
                
                # 检查进程是否存活
                if self._process is None or self._process.returncode is not None:
                    logger.warning(f"MCP [{self.name}] 进程已退出，尝试重连...")
                    self._connected = False
                    if self.auto_reconnect:
                        await self._try_reconnect()
                    continue
                
                # 发送 ping 请求
                healthy = await self._ping()
                self._last_health_check = datetime.now()
                
                if healthy:
                    self._consecutive_failures = 0
                else:
                    self._consecutive_failures += 1
                    logger.warning(f"MCP [{self.name}] 健康检查失败 ({self._consecutive_failures})")
                    
                    if self._consecutive_failures >= 3:
                        logger.error(f"MCP [{self.name}] 连续 3 次健康检查失败，断开连接")
                        self._connected = False
                        if self.auto_reconnect:
                            await self._try_reconnect()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"MCP [{self.name}] 健康检查异常: {e}")
    
    async def _ping(self) -> bool:
        """发送 ping 请求检测服务存活"""
        try:
            # 尝试获取工具列表作为 ping
            result = await asyncio.wait_for(
                self._request("tools/list", {}),
                timeout=10
            )
            return result is not None
        except Exception:
            return False
    
    async def _try_reconnect(self) -> bool:
        """尝试重连"""
        if self._reconnect_attempts >= self._max_reconnect_attempts:
            logger.error(f"MCP [{self.name}] 已达最大重连次数 ({self._max_reconnect_attempts})，放弃重连")
            return False
        
        self._reconnect_attempts += 1
        logger.info(f"MCP [{self.name}] 尝试重连 (第 {self._reconnect_attempts} 次)...")
        
        # 先停止
        await self.stop()
        
        # 等待一段时间再重连（指数退避）
        wait_time = min(30, 2 ** self._reconnect_attempts)
        await asyncio.sleep(wait_time)
        
        # 重新启动
        success = await self.start()
        if success:
            logger.info(f"MCP [{self.name}] 重连成功")
            # 重新注册工具
            self.register_tools()
        else:
            logger.error(f"MCP [{self.name}] 重连失败")
        
        return success

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """调用 MCP 工具（带自动重连）"""
        # 检查连接状态
        if not self.is_connected:
            if self.auto_reconnect:
                logger.warning(f"MCP [{self.name}] 未连接，尝试重连...")
                if not await self._try_reconnect():
                    return f"MCP 服务 {self.name} 未连接且重连失败"
            else:
                return f"MCP 服务 {self.name} 未连接"
        
        try:
            result = await self._request("tools/call", {
                "name": tool_name,
                "arguments": arguments,
            })
            if not result:
                return "工具无返回"
            content_parts = result.get("content", [])
            texts = []
            for part in content_parts:
                if part.get("type") == "text":
                    texts.append(part.get("text", ""))
                elif part.get("type") == "image":
                    texts.append(f"[图片: {part.get('mimeType', 'image')}]")
                else:
                    texts.append(json.dumps(part, ensure_ascii=False)[:500])
            return "\n".join(texts) or "工具执行完成（无文本输出）"
        except Exception as e:
            # 工具调用失败，可能是连接问题
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3 and self.auto_reconnect:
                self._connected = False
                task = asyncio.create_task(self._try_reconnect())
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() and t.exception() else None)
            return f"MCP 工具调用失败: {e}"
    
    def get_health_status(self) -> dict:
        """获取健康状态"""
        return {
            "connected": self._connected,
            "process_alive": self._process is not None and self._process.returncode is None,
            "last_health_check": self._last_health_check.isoformat() if self._last_health_check else None,
            "consecutive_failures": self._consecutive_failures,
            "reconnect_attempts": self._reconnect_attempts,
            "tools_count": len(self._tools),
        }

    def register_tools(self):
        """将发现的 MCP tools 注册为灵雀技能"""
        registered = 0
        for tool in self._tools:
            tool_name = tool.get("name", "")
            if not tool_name:
                continue
            skill_name = f"mcp_{self.name}_{tool_name}"
            description = tool.get("description", f"MCP 工具: {tool_name}")
            input_schema = tool.get("inputSchema", {
                "type": "object", "properties": {},
            })

            self._register_single_tool(skill_name, description, input_schema, tool_name)
            registered += 1

        logger.info(f"MCP [{self.name}] 注册了 {registered} 个技能")
        return registered

    def _register_single_tool(self, skill_name: str, description: str,
                              input_schema: dict, mcp_tool_name: str):
        """注册单个 MCP 工具为灵雀技能"""
        client = self

        @skill_registry.register(
            name=skill_name,
            description=description[:200],
            parameters=input_schema,
            risk_level="medium",
            category="mcp",
        )
        async def _mcp_handler(**kwargs) -> SkillResult:
            result_text = await client.call_tool(mcp_tool_name, kwargs)
            is_error = "失败" in result_text or "error" in result_text.lower()
            if is_error:
                return SkillResult(success=False, error=result_text)
            return SkillResult(success=True, data=result_text)

    async def _request(self, method: str, params: dict, timeout: float = 30) -> dict | None:
        """发送 JSON-RPC 请求并等待响应"""
        self._request_id += 1
        req_id = self._request_id
        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        line = json.dumps(message, ensure_ascii=False) + "\n"
        try:
            self._process.stdin.write(line.encode("utf-8"))
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            self._connected = False
            self._pending.pop(req_id, None)
            raise ConnectionError(f"MCP [{self.name}] 进程已退出: {e}")

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"MCP 请求超时: {method}")

    async def _notify(self, method: str, params: dict):
        """发送不需要响应的通知"""
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        line = json.dumps(message, ensure_ascii=False) + "\n"
        self._process.stdin.write(line.encode("utf-8"))
        await self._process.stdin.drain()

    async def _read_loop(self):
        """持续读取子进程 stdout 并分发响应"""
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug(f"MCP [{self.name}] 非 JSON 输出: {line[:200]}")
                    continue

                req_id = msg.get("id")
                if req_id is not None and req_id in self._pending:
                    future = self._pending.pop(req_id)
                    if "error" in msg:
                        err = msg["error"]
                        future.set_exception(
                            Exception(f"MCP 错误 [{err.get('code')}]: {err.get('message')}")
                        )
                    else:
                        future.set_result(msg.get("result"))
                elif "method" in msg:
                    logger.debug(f"MCP [{self.name}] 收到通知: {msg['method']}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"MCP [{self.name}] 读取循环异常: {e}")


class MCPManager:
    """
    MCP 服务管理器
    
    功能:
    - 管理多个 MCP 服务器连接
    - 服务注册表（内置 + 自定义）
    - 一键安装/卸载
    - 状态监控
    """

    def __init__(self, config_dir: Optional[Path] = None):
        self._clients: dict[str, MCPClient] = {}
        self._config_dir = config_dir or Path.home() / ".lingque" / "mcp"
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._installed_file = self._config_dir / "installed.json"
        self._custom_registry_file = self._config_dir / "custom_servers.json"
        
        # 加载已安装的服务
        self._installed: dict[str, dict] = self._load_installed()
        # 加载自定义注册表
        self._custom_registry: list[MCPServerInfo] = self._load_custom_registry()
    
    def _load_installed(self) -> dict[str, dict]:
        """加载已安装的服务列表"""
        if self._installed_file.exists():
            try:
                return json.loads(self._installed_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}
    
    def _save_installed(self):
        """保存已安装的服务列表"""
        self._installed_file.write_text(
            json.dumps(self._installed, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    
    def _load_custom_registry(self) -> list[MCPServerInfo]:
        """加载自定义服务注册表"""
        if self._custom_registry_file.exists():
            try:
                data = json.loads(self._custom_registry_file.read_text(encoding="utf-8"))
                return [MCPServerInfo.from_dict(d) for d in data]
            except Exception:
                pass
        return []
    
    def _save_custom_registry(self):
        """保存自定义服务注册表"""
        self._custom_registry_file.write_text(
            json.dumps([s.to_dict() for s in self._custom_registry], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    async def connect_from_config(self, config_str: str) -> int:
        """
        从配置字符串连接 MCP 服务器

        格式: "server1=/path/to/cmd arg1 arg2,server2=/path/to/cmd2"
        兼容旧格式: "server1:/path/to/cmd" (Unix) / "server1:C:\\path" (Windows)
        """
        if not config_str or not config_str.strip():
            return 0

        total_tools = 0
        for entry in config_str.split(","):
            entry = entry.strip()
            if not entry:
                continue

            # 优先用 = 分隔（新格式，无歧义）
            if "=" in entry:
                name, cmd_str = entry.split("=", 1)
            elif ":" in entry:
                # 兼容旧 : 格式，但需处理 Windows 盘符 (如 name:C:\path)
                first_colon = entry.index(":")
                # 如果冒号后紧跟 \ 或 /，且冒号前只有一个字母 → Windows 盘符，不是分隔符
                after = entry[first_colon + 1:first_colon + 2] if first_colon + 1 < len(entry) else ""
                if first_colon >= 2 or after not in ("\\", "/"):
                    name, cmd_str = entry.split(":", 1)
                else:
                    # 整个 entry 看起来是 "X:..." 即 Windows 路径，跳过
                    logger.warning(f"MCP 配置项格式无法解析（建议用 = 分隔）: {entry}")
                    continue
            else:
                continue

            name = name.strip()
            cmd_str = cmd_str.strip()
            parts = cmd_str.split()
            if not parts:
                continue

            command = parts[0]
            args = parts[1:] if len(parts) > 1 else []

            client = MCPClient(name=name, command=command, args=args)
            if await client.start():
                self._clients[name] = client
                total_tools += client.register_tools()
            else:
                logger.warning(f"MCP 服务器 [{name}] 连接失败，跳过")

        return total_tools
    
    async def connect_server(self, server_id: str, env_overrides: dict[str, str] = None) -> bool:
        """
        连接一个注册表中的 MCP 服务器
        
        Args:
            server_id: 服务 ID (如 "filesystem", "github")
            env_overrides: 环境变量覆盖
        """
        # 查找服务信息
        server_info = self.get_server_info(server_id)
        if not server_info:
            logger.error(f"MCP 服务不存在: {server_id}")
            return False
        
        # 合并环境变量
        env = dict(server_info.env)
        if env_overrides:
            env.update(env_overrides)
        
        # 创建客户端
        client = MCPClient(
            name=server_id,
            command=server_info.command,
            args=server_info.args,
            env=env if env else None
        )
        
        if await client.start():
            self._clients[server_id] = client
            tools_count = client.register_tools()
            
            # 记录为已安装
            self._installed[server_id] = {
                "name": server_info.name,
                "installed_at": datetime.now().isoformat(),
                "tools_count": tools_count,
            }
            self._save_installed()
            
            logger.info(f"MCP [{server_id}] 已连接，注册了 {tools_count} 个工具")
            return True
        
        return False
    
    async def disconnect_server(self, server_id: str) -> bool:
        """断开一个 MCP 服务器"""
        client = self._clients.get(server_id)
        if not client:
            return False
        
        await client.stop()
        del self._clients[server_id]
        
        if server_id in self._installed:
            del self._installed[server_id]
            self._save_installed()
        
        logger.info(f"MCP [{server_id}] 已断开")
        return True

    async def stop_all(self):
        """停止所有 MCP 服务器"""
        for name, client in list(self._clients.items()):
            logger.info(f"停止 MCP 服务器: {name}")
            await client.stop()
        self._clients.clear()
    
    # ==================== 包管理功能 ====================
    
    def get_server_info(self, server_id: str) -> Optional[MCPServerInfo]:
        """获取服务信息"""
        # 先查内置注册表
        for s in _BUILTIN_MCP_REGISTRY:
            if s.id == server_id:
                return s
        # 再查自定义注册表
        for s in self._custom_registry:
            if s.id == server_id:
                return s
        return None
    
    def list_available_servers(self, category: str = None) -> list[MCPServerInfo]:
        """列出所有可用的 MCP 服务"""
        all_servers = _BUILTIN_MCP_REGISTRY + self._custom_registry
        if category:
            all_servers = [s for s in all_servers if s.category == category]
        return all_servers
    
    def list_connected_servers(self) -> list[dict]:
        """列出已连接的服务及状态（包括健康信息）"""
        result = []
        for name, client in self._clients.items():
            result.append({
                "id": name,
                "name": client.name,
                "tools_count": len(client._tools),
                "server_info": client._server_info,
                "connected": client.is_connected,
                "health": client.get_health_status(),
            })
        return result
    
    def add_custom_server(self, server_info: MCPServerInfo) -> bool:
        """添加自定义 MCP 服务"""
        # 检查是否已存在
        for i, s in enumerate(self._custom_registry):
            if s.id == server_info.id:
                self._custom_registry[i] = server_info
                self._save_custom_registry()
                logger.info(f"更新自定义 MCP 服务: {server_info.id}")
                return True
        
        self._custom_registry.append(server_info)
        self._save_custom_registry()
        logger.info(f"添加自定义 MCP 服务: {server_info.id}")
        return True
    
    def remove_custom_server(self, server_id: str) -> bool:
        """移除自定义 MCP 服务"""
        for i, s in enumerate(self._custom_registry):
            if s.id == server_id:
                del self._custom_registry[i]
                self._save_custom_registry()
                logger.info(f"移除自定义 MCP 服务: {server_id}")
                return True
        return False
    
    def get_status_summary(self) -> str:
        """获取 MCP 状态摘要"""
        if not self._clients:
            return "🔌 没有已连接的 MCP 服务"
        
        lines = [f"🔌 **MCP 服务** ({len(self._clients)} 个连接)\n"]
        for name, client in self._clients.items():
            status = "🟢" if client._process else "🔴"
            lines.append(f"{status} **{name}** - {len(client._tools)} 个工具")
        
        return "\n".join(lines)

    @property
    def connected_servers(self) -> list[str]:
        return list(self._clients.keys())


# 全局实例
_mcp_manager: Optional[MCPManager] = None


def get_mcp_manager() -> Optional[MCPManager]:
    """获取 MCP 管理器实例"""
    return _mcp_manager


def init_mcp_manager(config_dir: Path = None) -> MCPManager:
    """初始化 MCP 管理器"""
    global _mcp_manager
    _mcp_manager = MCPManager(config_dir)
    return _mcp_manager
