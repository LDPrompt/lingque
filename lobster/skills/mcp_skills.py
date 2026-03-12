"""
🐦 灵雀 - MCP 包管理技能

提供 MCP 服务的用户交互接口：
- 列出可用 MCP 服务
- 连接/断开 MCP 服务
- 查看 MCP 状态
- 添加自定义 MCP 服务
"""

import logging
from .registry import register, SkillResult

logger = logging.getLogger("lingque.skills.mcp")


def _get_mcp_manager():
    """延迟导入，避免循环依赖"""
    from ..mcp.client import get_mcp_manager
    return get_mcp_manager()


@register(
    name="list_mcp_services",
    description=(
        "列出所有可用的 MCP (Model Context Protocol) 服务。\n"
        "MCP 是一种让 AI 调用外部工具的标准协议。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "按分类筛选: general/web/data/ai (可选)",
                "enum": ["general", "web", "data", "ai", "all"],
            },
            "show_connected_only": {
                "type": "boolean",
                "description": "是否只显示已连接的服务",
                "default": False,
            },
        },
        "required": [],
    },
    risk_level="low",
)
async def list_mcp_services(
    category: str = "all",
    show_connected_only: bool = False
) -> SkillResult:
    """列出可用的 MCP 服务"""
    manager = _get_mcp_manager()
    if not manager:
        return SkillResult(success=False, error="MCP 管理器未初始化")
    
    if show_connected_only:
        connected = manager.list_connected_servers()
        if not connected:
            return SkillResult(success=True, data="🔌 没有已连接的 MCP 服务")
        
        lines = [f"🔌 **已连接的 MCP 服务** ({len(connected)} 个)\n"]
        for s in connected:
            status = "🟢" if s.get("connected") else "🔴"
            lines.append(f"{status} **{s['id']}** - {s.get('tools_count', 0)} 个工具")
        
        return SkillResult(success=True, data="\n".join(lines))
    
    # 列出所有可用服务
    cat = None if category == "all" else category
    servers = manager.list_available_servers(cat)
    
    if not servers:
        return SkillResult(success=True, data="没有可用的 MCP 服务")
    
    connected_ids = set(manager.connected_servers)
    
    lines = [f"🔌 **可用 MCP 服务** ({len(servers)} 个)\n"]
    
    # 按分类分组
    by_category = {}
    for s in servers:
        if s.category not in by_category:
            by_category[s.category] = []
        by_category[s.category].append(s)
    
    category_names = {
        "general": "📦 通用",
        "web": "🌐 网络",
        "data": "💾 数据",
        "ai": "🤖 AI",
    }
    
    for cat, cat_servers in sorted(by_category.items()):
        lines.append(f"\n{category_names.get(cat, cat)}")
        for s in cat_servers:
            status = "✅" if s.id in connected_ids else "⬜"
            lines.append(f"  {status} **{s.id}**: {s.name}")
            lines.append(f"      {s.description[:50]}...")
    
    lines.append("\n使用 `connect_mcp_service('服务ID')` 连接服务")
    
    return SkillResult(success=True, data="\n".join(lines))


@register(
    name="connect_mcp_service",
    description="连接一个 MCP 服务，使其工具可用",
    parameters={
        "type": "object",
        "properties": {
            "server_id": {
                "type": "string",
                "description": "MCP 服务 ID，如 'filesystem', 'github'"
            },
            "env_vars": {
                "type": "object",
                "description": "环境变量（API Key 等），如 {'GITHUB_PERSONAL_ACCESS_TOKEN': 'xxx'}",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["server_id"],
    },
    risk_level="medium",
)
async def connect_mcp_service(
    server_id: str,
    env_vars: dict = None
) -> SkillResult:
    """连接 MCP 服务"""
    manager = _get_mcp_manager()
    if not manager:
        return SkillResult(success=False, error="MCP 管理器未初始化")
    
    # 检查是否已连接
    if server_id in manager.connected_servers:
        return SkillResult(
            success=False,
            error=f"MCP 服务 '{server_id}' 已连接"
        )
    
    # 检查服务是否存在
    server_info = manager.get_server_info(server_id)
    if not server_info:
        return SkillResult(
            success=False,
            error=f"未知的 MCP 服务: {server_id}。使用 list_mcp_services() 查看可用服务。"
        )
    
    # 检查是否需要 API Key
    required_env = [k for k, v in server_info.env.items() if not v]
    if required_env and not env_vars:
        return SkillResult(
            success=False,
            error=f"此服务需要配置环境变量: {', '.join(required_env)}"
        )
    
    # 连接服务
    success = await manager.connect_server(server_id, env_vars)
    
    if success:
        connected = manager.list_connected_servers()
        tools_count = 0
        for s in connected:
            if s["id"] == server_id:
                tools_count = s.get("tools_count", 0)
                break
        
        return SkillResult(
            success=True,
            data=(
                f"✅ MCP 服务 **{server_info.name}** 已连接！\n\n"
                f"📦 注册了 {tools_count} 个工具\n"
                f"工具名称格式: `mcp_{server_id}_<工具名>`"
            )
        )
    else:
        return SkillResult(
            success=False,
            error=f"连接 MCP 服务失败: {server_id}"
        )


@register(
    name="disconnect_mcp_service",
    description="断开一个已连接的 MCP 服务",
    parameters={
        "type": "object",
        "properties": {
            "server_id": {
                "type": "string",
                "description": "要断开的 MCP 服务 ID"
            },
        },
        "required": ["server_id"],
    },
    risk_level="low",
)
async def disconnect_mcp_service(server_id: str) -> SkillResult:
    """断开 MCP 服务"""
    manager = _get_mcp_manager()
    if not manager:
        return SkillResult(success=False, error="MCP 管理器未初始化")
    
    if server_id not in manager.connected_servers:
        return SkillResult(
            success=False,
            error=f"MCP 服务 '{server_id}' 未连接"
        )
    
    success = await manager.disconnect_server(server_id)
    
    if success:
        return SkillResult(
            success=True,
            data=f"✅ MCP 服务 **{server_id}** 已断开"
        )
    else:
        return SkillResult(success=False, error="断开失败")


@register(
    name="add_custom_mcp_service",
    description=(
        "添加自定义 MCP 服务到注册表。\n"
        "添加后可以使用 connect_mcp_service 连接。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "server_id": {
                "type": "string",
                "description": "服务 ID（唯一标识）"
            },
            "name": {
                "type": "string",
                "description": "显示名称"
            },
            "description": {
                "type": "string",
                "description": "服务描述"
            },
            "command": {
                "type": "string",
                "description": "启动命令，如 'npx', 'uvx', 'python'"
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "命令参数列表"
            },
            "category": {
                "type": "string",
                "description": "分类: general/web/data/ai",
                "default": "general"
            },
            "env_vars": {
                "type": "object",
                "description": "需要的环境变量",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["server_id", "name", "description", "command"],
    },
    risk_level="medium",
)
async def add_custom_mcp_service(
    server_id: str,
    name: str,
    description: str,
    command: str,
    args: list = None,
    category: str = "general",
    env_vars: dict = None,
) -> SkillResult:
    """添加自定义 MCP 服务"""
    manager = _get_mcp_manager()
    if not manager:
        return SkillResult(success=False, error="MCP 管理器未初始化")
    
    from ..mcp.client import MCPServerInfo
    
    server_info = MCPServerInfo(
        id=server_id,
        name=name,
        description=description,
        command=command,
        args=args or [],
        category=category,
        env=env_vars or {},
        source="custom",
    )
    
    manager.add_custom_server(server_info)
    
    return SkillResult(
        success=True,
        data=(
            f"✅ 自定义 MCP 服务已添加\n\n"
            f"📦 **{name}** (`{server_id}`)\n"
            f"命令: `{command} {' '.join(args or [])}`\n\n"
            f"使用 `connect_mcp_service('{server_id}')` 连接"
        )
    )


@register(
    name="mcp_status",
    description="查看 MCP 服务的当前状态（包括健康状态）",
    parameters={"type": "object", "properties": {}, "required": []},
    risk_level="low",
)
async def mcp_status() -> SkillResult:
    """查看 MCP 状态"""
    manager = _get_mcp_manager()
    if not manager:
        return SkillResult(success=False, error="MCP 管理器未初始化")
    
    # 获取详细健康状态
    connected = manager.list_connected_servers()
    if not connected:
        return SkillResult(success=True, data="🔌 没有已连接的 MCP 服务")
    
    lines = [f"🔌 **MCP 服务状态** ({len(connected)} 个)\n"]
    
    for s in connected:
        health = s.get("health", {})
        is_connected = health.get("connected", s.get("connected", False))
        process_alive = health.get("process_alive", True)
        failures = health.get("consecutive_failures", 0)
        tools = s.get("tools_count", 0)
        
        # 状态图标
        if is_connected and process_alive and failures == 0:
            status = "🟢"
        elif is_connected and failures < 3:
            status = "🟡"
        else:
            status = "🔴"
        
        lines.append(f"{status} **{s['id']}** - {tools} 个工具")
        
        # 显示健康详情
        if failures > 0:
            lines.append(f"   ⚠️ 连续失败: {failures} 次")
        if health.get("reconnect_attempts", 0) > 0:
            lines.append(f"   🔄 重连次数: {health['reconnect_attempts']}")
    
    return SkillResult(success=True, data="\n".join(lines))


@register(
    name="mcp_health_check",
    description="对指定或所有 MCP 服务进行健康检查",
    parameters={
        "type": "object",
        "properties": {
            "server_id": {
                "type": "string",
                "description": "要检查的服务 ID（不填则检查所有）"
            },
        },
        "required": [],
    },
    risk_level="low",
)
async def mcp_health_check(server_id: str = None) -> SkillResult:
    """执行健康检查"""
    manager = _get_mcp_manager()
    if not manager:
        return SkillResult(success=False, error="MCP 管理器未初始化")
    
    results = []
    servers_to_check = [server_id] if server_id else manager.connected_servers
    
    for sid in servers_to_check:
        client = manager._clients.get(sid)
        if not client:
            results.append(f"❌ {sid}: 未连接")
            continue
        
        # 执行 ping
        healthy = await client._ping()
        health = client.get_health_status()
        
        if healthy:
            results.append(f"✅ {sid}: 健康")
        else:
            results.append(f"❌ {sid}: 不健康 (失败 {health['consecutive_failures']} 次)")
            
            # 尝试重连
            if client.auto_reconnect:
                results.append(f"   🔄 正在尝试重连...")
    
    return SkillResult(
        success=True,
        data="🏥 **MCP 健康检查结果**\n\n" + "\n".join(results)
    )


@register(
    name="mcp_reconnect",
    description="手动重连一个断开的 MCP 服务",
    parameters={
        "type": "object",
        "properties": {
            "server_id": {
                "type": "string",
                "description": "要重连的服务 ID"
            },
        },
        "required": ["server_id"],
    },
    risk_level="low",
)
async def mcp_reconnect(server_id: str) -> SkillResult:
    """手动重连"""
    manager = _get_mcp_manager()
    if not manager:
        return SkillResult(success=False, error="MCP 管理器未初始化")
    
    client = manager._clients.get(server_id)
    if not client:
        return SkillResult(success=False, error=f"服务 {server_id} 未找到")
    
    # 重置重连计数器
    client._reconnect_attempts = 0
    
    success = await client._try_reconnect()
    
    if success:
        return SkillResult(
            success=True,
            data=f"✅ MCP 服务 **{server_id}** 重连成功"
        )
    else:
        return SkillResult(
            success=False,
            error=f"MCP 服务 {server_id} 重连失败"
        )
