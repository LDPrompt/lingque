from .registry import registry

# 导入所有技能模块以触发注册
from . import file_ops
from . import web_browse
from . import code_runner
from . import email_calendar
from . import scheduler_skills  # P2: 调度器相关技能
from . import skill_generator   # P3: 技能自动生成
from . import memory_skills     # P3: 记忆增强技能
from . import skill_market      # 技能市场
from . import feishu_group      # 飞书群聊（@ 成员、查群成员）
from . import browser_login     # 截图指导式登录
from . import workflow_skills   # P4: 声明式工作流引擎
from . import self_improvement  # P2: 自我学习系统
from . import feishu_docs       # 飞书云文档（创建/写入/分享）
from . import ralph_skills      # Ralph Loop 自主循环
from . import mcp_skills        # MCP 包管理
from . import knowledge_skills  # 知识图谱
from . import credential_skills # 凭证保险箱

# P3: 条件导入（避免未安装依赖时报错）
try:
    from ..sandbox import docker_sandbox  # Docker 沙箱技能
except ImportError:
    pass

try:
    from ..browser import playwright_browser  # 浏览器自动化技能
except ImportError:
    pass

__all__ = ["registry"]
