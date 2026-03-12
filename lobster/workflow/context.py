"""
工作流运行时上下文

负责：
- 变量存储和步骤间数据传递
- ${variable} 模板插值
- 条件表达式求值
"""

import re
import json
import logging
from typing import Any

logger = logging.getLogger("lobster.workflow")

_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


class WorkflowContext:
    """工作流执行上下文 - 管理变量和表达式求值"""

    def __init__(self, variables: dict[str, Any] | None = None):
        self._vars: dict[str, Any] = variables or {}

    def set(self, key: str, value: Any):
        self._vars[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._vars.get(key, default)

    def get_all(self) -> dict[str, Any]:
        return dict(self._vars)

    def update(self, data: dict[str, Any]):
        self._vars.update(data)

    # ==================== 模板插值 ====================

    def interpolate(self, template: str) -> str:
        """
        将模板中的 ${xxx} 替换为实际值

        支持：
          ${var}                 - 直接变量
          ${step_id.output}     - 步骤结果的 output
          ${step_id.success}    - 步骤结果的 success
          ${inputs.key}         - 输入参数
        """
        if not isinstance(template, str):
            return template

        def _replace(match):
            expr = match.group(1).strip()
            val = self._resolve(expr)
            if val is None:
                return match.group(0)
            return str(val)

        return _VAR_PATTERN.sub(_replace, template)

    def interpolate_dict(self, d: dict) -> dict:
        """递归插值字典中的所有字符串值"""
        result = {}
        for k, v in d.items():
            if isinstance(v, str):
                result[k] = self.interpolate(v)
            elif isinstance(v, dict):
                result[k] = self.interpolate_dict(v)
            elif isinstance(v, list):
                result[k] = [
                    self.interpolate(item) if isinstance(item, str) else item
                    for item in v
                ]
            else:
                result[k] = v
        return result

    def _resolve(self, expr: str) -> Any:
        """解析点号路径表达式：step_id.output, inputs.key 等"""
        parts = expr.split(".")
        current = self._vars

        for part in parts:
            if isinstance(current, dict):
                if part in current:
                    current = current[part]
                else:
                    return None
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return None
        return current

    # ==================== 条件求值 ====================

    def evaluate_condition(self, expr: str) -> bool:
        """
        求值条件表达式

        支持：
          ${var} == "value"
          ${var} != "value"
          ${var} > 0
          ${var} contains "text"
          ${step.success} == true
          not ${var}
          ${var}                   - truthy 判断
        """
        expr = expr.strip()

        interpolated = self.interpolate(expr)

        if " contains " in interpolated:
            left, right = interpolated.split(" contains ", 1)
            return right.strip().strip('"').strip("'") in left.strip()

        if " not contains " in interpolated:
            left, right = interpolated.split(" not contains ", 1)
            return right.strip().strip('"').strip("'") not in left.strip()

        for op in ("==", "!=", ">=", "<=", ">", "<"):
            if op in interpolated:
                left, right = interpolated.split(op, 1)
                left = self._coerce(left.strip())
                right = self._coerce(right.strip())
                if op == "==":
                    return left == right
                elif op == "!=":
                    return left != right
                elif op == ">":
                    return float(left) > float(right)
                elif op == "<":
                    return float(left) < float(right)
                elif op == ">=":
                    return float(left) >= float(right)
                elif op == "<=":
                    return float(left) <= float(right)

        if interpolated.startswith("not "):
            val = self._coerce(interpolated[4:].strip())
            return not val

        return bool(self._coerce(interpolated))

    def resolve_list(self, expr: str) -> list:
        """解析表达式为列表（用于 loop.over）"""
        interpolated = self.interpolate(expr)

        if isinstance(interpolated, list):
            return interpolated

        val = self._resolve(expr.strip().lstrip("${").rstrip("}"))
        if isinstance(val, list):
            return val

        try:
            parsed = json.loads(interpolated)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

        if "," in str(interpolated):
            return [item.strip() for item in str(interpolated).split(",")]

        return [interpolated]

    @staticmethod
    def _coerce(value: str) -> Any:
        """将字符串智能转换为 Python 类型"""
        v = str(value).strip().strip('"').strip("'")
        if v.lower() == "true":
            return True
        if v.lower() == "false":
            return False
        if v.lower() == "none" or v.lower() == "null":
            return None
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            pass
        return v
