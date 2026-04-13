# -*- coding: utf-8 -*-
"""
统一LLM驱动架构 Agent 模块

架构说明：
- 移除RuleAgent，统一使用LLM驱动的工作流
- 支持两种LLM调用方式：
  1. API调用阿里云模型（DashScope）
  2. 调用微调后的本地模型
- 内部使用完整L1-L4工作流
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
# Agent结果数据类
# ============================================

@dataclass
class AgentResult:
    """Agent执行结果"""
    success: bool
    recognized_scenario: str
    selected_skill: str
    selected_solver: str
    reasoning: str
    llm_summary: str  # LLM 评估摘要
    dispatch_result: Optional[DispatchSkillOutput]
    model_response: str
    computation_time: float
    model_used: str = ""
    error_message: str = ""


# ============================================
# 统一LLM驱动 Agent
# ============================================

class LLMAgent:
    """
    统一LLM驱动 Agent

    特点：
    - 完全基于LLM驱动，不依赖规则
    - 使用L1-L4完整工作流
    - 支持阿里云API和本地微调模型
    - 通过PROVIDER配置切换调用方式
    """

    def __init__(self, trains=None, stations=None):
        """
        初始化LLM驱动 Agent

        Args:
            trains: 列车列表
            stations: 车站列表
        """
        from config import LLMConfig

        self.trains = trains
        self.stations = stations
        self.skill_registry: SkillRegistry = get_skill_registry(trains, stations)
        self.provider = LLMConfig.PROVIDER
        self.model_name = LLMConfig.get_model_name()

        logger.info(f"LLM驱动 Agent 初始化完成")
        logger.info(f"  - 提供商: {LLMConfig.get_provider_name()}")
        logger.info(f"  - 模型: {self.model_name}")

    def analyze(self, delay_injection: Dict[str, Any], user_prompt: str = "") -> AgentResult:
        """
        分析场景并执行调度（使用 WorkflowEngine 执行完整工作流）

        Args:
            delay_injection: 延误注入数据
            user_prompt: 用户输入的原始文本

        Returns:
            AgentResult: 执行结果
        """
        start_time = time.time()

        try:
            # 使用 WorkflowEngine 执行完整工作流（方案A推荐）
            from railway_agent.llm_workflow_engine_v2 import create_workflow_engine

            logger.info("[Agent] 使用 WorkflowEngine 执行完整 L1-L4 工作流")

            workflow_engine = create_workflow_engine()
            workflow_result = workflow_engine.execute_full_workflow(
                user_input=user_prompt or delay_injection.get("raw_input", ""),
                canonical_request=None,
                enable_rag=True
            )

            if not workflow_result.success:
                raise RuntimeError(f"工作流执行失败: {workflow_result.message}")

            # 从工作流结果提取信息
            accident_card_data = workflow_result.debug_trace.get("accident_card", {})
            planning_intent = workflow_result.debug_trace.get("planning_intent", "unknown")
            skill_dispatch = workflow_result.debug_trace.get("skill_dispatch", {})
            selected_solver = skill_dispatch.get("主技能", "unknown")

            # 映射场景类型
            scene_mapping = {
                "临时限速": "temporary_speed_limit",
                "突发故障": "sudden_failure",
                "区间封锁": "section_interrupt"
            }
            recognized_scenario = scene_mapping.get(accident_card_data.get("scene_category", ""), "unknown")

            # 映射技能
            skill_mapping = {
                "临时限速": "temporary_speed_limit_skill",
                "突发故障": "sudden_failure_skill",
                "区间封锁": "section_interrupt_skill"
            }
            selected_skill = skill_mapping.get(accident_card_data.get("scene_category", ""), "unknown")

            # 提取 policy_decision
            policy_decision = workflow_result.debug_trace.get("policy_decision", {})
            # 处理可能是对象或字典的情况
            if hasattr(policy_decision, 'decision'):
                decision_value = policy_decision.decision.value if hasattr(policy_decision.decision, 'value') else str(policy_decision.decision)
            elif isinstance(policy_decision, dict):
                decision_value = policy_decision.get('decision', 'unknown')
                if hasattr(decision_value, 'value'):
                    decision_value = decision_value.value
            else:
                decision_value = 'unknown'

            # 提取 LLM 评估摘要
            llm_summary = workflow_result.debug_trace.get("llm_summary", "")

            # 构建推理过程
            reasoning_parts = [
                f"【场景识别】{accident_card_data.get('scene_category', '未知')} - {accident_card_data.get('fault_type', '未知')}",
                f"【位置信息】{accident_card_data.get('location_name', '未知')} ({accident_card_data.get('location_code', '未知')})",
                f"【影响列车】{', '.join(accident_card_data.get('affected_train_ids', []))}",
                f"【规划意图】{planning_intent}",
                f"【求解器】{selected_solver}",
                f"【评估结果】{decision_value}"
            ]
            reasoning = "\n".join(reasoning_parts)

            # 提取调度结果
            solver_result = workflow_result.solver_result
            if solver_result and solver_result.success:
                dispatch_result = DispatchSkillOutput(
                    optimized_schedule=solver_result.schedule,
                    delay_statistics=solver_result.metrics,
                    computation_time=solver_result.solving_time_seconds,
                    success=True,
                    message="调度完成",
                    skill_name=selected_skill
                )
            else:
                dispatch_result = DispatchSkillOutput(
                    optimized_schedule=[],
                    delay_statistics={},
                    computation_time=0,
                    success=False,
                    message=solver_result.message if solver_result else "工作流执行失败",
                    skill_name=selected_skill
                )

            computation_time = time.time() - start_time

            return AgentResult(
                success=workflow_result.success,
                recognized_scenario=recognized_scenario,
                selected_skill=selected_skill,
                selected_solver=selected_solver,
                reasoning=reasoning,
                llm_summary=llm_summary,
                dispatch_result=dispatch_result,
                model_response=reasoning,
                computation_time=computation_time,
                model_used=self.model_name
            )

        except Exception as e:
            logger.exception(f"LLM Agent 执行错误: {str(e)}")
            return AgentResult(
                success=False,
                recognized_scenario="error",
                selected_skill="",
                selected_solver="",
                reasoning="",
                llm_summary="",
                dispatch_result=None,
                model_response="",
                computation_time=time.time() - start_time,
                model_used=self.model_name,
                error_message=str(e)
            )

    def analyze_with_comparison(
        self,
        delay_injection: Dict[str, Any],
        user_prompt: str = "",
        comparison_criteria: str = "balanced"
    ) -> AgentResult:
        """
        分析场景并执行调度（带调度器比较）

        Args:
            delay_injection: 延误注入数据
            user_prompt: 用户输入的原始文本
            comparison_criteria: 比较准则

        Returns:
            AgentResult: 执行结果
        """
        start_time = time.time()

        try:
            from scheduler_comparison.comparator import SchedulerComparator, ComparisonCriteria
            from models.data_models import DelayInjection, InjectedDelay, DelayLocation

            logger.info("[Agent] 开始带比较的调度分析")

            # 使用analyze方法获取基础结果
            result = self.analyze(delay_injection, user_prompt)

            if not result.success:
                return result

            # 执行调度器比较（对所有场景一致）
            affected_trains = delay_injection.get("affected_trains", result.dispatch_result.optimized_schedule.get("trains", []))
            if not affected_trains:
                affected_trains = delay_injection.get("injected_delays", [{}])[0].get("train_id", "")

            # 验证关键信息是否完整
            if not affected_trains:
                logger.warning("[Agent] 缺少列车号信息，无法执行调度器比较")
                return AgentResult(
                    success=False,
                    recognized_scenario="error",
                    selected_skill="",
                    selected_solver="",
                    reasoning="",
                    llm_summary="",
                    dispatch_result=None,
                    model_response="",
                    computation_time=time.time() - start_time,
                    model_used=self.model_name,
                    error_message="请提供受影响的列车号（如：G1563）"
                )

            first_train_id = affected_trains[0] if isinstance(affected_trains, list) else affected_trains
            
            # 从injected_delays中提取位置信息（与parse_user_prompt返回的结构一致）
            location_info = {}
            injected_delays = delay_injection.get("injected_delays", [])
            if injected_delays and len(injected_delays) > 0:
                location_info = injected_delays[0].get("location", {})

            # 检查位置信息
            if not location_info.get("station_code") and not location_info.get("section_id"):
                logger.warning("[Agent] 缺少位置信息，无法执行调度器比较")
                return AgentResult(
                    success=False,
                    recognized_scenario="error",
                    selected_skill="",
                    selected_solver="",
                    reasoning="",
                    llm_summary="",
                    dispatch_result=None,
                    model_response="",
                    computation_time=time.time() - start_time,
                    model_used=self.model_name,
                    error_message="请提供事故发生位置（如：SJP站或XSD-BDD区间）"
                )

            # 检查延误时间信息
            delay_seconds = None
            # 从injected_delays中获取delay_seconds
            if delay_injection.get("injected_delays"):
                delay_seconds = delay_injection["injected_delays"][0].get("initial_delay_seconds")

            if delay_seconds is None:
                logger.warning("[Agent] 缺少延误时间信息，无法执行调度器比较")
                return AgentResult(
                    success=False,
                    recognized_scenario="error",
                    selected_skill="",
                    selected_solver="",
                    reasoning="",
                    llm_summary="",
                    dispatch_result=None,
                    model_response="",
                    computation_time=time.time() - start_time,
                    model_used=self.model_name,
                    error_message="请提供延误时间（如：延误10分钟）"
                )

            station_code = location_info.get("station_code")
            section_id = location_info.get("section_id")

            # 根据位置类型设置location
            if section_id:
                location_type = "section"
                # 对于区间，提取第一个车站代码作为比较用的车站（临时方案）
                # 实际应该支持区间比较，但目前scheduler只支持车站
                temp_station_code = section_id.split("-")[0] if "-" in section_id else station_code
                actual_station_code = temp_station_code
                logger.info(f"[Agent] 区间调度比较，使用第一个车站: {actual_station_code}（临时方案，后续应支持区间比较）")
            else:
                location_type = "station"
                actual_station_code = station_code

            # 映射比较准则
            criteria_map = {
                "min_max_delay": ComparisonCriteria.MIN_MAX_DELAY,
                "min_avg_delay": ComparisonCriteria.MIN_AVG_DELAY,
                "balanced": ComparisonCriteria.BALANCED
            }
            criteria = criteria_map.get(comparison_criteria, ComparisonCriteria.BALANCED)

            # 构建 DelayInjection 对象
            injected_delays = []
            for train_id in affected_trains:
                injected_delays.append(InjectedDelay(
                    train_id=train_id,
                    location=DelayLocation(
                        location_type=location_type,
                        station_code=actual_station_code
                    ),
                    initial_delay_seconds=delay_seconds,
                    timestamp="2024-01-15T10:00:00"
                ))
            
            delay_injection_obj = DelayInjection(
                scenario_type=delay_injection.get("scenario_type", "temporary_speed_limit"),
                scenario_id="llm_comparison_001",
                injected_delays=injected_delays,
                affected_trains=affected_trains,
                scenario_params={}
            )

            # 创建比较器
            comparator = SchedulerComparator(
                trains=self.trains,
                stations=self.stations,
                default_criteria=criteria
            )

            # 注册多个调度器进行比较
            available_schedulers = ["mip", "fcfs", "max_delay_first", "noop"]
            for sched_name in available_schedulers:
                try:
                    comparator.register_scheduler_by_name(sched_name)
                except Exception as e:
                    logger.warning(f"注册调度器 {sched_name} 失败: {e}")

            logger.info(f"已注册的调度器: {comparator.list_schedulers()}")

            # 执行比较（使用compare_all方法）
            result_comparison = comparator.compare_all(
                delay_injection=delay_injection_obj,
                criteria=criteria
            )
            
            logger.info(f"比较结果: success={result_comparison.success}, 结果数={len(result_comparison.results) if result_comparison else 0}")

            # 构建比较结果
            if result_comparison and result_comparison.success and result_comparison.results:
                winner = result_comparison.winner
                ranking = []
                for r in result_comparison.results:
                    ranking.append({
                        "rank": r.rank,
                        "scheduler": r.scheduler_name,
                        "max_delay_minutes": r.result.metrics.max_delay_seconds / 60 if r.result.metrics else 0,
                        "avg_delay_minutes": r.result.metrics.avg_delay_seconds / 60 if r.result.metrics else 0,
                        "score": r.score
                    })

                winner_scheduler = winner.scheduler_name if winner else "unknown"
                delay_statistics = {
                    "winner_scheduler": winner_scheduler,
                    "ranking": ranking,
                    "max_delay_seconds": winner.result.metrics.max_delay_seconds if winner and winner.result.metrics else 0,
                    "avg_delay_seconds": winner.result.metrics.avg_delay_seconds / 60 if winner and winner.result.metrics else 0,
                    "comparison_enabled": True,
                    "schedulers_compared": len(ranking)
                }

                # 构建比较详情用于展示
                comparison_details = []
                for r in result_comparison.results:
                    comp_item = {
                        "scheduler": r.scheduler_name,
                        "rank": r.rank,
                        "score": r.score,
                        "max_delay_min": round(r.result.metrics.max_delay_seconds / 60, 1) if r.result.metrics else 0,
                        "avg_delay_min": round(r.result.metrics.avg_delay_seconds / 60, 1) if r.result.metrics else 0
                    }
                    comparison_details.append(comp_item)

                # 更新dispatch_result
                if winner and winner.result.optimized_schedule:
                    result.dispatch_result.optimized_schedule = winner.result.optimized_schedule
                result.dispatch_result.delay_statistics = delay_statistics
                
                # 增强推理过程显示比较结果
                ranking_str = "\n".join([f"  {i+1}. {r['scheduler']}: 最高延误{r['max_delay_minutes']:.1f}分钟, 平均延误{r['avg_delay_minutes']:.1f}分钟" 
                                        for i, r in enumerate(ranking)])
                result.reasoning += f"\n【调度器比较】\n{ranking_str}\n【推荐方案】{winner_scheduler}"

            computation_time = time.time() - start_time
            result.computation_time = computation_time
            result.selected_solver = result.dispatch_result.delay_statistics.get("winner_scheduler", result.selected_solver)

            return result

        except Exception as e:
            logger.exception(f"LLM Agent 比较执行错误: {str(e)}")
            return AgentResult(
                success=False,
                recognized_scenario="error",
                selected_skill="",
                selected_solver="",
                reasoning="",
                llm_summary="",
                dispatch_result=None,
                model_response="",
                computation_time=time.time() - start_time,
                model_used=self.model_name,
                error_message=str(e)
            )

    def chat_direct(self, messages: List[Dict[str, str]], max_new_tokens: int = 512) -> str:
        """
        直接对话接口

        Args:
            messages: 对话消息列表
            max_new_tokens: 最大生成token数

        Returns:
            str: 模型响应
        """
        # 提取用户消息
        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        # 执行快速分析
        delay_injection = {
            "raw_input": user_message,
            "affected_trains": []
        }

        result = self.analyze(delay_injection, user_message)

        response = f"""您好！我是铁路调度助手（LLM驱动模式）。

根据您的描述，我识别到：
- 场景类型：{result.recognized_scenario}
- 相关列车：{', '.join(result.dispatch_result.delay_statistics.get('affected_trains', ['未识别']))}
- 求解器：{result.selected_solver}

如需执行完整调度，请使用智能调度功能。"""

        return response


# ============================================
# 工厂函数
# ============================================

def create_llm_agent(
    trains=None,
    stations=None,
    enable_comparison: bool = True
):
    """
    创建LLM驱动 Agent实例

    Args:
        trains: 列车列表（可选，默认使用真实数据）
        stations: 车站列表（可选，默认使用真实数据）
        enable_comparison: 是否启用调度比较功能

    Returns:
        LLMAgent: Agent实例
    """
    if trains is None or stations is None:
        from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
        use_real_data(True)
        trains = get_trains_pydantic()
        stations = get_stations_pydantic()

    return LLMAgent(trains, stations)


# ============================================
# 向后兼容的导出
# ============================================

# 导出LLMAgent作为RuleAgent（兼容旧接口）
RuleAgent = LLMAgent

# 导出工厂函数
create_rule_agent = create_llm_agent

# 导出技能相关
from .adapters.skills import create_skills, execute_skill
from .adapters.skill_registry import SkillRegistry as ToolRegistry
