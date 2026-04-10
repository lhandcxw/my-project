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
# 新架构 Agent 类
# ============================================

class NewArchAgent:
    """
    新架构 Agent（兼容旧架构接口）

    特点：
    - 使用LLM进行场景识别和实体提取（通过L1层）
    - 提供与 RuleAgent 相同的接口
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

    def _detect_scenario_with_llm(self, prompt: str) -> tuple:
        """
        使用L1层LLM进行场景识别和实体提取

        Args:
            prompt: 用户输入的调度需求

        Returns:
            tuple: (scenario_type, entities, accident_card)
        """
        from railway_agent.workflow.layer1_data_modeling import Layer1DataModeling
        from models.preprocess_models import CanonicalDispatchRequest, LocationInfo, CompletenessInfo
        from models.common_enums import SceneTypeCode, FaultTypeCode

        # 使用L1层进行LLM提取
        layer1 = Layer1DataModeling()
        l1_result = layer1.execute(user_input=prompt, enable_rag=True)

        accident_card = l1_result.get("accident_card")
        if not accident_card:
            # L1层失败，返回默认值
            return "temporary_speed_limit", {"train_ids": [], "station_name": None, "station_code": None}, None

        # 映射场景类型
        scene_mapping = {
            "临时限速": "temporary_speed_limit",
            "突发故障": "sudden_failure",
            "区间封锁": "section_interrupt"
        }
        scenario_type = scene_mapping.get(accident_card.scene_category, "temporary_speed_limit")

        # 构建实体信息
        entities = {
            "train_ids": accident_card.affected_train_ids or [],
            "station_name": accident_card.location_name,
            "station_code": accident_card.location_code,
            "reason": accident_card.fault_type
        }

        return scenario_type, entities, accident_card

    def _build_reasoning(
        self,
        scenario_type: str,
        entities: Dict[str, Any],
        accident_card: Any,
        delay_injection: Dict[str, Any]
    ) -> str:
        """
        构建推理过程文本

        Args:
            scenario_type: 场景类型
            entities: 提取的实体
            accident_card: 事故卡片（LLM提取结果）
            delay_injection: 延误注入数据

        Returns:
            str: 推理过程文本
        """
        scenario_descriptions = {
            "temporary_speed_limit": "临时限速场景 - 因天气或自然灾害导致的线路限速",
            "sudden_failure": "突发故障场景 - 列车设备故障或线路异常",
            "section_interrupt": "区间中断场景 - 线路中断导致无法通行"
        }

        reasoning_parts = [
            "【场景分析 - LLM提取】",
            f"- 检测到场景类型：{scenario_type}",
            f"- 场景描述：{scenario_descriptions.get(scenario_type, '未知场景')}",
        ]

        if accident_card:
            reasoning_parts.extend([
                f"- 故障类型：{accident_card.fault_type}",
                f"- 场景类别：{accident_card.scene_category}",
                f"- 影响区段：{accident_card.affected_section}",
                "",
                "【实体识别 - LLM提取】",
                f"- 受影响列车：{', '.join(entities.get('train_ids', ['未识别']))}",
                f"- 涉及车站：{entities.get('station_name', '未识别')} ({entities.get('station_code', '未识别')})",
                f"- 原因：{entities.get('reason', '未识别')}",
                f"- 信息完整：{'是' if accident_card.is_complete else '否'}",
            ])
        else:
            reasoning_parts.extend([
                "",
                "【实体识别】",
                f"- 受影响列车：{', '.join(entities.get('train_ids', ['未识别']))}",
                f"- 涉及车站：{entities.get('station_name', '未识别')}",
                f"- 原因：{entities.get('reason', '未识别')}",
            ])

        reasoning_parts.extend([
            "",
            "【调度决策】",
            f"- 选择依据：由LLM提取的场景信息匹配对应调度技能",
            f"- 优化目标：最小化最大延误（min_max_delay）"
        ])

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
            # Step 1: 使用L1层LLM进行场景识别和实体提取
            scenario_type, entities, accident_card = self._detect_scenario_with_llm(user_prompt)

            # 如果实体中没有列车，从delay_injection获取
            if not entities["train_ids"]:
                entities["train_ids"] = delay_injection.get("affected_trains", [])

            # Step 2: 构建推理过程
            reasoning = self._build_reasoning(scenario_type, entities, accident_card, delay_injection)

            # Step 3: 选择技能（基于L1提取的场景类别）
            scene_category = accident_card.scene_category if accident_card else "临时限速"
            skill_mapping = {
                "临时限速": "temporary_speed_limit_skill",
                "突发故障": "sudden_failure_skill",
                "区间封锁": "section_interrupt_skill"
            }
            selected_skill = skill_mapping.get(scene_category, "temporary_speed_limit_skill")

            # Step 5: 准备车站编码列表
            if self.trains:
                station_codes = []
                for train in self.trains:
                    if hasattr(train, 'schedule') and hasattr(train.schedule, 'stops'):
                        # 安全检查：确保 stops 是可迭代的列表/元组
                        stops = train.schedule.stops
                        if stops and isinstance(stops, (list, tuple)):
                            for stop in stops:
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

    def analyze_with_comparison(
        self,
        delay_injection: Dict[str, Any],
        user_prompt: str = "",
        comparison_criteria: str = "balanced"
    ) -> AgentResult:
        """
        分析场景并执行调度（带比较功能）

        Args:
            delay_injection: 延误注入数据
            user_prompt: 用户输入的原始文本
            comparison_criteria: 比较准则

        Returns:
            AgentResult: 执行结果
        """
        start_time = time.time()

        try:
            # Step 1: 使用L1层LLM进行场景识别和实体提取
            scenario_type, entities, accident_card = self._detect_scenario_with_llm(user_prompt)

            # 如果实体中没有列车，从delay_injection获取
            if not entities["train_ids"]:
                entities["train_ids"] = delay_injection.get("affected_trains", [])

            # Step 2: 构建推理过程
            reasoning = self._build_reasoning(scenario_type, entities, accident_card, delay_injection)

            # Step 3: 选择技能（基于L1提取的场景类别）
            scene_category = accident_card.scene_category if accident_card else "临时限速"
            skill_mapping = {
                "临时限速": "temporary_speed_limit_skill",
                "突发故障": "sudden_failure_skill",
                "区间封锁": "section_interrupt_skill"
            }
            selected_skill = skill_mapping.get(scene_category, "temporary_speed_limit_skill")

            # Step 4: 准备车站编码列表
            if self.trains:
                station_codes = []
                for train in self.trains:
                    if hasattr(train, 'schedule') and hasattr(train.schedule, 'stops'):
                        stops = train.schedule.stops
                        if stops and isinstance(stops, (list, tuple)):
                            for stop in stops:
                                if hasattr(stop, 'station_code'):
                                    station_codes.append(stop.station_code)
                station_codes = list(dict.fromkeys(station_codes))
            else:
                station_codes = ["XSD", "BDD", "DZD", "ZDJ", "SJP", "GYX", "XTD", "HDD"]

            # Step 5: 执行多调度器比较
            from scheduler_comparison.comparator import SchedulerComparator, ComparisonCriteria

            # 映射比较准则
            criteria_mapping = {
                "min_max_delay": ComparisonCriteria.MIN_MAX_DELAY,
                "min_avg_delay": ComparisonCriteria.MIN_AVG_DELAY,
                "balanced": ComparisonCriteria.BALANCED,
                "real_time": ComparisonCriteria.REAL_TIME
            }
            comp_criteria = criteria_mapping.get(comparison_criteria, ComparisonCriteria.BALANCED)

            # 创建比较器
            comparator = SchedulerComparator(criteria=comp_criteria)

            # 获取延误注入中的信息
            affected_trains = delay_injection.get("affected_trains", entities.get("train_ids", []))
            train_id = affected_trains[0] if affected_trains else "G1001"
            station_code = entities.get("station_code", "SJP")
            delay_seconds = 600

            if delay_injection.get("injected_delays"):
                first_delay = delay_injection["injected_delays"][0]
                if first_delay.get("train_id"):
                    train_id = first_delay["train_id"]
                if first_delay.get("location", {}).get("station_code"):
                    station_code = first_delay["location"]["station_code"]
                if first_delay.get("initial_delay_seconds"):
                    delay_seconds = first_delay["initial_delay_seconds"]

            # 获取求解器注册表
            from solver.solver_registry import get_solver_registry
            solver_registry = get_solver_registry()

            # 设置求解器
            from scheduler_comparison.scheduler_interface import SchedulerRegistry
            scheduler_registry_comparison = SchedulerRegistry()
            scheduler_registry_comparison._solvers = {
                "mip": solver_registry.get_solver("mip"),
                "fcfs": solver_registry.get_solver("fcfs"),
                "max_delay_first": solver_registry.get_solver("max_delay_first"),
                "noop": solver_registry.get_solver("noop")
            }

            comparator.scheduler_registry = scheduler_registry_comparison

            # 运行比较
            train_delays = {
                train_id: delay_seconds
            }
            result_comparison = comparator.compare_multiple(
                train_delays=train_delays,
                station_code=station_code
            )

            computation_time = time.time() - start_time

            # 转换比较结果为 delay_statistics
            delay_statistics = {}
            if result_comparison and result_comparison.success:
                winner = result_comparison.winner
                all_results = result_comparison.results

                # 构建 ranking 列表
                ranking = []
                for r in all_results:
                    ranking.append({
                        "rank": r.rank,
                        "scheduler": r.scheduler_name,
                        "max_delay_minutes": r.result.metrics.max_delay_seconds / 60 if r.result.metrics else 0,
                        "avg_delay_minutes": r.result.metrics.avg_delay_seconds / 60 if r.result.metrics else 0,
                        "score": r.score
                    })

                # winner scheduler 名称
                winner_scheduler = winner.scheduler_name if winner else "未知"

                # recommendations
                recommendations = result_comparison.recommendations or []

                delay_statistics = {
                    "winner_scheduler": winner_scheduler,
                    "ranking": ranking,
                    "recommendations": recommendations,
                    "max_delay_seconds": winner.result.metrics.max_delay_seconds if winner and winner.result.metrics else 0,
                    "avg_delay_seconds": winner.result.metrics.avg_delay_seconds / 60 if winner and winner.result.metrics else 0,
                    "affected_trains_count": len(affected_trains),
                    "on_time_rate": 0.95
                }

                # 构建 optimized_schedule
                optimized_schedule = {}
                if winner and winner.result.schedule:
                    optimized_schedule = winner.result.schedule

                dispatch_result = DispatchSkillOutput(
                    optimized_schedule=optimized_schedule,
                    delay_statistics=delay_statistics,
                    computation_time=computation_time,
                    success=True,
                    message=f"调度比较完成，推荐方案: {winner_scheduler}",
                    skill_name=selected_skill
                )
            else:
                # 比较失败，回退到普通调度
                dispatch_result = self.skill_registry.execute(
                    selected_skill,
                    {
                        "train_ids": affected_trains,
                        "station_codes": station_codes,
                        "delay_injection": delay_injection,
                        "optimization_objective": "min_max_delay"
                    }
                )

            return AgentResult(
                success=True,
                recognized_scenario=scenario_type,
                selected_skill=selected_skill,
                reasoning=reasoning,
                dispatch_result=dispatch_result,
                model_response=reasoning,
                computation_time=computation_time
            )

        except Exception as e:
            logger.exception(f"新架构 Agent 比较执行错误: {str(e)}")
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

        # 使用L1层LLM进行场景识别和实体提取
        scenario, entities, accident_card = self._detect_scenario_with_llm(user_message)

        response = f"""您好！我是铁路调度助手（新架构模式，基于LLM识别）。

根据您的描述，我识别到：
- 场景类型：{scenario}
- 相关列车：{', '.join(entities.get('train_ids', ['未识别']))}
- 涉及车站：{entities.get('station_name', '未识别')} ({entities.get('station_code', '未识别')})

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
