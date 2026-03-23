"""
🐦 灵雀 LingQue - 入口 (P3 版)

P1 功能:
- 多通道并行 (CHANNELS=cli,feishu,dingtalk 同时启动)
- 邮件配置自动注入
- 飞书日历配置自动注入

P2 功能:
- Cron 定时任务调度器 (Heartbeat 心跳)
- 每日摘要推送
- 外部 Webhook 接入 (GitHub/Sentry)
- 邮件监控 (新邮件主动通知)
- 任务队列 (消息排队处理)
- 长任务异步化

P3 功能:
- 向量语义检索 (ChromaDB + sentence-transformers)
- 自动记忆提取 (LLM 判断 + MEMORY.md)
- 上下文压缩/截断 (LLM 摘要 + 滑动窗口)
- 主 Agent 系统 (子任务并行处理)
- 技能自动生成 (LLM 生成代码 + 动态注册)
- Docker 沙箱 (代码隔离执行)
- Playwright 浏览器自动化

用法:
  python -m lobster.main                     # 默认 CLI
  python -m lobster.main --channel feishu     # 单通道
  CHANNELS=cli,feishu python -m lobster.main  # 多通道并行
"""

import os
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

import argparse
import asyncio
import logging
import sys

from dotenv import load_dotenv
load_dotenv()

from .config import get_config
from .llm import LLMRouter
from .agent import Agent, Memory
from .skills import registry
from .skills.file_ops import set_allowed_paths
from .skills.email_calendar import configure_email, configure_feishu_calendar
from .skills.skill_market import set_transplanter
from .skills.memory_skills import set_memory_dir
from .skills.self_improvement import set_workspace_dir as set_si_workspace_dir
from .gateway import CLIChannel, FeishuChannel, DingTalkChannel
from .scheduler import CronScheduler, DailySummary, TaskQueue, EmailMonitor, HeartbeatEngine
from .transplanter import SkillTransplanter


def _make_banner() -> str:
    from . import __version__
    return f"""\
   __    _             ____
  / /   (_)___  ____ _/ __ \\__  _____
 / /   / / __ \\/ __ `/ / / / / / / _ \\
/ /___/ / / / / /_/ / /_/ / /_/ /  __/
\\____/_/_/ /_/\\__, /\\___\\_\\__,_/\\___/
             /____/
🐦 灵雀 LingQue v{__version__} - 灵动 Prompt 出品
"""

BANNER = _make_banner()


def _security_preflight(config, channel_list: list[str]):
    """启动时安全配置检查，输出警告帮助用户加固"""
    logger = logging.getLogger("lingque.security")
    warnings = []

    if "feishu" in channel_list and not config.feishu.allowed_users:
        warnings.append(
            "FEISHU_ALLOWED_USERS 未设置 → 所有飞书用户均可访问。"
            "生产环境请配置用户白名单"
        )

    if not config.security.require_confirmation:
        warnings.append(
            "REQUIRE_CONFIRMATION=false → 高危操作不需要用户确认。"
            "生产环境建议设为 true"
        )

    allowed = config.security.allowed_paths
    if not allowed or allowed == ["."]:
        warnings.append(
            "ALLOWED_PATHS 未配置或过于宽泛。"
            "建议限制为具体的工作目录"
        )

    _known_key_vars = (
        "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY", "DOUBAO_API_KEY",
    )
    has_any_key = any(os.environ.get(k) for k in _known_key_vars)
    if not has_any_key:
        has_provider = any(
            k.startswith("LLM_PROVIDER_") for k in os.environ
        )
        if not has_provider:
            warnings.append("未检测到任何 LLM API Key，Agent 将无法正常工作")

    if warnings:
        logger.warning("=" * 60)
        logger.warning("安全配置检查发现以下问题:")
        for i, w in enumerate(warnings, 1):
            logger.warning(f"  {i}. {w}")
        logger.warning("=" * 60)
    else:
        logger.info("安全配置检查通过")


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _inject_configs(config):
    """将 .env 配置注入到各技能模块"""
    logger = logging.getLogger("lingque")

    # 邮件
    if config.email.imap_host:
        configure_email(
            imap_host=config.email.imap_host,
            smtp_host=config.email.smtp_host,
            username=config.email.username,
            password=config.email.password,
            imap_port=config.email.imap_port,
            smtp_port=config.email.smtp_port,
        )
        logger.info("📧 邮件已配置")

    # 飞书日历
    if config.feishu.app_id:
        configure_feishu_calendar(
            app_id=config.feishu.app_id,
            app_secret=config.feishu.app_secret,
        )
        logger.info("📅 飞书日历已配置")


def _setup_p3_features(llm_router, config):
    """初始化 P3 功能"""
    logger = logging.getLogger("lingque")

    # 技能自动生成器
    try:
        from .skills.skill_generator import get_skill_generator
        get_skill_generator(llm_router)
        logger.info("🔧 技能自动生成器已初始化")
    except Exception as e:
        logger.warning(f"技能生成器初始化失败: {e}")

    # 向量记忆库（可通过 VECTOR_MEMORY_ENABLED=false 关闭，节省内存）
    import os
    vector_enabled = os.environ.get("VECTOR_MEMORY_ENABLED", "true").lower() not in ("false", "0", "no", "off")
    if vector_enabled:
        try:
            from .memory.vector_store import init_vector_memory
            init_vector_memory(config.memory_dir / "vector_db")
            logger.info("🧠 向量记忆库已就绪 (后台预热中)")
        except Exception as e:
            logger.warning(f"向量记忆库初始化失败: {e}")
    else:
        logger.info("🧠 向量记忆库已关闭 (VECTOR_MEMORY_ENABLED=false)")

    # 自我进化引擎 (LEARNING_BACKEND=sqlite|vector 切换检索后端)
    try:
        from .memory.learning_engine import init_learning_engine
        le = init_learning_engine(config.memory_dir)
        backend_label = "SQLite FTS5 (轻量)" if le._backend_type == "sqlite" else "ChromaDB 向量 (语义)"
        logger.info(f"📚 学习引擎已就绪 — 检索后端: {backend_label}")
    except Exception as e:
        logger.warning(f"学习引擎初始化失败: {e}")

    # Docker 沙箱（检查可用性）
    try:
        from .sandbox.docker_sandbox import get_sandbox
        sandbox = get_sandbox()
        if sandbox.is_available():
            logger.info("🐳 Docker 沙箱可用")
        else:
            logger.info("🐳 Docker 沙箱不可用 (Docker 未运行)")
    except ImportError:
        logger.info("🐳 Docker 沙箱未安装 (pip install docker)")
    except Exception:
        pass

    # Playwright 浏览器（延迟初始化 + 注入 LLM router 供视觉分析）
    try:
        from .browser.playwright_browser import _SharedBrowser
        _SharedBrowser.set_llm_router(llm_router)
        logger.info("🌐 Playwright 浏览器已就绪 (延迟加载, 视觉分析可用)")
    except ImportError:
        logger.info("🌐 Playwright 浏览器已就绪 (延迟加载)")


# ==================== P2: 调度器 & 邮件监控 ====================

_scheduler: CronScheduler = None
_task_queue: TaskQueue = None
_email_monitor: EmailMonitor = None
_heartbeat: HeartbeatEngine = None
_ralph_loop = None  # Ralph Loop 自主循环引擎


async def _setup_scheduler(agent: Agent, config, send_callback=None,
                           chat_id: str = "", get_chat_id=None):
    """初始化 Cron 定时任务调度器"""
    global _scheduler
    logger = logging.getLogger("lingque")

    if not config.scheduler.enabled:
        logger.info("📅 调度器未启用 (SCHEDULER_ENABLED=false)")
        return

    _scheduler = CronScheduler()

    if config.scheduler.daily_summary_enabled and send_callback:
        daily_summary = DailySummary(agent, send_callback, get_chat_id=get_chat_id)
        _scheduler.add_task(
            "每日摘要",
            config.scheduler.daily_summary_cron,
            daily_summary.generate_and_send,
        )
        logger.info(f"📅 每日摘要已配置: {config.scheduler.daily_summary_cron}")

    # 从磁盘恢复用户添加的定时任务
    try:
        from .skills.scheduler_skills import restore_tasks_from_disk
        restored = restore_tasks_from_disk(_scheduler)
        if restored:
            logger.info(f"📅 已从磁盘恢复 {restored} 个定时任务")
    except Exception as e:
        logger.warning(f"恢复定时任务失败: {e}")

    await _scheduler.start()
    logger.info("📅 调度器已启动")


async def _setup_task_queue(notify_callback=None):
    """初始化任务队列"""
    global _task_queue
    logger = logging.getLogger("lingque")

    _task_queue = TaskQueue(notify_callback=notify_callback, max_workers=1)
    await _task_queue.start()
    logger.info("📋 任务队列已启动")


async def _setup_email_monitor(config, notify_callback=None, chat_id: str = ""):
    """初始化邮件监控"""
    global _email_monitor
    logger = logging.getLogger("lingque")

    if not config.email.monitor_enabled:
        logger.info("📧 邮件监控未启用 (EMAIL_MONITOR_ENABLED=false)")
        return

    if not config.email.imap_host:
        logger.warning("📧 邮件监控需要 IMAP 配置")
        return

    _email_monitor = EmailMonitor(
        imap_host=config.email.imap_host,
        imap_port=config.email.imap_port,
        username=config.email.username,
        password=config.email.password,
        notify_callback=notify_callback,
        check_interval=config.email.monitor_interval,
    )

    # 添加重要发件人
    if config.email.important_senders:
        for sender in config.email.important_senders.split(","):
            if sender.strip():
                _email_monitor.add_important_sender(sender.strip())

    # 添加重要关键词
    if config.email.important_keywords:
        for kw in config.email.important_keywords.split(","):
            if kw.strip():
                _email_monitor.add_important_keyword(kw.strip())

    _email_monitor.set_notify_chat_id(chat_id)
    await _email_monitor.start()
    logger.info("📧 邮件监控已启动")


def get_task_queue() -> TaskQueue:
    """获取任务队列实例（供其他模块使用）"""
    return _task_queue


def get_scheduler() -> CronScheduler:
    """获取调度器实例"""
    return _scheduler


async def _start_channel(name: str, agent: Agent, config) -> tuple[asyncio.Task | None, any]:
    """启动单个通道, 返回 (Task, Channel 实例)"""
    if name == "cli":
        channel = CLIChannel(agent)
        return asyncio.create_task(channel.start()), channel

    elif name == "feishu":
        if not config.feishu.app_id:
            logging.getLogger("lingque").error("飞书配置缺失! 跳过飞书通道")
            return None, None
        channel = FeishuChannel(agent, config)
        return asyncio.create_task(channel.start()), channel

    elif name == "dingtalk":
        if not config.dingtalk.app_key:
            logging.getLogger("lingque").error("钉钉配置缺失! 跳过钉钉通道")
            return None, None
        channel = DingTalkChannel(agent, {
            "app_key": config.dingtalk.app_key,
            "app_secret": config.dingtalk.app_secret,
        })
        return asyncio.create_task(channel.start()), channel

    else:
        logging.getLogger("lingque").warning(f"未知通道: {name}, 跳过")
        return None, None


async def main():
    parser = argparse.ArgumentParser(description="🐦 灵雀 LingQue - 你的私人 AI Agent")
    parser.add_argument("--channel", type=str, default=None, help="消息通道 (cli/feishu/dingtalk), 覆盖 CHANNELS 配置")
    args = parser.parse_args()

    config = get_config()
    setup_logging(config.log_level)
    logger = logging.getLogger("lingque")

    print(BANNER)

    # 数据迁移 (版本升级时自动适配旧数据)
    try:
        from .migrations import run_pending
        migrated = run_pending(config.memory_dir)
        if migrated:
            logger.info(f"📦 数据迁移完成: {migrated}")
    except Exception as e:
        logger.warning(f"数据迁移检查失败 (不影响启动): {e}")

    # 安全路径
    allowed = config.security.get_allowed_paths()
    if allowed:
        set_allowed_paths(allowed)
    else:
        set_allowed_paths([str(config.workspace_dir.resolve())])

    # 技能结果截断（与 core.py 共用同一配置）
    registry.max_result_chars = config.agent.max_tool_result_chars

    # 注入邮件/日历配置
    _inject_configs(config)

    # 初始化 LLM
    llm_router = LLMRouter(config)

    # P3: 初始化高级功能
    _setup_p3_features(llm_router, config)

    # MCP 协议支持（初始化包管理器）
    mcp_mgr = None
    try:
        from .mcp.client import init_mcp_manager
        mcp_mgr = init_mcp_manager(config.memory_dir / "mcp")
        if config.mcp.servers:
            tool_count = await mcp_mgr.connect_from_config(config.mcp.servers)
            if tool_count > 0:
                logger.info(f"🔌 MCP 已连接 {len(mcp_mgr.connected_servers)} 个服务器, 注册 {tool_count} 个工具")
            else:
                logger.info("🔌 MCP 服务器已配置但未发现工具")
        else:
            logger.info("🔌 MCP 包管理器已初始化（无预配置服务）")
    except Exception as e:
        logger.warning(f"🔌 MCP 初始化失败: {e}")

    # 初始化记忆
    memory = Memory(
        memory_dir=config.memory_dir,
        max_context_messages=config.agent.max_context_messages,
        max_context_tokens=config.agent.max_context_tokens,
        idle_timeout_minutes=config.session.idle_timeout_minutes,
        daily_reset_hour=config.session.daily_reset_hour,
    )
    set_memory_dir(config.memory_dir)
    set_si_workspace_dir(str(config.workspace_dir))

    # 初始化 Agent
    agent = Agent(
        llm_router=llm_router,
        memory=memory,
        max_loops=config.security.max_tool_loops,
        require_confirmation=config.security.require_confirmation,
        agent_config=config.agent,
    )

    # 技能移植器
    transplanter = SkillTransplanter(
        llm_router=llm_router,
        install_dir=config.memory_dir / "transplanted_skills",
        github_token=config.github_token,
    )
    transplanter.load_installed_skills()
    set_transplanter(transplanter)
    logger.info("📦 技能移植器已初始化")

    # P4: 初始化工作流引擎
    try:
        from .workflow import WorkflowEngine
        from .skills.workflow_skills import set_workflow_engine
        workflow_engine = WorkflowEngine(
            workspace_dir=str(config.workspace_dir),
            llm_router=llm_router,
        )
        workflow_engine.load_workflows()
        set_workflow_engine(workflow_engine)
        logger.info(f"🔄 工作流引擎已初始化 ({len(workflow_engine.list_workflows())} 个工作流)")
    except Exception as e:
        logger.warning(f"工作流引擎初始化失败: {e}")

    # P4: 插件热加载
    try:
        from .skills.plugin_loader import PluginLoader
        plugins_dir = config.workspace_dir / "plugins"
        plugin_loader = PluginLoader(plugins_dir=str(plugins_dir), registry=registry)
        loaded = plugin_loader.scan()
        if loaded:
            logger.info(f"🔌 已加载 {len(loaded)} 个插件: {loaded}")
        else:
            logger.info(f"🔌 插件系统就绪 (目录: {plugins_dir})")
    except Exception as e:
        logger.warning(f"插件系统初始化失败: {e}")
        plugin_loader = None

    # P1: 知识图谱 (SuperMemory)
    try:
        from .memory.knowledge_graph import init_knowledge_graph
        kg = init_knowledge_graph(config.memory_dir / "knowledge_graph")
        kg.set_llm(llm_router)  # 注入 LLM 用于智能抽取
        stats = kg.stats()
        logger.info(f"🧠 知识图谱已初始化 ({stats['entity_count']} 实体, {stats['relation_count']} 关系)")
    except Exception as e:
        logger.warning(f"知识图谱初始化失败: {e}")

    # P2: 用户画像管理器
    try:
        from .memory.user_profile import init_profile_manager
        init_profile_manager(config.memory_dir)
        logger.info("👤 用户画像管理器已初始化")
    except Exception as e:
        logger.warning(f"用户画像管理器初始化失败: {e}")

    # 打印技能
    skills = registry.list_all()
    logger.info(f"已加载 {len(skills)} 个技能: {[s.name for s in skills]}")

    # 确定要启动的通道
    if args.channel:
        channel_list = [args.channel]
    else:
        channel_list = config.get_channel_list()

    if not channel_list:
        channel_list = ["cli"]

    # 安全配置检查
    _security_preflight(config, channel_list)

    logger.info(f"启动通道: {channel_list}")

    # P1: 多通道并行启动
    tasks = []
    feishu_channel = None

    for ch_name in channel_list:
        task, channel = await _start_channel(ch_name, agent, config)
        if task:
            tasks.append(task)
        if ch_name == "feishu" and channel:
            feishu_channel = channel

    if not tasks:
        logger.error("没有通道成功启动!")
        sys.exit(1)

    # P2: 启动任务队列
    notify_callback = None
    if feishu_channel:
        notify_callback = feishu_channel.send_card

    await _setup_task_queue(notify_callback)

    # P2: 启动调度器 (如果飞书通道启动，可以发送每日摘要)
    if feishu_channel:
        await _setup_scheduler(
            agent=agent,
            config=config,
            send_callback=feishu_channel.send_card,
            chat_id="",
            get_chat_id=feishu_channel.get_current_chat_id,
        )

        # P2: 启动邮件监控
        await _setup_email_monitor(
            config=config,
            notify_callback=feishu_channel.send_card,
            chat_id="",  # 需要在首次消息时获取
        )

        # 注入飞书通道到群聊技能（@ 成员、查群成员）
        try:
            from .skills.feishu_group import set_feishu_channel as set_feishu_channel_for_group
            set_feishu_channel_for_group(feishu_channel)
            logger.info("👥 群聊 @ 成员功能已启用")
        except Exception:
            pass

        # P3: 注入飞书通道到浏览器模块（用于截图发送）
        try:
            from .browser.playwright_browser import set_feishu_channel
            set_feishu_channel(feishu_channel)
            logger.info("📸 截图发送功能已启用")
        except ImportError:
            pass

        # P3: 注入飞书通道到文件模块（用于文件发送）
        try:
            from .skills.file_ops import set_feishu_channel_for_file
            set_feishu_channel_for_file(feishu_channel)
            logger.info("📁 文件发送功能已启用")
        except Exception:
            pass

        # P3: 注入飞书通道到登录模块（截图指导式登录）
        try:
            from .skills.browser_login import set_feishu_channel_for_login
            set_feishu_channel_for_login(feishu_channel)
            logger.info("🔐 截图指导式登录功能已启用")
        except Exception:
            pass

        # 注入飞书通道到云文档技能（创建/写入/分享飞书文档）
        try:
            from .skills.feishu_docs import set_feishu_channel as set_feishu_channel_for_docs
            set_feishu_channel_for_docs(feishu_channel)
            logger.info("📄 飞书云文档功能已启用")
        except Exception:
            pass

        # P2: 注入飞书通道和 Agent 到调度器技能（定时任务执行）
        try:
            from .skills.scheduler_skills import set_feishu_channel_for_scheduler, set_agent_for_scheduler
            set_feishu_channel_for_scheduler(feishu_channel)
            set_agent_for_scheduler(agent)
            logger.info("⏰ 定时任务功能已启用")
        except Exception:
            pass

        # Ralph Loop 自主循环引擎
        try:
            global _ralph_loop
            from .agent.ralph_loop import init_ralph_loop
            from .skills.ralph_skills import set_feishu_channel_for_ralph
            _ralph_loop = init_ralph_loop(config.memory_dir / "ralph")
            _ralph_loop.set_agent(agent)
            _ralph_loop.set_send_callback(feishu_channel.send_card)
            set_feishu_channel_for_ralph(feishu_channel)
            await _ralph_loop.start()
            logger.info("🔄 Ralph Loop 自主循环引擎已启动")
        except Exception as e:
            logger.warning(f"Ralph Loop 启动失败: {e}")

        # P4: 注入飞书通道到工作流引擎（通知回调）
        try:
            from .skills.workflow_skills import get_workflow_engine
            wf_engine = get_workflow_engine()
            if wf_engine:
                wf_engine.notify_callback = feishu_channel.send_card
                logger.info("🔄 工作流通知已接入飞书")
        except Exception:
            pass

        # P4: 启动心跳引擎
        try:
            global _heartbeat
            from .scheduler.heartbeat import check_paused_workflows, check_pending_cron_tasks
            _heartbeat = HeartbeatEngine(interval_minutes=30)
            _heartbeat.add_check("暂停工作流", check_paused_workflows)
            _heartbeat.add_check("定时任务", check_pending_cron_tasks)

            # 心跳时顺便检查插件变更
            if plugin_loader:
                async def _check_plugins():
                    reloaded = plugin_loader.hot_reload()
                    if reloaded:
                        return f"已热重载插件: {', '.join(reloaded)}"
                    return None
                _heartbeat.add_check("插件变更", _check_plugins)

            _heartbeat.set_action_callback(
                lambda prompt: agent.process_message(prompt, session_id="heartbeat")
            )
            _heartbeat.set_notify_callback(feishu_channel.send_card)
            _heartbeat.set_chat_id_getter(feishu_channel.get_current_chat_id)
            await _heartbeat.start()
            logger.info("💓 心跳引擎已启动")
        except Exception as e:
            logger.warning(f"心跳引擎启动失败: {e}")

    # 后台检查版本更新 (不阻塞主流程)
    _update_check_task = None
    if os.environ.get("AUTO_UPDATE_CHECK", "true").lower() not in ("false", "0", "no", "off"):
        try:
            from .updater import startup_check
            _update_check_task = asyncio.create_task(startup_check())
        except Exception:
            pass

    # 等待所有通道 (任一退出则全部退出)
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for t in done:
            if t.exception():
                logger.error(f"通道异常退出: {t.exception()}")
        for t in pending:
            t.cancel()
    except asyncio.CancelledError:
        pass
    finally:
        if _heartbeat:
            await _heartbeat.stop()
        if _scheduler:
            await _scheduler.stop()
        if _task_queue:
            await _task_queue.stop()
        if _email_monitor:
            await _email_monitor.stop()
        if _ralph_loop:
            await _ralph_loop.stop()


def run():
    import sys
    if "lobster.main" not in sys.modules:
        sys.modules["lobster.main"] = sys.modules.get(__name__, sys.modules.get("__main__"))
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🐦 灵雀已退出")


if __name__ == "__main__":
    import sys
    if "lobster.main" not in sys.modules:
        sys.modules["lobster.main"] = sys.modules[__name__]
    run()
