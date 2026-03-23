"""
灵雀自动升级模块

- 通过 GitHub API 检查最新版本
- 自动检测部署方式 (Docker / systemd / 开发模式)
- 执行升级: git pull → pip install → 数据迁移 → 重启
"""

import asyncio
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("lobster.updater")

GITHUB_REPO = "LDPrompt/lingque"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_TAGS_URL = f"https://api.github.com/repos/{GITHUB_REPO}/tags"

_update_cache: dict = {}
_CACHE_TTL = 3600


def get_current_version() -> str:
    from . import __version__
    return __version__


def _parse_version(v: str) -> tuple[int, ...]:
    """'v0.4.0' / '0.4.0' → (0, 4, 0)"""
    v = v.lstrip("vV").strip()
    parts = []
    for seg in v.split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            break
    return tuple(parts) or (0,)


def _is_newer(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)


# ── 部署类型检测 ──────────────────────────────────────────────

class DeployType:
    DOCKER = "docker"
    SYSTEMD = "systemd"
    DEV = "dev"


def detect_deploy_type() -> str:
    if os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER"):
        return DeployType.DOCKER

    if platform.system() == "Linux":
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "lingque"],
                capture_output=True, text=True, timeout=5,
            )
            if r.stdout.strip() == "active":
                return DeployType.SYSTEMD
        except Exception:
            pass

    return DeployType.DEV


# ── GitHub API 版本检查 ───────────────────────────────────────

async def check_for_updates() -> dict:
    """检查 GitHub 是否有新版本

    返回:
        {
            "has_update": bool,
            "current": "0.4.0",
            "latest": "0.5.0",
            "release_notes": "...",
            "release_url": "https://...",
            "published_at": "2026-03-20T...",
        }
    """
    now = time.time()
    if _update_cache.get("result") and now - _update_cache.get("ts", 0) < _CACHE_TTL:
        return _update_cache["result"]

    current = get_current_version()
    result = {
        "has_update": False,
        "current": current,
        "latest": current,
        "release_notes": "",
        "release_url": "",
        "published_at": "",
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                GITHUB_API_URL,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                tag = data.get("tag_name", "")
                if tag and _is_newer(tag, current):
                    result.update({
                        "has_update": True,
                        "latest": tag.lstrip("vV"),
                        "release_notes": data.get("body", "") or "",
                        "release_url": data.get("html_url", ""),
                        "published_at": data.get("published_at", ""),
                    })
            elif resp.status_code == 404:
                resp2 = await client.get(
                    GITHUB_TAGS_URL,
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                if resp2.status_code == 200:
                    tags = resp2.json()
                    if tags:
                        tag = tags[0].get("name", "")
                        if tag and _is_newer(tag, current):
                            result.update({
                                "has_update": True,
                                "latest": tag.lstrip("vV"),
                            })
    except ImportError:
        try:
            import urllib.request
            req = urllib.request.Request(
                GITHUB_API_URL,
                headers={"Accept": "application/vnd.github.v3+json",
                         "User-Agent": "LingQue-Updater"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                tag = data.get("tag_name", "")
                if tag and _is_newer(tag, current):
                    result.update({
                        "has_update": True,
                        "latest": tag.lstrip("vV"),
                        "release_notes": data.get("body", "") or "",
                        "release_url": data.get("html_url", ""),
                        "published_at": data.get("published_at", ""),
                    })
        except Exception as e:
            logger.debug(f"版本检查失败 (urllib): {e}")
    except Exception as e:
        logger.debug(f"版本检查失败: {e}")

    _update_cache["result"] = result
    _update_cache["ts"] = now
    return result


# ── 升级执行 ──────────────────────────────────────────────────

def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _run_cmd(cmd: list[str], cwd: str | None = None, timeout: int = 120) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=cwd or str(_get_project_root()),
            timeout=timeout,
        )
        output = (r.stdout + "\n" + r.stderr).strip()
        return r.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"命令超时 ({timeout}s): {' '.join(cmd)}"
    except Exception as e:
        return False, str(e)


def _needs_pip_install() -> bool:
    """检查 requirements.txt 是否有变化"""
    ok, diff = _run_cmd(["git", "diff", "HEAD@{1}", "--name-only"])
    if ok and "requirements.txt" in diff:
        return True
    return False


async def perform_upgrade(notify_callback=None) -> dict:
    """执行完整升级流程

    Args:
        notify_callback: async 回调函数，用于给用户发送进度消息
            signature: async def callback(message: str)

    Returns:
        {"success": bool, "from_version": str, "to_version": str,
         "steps": [...], "error": str}
    """
    root = _get_project_root()
    current = get_current_version()
    deploy_type = detect_deploy_type()
    steps = []

    async def _notify(msg: str):
        steps.append(msg)
        logger.info(msg)
        if notify_callback:
            try:
                await notify_callback(msg)
            except Exception:
                pass

    await _notify(f"🔄 开始升级 (当前 v{current}, 部署方式: {deploy_type})")

    if deploy_type == DeployType.DOCKER:
        return await _upgrade_docker(root, current, steps, _notify)

    # ── 源码部署升级 ──

    # 1. git stash 保存本地修改
    ok, out = _run_cmd(["git", "stash", "--include-untracked", "-m", "auto-upgrade-stash"])
    stashed = ok and "No local changes" not in out
    if stashed:
        await _notify("📦 已暂存本地修改")

    # 2. git pull
    ok, out = _run_cmd(["git", "pull", "--rebase", "origin", "main"])
    if not ok:
        if stashed:
            _run_cmd(["git", "stash", "pop"])
        await _notify(f"❌ git pull 失败: {out[:200]}")
        return {"success": False, "from_version": current, "to_version": current,
                "steps": steps, "error": f"git pull 失败: {out[:200]}"}
    await _notify("✅ 代码已更新")

    # 3. pip install (如果依赖变化)
    if _needs_pip_install():
        await _notify("📦 检测到依赖变化，正在安装...")
        ok, out = _run_cmd(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--quiet"],
            timeout=300,
        )
        if ok:
            await _notify("✅ 依赖安装完成")
        else:
            await _notify(f"⚠️ 依赖安装可能有问题: {out[:200]}")

    # 4. 数据迁移
    try:
        from .migrations import run_pending
        from .config import get_config
        config = get_config()
        migrated = run_pending(config.memory_dir)
        if migrated:
            await _notify(f"✅ 数据迁移完成: {migrated}")
        else:
            await _notify("✅ 无需数据迁移")
    except Exception as e:
        await _notify(f"⚠️ 数据迁移跳过: {e}")

    # 5. 恢复本地修改
    if stashed:
        ok, _ = _run_cmd(["git", "stash", "pop"])
        if ok:
            await _notify("📦 已恢复本地修改")

    # 6. 读取新版本号
    new_version = current
    try:
        init_path = root / "lobster" / "__init__.py"
        text = init_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("__version__"):
                new_version = line.split("=")[1].strip().strip("\"'")
                break
    except Exception:
        pass

    # 7. 重启服务
    await _notify(f"🔄 升级完成 v{current} → v{new_version}，正在重启...")
    _restart_service(deploy_type)

    return {
        "success": True,
        "from_version": current,
        "to_version": new_version,
        "steps": steps,
        "error": "",
    }


async def _upgrade_docker(root: Path, current: str, steps: list, _notify) -> dict:
    """Docker 部署的升级流程"""
    await _notify("🐳 Docker 部署模式，执行容器升级...")

    compose_file = root / "docker-compose.yml"
    if not compose_file.exists():
        compose_file = root / "docker-compose.yaml"
    if not compose_file.exists():
        await _notify("❌ 未找到 docker-compose.yml")
        return {"success": False, "from_version": current, "to_version": current,
                "steps": steps, "error": "未找到 docker-compose.yml"}

    ok, out = _run_cmd(["git", "pull", "--rebase", "origin", "main"])
    if not ok:
        await _notify(f"❌ git pull 失败: {out[:200]}")
        return {"success": False, "from_version": current, "to_version": current,
                "steps": steps, "error": f"git pull 失败: {out[:200]}"}
    await _notify("✅ 代码已更新")

    await _notify("🔨 正在重建 Docker 镜像...")
    ok, out = _run_cmd(["docker", "compose", "build"], timeout=600)
    if not ok:
        await _notify(f"❌ Docker build 失败: {out[:300]}")
        return {"success": False, "from_version": current, "to_version": current,
                "steps": steps, "error": f"Docker build 失败: {out[:300]}"}
    await _notify("✅ 镜像构建完成")

    await _notify("🔄 正在重启容器...")
    ok, out = _run_cmd(["docker", "compose", "up", "-d"])
    if not ok:
        await _notify(f"❌ 容器启动失败: {out[:200]}")
        return {"success": False, "from_version": current, "to_version": current,
                "steps": steps, "error": f"容器启动失败: {out[:200]}"}

    await _notify("✅ Docker 升级完成，容器已重启")
    return {"success": True, "from_version": current, "to_version": "latest",
            "steps": steps, "error": ""}


def _restart_service(deploy_type: str):
    """根据部署类型重启服务"""
    try:
        if deploy_type == DeployType.SYSTEMD:
            subprocess.Popen(
                ["bash", "-c", "sleep 2 && systemctl restart lingque"],
                start_new_session=True,
            )
        elif deploy_type == DeployType.DEV:
            subprocess.Popen(
                ["bash", "-c", f"sleep 2 && kill {os.getpid()} && sleep 1 && "
                 f"cd {_get_project_root()} && {sys.executable} -m lobster.main"],
                start_new_session=True,
            )
    except Exception as e:
        logger.warning(f"自动重启失败，请手动重启: {e}")


# ── 启动时异步检查 ────────────────────────────────────────────

_startup_result: Optional[dict] = None


async def startup_check():
    """启动时后台检查更新，结果存入 _startup_result 供后续使用"""
    global _startup_result
    try:
        await asyncio.sleep(5)
        result = await check_for_updates()
        _startup_result = result
        if result["has_update"]:
            logger.info(
                f"🆕 灵雀新版本可用: v{result['latest']} (当前 v{result['current']})"
            )
        else:
            logger.debug(f"当前已是最新版本 v{result['current']}")
    except Exception as e:
        logger.debug(f"启动版本检查跳过: {e}")


def get_startup_result() -> Optional[dict]:
    return _startup_result
