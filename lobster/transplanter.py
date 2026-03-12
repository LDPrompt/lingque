"""
🐦 灵雀 - 技能移植器 (SkillTransplanter)

从社区技能市场自动发现、翻译、安装技能
"""

import ast
import asyncio
import json
import logging
import re
import importlib.util
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

import httpx

from .skills.registry import registry

logger = logging.getLogger("lingque.transplanter")

GITHUB_API = "https://api.github.com"
CLAWHUB_REPO = "LDPrompt/skills"
CLAWHUB_RAW = "https://raw.githubusercontent.com/LDPrompt/skills/main"

# 跳过的技能关键词
SKIP_KEYWORDS = [
    "macos", "mac-os", "apple", "icloud", "iphone", "ipad", "ios",
    "safari", "finder", "homebrew", "applescript", "swift",
    "crypto", "bitcoin", "ethereum", "web3", "wallet", "nft",
    "arduino", "raspberry", "hardware", "usb", "bluetooth",
    "windows-only", "powershell",
]

# 危险调用黑名单
DANGEROUS_CALLS = {
    "eval", "exec", "compile",
    "os.system", "os.popen", "os.spawn",
    "subprocess.call", "subprocess.run", "subprocess.Popen",
    "__import__", "importlib.import_module",
    "shutil.rmtree", "shutil.move",
    "open",  # 文件操作需要谨慎
}

ANALYSIS_PROMPT = """你是一个技能移植专家。分析以下技能的 SKILL.md 文档，判断能否移植为 Python 技能。

## SKILL.md 内容:
```
{skill_content}
```

## 移植要求:
1. 技能必须有明确、实用的功能
2. 不能依赖特定操作系统 (macOS/Windows)、Apple 生态、加密货币、特定硬件
3. 优先移植：网络请求、文本处理、数据转换、API 调用类技能
4. HTTP 请求使用 httpx (异步)
5. 技能函数必须是 async def，返回字符串

## 灵雀技能格式:
```python
from lobster.skills.registry import registry

@registry.register(
    name="skill_name",
    description="一句话中文描述，给 LLM 看的",
    parameters={{
        "type": "object",
        "properties": {{
            "param1": {{"type": "string", "description": "参数说明"}},
        }},
        "required": ["param1"],
    }},
    risk_level="low",  # low / medium / high
    category="transplanted",
)
async def skill_name(param1: str) -> str:
    # 实现逻辑
    return "结果字符串"
```

请以 JSON 格式返回分析结果:
{{
    "can_transplant": true/false,
    "reason": "判断原因（中文）",
    "name": "skill_name_snake_case",
    "description": "一句话中文描述",
    "python_code": "完整的 Python 代码（如果 can_transplant 为 false 则为空字符串）"
}}

只返回 JSON，不要其他内容。"""


class SkillTransplanter:
    """
    技能移植器

    从 ClawHub 技能市场自动发现、翻译、安装技能到灵雀系统。
    """

    def __init__(
        self,
        llm_router,
        install_dir: Path,
        notify_callback: Optional[Callable] = None,
        github_token: str = "",
    ):
        self.llm = llm_router
        self.install_dir = Path(install_dir).resolve()
        self.notify_callback = notify_callback
        self.github_token = github_token

        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_file = self.install_dir / "manifest.json"
        self._manifest = self._load_manifest()
        self._fix_relative_paths()

    def _load_manifest(self) -> dict:
        if self.manifest_file.exists():
            try:
                return json.loads(self.manifest_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"installed": {}, "skipped": {}, "last_scan": None}

    def _fix_relative_paths(self):
        """修复 manifest 中的相对路径为绝对路径"""
        changed = False
        for slug, info in self._manifest.get("installed", {}).items():
            file_path = info.get("file", "")
            if file_path and not Path(file_path).is_absolute():
                abs_path = (self.install_dir / Path(file_path).name).resolve()
                if abs_path.exists():
                    info["file"] = str(abs_path)
                    changed = True
                    logger.info(f"修复技能路径: {file_path} -> {abs_path}")
        if changed:
            self._save_manifest()

    def _save_manifest(self):
        self.manifest_file.write_text(
            json.dumps(self._manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _get_headers(self) -> dict:
        headers = {"Accept": "application/vnd.github.v3+json"}
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"
        return headers

    _tree_cache: list[str] | None = None
    _tree_cache_time: float = 0

    async def _fetch_skill_list(self) -> list[str]:
        """
        从 GitHub 获取技能列表（单次 Tree API 请求）

        ClawHub 仓库结构: skills/{username}/{skill-slug}/SKILL.md
        返回格式: ["username/skill-slug", ...]
        """
        import time as _time

        if self._tree_cache and (_time.time() - self._tree_cache_time < 3600):
            return self._tree_cache

        async with httpx.AsyncClient(timeout=30) as client:
            url = f"{GITHUB_API}/repos/{CLAWHUB_REPO}/git/trees/main?recursive=1"
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code != 200:
                logger.error(f"获取仓库树失败: {resp.status_code}")
                return self._tree_cache or []

            tree = resp.json().get("tree", [])

        skill_slugs = set()
        for item in tree:
            path = item.get("path", "")
            if path.startswith("skills/") and path.endswith("/SKILL.md"):
                parts = path.split("/")
                if len(parts) == 4:
                    skill_slugs.add(f"{parts[1]}/{parts[2]}")

        result = sorted(skill_slugs)
        self._tree_cache = result
        self._tree_cache_time = _time.time()
        logger.info(f"发现 {len(result)} 个技能 (单次 Tree API)")
        return result

    async def _fetch_skill_md(self, skill_slug: str) -> str:
        """
        获取技能的 SKILL.md 内容
        
        skill_slug 格式: "username/skill-name"
        实际路径: skills/username/skill-name/SKILL.md
        """
        url = f"{CLAWHUB_RAW}/skills/{skill_slug}/SKILL.md"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text
            logger.debug(f"获取 SKILL.md 失败 ({skill_slug}): {resp.status_code}")
        return ""

    def _should_skip(self, skill_slug: str, skill_content: str = "") -> tuple[bool, str]:
        """检查是否应该跳过此技能"""
        slug_lower = skill_slug.lower()
        content_lower = skill_content.lower()

        for kw in SKIP_KEYWORDS:
            if kw in slug_lower:
                return True, f"技能名包含 '{kw}'，不适合移植"
            if skill_content and kw in content_lower[:500]:
                return True, f"技能描述包含 '{kw}'，不适合移植"

        return False, ""

    async def _analyze_skill(self, skill_slug: str, skill_content: str) -> dict:
        """用 LLM 分析技能并生成 Python 代码"""
        from .llm.base import Message

        prompt = ANALYSIS_PROMPT.format(skill_content=skill_content[:6000])

        try:
            response = await self.llm.chat(
                messages=[Message(role="user", content=prompt)],
                temperature=0.2,
            )

            text = response.content.strip()
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if not match:
                return {"can_transplant": False, "reason": "LLM 返回格式错误"}

            return json.loads(match.group())

        except Exception as e:
            logger.error(f"分析技能失败 {skill_slug}: {e}")
            return {"can_transplant": False, "reason": str(e)}

    def _validate_code(self, code: str) -> tuple[bool, str]:
        """验证生成的代码安全性"""
        # 1. AST 语法检查
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"语法错误: {e}"

        # 2. 危险调用检查
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name):
                        func_name = f"{node.func.value.id}.{node.func.attr}"

                if func_name in DANGEROUS_CALLS:
                    return False, f"包含危险调用: {func_name}"

            # 检查 import
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("os", "subprocess", "shutil"):
                        return False, f"禁止导入危险模块: {alias.name}"

            if isinstance(node, ast.ImportFrom):
                if node.module in ("os", "subprocess", "shutil"):
                    return False, f"禁止导入危险模块: {node.module}"

        return True, ""

    def _install_skill(self, skill_slug: str, analysis: dict) -> bool:
        """安装技能到本地"""
        name = analysis.get("name", "")
        code = analysis.get("python_code", "")

        if not name or not code:
            return False

        # 验证代码
        valid, error = self._validate_code(code)
        if not valid:
            logger.warning(f"技能代码验证失败 {skill_slug}: {error}")
            self._manifest["skipped"][skill_slug] = f"代码不安全: {error}"
            self._save_manifest()
            return False

        # 保存文件（使用绝对路径）
        file_path = (self.install_dir / f"{name}.py").resolve()
        file_path.write_text(code, encoding="utf-8")

        # 动态加载
        try:
            self._load_skill_file(file_path)
        except Exception as e:
            logger.error(f"加载技能失败 {skill_slug}: {e}")
            file_path.unlink(missing_ok=True)
            self._manifest["skipped"][skill_slug] = f"加载失败: {e}"
            self._save_manifest()
            return False

        # 记录到 manifest（绝对路径）
        self._manifest["installed"][skill_slug] = {
            "name": name,
            "description": analysis.get("description", ""),
            "source": f"clawhub:{skill_slug}",
            "installed_at": datetime.now().isoformat(),
            "file": str(file_path),
        }
        self._save_manifest()

        logger.info(f"成功安装技能: {name} (from {skill_slug})")
        return True

    def _load_skill_file(self, file_path: Path):
        """动态加载技能文件"""
        spec = importlib.util.spec_from_file_location(file_path.stem, file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    async def _notify(self, message: str):
        """发送通知"""
        if self.notify_callback:
            try:
                await self.notify_callback(message)
            except Exception as e:
                logger.error(f"发送通知失败: {e}")

    async def run_daily_scan(self) -> str:
        """每日巡检：自动发现+安装新技能"""
        logger.info("开始每日技能巡检...")

        # 获取技能列表
        all_skills = await self._fetch_skill_list()
        if not all_skills:
            return "❌ 无法获取技能列表"

        # 找出未处理的技能
        processed = set(self._manifest["installed"].keys()) | set(self._manifest["skipped"].keys())
        new_skills = [s for s in all_skills if s not in processed]

        if not new_skills:
            self._manifest["last_scan"] = datetime.now().isoformat()
            self._save_manifest()
            return f"✅ 巡检完成，没有新技能（已处理 {len(processed)}/{len(all_skills)}）"

        # 每次最多处理 5 个
        to_process = new_skills[:5]
        installed = []
        skipped = []

        for skill_slug in to_process:
            logger.info(f"处理技能: {skill_slug}")

            # 检查是否应该跳过
            should_skip, reason = self._should_skip(skill_slug)
            if should_skip:
                self._manifest["skipped"][skill_slug] = reason
                skipped.append(f"{skill_slug}: {reason}")
                continue

            # 获取 SKILL.md
            content = await self._fetch_skill_md(skill_slug)
            if not content:
                self._manifest["skipped"][skill_slug] = "无法获取 SKILL.md"
                skipped.append(f"{skill_slug}: 无法获取")
                continue

            # 再次检查内容
            should_skip, reason = self._should_skip(skill_slug, content)
            if should_skip:
                self._manifest["skipped"][skill_slug] = reason
                skipped.append(f"{skill_slug}: {reason}")
                continue

            # LLM 分析
            analysis = await self._analyze_skill(skill_slug, content)
            if not analysis.get("can_transplant"):
                reason = analysis.get("reason", "不适合移植")
                self._manifest["skipped"][skill_slug] = reason
                skipped.append(f"{skill_slug}: {reason}")
                continue

            # 安装
            if self._install_skill(skill_slug, analysis):
                installed.append(f"✅ {analysis.get('name')}: {analysis.get('description')}")
            else:
                skipped.append(f"{skill_slug}: 安装失败")

        self._manifest["last_scan"] = datetime.now().isoformat()
        self._save_manifest()

        # 构建报告
        report = [f"📦 技能巡检完成 ({len(to_process)}/{len(new_skills)} 新技能)"]
        if installed:
            report.append(f"\n**已安装 ({len(installed)}):**")
            report.extend(installed)
        if skipped:
            report.append(f"\n**已跳过 ({len(skipped)}):**")
            report.extend([f"⏭️ {s}" for s in skipped[:5]])
            if len(skipped) > 5:
                report.append(f"...等 {len(skipped)} 个")

        result = "\n".join(report)
        await self._notify(result)
        return result

    async def search_and_install(self, keyword: str) -> str:
        """按关键词搜索并安装技能"""
        if not keyword:
            return self.get_stats()

        keyword_lower = keyword.lower()
        keywords = keyword_lower.split()

        all_skills = await self._fetch_skill_list()
        if not all_skills:
            return "❌ 无法获取技能列表"

        # 多关键词模糊匹配（所有关键词都要出现在 slug 中）
        matches = [
            s for s in all_skills
            if all(kw in s.lower() for kw in keywords)
        ]

        # 如果精确匹配太少，放宽到任意关键词命中
        if len(matches) < 3:
            fuzzy = [
                s for s in all_skills
                if any(kw in s.lower() for kw in keywords) and s not in matches
            ]
            matches.extend(fuzzy[:10])

        if not matches:
            return (
                f"🔍 未找到包含 '{keyword}' 的技能\n"
                f"共 {len(all_skills)} 个技能可用，试试其他关键词"
            )

        processed = set(self._manifest["installed"].keys()) | set(self._manifest["skipped"].keys())
        new_matches = [s for s in matches if s not in processed]

        # 先展示搜索结果
        if not new_matches:
            installed_matches = [s for s in matches if s in self._manifest["installed"]]
            if installed_matches:
                lines = [f"🔍 找到 {len(matches)} 个匹配，以下已安装:\n"]
                for slug in installed_matches[:5]:
                    info = self._manifest["installed"][slug]
                    lines.append(f"  ✅ **{info['name']}**: {info['description']}")
                return "\n".join(lines)
            return f"🔍 找到 {len(matches)} 个匹配，但都已处理过"

        skill_slug = new_matches[0]
        logger.info(f"搜索安装技能: {skill_slug}")

        should_skip, reason = self._should_skip(skill_slug)
        if should_skip:
            self._manifest["skipped"][skill_slug] = reason
            self._save_manifest()
            return f"⏭️ 跳过 {skill_slug}: {reason}"

        content = await self._fetch_skill_md(skill_slug)
        if not content:
            return f"❌ 无法获取 {skill_slug} 的 SKILL.md"

        should_skip, reason = self._should_skip(skill_slug, content)
        if should_skip:
            self._manifest["skipped"][skill_slug] = reason
            self._save_manifest()
            return f"⏭️ 跳过 {skill_slug}: {reason}"

        analysis = await self._analyze_skill(skill_slug, content)
        if not analysis.get("can_transplant"):
            reason = analysis.get("reason", "不适合移植")
            self._manifest["skipped"][skill_slug] = reason
            self._save_manifest()
            return f"⏭️ 无法移植 {skill_slug}: {reason}"

        if self._install_skill(skill_slug, analysis):
            extra = ""
            if len(new_matches) > 1:
                extra = f"\n\n还有 {len(new_matches) - 1} 个相关技能可安装"
            return (
                f"✅ 安装成功!\n\n"
                f"**技能名**: {analysis.get('name')}\n"
                f"**描述**: {analysis.get('description')}\n"
                f"**来源**: clawhub:{skill_slug}"
                + extra
            )
        else:
            return f"❌ 安装失败: {skill_slug}"

    def get_stats(self) -> str:
        """返回移植器状态统计"""
        installed = len(self._manifest.get("installed", {}))
        skipped = len(self._manifest.get("skipped", {}))
        last_scan = self._manifest.get("last_scan", "从未")

        return (
            f"📊 **技能移植器状态**\n\n"
            f"已安装: {installed} 个\n"
            f"已跳过: {skipped} 个\n"
            f"上次巡检: {last_scan}"
        )

    def list_installed(self) -> str:
        """列出已安装的移植技能"""
        installed = self._manifest.get("installed", {})
        if not installed:
            return "📦 暂无已安装的移植技能"

        lines = [f"📦 **已安装的移植技能** ({len(installed)} 个)\n"]
        for slug, info in list(installed.items())[:20]:
            lines.append(f"• **{info['name']}**: {info['description']}")

        if len(installed) > 20:
            lines.append(f"\n...等 {len(installed)} 个")

        return "\n".join(lines)

    async def install_from_github_repo(self, repo_url: str) -> str:
        """
        从任意 GitHub 仓库安装技能。
        
        支持格式:
        - https://github.com/owner/repo
        - https://github.com/owner/repo/tree/main/path/to/skill
        - owner/repo
        """
        import re as _re

        repo_url = repo_url.strip().rstrip("/")

        # 解析 owner/repo 和可选子路径
        m = _re.match(
            r"(?:https?://github\.com/)?([^/]+/[^/]+?)(?:/tree/[^/]+/(.+))?$",
            repo_url,
        )
        if not m:
            return f"❌ 无法解析仓库地址: {repo_url}\n支持格式: https://github.com/owner/repo"

        owner_repo = m.group(1).removesuffix(".git")
        sub_path = m.group(2) or ""

        await self._notify(f"🔍 正在扫描仓库 `{owner_repo}` ...")

        async with httpx.AsyncClient(timeout=30) as client:
            skill_mds = await self._find_skill_mds_in_repo(client, owner_repo, sub_path)

        if not skill_mds:
            return (
                f"❌ 在 `{owner_repo}` 中未找到 SKILL.md 文件\n"
                f"提示: 仓库中需要有 SKILL.md 文件来描述技能"
            )

        installed = []
        failed = []

        for path, raw_url in skill_mds:
            slug = f"github:{owner_repo}/{path}"

            if slug in self._manifest.get("installed", {}):
                installed.append(f"⏭️ `{path}` (已安装)")
                continue

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(raw_url)
                if resp.status_code != 200:
                    failed.append(f"`{path}`: 无法下载 SKILL.md")
                    continue
                content = resp.text

            should_skip, reason = self._should_skip(path, content)
            if should_skip:
                failed.append(f"`{path}`: {reason}")
                continue

            await self._notify(f"🧠 正在分析技能: `{path}` ...")
            analysis = await self._analyze_skill(slug, content)

            if not analysis.get("can_transplant"):
                reason = analysis.get("reason", "不适合移植")
                failed.append(f"`{path}`: {reason}")
                continue

            if self._install_skill(slug, analysis):
                name = analysis.get("name", "unknown")
                desc = analysis.get("description", "")
                installed.append(f"✅ **{name}**: {desc}")
            else:
                failed.append(f"`{path}`: 安装失败")

        lines = [f"📦 仓库 `{owner_repo}` 扫描完成\n"]
        if installed:
            lines.append(f"**安装结果 ({len(installed)}):**")
            lines.extend(installed)
        if failed:
            lines.append(f"\n**跳过/失败 ({len(failed)}):**")
            lines.extend(failed)
        if not installed and not failed:
            lines.append("没有可安装的新技能")

        return "\n".join(lines)

    async def _find_skill_mds_in_repo(
        self,
        client: httpx.AsyncClient,
        owner_repo: str,
        sub_path: str = "",
    ) -> list[tuple[str, str]]:
        """
        在 GitHub 仓库中递归查找 SKILL.md 文件。
        优先用 API，被限流时自动降级到 HTML + raw URL。
        返回 [(相对路径, raw 下载 URL), ...]
        """
        # 先尝试 API
        results = await self._find_via_api(client, owner_repo, sub_path)
        if results is not None:
            return results

        # API 失败 (403/rate limit)，降级到 HTML 解析 + raw URL
        logger.info(f"GitHub API 不可用，降级到 HTML 模式: {owner_repo}")
        return await self._find_via_html(client, owner_repo, sub_path)

    async def _find_via_api(
        self,
        client: httpx.AsyncClient,
        owner_repo: str,
        sub_path: str = "",
    ) -> list[tuple[str, str]] | None:
        """通过 GitHub API 查找，返回 None 表示 API 不可用（需降级）"""
        api_path = f"contents/{sub_path}" if sub_path else "contents"
        url = f"{GITHUB_API}/repos/{owner_repo}/{api_path}"

        resp = await client.get(url, headers=self._get_headers())
        if resp.status_code == 403:
            return None
        if resp.status_code != 200:
            logger.error(f"获取仓库内容失败: {owner_repo}/{sub_path} -> {resp.status_code}")
            return []

        items = resp.json()
        if not isinstance(items, list):
            items = [items]

        results = []
        dirs_to_scan = []

        for item in items:
            name = item.get("name", "")
            item_type = item.get("type", "")
            item_path = item.get("path", "")

            if name.upper() == "SKILL.MD" and item_type == "file":
                raw_url = item.get("download_url", "")
                if raw_url:
                    results.append((item_path, raw_url))
            elif item_type == "dir" and name not in ("node_modules", ".git", "__pycache__", "venv"):
                dirs_to_scan.append(item_path)

        depth = sub_path.count("/") + 1 if sub_path else 0
        if depth < 3 and dirs_to_scan:
            tasks = [self._find_via_api(client, owner_repo, d) for d in dirs_to_scan[:10]]
            sub_results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in sub_results:
                if r is None:
                    # API 被限流，对已有结果直接返回
                    return results if results else None
                if isinstance(r, list):
                    results.extend(r)

        return results

    async def _find_via_html(
        self,
        client: httpx.AsyncClient,
        owner_repo: str,
        sub_path: str = "",
    ) -> list[tuple[str, str]]:
        """不依赖 API，通过 GitHub HTML 页面 + raw URL 直接查找 SKILL.md"""
        results = []

        # 检测默认分支
        branch = await self._detect_default_branch(client, owner_repo)
        if not branch:
            return results

        base_raw = f"https://raw.githubusercontent.com/{owner_repo}/{branch}"

        # 1) 直接尝试当前路径的 SKILL.md
        check_path = f"{sub_path}/SKILL.md" if sub_path else "SKILL.md"
        raw_url = f"{base_raw}/{check_path}"
        resp = await client.get(raw_url)
        if resp.status_code == 200:
            results.append((check_path, raw_url))

        # 2) 抓取 GitHub HTML 页面，发现子目录
        if sub_path:
            page_url = f"https://github.com/{owner_repo}/tree/{branch}/{sub_path}"
        else:
            page_url = f"https://github.com/{owner_repo}"
        resp = await client.get(page_url, follow_redirects=True)
        if resp.status_code != 200:
            return results

        dirs = self._parse_dirs_from_html(resp.text, owner_repo, branch, sub_path)

        # 3) 并发检测每个子目录的 SKILL.md
        async def check_dir(dir_path: str):
            skill_path = f"{dir_path}/SKILL.md"
            url = f"{base_raw}/{skill_path}"
            r = await client.get(url)
            if r.status_code == 200:
                return (skill_path, url)
            # 尝试再深一层（如 skills/user/skill-name/SKILL.md）
            sub_resp = await client.get(
                f"https://github.com/{owner_repo}/tree/{branch}/{dir_path}",
                follow_redirects=True,
            )
            if sub_resp.status_code == 200:
                sub_dirs = self._parse_dirs_from_html(sub_resp.text, owner_repo, branch, dir_path)
                for sd in sub_dirs[:10]:
                    sp = f"{sd}/SKILL.md"
                    sr = await client.get(f"{base_raw}/{sp}")
                    if sr.status_code == 200:
                        return (sp, f"{base_raw}/{sp}")
            return None

        tasks = [check_dir(d) for d in dirs[:15]]
        check_results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in check_results:
            if isinstance(r, tuple):
                results.append(r)

        return results

    async def _detect_default_branch(self, client: httpx.AsyncClient, owner_repo: str) -> str:
        """检测仓库默认分支（main 或 master）"""
        for branch in ("main", "master"):
            url = f"https://raw.githubusercontent.com/{owner_repo}/{branch}/README.md"
            resp = await client.head(url)
            if resp.status_code == 200:
                return branch
        # 再试一次，有些仓库没有 README
        for branch in ("main", "master"):
            url = f"https://github.com/{owner_repo}/tree/{branch}"
            resp = await client.head(url, follow_redirects=True)
            if resp.status_code == 200:
                return branch
        logger.warning(f"无法检测 {owner_repo} 的默认分支")
        return ""

    @staticmethod
    def _parse_dirs_from_html(html: str, owner_repo: str, branch: str, parent: str) -> list[str]:
        """从 GitHub HTML 页面解析子目录路径"""
        import re as _re
        dirs = []
        # 匹配 /owner/repo/tree/branch/path 形式的链接
        prefix = f"/{owner_repo}/tree/{branch}/"
        pattern = _re.escape(prefix) + r'([^"\'>\s]+)'
        for m in _re.finditer(pattern, html):
            path = m.group(1).rstrip("/")
            if not path or "/" not in path and parent:
                full = f"{parent}/{path}" if parent else path
            else:
                full = path
            # 只取直接子目录（相对于 parent 多一层）
            if parent:
                if full.startswith(parent + "/"):
                    relative = full[len(parent) + 1:]
                    if "/" not in relative and relative not in (".", ".."):
                        if full not in dirs:
                            dirs.append(full)
            else:
                if "/" not in path and path not in (".", ".."):
                    if path not in dirs:
                        dirs.append(path)
        return dirs

    def load_installed_skills(self):
        """启动时加载所有已安装的技能"""
        count = 0
        fixed = False
        for slug, info in list(self._manifest.get("installed", {}).items()):
            file_path = Path(info.get("file", ""))

            # 路径不存在时，尝试在 install_dir 下按文件名查找
            if not file_path.exists():
                fallback = self.install_dir / file_path.name
                if fallback.exists():
                    file_path = fallback
                    info["file"] = str(fallback.resolve())
                    fixed = True
                    logger.info(f"修复技能路径: {slug} -> {fallback}")
                else:
                    logger.warning(f"技能文件不存在，跳过: {slug} ({file_path})")
                    continue

            try:
                self._load_skill_file(file_path)
                count += 1
            except Exception as e:
                logger.warning(f"加载移植技能失败 {slug}: {e}")

        if fixed:
            self._save_manifest()
        if count:
            logger.info(f"已加载 {count} 个移植技能")
        elif self._manifest.get("installed"):
            logger.warning(f"有 {len(self._manifest['installed'])} 个已安装技能但全部加载失败")
