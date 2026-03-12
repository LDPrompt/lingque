"""
工作流数据模型

WorkflowDef  - YAML 解析后的工作流定义
StepDef      - 单个步骤定义
WorkflowRun  - 运行时实例（含状态、上下文、审批 token）
StepResult   - 步骤执行结果
"""

import uuid
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Optional


class RunStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"          # 等待审批
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepType(Enum):
    TOOL_CALL = "tool_call"
    LLM = "llm"
    CONDITION = "condition"
    LOOP = "loop"
    APPROVAL = "approval"
    NOTIFY = "notify"
    SET_VAR = "set_var"
    PARALLEL = "parallel"


@dataclass
class StepDef:
    """步骤定义（从 YAML 解析）"""
    id: str
    type: StepType
    # tool_call
    tool: str = ""
    args: dict = field(default_factory=dict)
    # llm
    prompt: str = ""
    model: str = ""
    # condition
    if_expr: str = ""
    then_steps: list["StepDef"] = field(default_factory=list)
    else_steps: list["StepDef"] = field(default_factory=list)
    # loop
    over: str = ""               # 表达式，解析为列表
    count: int = 0               # 固定次数循环
    loop_var: str = "item"       # 循环变量名
    loop_steps: list["StepDef"] = field(default_factory=list)
    # approval
    approval_prompt: str = ""
    timeout: int = 3600
    # notify
    message: str = ""
    # set_var
    var_name: str = ""
    var_value: str = ""
    # parallel
    parallel_steps: list["StepDef"] = field(default_factory=list)
    # 通用
    on_error: str = "fail"       # fail / continue / retry


@dataclass
class StepResult:
    """步骤执行结果"""
    step_id: str
    success: bool
    output: Any = ""
    error: str = ""
    duration_ms: int = 0


@dataclass
class WorkflowDef:
    """工作流定义（从 YAML 解析）"""
    name: str
    description: str = ""
    version: str = "1.0"
    triggers: list[dict] = field(default_factory=list)
    inputs: dict[str, dict] = field(default_factory=dict)
    steps: list[StepDef] = field(default_factory=list)
    on_success: str = ""
    on_failure: str = ""


@dataclass
class WorkflowRun:
    """工作流运行实例"""
    id: str = field(default_factory=lambda: f"wf_{uuid.uuid4().hex[:12]}")
    workflow_name: str = ""
    status: RunStatus = RunStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    # 执行状态
    current_step_idx: int = 0
    current_step_id: str = ""
    step_results: dict[str, StepResult] = field(default_factory=dict)
    # 上下文变量（步骤间数据传递）
    variables: dict[str, Any] = field(default_factory=dict)
    # 审批门控
    resume_token: str = ""
    paused_at_step: str = ""
    # 输入参数
    inputs: dict[str, Any] = field(default_factory=dict)
    # 错误信息
    error: str = ""
    # 关联
    chat_id: str = ""
    user_id: str = ""

    def to_dict(self) -> dict:
        """序列化为可持久化的字典"""
        return {
            "id": self.id,
            "workflow_name": self.workflow_name,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "current_step_idx": self.current_step_idx,
            "current_step_id": self.current_step_id,
            "step_results": {
                k: {"step_id": v.step_id, "success": v.success,
                     "output": str(v.output)[:2000], "error": v.error,
                     "duration_ms": v.duration_ms}
                for k, v in self.step_results.items()
            },
            "variables": {k: str(v)[:1000] for k, v in self.variables.items()},
            "resume_token": self.resume_token,
            "paused_at_step": self.paused_at_step,
            "inputs": self.inputs,
            "error": self.error,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
        }
