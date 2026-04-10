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
        分析场景并执行调度（使用完整L1-L4工作流）

        Args:
            delay_injection: 延误注入数据
            user_prompt: 用户输入的原始文本

        Returns:
            AgentResult: 执行结果
        """
        start_time = time.time()

        try:
            from railway_agent.workflow.layer1_data_modeling import Layer1DataModeling
            from railway_agent.workflow.layer2_planner import Layer2Planner
            from railway_agent.workflow.layer3_solver import Layer3Solver
            from railway_agent.workflow.layer4_evaluation import Layer4Evaluation

            logger.info("[Agent] 开始执行L1-L4完整工作流")

            # ============ L1: 数据建模层 ============
            logger.info("[Agent] ========== L1: 数据建模层 ==========")
            layer1 = Layer1DataModeling()
            l1_result = layer1.execute(
                user_input=user_prompt or delay_injection.get("raw_input", ""),
                enable_rag=True
            )

            accident_card = l1_result.get("accident_card")
            if not accident_card:
                raise RuntimeError("L1层未能提取事故信息")

            logger.info(f"[Agent] L1完成: scene={accident_card.scene_category}, complete={accident_card.is_complete}")

            # ============ L2: Planner层 ============
            logger.info("[Agent] ========== L2: Planner层 ==========")
            layer2 = Layer2Planner()
            l2_result = layer2.execute(
                accident_card=accident_card,
                enable_rag=True
            )

            planning_intent = l2_result.get("planning_intent", "unknown")
            logger.info(f"[Agent] L2完成: planning_intent={planning_intent}")

            # ============ L3: Solver执行层 ============
            logger.info("[Agent] ========== L3: Solver执行层 ==========")
            layer3 = Layer3Solver()
            l3_result = layer3.execute(
                planning_intent=planning_intent,
                accident_card=accident_card,
                trains=self.trains,
                stations=self.stations
            )

            skill_execution_result = l3_result.get("skill_execution_result", {})
            selected_solver = skill_execution_result.get("skill_name", "unknown")
            logger.info(f"[Agent] L3完成: solver={selected_solver}")

            # ============ L4: 评估层 ============
            logger.info("[Agent] ========== L4: 评估层 ==========")
            layer4 = Layer4Evaluation()
            l4_result = layer4.execute(
                skill_execution_result=skill_execution_result,
                solver_response=l3_result.get("solver_response"),
                enable_rag=False
            )

            policy_decision = l4_result.get("policy_decision", {})
            logger.info(f"[Agent] L4完成: decision={policy_decision.get('decision', 'unknown')}")

            # ============ 构建结果 ============
            # 映射场景类型
            scene_mapping = {
                "临时限速": "temporary_speed_limit",
                "突发故障": "sudden_failure",
                "区间封锁": "section_interrupt"
            }
            recognized_scenario = scene_mapping.get(accident_card.scene_category, "unknown")

            # 映射技能
            skill_mapping = {
                "临时限速": "temporary_speed_limit_skill",
                "突发故障": "sudden_failure_skill",
                "区间封锁": "section_interrupt_skill"
            }
            selected_skill = skill_mapping.get(accident_card.scene_category, "unknown")

            # 构建推理过程
            reasoning_parts = [
                f"【场景识别】{accident_card.scene_category} - {accident_card.fault_type}",
                f"【位置信息】{accident_card.location_name} ({accident_card.location_code})",
                f"【影响列车】{', '.join(accident_card.affected_train_ids)}",
                f"【规划意图】{planning_intent}",
                f"【求解器】{selected_solver}",
                f"【评估结果】{policy_decision.get('decision', 'unknown')}"
            ]
            reasoning = "\n".join(reasoning_parts)

            # 提取调度结果
            solver_resp = l3_result.get("solver_response", {})
            if skill_execution_result.get("success"):
                dispatch_result = DispatchSkillOutput(
                    optimized_schedule=solver_resp.get("schedule", {}),
                    delay_statistics=solver_resp.get("metrics", {}),
                    computation_time=skill_execution_result.get("solving_time", 0),
                    success=True,
                    message="调度完成",
                    skill_name=selected_skill
                )
            else:
                dispatch_result = DispatchSkillOutput(
                    optimized_schedule={},
                    delay_statistics={},
                    computation_time=0,
                    success=False,
                    message=skill_execution_result.get("error", "调度失败"),
                    skill_name=selected_skill
                )

            computation_time = time.time() - start_time

            return AgentResult(
                success=True,
                recognized_scenario=recognized_scenario,
                selected_skill=selected_skill,
                selected_solver=selected_solver,
                reasoning=reasoning,
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

            # 执行调度器比较
            affected_trains = delay_injection.get("affected_trains", result.dispatch_result.optimized_schedule.get("trains", []))
            if not affected_trains:
                affected_trains = delay_injection.get("injected_delays", [{}])[0].get("train_id", "")

            train_id = affected_trains[0] if affected_trains else "G1001"
            station_code = delay_injection.get("location", {}).get("station_code", "SJP")
            delay_seconds = 600

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
                        location_type="station",
                        station_code=station_code
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
