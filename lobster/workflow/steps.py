"""
步骤执行器

每种 StepType 对应一个执行函数，统一签名：
    async def execute_xxx(step, ctx, engine) -> StepResult
"""

import asyncio
import json
import time
import uuid
import logging
from typing import TYPE_CHECKING

from .models import StepDef, StepResult, StepType, RunStatus
from .context import WorkflowContext

if TYPE_CHECKING:
    from .engine import WorkflowEngine

logger = logging.getLogger("lobster.workflow")


async def execute_step(step: StepDef, ctx: WorkflowContext,
                       engine: "WorkflowEngine") -> StepResult:
    """分发步骤到对应执行器"""
    executors = {
        StepType.TOOL_CALL: _exec_tool_call,
        StepType.LLM: _exec_llm,
        StepType.CONDITION: _exec_condition,
        StepType.LOOP: _exec_loop,
        StepType.APPROVAL: _exec_approval,
        StepType.NOTIFY: _exec_notify,
        StepType.SET_VAR: _exec_set_var,
        StepType.PARALLEL: _exec_parallel,
    }
    executor = executors.get(step.type)
    if not executor:
        return StepResult(step_id=step.id, success=False,
                          error=f"未知步骤类型: {step.type}")

    start = time.monotonic()
    try:
        result = await asyncio.wait_for(executor(step, ctx, engine), timeout=300)
        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result
    except asyncio.TimeoutError:
        return StepResult(step_id=step.id, success=False,
                          error="步骤执行超时 (300s)",
                          duration_ms=300_000)
    except Exception as e:
        return StepResult(step_id=step.id, success=False,
                          error=f"{type(e).__name__}: {e}",
                          duration_ms=int((time.monotonic() - start) * 1000))


# ==================== tool_call ====================

async def _exec_tool_call(step: StepDef, ctx: WorkflowContext,
                          engine: "WorkflowEngine") -> StepResult:
    """调用已注册的技能"""
    from ..skills import registry

    tool_name = ctx.interpolate(step.tool)
    args = ctx.interpolate_dict(step.args)

    skill = registry.get(tool_name)
    if not skill:
        return StepResult(step_id=step.id, success=False,
                          error=f"未找到技能: {tool_name}")

    logger.info(f"[workflow] 调用技能: {tool_name}({args})")
    result = await registry.execute_raw(tool_name, args)
    output = result.data if result.success else (result.error or str(result))
    return StepResult(step_id=step.id, success=result.success, output=output)


# ==================== llm ====================

async def _exec_llm(step: StepDef, ctx: WorkflowContext,
                    engine: "WorkflowEngine") -> StepResult:
    """调用 LLM 生成内容"""
    prompt = ctx.interpolate(step.prompt)
    logger.info(f"[workflow] LLM 步骤: {step.id}, prompt 长度={len(prompt)}")

    if not engine.llm_router:
        return StepResult(step_id=step.id, success=False,
                          error="LLM Router 未配置")

    from ..llm import Message
    messages = [Message(role="user", content=prompt)]
    response = await engine.llm_router.chat(
        messages=messages,
        system_prompt="你是工作流引擎的 AI 助手。请根据指示完成任务，直接输出结果。",
    )

    output = response.content if response else ""
    return StepResult(step_id=step.id, success=bool(output), output=output)


# ==================== condition ====================

async def _exec_condition(step: StepDef, ctx: WorkflowContext,
                          engine: "WorkflowEngine") -> StepResult:
    """条件分支"""
    condition_met = ctx.evaluate_condition(step.if_expr)
    logger.info(f"[workflow] 条件判断: '{step.if_expr}' => {condition_met}")

    branch = step.then_steps if condition_met else step.else_steps
    branch_name = "then" if condition_met else "else"

    outputs = []
    for sub_step in branch:
        sub_result = await execute_step(sub_step, ctx, engine)
        ctx.set(sub_step.id, {"output": sub_result.output,
                               "success": sub_result.success})
        engine.current_run.step_results[sub_step.id] = sub_result
        outputs.append(str(sub_result.output))

        if not sub_result.success and sub_step.on_error == "fail":
            return StepResult(
                step_id=step.id, success=False,
                output=f"[{branch_name}] 子步骤 {sub_step.id} 失败",
                error=sub_result.error)

        if engine.current_run.status == RunStatus.PAUSED:
            return StepResult(step_id=step.id, success=True,
                              output=f"[{branch_name}] 在 {sub_step.id} 暂停等待审批")

    return StepResult(step_id=step.id, success=True,
                      output=f"[{branch_name}] " + "; ".join(outputs)[:1000])


# ==================== loop ====================

async def _exec_loop(step: StepDef, ctx: WorkflowContext,
                     engine: "WorkflowEngine") -> StepResult:
    """循环执行"""
    if step.over:
        items = ctx.resolve_list(step.over)
    elif step.count > 0:
        items = list(range(step.count))
    else:
        return StepResult(step_id=step.id, success=False,
                          error="循环未指定 over 或 count")

    logger.info(f"[workflow] 循环: {step.id}, {len(items)} 次迭代")
    all_outputs = []

    for idx, item in enumerate(items):
        ctx.set(step.loop_var, item)
        ctx.set("loop_index", idx)

        for sub_step in step.loop_steps:
            sub_result = await execute_step(sub_step, ctx, engine)
            ctx.set(sub_step.id, {"output": sub_result.output,
                                   "success": sub_result.success})
            engine.current_run.step_results[f"{sub_step.id}_{idx}"] = sub_result

            if not sub_result.success and sub_step.on_error == "fail":
                return StepResult(
                    step_id=step.id, success=False,
                    output=f"循环第 {idx} 次在 {sub_step.id} 失败",
                    error=sub_result.error)

            if engine.current_run.status == RunStatus.PAUSED:
                return StepResult(step_id=step.id, success=True,
                                  output=f"循环第 {idx} 次在 {sub_step.id} 暂停")

        all_outputs.append(f"#{idx}: OK")

    return StepResult(step_id=step.id, success=True,
                      output=f"循环完成 {len(items)} 次: " + ", ".join(all_outputs[-5:]))


# ==================== approval ====================

async def _exec_approval(step: StepDef, ctx: WorkflowContext,
                         engine: "WorkflowEngine") -> StepResult:
    """
    审批门控 - 暂停工作流等待人工确认

    机制：
    1. 生成 resume_token
    2. 将工作流状态设为 PAUSED
    3. 通过 notify_callback 发送审批请求
    4. 返回，引擎检测到 PAUSED 后停止执行
    5. 外部调用 engine.resume(token, approved) 继续
    """
    run = engine.current_run
    token = f"approve_{uuid.uuid4().hex[:16]}"
    run.resume_token = token
    run.paused_at_step = step.id
    run.status = RunStatus.PAUSED

    prompt = ctx.interpolate(step.approval_prompt)
    logger.info(f"[workflow] 审批门控: {step.id}, token={token}")

    if engine.notify_callback:
        msg = (
            f"⏸️ **工作流等待审批**\n\n"
            f"**工作流**: {run.workflow_name}\n"
            f"**步骤**: {step.id}\n"
            f"**说明**: {prompt}\n\n"
            f"请回复以下命令继续：\n"
            f"- `批准工作流 {token}` - 继续执行\n"
            f"- `拒绝工作流 {token}` - 取消工作流"
        )
        try:
            await engine.notify_callback(run.chat_id, msg)
        except Exception as e:
            logger.error(f"发送审批通知失败: {e}")

    engine.save_run(run)

    return StepResult(step_id=step.id, success=True,
                      output=f"等待审批 (token: {token})")


# ==================== notify ====================

async def _exec_notify(step: StepDef, ctx: WorkflowContext,
                       engine: "WorkflowEngine") -> StepResult:
    """发送通知消息"""
    message = ctx.interpolate(step.message)
    logger.info(f"[workflow] 通知: {step.id}")

    if engine.notify_callback and engine.current_run.chat_id:
        try:
            await engine.notify_callback(engine.current_run.chat_id, message)
            return StepResult(step_id=step.id, success=True, output="通知已发送")
        except Exception as e:
            return StepResult(step_id=step.id, success=False,
                              error=f"通知发送失败: {e}")

    return StepResult(step_id=step.id, success=True,
                      output=f"[通知] {message[:200]}")


# ==================== set_var ====================

async def _exec_set_var(step: StepDef, ctx: WorkflowContext,
                        engine: "WorkflowEngine") -> StepResult:
    """设置上下文变量"""
    var_name = ctx.interpolate(step.var_name)
    var_value = ctx.interpolate(step.var_value)

    try:
        parsed = json.loads(var_value)
        ctx.set(var_name, parsed)
    except (json.JSONDecodeError, TypeError):
        ctx.set(var_name, var_value)

    logger.info(f"[workflow] 设置变量: {var_name} = {str(var_value)[:100]}")
    return StepResult(step_id=step.id, success=True,
                      output=f"{var_name} = {str(var_value)[:200]}")


# ==================== parallel ====================

async def _exec_parallel(step: StepDef, ctx: WorkflowContext,
                         engine: "WorkflowEngine") -> StepResult:
    """并行执行多个步骤"""
    logger.info(f"[workflow] 并行执行: {step.id}, {len(step.parallel_steps)} 个子步骤")

    tasks = [execute_step(s, ctx, engine) for s in step.parallel_steps]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    outputs = []
    all_success = True
    for sub_step, result in zip(step.parallel_steps, results):
        if isinstance(result, Exception):
            sub_result = StepResult(step_id=sub_step.id, success=False,
                                    error=str(result))
        else:
            sub_result = result

        ctx.set(sub_step.id, {"output": sub_result.output,
                               "success": sub_result.success})
        engine.current_run.step_results[sub_step.id] = sub_result
        outputs.append(f"{sub_step.id}: {'OK' if sub_result.success else 'FAIL'}")
        if not sub_result.success:
            all_success = False

    return StepResult(step_id=step.id, success=all_success,
                      output="并行完成: " + ", ".join(outputs))
