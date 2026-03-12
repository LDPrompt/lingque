"""
🐦 灵雀 - P2 调度器相关技能

提供:
- 查看定时任务状态
- 查看任务队列状态
- 添加/管理定时任务
- 提交后台任务
"""

import asyncio
import json
import logging
from pathlib import Path
from .registry import register, SkillResult

logger = logging.getLogger("lingque.skills.scheduler")

_feishu_channel = None
_agent = None
_TASKS_FILE = Path("workspaces/scheduler_tasks.json")


def set_feishu_channel_for_scheduler(channel):
    global _feishu_channel
    _feishu_channel = channel


def set_agent_for_scheduler(agent):
    global _agent
    _agent = agent


def _save_tasks_to_disk(scheduler) -> None:
    """将用户动态添加的任务持久化到磁盘"""
    _TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tasks = []
    for t in scheduler.tasks.values():
        if hasattr(t, "_user_task_prompt"):
            tasks.append({
                "name": t.name,
                "cron_expr": t.cron_expr,
                "task_prompt": t._user_task_prompt,
                "chat_id": getattr(t, "_target_chat_id", ""),
                "enabled": t.enabled,
            })
    _TASKS_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"定时任务已持久化: {len(tasks)} 个 → {_TASKS_FILE}")


def _make_task_callback(name: str, task_prompt: str, target_chat_id: str = ""):
    """为定时任务创建执行回调
    
    Args:
        name: 任务名称
        task_prompt: 任务执行指令
        target_chat_id: 目标会话 ID（任务结果发送到哪个群/私聊）
    """
    async def _execute():
        if not _feishu_channel or not _agent:
            logger.warning(f"定时任务 '{name}' 跳过: 飞书通道或 Agent 未就绪")
            return
        
        chat_id = target_chat_id
        if not chat_id:
            logger.warning(f"定时任务 '{name}' 跳过: 未指定目标会话 (chat_id)")
            return

        logger.info(f"开始执行定时任务: {name}, 目标会话: {chat_id}")
        await _feishu_channel.send_card(chat_id, "⏰ 定时任务开始", f"**{name}**\n正在执行...")

        try:
            response = await _agent.process_message(
                f"[定时任务: {name}] {task_prompt}",
                session_id="scheduler",
            )
            await _feishu_channel.send_card(chat_id, f"⏰ {name}", response)
            logger.info(f"定时任务完成: {name}")
        except Exception as e:
            await _feishu_channel.send_card(chat_id, f"⏰ {name} 失败", f"执行出错: {e}")
            logger.error(f"定时任务 '{name}' 执行失败: {e}")
    return _execute


def restore_tasks_from_disk(scheduler) -> int:
    """从磁盘恢复用户添加的定时任务，返回恢复数量"""
    if not _TASKS_FILE.exists():
        return 0
    try:
        tasks = json.loads(_TASKS_FILE.read_text(encoding="utf-8"))
        count = 0
        for t in tasks:
            name = t["name"]
            cron_expr = t["cron_expr"]
            task_prompt = t["task_prompt"]
            chat_id = t.get("chat_id", "")
            if name in scheduler.tasks:
                continue
            callback = _make_task_callback(name, task_prompt, target_chat_id=chat_id)
            scheduler.add_task(name, cron_expr, callback)
            scheduler.tasks[name]._user_task_prompt = task_prompt
            scheduler.tasks[name]._target_chat_id = chat_id
            if not t.get("enabled", True):
                scheduler.tasks[name].enabled = False
            count += 1
        logger.info(f"从磁盘恢复了 {count} 个定时任务")
        return count
    except Exception as e:
        logger.error(f"恢复定时任务失败: {e}")
        return 0


@register(
    name="get_scheduler_status",
    description="查看定时任务调度器状态，包括所有已配置的定时任务及其下次执行时间",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    risk_level="low",
)
async def get_scheduler_status() -> SkillResult:
    """获取调度器状态"""
    try:
        from ..main import get_scheduler
        scheduler = get_scheduler()
        if scheduler:
            return SkillResult(success=True, data=scheduler.get_status())
        return SkillResult(success=True, data="📅 调度器未启动 (SCHEDULER_ENABLED=false)")
    except Exception as e:
        return SkillResult(success=False, error=str(e))


@register(
    name="get_task_queue_status",
    description="查看后台任务队列状态，包括待处理、执行中、已完成的任务数",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    risk_level="low",
)
async def get_task_queue_status() -> SkillResult:
    """获取任务队列状态"""
    try:
        from ..main import get_task_queue
        queue = get_task_queue()
        if queue:
            return SkillResult(success=True, data=queue.get_status())
        return SkillResult(success=True, data="📋 任务队列未启动")
    except Exception as e:
        return SkillResult(success=False, error=str(e))


@register(
    name="submit_background_task",
    description="提交一个后台任务，任务会异步执行，完成后通过飞书消息通知",
    parameters={
        "type": "object",
        "properties": {
            "task_name": {"type": "string", "description": "任务名称"},
            "task_type": {"type": "string", "description": "任务类型: 'agent' 或 'shell'"},
            "content": {"type": "string", "description": "任务内容 (Agent 指令或 shell 命令)"},
        },
        "required": ["task_name", "task_type", "content"],
    },
    risk_level="high",
)
async def submit_background_task(
    task_name: str,
    task_type: str,
    content: str,
    chat_id: str = "",
) -> SkillResult:
    """提交后台任务"""
    try:
        from ..main import get_task_queue
        queue = get_task_queue()

        if not queue:
            return SkillResult(success=False, error="任务队列未启动")

        if task_type == "agent":
            return SkillResult(
                success=False,
                error="Agent 后台任务暂不支持直接提交，请使用其他方式"
            )

        elif task_type == "shell":
            import asyncio
            import subprocess
            from .code_runner import (
                _is_safe_command, _check_dangerous_patterns, _check_sensitive_paths,
            )

            if not _is_safe_command(content):
                danger_err = _check_dangerous_patterns(content)
                detail = danger_err or "不在安全命令白名单中"
                return SkillResult(success=False, error=f"安全限制: {detail}")
            danger_err = _check_dangerous_patterns(content)
            if danger_err:
                return SkillResult(success=False, error=danger_err)
            path_err = _check_sensitive_paths(content)
            if path_err:
                return SkillResult(success=False, error=path_err)

            async def run_shell_task(cmd: str):
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(), timeout=300
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.communicate()
                    raise Exception(f"命令执行超时 (300s)，已终止进程")
                output = stdout.decode() if stdout else ""
                error = stderr.decode() if stderr else ""
                if proc.returncode != 0:
                    raise Exception(f"命令执行失败 (code={proc.returncode}): {error}")
                return output

            task_id = await queue.submit(
                task_name,
                run_shell_task,
                content,
                chat_id=chat_id,
                notify=True,
            )
            return SkillResult(
                success=True,
                data=f"✅ 任务已提交到后台队列\n任务 ID: {task_id}\n完成后会通过消息通知你"
            )

        else:
            return SkillResult(success=False, error=f"不支持的任务类型: {task_type}")

    except Exception as e:
        logger.error(f"提交后台任务失败: {e}")
        return SkillResult(success=False, error=str(e))


@register(
    name="add_cron_task",
    description=(
        "添加一个定时任务。到时间后灵雀会自动执行指定任务（浏览网页、搜索信息、分析数据等），并把结果发到飞书。\n"
        "用户说自然语言，你来转换成 cron 表达式和任务描述。\n"
        "示例:\n"
        "- '每天中午12点看看马斯克X上有没有新推文' → cron='0 12 * * *', task='打开 x.com/elonmusk 查看最新推文，如果有新内容就汇总发给我'\n"
        "- '每周一早上9点查看GitHub趋势' → cron='0 9 * * 1', task='打开 github.com/trending 查看本周热门项目，汇总前5个发给我'\n"
        "- '每天下午6点帮我总结今天的工作' → cron='0 18 * * *', task='查看今天的对话记录和日志，生成工作日报'"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "任务名称，如 '马斯克推文监控'"},
            "cron_expr": {"type": "string", "description": "Cron 表达式 (分 时 日 月 周)"},
            "task_prompt": {
                "type": "string",
                "description": "灵雀要执行的任务描述，写清楚要做什么、怎么做、结果怎么呈现",
            },
        },
        "required": ["name", "cron_expr", "task_prompt"],
    },
    risk_level="medium",
)
async def add_cron_task(name: str, cron_expr: str, task_prompt: str) -> SkillResult:
    """添加定时任务：到时间后执行完整的 Agent 任务循环"""
    try:
        from ..main import get_scheduler
        scheduler = get_scheduler()

        if not scheduler:
            return SkillResult(
                success=False,
                error="调度器未启用，请在 .env 中设置 SCHEDULER_ENABLED=true 并重启"
            )

        try:
            from croniter import croniter
            croniter(cron_expr)
        except Exception as e:
            return SkillResult(success=False, error=f"无效的 Cron 表达式: {e}")

        if name in scheduler.tasks:
            return SkillResult(success=False, error=f"任务 '{name}' 已存在，请先删除或换个名字")

        # 获取当前会话 ID，任务到期时结果会发送到这个会话
        target_chat_id = ""
        if _feishu_channel:
            target_chat_id = _feishu_channel.get_current_chat_id()
        
        if not target_chat_id:
            return SkillResult(
                success=False,
                error="无法获取当前会话 ID，请在飞书群聊/私聊中添加定时任务"
            )

        callback = _make_task_callback(name, task_prompt, target_chat_id=target_chat_id)
        scheduler.add_task(name, cron_expr, callback)
        scheduler.tasks[name]._user_task_prompt = task_prompt
        scheduler.tasks[name]._target_chat_id = target_chat_id

        _save_tasks_to_disk(scheduler)

        next_run = scheduler.tasks[name].next_run
        next_str = next_run.strftime("%Y-%m-%d %H:%M") if next_run else "计算中"

        return SkillResult(
            success=True,
            data=(
                f"✅ 定时任务已添加并持久化!\n"
                f"  名称: {name}\n"
                f"  Cron: {cron_expr}\n"
                f"  任务: {task_prompt}\n"
                f"  下次执行: {next_str}\n"
                f"  结果发送到: 当前会话\n\n"
                "到时间后灵雀会自动执行任务并把结果发到飞书。\n"
                "重启后自动恢复，无需重新添加。"
            )
        )

    except Exception as e:
        return SkillResult(success=False, error=str(e))


@register(
    name="remove_cron_task",
    description="删除一个定时任务。先用 get_scheduler_status 查看所有任务名称。",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "要删除的任务名称"},
        },
        "required": ["name"],
    },
    risk_level="medium",
)
async def remove_cron_task(name: str) -> SkillResult:
    """删除定时任务"""
    try:
        from ..main import get_scheduler
        scheduler = get_scheduler()
        if not scheduler:
            return SkillResult(success=False, error="调度器未启用")
        if name not in scheduler.tasks:
            available = ", ".join(scheduler.tasks.keys()) or "无"
            return SkillResult(success=False, error=f"任务 '{name}' 不存在。当前任务: {available}")
        scheduler.remove_task(name)
        _save_tasks_to_disk(scheduler)
        return SkillResult(success=True, data=f"✅ 定时任务 '{name}' 已删除")
    except Exception as e:
        return SkillResult(success=False, error=str(e))


@register(
    name="list_cron_examples",
    description="列出常用的 Cron 表达式示例",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    risk_level="low",
)
async def list_cron_examples() -> SkillResult:
    """列出 Cron 示例"""
    examples = """
📅 **Cron 表达式示例**

**基本格式**: `分 时 日 月 周`

| 表达式 | 含义 |
|--------|------|
| `0 8 * * *` | 每天早上 8:00 |
| `30 9 * * *` | 每天早上 9:30 |
| `0 18 * * *` | 每天晚上 18:00 |
| `0 9 * * 1` | 每周一早上 9:00 |
| `0 9 * * 1-5` | 周一到周五早上 9:00 |
| `0 9,18 * * *` | 每天早上 9:00 和晚上 18:00 |
| `0 */2 * * *` | 每 2 小时整点 |
| `0 0 1 * *` | 每月 1 号 0:00 |
| `0 0 * * 0` | 每周日 0:00 |

**字段说明**:
- 分钟: 0-59
- 小时: 0-23
- 日期: 1-31
- 月份: 1-12
- 星期: 0-7 (0 和 7 都是周日)
"""
    return SkillResult(success=True, data=examples)


# ==================== 一次性延迟任务 ====================

_delayed_tasks: dict[str, "asyncio.Task"] = {}


@register(
    name="add_delayed_task",
    description=(
        "添加一个一次性延迟任务，N分钟后执行一次。适合'5分钟后提醒我XXX'、'半小时后帮我查一下XXX'这类场景。\n"
        "与 add_cron_task 的区别：\n"
        "- add_delayed_task: 一次性任务，执行一次后自动删除\n"
        "- add_cron_task: 周期性任务，按 Cron 表达式重复执行"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "任务名称，如 '开会提醒'"},
            "delay_minutes": {"type": "number", "description": "延迟时间（分钟），如 5 表示5分钟后执行"},
            "task_prompt": {
                "type": "string",
                "description": "灵雀要执行的任务描述，如 '提醒用户去开会'、'查询今天的天气并告诉用户'",
            },
        },
        "required": ["name", "delay_minutes", "task_prompt"],
    },
    risk_level="medium",
)
async def add_delayed_task(name: str, delay_minutes: float, task_prompt: str) -> SkillResult:
    """添加一次性延迟任务：N分钟后执行一次，执行后自动删除"""
    import asyncio
    from datetime import datetime, timedelta

    try:
        if delay_minutes <= 0:
            return SkillResult(success=False, error="延迟时间必须大于 0 分钟")
        
        if delay_minutes > 1440:  # 24小时
            return SkillResult(success=False, error="延迟时间不能超过 24 小时（1440分钟），长期任务请使用 add_cron_task")
        
        if name in _delayed_tasks:
            return SkillResult(success=False, error=f"任务 '{name}' 已存在，请先取消或换个名字")

        # 获取当前会话 ID
        target_chat_id = ""
        if _feishu_channel:
            target_chat_id = _feishu_channel.get_current_chat_id()
        
        if not target_chat_id:
            return SkillResult(
                success=False,
                error="无法获取当前会话 ID，请在飞书群聊/私聊中添加任务"
            )

        execute_at = datetime.now() + timedelta(minutes=delay_minutes)
        execute_at_str = execute_at.strftime("%Y-%m-%d %H:%M:%S")

        async def _delayed_execute():
            """延迟执行并在完成后清理"""
            await asyncio.sleep(delay_minutes * 60)
            
            if not _feishu_channel or not _agent:
                logger.warning(f"延迟任务 '{name}' 跳过: 飞书通道或 Agent 未就绪")
                return
            
            logger.info(f"开始执行延迟任务: {name}, 目标会话: {target_chat_id}")
            await _feishu_channel.send_card(target_chat_id, "⏰ 定时任务触发", f"**{name}**\n正在执行...")

            try:
                response = await _agent.process_message(
                    f"[定时任务: {name}] {task_prompt}",
                    session_id="scheduler",
                )
                await _feishu_channel.send_card(target_chat_id, f"⏰ {name}", response)
                logger.info(f"延迟任务完成: {name}")
            except Exception as e:
                await _feishu_channel.send_card(target_chat_id, f"⏰ {name} 失败", f"执行出错: {e}")
                logger.error(f"延迟任务 '{name}' 执行失败: {e}")
            finally:
                # 执行完成后从列表中移除
                _delayed_tasks.pop(name, None)

        # 创建后台任务
        task = asyncio.create_task(_delayed_execute())
        _delayed_tasks[name] = task

        return SkillResult(
            success=True,
            data=(
                f"✅ 一次性延迟任务已添加!\n"
                f"  名称: {name}\n"
                f"  延迟: {delay_minutes} 分钟\n"
                f"  任务: {task_prompt}\n"
                f"  预计执行时间: {execute_at_str}\n"
                f"  结果发送到: 当前会话\n\n"
                "⚠️ 注意：一次性任务不会持久化，服务重启后会丢失。如需重启后保留，请使用 add_cron_task。"
            )
        )

    except Exception as e:
        return SkillResult(success=False, error=str(e))


@register(
    name="cancel_delayed_task",
    description="取消一个尚未执行的延迟任务",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "要取消的任务名称"},
        },
        "required": ["name"],
    },
    risk_level="low",
)
async def cancel_delayed_task(name: str) -> SkillResult:
    """取消延迟任务"""
    if name not in _delayed_tasks:
        available = ", ".join(_delayed_tasks.keys()) or "无"
        return SkillResult(success=False, error=f"任务 '{name}' 不存在或已执行。当前待执行任务: {available}")
    
    task = _delayed_tasks.pop(name)
    task.cancel()
    return SkillResult(success=True, data=f"✅ 延迟任务 '{name}' 已取消")


@register(
    name="list_delayed_tasks",
    description="列出所有待执行的延迟任务",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    risk_level="low",
)
async def list_delayed_tasks() -> SkillResult:
    """列出延迟任务"""
    if not _delayed_tasks:
        return SkillResult(success=True, data="📅 没有待执行的延迟任务")
    
    lines = [f"📅 待执行的延迟任务 ({len(_delayed_tasks)} 个):\n"]
    for name, task in _delayed_tasks.items():
        status = "⏳ 等待中" if not task.done() else "✅ 已完成"
        lines.append(f"- {name}: {status}")
    
    return SkillResult(success=True, data="\n".join(lines))
