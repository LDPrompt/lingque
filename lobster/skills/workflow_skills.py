"""
工作流技能 - 让 Agent 能够管理和执行声明式工作流

技能列表：
- list_workflows: 查看所有可用工作流
- run_workflow: 启动一个工作流
- get_workflow_status: 查看工作流执行状态
- resume_workflow: 批准/拒绝暂停的工作流
- create_workflow: 通过 YAML 创建新工作流
"""

import os
import json
import logging

from .registry import registry

logger = logging.getLogger("lobster.workflow")

_engine = None


def set_workflow_engine(engine):
    """由 main.py 调用，注入工作流引擎实例"""
    global _engine
    _engine = engine


def get_workflow_engine():
    return _engine


# ==================== list_workflows ====================

@registry.register(
    name="list_workflows",
    description="列出所有可用的声明式工作流。返回工作流名称、描述、步骤数、所需输入参数。",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    risk_level="low",
    category="workflow",
)
async def list_workflows() -> str:
    if not _engine:
        return "工作流引擎未初始化"

    workflows = _engine.list_workflows()
    if not workflows:
        return "暂无可用工作流。可以通过 create_workflow 创建新工作流。"

    lines = ["📋 **可用工作流**\n"]
    for wf in workflows:
        inputs_str = ", ".join(wf["inputs"]) if wf["inputs"] else "无"
        lines.append(
            f"**{wf['name']}** (v{wf['version']})\n"
            f"  {wf['description']}\n"
            f"  步骤: {wf['steps']} | 输入参数: {inputs_str}"
        )
    return "\n\n".join(lines)


# ==================== run_workflow ====================

@registry.register(
    name="run_workflow",
    description=(
        "启动一个声明式工作流。工作流会按 YAML 定义的步骤自动执行，支持：\n"
        "- 调用任何已注册技能\n"
        "- LLM 智能处理\n"
        "- 条件分支和循环\n"
        "- 审批门控（暂停等待人工确认）\n"
        "- 步骤间自动数据传递\n\n"
        "先用 list_workflows 查看可用工作流和所需参数。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "workflow_name": {
                "type": "string",
                "description": "工作流名称",
            },
            "inputs": {
                "type": "string",
                "description": "输入参数，JSON 格式，如 {\"key\": \"value\"}。无参数传空 {}",
            },
        },
        "required": ["workflow_name"],
    },
    risk_level="medium",
    category="workflow",
)
async def run_workflow(workflow_name: str, inputs: str = "{}") -> str:
    if not _engine:
        return "工作流引擎未初始化"

    try:
        input_dict = json.loads(inputs) if isinstance(inputs, str) else inputs
    except json.JSONDecodeError:
        return f"输入参数 JSON 格式错误: {inputs}"

    try:
        run_id = await _engine.start(
            workflow_name=workflow_name,
            inputs=input_dict,
            chat_id=_engine.current_run.chat_id if _engine.current_run else "",
        )
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"启动工作流失败: {e}"

    run = _engine.get_run_status(run_id)
    if not run:
        return f"工作流已启动 (ID: {run_id})"

    status = run.get("status", "unknown")
    lines = [f"🔄 工作流 **{workflow_name}** {status}"]
    lines.append(f"ID: `{run_id}`")

    step_results = run.get("step_results", {})
    for step_id, result in step_results.items():
        icon = "✅" if result.get("success") else "❌"
        output = str(result.get("output", ""))[:150]
        lines.append(f"  {icon} {step_id}: {output}")

    if run.get("error"):
        lines.append(f"\n❌ 错误: {run['error']}")

    if status == "paused":
        lines.append(f"\n⏸️ 等待审批，token: `{run.get('resume_token', '')}`")

    return "\n".join(lines)


# ==================== get_workflow_status ====================

@registry.register(
    name="get_workflow_status",
    description="查看工作流执行状态。可指定运行 ID，或查看最近的运行记录。",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "工作流运行 ID。留空则列出最近的运行记录。",
            },
        },
        "required": [],
    },
    risk_level="low",
    category="workflow",
)
async def get_workflow_status(run_id: str = "") -> str:
    if not _engine:
        return "工作流引擎未初始化"

    if run_id:
        run = _engine.get_run_status(run_id)
        if not run:
            return f"未找到工作流: {run_id}"
        return _format_run(run)

    runs = _engine.list_runs()
    if not runs:
        return "暂无工作流运行记录"

    lines = ["📊 **最近工作流运行**\n"]
    for r in runs[:10]:
        status_icon = {"completed": "✅", "failed": "❌", "paused": "⏸️",
                       "running": "🔄", "cancelled": "🚫"}.get(r["status"], "📋")
        lines.append(
            f"{status_icon} **{r['workflow_name']}** - {r['status']}\n"
            f"  ID: `{r['id']}` | 创建: {r.get('created_at', '')[:19]}"
        )
    return "\n\n".join(lines)


# ==================== resume_workflow ====================

@registry.register(
    name="resume_workflow",
    description=(
        "批准或拒绝一个暂停等待审批的工作流。\n"
        "当工作流执行到审批步骤时会暂停并生成 token，使用此技能继续或取消。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "token": {
                "type": "string",
                "description": "审批 token（审批暂停时生成的）",
            },
            "approved": {
                "type": "boolean",
                "description": "true=批准继续执行，false=取消工作流",
            },
        },
        "required": ["token", "approved"],
    },
    risk_level="high",
    category="workflow",
)
async def resume_workflow(token: str, approved: bool = True) -> str:
    if not _engine:
        return "工作流引擎未初始化"
    return await _engine.resume(token, approved)


# ==================== create_workflow ====================

@registry.register(
    name="create_workflow",
    description=(
        "通过 YAML 内容创建新的工作流定义。工作流保存到 workflows/ 目录，立即可用。\n\n"
        "YAML 格式示例：\n"
        "```yaml\n"
        "name: my_workflow\n"
        "description: 我的工作流\n"
        "inputs:\n"
        "  param1:\n"
        "    type: string\n"
        "    description: 参数说明\n"
        "steps:\n"
        "  - id: step1\n"
        "    type: tool_call\n"
        "    tool: read_file\n"
        "    args:\n"
        "      path: ${inputs.param1}\n"
        "  - id: step2\n"
        "    type: llm\n"
        "    prompt: 分析以下内容：${step1.output}\n"
        "  - id: step3\n"
        "    type: notify\n"
        "    message: 分析结果：${step2.output}\n"
        "```\n\n"
        "步骤类型: tool_call, llm, condition, loop, approval, notify, set_var, parallel"
    ),
    parameters={
        "type": "object",
        "properties": {
            "yaml_content": {
                "type": "string",
                "description": "完整的 YAML 工作流定义内容",
            },
        },
        "required": ["yaml_content"],
    },
    risk_level="medium",
    category="workflow",
)
async def create_workflow(yaml_content: str) -> str:
    if not _engine:
        return "工作流引擎未初始化"

    import yaml as yaml_lib
    from ..workflow.loader import load_workflow

    try:
        raw = yaml_lib.safe_load(yaml_content)
        if not raw or not isinstance(raw, dict):
            return "YAML 内容无效"
        name = raw.get("name", "unnamed_workflow")
    except Exception as e:
        return f"YAML 解析失败: {e}"

    filepath = os.path.join(_engine._workflows_dir, f"{name}.yaml")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(yaml_content)

    try:
        wf_def = load_workflow(filepath)
        _engine.register_workflow(wf_def)
        return (
            f"✅ 工作流 **{name}** 已创建\n"
            f"步骤数: {len(wf_def.steps)}\n"
            f"输入参数: {list(wf_def.inputs.keys()) or '无'}\n"
            f"文件: {filepath}\n\n"
            f"使用 `run_workflow` 启动执行。"
        )
    except Exception as e:
        os.remove(filepath)
        return f"工作流校验失败: {e}"


def _format_run(run: dict) -> str:
    """格式化单个运行记录"""
    status_icon = {"completed": "✅", "failed": "❌", "paused": "⏸️",
                   "running": "🔄", "cancelled": "🚫"}.get(run["status"], "📋")
    lines = [
        f"{status_icon} **{run['workflow_name']}** - {run['status']}",
        f"ID: `{run['id']}`",
        f"创建: {run.get('created_at', '')[:19]}",
    ]

    step_results = run.get("step_results", {})
    if step_results:
        lines.append("\n**步骤结果:**")
        for step_id, result in step_results.items():
            icon = "✅" if result.get("success") else "❌"
            output = str(result.get("output", ""))[:150]
            duration = result.get("duration_ms", 0)
            lines.append(f"  {icon} {step_id} ({duration}ms): {output}")

    if run.get("error"):
        lines.append(f"\n❌ 错误: {run['error']}")
    if run.get("resume_token"):
        lines.append(f"\n⏸️ 审批 token: `{run['resume_token']}`")

    return "\n".join(lines)
