"""
🐦 灵雀 - MCP (Model Context Protocol) 支持模块

提供最小化的 MCP 客户端，支持 stdio 模式连接 MCP 服务器，
将服务器提供的 tools 注册为灵雀技能。
"""

from .client import MCPClient

__all__ = ["MCPClient"]
