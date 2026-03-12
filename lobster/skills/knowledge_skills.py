"""
🐦 灵雀 - 知识图谱技能

提供知识图谱的用户交互接口：
- 添加实体和关系
- 查询实体信息
- 探索关联
- 查看图谱统计
"""

import logging
from .registry import register, SkillResult

logger = logging.getLogger("lingque.skills.knowledge")


def _get_kg():
    """延迟导入，避免循环依赖"""
    from ..memory.knowledge_graph import get_knowledge_graph
    return get_knowledge_graph()


@register(
    name="knowledge_add_entity",
    description=(
        "向知识图谱添加一个实体（人物、组织、地点、概念等）。\n"
        "知识图谱用于存储和检索结构化的知识信息。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "实体名称，如 '张三'、'阿里巴巴'"
            },
            "entity_type": {
                "type": "string",
                "description": "实体类型",
                "enum": ["person", "organization", "place", "concept", "event", "other"]
            },
            "properties": {
                "type": "object",
                "description": "实体属性，如 {'职业': '工程师', '年龄': '30'}",
                "additionalProperties": {"type": "string"}
            },
        },
        "required": ["name", "entity_type"],
    },
    risk_level="low",
)
async def knowledge_add_entity(
    name: str,
    entity_type: str,
    properties: dict = None
) -> SkillResult:
    """添加实体"""
    kg = _get_kg()
    if not kg:
        return SkillResult(success=False, error="知识图谱未初始化")
    
    entity = kg.add_entity(name, entity_type, properties)
    
    props_str = ""
    if entity.properties:
        props_str = "\n属性: " + ", ".join(f"{k}={v}" for k, v in entity.properties.items())
    
    return SkillResult(
        success=True,
        data=(
            f"✅ 实体已添加\n\n"
            f"📌 **{entity.name}** ({entity.type})\n"
            f"ID: `{entity.id}`"
            f"{props_str}"
        )
    )


@register(
    name="knowledge_add_relation",
    description="在知识图谱中添加两个实体之间的关系",
    parameters={
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "源实体名称"
            },
            "relation": {
                "type": "string",
                "description": "关系类型，如 'works_at'、'knows'、'likes'、'located_in'"
            },
            "target": {
                "type": "string",
                "description": "目标实体名称"
            },
        },
        "required": ["source", "relation", "target"],
    },
    risk_level="low",
)
async def knowledge_add_relation(
    source: str,
    relation: str,
    target: str
) -> SkillResult:
    """添加关系"""
    kg = _get_kg()
    if not kg:
        return SkillResult(success=False, error="知识图谱未初始化")
    
    rel = kg.add_relation(source, relation, target)
    
    return SkillResult(
        success=True,
        data=f"✅ 关系已添加: **{source}** --[{relation}]--> **{target}**"
    )


@register(
    name="knowledge_query",
    description="查询知识图谱中某个实体的详细信息和关联",
    parameters={
        "type": "object",
        "properties": {
            "entity_name": {
                "type": "string",
                "description": "要查询的实体名称"
            },
        },
        "required": ["entity_name"],
    },
    risk_level="low",
)
async def knowledge_query(entity_name: str) -> SkillResult:
    """查询实体"""
    kg = _get_kg()
    if not kg:
        return SkillResult(success=False, error="知识图谱未初始化")
    
    result = kg.query(entity_name)
    
    if not result["entity"]:
        return SkillResult(success=True, data=f"❌ 未找到实体: {entity_name}")
    
    entity = result["entity"]
    lines = [
        f"📌 **{entity.name}** ({entity.type})",
        f"ID: `{entity.id}`",
        f"提及次数: {entity.mention_count}",
    ]
    
    if entity.properties:
        lines.append("\n📋 **属性**")
        for k, v in entity.properties.items():
            lines.append(f"  - {k}: {v}")
    
    if result["relations"]:
        lines.append(f"\n🔗 **关系** ({len(result['relations'])} 个)")
        for rel in result["relations"][:10]:
            if rel.source == entity.id:
                lines.append(f"  → {rel.relation} → {rel.target}")
            else:
                lines.append(f"  ← {rel.relation} ← {rel.source}")
    
    if result["related_entities"]:
        lines.append(f"\n👥 **相关实体** ({len(result['related_entities'])} 个)")
        for e in result["related_entities"][:5]:
            lines.append(f"  - {e.name} ({e.type})")
    
    return SkillResult(success=True, data="\n".join(lines))


@register(
    name="knowledge_search",
    description="在知识图谱中搜索实体（支持时间衰减排序）",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词"
            },
            "entity_type": {
                "type": "string",
                "description": "按类型筛选 (可选)",
                "enum": ["person", "organization", "place", "concept", "event", "other"]
            },
            "use_time_decay": {
                "type": "boolean",
                "description": "是否使用时间衰减权重排序（旧知识排名靠后）",
                "default": True
            },
        },
        "required": ["query"],
    },
    risk_level="low",
)
async def knowledge_search(
    query: str,
    entity_type: str = None,
    use_time_decay: bool = True
) -> SkillResult:
    """搜索实体"""
    kg = _get_kg()
    if not kg:
        return SkillResult(success=False, error="知识图谱未初始化")
    
    results = kg.search_entities(query, entity_type, use_time_decay=use_time_decay)
    
    if not results:
        return SkillResult(success=True, data=f"未找到匹配 '{query}' 的实体")
    
    lines = [f"🔍 **搜索结果** ({len(results)} 个)\n"]
    for entity in results:
        weight_info = f"权重: {entity.get_effective_weight():.2f}" if use_time_decay else f"提及: {entity.mention_count}"
        lines.append(f"📌 **{entity.name}** ({entity.type}) - {weight_info}")
        if entity.properties:
            props = ", ".join(f"{k}={v}" for k, v in list(entity.properties.items())[:3])
            lines.append(f"   {props}")
    
    return SkillResult(success=True, data="\n".join(lines))


@register(
    name="knowledge_hybrid_search",
    description=(
        "混合搜索：同时搜索知识图谱和向量记忆库。\n"
        "适合需要综合历史知识的场景，如 '我之前跟谁讨论过XX' 或 '关于XX的所有信息'。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询"
            },
            "kg_weight": {
                "type": "number",
                "description": "知识图谱结果权重 (0-1)，向量结果权重为 1-kg_weight",
                "default": 0.5
            },
        },
        "required": ["query"],
    },
    risk_level="low",
)
async def knowledge_hybrid_search(query: str, kg_weight: float = 0.5) -> SkillResult:
    """混合搜索"""
    kg = _get_kg()
    if not kg:
        return SkillResult(success=False, error="知识图谱未初始化")
    
    results = await kg.hybrid_search(query, kg_weight=kg_weight)
    
    if not results:
        return SkillResult(success=True, data=f"未找到匹配 '{query}' 的结果")
    
    lines = [f"🔍 **混合搜索结果** ({len(results)} 个)\n"]
    
    for item in results:
        source_emoji = "📌" if item["source"] == "kg" else "📝"
        source_label = "知识图谱" if item["source"] == "kg" else "向量记忆"
        
        if item["source"] == "kg":
            lines.append(f"{source_emoji} **{item['name']}** ({item['entity_type']}) [{source_label}]")
        else:
            content_preview = item["content"][:80] + "..." if len(item["content"]) > 80 else item["content"]
            lines.append(f"{source_emoji} {content_preview} [{source_label}]")
    
    return SkillResult(success=True, data="\n".join(lines))


@register(
    name="knowledge_find_path",
    description="查找两个实体之间的关联路径",
    parameters={
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "起始实体名称"
            },
            "target": {
                "type": "string",
                "description": "目标实体名称"
            },
        },
        "required": ["source", "target"],
    },
    risk_level="low",
)
async def knowledge_find_path(source: str, target: str) -> SkillResult:
    """查找路径"""
    kg = _get_kg()
    if not kg:
        return SkillResult(success=False, error="知识图谱未初始化")
    
    paths = kg.find_path(source, target)
    
    if not paths:
        return SkillResult(success=True, data=f"未找到 {source} 和 {target} 之间的路径")
    
    lines = [f"🔗 **{source} → {target}** 的路径:\n"]
    for i, path in enumerate(paths[:3], 1):
        path_str = " → ".join(path)
        lines.append(f"{i}. {path_str}")
    
    return SkillResult(success=True, data="\n".join(lines))


@register(
    name="knowledge_extract",
    description="从一段文本中自动抽取实体和关系并添加到知识图谱",
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要分析的文本"
            },
            "auto_add": {
                "type": "boolean",
                "description": "是否自动添加到图谱",
                "default": True
            },
        },
        "required": ["text"],
    },
    risk_level="low",
)
async def knowledge_extract(text: str, auto_add: bool = True) -> SkillResult:
    """从文本抽取"""
    kg = _get_kg()
    if not kg:
        return SkillResult(success=False, error="知识图谱未初始化")

    if len(text) > 10000:
        text = text[:10000]

    if hasattr(kg, 'extract_from_text_async'):
        result = await kg.extract_from_text_async(text, auto_add)
    else:
        result = kg.extract_from_text(text, auto_add)
    
    lines = ["📝 **文本分析结果**\n"]
    
    if result["entities"]:
        lines.append(f"**实体** ({len(result['entities'])} 个)")
        for e in result["entities"][:10]:
            lines.append(f"  - {e['name']} ({e['type']})")
    else:
        lines.append("未发现实体")
    
    if result["relations"]:
        lines.append(f"\n**关系** ({len(result['relations'])} 个)")
        for r in result["relations"][:5]:
            lines.append(f"  - {r['source']} --{r['relation']}--> {r['target']}")
    
    if auto_add and (result["entities"] or result["relations"]):
        lines.append("\n✅ 已自动添加到知识图谱")
    
    return SkillResult(success=True, data="\n".join(lines))


@register(
    name="knowledge_stats",
    description="查看知识图谱的统计信息",
    parameters={"type": "object", "properties": {}, "required": []},
    risk_level="low",
)
async def knowledge_stats() -> SkillResult:
    """查看统计"""
    kg = _get_kg()
    if not kg:
        return SkillResult(success=False, error="知识图谱未初始化")
    
    return SkillResult(success=True, data=kg.summary())
