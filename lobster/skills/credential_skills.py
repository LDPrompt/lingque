"""
凭证保险箱 — Fernet 对称加密存储 Skill 所需的 API Key / Token

存储位置: workspaces/credentials.enc
密钥文件: workspaces/.credential_key (自动生成，首次使用时创建)
加密算法: Fernet (AES-128-CBC + HMAC-SHA256)
运行时: 启动后自动解密并注入 os.environ，技能可直接 os.getenv() 使用
向后兼容: 自动检测并迁移旧版 base64 格式 (credentials.json)
"""

import base64
import json
import logging
import os
import stat
from pathlib import Path
from .registry import register, SkillResult

logger = logging.getLogger("lobster.skills.credentials")

_CREDENTIALS_DIR = Path("workspaces")
_CREDENTIALS_FILE = _CREDENTIALS_DIR / "credentials.enc"
_KEY_FILE = _CREDENTIALS_DIR / ".credential_key"
_LEGACY_FILE = _CREDENTIALS_DIR / "credentials.json"


def _ensure_dir():
    _CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)


def _get_or_create_key() -> bytes:
    """获取或生成 Fernet 密钥（首次使用时自动创建）"""
    _ensure_dir()
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes().strip()

    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)

    try:
        if os.name != "nt":
            os.chmod(_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 600
    except OSError:
        pass

    logger.info("已生成凭证加密密钥")
    return key


def _get_fernet():
    from cryptography.fernet import Fernet
    return Fernet(_get_or_create_key())


def _load_all() -> dict[str, str]:
    """加载并解密所有凭证"""
    _migrate_legacy()

    if not _CREDENTIALS_FILE.exists():
        return {}

    try:
        f = _get_fernet()
        encrypted = _CREDENTIALS_FILE.read_bytes()
        decrypted = f.decrypt(encrypted)
        return json.loads(decrypted.decode("utf-8"))
    except Exception as e:
        logger.error(f"凭证解密失败: {e}")
        return {}


def _save_all(data: dict[str, str]):
    """加密并保存所有凭证"""
    _ensure_dir()
    f = _get_fernet()
    plaintext = json.dumps(data, ensure_ascii=False).encode("utf-8")
    encrypted = f.encrypt(plaintext)
    _CREDENTIALS_FILE.write_bytes(encrypted)


def _migrate_legacy():
    """自动迁移旧版 base64 格式的 credentials.json"""
    if not _LEGACY_FILE.exists() or _CREDENTIALS_FILE.exists():
        return

    try:
        raw = json.loads(_LEGACY_FILE.read_text(encoding="utf-8"))
        if not raw:
            return

        migrated = {}
        for k, v in raw.items():
            try:
                migrated[k] = base64.b64decode(v).decode("utf-8")
            except Exception:
                migrated[k] = v

        _save_all(migrated)

        backup = _LEGACY_FILE.with_suffix(".json.bak")
        _LEGACY_FILE.rename(backup)
        logger.info(f"已迁移 {len(migrated)} 个凭证到加密存储，旧文件备份为 {backup.name}")
    except Exception as e:
        logger.warning(f"凭证迁移失败（旧文件保留）: {e}")


def load_credentials_to_env():
    """启动时调用：将凭证注入 os.environ（不覆盖已有值）"""
    creds = _load_all()
    loaded = 0
    for k, v in creds.items():
        if k not in os.environ:
            os.environ[k] = v
            loaded += 1
    if loaded:
        logger.info(f"已从凭证保险箱加载 {loaded} 个凭证到环境变量")
    return loaded


# ==================== 技能注册 ====================

@register(
    name="save_credential",
    description="保存 API Key / Token 到凭证保险箱（Fernet 加密存储，自动注入环境变量）。用户提供密钥时必须用此工具保存，禁止写入 .env 或记忆。",
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "凭证名称（建议大写下划线风格，如 LISTENHUB_API_KEY、TTS_API_KEY）",
            },
            "value": {
                "type": "string",
                "description": "凭证值（API Key / Token / Secret）",
            },
        },
        "required": ["name", "value"],
    },
    risk_level="medium",
    category="credential",
)
async def save_credential(name: str, value: str) -> SkillResult:
    name = name.strip().upper()
    if not name or not value:
        return SkillResult(success=False, error="凭证名称和值不能为空")

    creds = _load_all()
    is_update = name in creds
    creds[name] = value
    _save_all(creds)

    os.environ[name] = value

    action = "更新" if is_update else "保存"
    mask = value[:4] + "****" if len(value) > 4 else "****"
    return SkillResult(
        success=True,
        data=f"已{action}凭证 {name} = {mask}\n已注入环境变量，技能可通过 os.getenv('{name}') 使用",
    )


@register(
    name="get_credential",
    description="从凭证保险箱获取指定凭证的值（仅限 Agent 内部使用，不会展示给用户）",
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "凭证名称",
            },
        },
        "required": ["name"],
    },
    risk_level="low",
    category="credential",
)
async def get_credential(name: str) -> SkillResult:
    name = name.strip().upper()
    creds = _load_all()
    if name not in creds:
        available = ", ".join(sorted(creds.keys())) if creds else "（空）"
        return SkillResult(success=False, error=f"凭证 {name} 不存在。可用凭证: {available}")
    return SkillResult(success=True, data=creds[name])


@register(
    name="delete_credential",
    description="从凭证保险箱删除指定凭证",
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "要删除的凭证名称",
            },
        },
        "required": ["name"],
    },
    risk_level="medium",
    category="credential",
)
async def delete_credential(name: str) -> SkillResult:
    name = name.strip().upper()
    creds = _load_all()
    if name not in creds:
        return SkillResult(success=False, error=f"凭证 {name} 不存在")
    del creds[name]
    _save_all(creds)
    os.environ.pop(name, None)
    return SkillResult(success=True, data=f"已删除凭证 {name}")


@register(
    name="list_credentials",
    description="列出凭证保险箱中所有已保存的凭证名称（不显示值）",
    parameters={"type": "object", "properties": {}},
    risk_level="low",
    category="credential",
)
async def list_credentials() -> SkillResult:
    creds = _load_all()
    if not creds:
        return SkillResult(success=True, data="凭证保险箱为空，还没有保存任何凭证")
    lines = []
    for name, val in sorted(creds.items()):
        mask = val[:4] + "****" if len(val) > 4 else "****"
        lines.append(f"  {name} = {mask}")
    return SkillResult(success=True, data=f"已保存 {len(creds)} 个凭证:\n" + "\n".join(lines))
