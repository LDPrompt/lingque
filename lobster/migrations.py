"""
灵雀数据迁移框架

在版本升级时自动将旧数据格式适配到新版本，迁移前自动备份。

用法:
    # 注册迁移 (在本文件底部添加)
    @migration("0.4.0", "0.5.0")
    def migrate_xxx(memory_dir: Path):
        ...

    # 启动时执行
    from lobster.migrations import run_pending
    run_pending(memory_dir)
"""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger("lobster.migrations")

_DATA_VERSION_FILE = ".data_version"
_BACKUP_DIR = ".migration_backup"

_migrations: list[dict] = []


def migration(from_ver: str, to_ver: str):
    """装饰器: 注册一个数据迁移函数

    Args:
        from_ver: 迁移起始版本 (如 "0.4.0")
        to_ver: 迁移目标版本 (如 "0.5.0")
    """
    def decorator(func: Callable[[Path], None]):
        _migrations.append({
            "from": from_ver,
            "to": to_ver,
            "func": func,
            "name": func.__name__,
        })
        _migrations.sort(key=lambda m: _parse_ver(m["from"]))
        return func
    return decorator


def _parse_ver(v: str) -> tuple[int, ...]:
    parts = []
    for seg in v.strip().lstrip("vV").split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            break
    return tuple(parts) or (0,)


def _read_data_version(memory_dir: Path) -> str | None:
    ver_file = memory_dir / _DATA_VERSION_FILE
    if ver_file.exists():
        try:
            return ver_file.read_text(encoding="utf-8").strip()
        except Exception:
            return None
    return None


def _write_data_version(memory_dir: Path, version: str):
    ver_file = memory_dir / _DATA_VERSION_FILE
    ver_file.write_text(version, encoding="utf-8")


def _backup_before_migration(memory_dir: Path, from_ver: str, to_ver: str):
    """迁移前备份受影响的数据文件"""
    backup_root = memory_dir / _BACKUP_DIR
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = backup_root / f"{from_ver}_to_{to_ver}_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    targets = [
        "MEMORY.md",
        _DATA_VERSION_FILE,
    ]
    for pattern in ["users/*/profile.json", "knowledge_graph/*.json",
                    ".learnings/*.json", ".learnings/*.jsonl"]:
        targets.extend(str(p.relative_to(memory_dir)) for p in memory_dir.glob(pattern))

    backed = 0
    for rel in targets:
        src = memory_dir / rel
        if src.exists() and src.is_file():
            dst = backup_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            backed += 1

    if backed:
        logger.info(f"迁移备份: {backed} 个文件 → {backup_dir}")
    return backup_dir


def run_pending(memory_dir: Path) -> list[str]:
    """检查并执行所有待执行的数据迁移

    Args:
        memory_dir: 记忆目录路径

    Returns:
        已执行的迁移名称列表
    """
    from . import __version__
    code_version = __version__

    data_version = _read_data_version(memory_dir)

    if data_version is None:
        logger.info(f"首次升级检测: 标记数据版本为 v{code_version} (无需迁移)")
        _write_data_version(memory_dir, code_version)
        return []

    if _parse_ver(data_version) >= _parse_ver(code_version):
        logger.debug(f"数据版本 v{data_version} >= 代码版本 v{code_version}, 无需迁移")
        return []

    pending = [
        m for m in _migrations
        if _parse_ver(m["from"]) >= _parse_ver(data_version)
        and _parse_ver(m["to"]) <= _parse_ver(code_version)
    ]

    if not pending:
        logger.info(f"v{data_version} → v{code_version}: 无迁移脚本，直接更新版本号")
        _write_data_version(memory_dir, code_version)
        return []

    executed = []
    for m in pending:
        logger.info(f"执行迁移: {m['name']} (v{m['from']} → v{m['to']})")
        try:
            _backup_before_migration(memory_dir, m["from"], m["to"])
            m["func"](memory_dir)
            executed.append(m["name"])
            logger.info(f"迁移完成: {m['name']}")
        except Exception as e:
            logger.error(f"迁移失败: {m['name']}: {e}", exc_info=True)
            raise RuntimeError(
                f"数据迁移 {m['name']} 失败: {e}\n"
                f"备份已保存在 {memory_dir / _BACKUP_DIR}"
            ) from e

    _write_data_version(memory_dir, code_version)
    logger.info(f"数据版本更新: v{data_version} → v{code_version} (执行了 {len(executed)} 个迁移)")
    return executed


# =====================================================================
# 迁移脚本区域 — 按版本顺序添加
# =====================================================================

# 示例 (当前无迁移需求，留作模板):
#
# @migration("0.4.0", "0.5.0")
# def migrate_profile_add_growth_fields(memory_dir: Path):
#     """给旧版 profile.json 补充成长追踪字段"""
#     for profile_path in memory_dir.glob("users/*/profile.json"):
#         data = json.loads(profile_path.read_text(encoding="utf-8"))
#         changed = False
#         if "task_stats" not in data:
#             data["task_stats"] = {"total": 0, "by_category": {}, "streak_days": 0}
#             changed = True
#         if changed:
#             profile_path.write_text(
#                 json.dumps(data, ensure_ascii=False, indent=2),
#                 encoding="utf-8",
#             )
