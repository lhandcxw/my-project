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
        optimization_objective: str = "min_total_delay"
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
        optimization_objective: str = "min_total_delay"
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

        # 自动选择策略
        if not strategies:
            scenario_type = delay_injection.get("scenario_type", "")
            if scenario_type == "section_interrupt":
                strategies = ["fcfs"]
            elif len(train_ids) <= 10:
                strategies = ["fcfs", "mip"]
            else:
                strategies = ["fcfs", "max_delay_first"]

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

        # 按最大延误排序
        successful = [r for r in results if r.get("success")]
        successful.sort(key=lambda r: r.get("max_delay_minutes", 9999))
        best = successful[0] if successful else None

        computation_time = time.time() - start_time

        summary_lines = []
        for r in results:
            if r.get("success"):
                summary_lines.append(
                    f"{r['scheduler']}: 总延误{r.get('total_delay_minutes')}分, "
                    f"最大延误{r.get('max_delay_minutes')}分, 耗时{r.get('solving_time_seconds')}秒"
                )
            else:
                summary_lines.append(f"{r['scheduler']}: 失败({r.get('error', '')})")

        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics={
                "strategies_tested": len(results),
                "results": results,
                "best_scheduler": best["scheduler"] if best else None,
                "comparison_summary": " | ".join(summary_lines)
            },
            computation_time=computation_time,
            success=best is not None,
            message=f"策略对比完成。最优: {best['scheduler'] if best else '无'}",
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
                            train_info["stops"] = [
                                {
                                    "station_code": s.station_code,
                                    "station_name": s.station_name,
                                    "arrival_time": s.arrival_time,
                                    "departure_time": s.departure_time,
                                    "is_stopped": s.is_stopped
                                }
                                for s in stops[:5]
                            ]
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
        "query_timetable": QueryTimetableSkill(trains, stations)
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
