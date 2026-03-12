"""
🌐 灵雀 - 截图指导式登录

流程：打开网站 → 截图发飞书 → 用户指导操作 → 执行并截图确认 → 保存 Cookie
统一使用 _SharedBrowser（继承 CDP 模式的反检测能力）

技能：
- browser_open_for_login: 打开网站截图
- browser_do_action: 执行操作（点击/输入/等待等）
- browser_save_cookies: 保存 Cookie
- browser_close_login: 关闭浏览器
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .registry import register, SkillResult

logger = logging.getLogger("lingque.skills.browser_login")

# ==================== 飞书通道注入 ====================

_feishu_channel = None


def set_feishu_channel_for_login(channel):
    global _feishu_channel
    _feishu_channel = channel


# ==================== 路径常量 ====================

COOKIES_DIR = Path("workspaces/cookies")
SCREENSHOT_DIR = Path("workspaces/screenshots")


# ==================== 共享浏览器引用 ====================

def _get_shared_browser():
    """延迟导入，避免循环引用"""
    from ..browser.playwright_browser import _SharedBrowser
    return _SharedBrowser


# ==================== Cookie 管理 ====================

async def _load_cookies_for_url(context, url: str) -> int:
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    domain = urlparse(url).netloc
    loaded = 0

    for cookie_file in COOKIES_DIR.glob("*.json"):
        cookie_domain = cookie_file.stem
        if cookie_domain in domain or domain.endswith("." + cookie_domain):
            try:
                cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
                await context.add_cookies(cookies)
                loaded += len(cookies)
                logger.info(f"已加载 Cookie: {cookie_domain} ({len(cookies)} 个)")
            except Exception as e:
                logger.error(f"加载 Cookie 失败 ({cookie_domain}): {e}")
    return loaded


def _save_cookies(domain: str, cookies: list[dict]) -> int:
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    filtered = [c for c in cookies if domain in c.get("domain", "")]
    if not filtered:
        filtered = cookies
    cookie_file = COOKIES_DIR / f"{domain}.json"
    cookie_file.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"已保存 {len(filtered)} 个 Cookie → {cookie_file}")
    return len(filtered)


# ==================== 截图发送 ====================

async def _wait_for_page_content(page, timeout: float = 10.0):
    """截图前智能等待页面渲染完成"""
    import time as _time
    check_js = """
    () => {
        const body = document.body;
        if (!body) return false;
        const text = (body.innerText || '').trim();
        if (text.length < 30) return false;
        const imgs = document.images;
        let loaded = 0;
        for (const img of imgs) { if (img.complete && img.naturalWidth > 0) loaded++; }
        if (imgs.length > 0 && loaded / imgs.length < 0.3) return false;
        const loaders = document.querySelectorAll(
            '.loading, .spinner, .skeleton, [class*="loading"], [class*="spinner"], [aria-busy="true"]'
        );
        for (const el of loaders) {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            if (r.width > 10 && r.height > 10 && s.display !== 'none' && s.visibility !== 'hidden') return false;
        }
        return true;
    }
    """
    start = _time.time()
    while _time.time() - start < timeout:
        try:
            ready = await page.evaluate(check_js)
            if ready:
                return
        except Exception:
            pass
        await asyncio.sleep(0.6)


async def _screenshot_and_send(page, label: str = "页面截图") -> str:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = str(SCREENSHOT_DIR / f"login_{timestamp}.png")

    await _wait_for_page_content(page, timeout=8)
    await page.screenshot(path=filepath, full_page=False)
    logger.info(f"截图已保存: {filepath}")

    if _feishu_channel:
        chat_id = _feishu_channel.get_current_chat_id()
        if chat_id:
            try:
                await _feishu_channel.send_image(chat_id, filepath, caption=label)
                logger.info(f"截图已发送到飞书: {label}")
            except Exception as e:
                logger.error(f"截图发送失败: {e}")
    return filepath


# ==================== 智能选择器 ====================

async def _smart_click(page, selector: str):
    """智能点击：CSS → 文本 → placeholder → label → role"""
    for strategy in [
        lambda: page.click(selector, timeout=5000),
        lambda: page.get_by_text(selector, exact=False).first.click(timeout=5000),
        lambda: page.get_by_placeholder(selector).first.click(timeout=5000),
        lambda: page.get_by_label(selector).first.click(timeout=5000),
        lambda: page.get_by_role("button", name=selector).first.click(timeout=5000),
    ]:
        try:
            await strategy()
            return
        except Exception:
            continue
    raise Exception(f"找不到元素: {selector}")


async def _smart_fill(page, selector: str, value: str):
    """智能填写：CSS → placeholder → label"""
    for strategy in [
        lambda: page.fill(selector, value, timeout=5000),
        lambda: page.get_by_placeholder(selector).first.fill(value, timeout=5000),
        lambda: page.get_by_label(selector).first.fill(value, timeout=5000),
    ]:
        try:
            await strategy()
            return
        except Exception:
            continue
    raise Exception(f"找不到输入框: {selector}")


# ==================== 技能注册 ====================

@register(
    name="browser_open_for_login",
    description="打开网站准备登录，自动加载已保存的 Cookie，截图发飞书让用户指导操作。浏览器保持运行等待后续指令。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要打开的网站 URL"},
        },
        "required": ["url"],
    },
    risk_level="medium",
    category="browser",
)
async def browser_open_for_login(url: str) -> str:
    try:
        SharedBrowser = _get_shared_browser()
        browser = await SharedBrowser.get()
        page = browser._page
        context = browser._context

        loaded = await _load_cookies_for_url(context, url)
        cookie_msg = f"（已加载 {loaded} 个已保存的 Cookie）" if loaded else ""

        await page.goto(url, timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)

        title = await page.title()
        current_url = page.url
        domain = urlparse(current_url).netloc

        mode = "CDP 真实浏览器" if browser._using_cdp else "内置 Chromium"
        await _screenshot_and_send(page, f"已打开: {domain}")

        return (
            f"已打开网站并截图发送到飞书（{mode}模式）。{cookie_msg}\n"
            f"- 页面标题: {title}\n"
            f"- 当前 URL: {current_url}\n"
            f"请根据截图告诉我需要怎么操作（如：在用户名框输入xxx，密码框输入xxx，点击登录按钮）"
        )
    except Exception as e:
        return f"打开网站失败: {e}"


@register(
    name="browser_do_action",
    description=(
        "在当前打开的浏览器页面执行操作并截图反馈。支持的操作类型：\n"
        "- click: 点击元素（selector 可以是 CSS 选择器、按钮文字、placeholder 文本）\n"
        "- fill: 在输入框填写内容（selector 是输入框标识，value 是要填的值）\n"
        "- select: 下拉选择（selector 是选择框，value 是选项值）\n"
        "- scroll: 滚动页面（value 可选，默认向下 500px）\n"
        "- wait: 等待（value 是秒数，默认 2 秒）\n"
        "- press_key: 按键（value 是按键名如 Enter、Tab）"
    ),
    parameters={
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "description": "要执行的操作列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["click", "fill", "select", "scroll", "wait", "press_key"],
                            "description": "操作类型",
                        },
                        "selector": {
                            "type": "string",
                            "description": "CSS选择器、按钮文字、placeholder 或 label 文本",
                        },
                        "value": {
                            "type": "string",
                            "description": "填写的值（fill）、按键名（press_key）、等待秒数（wait）",
                            "default": "",
                        },
                    },
                    "required": ["type"],
                },
            },
        },
        "required": ["actions"],
    },
    risk_level="medium",
    category="browser",
)
async def browser_do_action(actions: list[dict]) -> str:
    SharedBrowser = _get_shared_browser()
    if not SharedBrowser.is_active():
        return "浏览器未打开，请先使用 browser_open_for_login 打开网站"

    browser = await SharedBrowser.get()
    page = browser._page

    results = []
    for i, action in enumerate(actions, 1):
        act_type = action.get("type", "")
        selector = action.get("selector", "")
        value = action.get("value", "")

        try:
            if act_type == "click":
                await _smart_click(page, selector)
                results.append(f"✅ {i}. 点击 '{selector}'")

            elif act_type == "fill":
                await _smart_fill(page, selector, value)
                results.append(f"✅ {i}. 输入 '{selector}' = '{value[:3]}***'")

            elif act_type == "select":
                await page.select_option(selector, value)
                results.append(f"✅ {i}. 选择 '{selector}' = '{value}'")

            elif act_type == "scroll":
                distance = int(value) if value else 500
                await page.evaluate(f"window.scrollBy(0, {distance})")
                results.append(f"✅ {i}. 滚动 {distance}px")

            elif act_type == "wait":
                seconds = int(value) if value else 2
                seconds = min(seconds, 30)
                await asyncio.sleep(seconds)
                results.append(f"✅ {i}. 等待 {seconds}s")

            elif act_type == "press_key":
                key = value or "Enter"
                await page.keyboard.press(key)
                results.append(f"✅ {i}. 按键 '{key}'")

            else:
                results.append(f"⚠️ {i}. 未知操作: {act_type}")
                continue

            await asyncio.sleep(0.5)

        except Exception as e:
            results.append(f"❌ {i}. {act_type} '{selector}' 失败: {e}")

    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    await _wait_for_page_content(page, timeout=6)

    title = await page.title()
    current_url = page.url
    await _screenshot_and_send(page, "操作完成截图")

    return (
        "操作执行结果:\n" + "\n".join(results)
        + f"\n\n当前页面: {title}\nURL: {current_url}\n"
        + "已截图发送到飞书，请确认操作结果。"
    )


@register(
    name="browser_login_save_cookies",
    description="保存当前浏览器的 Cookie（登录流程专用）。登录成功后调用，以后访问该网站时会自动加载。",
    parameters={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "网站域名（如 github.com），留空则自动从当前页面提取",
                "default": "",
            },
        },
    },
    risk_level="low",
    category="browser",
)
async def browser_save_cookies(domain: str = "") -> str:
    SharedBrowser = _get_shared_browser()
    if not SharedBrowser.is_active():
        return "浏览器未打开，无法保存 Cookie"

    try:
        browser = await SharedBrowser.get()
        context = browser._context
        page = browser._page

        if not domain:
            domain = urlparse(page.url).netloc

        cookies = await context.cookies()
        if not cookies:
            return "当前浏览器没有 Cookie，可能还未登录"

        count = _save_cookies(domain, cookies)
        return f"✅ 已保存 {domain} 的 {count} 个 Cookie，以后访问该网站会自动加载登录状态"
    except Exception as e:
        return f"保存 Cookie 失败: {e}"


@register(
    name="browser_close_login",
    description="关闭登录用的浏览器，释放内存。登录完成后建议调用。",
    parameters={"type": "object", "properties": {}},
    risk_level="low",
    category="browser",
)
async def browser_close_login() -> str:
    try:
        SharedBrowser = _get_shared_browser()
        await SharedBrowser.close()
        return "✅ 浏览器已关闭，内存已释放"
    except Exception as e:
        return f"关闭浏览器时出错: {e}"
