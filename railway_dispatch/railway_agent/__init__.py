# -*- coding: utf-8 -*-
"""
railway_agent - LLM-TTRA 铁路调度Agent模块
基于大模型的列车时刻表重排系统
"""

# 主入口：工作流引擎（推荐）
from railway_agent.llm_workflow_engine_v2 import (
    LLMWorkflowEngineV2,
    create_workflow_engine
)

# Agent接口（兼容层）
from railway_agent.agents import RuleAgent, create_rule_agent, AgentResult

# 技能系统
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

# 工作流分层模块
from railway_agent.workflow import (
    Layer1DataModeling,
    Layer2Planner,
    Layer3Solver,
    Layer4Evaluation
)

# 快照构建器
from railway_agent.snapshot_builder import SnapshotBuilder, get_snapshot_builder

# 会话管理
from railway_agent.session_manager import SessionManager, get_session_manager

# 适配器
from railway_agent.adapters.llm_adapter import LLMAdapter, get_llm_adapter
from railway_agent.adapters.llm_prompt_adapter import LLMPromptAdapter, get_llm_prompt_adapter

# 为了向后兼容，ToolRegistry 指向 SkillRegistry
ToolRegistry = SkillRegistry

__all__ = [
    # 主入口：工作流引擎
    "LLMWorkflowEngineV2",
    "create_workflow_engine",
    # Agent接口（兼容层）
    "RuleAgent",
    "create_rule_agent",
    "AgentResult",
    # 工作流分层模块
    "Layer1DataModeling",
    "Layer2Planner",
    "Layer3Solver",
    "Layer4Evaluation",
    # 快照构建器
    "SnapshotBuilder",
    "get_snapshot_builder",
    # 会话管理
    "SessionManager",
    "get_session_manager",
    # LLM适配器
    "LLMAdapter",
    "get_llm_adapter",
    "LLMPromptAdapter",
    "get_llm_prompt_adapter",
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
    # 技能注册表
    "SkillRegistry",
    "get_skill_registry",
    "ToolRegistry",
]
