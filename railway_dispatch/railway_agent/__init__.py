# -*- coding: utf-8 -*-
"""
railway_agent - 铁路调度Agent模块
包含Qwen Agent、Rule Agent、Tool注册表、Prompts和Skills
"""

# RuleAgent不依赖modelscope，可以独立使用
from railway_agent.rule_agent import RuleAgent, create_rule_agent, AgentResult
from railway_agent.dispatch_skills import (
    BaseDispatchSkill,
    TemporarySpeedLimitSkill,
    SuddenFailureSkill,
    create_skills,
    execute_skill,
    DispatchSkillOutput
)
from railway_agent.tool_registry import ToolRegistry, ToolCall

# 调度比较技能
from railway_agent.comparison_skill import (
    SchedulerComparisonSkill,
    create_comparison_skill
)

# QwenAgent依赖modelscope，延迟导入以避免在RuleAgent模式下加载
def get_qwen_agent_class():
    """延迟导入QwenAgent类"""
    from railway_agent.qwen_agent import QwenAgent, create_qwen_agent
    return QwenAgent, create_qwen_agent

# 为了向后兼容，保留QwenAgent的导入（但会在导入时触发modelscope）
# 如果只需要RuleAgent，可以只导入RuleAgent相关类

__all__ = [
    # Rule Agent (推荐，无需大模型)
    "RuleAgent",
    "create_rule_agent",
    "AgentResult",
    # Skills
    "BaseDispatchSkill",
    "TemporarySpeedLimitSkill",
    "SuddenFailureSkill",
    "create_skills",
    "execute_skill",
    "DispatchSkillOutput",
    # Tools
    "ToolRegistry",
    "ToolCall",
    # Comparison Skill
    "SchedulerComparisonSkill",
    "create_comparison_skill",
    # Qwen Agent (需要大模型)
    "get_qwen_agent_class"
]
