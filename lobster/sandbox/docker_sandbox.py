"""
🐦 灵雀 - P3 Docker 沙箱

功能:
- 代码在容器内执行
- 完全隔离宿主系统
- 资源限制 (CPU/内存/时间)
- 自动清理容器
"""

import asyncio
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger("lingque.sandbox.docker")

# 延迟导入
docker = None


def _ensure_docker():
    """确保 docker-py 已安装"""
    global docker
    if docker is None:
        try:
            import docker as _docker
            docker = _docker
        except ImportError:
            raise ImportError("请安装 docker-py: pip install docker")


@dataclass
class ExecutionResult:
    """执行结果"""
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float  # 秒
    error: str = ""


class DockerSandbox:
    """
    Docker 沙箱执行器

    在隔离的 Docker 容器中执行代码，
    防止恶意代码影响宿主系统。

    用法:
        sandbox = DockerSandbox()
        result = await sandbox.execute_python("print('Hello')")
    """

    # 预置镜像
    IMAGES = {
        "python": "python:3.12-slim",
        "node": "node:20-slim",
        "bash": "alpine:latest",
    }

    def __init__(
        self,
        memory_limit: str = "256m",  # 内存限制
        cpu_period: int = 100000,     # CPU 周期
        cpu_quota: int = 50000,       # CPU 配额 (50%)
        timeout: int = 30,            # 执行超时（秒）
        network_disabled: bool = True,  # 禁用网络
    ):
        self.memory_limit = memory_limit
        self.cpu_period = cpu_period
        self.cpu_quota = cpu_quota
        self.timeout = timeout
        self.network_disabled = network_disabled

        self._client = None

    def _get_client(self):
        """获取 Docker 客户端"""
        _ensure_docker()
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    async def execute_python(
        self,
        code: str,
        packages: Optional[list[str]] = None,
    ) -> ExecutionResult:
        """
        在 Docker 中执行 Python 代码

        Args:
            code: Python 代码
            packages: 需要安装的包

        Returns:
            执行结果
        """
        # 构建完整脚本
        script = code
        _PKG_RE = __import__("re").compile(r'^[a-zA-Z0-9._-]+([<>=!~]+[a-zA-Z0-9.*]+)?$')
        if packages:
            safe_pkgs = [p for p in packages if _PKG_RE.match(p)]
            if len(safe_pkgs) != len(packages):
                rejected = set(packages) - set(safe_pkgs)
                logger.warning(f"已拒绝非法包名: {rejected}")
            install_cmd = f"pip install -q {' '.join(safe_pkgs)} && " if safe_pkgs else ""
        else:
            install_cmd = ""

        return await self._execute(
            image=self.IMAGES["python"],
            command=f"sh -c '{install_cmd}python -c \"{self._escape_code(code)}\"'",
        )

    async def execute_javascript(self, code: str) -> ExecutionResult:
        """在 Docker 中执行 JavaScript 代码"""
        return await self._execute(
            image=self.IMAGES["node"],
            command=f"node -e \"{self._escape_code(code)}\"",
        )

    async def execute_bash(self, command: str) -> ExecutionResult:
        """在 Docker 中执行 Bash 命令"""
        return await self._execute(
            image=self.IMAGES["bash"],
            command=f"sh -c \"{self._escape_code(command)}\"",
        )

    async def execute_file(
        self,
        file_path: str | Path,
        language: str = "python",
    ) -> ExecutionResult:
        """
        在 Docker 中执行文件

        Args:
            file_path: 文件路径
            language: 语言 (python/node/bash)

        Returns:
            执行结果
        """
        file_path = Path(file_path)
        if not file_path.exists():
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=-1,
                execution_time=0,
                error=f"文件不存在: {file_path}",
            )

        code = file_path.read_text(encoding="utf-8")

        if language == "python":
            return await self.execute_python(code)
        elif language in ("javascript", "node", "js"):
            return await self.execute_javascript(code)
        elif language in ("bash", "shell", "sh"):
            return await self.execute_bash(code)
        else:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=-1,
                execution_time=0,
                error=f"不支持的语言: {language}",
            )

    async def _execute(self, image: str, command: str) -> ExecutionResult:
        """执行容器"""
        import time

        def _run_container():
            client = self._get_client()
            container = None
            start_time = time.time()

            try:
                # 创建容器
                container = client.containers.run(
                    image=image,
                    command=command,
                    detach=True,
                    mem_limit=self.memory_limit,
                    cpu_period=self.cpu_period,
                    cpu_quota=self.cpu_quota,
                    network_disabled=self.network_disabled,
                    remove=False,  # 先不删除，需要获取日志
                )

                # 等待完成
                result = container.wait(timeout=self.timeout)
                exit_code = result.get("StatusCode", -1)

                # 获取输出
                stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
                stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

                execution_time = time.time() - start_time

                return ExecutionResult(
                    success=(exit_code == 0),
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    execution_time=execution_time,
                )

            except docker.errors.ContainerError as e:
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr=str(e),
                    exit_code=e.exit_status,
                    execution_time=time.time() - start_time,
                    error=f"容器错误: {e}",
                )

            except Exception as e:
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr="",
                    exit_code=-1,
                    execution_time=time.time() - start_time,
                    error=str(e),
                )

            finally:
                # 清理容器
                if container:
                    try:
                        container.remove(force=True)
                    except Exception:
                        pass

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _run_container)

    def _escape_code(self, code: str) -> str:
        """转义代码中的特殊字符（双引号上下文和单引号上下文通用）"""
        code = code.replace("\\", "\\\\")
        code = code.replace("'", "'\\''")
        code = code.replace('"', '\\"')
        code = code.replace("$", "\\$")
        code = code.replace("`", "\\`")
        code = code.replace("\n", "\\n")
        return code

    async def pull_images(self):
        """预拉取所有镜像"""
        client = self._get_client()
        for name, image in self.IMAGES.items():
            logger.info(f"拉取镜像: {image}")
            try:
                client.images.pull(image)
                logger.info(f"镜像就绪: {image}")
            except Exception as e:
                logger.error(f"拉取镜像失败: {image} - {e}")

    def is_available(self) -> bool:
        """检查 Docker 是否可用"""
        try:
            client = self._get_client()
            client.ping()
            return True
        except Exception:
            return False


# ==================== 技能注册 ====================

_sandbox: Optional[DockerSandbox] = None


def get_sandbox() -> DockerSandbox:
    """获取全局沙箱实例"""
    global _sandbox
    if _sandbox is None:
        _sandbox = DockerSandbox()
    return _sandbox


# 注册技能
from ..skills.registry import register, SkillResult


@register(
    name="sandbox_python",
    description="在 Docker 沙箱中安全执行 Python 代码（隔离环境，不影响宿主系统）",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "要执行的 Python 代码"},
            "packages": {"type": "string", "description": "需要安装的包（可选，逗号分隔）"},
        },
        "required": ["code"],
    },
    risk_level="medium",
)
async def sandbox_python(code: str, packages: str = "") -> SkillResult:
    """在 Docker 沙箱执行 Python"""
    sandbox = get_sandbox()

    if not sandbox.is_available():
        return SkillResult(
            success=False,
            error="Docker 不可用，请确保 Docker 已安装并正在运行"
        )

    pkg_list = [p.strip() for p in packages.split(",") if p.strip()] if packages else None

    result = await sandbox.execute_python(code, packages=pkg_list)

    output = []
    if result.stdout:
        output.append(f"**输出**:\n```\n{result.stdout}\n```")
    if result.stderr:
        output.append(f"**错误**:\n```\n{result.stderr}\n```")
    output.append(f"**退出码**: {result.exit_code}")
    output.append(f"**执行时间**: {result.execution_time:.2f}s")

    return SkillResult(
        success=result.success,
        data="\n\n".join(output),
        error=result.error if not result.success else "",
    )


@register(
    name="sandbox_bash",
    description="在 Docker 沙箱中安全执行 Bash 命令（隔离环境）",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 Bash 命令"},
        },
        "required": ["command"],
    },
    risk_level="medium",
)
async def sandbox_bash(command: str) -> SkillResult:
    """在 Docker 沙箱执行 Bash"""
    sandbox = get_sandbox()

    if not sandbox.is_available():
        return SkillResult(
            success=False,
            error="Docker 不可用"
        )

    result = await sandbox.execute_bash(command)

    output = []
    if result.stdout:
        output.append(f"**输出**:\n```\n{result.stdout}\n```")
    if result.stderr:
        output.append(f"**错误**:\n```\n{result.stderr}\n```")

    return SkillResult(
        success=result.success,
        data="\n\n".join(output) if output else "命令执行完成（无输出）",
        error=result.error if not result.success else "",
    )
