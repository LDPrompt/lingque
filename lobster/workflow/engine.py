"""
工作流引擎核心

职责：
- 加载工作流定义（YAML）
- 创建并执行工作流实例（WorkflowRun）
- 管理审批门控（pause / resume）
- 步骤间数据传递
- 状态持久化
"""

import os
import logging
import contextvars
from datetime import datetime
from typing import Any, Callable, Awaitable, Optional

from .models import WorkflowDef, WorkflowRun, StepResult, RunStatus
from .context import WorkflowContext
from .steps import execute_step
from .loader import load_workflow, scan_workflows
from .store import WorkflowStore

logger = logging.getLogger("lobster.workflow")


class WorkflowEngine:
    """
    声明式工作流引擎

    用法：
        engine = WorkflowEngine(workspace_dir="/path/to/workspace")
        engine.load_workflows()

        run_id = await engine.start("daily_report", inputs={"date": "today"})
        status = engine.get_run_status(run_id)

        # 审批后继续
        await engine.resume(token, approved=True)
    """

    def __init__(
        self,
        workspace_dir: str = ".",
        llm_router=None,
        notify_callback: Callable[[str, str], Awaitable] = None,
    ):
        self.workspace_dir = workspace_dir
        self.llm_router = llm_router
        self.notify_callback = notify_callback

        self._workflows_dir = os.path.join(workspace_dir, "workflows")
        self._store = WorkflowStore(
            os.path.join(workspace_dir, ".workflow_runs")
        )
        self._definitions: dict[str, WorkflowDef] = {}
        self._active_runs: dict[str, WorkflowRun] = {}

        self._current_run_var: contextvars.ContextVar[Optional[WorkflowRun]] = contextvars.ContextVar(
            "workflow_current_run", default=None
        )

    @property
    def current_run(self) -> Optional[WorkflowRun]:
        return self._current_run_var.get(None)

    @current_run.setter
    def current_run(self, run: Optional[WorkflowRun]):
        self._current_run_var.set(run)

    # ==================== 工作流管理 ====================

    def load_workflows(self):
        """扫描并加载所有工作流定义"""
        os.makedirs(self._workflows_dir, exist_ok=True)
        self._definitions = scan_workflows(self._workflows_dir)
        logger.info(f"已加载 {len(self._definitions)} 个工作流定义")

    def register_workflow(self, workflow_def: WorkflowDef):
        """动态注册工作流定义"""
        self._definitions[workflow_def.name] = workflow_def
        logger.info(f"注册工作流: {workflow_def.name}")

    def get_workflow(self, name: str) -> Optional[WorkflowDef]:
        return self._definitions.get(name)

    def list_workflows(self) -> list[dict]:
        """列出所有可用工作流"""
        return [
            {
                "name": wf.name,
                "description": wf.description,
                "version": wf.version,
                "steps": len(wf.steps),
                "inputs": list(wf.inputs.keys()),
                "triggers": wf.triggers,
            }
            for wf in self._definitions.values()
        ]

    # ==================== 执行 ====================

    async def start(
        self,
        workflow_name: str,
        inputs: dict[str, Any] = None,
        chat_id: str = "",
        user_id: str = "",
    ) -> str:
        """
        启动工作流

        Returns:
            run_id: 工作流运行 ID
        """
        wf_def = self._definitions.get(workflow_name)
        if not wf_def:
            raise ValueError(f"工作流 '{workflow_name}' 不存在。"
                             f"可用: {list(self._definitions.keys())}")

        run = WorkflowRun(
            workflow_name=workflow_name,
            status=RunStatus.RUNNING,
            started_at=datetime.now(),
            inputs=inputs or {},
            chat_id=chat_id,
            user_id=user_id,
        )
        run.variables["inputs"] = inputs or {}

        self._active_runs[run.id] = run
        self.current_run = run
        self._store.save(run)

        logger.info(f"启动工作流: {workflow_name} (ID: {run.id})")

        try:
            await self._execute_steps(run, wf_def.steps)
        except Exception as e:
            if run.status != RunStatus.PAUSED:
                run.status = RunStatus.FAILED
                run.error = str(e)
                logger.error(f"工作流执行异常: {e}", exc_info=True)

        if run.status == RunStatus.RUNNING:
            run.status = RunStatus.COMPLETED
            run.completed_at = datetime.now()

        self._store.save(run)

        if run.status == RunStatus.COMPLETED and self.notify_callback and chat_id:
            summary = self._build_summary(run, wf_def)
            try:
                await self.notify_callback(chat_id, summary)
            except Exception as e:
                logger.error(f"发送完成通知失败: {e}")

        return run.id

    async def _execute_steps(self, run: WorkflowRun, steps: list):
        """顺序执行步骤列表"""
        ctx = WorkflowContext(run.variables)

        for idx, step_def in enumerate(steps):
            if run.status == RunStatus.PAUSED:
                run.current_step_idx = idx
                break
            if run.status in (RunStatus.FAILED, RunStatus.CANCELLED):
                break

            run.current_step_id = step_def.id
            logger.info(f"执行步骤 [{idx + 1}/{len(steps)}]: {step_def.id} ({step_def.type.value})")

            result = await execute_step(step_def, ctx, self)

            run.step_results[step_def.id] = result
            ctx.set(step_def.id, {
                "output": result.output,
                "success": result.success,
                "error": result.error,
            })

            run.variables = ctx.get_all()

            if not result.success and step_def.on_error == "fail":
                run.status = RunStatus.FAILED
                run.error = f"步骤 {step_def.id} 失败: {result.error}"
                logger.error(f"工作流失败: {run.error}")
                break

            if run.status == RunStatus.PAUSED:
                run.current_step_idx = idx + 1
                break

    # ==================== 审批门控 ====================

    async def resume(self, token: str, approved: bool = True) -> str:
        """
        恢复暂停的工作流

        Args:
            token: resume_token（审批步骤生成的）
            approved: True=批准继续，False=取消工作流

        Returns:
            状态消息
        """
        run_data = self._store.find_by_token(token)
        if not run_data:
            return f"未找到 token 对应的工作流: {token}"

        run_id = run_data["id"]
        workflow_name = run_data["workflow_name"]
        wf_def = self._definitions.get(workflow_name)
        if not wf_def:
            return f"工作流定义 '{workflow_name}' 已不存在"

        run = self._active_runs.get(run_id)
        if not run:
            run = WorkflowRun(
                id=run_id,
                workflow_name=workflow_name,
                status=RunStatus.PAUSED,
                variables=run_data.get("variables", {}),
                inputs=run_data.get("inputs", {}),
                chat_id=run_data.get("chat_id", ""),
                user_id=run_data.get("user_id", ""),
                current_step_idx=run_data.get("current_step_idx", 0),
            )
            self._active_runs[run_id] = run

        if not approved:
            run.status = RunStatus.CANCELLED
            run.completed_at = datetime.now()
            run.resume_token = ""
            self._store.save(run)
            logger.info(f"工作流已取消: {run_id}")
            return f"工作流 {workflow_name} 已取消"

        run.status = RunStatus.RUNNING
        run.resume_token = ""
        self.current_run = run

        remaining_steps = wf_def.steps[run.current_step_idx:]
        logger.info(f"恢复工作流: {run_id}, 从步骤 {run.current_step_idx} 继续, "
                     f"剩余 {len(remaining_steps)} 步")

        try:
            await self._execute_steps(run, remaining_steps)
        except Exception as e:
            if run.status != RunStatus.PAUSED:
                run.status = RunStatus.FAILED
                run.error = str(e)

        if run.status == RunStatus.RUNNING:
            run.status = RunStatus.COMPLETED
            run.completed_at = datetime.now()

        self._store.save(run)

        status_text = {
            RunStatus.COMPLETED: "已完成",
            RunStatus.PAUSED: "再次等待审批",
            RunStatus.FAILED: f"失败: {run.error}",
        }.get(run.status, run.status.value)

        return f"工作流 {workflow_name} {status_text}"

    # ==================== 状态查询 ====================

    def get_run_status(self, run_id: str) -> Optional[dict]:
        """获取工作流运行状态"""
        run = self._active_runs.get(run_id)
        if run:
            return run.to_dict()
        return self._store.load(run_id)

    def list_runs(self, status: str = None) -> list[dict]:
        return self._store.list_runs(status=status)

    def save_run(self, run: WorkflowRun):
        self._store.save(run)

    # ==================== 辅助 ====================

    def _build_summary(self, run: WorkflowRun, wf_def: WorkflowDef) -> str:
        """构建工作流完成摘要"""
        status_emoji = {
            RunStatus.COMPLETED: "✅",
            RunStatus.FAILED: "❌",
            RunStatus.CANCELLED: "🚫",
            RunStatus.PAUSED: "⏸️",
        }.get(run.status, "📋")

        lines = [f"{status_emoji} **工作流完成: {wf_def.name}**\n"]

        if wf_def.description:
            lines.append(f"_{wf_def.description}_\n")

        lines.append("**步骤执行结果:**")
        for step_def in wf_def.steps:
            result = run.step_results.get(step_def.id)
            if result:
                icon = "✅" if result.success else "❌"
                output_preview = str(result.output)[:100]
                lines.append(f"  {icon} {step_def.id}: {output_preview}")
            else:
                lines.append(f"  ⏭️ {step_def.id}: 未执行")

        if run.error:
            lines.append(f"\n**错误**: {run.error}")

        elapsed = ""
        if run.started_at and run.completed_at:
            delta = run.completed_at - run.started_at
            elapsed = f"{delta.total_seconds():.1f}s"
            lines.append(f"\n⏱️ 耗时: {elapsed}")

        return "\n".join(lines)
