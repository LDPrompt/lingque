"""
🐦 灵雀 - 代码执行技能 (安全加固版)
"""

import ast
import asyncio
import tempfile
import os
import re
from .registry import registry

import logging
logger = logging.getLogger("lingque.skills.code_runner")

# ============================================================
# 安全配置
# ============================================================

SAFE_COMMAND_PREFIXES = [
    "ls", "cat", "head", "tail", "grep", "egrep", "fgrep",
    "find", "wc", "pwd", "whoami", "date", "df", "free",
    "top -bn1", "ps", "echo", "file", "stat", "du",
    "which", "env", "uname", "hostname", "uptime",
    "pip list", "pip show", "pip3 list", "pip3 show",
    "python3 --version", "python3 -c",
    "docker ps", "docker images", "docker logs",
    "systemctl status",
    "netstat", "ss",
    "node -v", "node --version", "npm -v", "npm list",
    "git status", "git log", "git branch", "git diff", "git show",
    "rg", "ag", "tree", "realpath", "basename", "dirname",
    "sort", "uniq", "diff", "md5sum", "sha256sum",
    "id", "groups", "lsb_release", "arch",
]

DANGEROUS_PATTERNS = [
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=", "> /dev/",
    ":(){ :|:& };:", "chmod 777 /", "chown root",
    "curl | sh", "curl | bash", "wget | sh", "wget | bash",
    "curl -o- | sh", "wget -O- | sh",
    "> /etc/", ">> /etc/", "tee /etc/",
    "passwd", "/etc/shadow", "sudoers",
    "ssh-keygen -f", "authorized_keys",
    "iptables -F", "iptables -X", "ufw disable",
    "systemctl stop lingque", "systemctl disable lingque",
    "kill -9 1", "reboot", "shutdown", "halt", "poweroff", "init 0",
    "python3 -c \"import os; os.system",
    "nc -e", "ncat -e",
]

SENSITIVE_PATH_KEYWORDS = [
    ".env", ".git/config", ".ssh/", "/etc/shadow",
    "id_rsa", "id_ed25519", "authorized_keys",
    ".bash_history", ".zsh_history",
    "config.json", "secrets", "credentials",
    ".credential_key", "credentials.enc",
    "api_key", "apikey", "secret_key", "access_token",
    "lingque.json", "token.json",
    "cookies/",
]

def _check_sensitive_paths(command: str) -> str | None:
    cmd_lower = command.lower()
    for keyword in SENSITIVE_PATH_KEYWORDS:
        if keyword.lower() in cmd_lower:
            return f"安全限制: 命令涉及敏感路径 '{keyword}'"
    return None


def _check_dangerous_patterns(command: str) -> str | None:
    cmd_lower = command.lower()
    for pattern in DANGEROUS_PATTERNS:
        if pattern.lower() in cmd_lower:
            return f"安全限制: 拒绝执行危险命令模式 '{pattern}'"

    # 管道到 shell 解释器（无论上游是什么命令都拦截）
    if "|" in cmd_lower:
        parts = [p.strip() for p in cmd_lower.split("|")]
        shell_interpreters = ("bash", "sh", "zsh", "python", "python3", "perl", "ruby", "node")
        for i, part in enumerate(parts):
            tokens = part.split()
            if i > 0 and tokens and tokens[0] in shell_interpreters:
                return "安全限制: 拒绝通过管道将数据传给脚本解释器执行"
    return None


def _is_safe_command(command: str) -> bool:
    cmd_stripped = command.strip()
    if ">>" in cmd_stripped or re.search(r'[^|]>[^|]', cmd_stripped):
        return False
    if "$(" in cmd_stripped or "`" in cmd_stripped or "<(" in cmd_stripped:
        return False
    for sep in ["&&", "||", ";"]:
        if sep in cmd_stripped:
            return all(_is_safe_command(part) for part in cmd_stripped.split(sep))
    if "|" in cmd_stripped:
        return all(_is_safe_command(part) for part in cmd_stripped.split("|"))
    for prefix in SAFE_COMMAND_PREFIXES:
        if cmd_stripped.startswith(prefix):
            return True
    return False


async def _execute_shell(command: str, timeout: int) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"命令超时 ({timeout}s)"

        result = ""
        if stdout:
            result += stdout.decode("utf-8", errors="replace")
        if stderr:
            result += "\n[stderr] " + stderr.decode("utf-8", errors="replace")

        if len(result) > 10000:
            result = result[:10000] + "\n...(输出已截断)"

        return result.strip() if result.strip() else "(无输出)"
    except Exception as e:
        return f"命令执行失败: {e}"


# ============================================================
# 技能注册
# ============================================================

@registry.register(
    name="run_query",
    description=(
        "执行只读查询命令（ls、cat、grep、find、ps、docker ps 等），不会修改系统。\n"
        "⚠️ 注意：如果需要查询多项信息，请用 && 或 ; 合并到一条命令中执行，"
        "不要多次调用此工具。例如: 'ps aux | head -5 && df -h && free -m'"
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "只读查询命令（可用 && 合并多条）"},
            "timeout": {"type": "integer", "description": "超时秒数", "default": 15},
        },
        "required": ["command"],
    },
    risk_level="low",
    category="code",
)
async def run_query(command: str, timeout: int = 15) -> str:
    """执行只读查询命令，白名单模式"""
    blocked = _check_sensitive_paths(command)
    if blocked:
        return blocked
    danger = _check_dangerous_patterns(command)
    if danger:
        return danger

    if not _is_safe_command(command):
        return (
            f"安全限制: '{command}' 不在只读命令白名单中。\n"
            f"如需执行修改类命令，请使用 run_command 工具。"
        )

    return await _execute_shell(command, timeout)


@registry.register(
    name="run_command",
    description="执行可能修改系统的 Shell 命令（安装软件、写入文件、管理服务等）。高风险，需要用户确认。",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell 命令"},
            "timeout": {"type": "integer", "description": "超时秒数", "default": 30},
        },
        "required": ["command"],
    },
    risk_level="high",
    category="code",
)
async def run_command(command: str, timeout: int = 30) -> str:
    """执行修改类命令，需要用户确认"""
    blocked = _check_dangerous_patterns(command)
    if blocked:
        return blocked

    blocked = _check_sensitive_paths(command)
    if blocked:
        return blocked

    return await _execute_shell(command, timeout)


_FORBIDDEN_MODULES = frozenset({
    "subprocess", "shutil", "ctypes", "importlib",
    "signal", "pty", "resource", "multiprocessing",
    "webbrowser", "http.server", "xmlrpc", "ftplib",
})

_FORBIDDEN_BUILTINS = frozenset({
    "exec", "eval", "compile", "__import__", "globals",
    "locals", "getattr", "setattr", "delattr", "breakpoint",
})

_SENSITIVE_PATH_KEYWORDS = frozenset({
    ".env", "/etc/shadow", "/etc/passwd", ".ssh/",
    "authorized_keys", "id_rsa", "server.env",
})


_DUNDER_BLACKLIST = frozenset({
    "__class__", "__bases__", "__subclasses__", "__mro__",
    "__globals__", "__builtins__", "__code__", "__func__",
    "__self__", "__dict__", "__init__", "__new__",
    "__import__", "__loader__", "__spec__",
})

_DANGEROUS_STRING_PATTERNS = [
    "__class__", "__bases__", "__subclasses__", "__globals__",
    "__builtins__", "__mro__", "__import__",
    "os.system", "os.popen", "os.exec",
    "open('/etc", "open(\"/etc", "open('C:\\\\",
]


def _check_python_safety(code: str) -> str | None:
    """AST + 字符串 + dunder 链三层检查"""
    code_lower = code.lower()
    for kw in _SENSITIVE_PATH_KEYWORDS:
        if kw in code_lower:
            return f"安全限制: 代码尝试访问敏感路径 '{kw}'"

    for pattern in _DANGEROUS_STRING_PATTERNS:
        if pattern in code:
            return f"安全限制: 代码包含危险模式 '{pattern}'"

    try:
        tree = ast.parse(code)
    except SyntaxError:
        for mod in _FORBIDDEN_MODULES:
            if mod in code:
                return f"安全限制: 代码疑似使用禁止模块 '{mod}'（且存在语法错误无法精确分析）"
        for fn in _FORBIDDEN_BUILTINS:
            if fn + "(" in code or fn + " (" in code:
                return f"安全限制: 代码疑似调用禁止函数 '{fn}()'（且存在语法错误无法精确分析）"
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _FORBIDDEN_MODULES:
                    return f"安全限制: 禁止导入模块 '{alias.name}'"
                if root == "os":
                    return "安全限制: 禁止导入 os 模块"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in _FORBIDDEN_MODULES:
                    return f"安全限制: 禁止导入模块 '{node.module}'"
                if root == "os":
                    return "安全限制: 禁止导入 os 模块"

        elif isinstance(node, ast.Attribute):
            if node.attr in _DUNDER_BLACKLIST:
                return f"安全限制: 禁止访问内省属性 '{node.attr}'"

        elif isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr

            if name in _FORBIDDEN_BUILTINS:
                return f"安全限制: 禁止调用 '{name}()'"

            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                fqn = f"{func.value.id}.{func.attr}"
                if fqn in ("os.system", "os.popen", "os.exec", "os.execvp",
                           "os.spawn", "os.spawnl", "os.fork",
                           "socket.connect", "socket.send", "socket.bind"):
                    return f"安全限制: 禁止调用 '{fqn}()'"

    return None


@registry.register(
    name="run_python",
    description="在隔离的子进程中执行 Python 代码，返回 stdout 和 stderr。代码无法访问 .env 等配置文件。",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "要执行的 Python 代码"},
            "timeout": {"type": "integer", "description": "超时秒数", "default": 30},
        },
        "required": ["code"],
    },
    risk_level="medium",
    category="code",
)
async def run_python(code: str, timeout: int = 30) -> str:
    """在子进程中执行 Python 代码，带安全检查"""
    safety_err = _check_python_safety(code)
    if safety_err:
        return safety_err

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        tmp_path = f.name

    try:
        import sys
        python_bin = sys.executable or "python3"
        proc = await asyncio.create_subprocess_exec(
            python_bin, tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": tempfile.gettempdir(),
                "PYTHONPATH": "",
            },
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"执行超时 ({timeout}s)，已终止进程"

        result_parts = []
        if stdout:
            result_parts.append(f"[stdout]\n{stdout.decode('utf-8', errors='replace')}")
        if stderr:
            result_parts.append(f"[stderr]\n{stderr.decode('utf-8', errors='replace')}")
        if proc.returncode != 0:
            result_parts.append(f"[exit code] {proc.returncode}")

        output = "\n".join(result_parts) if result_parts else "(无输出)"
        return output

    finally:
        os.unlink(tmp_path)
