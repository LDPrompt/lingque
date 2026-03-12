"""
🐦 灵雀 - P3 技能自动生成

功能:
- 用户描述需求
- LLM 生成技能代码
- 动态注册到技能库
"""

import logging
import re
import ast
import asyncio
import string
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from .registry import registry, register, SkillResult

logger = logging.getLogger("lingque.skills.generator")


SKILL_TEMPLATE = '''"""
🐦 灵雀 - 自动生成的技能: $name
生成时间: $timestamp
描述: $description
"""

import logging
from lobster.skills.registry import register, SkillResult

logger = logging.getLogger("lingque.skills.generated")


@register(
    name="$name",
    description="""$description""",
    parameters=$params,
    risk_level="$risk_level",
)
async def $func_name($func_params) -> SkillResult:
    """
    $description
    """
    try:
$code
    except Exception as e:
        logger.error(f"技能 $name 执行失败: {e}")
        return SkillResult(success=False, error=str(e))
'''


GENERATION_PROMPT = """你是一个 Python 技能代码生成专家。根据用户的需求描述，生成一个可以直接执行的技能函数。

用户需求:
{requirement}

技能函数的要求:
1. 必须是 async 函数
2. 必须返回 SkillResult(success=True/False, data=结果字符串, error=错误信息)
3. 代码缩进使用 8 个空格（会被放入 try 块内）
4. 可以使用: httpx, json, datetime, re, os, subprocess, asyncio
5. 不要导入其他库（除非是标准库）

请以 JSON 格式返回:
{{
  "name": "技能名称（英文snake_case）",
  "description": "技能描述（中文）",
  "params": {{"type": "object", "properties": {{"param1": {{"type": "string", "description": "参数1描述"}}}}, "required": ["param1"]}},
  "risk_level": "low/medium/high",
  "func_params": "param1: str, param2: int = 10",
  "code": "        # 函数实现代码（注意8空格缩进）\\n        result = do_something()\\n        return SkillResult(success=True, data=str(result))"
}}

只返回 JSON，不要其他内容。"""


@dataclass
class GeneratedSkill:
    """生成的技能"""
    name: str
    description: str
    code: str
    file_path: Path
    success: bool
    error: str = ""


class SkillGenerator:
    """
    技能自动生成器

    根据用户的自然语言描述，
    自动生成并注册新技能。

    用法:
        generator = SkillGenerator(llm_router, skills_dir)
        skill = await generator.generate("查询某个城市的天气")
        if skill.success:
            print(f"已生成技能: {skill.name}")
    """

    def __init__(
        self,
        llm_router,
        skills_dir: str | Path = "./lobster/skills/generated",
    ):
        self.llm_router = llm_router
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    async def generate(self, requirement: str) -> GeneratedSkill:
        """
        根据需求生成技能

        Args:
            requirement: 需求描述

        Returns:
            生成的技能信息
        """
        import json

        prompt = GENERATION_PROMPT.format(requirement=requirement)

        try:
            from ..llm.base import Message
            response = await self.llm_router.chat(
                messages=[Message(role="user", content=prompt)],
                temperature=0.3,
            )

            text = (response.content or "").strip()
            data = None
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                decoder = json.JSONDecoder()
                for i, ch in enumerate(text):
                    if ch == '{':
                        try:
                            data, _ = decoder.raw_decode(text, i)
                            break
                        except json.JSONDecodeError:
                            continue
            if data is None:
                raise ValueError("未找到有效 JSON")

            name = data.get("name", "generated_skill")
            description = data.get("description", requirement)
            params = data.get("params", {"type": "object", "properties": {}, "required": []})
            risk_level = data.get("risk_level", "medium")
            func_params = data.get("func_params", "")
            code = data.get("code", "        return SkillResult(success=False, error='未实现')")

            # 验证技能名称
            name = self._sanitize_name(name)
            func_name = name

            full_code = string.Template(SKILL_TEMPLATE).safe_substitute(
                name=name,
                description=description,
                params=params,
                risk_level=risk_level,
                func_name=func_name,
                func_params=func_params,
                code=code,
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )

            try:
                tree = ast.parse(full_code)
            except SyntaxError as e:
                raise ValueError(f"生成的代码语法错误: {e}")

            safety_err = self._check_code_safety(tree, full_code)
            if safety_err:
                raise ValueError(safety_err)

            # 保存到文件
            file_path = self.skills_dir / f"{name}.py"
            file_path.write_text(full_code, encoding="utf-8")

            # 动态加载
            try:
                self._load_skill(file_path)
                logger.info(f"已生成并加载技能: {name}")
            except Exception as e:
                logger.warning(f"技能加载失败 (已保存到文件): {e}")

            return GeneratedSkill(
                name=name,
                description=description,
                code=full_code,
                file_path=file_path,
                success=True,
            )

        except Exception as e:
            logger.error(f"技能生成失败: {e}")
            return GeneratedSkill(
                name="",
                description="",
                code="",
                file_path=Path(),
                success=False,
                error=str(e),
            )

    def _sanitize_name(self, name: str) -> str:
        """清理技能名称，加 gen_ 前缀防止覆盖内置技能"""
        name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        if name and name[0].isdigit():
            name = "skill_" + name
        name = name.lower()
        if not name.startswith("gen_"):
            name = f"gen_{name}"
        return name

    _FORBIDDEN_IMPORTS = {
        "subprocess", "shutil", "ctypes", "socket",
        "multiprocessing", "signal", "resource", "pty",
    }
    _FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "globals", "locals", "getattr"}

    def _check_code_safety(self, tree: ast.AST, code: str) -> str | None:
        """AST 级别安全检查，阻止危险导入和调用"""
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif node.module:
                    names = [node.module.split(".")[0]]
                for mod in names:
                    if mod in self._FORBIDDEN_IMPORTS:
                        return f"安全限制: 生成的代码禁止导入 '{mod}'"

            if isinstance(node, ast.Call):
                func = node.func
                fname = None
                if isinstance(func, ast.Name):
                    fname = func.id
                elif isinstance(func, ast.Attribute):
                    fname = func.attr
                if fname in self._FORBIDDEN_CALLS:
                    return f"安全限制: 生成的代码禁止调用 '{fname}()'"

            if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
                if node.attr not in ("__init__", "__str__", "__repr__", "__len__", "__name__"):
                    return f"安全限制: 生成的代码禁止访问 dunder 属性 '{node.attr}'"
        return None

    def _load_skill(self, file_path: Path):
        """动态加载技能模块"""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            file_path.stem,
            file_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    def list_generated(self) -> list[str]:
        """列出所有生成的技能"""
        skills = []
        for f in self.skills_dir.glob("*.py"):
            if not f.name.startswith("__"):
                skills.append(f.stem)
        return skills

    def delete_skill(self, name: str) -> bool:
        """删除生成的技能"""
        file_path = self.skills_dir / f"{name}.py"
        if file_path.exists():
            file_path.unlink()
            if name in registry._skills:
                del registry._skills[name]
            logger.info(f"已删除技能: {name}")
            return True
        return False


# ==================== 技能注册 ====================

_generator: Optional[SkillGenerator] = None


def get_skill_generator(llm_router) -> SkillGenerator:
    """获取全局技能生成器"""
    global _generator
    if _generator is None:
        _generator = SkillGenerator(llm_router)
    return _generator


@register(
    name="generate_skill",
    description="根据自然语言描述自动生成新技能。例如：'查询股票价格'、'下载指定URL的图片'",
    parameters={
        "type": "object",
        "properties": {
            "requirement": {"type": "string", "description": "技能需求描述"},
        },
        "required": ["requirement"],
    },
    risk_level="high",
)
async def generate_skill(requirement: str) -> SkillResult:
    """自动生成技能"""
    if _generator is None:
        return SkillResult(
            success=False,
            error="技能生成器未初始化，请先配置 LLM"
        )

    skill = await _generator.generate(requirement)

    if skill.success:
        return SkillResult(
            success=True,
            data=f"✅ 技能生成成功!\n\n"
                 f"**名称**: {skill.name}\n"
                 f"**描述**: {skill.description}\n"
                 f"**文件**: {skill.file_path}\n\n"
                 f"技能已自动加载，可以直接使用。"
        )
    else:
        return SkillResult(success=False, error=skill.error)


@register(
    name="list_generated_skills",
    description="列出所有自动生成的技能",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    risk_level="low",
)
async def list_generated_skills() -> SkillResult:
    """列出生成的技能"""
    if _generator is None:
        return SkillResult(success=True, data="技能生成器未初始化")

    skills = _generator.list_generated()
    if not skills:
        return SkillResult(success=True, data="还没有自动生成的技能")

    return SkillResult(
        success=True,
        data=f"📝 自动生成的技能 ({len(skills)} 个):\n\n" +
             "\n".join(f"- {s}" for s in skills)
    )


@register(
    name="delete_generated_skill",
    description="删除一个自动生成的技能",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "要删除的技能名称"},
        },
        "required": ["name"],
    },
    risk_level="medium",
)
async def delete_generated_skill(name: str) -> SkillResult:
    """删除生成的技能"""
    if _generator is None:
        return SkillResult(success=False, error="技能生成器未初始化")

    if _generator.delete_skill(name):
        return SkillResult(success=True, data=f"✅ 已删除技能: {name}")
    else:
        return SkillResult(success=False, error=f"技能不存在: {name}")
