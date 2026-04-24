# -*- coding: utf-8 -*-
"""
技能注册表（Agent 框架版）

管理可用的 Skills 工具，提供执行接口和 JSON Schema。
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


@dataclass
class ToolCall:
    """工具调用数据类"""
    tool_name: str
    arguments: Dict[str, Any]
    reasoning: str = ""


class SkillRegistry:
    """
    技能注册表（Agent 框架版）

    管理所有可用 Skills，提供 JSON Schema 和执行接口。
    """

    def __init__(self, trains=None, stations=None):
        self.trains = trains
        self.stations = stations
        self.skills: Dict[str, BaseDispatchSkill] = create_skills(trains, stations)
        logger.info(f"技能注册表初始化完成，共 {len(self.skills)} 个技能")

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """获取 Tools JSON Schema"""
        return [
            # ---- 求解类技能 ----
            {
                "type": "function",
                "function": {
                    "name": "dispatch_solve_skill",
                    "description": (
                        "通用调度求解技能。支持参数化选择求解器（mip/fcfs/fsfs/max_delay_first/srpt/spt/noop）"
                        "和配置参数（优化目标、时间限制、最优性间隙）。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "train_ids": {"type": "array", "items": {"type": "string"}, "description": "受影响的列车ID列表"},
                            "station_codes": {"type": "array", "items": {"type": "string"}, "description": "相关车站编码列表"},
                            "delay_injection": {
                                "type": "object",
                                "description": "延误注入数据",
                                "properties": {
                                    "scenario_type": {"type": "string"},
                                    "injected_delays": {"type": "array"},
                                    "solver_config": {
                                        "type": "object",
                                        "properties": {
                                            "solver": {"type": "string", "enum": ["mip", "fcfs", "fsfs", "max_delay_first", "srpt", "spt", "noop"]},
                                            "optimization_objective": {"type": "string", "enum": ["min_max_delay", "min_total_delay", "min_avg_delay"]},
                                            "time_limit": {"type": "integer"},
                                            "optimality_gap": {"type": "number"}
                                        }
                                    }
                                }
                            }
                        },
                        "required": ["train_ids", "station_codes", "delay_injection"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "compare_strategies_skill",
                    "description": "运行多个求解策略并对比结果，自动选出最优方案。适用于需要在速度和最优性之间权衡的场景。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "train_ids": {"type": "array", "items": {"type": "string"}},
                            "station_codes": {"type": "array", "items": {"type": "string"}},
                            "delay_injection": {"type": "object"},
                            "strategies": {"type": "array", "items": {"type": "string"}, "description": "要求对比的求解器列表，如 ['fcfs', 'mip']"},
                            "time_budget": {"type": "integer", "description": "对比总时间预算（秒），默认300"}
                        },
                        "required": ["train_ids", "station_codes", "delay_injection"]
                    }
                }
            },
            # ---- 分析类技能 ----
            {
                "type": "function",
                "function": {
                    "name": "station_load_skill",
                    "description": "分析车站在不同时段的列车密度和负荷状况，判断高峰/平峰时段。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "station_code": {"type": "string", "description": "车站编码，如 SJP、BDD"}
                        },
                        "required": ["station_code"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "delay_propagation_skill",
                    "description": "预测延误沿线路的链式传播路径和影响范围，量化间接受影响的列车数和传播深度。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "train_ids": {"type": "array", "items": {"type": "string"}, "description": "直接受影响的列车ID列表"},
                            "location_code": {"type": "string", "description": "事故位置车站编码"},
                            "delay_minutes": {"type": "integer", "description": "初始延误分钟数"}
                        },
                        "required": ["train_ids", "location_code"]
                    }
                }
            },
            # ---- 查询类技能 ----
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
        return list(self.skills.keys())

    def get_tool_description(self, tool_name: str) -> Optional[str]:
        if tool_name in self.skills:
            return self.skills[tool_name].description
        return None

    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> DispatchSkillOutput:
        """执行指定的工具"""
        train_ids = arguments.get("train_ids", [])
        station_codes = arguments.get("station_codes", [])
        delay_injection = arguments.get("delay_injection", {})
        optimization_objective = arguments.get("optimization_objective", "min_max_delay")

        extra_kwargs = {}
        for key in ["train_id", "station_code", "from_station", "to_station",
                    "delay_minutes", "propagation_depth", "timetable_type",
                    "time_range", "include_position", "include_delay",
                    "strategies", "time_budget", "location_code"]:
            if key in arguments:
                extra_kwargs[key] = arguments[key]

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
        return tool_name in self.skills


# ============================================
# 全局实例
# ============================================

_skill_registry: Optional[SkillRegistry] = None


def get_skill_registry(trains=None, stations=None) -> SkillRegistry:
    """获取技能注册表实例（单例模式）"""
    global _skill_registry
    if _skill_registry is None:
        _skill_registry = SkillRegistry(trains, stations)
    return _skill_registry
