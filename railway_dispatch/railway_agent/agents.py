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
from .workflow.intent_router import IntentRouter

try:
    from .session_manager import get_session_manager
    _SESSION_MANAGER_AVAILABLE = True
except ImportError:
    _SESSION_MANAGER_AVAILABLE = False

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
        from railway_agent.workflow import Layer1DataModeling

        self.trains = trains
        self.stations = stations
        self.skill_registry: SkillRegistry = get_skill_registry(trains, stations)
        self.provider = LLMConfig.PROVIDER
        self.model_name = LLMConfig.get_model_name()
        self._layer1 = Layer1DataModeling()
        self._intent_router = IntentRouter()
        self._workflow_engine = None

        # 跨模式会话状态（Fix #8）
        self.session_state: Dict[str, Any] = {
            "history": [],           # 自动累积的对话历史
            "last_accident_card": None,
            "last_dispatch_result": None,
            "last_mode": None,
            "turn_count": 0,
        }

        logger.debug(f"RailwayDispatchAgent 初始化完成")
        logger.debug(f"  - 提供商: {LLMConfig.get_provider_name()}")
        logger.debug(f"  - 模型: {self.model_name}")

    def _build_prompt_from_delay_injection(self, delay_injection: Dict[str, Any]) -> str:
        """
        将结构化 DelayInjection 转换为自然语言描述，供 handle() 统一入口使用
        【设计原则】所有入口最终都通过自然语言进入 handle()，确保 L1 提取的一致性
        """
        scenario_type = delay_injection.get("scenario_type", "")
        injected_delays = delay_injection.get("injected_delays", [])
        scenario_params = delay_injection.get("scenario_params", {})

        parts = []
        if scenario_type == "temporary_speed_limit":
            limit_speed = scenario_params.get("limit_speed_kmh", 200)
            duration = scenario_params.get("duration_minutes", 120)
            section = scenario_params.get("affected_section", "")
            parts.append(f"因天气原因导致{section}区间临时限速{limit_speed}km/h，预计持续{duration}分钟")
        elif scenario_type == "sudden_failure":
            if injected_delays:
                d = injected_delays[0]
                train_id = d.get("train_id", "")
                station = d.get("location", {}).get("station_code", "")
                delay_sec = d.get("initial_delay_seconds", 0)
                delay_min = delay_sec // 60
                parts.append(f"{train_id}在{station}站发生突发故障，预计延误{delay_min}分钟")
        elif scenario_type == "section_interrupt":
            if injected_delays:
                d = injected_delays[0]
                train_id = d.get("train_id", "")
                station = d.get("location", {}).get("station_code", "")
                delay_min = d.get("initial_delay_seconds", 0) // 60
                parts.append(f"{train_id}在{station}站附近区间因施工封锁，预计延误{delay_min}分钟")
        else:
            if injected_delays:
                d = injected_delays[0]
                train_id = d.get("train_id", "")
                station = d.get("location", {}).get("station_code", "")
                delay_min = d.get("initial_delay_seconds", 0) // 60
                parts.append(f"{train_id}在{station}站发生异常，预计延误{delay_min}分钟")

        return " ".join(parts) if parts else "请分析当前调度场景并生成调整方案"

    def _convert_handle_result_to_agent_result(self, handle_result: Dict[str, Any], model_used: str, computation_time: float) -> AgentResult:
        """
        将 handle() 返回的字典转换为 AgentResult，保持向后兼容
        """
        if not handle_result.get("success", False):
            return AgentResult(
                success=False,
                recognized_scenario=handle_result.get("recognized_scenario", "error"),
                selected_skill="",
                selected_solver="",
                reasoning="",
                llm_summary="",
                dispatch_result=None,
                model_response=handle_result.get("message", ""),
                computation_time=computation_time,
                model_used=model_used,
                error_message=handle_result.get("message", ""),
            )

        accident_card = handle_result.get("accident_card", {})
        dispatch_metrics = handle_result.get("dispatch_metrics", {})
        optimized_schedule = handle_result.get("optimized_schedule", {})
        selected_solver = handle_result.get("selected_solver", "unknown")

        dispatch_result = DispatchSkillOutput(
            optimized_schedule=optimized_schedule,
            delay_statistics=dispatch_metrics,
            computation_time=dispatch_metrics.get("computation_time", 0),
            success=True,
            message=handle_result.get("message", "调度完成"),
            skill_name="dispatch_solve_skill",
        )

        reasoning = handle_result.get("reasoning", "")
        return AgentResult(
            success=True,
            recognized_scenario=handle_result.get("recognized_scenario", "unknown"),
            selected_skill="dispatch_solve_skill",
            selected_solver=selected_solver,
            reasoning=reasoning,
            llm_summary=handle_result.get("message", ""),
            dispatch_result=dispatch_result,
            model_response=reasoning,
            computation_time=computation_time,
            model_used=model_used,
            evaluation_report=handle_result.get("evaluation_report", {}),
            natural_language_plan=handle_result.get("natural_language_plan", ""),
            operations_guide=handle_result.get("operations_guide", {}),
        )

    def analyze(self, delay_injection: Dict[str, Any], user_prompt: str = "",
                time_budget_seconds: float = 120.0) -> AgentResult:
        """
        【已弃用 / 内部委托】analyze() 现已委托给统一入口 handle()

        为保持向后兼容，本方法将结构化 delay_injection 转为自然语言 prompt，
        然后调用 handle() 执行完整 L1-L4 工作流。

        新代码请直接使用 agent.handle(user_input=...) 作为统一入口。
        """
        start_time = time.time()

        prompt = user_prompt or self._build_prompt_from_delay_injection(delay_injection)

        handle_result = self.handle(
            user_input=prompt,
            time_budget_seconds=time_budget_seconds,
        )

        computation_time = time.time() - start_time

        return self._convert_handle_result_to_agent_result(
            handle_result, self.model_name, computation_time
        )


    def analyze_with_comparison(
        self,
        delay_injection: Dict[str, Any],
        user_prompt: str = "",
        comparison_criteria: str = "balanced"
    ) -> AgentResult:
        """
        【LLM驱动 + 规则计算】分析场景并执行调度（带调度器比较）
        LLM 完成 L1-L4 工作流；SchedulerComparator 执行多求解器规则计算对比。

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
                "mip", "fcfs", "hierarchical", "max-delay-first", "noop"
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

    # ================================================================
    # UAO-RD 统一入口（新增）
    # ================================================================

    def handle(self, user_input: str, session_history: Optional[List[Dict[str, str]]] = None,
               time_budget_seconds: float = 300.0,
               session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        【统一入口 / LLM驱动】全局Agent统一入口（UAO-RD架构）

        流程：
        1. L1提取 + 意图分类
        2. 根据 intent 进入 Light Mode 或 Heavy Mode

        Args:
            time_budget_seconds: 全局时间预算（秒），默认120秒
        """
        start_time = time.time()
        deadline = start_time + time_budget_seconds

        def _check_timeout(label: str):
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(f"[{label}] 全局超时，已用尽 {time_budget_seconds} 秒时间预算")
            return remaining

        # Fix #8: 合并外部历史与内部会话状态
        merged_history = list(self.session_state.get("history", []))
        if session_history:
            for msg in session_history:
                if msg not in merged_history:
                    merged_history.append(msg)

        # 如果提供了 session_id，从 SessionManager 加载持久化历史
        if session_id and _SESSION_MANAGER_AVAILABLE:
            try:
                session_mgr = get_session_manager()
                status = session_mgr.get_session_status(session_id)
                if status:
                    persisted_msgs = status.get("messages", [])
                    for msg in persisted_msgs:
                        if msg.get("role") in ("user", "assistant") and msg not in merged_history:
                            merged_history.append(msg)
            except Exception as e:
                logger.warning(f"[Agent] 加载会话历史失败: {e}")

        # 截断到最近10轮，防止过长
        merged_history = merged_history[-20:]

        try:
            # Step 0: 意图识别前置（轻量拦截，避免非调度请求进入L1）
            _check_timeout("意图识别")
            router_result = self._intent_router.classify_with_fallback(user_input)
            intent = router_result.get("intent", "dispatch")
            logger.info(f"[Agent] 意图分类: {intent} (classifier={router_result.get('classifier', 'unknown')}, confidence={router_result.get('confidence', 0)})")

            # Step 1: 非调度意图直接短路，不调用L1数据建模
            if intent in ("query", "chat", "overview"):
                l1_result = {"intent": intent, "accident_card": None, "can_solve": False}
                result = self._light_mode(user_input, l1_result, merged_history, deadline)
                self._update_session_state(user_input, result, None, session_id)
                return result

            # Step 2: dispatch 意图进入 Heavy Mode
            # 【注意】L1 由 workflow_engine.execute_full_workflow() 内部执行，此处不预先调用，避免重复执行
            _check_timeout("调度求解")
            result = self._heavy_mode(user_input, None, merged_history, deadline)

            # 将正确意图写回返回结果中的 accident_card
            accident_card_data = result.get("accident_card")
            if isinstance(accident_card_data, dict):
                accident_card_data["intent"] = intent
            elif hasattr(accident_card_data, 'intent'):
                accident_card_data.intent = intent

            # Fix #8: 更新跨模式会话状态
            self._update_session_state(user_input, result, accident_card_data, session_id)
            return result

        except TimeoutError as e:
            logger.warning(str(e))
            return {
                "success": False,
                "mode": "timeout",
                "ui_action": "render_chat",
                "chat_message": f"请求处理超时: {str(e)}",
                "message": f"请求处理超时: {str(e)}",
                "computation_time": time.time() - start_time
            }
        except Exception as e:
            logger.exception(f"[Agent] handle 执行错误: {e}")
            return {
                "success": False,
                "mode": "error",
                "ui_action": "render_chat",
                "chat_message": f"处理失败: {str(e)}",
                "message": f"处理失败: {str(e)}",
                "computation_time": time.time() - start_time
            }

    def _update_session_state(self, user_input: str, result: Dict[str, Any], accident_card: Any,
                               session_id: Optional[str] = None):
        """更新内部会话状态（Fix #8），并同步到 SessionManager"""
        mode = result.get("mode", "unknown")
        self.session_state["turn_count"] = self.session_state.get("turn_count", 0) + 1
        self.session_state["last_mode"] = mode
        self.session_state["last_accident_card"] = accident_card

        # 累积对话历史
        history = self.session_state.get("history", [])
        history.append({"role": "user", "content": user_input})
        assistant_content = result.get("content") or result.get("message", "")
        history.append({"role": "assistant", "content": assistant_content})
        # 截断保留最近10轮（20条消息）
        self.session_state["history"] = history[-20:]

        # 如果是调度模式，保存结果快照
        if mode == "heavy" and result.get("success"):
            self.session_state["last_dispatch_result"] = {
                "dispatch_metrics": result.get("dispatch_metrics"),
                "selected_solver": result.get("selected_solver"),
                "optimized_schedule": result.get("optimized_schedule"),
                "eval_grade": result.get("eval_grade"),
                "timestamp": time.time(),
            }
        # 同步到 SessionManager（如果提供了 session_id）
        if session_id and _SESSION_MANAGER_AVAILABLE:
            try:
                session_mgr = get_session_manager()
                session_mgr.update_messages(session_id, self.session_state.get("history", []))
                if mode == "heavy" and result.get("success"):
                    session_mgr.update_layer_result(session_id, 4, {
                        "success": True,
                        "evaluation_report": result.get("evaluation_report"),
                        "accident_card": accident_card,
                        "dispatch_metrics": result.get("dispatch_metrics"),
                    })
                    session_mgr.complete_session(session_id)
            except Exception as e:
                logger.warning(f"[Agent] 同步会话状态到 SessionManager 失败: {e}")

        logger.debug(f"[Agent] 会话状态更新: turn={self.session_state['turn_count']}, mode={mode}")

    def _light_mode(self, user_input: str, l1_result: Dict[str, Any],
                    session_history: Optional[List[Dict[str, str]]] = None,
                    deadline: Optional[float] = None) -> Dict[str, Any]:
        """
        Light Mode：轻量查询/分析，单次 Function Calling
        """
        if deadline and time.time() > deadline:
            raise TimeoutError("Light Mode 全局超时")
        start_time = time.time()
        from railway_agent.adapters.llm_adapter import get_llm_caller

        # 轻量工具集（查询/分析类，不含调度求解）
        light_tool_names = {
            "get_train_status", "query_timetable", "station_load_skill",
            "delay_propagation_skill", "quick_line_overview", "check_impact_cascade"
        }
        all_tools = self.skill_registry.get_tools_schema()
        light_tools = [t for t in all_tools if t.get("function", {}).get("name", "") in light_tool_names]

        # Fix #8: 如果有上一次的调度结果，注入上下文
        context_hint = ""
        if self.session_state.get("last_dispatch_result"):
            last = self.session_state["last_dispatch_result"]
            metrics = last.get("dispatch_metrics", {})
            context_hint = (
                f"[上下文] 最近一次调度结果: 求解器={last.get('selected_solver','未知')}, "
                f"最大延误={metrics.get('max_delay_minutes',0)}分钟, "
                f"受影响列车={metrics.get('affected_trains_count',0)}列, "
                f"等级={last.get('eval_grade','N')}。"
                f"用户可能询问此前结果，可直接引用上述数据。"
            )

        system_prompt = (
            "你是京广高铁智能调度系统的专业信息查询助手，服务于京广高铁（北京西→安阳东，13站，147列列车）的调度员。\n"
            "你的职责是根据用户问题，精准调用工具获取数据，并以专业、简洁的方式回答。\n\n"
            "【工具使用规范】\n"
            "1. get_train_status：查询指定列车的运行状态、停站信息、当前位置。用户提到具体车次号时使用。\n"
            "2. query_timetable：查询指定车站的时刻表、列车密度。用户询问'某站有哪些车'、'几点到'时使用。\n"
            "3. station_load_skill：分析车站负荷、高峰时段、接发车能力。用户询问'某站忙不忙'、'负荷'时使用。\n"
            "4. delay_propagation_skill：分析延误传播影响。用户询问'某车晚点会影响哪些车'时使用。\n"
            "5. quick_line_overview：全线快速概览。用户询问'全线情况'、'整体状态'时使用。\n"
            "6. check_impact_cascade：快速检查延误连锁反应。用户询问'连锁反应'、'波及范围'时使用。\n\n"
            "【回答规范】\n"
            "- 优先使用工具获取实时数据，不要凭知识猜测\n"
            "- 回答简洁准确，突出重点指标（时间、车次、延误分钟数）\n"
            "- 如果用户询问此前调度结果，直接引用上下文数据，无需重复调用工具\n"
            "- 多列车查询可简要列表，单列车查询可详细到每个站点\n"
            "- 涉及安全数据（追踪间隔、限速值）必须精确到具体数字\n"
            + (f"\n{context_hint}" if context_hint else "")
        )

        messages = [{"role": "system", "content": system_prompt}]

        # 注入会话历史
        if session_history:
            for msg in session_history[-4:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role in ("user", "assistant"):
                    messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_input})

        try:
            llm = get_llm_caller()
            response = llm.call_with_tools(
                messages=messages,
                tools=light_tools,
                max_tokens=1024,
                temperature=0.3
            )
        except Exception as e:
            logger.error(f"[Agent Light Mode] LLM调用失败: {e}")
            # 降级：直接调用LLM回答，不用工具
            try:
                text, _ = llm.call(user_input, max_tokens=1024, temperature=0.3)
                content = text.get("content", "") if isinstance(text, dict) else str(text)
            except Exception as e2:
                content = f"查询服务暂不可用: {e2}"
            intent = l1_result.get("intent", "chat")
            return {
                "success": True,
                "mode": "light",
                "ui_action": "render_query" if intent == "query" else "render_chat",
                "chat_message": content,
                "content": content,
                "tool_calls": [],
                "computation_time": time.time() - start_time
            }

        assistant_msg = response.get("assistant_message", {})
        content = assistant_msg.get("content", "")
        tool_calls = response.get("tool_calls", [])

        # 执行工具调用
        tool_results = []
        if tool_calls:
            for tc in tool_calls:
                tool_name = tc.get("name", "")
                try:
                    args = json.loads(tc.get("arguments", "{}")) if isinstance(tc.get("arguments"), str) else tc.get("arguments", {})
                except json.JSONDecodeError:
                    args = {}

                try:
                    skill_output = self.skill_registry.execute(tool_name, args)
                    tool_result = {
                        "tool": tool_name,
                        "success": skill_output.success,
                        "data": skill_output.delay_statistics if skill_output.success else {"error": skill_output.message}
                    }
                except Exception as e:
                    tool_result = {"tool": tool_name, "success": False, "error": str(e)}

                tool_results.append(tool_result)

                # 将工具结果追加到messages，让LLM总结
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(tool_result, ensure_ascii=False, default=str)
                })

            # 再次调用LLM生成最终回答
            try:
                final_resp = llm.call_with_tools(
                    messages=messages,
                    tools=[],
                    max_tokens=1024,
                    temperature=0.3
                )
                final_msg = final_resp.get("assistant_message", {})
                content = final_msg.get("content", content)
            except Exception as e:
                logger.warning(f"[Agent Light Mode] 最终总结失败: {e}")

        intent = l1_result.get("intent", "chat")
        return {
            "success": True,
            "mode": "light",
            "ui_action": "render_query" if intent == "query" else "render_chat",
            "chat_message": content,
            "content": content,
            "tool_calls": [t["tool"] for t in tool_results],
            "tool_results": tool_results,
            "computation_time": time.time() - start_time
        }

    def _heavy_mode(self, user_input: str, l1_result: Dict[str, Any],
                    session_history: Optional[List[Dict[str, str]]] = None,
                    deadline: Optional[float] = None) -> Dict[str, Any]:
        """
        Heavy Mode：应急调度，走完整 L1-L4 工作流
        """
        if deadline and time.time() > deadline:
            raise TimeoutError("Heavy Mode 全局超时")
        start_time = time.time()

        try:
            from railway_agent.llm_workflow_engine_v2 import create_workflow_engine

            workflow_engine = create_workflow_engine()
            remaining_budget = max(0, deadline - time.time()) if deadline else None
            workflow_result = workflow_engine.execute_full_workflow(
                user_input=user_input,
                canonical_request=None,
                enable_rag=True,
                time_budget_seconds=remaining_budget
            )

            if not workflow_result.success:
                # 区分信息不完整与真正失败
                debug_trace = getattr(workflow_result, "debug_trace", {}) or {}
                missing_info = debug_trace.get("missing_info", []) if isinstance(debug_trace, dict) else []
                if missing_info:
                    return {
                        "success": True,
                        "mode": "incomplete",
                        "needs_more_info": True,
                        "missing_fields": missing_info,
                        "accident_card": debug_trace.get("accident_card", {}),
                        "message": workflow_result.message,
                        "computation_time": time.time() - start_time
                    }
                return {
                    "success": False,
                    "mode": "heavy",
                    "message": f"工作流执行失败: {workflow_result.message}",
                    "computation_time": time.time() - start_time
                }

            accident_card_data = workflow_result.debug_trace.get("accident_card", {})
            policy_decision = workflow_result.debug_trace.get("policy_decision", {})
            decision_value = "unknown"
            if isinstance(policy_decision, dict):
                decision_value = policy_decision.get("decision", "unknown")
            elif hasattr(policy_decision, "decision"):
                decision_value = str(policy_decision.decision)

            reasoning_parts = [
                f"【场景识别】{accident_card_data.get('scene_category', '未知')} - {accident_card_data.get('fault_type', '未知')}",
                f"【位置信息】{accident_card_data.get('location_name', '未知')} ({accident_card_data.get('location_code', '未知')})",
                f"【影响列车】{', '.join(accident_card_data.get('affected_train_ids', []))}",
                f"【评估结果】{decision_value}"
            ]

            solver_result = workflow_result.solver_result
            dispatch_metrics = {}
            optimized_schedule = {}
            selected_solver = "unknown"
            skill_message = workflow_result.message
            affected_trains = []
            affected_trains_count = 0

            # 【关键修复】兼容 L2 直接求解和 L3 回退两种路径
            # L2 Agent 直接求解时，指标在 debug_trace["solver_result"]（即 skill_execution_result）中
            # L3 Solver 求解时，指标在 solver_result.metrics 中（秒级，需转分钟）
            skill_exec_result = workflow_result.debug_trace.get("solver_result", {})
            if isinstance(skill_exec_result, dict) and skill_exec_result.get("success"):
                # 优先使用 skill_execution_result（分钟级，直接可用）
                dispatch_metrics = {
                    "max_delay_minutes": skill_exec_result.get("max_delay_minutes", 0),
                    "avg_delay_minutes": skill_exec_result.get("avg_delay_minutes", 0),
                    "total_delay_minutes": skill_exec_result.get("total_delay_minutes", 0),
                    "affected_trains_count": skill_exec_result.get("affected_trains_count", 0),
                    "affected_trains": skill_exec_result.get("affected_trains", []),
                    "computation_time": round(
                        solver_result.solving_time_seconds if solver_result else skill_exec_result.get("solving_time_seconds", 0), 2
                    ),
                    "on_time_rate": skill_exec_result.get("on_time_rate", 1.0),
                    "punctuality_strict": skill_exec_result.get("punctuality_strict", 1.0),
                    "delay_std_dev": skill_exec_result.get("delay_std_dev", 0.0),
                    "delay_propagation_depth": skill_exec_result.get("delay_propagation_depth", 0),
                    "delay_propagation_breadth": skill_exec_result.get("delay_propagation_breadth", 0),
                    "evaluation_grade": skill_exec_result.get("evaluation_grade", "N"),
                }
                optimized_schedule = (
                    solver_result.schedule if solver_result and isinstance(solver_result.schedule, dict)
                    else skill_exec_result.get("optimized_schedule", {})
                )
                selected_solver = (
                    solver_result.solver_type if solver_result
                    else skill_exec_result.get("solver", skill_exec_result.get("skill_name", "unknown"))
                )
                affected_trains = skill_exec_result.get("affected_trains", [])
                affected_trains_count = skill_exec_result.get("affected_trains_count", 0)
                skill_message = (
                    solver_result.error_message or workflow_result.message
                    if solver_result else workflow_result.message
                )
            elif solver_result and solver_result.success:
                # 回退：使用 solver_result.metrics（秒级，需除以60）
                m = solver_result.metrics if isinstance(solver_result.metrics, dict) else {}
                dispatch_metrics = {
                    "max_delay_minutes": round(m.get("max_delay_seconds", 0) / 60, 2),
                    "avg_delay_minutes": round(m.get("avg_delay_seconds", 0) / 60, 2),
                    "total_delay_minutes": round(m.get("total_delay_seconds", 0) / 60, 2),
                    "affected_trains_count": m.get("affected_trains_count", 0),
                    "affected_trains": m.get("affected_trains", []),
                    "computation_time": round(solver_result.solving_time_seconds, 2),
                    "on_time_rate": m.get("on_time_rate", 1.0),
                    "punctuality_strict": m.get("punctuality_strict", 1.0),
                    "delay_std_dev": m.get("delay_std_dev", 0.0),
                    "delay_propagation_depth": m.get("delay_propagation_depth", 0),
                    "delay_propagation_breadth": m.get("delay_propagation_breadth", 0),
                    "evaluation_grade": m.get("evaluation_grade", "N"),
                }
                optimized_schedule = solver_result.schedule if isinstance(solver_result.schedule, dict) else {}
                selected_solver = solver_result.solver_type
                affected_trains = m.get("affected_trains", [])
                affected_trains_count = m.get("affected_trains_count", 0)
                skill_message = solver_result.error_message or workflow_result.message

            # 提取对比结果（如果L2做了多求解器对比）
            comparison_results = None
            planner_decision = workflow_result.debug_trace.get("planner_decision", {})
            if planner_decision and planner_decision.get("solver_results"):
                for sr in planner_decision["solver_results"]:
                    if sr.get("strategies_tested"):
                        comparison_results = {
                            "strategies_tested": sr.get("strategies_tested", 0),
                            "best_solver": sr.get("best_solver", ""),
                            "comparison_summary": sr.get("comparison_summary", ""),
                            "results": sr.get("results", [])
                        }
                        break

            # 提取评估报告中的指标
            eval_report = workflow_result.debug_trace.get("evaluation_report", {})
            eval_grade = "N"
            if hasattr(eval_report, "evaluation_grade"):
                eval_grade = eval_report.evaluation_grade
            elif isinstance(eval_report, dict):
                eval_grade = eval_report.get("evaluation_grade", "N")

            return {
                "success": True,
                "mode": "heavy",
                "message": skill_message or workflow_result.message,
                "reasoning": "\n".join(reasoning_parts),
                "accident_card": accident_card_data,
                "evaluation_report": eval_report,
                "natural_language_plan": workflow_result.debug_trace.get("natural_language_plan", ""),
                "operations_guide": workflow_result.debug_trace.get("dispatcher_operations", {}),
                "dispatch_metrics": dispatch_metrics,
                "computation_time": time.time() - start_time,
                "recognized_scenario": accident_card_data.get("scene_category", "unknown") if isinstance(accident_card_data, dict) else getattr(accident_card_data, "scene_category", "unknown"),
                "selected_skill": "dispatch_solve_skill",
                "selected_solver": selected_solver,
                "delay_statistics": dispatch_metrics,
                "optimized_schedule": optimized_schedule,
                "comparison_results": comparison_results,
                "eval_grade": eval_grade,
            }

        except Exception as e:
            logger.exception(f"[Agent Heavy Mode] 执行错误: {e}")
            return {
                "success": False,
                "mode": "heavy",
                "message": f"调度执行失败: {str(e)}",
                "computation_time": time.time() - start_time
            }

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
# 技能导出（保持外部引用兼容性）
# ============================================
from .adapters.skills import create_skills, execute_skill
