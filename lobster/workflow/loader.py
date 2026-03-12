"""
YAML 工作流加载器

从 YAML 文件解析为 WorkflowDef 对象，支持：
- 步骤类型校验
- 嵌套步骤递归解析（condition.then/else, loop.steps, parallel.steps）
- 多工作流目录扫描
"""

import os
import logging
from pathlib import Path
from typing import Optional

import yaml

from .models import WorkflowDef, StepDef, StepType

logger = logging.getLogger("lobster.workflow")


def _parse_step(raw: dict) -> StepDef:
    """解析单个步骤定义"""
    step_id = raw.get("id", "unnamed")
    try:
        step_type = StepType(raw.get("type", ""))
    except ValueError:
        raise ValueError(f"步骤 '{step_id}' 的类型 '{raw.get('type')}' 无效，"
                         f"支持: {[t.value for t in StepType]}")

    step = StepDef(id=step_id, type=step_type)

    if step_type == StepType.TOOL_CALL:
        step.tool = raw.get("tool", "")
        step.args = raw.get("args", {})
        if not step.tool:
            raise ValueError(f"步骤 '{step_id}': tool_call 类型必须指定 tool")

    elif step_type == StepType.LLM:
        step.prompt = raw.get("prompt", "")
        step.model = raw.get("model", "")
        if not step.prompt:
            raise ValueError(f"步骤 '{step_id}': llm 类型必须指定 prompt")

    elif step_type == StepType.CONDITION:
        step.if_expr = raw.get("if", "")
        step.then_steps = [_parse_step(s) for s in raw.get("then", [])]
        step.else_steps = [_parse_step(s) for s in raw.get("else", [])]
        if not step.if_expr:
            raise ValueError(f"步骤 '{step_id}': condition 类型必须指定 if 表达式")

    elif step_type == StepType.LOOP:
        step.over = raw.get("over", "")
        step.count = raw.get("count", 0)
        step.loop_var = raw.get("loop_var", "item")
        step.loop_steps = [_parse_step(s) for s in raw.get("steps", [])]
        if not step.over and not step.count:
            raise ValueError(f"步骤 '{step_id}': loop 类型必须指定 over 或 count")

    elif step_type == StepType.APPROVAL:
        step.approval_prompt = raw.get("prompt", "需要人工确认才能继续")
        step.timeout = raw.get("timeout", 3600)

    elif step_type == StepType.NOTIFY:
        step.message = raw.get("message", "")
        if not step.message:
            raise ValueError(f"步骤 '{step_id}': notify 类型必须指定 message")

    elif step_type == StepType.SET_VAR:
        step.var_name = raw.get("var", "")
        step.var_value = raw.get("value", "")
        if not step.var_name:
            raise ValueError(f"步骤 '{step_id}': set_var 类型必须指定 var")

    elif step_type == StepType.PARALLEL:
        step.parallel_steps = [_parse_step(s) for s in raw.get("steps", [])]
        if not step.parallel_steps:
            raise ValueError(f"步骤 '{step_id}': parallel 类型必须指定 steps")

    step.on_error = raw.get("on_error", "fail")
    return step


def load_workflow(path: str) -> WorkflowDef:
    """从 YAML 文件加载工作流定义"""
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not raw or not isinstance(raw, dict):
        raise ValueError(f"无效的工作流文件: {path}")

    name = raw.get("name", Path(path).stem)
    steps = [_parse_step(s) for s in raw.get("steps", [])]

    return WorkflowDef(
        name=name,
        description=raw.get("description", ""),
        version=raw.get("version", "1.0"),
        triggers=raw.get("triggers", []),
        inputs=raw.get("inputs", {}),
        steps=steps,
        on_success=raw.get("on_success", ""),
        on_failure=raw.get("on_failure", ""),
    )


def scan_workflows(directory: str) -> dict[str, WorkflowDef]:
    """扫描目录下所有 .yaml/.yml 工作流文件"""
    workflows = {}
    if not os.path.isdir(directory):
        logger.warning(f"工作流目录不存在: {directory}")
        return workflows

    for filename in os.listdir(directory):
        if not filename.endswith((".yaml", ".yml")):
            continue
        filepath = os.path.join(directory, filename)
        try:
            wf = load_workflow(filepath)
            workflows[wf.name] = wf
            logger.info(f"加载工作流: {wf.name} ({filename})")
        except Exception as e:
            logger.error(f"加载工作流失败 {filename}: {e}")

    return workflows
