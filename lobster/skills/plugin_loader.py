"""
插件热加载系统

扫描 plugins/ 目录，自动发现并注册技能插件。
支持运行时热加载，不需要重启服务。

插件格式：
  plugins/
    my_plugin.py          # 单文件插件
    my_package/           # 包插件
      __init__.py
      plugin.py

每个插件需要定义 register(registry) 函数，或使用 PLUGIN_META 声明。

示例插件 (plugins/hello.py):
    PLUGIN_META = {
        "name": "hello",
        "description": "示例插件",
        "version": "1.0",
    }

    async def greet(name: str = "World") -> str:
        return f"Hello, {name}!"

    def register(registry):
        registry.register(
            name="greet",
            description="打招呼",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "名字"}},
                "required": [],
            },
            risk_level="low",
            category="plugin",
        )(greet)
"""

import os
import sys
import time
import logging
import importlib
import importlib.util
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger("lobster.plugins")


@dataclass
class PluginInfo:
    """已加载插件的元信息"""
    name: str
    path: str
    version: str = "1.0"
    description: str = ""
    skills: list[str] = field(default_factory=list)
    loaded_at: float = 0.0
    file_mtime: float = 0.0
    module_name: str = ""


class PluginLoader:
    """
    插件热加载管理器

    用法：
        loader = PluginLoader(plugins_dir="./plugins", registry=registry)
        loader.scan()           # 首次扫描加载
        loader.hot_reload()     # 检查变更并重载
    """

    def __init__(self, plugins_dir: str, registry):
        self.plugins_dir = Path(plugins_dir).resolve()
        self.registry = registry
        self._plugins: dict[str, PluginInfo] = {}
        self._watching = False
        self._watch_task = None

    def scan(self) -> list[str]:
        """扫描并加载所有插件，返回加载的插件名列表"""
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        loaded = []

        for item in sorted(self.plugins_dir.iterdir()):
            if item.name.startswith("_") or item.name.startswith("."):
                continue

            if item.is_file() and item.suffix == ".py":
                name = item.stem
                if self._load_plugin(name, item):
                    loaded.append(name)

            elif item.is_dir() and (item / "__init__.py").exists():
                name = item.name
                entry = item / "plugin.py" if (item / "plugin.py").exists() else item / "__init__.py"
                if self._load_plugin(name, entry):
                    loaded.append(name)

        if loaded:
            logger.info(f"🔌 已加载 {len(loaded)} 个插件: {loaded}")
        return loaded

    def _load_plugin(self, name: str, path: Path) -> bool:
        """加载单个插件"""
        module_name = f"lobster_plugin_{name}"

        try:
            spec = importlib.util.spec_from_file_location(module_name, str(path))
            if not spec or not spec.loader:
                logger.warning(f"插件 {name} 无法创建 spec: {path}")
                return False

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            skills_before = set(self.registry._skills.keys())

            if hasattr(module, "register") and callable(module.register):
                module.register(self.registry)
            elif hasattr(module, "setup") and callable(module.setup):
                module.setup(self.registry)

            skills_after = set(self.registry._skills.keys())
            new_skills = list(skills_after - skills_before)

            meta = getattr(module, "PLUGIN_META", {})
            info = PluginInfo(
                name=meta.get("name", name),
                path=str(path),
                version=meta.get("version", "1.0"),
                description=meta.get("description", ""),
                skills=new_skills,
                loaded_at=time.time(),
                file_mtime=path.stat().st_mtime,
                module_name=module_name,
            )
            self._plugins[name] = info

            logger.info(f"🔌 加载插件: {info.name} v{info.version} "
                        f"(技能: {new_skills or '无新增'})")
            return True

        except Exception as e:
            logger.error(f"加载插件 {name} 失败: {e}", exc_info=True)
            if module_name in sys.modules:
                del sys.modules[module_name]
            return False

    def hot_reload(self) -> list[str]:
        """检查文件变更并热重载"""
        reloaded = []

        for name, info in list(self._plugins.items()):
            path = Path(info.path)
            if not path.exists():
                logger.info(f"🔌 插件文件已删除: {name}")
                self._unload_plugin(name)
                continue

            current_mtime = path.stat().st_mtime
            if current_mtime > info.file_mtime:
                logger.info(f"🔌 检测到插件变更: {name}")
                self._unload_plugin(name)
                if self._load_plugin(name, path):
                    reloaded.append(name)

        for item in sorted(self.plugins_dir.iterdir()):
            if item.name.startswith("_") or item.name.startswith("."):
                continue
            name = item.stem if item.is_file() else item.name
            if name not in self._plugins:
                if item.is_file() and item.suffix == ".py":
                    if self._load_plugin(name, item):
                        reloaded.append(name)
                elif item.is_dir() and (item / "__init__.py").exists():
                    entry = item / "plugin.py" if (item / "plugin.py").exists() else item / "__init__.py"
                    if self._load_plugin(name, entry):
                        reloaded.append(name)

        if reloaded:
            logger.info(f"🔌 热重载: {reloaded}")
        return reloaded

    def _unload_plugin(self, name: str):
        """卸载插件（移除注册的技能）"""
        info = self._plugins.pop(name, None)
        if not info:
            return

        for skill_name in info.skills:
            if skill_name in self.registry._skills:
                del self.registry._skills[skill_name]
                logger.info(f"🔌 卸载技能: {skill_name} (来自插件 {name})")

        if info.module_name in sys.modules:
            del sys.modules[info.module_name]

    async def start_watching(self, interval_seconds: int = 30):
        """启动文件监控（定时热重载）"""
        import asyncio
        self._watching = True

        async def _watch_loop():
            while self._watching:
                await asyncio.sleep(interval_seconds)
                try:
                    self.hot_reload()
                except Exception as e:
                    logger.error(f"热重载异常: {e}")

        self._watch_task = asyncio.create_task(_watch_loop())
        logger.info(f"🔌 插件监控已启动 (每 {interval_seconds}s 检查)")

    async def stop_watching(self):
        """停止文件监控"""
        self._watching = False
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except Exception:
                pass

    def list_plugins(self) -> list[dict]:
        """列出已加载的插件"""
        return [
            {
                "name": info.name,
                "version": info.version,
                "description": info.description,
                "skills": info.skills,
                "path": info.path,
            }
            for info in self._plugins.values()
        ]

    def get_status(self) -> str:
        """获取插件系统状态"""
        if not self._plugins:
            return "🔌 暂无已加载的插件。将 .py 文件放入 plugins/ 目录即可自动加载。"

        lines = [f"🔌 **已加载 {len(self._plugins)} 个插件**\n"]
        for info in self._plugins.values():
            skills_str = ", ".join(info.skills) if info.skills else "无"
            lines.append(
                f"**{info.name}** v{info.version}\n"
                f"  {info.description or '无描述'}\n"
                f"  技能: {skills_str}"
            )
        return "\n\n".join(lines)
