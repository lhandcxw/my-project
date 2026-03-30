# -*- coding: utf-8 -*-
"""
铁路调度系统 - MCP Tools注册表模块
管理可用的Skills工具，提供JSON Schema定义和执行接口
"""

from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
import json
import logging

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from railway_agent.dispatch_skills import (
    create_skills,
    execute_skill,
    DispatchSkillOutput,
    BaseDispatchSkill
)
from solver.mip_scheduler import MIPScheduler

logger = logging.getLogger(__name__)


# ============================================
# Tools JSON Schema 定义
# ============================================

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "temporary_speed_limit_skill",
            "description": "处理临时限速场景的列车调度。适用于：铁路线路临时限速导致的多列列车延误调整。输入受影响列车列表、限速区段、限速值、持续时间，输出调整后的时刻表和延误统计。",
            "parameters": {
                "type": "object",
                "properties": {
                    "train_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "受影响列车ID列表，如 ['G1001', 'G1003']"
                    },
                    "station_codes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "涉及的车站编码列表，如 ['BJP', 'TJG', 'JNZ', 'NJH', 'SHH']"
                    },
                    "delay_injection": {
                        "type": "object",
                        "description": "延误注入数据，包含scenario_type、scenario_id、injected_delays、affected_trains、scenario_params等字段"
                    },
                    "optimization_objective": {
                        "type": "string",
                        "enum": ["min_max_delay", "min_avg_delay"],
                        "default": "min_max_delay",
                        "description": "优化目标：min_max_delay=最小化最大延误，min_avg_delay=最小化平均延误"
                    }
                },
                "required": ["train_ids", "station_codes", "delay_injection"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sudden_failure_skill",
            "description": "处理突发故障场景的列车调度。适用于：列车设备故障、区间占用等单列车故障场景。输入故障列车信息、故障位置、预计恢复时间，输出调整后的时刻表和延误统计。",
            "parameters": {
                "type": "object",
                "properties": {
                    "train_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "受影响列车ID列表，通常只有一个故障列车"
                    },
                    "station_codes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "涉及的车站编码列表"
                    },
                    "delay_injection": {
                        "type": "object",
                        "description": "延误注入数据"
                    },
                    "optimization_objective": {
                        "type": "string",
                        "enum": ["min_max_delay", "min_avg_delay"],
                        "default": "min_max_delay",
                        "description": "优化目标"
                    }
                },
                "required": ["train_ids", "station_codes", "delay_injection"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "section_interrupt_skill",
            "description": "处理区间中断场景的列车调度（预留接口，当前版本暂不支持）。适用于：线路中断、严重自然灾害等导致区间无法通行。需要处理车底调配、乘务员调整等复杂情况。",
            "parameters": {
                "type": "object",
                "properties": {
                    "train_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "受影响列车ID列表"
                    },
                    "station_codes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "涉及的车站编码列表"
                    },
                    "delay_injection": {
                        "type": "object",
                        "description": "延误注入数据"
                    },
                    "optimization_objective": {
                        "type": "string",
                        "enum": ["min_max_delay", "min_avg_delay"],
                        "default": "min_max_delay",
                        "description": "优化目标"
                    }
                },
                "required": ["train_ids", "station_codes", "delay_injection"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scheduler_comparison_skill",
            "description": "比较多种调度方法（FCFS、MIP等），根据用户偏好选择最优调度方案。适用于需要综合比较不同调度策略的场景。输出各调度方法的比较结果、推荐方案、详细指标对比。",
            "parameters": {
                "type": "object",
                "properties": {
                    "train_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "受影响列车ID列表"
                    },
                    "station_codes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "涉及的车站编码列表"
                    },
                    "delay_injection": {
                        "type": "object",
                        "description": "延误注入数据，包含scenario_params.user_preference指定比较准则"
                    },
                    "optimization_objective": {
                        "type": "string",
                        "enum": ["min_max_delay", "min_avg_delay"],
                        "default": "min_max_delay",
                        "description": "优化目标"
                    }
                },
                "required": ["train_ids", "station_codes", "delay_injection"]
            }
        }
    },
    # ========== 新增工具：列车状态查询 ==========
    {
        "type": "function",
        "function": {
            "name": "get_train_status",
            "description": "查询指定列车的实时运行状态。返回列车的当前位置、晚点情况、下一站信息等。适用于：调度员需要了解某列车的实时状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "train_id": {
                        "type": "string",
                        "description": "列车ID，如 'G1215'"
                    },
                    "include_position": {
                        "type": "boolean",
                        "default": True,
                        "description": "是否包含位置信息"
                    },
                    "include_delay": {
                        "type": "boolean",
                        "default": True,
                        "description": "是否包含晚点信息"
                    }
                },
                "required": ["train_id"]
            }
        }
    },
    # ========== 新增工具：晚点传播分析 ==========
    {
        "type": "function",
        "function": {
            "name": "analyze_delay_propagation",
            "description": "分析晚点对后续列车的影响范围和程度。返回受影响的列车列表、传播层级、预计晚点时间等。适用于：评估某列晚点后对整体运行图的影响。",
            "parameters": {
                "type": "object",
                "properties": {
                    "train_id": {
                        "type": "string",
                        "description": "晚点列车ID"
                    },
                    "delay_minutes": {
                        "type": "integer",
                        "description": "晚点时间（分钟）"
                    },
                    "propagation_depth": {
                        "type": "integer",
                        "default": 3,
                        "description": "传播深度（分析几代后续列车），默认3"
                    }
                },
                "required": ["train_id", "delay_minutes"]
            }
        }
    },
    # ========== 新增工具：时刻表查询 ==========
    {
        "type": "function",
        "function": {
            "name": "query_timetable",
            "description": "查询列车时刻表信息。可以按列车ID查询特定列车的时刻表，或按车站查询所有到发列车。适用于：查询计划时刻表或历史时刻表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "train_id": {
                        "type": "string",
                        "description": "列车ID（可选，如不指定则查询车站所有列车）"
                    },
                    "station_code": {
                        "type": "string",
                        "description": "车站编码（可选）"
                    },
                    "timetable_type": {
                        "type": "string",
                        "enum": ["plan", "actual", "both"],
                        "default": "plan",
                        "description": "时刻表类型：plan=计划时刻表，actual=实际时刻表，both=两者都返回"
                    }
                }
            }
        }
    },
    # ========== 新增工具：车站状态查询 ==========
    {
        "type": "function",
        "function": {
            "name": "get_station_status",
            "description": "查询车站的实时状态信息。包括到发线占用情况、正在接发车作业的列车等。适用于：了解车站接发车能力。",
            "parameters": {
                "type": "object",
                "properties": {
                    "station_code": {
                        "type": "string",
                        "description": "车站编码，如 'XSD'"
                    }
                },
                "required": ["station_code"]
            }
        }
    },
    # ========== 新增工具：运力分析 ==========
    {
        "type": "function",
        "function": {
            "name": "analyze_capacity",
            "description": "分析区间的通过能力和冗余运力。返回区间的最大通过能力、当前占用情况、可增加的列车数量等。适用于：评估区间承载能力和加开列车可能性。",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_station": {
                        "type": "string",
                        "description": "起始站编码"
                    },
                    "to_station": {
                        "type": "string",
                        "description": "终止站编码"
                    },
                    "time_range": {
                        "type": "string",
                        "description": "分析的时间范围，如 '09:00-12:00'"
                    }
                },
                "required": ["from_station", "to_station"]
            }
        }
    }
]


# ============================================
# Tool 注册表类
# ============================================

class ToolRegistry:
    """
    MCP Tools注册表
    
    管理可用的Skills工具，提供：
    1. JSON Schema定义（用于Function Calling）
    2. 执行接口封装
    """
    
    def __init__(self, scheduler: MIPScheduler, trains=None, stations=None):
        """
        初始化Tool注册表
        
        Args:
            scheduler: MIP调度器实例
            trains: 列车列表（用于比较功能）
            stations: 车站列表（用于比较功能）
        """
        self.scheduler = scheduler
        self.trains = trains or scheduler.trains
        self.stations = stations or scheduler.stations
        self.skills: Dict[str, BaseDispatchSkill] = create_skills(scheduler)
        
        # 初始化比较技能
        self._init_comparison_skill()
    
    def _init_comparison_skill(self):
        """初始化调度比较技能"""
        try:
            from railway_agent.comparison_skill import SchedulerComparisonSkill
            self.comparison_skill = SchedulerComparisonSkill(self.trains, self.stations)
            self.skills["scheduler_comparison_skill"] = self.comparison_skill
            logger.info("调度比较技能初始化完成")
        except Exception as e:
            logger.warning(f"调度比较技能初始化失败: {e}")
            self.comparison_skill = None
    
    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """
        获取Tools JSON Schema
        
        Returns:
            List[Dict]: Tools定义列表
        """
        return TOOLS_SCHEMA
    
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
# 工具调用解析器
# ============================================

@dataclass
class ToolCall:
    """工具调用数据类"""
    tool_name: str
    arguments: Dict[str, Any]
    reasoning: str = ""


def parse_tool_call(response_text: str) -> Optional[ToolCall]:
    """
    从模型响应中解析工具调用
    
    Args:
        response_text: 模型响应文本
        
    Returns:
        Optional[ToolCall]: 解析出的工具调用，解析失败返回None
    """
    import re
    
    # 尝试提取JSON块
    json_patterns = [
        r'```json\s*([\s\S]*?)\s*```',  # ```json ... ```
        r'```\s*([\s\S]*?)\s*```',       # ``` ... ```
        r'\{[\s\S]*\}'                    # 直接的JSON对象
    ]
    
    for pattern in json_patterns:
        matches = re.findall(pattern, response_text)
        for match in matches:
            try:
                # 尝试解析JSON
                if pattern == r'\{[\s\S]*\}':
                    json_str = match if match.startswith('{') else '{' + match + '}'
                else:
                    json_str = match
                
                # 替换单引号为双引号（处理模型输出的非标准JSON）
                json_str = json_str.replace("'", '"')
                
                data = json.loads(json_str)

                # 验证必要字段
                if "tool_name" in data and "arguments" in data:
                    # 优先使用thinking字段，如果没有则用reasoning
                    reasoning = data.get("thinking") or data.get("reasoning", "")
                    return ToolCall(
                        tool_name=data["tool_name"],
                        arguments=data["arguments"],
                        reasoning=reasoning
                    )
            except json.JSONDecodeError:
                continue
    
    return None


def validate_tool_call(tool_call: ToolCall, registry: ToolRegistry) -> Tuple[bool, str]:
    """
    验证工具调用是否有效
    
    Args:
        tool_call: 工具调用
        registry: 工具注册表
        
    Returns:
        Tuple[bool, str]: (是否有效, 错误信息)
    """
    # 检查工具是否存在
    if not registry.has_tool(tool_call.tool_name):
        return False, f"工具 '{tool_call.tool_name}' 不存在"
    
    # 检查必需参数
    args = tool_call.arguments
    if "train_ids" not in args or not args["train_ids"]:
        return False, "缺少必需参数: train_ids"
    if "station_codes" not in args or not args["station_codes"]:
        return False, "缺少必需参数: station_codes"
    if "delay_injection" not in args:
        return False, "缺少必需参数: delay_injection"
    
    return True, ""


# ============================================
# 测试代码
# ============================================

if __name__ == "__main__":
    from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
    from solver.mip_scheduler import create_scheduler

    # 使用真实数据
    use_real_data(True)
    trains = get_trains_pydantic()[:10]
    stations = get_stations_pydantic()
    scheduler = create_scheduler(trains, stations)
    
    # 创建Tool注册表
    registry = ToolRegistry(scheduler)
    
    print("=" * 60)
    print("可用工具:")
    print("=" * 60)
    for name in registry.get_tool_names():
        print(f"  - {name}")
    
    print("\n" + "=" * 60)
    print("Tools Schema:")
    print("=" * 60)
    for tool in registry.get_tools_schema():
        print(f"\n{tool['function']['name']}:")
        print(f"  描述: {tool['function']['description'][:50]}...")
    
    # 测试工具调用解析
    print("\n" + "=" * 60)
    print("测试工具调用解析:")
    print("=" * 60)
    
    test_response = '''
    ```json
    {
        "tool_name": "temporary_speed_limit_skill",
        "arguments": {
            "train_ids": ["G1001", "G1003"],
            "station_codes": ["BJP", "TJG", "JNZ", "NJH", "SHH"],
            "delay_injection": {
                "scenario_type": "temporary_speed_limit",
                "scenario_id": "TEST_001"
            },
            "optimization_objective": "min_max_delay"
        },
        "reasoning": "场景为临时限速，选择对应skill"
    }
    ```
    '''
    
    tool_call = parse_tool_call(test_response)
    if tool_call:
        print(f"解析成功:")
        print(f"  工具名称: {tool_call.tool_name}")
        print(f"  参数: {tool_call.arguments}")
        print(f"  理由: {tool_call.reasoning}")
        
        # 验证
        is_valid, error = validate_tool_call(tool_call, registry)
        print(f"\n验证结果: {'有效' if is_valid else '无效'}")
        if error:
            print(f"错误: {error}")
