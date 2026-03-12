"""
🐦 技能: 网页浏览 & 信息搜集
"""

import httpx
from bs4 import BeautifulSoup
from .registry import registry


@registry.register(
    name="fetch_webpage",
    description="获取网页内容，返回提取后的纯文本（去除 HTML 标签）",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "网页 URL"},
            "max_length": {
                "type": "integer",
                "description": "返回内容最大字符数",
                "default": 5000,
            },
        },
        "required": ["url"],
    },
    risk_level="low",
    category="web",
)
async def fetch_webpage(url: str, max_length: int = 5000) -> str:
    try:
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "MyLobster/1.0 (Personal AI Assistant)"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # 移除无用标签
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)

        # 压缩多余空行
        lines = [line for line in text.splitlines() if line.strip()]
        text = "\n".join(lines)

        if len(text) > max_length:
            text = text[:max_length] + "\n...(内容已截断)"

        return f"URL: {url}\n内容:\n{text}"

    except Exception as e:
        return f"获取网页失败: {e}"


@registry.register(
    name="web_search",
    description="通过搜索引擎搜索信息（使用 DuckDuckGo，无需 API Key）",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "最大结果数", "default": 5},
        },
        "required": ["query"],
    },
    risk_level="low",
    category="web",
)
async def web_search(query: str, max_results: int = 5) -> str:
    """使用 DuckDuckGo HTML 搜索（无需 API）"""
    try:
        async with httpx.AsyncClient(
            timeout=10,
            headers={"User-Agent": "MyLobster/1.0"},
        ) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
            )
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        results = []

        for i, result in enumerate(soup.select(".result"), 1):
            if i > max_results:
                break
            title_el = result.select_one(".result__title")
            snippet_el = result.select_one(".result__snippet")
            link_el = result.select_one(".result__url")

            title = title_el.get_text(strip=True) if title_el else "无标题"
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            link = link_el.get_text(strip=True) if link_el else ""

            results.append(f"{i}. {title}\n   {link}\n   {snippet}")

        if not results:
            return f"未找到关于 '{query}' 的搜索结果"

        return f"搜索 '{query}' 的结果:\n\n" + "\n\n".join(results)

    except Exception as e:
        return f"搜索失败: {e}"
