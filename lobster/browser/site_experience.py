"""
站点经验存储 - 按域名记录浏览器操作经验，跨会话复用。

存储位置: memory/site_experience/{domain}.md
格式:
---
domain: example.com
updated: 2026-03-13
---
## 平台特征
...
## 有效模式
...
## 已知陷阱
...
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger("lingque.site_experience")

_EXPERIENCE_DIR: Path | None = None


def _get_experience_dir() -> Path:
    global _EXPERIENCE_DIR
    if _EXPERIENCE_DIR is None:
        memory_dir = os.environ.get("MEMORY_DIR", "./memory")
        _EXPERIENCE_DIR = Path(memory_dir).resolve() / "site_experience"
        _EXPERIENCE_DIR.mkdir(parents=True, exist_ok=True)
    return _EXPERIENCE_DIR


def _normalize_domain(domain_or_url: str) -> str:
    """从 URL 或域名中提取规范化的域名"""
    s = domain_or_url.strip()
    if "://" in s:
        s = urlparse(s).netloc
    s = s.lower().strip()
    s = re.sub(r"^www\.", "", s)
    s = re.sub(r"[^a-z0-9.\-]", "_", s)
    return s


def list_experiences() -> list[str]:
    """列出所有已有经验的域名"""
    d = _get_experience_dir()
    if not d.exists():
        return []
    return sorted(
        p.stem for p in d.glob("*.md") if p.stat().st_size > 0
    )


def load_experience(domain_or_url: str) -> str:
    """加载指定域名的经验内容，无则返回空字符串"""
    domain = _normalize_domain(domain_or_url)
    if not domain:
        return ""
    path = _get_experience_dir() / f"{domain}.md"
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"读取站点经验失败 {domain}: {e}")
    return ""


def save_experience(domain_or_url: str, content: str) -> bool:
    """保存或更新指定域名的经验"""
    domain = _normalize_domain(domain_or_url)
    if not domain or not content.strip():
        return False
    path = _get_experience_dir() / f"{domain}.md"

    existing = ""
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except Exception:
            pass

    if existing:
        updated = _merge_experience(existing, content, domain)
    else:
        updated = _format_new_experience(domain, content)

    try:
        path.write_text(updated, encoding="utf-8")
        logger.info(f"站点经验已保存: {domain} ({len(updated)} 字符)")
        return True
    except Exception as e:
        logger.error(f"保存站点经验失败 {domain}: {e}")
        return False


def _format_new_experience(domain: str, content: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""---
domain: {domain}
updated: {today}
---

{content.strip()}
"""


def _merge_experience(existing: str, new_content: str, domain: str) -> str:
    """合并新经验到已有内容末尾，更新日期"""
    today = datetime.now().strftime("%Y-%m-%d")
    date_pattern = re.compile(r"(updated:\s*)(\d{4}-\d{2}-\d{2})")
    if date_pattern.search(existing):
        merged = date_pattern.sub(rf"\g<1>{today}", existing)
    else:
        merged = existing

    merged = merged.rstrip() + f"\n\n## 补充经验 ({today})\n\n{new_content.strip()}\n"
    return merged


def load_experience_for_url(url: str) -> str:
    """根据 URL 自动提取域名并加载经验，供浏览器技能内部使用"""
    domain = _normalize_domain(url)
    if not domain:
        return ""
    exp = load_experience(domain)
    if exp:
        logger.info(f"已加载站点经验: {domain}")
    return exp
