# -*- coding: utf-8 -*-
"""
技能注册表（新架构）
替代旧架构的 tool_registry.py，适配新的适配器模式
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import logging

from .skills import (
    create_skills,
    execute_skill,
    DispatchSkillOutput,
    BaseDispatchSkill
)

logger = logging.getLogger(__name__)


# ============================================
# 工具调用数据类
# ============================================

@dataclass
class ToolCall:
    """工具调用数据类"""
    tool_name: str
    arguments: Dict[str, Any]
    reasoning: str = ""


# ============================================
# 技能注册表类
# ============================================

class SkillRegistry:
    """
    技能注册表（新架构）
    管理可用的Skills工具，提供执行接口

    替代旧架构的 ToolRegistry
    """

    def __init__(self, trains=None, stations=None):
        """
        初始化技能注册表

        Args:
            trains: 列车列表
            stations: 车站列表
        """
        self.trains = trains
        self.stations = stations
        self.skills: Dict[str, BaseDispatchSkill] = create_skills(trains, stations)
        logger.info(f"技能注册表初始化完成，共 {len(self.skills)} 个技能")

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """
        获取Tools JSON Schema（用于兼容旧接口）

        Returns:
            List[Dict]: Tools定义列表
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "temporary_speed_limit_skill",
                    "description": "处理临时限速场景的列车调度",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "train_ids": {"type": "array", "items": {"type": "string"}},
                            "station_codes": {"type": "array", "items": {"type": "string"}},
                            "delay_injection": {"type": "object"}
                        },
                        "required": ["train_ids", "station_codes", "delay_injection"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "sudden_failure_skill",
                    "description": "处理突发故障场景的列车调度",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "train_ids": {"type": "array", "items": {"type": "string"}},
                            "station_codes": {"type": "array", "items": {"type": "string"}},
                            "delay_injection": {"type": "object"}
                        },
                        "required": ["train_ids", "station_codes", "delay_injection"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "section_interrupt_skill",
                    "description": "处理区间中断场景的列车调度",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "train_ids": {"type": "array", "items": {"type": "string"}},
                            "station_codes": {"type": "array", "items": {"type": "string"}},
                            "delay_injection": {"type": "object"}
                        },
                        "required": ["train_ids", "station_codes", "delay_injection"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_train_status",
                    "description": "查询指定列车的实时运行状态",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "train_id": {"type": "string"}
                        },
                        "required": ["train_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "query_timetable",
                    "description": "查询列车时刻表或车站时刻表",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "train_id": {"type": "string"},
                            "station_code": {"type": "string"}
                        }
                    }
                }
            }
        ]

    def get_tool_names(self) -> List[str]:
        """
        获取所有可用工具名称

        Returns:
            List[str]: 工具名称列表
        """
        return list(self.skills.keys())

    def get_tool_description(self, tool_name: str) -> Optional[str]:
        """
        获取指定工具的描述

        Args:
            tool_name: 工具名称

        Returns:
            Optional[str]: 工具描述，不存在则返回None
        """
        if tool_name in self.skills:
            return self.skills[tool_name].description
        return None

    def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> DispatchSkillOutput:
        """
        执行指定的工具

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            DispatchSkillOutput: 执行结果
        """
        # 提取标准参数
        train_ids = arguments.get("train_ids", [])
        station_codes = arguments.get("station_codes", [])
        delay_injection = arguments.get("delay_injection", {})
        optimization_objective = arguments.get("optimization_objective", "min_max_delay")

        # 提取额外参数（用于查询类技能）
        extra_kwargs = {}
        for key in ["train_id", "station_code", "from_station", "to_station",
                    "delay_minutes", "propagation_depth", "timetable_type",
                    "time_range", "include_position", "include_delay"]:
            if key in arguments:
                extra_kwargs[key] = arguments[key]

        # 执行Skill
        return execute_skill(
            skill_name=tool_name,
            skills=self.skills,
            train_ids=train_ids,
            station_codes=station_codes,
            delay_injection=delay_injection,
            optimization_objective=optimization_objective,
            **extra_kwargs
        )

    def has_tool(self, tool_name: str) -> bool:
        """
        检查工具是否存在

        Args:
            tool_name: 工具名称

        Returns:
            bool: 是否存在
        """
        return tool_name in self.skills


# ============================================
# 全局实例
# ============================================

_skill_registry: Optional[SkillRegistry] = None


def get_skill_registry(trains=None, stations=None) -> SkillRegistry:
    """
    获取技能注册表实例（单例模式）

    Args:
        trains: 列车列表（仅第一次初始化时使用）
        stations: 车站列表（仅第一次初始化时使用）

    Returns:
        SkillRegistry: 技能注册表实例
    """
    global _skill_registry
    if _skill_registry is None:
        _skill_registry = SkillRegistry(trains, stations)
    return _skill_registry
