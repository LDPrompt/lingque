"""
示例插件 - 放入 plugins/ 目录即可自动加载

文件名以 _ 开头的不会被加载，去掉前缀即可启用。

插件需要定义 register(registry) 函数来注册技能。
"""

PLUGIN_META = {
    "name": "example",
    "description": "示例插件 - 展示如何编写灵雀插件",
    "version": "1.0",
}


async def hello(name: str = "World") -> str:
    """一个简单的问候技能"""
    return f"Hello, {name}! 这是来自示例插件的问候。"


async def random_quote() -> str:
    """返回一条随机名言"""
    import random
    quotes = [
        "代码是写给人看的，顺便能在机器上运行。 —— Harold Abelson",
        "过早优化是万恶之源。 —— Donald Knuth",
        "简单是可靠的先决条件。 —— Edsger Dijkstra",
        "先让它工作，再让它正确，最后让它快。 —— Kent Beck",
    ]
    return random.choice(quotes)


def register(registry):
    """注册插件技能到全局 Registry"""

    registry.register(
        name="plugin_hello",
        description="来自示例插件的问候",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要问候的名字"},
            },
            "required": [],
        },
        risk_level="low",
        category="plugin",
    )(hello)

    registry.register(
        name="random_quote",
        description="返回一条随机编程名言",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        risk_level="low",
        category="plugin",
    )(random_quote)
