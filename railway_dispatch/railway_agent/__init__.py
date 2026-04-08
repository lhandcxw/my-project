# -*- coding: utf-8 -*-
"""
railway_agent - 铁路调度Agent模块（新架构v2）
包含新架构Agent、技能注册表、适配器和技能
"""

# 新架构 Agent 和接口
from railway_agent.agents import RuleAgent, create_rule_agent, AgentResult
from railway_agent.adapters.skill_registry import SkillRegistry, get_skill_registry
from railway_agent.adapters.skills import (
    BaseDispatchSkill,
    TemporarySpeedLimitSkill,
    SuddenFailureSkill,
    SectionInterruptSkill,
    GetTrainStatusSkill,
    QueryTimetableSkill,
    create_skills,
    execute_skill,
    DispatchSkillOutput
)

# 调度比较技能（如果存在）
try:
    from railway_agent.comparison_skill import (
        SchedulerComparisonSkill,
        create_comparison_skill
    )
    HAS_COMPARISON_SKILL = True
except ImportError:
    HAS_COMPARISON_SKILL = False

# 为了向后兼容，ToolRegistry 指向 SkillRegistry
ToolRegistry = SkillRegistry

__all__ = [
    # Rule Agent (新架构，无需大模型)
    "RuleAgent",
    "create_rule_agent",
    "AgentResult",
    # Skills
    "BaseDispatchSkill",
    "TemporarySpeedLimitSkill",
    "SuddenFailureSkill",
    "SectionInterruptSkill",
    "GetTrainStatusSkill",
    "QueryTimetableSkill",
    "create_skills",
    "execute_skill",
    "DispatchSkillOutput",
    # Tools (SkillRegistry)
    "SkillRegistry",
    "get_skill_registry",
    "ToolRegistry",  # 兼容旧接口
    # Comparison Skill (可选）
]

# 如果有比较技能，也导出
if HAS_COMPARISON_SKILL:
    __all__.extend([
        "SchedulerComparisonSkill",
        "create_comparison_skill"
    ])
