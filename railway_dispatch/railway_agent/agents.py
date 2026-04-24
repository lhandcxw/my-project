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
    evaluation_report: Optional[Dict[str, Any]] = None  # L4层评估报告（含高铁专用指标）
    natural_language_plan: str = ""  # 自然语言调度方案
    operations_guide: Optional[Dict[str, Any]] = None  # 调度员操作指南


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

        logger.debug(f"LLM驱动 Agent 初始化完成")
        logger.debug(f"  - 提供商: {LLMConfig.get_provider_name()}")
        logger.debug(f"  - 模型: {self.model_name}")

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

            logger.debug("[Agent] 使用 WorkflowEngine 执行完整 L1-L4 工作流")

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

            # 处理 skill_dispatch 可能是字符串的情况
            if isinstance(skill_dispatch, str):
                selected_solver = skill_dispatch
            elif isinstance(skill_dispatch, dict):
                selected_solver = skill_dispatch.get("主技能", skill_dispatch.get("solver", "unknown"))
            else:
                selected_solver = "unknown"

            # 映射场景类型
            scene_mapping = {
                "临时限速": "temporary_speed_limit",
                "突发故障": "sudden_failure",
                "区间封锁": "section_interrupt"
            }
            recognized_scenario = scene_mapping.get(accident_card_data.get("scene_category", ""), "unknown")

            # 所有场景统一使用通用求解技能
            selected_skill = "dispatch_solve_skill"

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
                error_msg = getattr(solver_result, 'error_message', None) if solver_result else None
                dispatch_result = DispatchSkillOutput(
                    optimized_schedule=[],
                    delay_statistics={},
                    computation_time=0,
                    success=False,
                    message=error_msg if error_msg else "工作流执行失败",
                    skill_name=selected_skill
                )

            computation_time = time.time() - start_time

            # 提取L4层评估报告（包含高铁专用指标）
            evaluation_report = workflow_result.debug_trace.get("evaluation_report", {})

            # 提取自然语言调度方案
            natural_language_plan = workflow_result.debug_trace.get("natural_language_plan", "")

            # 提取调度员操作指南
            operations_guide = workflow_result.debug_trace.get("dispatcher_operations", {})

            # 构建AgentResult
            agent_result = AgentResult(
                success=workflow_result.success,
                recognized_scenario=recognized_scenario,
                selected_skill=selected_skill,
                selected_solver=selected_solver,
                reasoning=reasoning,
                llm_summary=llm_summary,
                dispatch_result=dispatch_result,
                model_response=reasoning,
                computation_time=computation_time,
                model_used=self.model_name,
                evaluation_report=evaluation_report,
                natural_language_plan=natural_language_plan,  # 添加自然语言调度方案
                operations_guide=operations_guide  # 添加调度员操作指南
            )

            # 保存工作流结果供后续使用（如调度器比较）
            agent_result._workflow_result = workflow_result
            agent_result._accident_card = accident_card_data
            
            return agent_result

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
            delay_injection: 延误注入数据（或仅包含user_prompt的字典）
            user_prompt: 用户输入的原始文本
            comparison_criteria: 比较准则

        Returns:
            AgentResult: 执行结果
        """
        start_time = time.time()

        try:
            from scheduler_comparison.comparator import SchedulerComparator, ComparisonCriteria
            from models.data_models import DelayInjection, InjectedDelay, DelayLocation

            logger.debug("[Agent] 开始带比较的调度分析")

            # 使用analyze方法获取基础结果（LLM驱动，内部完成实体提取）
            logger.debug("[Agent] 调用analyze方法获取基础结果...")
            result = self.analyze(delay_injection, user_prompt)
            logger.info(f"[Agent] analyze完成，success={result.success}, 计算时间={result.computation_time:.2f}秒")

            if not result.success:
                logger.warning("[Agent] analyze方法失败，直接返回")
                return result

            # 从工作流结果中提取实体信息（由LLM在L1层提取）
            # 注意：这里不再依赖delay_injection中的规则提取信息
            workflow_result = getattr(result, '_workflow_result', None)
            accident_card_data = getattr(result, '_accident_card', {}) if not workflow_result else None

            # 从accident_card_data中提取信息
            if not accident_card_data and workflow_result:
                accident_card_data = workflow_result.debug_trace.get("accident_card", {}) if hasattr(workflow_result, 'debug_trace') else {}

            # 打印事故卡片信息
            if accident_card_data:
                logger.info("=" * 50)
                logger.info("事故卡片（Accident Card）:")
                logger.info(f"  - 场景类型: {accident_card_data.get('scene_category', '未知')}")
                logger.info(f"  - 故障类型: {accident_card_data.get('fault_type', '未知')}")
                logger.info(f"  - 位置: {accident_card_data.get('location_name', '未知')} ({accident_card_data.get('location_code', '')})")
                logger.info(f"  - 预计延误: {accident_card_data.get('expected_duration', 0)}分钟")
                logger.info(f"  - 受影响列车: {accident_card_data.get('affected_train_ids', [])}")
                logger.info("=" * 50)
            
            # 从调度结果中提取受影响列车
            affected_trains = []
            if accident_card_data and accident_card_data.get('affected_train_ids'):
                affected_trains = accident_card_data['affected_train_ids']
            
            # 如果没有提取到列车，尝试从调度结果获取
            if not affected_trains and result.dispatch_result and result.dispatch_result.optimized_schedule:
                schedule = result.dispatch_result.optimized_schedule
                if isinstance(schedule, dict):
                    affected_trains = list(schedule.keys())
                elif isinstance(schedule, list) and len(schedule) > 0:
                    affected_trains = [s.get("train_id") for s in schedule if s.get("train_id")]
            
            # 如果没有提取到列车，尝试从delay_injection获取（兜底）
            if not affected_trains and delay_injection.get("affected_trains"):
                affected_trains = delay_injection["affected_trains"]
            
            if not affected_trains:
                logger.warning("[Agent] 无法识别受影响列车，跳过调度器比较")
                return result

            first_train_id = affected_trains[0] if isinstance(affected_trains, list) else affected_trains
            
            # 从accident_card_data中提取位置信息
            location_info = {}
            location_code = accident_card_data.get('location_code', '') if accident_card_data else ''
            if location_code:
                if '-' in location_code:
                    location_info = {"section_id": location_code}
                else:
                    location_info = {"station_code": location_code}
            
            # 如果LLM没有提取到，尝试从delay_injection获取（兜底）
            if not location_info:
                injected_delays = delay_injection.get("injected_delays", [])
                if injected_delays and len(injected_delays) > 0:
                    location_info = injected_delays[0].get("location", {})

            # 检查位置信息
            if not location_info.get("station_code") and not location_info.get("section_id"):
                logger.warning("[Agent] 缺少位置信息，无法执行调度器比较")
                # 返回基础结果，不进行调度器比较
                return result

            # 从工作流结果中提取延误时间
            delay_seconds = None
            if accident_card_data and accident_card_data.get('expected_duration'):
                delay_seconds = accident_card_data['expected_duration'] * 60  # 分钟转秒
            
            # 如果LLM没有提取到，尝试从delay_injection获取（兜底）
            if delay_seconds is None and delay_injection.get("injected_delays"):
                delay_seconds = delay_injection["injected_delays"][0].get("initial_delay_seconds")

            if delay_seconds is None:
                logger.warning("[Agent] 缺少延误时间信息，无法执行调度器比较")
                # 返回基础结果，不进行调度器比较
                return result

            station_code = location_info.get("station_code")
            section_id = location_info.get("section_id")

            # 根据位置类型设置location
            if section_id:
                location_type = "section"
                # 对于区间，提取第一个车站代码
                temp_station_code = section_id.split("-")[0] if "-" in section_id else station_code

                # 验证提取的车站代码是否在时刻表数据中，且受影响列车经过该站
                actual_station_code = temp_station_code
                station_found = False

                # 检查该车站是否在车站列表中
                if hasattr(self, 'stations') and self.stations:
                    station_codes = [s.station_code for s in self.stations if hasattr(s, 'station_code')]
                    if temp_station_code not in station_codes:
                        logger.warning(f"[Agent] 提取的车站 {temp_station_code} 不在时刻表数据中，尝试查找匹配")
                        # 尝试模糊匹配（处理编码格式问题）
                        for sc in station_codes:
                            if temp_station_code in sc or sc in temp_station_code:
                                actual_station_code = sc
                                logger.debug(f"[Agent] 找到匹配车站: {sc}")
                                break

                # 检查受影响列车是否经过该车站
                if affected_trains and hasattr(self, 'trains') and self.trains:
                    train_stations_found = []
                    for train in self.trains:
                        if train.train_id in affected_trains and hasattr(train, 'schedule') and train.schedule:
                            if hasattr(train.schedule, 'stops') and train.schedule.stops:
                                train_stations = [stop.station_code for stop in train.schedule.stops if hasattr(stop, 'station_code')]
                                if actual_station_code in train_stations:
                                    station_found = True
                                    train_stations_found.append(train.train_id)
                                    logger.debug(f"[Agent] 列车 {train.train_id} 经过车站 {actual_station_code}")
                                else:
                                    logger.warning(f"[Agent] 列车 {train.train_id} 不经过车站 {actual_station_code}，经过的车站: {train_stations}")

                    if not station_found:
                        logger.error(f"[Agent] 严重错误：所有受影响列车都不经过车站 {actual_station_code}")
                        logger.error(f"[Agent] 这将导致MIP调度器返回Infeasible")
                        # 如果没有找到任何列车经过该车站，返回错误结果
                        return result

                if station_found:
                    logger.debug(f"[Agent] 区间调度比较，使用车站: {actual_station_code}，受影响列车: {train_stations_found}")
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

            # 使用完整的列车和车站数据（不删减）
            # 网络快照将用于后续规模优化
            logger.info(f"[Agent] 使用完整数据：列车数={len(self.trains)}，车站数={len(self.stations)}")

            # 创建比较器（使用完整数据）
            logger.info("[Agent] 创建调度器比较器...")
            comparator = SchedulerComparator(
                trains=self.trains,
                stations=self.stations,
                default_criteria=criteria
            )

            # 注册多个调度器进行比较
            # 【修复】移除已废弃的fsfs调度器，确保所有可用调度器都被注册
            available_schedulers = [
                "mip", "fcfs", "hierarchical", "max_delay_first", "noop"
                # 可选：添加 "eaf"（最早到站优先）进行对比
            ]
            logger.debug(f"[Agent] 开始注册调度器: {available_schedulers}")
            for sched_name in available_schedulers:
                try:
                    comparator.register_scheduler_by_name(sched_name)
                    logger.debug(f"[Agent] 成功注册调度器: {sched_name}")
                except Exception as e:
                    logger.warning(f"注册调度器 {sched_name} 失败: {e}")

            logger.debug(f"[Agent] 已注册的调度器: {comparator.list_schedulers()}")

            # 执行比较（使用compare_all方法）
            logger.debug("[Agent] 开始执行调度器比较...")
            result_comparison = comparator.compare_all(
                delay_injection=delay_injection_obj,
                criteria=criteria
            )
            logger.info(f"[Agent] 调度器比较完成，success={result_comparison.success}")

            logger.debug(f"比较结果: success={result_comparison.success}, 结果数={len(result_comparison.results) if result_comparison else 0}")

            # 检查是否有成功的调度器
            if result_comparison.success and result_comparison.results:
                # 统计成功的调度器
                successful_schedulers = [r.scheduler_name for r in result_comparison.results if r.result.success]
                failed_schedulers = [r.scheduler_name for r in result_comparison.results if not r.result.success]

                if successful_schedulers:
                    logger.debug(f"[Agent] 成功的调度器: {successful_schedulers}")
                if failed_schedulers:
                    logger.warning(f"[Agent] 失败的调度器: {failed_schedulers}")

                # 如果MIP失败但其他调度器成功，记录警告
                if "MIP调度器" in failed_schedulers and successful_schedulers:
                    logger.warning("[Agent] MIP调度器失败，使用其他调度器的结果")
            elif result_comparison.results:
                # 所有调度器都失败了
                logger.error("[Agent] 所有调度器都失败了！")
                logger.error(f"[Agent] 失败原因: {[r.result.message for r in result_comparison.results]}")
                # 返回失败结果，但仍尝试显示部分信息
                return result

# 构建比较结果
            if result_comparison and result_comparison.success and result_comparison.results:
                winner = result_comparison.winner
                ranking = []
                for r in result_comparison.results:
                    # 【专家修复】添加小数位格式化（统一保留2位小数）
                    max_delay_min = r.result.metrics.max_delay_seconds / 60 if r.result.metrics else 0
                    avg_delay_min = r.result.metrics.avg_delay_seconds / 60 if r.result.metrics else 0
                    total_delay_min = r.result.metrics.total_delay_seconds / 60 if r.result.metrics else 0

                    # 【关键修复】加入受影响列车数和求解时间，保证前后端展示一致
                    affected_count = r.result.metrics.affected_trains_count if r.result.metrics else 0
                    comp_time = r.result.metrics.computation_time if r.result.metrics else 0

                    ranking.append({
                        "rank": r.rank,
                        "scheduler": r.scheduler_name,
                        "max_delay_minutes": round(max_delay_min, 2),
                        "avg_delay_minutes": round(avg_delay_min, 2),
                        "total_delay_minutes": round(total_delay_min, 2),
                        "affected_trains_count": affected_count,
                        "computation_time": round(comp_time, 2),
                        "score": round(r.score, 2)
                    })

                winner_scheduler = winner.scheduler_name if winner else "unknown"
                # 转换秒为分钟（前端显示用）
                max_delay_min = (winner.result.metrics.max_delay_seconds / 60) if winner and winner.result.metrics else 0
                avg_delay_min = (winner.result.metrics.avg_delay_seconds / 60) if winner and winner.result.metrics else 0
                total_delay_min = (winner.result.metrics.total_delay_seconds / 60) if winner and winner.result.metrics else 0
                delay_statistics = {
                    "winner_scheduler": winner_scheduler,
                    "ranking": ranking,
                    "max_delay_seconds": winner.result.metrics.max_delay_seconds if winner and winner.result.metrics else 0,
                    "avg_delay_seconds": winner.result.metrics.avg_delay_seconds if winner and winner.result.metrics else 0,
                    "total_delay_seconds": winner.result.metrics.total_delay_seconds if winner and winner.result.metrics else 0,
                    "max_delay_minutes": max_delay_min,
                    "avg_delay_minutes": avg_delay_min,
                    "total_delay_minutes": total_delay_min,
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
                # 【关键修复】统一显示格式：与 comparator.get_ranking_table() 保持一致
                ranking_lines = [
                    "  排名  调度器           最大延误    晚点列车平均延误      总延误      受影响列车  计算时间",
                    "  " + "-" * 88
                ]
                for i, r in enumerate(ranking):
                    max_delay_str = f"{r['max_delay_minutes']:.2f}分"
                    avg_delay_str = f"{r['avg_delay_minutes']:.2f}分/{r['affected_trains_count']}列"
                    total_delay_str = f"{r['total_delay_minutes']:.2f}分"
                    comp_time_str = f"{r['computation_time']:.2f}秒"
                    winner_mark = " ★" if r['scheduler'] == winner_scheduler else ""

                    ranking_lines.append(
                        f"  {r['rank']:<6}{r['scheduler']:<16}"
                        f"{max_delay_str:<10}"
                        f"{avg_delay_str:<18}"
                        f"{total_delay_str:<12}"
                        f"{r['affected_trains_count']}列{' ' * 6}"
                        f"{comp_time_str}{winner_mark}"
                    )

                ranking_str = "\n".join(ranking_lines)
                result.reasoning += f"\n【调度器比较】\n{ranking_str}\n【推荐方案】{winner_scheduler}"

            computation_time = time.time() - start_time
            result.computation_time = computation_time
            result.selected_solver = result.dispatch_result.delay_statistics.get("winner_scheduler", result.selected_solver)

            # 打印调度方案摘要
            if result.dispatch_result and result.dispatch_result.delay_statistics:
                stats = result.dispatch_result.delay_statistics
                logger.info(f"[Agent] 调度完成: 总延误{stats.get('total_delay_minutes', 0):.1f}分钟, "
                           f"最大延误{stats.get('max_delay_minutes', 0):.1f}分钟, "
                           f"推荐调度器: {stats.get('winner_scheduler', '未知')}, "
                           f"耗时: {computation_time:.2f}秒")

            # 获取自然语言调度方案
            if result.natural_language_plan:
                pass  # 已有方案
            elif hasattr(result, '_workflow_result'):
                workflow_result = result._workflow_result
                if hasattr(workflow_result, 'debug_trace'):
                    result.natural_language_plan = workflow_result.debug_trace.get("natural_language_plan", "")
                    # 同样处理 operations_guide
                    if not result.operations_guide:
                        result.operations_guide = workflow_result.debug_trace.get("dispatcher_operations", {})

            # 确保 operations_guide 和 natural_language_plan 被正确传递
            # 从工作流结果中提取（如果还没有的话）
            if hasattr(result, '_workflow_result') and result._workflow_result:
                workflow_result = result._workflow_result
                if hasattr(workflow_result, 'debug_trace') and workflow_result.debug_trace:
                    if not result.natural_language_plan:
                        result.natural_language_plan = workflow_result.debug_trace.get("natural_language_plan", "")
                    if not result.operations_guide:
                        result.operations_guide = workflow_result.debug_trace.get("dispatcher_operations", {})

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

    def analyze_with_session(
        self,
        user_input: str,
        session_history: Optional[List[Dict[str, str]]] = None,
        enable_rag: bool = True
    ) -> Dict[str, Any]:
        """
        支持多轮对话的统一工作流分析
        
        与单轮对话的区别：
        - 支持会话历史，LLM可以理解上下文
        - 返回结构化结果，便于多轮对话管理
        - 信息不完整时返回缺失字段，而不是报错
        
        Args:
            user_input: 用户当前输入
            session_history: 会话历史消息列表
            enable_rag: 是否启用RAG
            
        Returns:
            Dict[str, Any]: 包含以下字段：
                - success: 是否成功
                - needs_more_info: 是否需要更多信息
                - missing_fields: 缺失字段列表
                - layer1_result: L1层结果
                - workflow_result: 完整工作流结果（如果完成）
                - message: 状态消息
        """
        try:
            from railway_agent.llm_workflow_engine_v2 import create_workflow_engine

            logger.debug(f"[Agent] 执行多轮对话工作流，输入: {user_input[:50]}...")
            
            # 构建完整输入（包含历史上下文）
            if session_history:
                # 将历史对话转换为上下文
                context_parts = []
                for msg in session_history[-4:]:  # 只取最近4轮
                    role = "用户" if msg.get("role") == "user" else "系统"
                    context_parts.append(f"{role}: {msg.get('content', '')}")
                context_parts.append(f"用户: {user_input}")
                full_input = "\n".join(context_parts)
            else:
                full_input = user_input
            
            # 执行完整工作流
            workflow_engine = create_workflow_engine()
            workflow_result = workflow_engine.execute_full_workflow(
                user_input=full_input,
                canonical_request=None,
                enable_rag=enable_rag
            )
            
            # 从工作流结果提取信息
            accident_card_data = workflow_result.debug_trace.get("accident_card", {})
            
            # 检查信息是否完整
            is_complete = accident_card_data.get("is_complete", True)
            missing_fields = accident_card_data.get("missing_fields", [])
            
            # 构建L1结果
            layer1_result = {
                "accident_card": accident_card_data,
                "can_solve": is_complete,
                "missing_info": missing_fields,
                "llm_response_type": "llm_real"
            }
            
            # 如果信息不完整，返回提示
            if not is_complete and missing_fields:
                return {
                    "success": True,
                    "needs_more_info": True,
                    "missing_fields": missing_fields,
                    "layer1_result": layer1_result,
                    "message": f"请补充以下信息：{', '.join(missing_fields)}",
                    "can_proceed": False
                }
            
            # 工作流执行成功，返回完整结果
            return {
                "success": workflow_result.success,
                "needs_more_info": False,
                "missing_fields": [],
                "layer1_result": layer1_result,
                "workflow_result": {
                    "success": workflow_result.success,
                    "message": workflow_result.message,
                    "accident_card": accident_card_data,
                    "planning_intent": workflow_result.debug_trace.get("planning_intent", ""),
                    "skill_dispatch": workflow_result.debug_trace.get("skill_dispatch", {}),
                    "solver_result": self._solver_result_to_dict(workflow_result.solver_result),
                    "policy_decision": workflow_result.debug_trace.get("policy_decision", {}),
                    "llm_summary": workflow_result.debug_trace.get("llm_summary", "")
                },
                "message": "工作流执行完成" if workflow_result.success else workflow_result.message,
                "can_proceed": True
            }
            
        except Exception as e:
            logger.exception(f"多轮对话工作流执行错误: {str(e)}")
            return {
                "success": False,
                "needs_more_info": False,
                "missing_fields": [],
                "layer1_result": {},
                "message": f"执行错误: {str(e)}",
                "can_proceed": False
            }
    
    def _solver_result_to_dict(self, solver_result) -> Optional[Dict[str, Any]]:
        """将SolverResult转换为字典"""
        if solver_result is None:
            return None
        if hasattr(solver_result, 'model_dump'):
            return solver_result.model_dump()
        elif hasattr(solver_result, '__dict__'):
            return solver_result.__dict__
        return None


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
