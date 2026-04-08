# -*- coding: utf-8 -*-
"""
新架构 Agent 模块（兼容旧架构接口）
提供与 RuleAgent、QwenAgent 相同的接口，但内部使用新架构
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import json
import time
import logging

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .adapters.skill_registry import SkillRegistry, get_skill_registry
from .adapters.skills import DispatchSkillOutput

logger = logging.getLogger(__name__)


# ============================================
# Agent结果数据类（与旧架构一致）
# ============================================

@dataclass
class AgentResult:
    """Agent执行结果（与旧架构兼容）"""
    success: bool
    recognized_scenario: str
    selected_skill: str
    reasoning: str
    dispatch_result: Optional[DispatchSkillOutput]
    model_response: str
    computation_time: float
    error_message: str = ""


# ============================================
# 场景关键词配置
# ============================================

SCENARIO_KEYWORDS = {
    "temporary_speed_limit": {
        "keywords": ["限速", "大风", "暴雨", "降雪", "冰雪", "雨量", "风速",
                     "天气", "自然灾害", "泥石流", "塌方", "水害", "台风"],
        "description": "临时限速场景 - 因天气或自然灾害导致的线路限速",
        "skill_name": "temporary_speed_limit_skill"
    },
    "sudden_failure": {
        "keywords": ["故障", "中断", "封锁", "设备故障", "降弓", "线路故障",
                     "设备", "停电", "信号故障", "道岔故障", "车辆故障"],
        "description": "突发故障场景 - 列车设备故障或线路异常",
        "skill_name": "sudden_failure_skill"
    },
    "section_interrupt": {
        "keywords": ["区间中断", "线路中断", "完全中断", "无法通行"],
        "description": "区间中断场景 - 线路中断导致无法通行（预留）",
        "skill_name": "section_interrupt_skill"
    }
}


# ============================================
# 新架构 Agent 类
# ============================================

class NewArchAgent:
    """
    新架构 Agent（兼容旧架构接口）

    特点：
    - 使用新架构的技能注册表
    - 提供与 RuleAgent 相同的接口
    - 支持基于规则的场景识别
    - 内部使用新架构的技能实现
    """

    def __init__(self, trains=None, stations=None):
        """
        初始化新架构 Agent

        Args:
            trains: 列车列表
            stations: 车站列表
        """
        self.trains = trains
        self.stations = stations
        self.skill_registry: SkillRegistry = get_skill_registry(trains, stations)
        logger.info("新架构 Agent 初始化完成")

    def _detect_scenario(self, prompt: str) -> str:
        """
        基于关键词检测场景类型

        Args:
            prompt: 用户输入的调度需求

        Returns:
            str: 场景类型标识
        """
        prompt_lower = prompt.lower()

        # 按优先级检测场景
        for scenario_type, config in SCENARIO_KEYWORDS.items():
            for keyword in config["keywords"]:
                if keyword in prompt_lower:
                    return scenario_type

        # 默认返回临时限速
        return "temporary_speed_limit"

    def _extract_entities(self, prompt: str) -> Dict[str, Any]:
        """
        从输入中提取实体信息

        Args:
            prompt: 用户输入

        Returns:
            Dict: 提取的实体信息
        """
        import re

        entities = {
            "train_ids": [],
            "delay_minutes": [],
            "station_name": None,
            "station_code": None,
            "reason": None
        }

        # 提取列车号
        train_pattern = r'([GDCTKZ]\d+)'
        entities["train_ids"] = re.findall(train_pattern, prompt)

        # 提取延误时间
        delay_pattern = r'(\d+)\s*分钟'
        delays = re.findall(delay_pattern, prompt)
        entities["delay_minutes"] = [int(d) for d in delays]

        # 车站名称映射
        station_name_to_code = {
            "北京西": "BJX", "杜家坎": "DJK", "涿州东": "ZBD",
            "高碑店东": "GBD", "徐水东": "XSD", "保定东": "BDD",
            "定州东": "DZD", "正定机场": "ZDJ", "石家庄": "SJP",
            "高邑西": "GYX", "邢台东": "XTD", "邯郸东": "HDD",
            "安阳东": "AYD"
        }

        for name, code in station_name_to_code.items():
            if name in prompt:
                entities["station_name"] = name
                entities["station_code"] = code
                break

        # 提取原因
        reason_keywords = ["大风", "暴雨", "降雪", "故障", "限速", "天气"]
        for kw in reason_keywords:
            if kw in prompt:
                entities["reason"] = kw
                break

        return entities

    def _build_reasoning(
        self,
        scenario_type: str,
        entities: Dict[str, Any],
        delay_injection: Dict[str, Any]
    ) -> str:
        """
        构建推理过程文本

        Args:
            scenario_type: 场景类型
            entities: 提取的实体
            delay_injection: 延误注入数据

        Returns:
            str: 推理过程文本
        """
        scenario_config = SCENARIO_KEYWORDS.get(scenario_type, {})
        selected_skill = scenario_config.get("skill_name", "temporary_speed_limit_skill")

        reasoning_parts = [
            "【场景分析】",
            f"- 检测到场景类型：{scenario_type}",
            f"- 场景描述：{scenario_config.get('description', '未知场景')}",
            "",
            "【实体识别】",
            f"- 受影响列车：{', '.join(entities.get('train_ids', ['未识别']))}",
            f"- 延误时间：{entities.get('delay_minutes', ['未识别'])}",
            f"- 涉及车站：{entities.get('station_name', '未识别')}",
            f"- 原因：{entities.get('reason', '未识别')}",
            "",
            "【调度决策】",
        ]

        reasoning_parts.append(f"- 选择技能：{selected_skill}")
        reasoning_parts.append(f"- 选择依据：场景类型为'{scenario_type}'，匹配对应调度技能")
        reasoning_parts.append(f"- 优化目标：最小化最大延误（min_max_delay）")

        return "\n".join(reasoning_parts)

    def analyze(self, delay_injection: Dict[str, Any], user_prompt: str = "") -> AgentResult:
        """
        分析场景并执行调度（与旧架构 RuleAgent 接口一致）

        Args:
            delay_injection: 延误注入数据
            user_prompt: 用户输入的原始文本（可选）

        Returns:
            AgentResult: 执行结果
        """
        start_time = time.time()

        try:
            # Step 1: 场景识别
            scenario_type = delay_injection.get("scenario_type", "")

            # 如果delay_injection中没有场景类型，从原始输入推断
            if not scenario_type or scenario_type == "unknown":
                scenario_type = self._detect_scenario(user_prompt)

            # Step 2: 提取实体（用于生成推理过程）
            entities = self._extract_entities(user_prompt)

            # 如果实体中没有列车，从delay_injection获取
            if not entities["train_ids"]:
                entities["train_ids"] = delay_injection.get("affected_trains", [])

            # Step 3: 构建推理过程
            reasoning = self._build_reasoning(scenario_type, entities, delay_injection)

            # Step 4: 选择技能
            scenario_config = SCENARIO_KEYWORDS.get(scenario_type, {})
            selected_skill = scenario_config.get("skill_name", "temporary_speed_limit_skill")

            # Step 5: 准备车站编码列表
            if self.trains:
                station_codes = []
                for train in self.trains:
                    if hasattr(train, 'schedule') and hasattr(train.schedule, 'stops'):
                        for stop in train.schedule.stops:
                            if hasattr(stop, 'station_code'):
                                station_codes.append(stop.station_code)
                station_codes = list(dict.fromkeys(station_codes))  # 去重保序
            else:
                station_codes = ["XSD", "BDD", "DZD", "ZDJ", "SJP", "GYX", "XTD", "HDD"]

            # Step 6: 执行技能
            dispatch_result = self.skill_registry.execute(
                selected_skill,
                {
                    "train_ids": delay_injection.get("affected_trains", entities.get("train_ids", [])),
                    "station_codes": station_codes,
                    "delay_injection": delay_injection,
                    "optimization_objective": "min_max_delay"
                }
            )

            computation_time = time.time() - start_time

            return AgentResult(
                success=True,
                recognized_scenario=scenario_type,
                selected_skill=selected_skill,
                reasoning=reasoning,
                dispatch_result=dispatch_result,
                model_response=reasoning,  # 新架构返回 reasoning
                computation_time=computation_time
            )

        except Exception as e:
            logger.exception(f"新架构 Agent 执行错误: {str(e)}")
            return AgentResult(
                success=False,
                recognized_scenario="error",
                selected_skill="",
                reasoning="",
                dispatch_result=None,
                model_response="",
                computation_time=time.time() - start_time,
                error_message=str(e)
            )

    def summarize_result(self, result: AgentResult) -> str:
        """
        生成结果总结（与旧架构 RuleAgent 接口一致）

        Args:
            result: Agent执行结果

        Returns:
            str: 总结文本
        """
        if not result.success:
            return f"调度执行失败: {result.error_message}"

        dispatch = result.dispatch_result

        summary = f"""
========================================
        铁路调度 Agent 分析报告
========================================

场景识别: {result.recognized_scenario}
选择工具: {result.selected_skill}
推理过程:
{result.reasoning}

调度结果:
  - 执行状态: {'成功' if dispatch.success else '失败'}
  - 消息: {dispatch.message}
  - 计算时间: {dispatch.computation_time:.2f}秒

延误统计:
  - 最大延误: {dispatch.delay_statistics.get('max_delay_minutes', 0)}分钟
  - 平均延误: {dispatch.delay_statistics.get('avg_delay_minutes', 0):.2f}分钟
  - 总延误: {dispatch.delay_statistics.get('total_delay_minutes', 0)}分钟

Agent总耗时: {result.computation_time:.2f}秒
========================================
"""
        return summary

    def chat_direct(self, messages: List[Dict[str, str]], max_new_tokens: int = 512) -> str:
        """
        直接对话接口（与旧架构 RuleAgent 接口一致）

        Args:
            messages: 对话消息列表
            max_new_tokens: 最大生成token数（固定模式下忽略）

        Returns:
            str: 模型响应
        """
        # 提取用户消息
        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        # 基于规则的简单回复
        scenario = self._detect_scenario(user_message)
        entities = self._extract_entities(user_message)

        response = f"""您好！我是铁路调度助手（新架构模式）。

根据您的描述，我识别到：
- 场景类型：{scenario}
- 相关列车：{', '.join(entities.get('train_ids', ['未识别']))}
- 涉及车站：{entities.get('station_name', '未识别')}

如需执行调度，请使用智能调度功能。"""

        return response


# ============================================
# 工厂函数（兼容旧架构）
# ============================================

def create_rule_agent(
    trains=None,
    stations=None,
    enable_comparison: bool = True
):
    """
    创建 Agent 实例（兼容旧架构接口）

    注意：此函数返回新架构的 Agent 实例，提供相同接口

    Args:
        trains: 列车列表（可选，默认使用真实数据）
        stations: 车站列表（可选，默认使用真实数据）
        enable_comparison: 是否启用调度比较功能（新架构暂未实现，参数保留兼容）

    Returns:
        NewArchAgent: Agent实例
    """
    if trains is None or stations is None:
        from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
        use_real_data(True)
        trains = get_trains_pydantic()
        stations = get_stations_pydantic()

    return NewArchAgent(trains, stations)


# ============================================
# 兼容性导出（供 web/app.py 使用）
# ============================================

# 导出新架构 Agent 作为 RuleAgent（兼容）
RuleAgent = NewArchAgent

# 导出技能创建函数（兼容）
from .adapters.skills import create_skills, execute_skill
from .adapters.skill_registry import SkillRegistry as ToolRegistry
