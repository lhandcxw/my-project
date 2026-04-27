# -*- coding: utf-8 -*-
"""
技能实现模块（Agent 框架版）

设计理念：
  - 旧版三个场景 Skill（临时限速/突发故障/区间封锁）各自硬编码绑定求解器 → 已删除
  - 新版采用能力导向设计：每个 Skill 是一个独立的调度能力单元
  - DispatchSolveSkill：通用求解引擎，求解器由调用方指定
  - CompareStrategiesSkill：多策略对比，自动选最优
  - StationLoadSkill：车站负荷分析，辅助决策
  - DelayPropagationSkill：延误传播预测，量化影响范围
  - 查询类 Skill 保持（get_train_status, query_timetable）

向后兼容：
  - DispatchSkillOutput / DispatchSkillInput 数据类不变
  - create_skills() / execute_skill() 签名不变
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import time
import logging

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scheduler_comparison.scheduler_interface import SchedulerRegistry, DelayInjection, SchedulerResult
from railway_agent.solver_selector import SolverSelector

logger = logging.getLogger(__name__)


# ============================================
# 数据模型（外部模块依赖，保持不变）
# ============================================

@dataclass
class DispatchSkillInput:
    """调度Skill输入参数"""
    train_ids: List[str]
    station_codes: List[str]
    delay_injection: Dict[str, Any]
    optimization_objective: str = "min_total_delay"


@dataclass
class DispatchSkillOutput:
    """调度Skill输出结果"""
    optimized_schedule: Dict[str, List[Dict]]
    delay_statistics: Dict[str, Any]
    computation_time: float
    success: bool
    message: str = ""
    skill_name: str = ""


# ============================================
# 基础技能类
# ============================================

class BaseDispatchSkill:
    """铁路调度Skill基类"""

    name: str = "base_dispatch_skill"
    description: str = "基础调度Skill"

    def __init__(self, trains=None, stations=None):
        self.trains = trains
        self.stations = stations

    def _build_delay_injection(self, delay_injection: Dict[str, Any]) -> DelayInjection:
        """
        构建 DelayInjection（使用 Scheduler 系统）
        从 delay_injection 字典转换为 DelayInjection 对象
        """
        from models.data_models import InjectedDelay, DelayLocation

        injected_delays = []
        for delay in delay_injection.get("injected_delays", []):
            delay_location = DelayLocation(
                location_type=delay.get("location_type", "station"),
                station_code=delay.get("station_code"),
                section_id=delay.get("section_id"),
                position=delay.get("station_name", delay.get("station_code", ""))
            )
            injected_delay = InjectedDelay(
                train_id=delay.get("train_id"),
                location=delay_location,
                initial_delay_seconds=delay.get("initial_delay_seconds", delay.get("delay_minutes", 0) * 60),
                timestamp=delay.get("timestamp", "2024-01-01 08:00:00")
            )
            injected_delays.append(injected_delay)

        return DelayInjection(
            scenario_type=delay_injection.get("scenario_type", "sudden_failure"),
            scenario_id=delay_injection.get("scenario_id", "default"),
            injected_delays=injected_delays,
            affected_trains=[d.get("train_id") for d in delay_injection.get("injected_delays", [])]
        )

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_total_delay",
        **kwargs
    ) -> DispatchSkillOutput:
        """执行调度Skill"""
        raise NotImplementedError


# ============================================
# 求解器加载（共享方法）
# ============================================

def _get_scheduler_instance(scheduler_name: str, trains: List, stations: List):
    """
    获取调度器实例（使用 Scheduler 系统）

    【迁移说明】
    - 原逻辑：使用 SolverRegistry.get_solver() + 动态导入 solver.*_adapter
    - 新逻辑：直接使用 SchedulerRegistry.create()，无需适配器层
    """
    try:
        scheduler = SchedulerRegistry.create(scheduler_name, trains, stations)
        return scheduler
    except Exception as e:
        logger.error(f"加载调度器失败 {scheduler_name}: {e}")
        return None


# ============================================
# 核心：通用调度求解技能
# ============================================

class DispatchSolveSkill(BaseDispatchSkill):
    """
    通用调度求解技能

    支持参数化选择全部 7 个求解器和配置参数。
    求解器类型和参数由调用方（L2 Agent 或 web 接口）决定，Skill 本身不做决策。
    """

    name = "dispatch_solve_skill"
    description = "通用调度求解技能，支持参数化选择调度器（mip/fcfs/max_delay_first/noop/hierarchical）和配置参数"

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_total_delay",
        **kwargs
    ) -> DispatchSkillOutput:
        start_time = time.time()

        solver_config = delay_injection.get("solver_config", {})
        scheduler_name = solver_config.get("solver", "fcfs")  # 保持兼容性：solver 参数名
        obj = solver_config.get("optimization_objective", optimization_objective)

        # 使用 Scheduler 系统
        scheduler = _get_scheduler_instance(scheduler_name, self.trains, self.stations)
        if scheduler is None:
            return DispatchSkillOutput(
                optimized_schedule={},
                delay_statistics={},
                computation_time=time.time() - start_time,
                success=False,
                message=f"调度器 {scheduler_name} 不可用",
                skill_name=self.name
            )

        # 构建 DelayInjection
        delay_info = self._build_delay_injection(delay_injection)

        # 执行调度（使用 Scheduler 系统）
        try:
            scheduler_result = scheduler.solve(
                delay_info,
                objective=obj
            )
        except Exception as e:
            logger.error(f"[{self.name}] 调度失败: {e}")
            return DispatchSkillOutput(
                optimized_schedule={},
                delay_statistics={},
                computation_time=time.time() - start_time,
                success=False,
                message=f"调度失败: {str(e)}",
                skill_name=self.name
            )

        computation_time = time.time() - start_time

        metrics = scheduler_result.metrics or {}
        optimized_schedule = {}
        if hasattr(scheduler_result, 'schedule') and scheduler_result.schedule:
            optimized_schedule = scheduler_result.schedule
        elif hasattr(scheduler_result, 'optimized_schedule') and scheduler_result.optimized_schedule:
            optimized_schedule = scheduler_result.optimized_schedule

        # 处理 metrics 对象（可能是 dict 或 EvaluationMetrics）
        if hasattr(metrics, 'max_delay_seconds'):
            # EvaluationMetrics 对象，使用属性访问
            delay_stats = {
                "max_delay_seconds": getattr(metrics, 'max_delay_seconds', 0),
                "avg_delay_seconds": getattr(metrics, 'avg_delay_seconds', 0),
                "total_delay_seconds": getattr(metrics, 'total_delay_seconds', 0),
                "affected_trains_count": getattr(metrics, 'affected_trains_count', len(train_ids))
            }
        else:
            # dict 对象，使用 get 方法
            delay_stats = {
                "max_delay_seconds": metrics.get("max_delay_seconds", 0),
                "avg_delay_seconds": metrics.get("avg_delay_seconds", 0),
                "total_delay_seconds": metrics.get("total_delay_seconds", 0),
                "affected_trains_count": metrics.get("affected_trains_count", len(train_ids))
            }

        return DispatchSkillOutput(
            optimized_schedule=optimized_schedule,
            delay_statistics=delay_stats,
            computation_time=computation_time + (
                scheduler_result.solving_time_seconds
                if hasattr(scheduler_result, 'solving_time_seconds') else 0
            ),
            success=scheduler_result.success,
            message=f"调度求解完成。调度器: {scheduler_name}, 优化目标: {obj}",
            skill_name=self.name
        )


# ============================================
# 多策略对比技能
# ============================================

class CompareStrategiesSkill(BaseDispatchSkill):
    """
    多策略对比技能

    运行多个求解器并对比结果，自动选出最优方案。
    与 L2 Agent 的 compare_strategies 工具功能对齐，
    但作为独立 Skill 可被 web 接口或外部调用者直接使用。

    适用场景：
    - 需要在速度（FCFS）和最优性（MIP）之间权衡
    - 不确定哪个求解器更适合当前场景
    - 需要量化依据来支持调度决策
    """

    name = "compare_strategies_skill"
    description = "运行多个求解策略并对比结果，自动选出最优方案"

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_total_delay",
        **kwargs
    ) -> DispatchSkillOutput:
        start_time = time.time()

        strategies = kwargs.get("strategies") or delay_injection.get("solver_config", {}).get("strategies")
        time_budget = kwargs.get("time_budget", 300)

        # 自动选择策略（与 layer2_planner._tool_compare_strategies 对齐）
        if not strategies:
            scenario_type = delay_injection.get("scenario_type", "")
            affected_count = len(train_ids)
            delay_mins = delay_injection.get("expected_duration", 10)
            is_large = affected_count > 10 or delay_mins > 30
            is_emergency = delay_mins > 60 or scenario_type == "section_interrupt"

            if is_emergency:
                strategies = ["fcfs"]
            elif is_large:
                # 大规模：分层求解器自动选择 + FCFS 保底
                strategies = ["hierarchical", "fcfs"]
            else:
                # 小规模：MIP 全局最优 + 分层求解器 + FCFS 保底
                strategies = ["mip", "hierarchical", "fcfs"]

        delay_info = self._build_delay_injection(delay_injection)

        results = []
        for scheduler_name in strategies:
            if time.time() - start_time > time_budget:
                results.append({"scheduler": scheduler_name, "success": False, "error": "超过时间预算"})
                continue

            scheduler = _get_scheduler_instance(scheduler_name, self.trains, self.stations)
            if scheduler is None:
                results.append({"scheduler": scheduler_name, "success": False, "error": f"调度器不可用"})
                continue

            try:
                resp = scheduler.solve(delay_info, objective=optimization_objective)
                metrics = resp.metrics or {}
                results.append({
                    "scheduler": scheduler_name,
                    "success": resp.success,
                    "total_delay_minutes": metrics.get("total_delay_seconds", 0) // 60,
                    "max_delay_minutes": metrics.get("max_delay_seconds", 0) // 60,
                    "avg_delay_minutes": round(metrics.get("avg_delay_seconds", 0) / 60, 1) if metrics.get("avg_delay_seconds") else 0,
                    "solving_time_seconds": round(resp.solving_time_seconds, 2),
                    "message": resp.message
                })
            except Exception as e:
                results.append({"scheduler": scheduler_name, "success": False, "error": str(e)})

        # 多目标评分与Pareto分析
        successful = [r for r in results if r.get("success")]
        scored_successful = [
            SolverSelector.score_result(r, optimization_objective)
            for r in successful
        ]
        pareto_results = SolverSelector.find_pareto_front(scored_successful)

        # 按综合得分排序（越低越好）
        scored_successful.sort(key=lambda r: r.get("composite_score", 9999))
        best = scored_successful[0] if scored_successful else None

        computation_time = time.time() - start_time

        # 构建摘要（成功+失败）
        summary_lines = []
        for r in scored_successful:
            summary_lines.append(
                f"{r['scheduler']}: 总延误{r.get('total_delay_minutes')}分, "
                f"最大延误{r.get('max_delay_minutes')}分, 耗时{r.get('solving_time_seconds')}秒, "
                f"综合得分={r.get('composite_score', 'N/A')}"
            )
        for r in results:
            if not r.get("success"):
                summary_lines.append(f"{r['scheduler']}: 失败({r.get('error', '')})")

        pareto_names = [p["solver"] for p in pareto_results] if pareto_results else []

        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics={
                "strategies_tested": len(results),
                "results": scored_successful + [r for r in results if not r.get("success")],
                "best_scheduler": best["solver"] if best else None,
                "pareto_solvers": pareto_names,
                "comparison_summary": " | ".join(summary_lines)
            },
            computation_time=computation_time,
            success=best is not None,
            message=f"策略对比完成。最优: {best['solver'] if best else '无'}, Pareto集: {pareto_names}",
            skill_name=self.name
        )


# ============================================
# 车站负荷分析技能
# ============================================

class StationLoadSkill(BaseDispatchSkill):
    """
    车站负荷分析技能

    分析指定车站在不同时段的列车密度、通过能力和负荷状况。
    辅助调度员和 Agent 判断当前是否为高峰时段、车站是否接近满负荷。

    输出：
      - 各时段列车分布（按 2 小时粒度统计）
      - 总通过列车数
      - 是否高峰时段
      - 停靠/通过比例
    """

    name = "station_load_skill"
    description = "分析车站在不同时段的列车密度和负荷状况，判断高峰/平峰时段"

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_total_delay",
        **kwargs
    ) -> DispatchSkillOutput:
        start_time = time.time()

        station_code = kwargs.get("station_code", station_codes[0] if station_codes else None)
        if not station_code:
            return DispatchSkillOutput(
                optimized_schedule={},
                delay_statistics={},
                computation_time=0.0,
                success=False,
                message="请提供车站编码",
                skill_name=self.name
            )

        # 统计各时段列车分布
        hour_distribution = {h: 0 for h in range(24)}
        total_trains = 0
        stopped_trains = 0
        passed_trains = 0

        for train in (self.trains or []):
            if hasattr(train, 'schedule') and hasattr(train.schedule, 'stops'):
                for stop in train.schedule.stops:
                    if stop.station_code == station_code:
                        total_trains += 1
                        if stop.is_stopped:
                            stopped_trains += 1
                        else:
                            passed_trains += 1

                        # 提取到达时间的小时
                        dep_str = stop.departure_time or stop.arrival_time or ""
                        hour = self._parse_hour(dep_str)
                        if hour is not None:
                            hour_distribution[hour] += 1
                        break

        # 车站名称
        station_name = station_code
        for s in (self.stations or []):
            if hasattr(s, 'station_code') and s.station_code == station_code:
                station_name = s.station_name
                break

        # 判断高峰时段
        peak_hours = list(range(7, 10)) + list(range(17, 20))
        peak_count = sum(hour_distribution[h] for h in peak_hours)
        is_peak = peak_count > total_trains * 0.4 if total_trains > 0 else False

        # 2小时粒度聚合
        period_distribution = {}
        period_labels = {
            0: "0:00-2:00（天窗）", 2: "2:00-4:00（天窗）",
            4: "4:00-6:00（天窗）", 6: "6:00-8:00",
            8: "8:00-10:00", 10: "10:00-12:00",
            12: "12:00-14:00", 14: "14:00-16:00",
            16: "16:00-18:00", 18: "18:00-20:00",
            20: "20:00-22:00", 22: "22:00-24:00"
        }
        for p_start in range(0, 24, 2):
            p_end = p_start + 2
            count = sum(hour_distribution[h] for h in range(p_start, p_end))
            if count > 0:
                period_distribution[period_labels.get(p_start, f"{p_start}-{p_end}")] = count

        computation_time = time.time() - start_time

        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics={
                "station_code": station_code,
                "station_name": station_name,
                "total_trains": total_trains,
                "stopped_trains": stopped_trains,
                "passed_trains": passed_trains,
                "stop_ratio": round(stopped_trains / total_trains, 2) if total_trains > 0 else 0,
                "is_peak": is_peak,
                "peak_train_count": peak_count,
                "hour_distribution": {str(k): v for k, v in hour_distribution.items() if v > 0},
                "period_distribution": period_distribution
            },
            computation_time=computation_time,
            success=True,
            message=f"车站 {station_name}（{station_code}）负荷分析完成，共{total_trains}列，{'高峰' if is_peak else '平峰'}时段",
            skill_name=self.name
        )

    @staticmethod
    def _parse_hour(time_str: str) -> Optional[int]:
        """从时间字符串解析小时（支持 HH:MM:SS 和 HH:MM 格式）"""
        if not time_str:
            return None
        try:
            parts = time_str.strip().split(":")
            if len(parts) >= 2:
                return int(parts[0]) % 24
        except (ValueError, IndexError):
            pass
        return None


# ============================================
# 延误传播预测技能
# ============================================

class DelayPropagationSkill(BaseDispatchSkill):
    """
    延误传播预测技能

    分析延误沿线路的链式传播路径和影响范围。
    基于列车时刻表中的先后到达关系，模拟延误的级联传播效应。

    输出：
      - 直接影响列车列表
      - 传播路径（哪些列车可能被间接影响）
      - 传播深度和各层受影响列车数
      - 最大潜在延误估计
    """

    name = "delay_propagation_skill"
    description = "预测延误沿线路的链式传播路径和影响范围，量化间接受影响列车"

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_total_delay",
        **kwargs
    ) -> DispatchSkillOutput:
        start_time = time.time()

        location_code = kwargs.get("location_code", station_codes[0] if station_codes else None)
        delay_minutes = kwargs.get("delay_minutes", 10)

        if not location_code or not train_ids:
            return DispatchSkillOutput(
                optimized_schedule={},
                delay_statistics={},
                computation_time=0.0,
                success=False,
                message="请提供车站编码和受影响列车列表",
                skill_name=self.name
            )

        # 收集经过事故车站的所有列车及其到站时间
        trains_at_station = {}
        for train in (self.trains or []):
            if hasattr(train, 'schedule') and hasattr(train.schedule, 'stops'):
                for stop in train.schedule.stops:
                    if stop.station_code == location_code:
                        dep_str = stop.departure_time or stop.arrival_time or ""
                        trains_at_station[train.train_id] = {
                            "arrival_time": stop.arrival_time,
                            "departure_time": stop.departure_time,
                            "is_stopped": stop.is_stopped,
                            "hour": self._parse_hour(dep_str) or 0
                        }
                        break

        # 排序：按到站时间
        sorted_trains = sorted(trains_at_station.items(), key=lambda x: x[1]["hour"])

        # 传播分析：事故列车后续到达的列车可能受影响
        affected_set = set(train_ids)
        direct_set = set(train_ids)

        propagation_layers = []  # [(layer_index, [train_ids])]
        current_affected = set(train_ids)

        for _ in range(3):  # 最多传播 3 层
            next_affected = set()
            # 找到当前受影响列车之后到达的列车
            affected_hours = [trains_at_station[tid]["hour"] for tid in current_affected if tid in trains_at_station]
            if not affected_hours:
                break
            max_hour = max(affected_hours)

            for tid, info in sorted_trains:
                if tid in affected_set:
                    continue
                if info["hour"] >= max_hour:
                    # 同一站后续到达的列车
                    next_affected.add(tid)
                    affected_set.add(tid)

            if not next_affected:
                break

            propagation_layers.append({
                "layer": len(propagation_layers) + 1,
                "newly_affected_count": len(next_affected),
                "train_ids": list(next_affected)[:10]
            })
            current_affected = next_affected

        indirect_count = len(affected_set) - len(direct_set)
        total_affected = len(affected_set)
        indirect_ids = list(affected_set - direct_set)[:10]

        computation_time = time.time() - start_time

        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics={
                "location_code": location_code,
                "initial_delay_minutes": delay_minutes,
                "directly_affected": {
                    "count": len(direct_set),
                    "train_ids": list(direct_set)
                },
                "indirectly_affected": {
                    "count": indirect_count,
                    "train_ids": indirect_ids
                },
                "total_potentially_affected": total_affected,
                "propagation_layers": propagation_layers,
                "propagation_depth": len(propagation_layers),
                "estimated_max_propagation_delay": delay_minutes + len(propagation_layers) * 3
            },
            computation_time=computation_time,
            success=True,
            message=(
                f"延误传播预测完成。直接影响{len(direct_set)}列，"
                f"间接影响{indirect_count}列，传播{len(propagation_layers)}层"
            ),
            skill_name=self.name
        )

    @staticmethod
    def _parse_hour(time_str: str) -> Optional[int]:
        if not time_str:
            return None
        try:
            parts = time_str.strip().split(":")
            if len(parts) >= 2:
                return int(parts[0]) % 24
        except (ValueError, IndexError):
            pass
        return None


# ============================================
# 查询技能（保持原有实现）
# ============================================

class GetTrainStatusSkill(BaseDispatchSkill):
    """列车状态查询技能"""

    name = "get_train_status"
    description = "查询指定列车的实时运行状态"

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_total_delay",
        **kwargs
    ) -> DispatchSkillOutput:
        start_time = time.time()
        train_id = kwargs.get("train_id", train_ids[0] if train_ids else None)

        if not train_id:
            return DispatchSkillOutput(
                optimized_schedule={}, delay_statistics={},
                computation_time=0.0, success=False,
                message="请提供列车ID", skill_name=self.name
            )

        train_info = None
        if self.trains:
            for t in self.trains:
                if hasattr(t, 'train_id') and t.train_id == train_id:
                    train_info = {
                        "train_id": t.train_id,
                        "train_type": getattr(t, 'train_type', '未知'),
                        "train_id_mapped": getattr(t, 'train_id_mapped', '')
                    }
                    if hasattr(t, 'schedule') and hasattr(t.schedule, 'stops'):
                        stops = t.schedule.stops
                        if isinstance(stops, (list, tuple)):
                            train_info["total_stops"] = len(stops)
                            # 返回全部站点，同时提供前3站摘要供快速预览
                            all_stops = [
                                {
                                    "station_code": s.station_code,
                                    "station_name": s.station_name,
                                    "arrival_time": s.arrival_time,
                                    "departure_time": s.departure_time,
                                    "is_stopped": s.is_stopped
                                }
                                for s in stops
                            ]
                            train_info["stops"] = all_stops
                            train_info["stops_preview"] = all_stops[:3]
                    break

        computation_time = time.time() - start_time
        if not train_info:
            return DispatchSkillOutput(
                optimized_schedule={}, delay_statistics={},
                computation_time=computation_time, success=False,
                message=f"未找到列车 {train_id}", skill_name=self.name
            )

        return DispatchSkillOutput(
            optimized_schedule={}, delay_statistics=train_info,
            computation_time=computation_time, success=True,
            message=f"列车 {train_id} 状态查询完成", skill_name=self.name
        )


class QueryTimetableSkill(BaseDispatchSkill):
    """时刻表查询技能"""

    name = "query_timetable"
    description = "查询列车时刻表或车站时刻表"

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_total_delay",
        **kwargs
    ) -> DispatchSkillOutput:
        start_time = time.time()
        train_id = kwargs.get("train_id")
        station_code = kwargs.get("station_code")

        results = {"query_type": None, "timetable_type": kwargs.get("timetable_type", "plan"), "trains": []}

        if train_id and self.trains:
            results["query_type"] = "train"
            results["train_id"] = train_id
            for train in self.trains:
                if hasattr(train, 'train_id') and train.train_id == train_id:
                    results["train_type"] = getattr(train, 'train_type', '未知')
                    if hasattr(train, 'schedule') and hasattr(train.schedule, 'stops'):
                        stops_list = train.schedule.stops if isinstance(train.schedule.stops, (list, tuple)) else []
                        results["total_stops"] = len(stops_list)
                        results["stops"] = [
                            {
                                "station_code": s.station_code,
                                "station_name": s.station_name,
                                "arrival_time": s.arrival_time,
                                "departure_time": s.departure_time,
                                "is_stopped": s.is_stopped,
                                "stop_duration_seconds": s.stop_duration
                            }
                            for s in stops_list
                        ]
                    break

        elif station_code and self.trains:
            results["query_type"] = "station"
            results["station_code"] = station_code
            station_name = station_code
            if self.stations:
                for s in self.stations:
                    if hasattr(s, 'station_code') and s.station_code == station_code:
                        station_name = s.station_name
                        break
            results["station_name"] = station_name

            trains_at_station = []
            for train in self.trains:
                if hasattr(train, 'schedule') and hasattr(train.schedule, 'stops'):
                    stops = train.schedule.stops
                    if isinstance(stops, (list, tuple)):
                        for stop in train.schedule.stops:
                            if stop.station_code == station_code:
                                trains_at_station.append({
                                    "train_id": train.train_id,
                                    "arrival_time": stop.arrival_time,
                                    "departure_time": stop.departure_time,
                                    "is_stopped": stop.is_stopped
                                })
                                break
            results["trains"] = trains_at_station[:20]
            results["total_trains"] = len(trains_at_station)

        computation_time = time.time() - start_time

        if train_id:
            message = f"列车 {train_id} 时刻表查询完成"
        elif station_code:
            message = f"车站 {station_code} 时刻表查询完成"
        else:
            return DispatchSkillOutput(
                optimized_schedule={}, delay_statistics={},
                computation_time=computation_time, success=False,
                message="请提供列车ID或车站编码", skill_name=self.name
            )

        return DispatchSkillOutput(
            optimized_schedule={}, delay_statistics=results,
            computation_time=computation_time, success=True,
            message=message, skill_name=self.name
        )


# ============================================
# L2 Agent 工具类（迁入 SkillRegistry，统一工具层）
# ============================================

class AssessImpactSkill(BaseDispatchSkill):
    """
    事故态势感知技能
    分析直接影响列车数、延误传播风险、紧急程度
    """
    name = "assess_impact"
    description = "评估事故的全局影响。分析直接影响列车数、延误传播风险、即将到达的列车数，返回量化的紧急程度和策略建议。建议在决策前首先调用此工具获取数据支撑。"

    def execute(self, train_ids, station_codes, delay_injection, optimization_objective="min_total_delay", **kwargs):
        start_time = time.time()
        from datetime import datetime

        accident_card = kwargs.get("accident_card")
        if not accident_card:
            return DispatchSkillOutput(
                optimized_schedule={}, delay_statistics={},
                computation_time=0.0, success=False,
                message="缺少 accident_card 上下文", skill_name=self.name
            )

        user_mentioned = set(getattr(accident_card, 'affected_train_ids', None) or [])
        location = getattr(accident_card, 'location_code', None) or ""
        delay = getattr(accident_card, 'expected_duration', None) or 10
        network_snapshot = kwargs.get("network_snapshot")

        search_scope = self.trains
        if network_snapshot and hasattr(network_snapshot, 'candidate_train_ids'):
            candidate_ids = set(network_snapshot.candidate_train_ids)
            search_scope = [
                t for t in self.trains
                if (hasattr(t, 'train_id') and t.train_id in candidate_ids)
                or (isinstance(t, dict) and t.get('train_id') in candidate_ids)
            ]

        trains_at_location = []
        initially_delayed = []
        nearby_not_mentioned = []

        if location and search_scope:
            for train in search_scope:
                train_id = getattr(train, 'train_id', None) if hasattr(train, 'train_id') else train.get('train_id')
                if not train_id:
                    continue
                passes_location = False
                schedule = getattr(train, 'schedule', None) if hasattr(train, 'schedule') else train.get('schedule')
                if schedule:
                    stops = getattr(schedule, 'stops', None) if hasattr(schedule, 'stops') else schedule.get('stops')
                    if stops:
                        for stop in stops:
                            station_code_val = getattr(stop, 'station_code', None) if hasattr(stop, 'station_code') else stop.get('station_code')
                            if station_code_val == location:
                                passes_location = True
                                break
                if passes_location:
                    trains_at_location.append(train_id)
                    if train_id in user_mentioned:
                        initially_delayed.append(train_id)
                    else:
                        nearby_not_mentioned.append(train_id)

        for tid in user_mentioned:
            if tid not in trains_at_location:
                initially_delayed.append(tid)
                trains_at_location.append(tid)

        exposed_count = len(trains_at_location)
        hour = datetime.now().hour
        is_peak = 9 <= hour <= 18
        is_window = 0 <= hour < 6
        density_factor = 1.5 if is_peak else (0.5 if is_window else 1.0)
        estimated_propagation = int((delay / 10) * (exposed_count / 5) * density_factor)
        estimated_propagation = min(estimated_propagation, exposed_count * 2)
        estimated_propagation = max(0, estimated_propagation)
        total_impact = exposed_count + estimated_propagation

        if is_window:
            urgency = "low"
        elif total_impact <= 3 and delay <= 15:
            urgency = "low"
        elif total_impact <= 8 and delay <= 30:
            urgency = "medium"
        elif total_impact <= 15 and delay <= 60:
            urgency = "high"
        else:
            urgency = "critical"
        if is_peak and urgency in ("low", "medium") and total_impact > 5:
            urgency = "high"

        trains_at_location_detail = []
        for tid in trains_at_location[:15]:
            for t in (self.trains or []):
                t_id = getattr(t, 'train_id', None) if hasattr(t, 'train_id') else t.get('train_id')
                if t_id == tid:
                    train_type = getattr(t, 'train_type', '未知') if hasattr(t, 'train_type') else t.get('train_type', '未知')
                    trains_at_location_detail.append({"train_id": tid, "train_type": train_type, "initially_delayed": tid in user_mentioned})
                    break

        result = {
            "success": True,
            "initially_delayed_trains": len(initially_delayed),
            "initially_delayed_ids": initially_delayed[:10],
            "trains_at_location": exposed_count,
            "trains_at_location_ids": trains_at_location[:10],
            "nearby_not_mentioned": len(nearby_not_mentioned),
            "nearby_not_mentioned_ids": nearby_not_mentioned[:10],
            "estimated_propagation": estimated_propagation,
            "total_potentially_affected": total_impact,
            "directly_affected": exposed_count,
            "approaching_trains": len(nearby_not_mentioned) + estimated_propagation,
            "nearby_train_ids": (nearby_not_mentioned + [f"传播~{i}" for i in range(estimated_propagation)])[:10],
            "base_delay_minutes": delay,
            "is_peak_hours": is_peak,
            "is_window_period": is_window,
            "urgency_reference": urgency,
            "affected_trains_detail": trains_at_location_detail,
            "scene_category": getattr(accident_card, 'scene_category', ''),
            "location_type": getattr(accident_card, 'location_type', ''),
            "is_complete": getattr(accident_card, 'is_complete', True),
            "methodology_note": (
                f"受影响列车基于运行图分析（非仅用户输入）。"
                f"{exposed_count}列会经过事故位置，"
                f"其中{len(initially_delayed)}列被注入初始延误，"
                f"估算{estimated_propagation}列可能受传播影响。"
            )
        }

        return DispatchSkillOutput(
            optimized_schedule={}, delay_statistics=result,
            computation_time=time.time() - start_time, success=True,
            message=f"态势感知完成，紧急度: {urgency}", skill_name=self.name
        )


class QuickLineOverviewSkill(BaseDispatchSkill):
    """线路快速概览技能"""
    name = "quick_line_overview"
    description = "线路快速概览：统计全线密度、高峰区间、当前时段。纯数据统计，不调用求解器，毫秒级响应。"

    def execute(self, train_ids, station_codes, delay_injection, optimization_objective="min_total_delay", **kwargs):
        start_time = time.time()
        from datetime import datetime

        total = len(self.trains) if self.trains else 0
        station_counts = {}
        for t in self.trains:
            if hasattr(t, 'schedule') and t.schedule and hasattr(t.schedule, 'stops'):
                for s in t.schedule.stops:
                    code = getattr(s, 'station_code', None)
                    if code:
                        station_counts[code] = station_counts.get(code, 0) + 1

        densest = max(station_counts.items(), key=lambda x: x[1]) if station_counts else ("", 0)
        hour = datetime.now().hour
        if 0 <= hour < 6:
            period, period_note = "天窗期", "列车稀疏，适合维修作业"
        elif 6 <= hour < 9:
            period, period_note = "早高峰前", "密度逐步增加"
        elif 9 <= hour < 14:
            period, period_note = "日间运营", "密度较高"
        elif 14 <= hour < 18:
            period, period_note = "下午高峰", "全天密度最高"
        elif 18 <= hour < 22:
            period, period_note = "晚间运营", "密度逐步下降"
        else:
            period, period_note = "深夜", "即将进入天窗期"

        result = {
            "success": True,
            "total_trains": total,
            "period": period,
            "period_note": period_note,
            "densest_station_code": densest[0],
            "densest_station_trains": densest[1],
            "station_count": len(station_counts),
            "summary": f"当前{period}，全线共{total}列运行图，{densest[0]}站密度最高({densest[1]}列停靠)"
        }
        return DispatchSkillOutput(
            optimized_schedule={}, delay_statistics=result,
            computation_time=time.time() - start_time, success=True,
            message="线路概览完成", skill_name=self.name
        )


class CheckImpactCascadeSkill(BaseDispatchSkill):
    """延误传播快速检查技能"""
    name = "check_impact_cascade"
    description = "延误传播快速检查：基于运行图静态分析，不调用求解器。回答某列车在某站晚点会堵多少车。"

    def execute(self, train_ids, station_codes, delay_injection, optimization_objective="min_total_delay", **kwargs):
        start_time = time.time()
        from datetime import datetime

        train_id = kwargs.get("train_id", "")
        station_code = kwargs.get("station_code", "")
        delay_mins = kwargs.get("delay_minutes", 0)

        if not train_id or not station_code:
            return DispatchSkillOutput(
                optimized_schedule={}, delay_statistics={"success": False, "error": "缺少train_id或station_code参数"},
                computation_time=0.0, success=False,
                message="缺少参数", skill_name=self.name
            )

        affected_stations = []
        for t in self.trains:
            if getattr(t, 'train_id', None) == train_id:
                found = False
                schedule = getattr(t, 'schedule', None)
                stops = getattr(schedule, 'stops', []) if schedule else []
                for s in stops:
                    code = getattr(s, 'station_code', None)
                    if found and code:
                        affected_stations.append(code)
                    if code == station_code:
                        found = True
                        affected_stations.append(code)
                break

        impacted = []
        for t in self.trains:
            tid = getattr(t, 'train_id', None)
            if not tid or tid == train_id:
                continue
            schedule = getattr(t, 'schedule', None)
            stops = getattr(schedule, 'stops', []) if schedule else []
            for s in stops:
                code = getattr(s, 'station_code', None)
                if code in affected_stations:
                    impacted.append({"train_id": tid, "station": code})
                    break

        density_factor = 1.5 if 9 <= datetime.now().hour <= 18 else 1.0
        estimated_propagation = int((delay_mins / 10) * (len(impacted) / 5) * density_factor)
        estimated_propagation = min(estimated_propagation, len(impacted) * 2)
        estimated_propagation = max(0, estimated_propagation)

        result = {
            "success": True,
            "source_train": train_id,
            "source_station": station_code,
            "delay_minutes": delay_mins,
            "downstream_stations": len(affected_stations),
            "downstream_station_ids": affected_stations[:10],
            "potentially_impacted_trains": len(impacted),
            "impacted_trains_sample": [i["train_id"] for i in impacted[:15]],
            "estimated_propagation": estimated_propagation,
            "note": (
                f"静态分析：基于运行图前后顺序。"
                f"{train_id}在{station_code}及之后共经{len(affected_stations)}站，"
                f"这些站上有{len(impacted)}列其他列车可能受传播影响。"
            )
        }
        return DispatchSkillOutput(
            optimized_schedule={}, delay_statistics=result,
            computation_time=time.time() - start_time, success=True,
            message="传播检查完成", skill_name=self.name
        )


class GenerateDispatchNoticeSkill(BaseDispatchSkill):
    """生成正式调度通知文本技能"""
    name = "generate_dispatch_notice"
    description = "生成正式的铁路调度通知文本。纯LLM调用，不跑求解器。"

    def execute(self, train_ids, station_codes, delay_injection, optimization_objective="min_total_delay", **kwargs):
        start_time = time.time()
        accident_card = kwargs.get("accident_card")
        if not accident_card:
            return DispatchSkillOutput(
                optimized_schedule={}, delay_statistics={},
                computation_time=0.0, success=False,
                message="缺少 accident_card 上下文", skill_name=self.name
            )

        audience = kwargs.get("audience", "station")
        audience_label = {"station": "车站值班员", "driver": "列车司机", "control_center": "行车调度台"}.get(audience, "车站值班员")

        prompt_lines = [
            f"请生成一份正式的铁路调度通知，受众：{audience_label}。",
            f"事故类型：{getattr(accident_card, 'scene_category', '未知')}，故障：{getattr(accident_card, 'fault_type', '未知')}。",
            f"位置：{getattr(accident_card, 'location_name', '') or getattr(accident_card, 'location_code', '') or '未知'}，预计持续{getattr(accident_card, 'expected_duration', '未知')}分钟。",
            f"受影响列车：{', '.join((getattr(accident_card, 'affected_train_ids', None) or [])[:8]) if getattr(accident_card, 'affected_train_ids', None) else '待排查'}。",
            "要求：",
            "1. 包含命令编号占位符[命令编号]、发令时间占位符[发令时间]、受令处所占位符[受令处所]",
            "2. 语气正式、简洁、准确，符合中国铁路调度命令规范",
            "3. 明确限速值、起止时间、影响范围",
            "4. 不超过200字"
        ]
        prompt = "\n".join(prompt_lines)

        try:
            from railway_agent.adapters.llm_adapter import get_llm_caller
            llm = get_llm_caller()
            response = llm.call(prompt, max_tokens=512, temperature=0.3)
            text = response.get("content", "") if isinstance(response, dict) else str(response)
        except Exception as e:
            logger.warning(f"[generate_dispatch_notice] LLM生成失败: {e}")
            text = (
                f"【调度通知草案，请人工完善】\n"
                f"因{getattr(accident_card, 'fault_type', getattr(accident_card, 'scene_category', ''))}，"
                f"{getattr(accident_card, 'location_name', '') or getattr(accident_card, 'location_code', '')}起限速运行，"
                f"预计持续{getattr(accident_card, 'expected_duration', '未知')}分钟，"
                f"请相关列车注意。"
            )

        result = {
            "success": True,
            "audience": audience,
            "audience_label": audience_label,
            "notice_text": text,
            "can_copy": True,
            "scene_category": getattr(accident_card, 'scene_category', ''),
            "location": getattr(accident_card, 'location_name', '') or getattr(accident_card, 'location_code', '')
        }
        return DispatchSkillOutput(
            optimized_schedule={}, delay_statistics=result,
            computation_time=time.time() - start_time, success=True,
            message="调度通知生成完成", skill_name=self.name
        )


class RunSolverSkill(BaseDispatchSkill):
    """执行单个求解器技能（L2 Agent专用）"""
    name = "run_solver"
    description = "执行单个求解器进行调度优化。可精确控制求解器类型、优化目标和参数。MIP适合小规模非紧急场景（全局最优但慢），FCFS适合紧急响应（秒级）。"

    def execute(self, train_ids, station_codes, delay_injection, optimization_objective="min_total_delay", **kwargs):
        start_time = time.time()
        accident_card = kwargs.get("accident_card")
        if not accident_card:
            return DispatchSkillOutput(
                optimized_schedule={}, delay_statistics={},
                computation_time=0.0, success=False,
                message="缺少 accident_card 上下文", skill_name=self.name
            )

        solver_name = kwargs.get("solver", "fcfs")
        objective = kwargs.get("optimization_objective", "min_total_delay")
        time_limit = kwargs.get("time_limit", 120)
        gap = kwargs.get("optimality_gap", 0.05)

        time_limit = max(30, min(600, int(time_limit)))
        gap = max(0.01, min(0.1, round(float(gap), 2)))
        if objective not in ["min_max_delay", "min_total_delay", "min_avg_delay"]:
            objective = "min_max_delay"

        # 使用现有 SkillRegistry 的 dispatch_solve_skill 执行
        # 先构建 delay_injection
        location_code = getattr(accident_card, 'location_code', "") or ""
        delay_seconds = int(getattr(accident_card, 'expected_duration', 0) * 60) if getattr(accident_card, 'expected_duration', None) else 600
        affected_train_ids = getattr(accident_card, 'affected_train_ids', None) or []

        from models.data_models import InjectedDelay, DelayLocation, DelayInjection
        injected_delays = []
        loc_type = getattr(accident_card, 'location_type', "station") or "station"
        for train_id in affected_train_ids:
            injected_delays.append(InjectedDelay(
                train_id=train_id,
                location=DelayLocation(location_type=loc_type, station_code=location_code),
                initial_delay_seconds=delay_seconds,
                timestamp=datetime.now().isoformat()
            ))

        inner_delay_injection = DelayInjection(
            scenario_type=getattr(accident_card, 'scene_type', 'sudden_failure'),
            scenario_id=getattr(accident_card, 'scene_id', 'default'),
            injected_delays=injected_delays,
            affected_trains=affected_train_ids
        )

        # 调用 dispatch_solve_skill
        solver = DispatchSolveSkill(self.trains, self.stations)
        solve_result = solver.execute(
            train_ids=affected_train_ids,
            station_codes=[location_code] if location_code else [],
            delay_injection={
                "scenario_type": getattr(accident_card, 'scene_type', 'sudden_failure'),
                "scenario_id": getattr(accident_card, 'scene_id', 'default'),
                "injected_delays": [
                    {
                        "train_id": tid,
                        "location_type": loc_type,
                        "station_code": location_code,
                        "initial_delay_seconds": delay_seconds
                    } for tid in affected_train_ids
                ],
                "solver_config": {
                    "solver": solver_name,
                    "optimization_objective": objective,
                    "time_limit": time_limit,
                    "optimality_gap": gap
                }
            },
            optimization_objective=objective
        )

        # 转换为 L2 期望的 Dict 格式
        stats = solve_result.delay_statistics
        result = {
            "solver": solver_name,
            "success": solve_result.success,
            "total_delay_minutes": round(stats.get("total_delay_seconds", 0) / 60, 2),
            "max_delay_minutes": round(stats.get("max_delay_seconds", 0) / 60, 2),
            "avg_delay_minutes": round(stats.get("avg_delay_seconds", 0) / 60, 2),
            "solving_time_seconds": round(solve_result.computation_time, 2),
            "affected_trains_count": stats.get("affected_trains_count", len(affected_train_ids)),
            "optimized_schedule": solve_result.optimized_schedule,
            "error": solve_result.message if not solve_result.success else None
        }
        return DispatchSkillOutput(
            optimized_schedule=result.get("optimized_schedule", {}),
            delay_statistics=result,
            computation_time=time.time() - start_time,
            success=solve_result.success,
            message=f"求解器 {solver_name} 执行完成" if solve_result.success else f"求解失败: {solve_result.message}",
            skill_name=self.name
        )


class CompareStrategiesToolSkill(BaseDispatchSkill):
    """多策略对比技能（L2 Agent专用）"""
    name = "compare_strategies"
    description = "基于场景特征和优化目标，通过规则推荐最优求解器及参数配置。不实际执行求解器，只做智能推荐，将推荐结果供下游调度引擎执行。适用于需要快速确定求解策略的场景。"

    def execute(self, train_ids, station_codes, delay_injection, optimization_objective="min_total_delay", **kwargs):
        start_time = time.time()
        accident_card = kwargs.get("accident_card")
        if not accident_card:
            return DispatchSkillOutput(
                optimized_schedule={}, delay_statistics={},
                computation_time=0.0, success=False,
                message="缺少 accident_card 上下文", skill_name=self.name
            )

        strategies = kwargs.get("strategies")
        objective = kwargs.get("optimization_objective", "min_total_delay")
        time_budget = kwargs.get("time_budget", 300)

        affected_count = len(getattr(accident_card, 'affected_train_ids', None) or [])
        expected_delay = getattr(accident_card, 'expected_duration', None) or 10
        is_large_scale = affected_count > 10 or expected_delay > 30
        is_emergency = expected_delay > 60 or getattr(accident_card, 'scene_category', '') == "区间封锁"

        if strategies is None:
            if getattr(accident_card, 'scene_category', '') == "区间封锁" or is_emergency:
                strategies = ["fcfs"]
            elif objective == "min_max_delay":
                strategies = ["max_delay_first", "hierarchical", "fcfs"] if is_large_scale else ["max_delay_first", "mip", "fcfs"]
            elif objective in ("min_total_delay", "min_avg_delay"):
                strategies = ["hierarchical", "mip", "fcfs"] if is_large_scale else ["mip", "hierarchical", "fcfs"]
            else:
                strategies = ["hierarchical", "mip", "fcfs"] if is_large_scale else ["mip", "hierarchical", "fcfs"]

        # 复用已有的 compare_strategies_skill 执行核心逻辑
        compare_skill = CompareStrategiesSkill(self.trains, self.stations)
        inner_delay_inj = {
            "scenario_type": getattr(accident_card, 'scene_type', 'sudden_failure'),
            "scenario_id": getattr(accident_card, 'scene_id', 'default'),
            "injected_delays": [
                {
                    "train_id": tid,
                    "location_type": getattr(accident_card, 'location_type', 'station'),
                    "station_code": getattr(accident_card, 'location_code', ''),
                    "initial_delay_seconds": int(getattr(accident_card, 'expected_duration', 0) * 60) if getattr(accident_card, 'expected_duration', None) else 600
                } for tid in (getattr(accident_card, 'affected_train_ids', None) or [])
            ],
            "solver_config": {
                "strategies": strategies,
                "optimization_objective": objective
            }
        }

        compare_result = compare_skill.execute(
            train_ids=getattr(accident_card, 'affected_train_ids', None) or [],
            station_codes=[getattr(accident_card, 'location_code', '')] if getattr(accident_card, 'location_code', '') else [],
            delay_injection=inner_delay_inj,
            optimization_objective=objective,
            strategies=strategies,
            time_budget=time_budget
        )

        stats = compare_result.delay_statistics
        result = {
            "success": compare_result.success,
            "strategies_tested": stats.get("strategies_tested", 0),
            "results": stats.get("results", []),
            "best_solution": stats.get("results", [{}])[0] if stats.get("results") else None,
            "best_solver": stats.get("best_scheduler", None),
            "comparison_summary": stats.get("comparison_summary", ""),
            "optimization_objective": objective,
            "reasoning": f"根据优化目标'{objective}'对比{len(strategies)}个策略"
        }
        return DispatchSkillOutput(
            optimized_schedule={}, delay_statistics=result,
            computation_time=time.time() - start_time,
            success=compare_result.success,
            message=compare_result.message, skill_name=self.name
        )


# ============================================
# 工厂函数
# ============================================

def create_skills(trains=None, stations=None) -> Dict[str, BaseDispatchSkill]:
    """
    创建 Skills 工厂函数

    Skills 列表：
      - dispatch_solve_skill: 通用调度求解
      - compare_strategies_skill: 多策略对比
      - station_load_skill: 车站负荷分析
      - delay_propagation_skill: 延误传播预测
      - get_train_status: 列车状态查询
      - query_timetable: 时刻表查询
    """
    # 兼容 web/app.py: create_skills(scheduler) 传入调度器实例
    if stations is None and hasattr(trains, 'stations'):
        stations = trains.stations
    if hasattr(trains, 'trains'):
        trains = trains.trains

    return {
        # 求解类技能
        "dispatch_solve_skill": DispatchSolveSkill(trains, stations),
        "compare_strategies_skill": CompareStrategiesSkill(trains, stations),
        # 分析类技能
        "station_load_skill": StationLoadSkill(trains, stations),
        "delay_propagation_skill": DelayPropagationSkill(trains, stations),
        # 查询类技能
        "get_train_status": GetTrainStatusSkill(trains, stations),
        "query_timetable": QueryTimetableSkill(trains, stations),
        # L2 Agent 工具类技能（统一迁入 SkillRegistry）
        "assess_impact": AssessImpactSkill(trains, stations),
        "quick_line_overview": QuickLineOverviewSkill(trains, stations),
        "check_impact_cascade": CheckImpactCascadeSkill(trains, stations),
        "generate_dispatch_notice": GenerateDispatchNoticeSkill(trains, stations),
        "run_solver": RunSolverSkill(trains, stations),
        "compare_strategies": CompareStrategiesToolSkill(trains, stations),
    }


def execute_skill(
    skill_name: str,
    skills: Dict[str, BaseDispatchSkill],
    train_ids: List[str],
    station_codes: List[str],
    delay_injection: Dict[str, Any],
    optimization_objective: str = "min_total_delay",
    **kwargs
) -> DispatchSkillOutput:
    """执行指定的 Skill"""
    if skill_name not in skills:
        return DispatchSkillOutput(
            optimized_schedule={}, delay_statistics={},
            computation_time=0.0, success=False,
            message=f"Skill '{skill_name}' 不存在", skill_name=skill_name
        )

    skill = skills[skill_name]
    return skill.execute(
        train_ids=train_ids, station_codes=station_codes,
        delay_injection=delay_injection,
        optimization_objective=optimization_objective, **kwargs
    )
