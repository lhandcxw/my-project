# -*- coding: utf-8 -*-
"""
技能实现模块
从旧架构迁移而来，适配新架构的适配器模式

包含所有调度和查询技能的完整实现
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import json
import time
import logging

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solver.base_solver import SolverRequest, SolverResponse
from solver.solver_registry import get_default_registry

logger = logging.getLogger(__name__)


# ============================================
# 数据模型
# ============================================

@dataclass
class DispatchSkillInput:
    """调度Skill输入参数"""
    train_ids: List[str]
    station_codes: List[str]
    delay_injection: Dict[str, Any]
    optimization_objective: str = "min_max_delay"


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
        """初始化技能"""
        self.trains = trains
        self.stations = stations
        self.solver_registry = get_default_registry()

    def _build_solver_request(self, delay_injection: Dict[str, Any]) -> SolverRequest:
        """构建求解请求"""
        return SolverRequest(
            scene_type=delay_injection.get("scenario_type", "unknown"),
            scene_id=delay_injection.get("scenario_id", "default"),
            injected_delays=delay_injection.get("injected_delays", []),
            trains=self.trains if self.trains else [],
            stations=self.stations if self.stations else [],
            solver_config={
                "optimization_objective": "min_max_delay"
            }
        )

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay"
    ) -> DispatchSkillOutput:
        """执行调度Skill"""
        raise NotImplementedError


# ============================================
# 调度技能类
# ============================================

class TemporarySpeedLimitSkill(BaseDispatchSkill):
    """
    临时限速场景调度Skill
    适用于：铁路线路临时限速导致的多列列车延误调整
    """

    name = "temporary_speed_limit_skill"
    description = "处理临时限速场景的列车调度"

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay"
    ) -> DispatchSkillOutput:
        """执行临时限速调度"""
        start_time = time.time()

        # 提取限速参数
        speed_limit = delay_injection.get("scenario_params", {}).get("limit_speed_kmh", 200)
        affected_section = delay_injection.get("scenario_params", {}).get("affected_section", "")

        # 使用求解器
        solver_request = self._build_solver_request(delay_injection)
        solver = self.solver_registry.get_solver("mip")
        solver_response = solver.solve(solver_request)

        computation_time = time.time() - start_time

        # 构建结果
        delay_stats = solver_response.metrics or {}
        if hasattr(solver_response, 'model_dump'):
            schedule_data = solver_response.model_dump()
        else:
            schedule_data = {
                "success": solver_response.success,
                "status": solver_response.status,
                "total_delay_minutes": delay_stats.get("total_delay_seconds", 0) // 60,
                "max_delay_minutes": delay_stats.get("max_delay_seconds", 0) // 60
            }

        return DispatchSkillOutput(
            optimized_schedule=schedule_data,
            delay_statistics={
                "max_delay_minutes": delay_stats.get("max_delay_seconds", 0) // 60,
                "avg_delay_minutes": delay_stats.get("avg_delay_seconds", 0) // 60 if "avg_delay_seconds" in delay_stats else 0,
                "total_delay_minutes": delay_stats.get("total_delay_seconds", 0) // 60,
                "affected_trains_count": len(train_ids)
            },
            computation_time=computation_time + (solver_response.solving_time_seconds if hasattr(solver_response, 'solving_time_seconds') else 0),
            success=solver_response.success,
            message=f"临时限速调度完成。限速值: {speed_limit}km/h, 影响区段: {affected_section}",
            skill_name=self.name
        )


class SuddenFailureSkill(BaseDispatchSkill):
    """
    突发故障场景调度Skill
    适用于：列车设备故障、区间占用等单列车故障场景
    """

    name = "sudden_failure_skill"
    description = "处理突发故障场景的列车调度"

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay"
    ) -> DispatchSkillOutput:
        """执行突发故障调度"""
        start_time = time.time()

        # 提取故障信息
        if delay_injection.get("injected_delays"):
            failure_info = delay_injection["injected_delays"][0]
            failure_train = failure_info.get("train_id", "未知")
        else:
            failure_train = "未知"

        # 使用FCFS求解器（快速响应）
        solver_request = self._build_solver_request(delay_injection)
        solver = self.solver_registry.get_solver("fcfs")
        solver_response = solver.solve(solver_request)

        computation_time = time.time() - start_time

        # 构建结果
        delay_stats = solver_response.metrics or {}
        if hasattr(solver_response, 'model_dump'):
            schedule_data = solver_response.model_dump()
        else:
            schedule_data = {
                "success": solver_response.success,
                "status": solver_response.status,
                "total_delay_minutes": delay_stats.get("total_delay_seconds", 0) // 60,
                "max_delay_minutes": delay_stats.get("max_delay_seconds", 0) // 60
            }

        return DispatchSkillOutput(
            optimized_schedule=schedule_data,
            delay_statistics={
                "max_delay_minutes": delay_stats.get("max_delay_seconds", 0) // 60,
                "avg_delay_minutes": delay_stats.get("avg_delay_seconds", 0) // 60 if "avg_delay_seconds" in delay_stats else 0,
                "total_delay_minutes": delay_stats.get("total_delay_seconds", 0) // 60,
                "affected_trains_count": len(train_ids)
            },
            computation_time=computation_time + (solver_response.solving_time_seconds if hasattr(solver_response, 'solving_time_seconds') else 0),
            success=solver_response.success,
            message=f"突发故障调度完成。故障列车: {failure_train}",
            skill_name=self.name
        )


class SectionInterruptSkill(BaseDispatchSkill):
    """
    区间中断场景调度Skill
    适用于：线路中断、严重自然灾害等导致区间无法通行
    """

    name = "section_interrupt_skill"
    description = "处理区间中断场景的列车调度"

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay"
    ) -> DispatchSkillOutput:
        """执行区间中断调度"""
        start_time = time.time()

        # 区间中断暂不支持，返回基线结果
        affected_section = delay_injection.get("scenario_params", {}).get("affected_section", "未知区段")

        computation_time = time.time() - start_time

        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics={},
            computation_time=computation_time,
            success=False,
            message=f"区间中断场景当前版本暂不支持。区段: {affected_section}",
            skill_name=self.name
        )


# ============================================
# 查询技能类
# ============================================

class GetTrainStatusSkill(BaseDispatchSkill):
    """
    列车状态查询技能
    查询指定列车的实时运行状态
    """

    name = "get_train_status"
    description = "查询指定列车的实时运行状态"

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay",
        **kwargs
    ) -> DispatchSkillOutput:
        """查询列车状态"""
        start_time = time.time()

        # 提取参数
        train_id = kwargs.get("train_id", train_ids[0] if train_ids else None)

        if not train_id:
            return DispatchSkillOutput(
                optimized_schedule={},
                delay_statistics={},
                computation_time=0.0,
                success=False,
                message="请提供列车ID",
                skill_name=self.name
            )

        # 查找列车
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
                        train_info["total_stops"] = len(t.schedule.stops)
                        train_info["stops"] = [
                            {
                                "station_code": s.station_code,
                                "station_name": s.station_name,
                                "arrival_time": s.arrival_time,
                                "departure_time": s.departure_time,
                                "is_stopped": s.is_stopped
                            }
                            for s in t.schedule.stops[:5]  # 只返回前5站
                        ]
                    break

        computation_time = time.time() - start_time

        if not train_info:
            return DispatchSkillOutput(
                optimized_schedule={},
                delay_statistics={},
                computation_time=computation_time,
                success=False,
                message=f"未找到列车 {train_id}",
                skill_name=self.name
            )

        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics=train_info,
            computation_time=computation_time,
            success=True,
            message=f"列车 {train_id} 状态查询完成",
            skill_name=self.name
        )


class QueryTimetableSkill(BaseDispatchSkill):
    """
    时刻表查询技能
    查询列车时刻表或车站时刻表
    """

    name = "query_timetable"
    description = "查询列车时刻表或车站时刻表"

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay",
        **kwargs
    ) -> DispatchSkillOutput:
        """查询时刻表"""
        start_time = time.time()

        train_id = kwargs.get("train_id")
        station_code = kwargs.get("station_code")

        results = {"query_type": None, "timetable_type": kwargs.get("timetable_type", "plan"), "trains": []}

        # 按列车查询
        if train_id and self.trains:
            results["query_type"] = "train"
            results["train_id"] = train_id

            for train in self.trains:
                if hasattr(train, 'train_id') and train.train_id == train_id:
                    results["train_type"] = getattr(train, 'train_type', '未知')
                    if hasattr(train, 'schedule') and hasattr(train.schedule, 'stops'):
                        results["total_stops"] = len(train.schedule.stops)
                        results["stops"] = [
                            {
                                "station_code": s.station_code,
                                "station_name": s.station_name,
                                "arrival_time": s.arrival_time,
                                "departure_time": s.departure_time,
                                "is_stopped": s.is_stopped,
                                "stop_duration_seconds": s.stop_duration
                            }
                            for s in train.schedule.stops
                        ]
                    break

        # 按车站查询
        elif station_code and self.trains:
            results["query_type"] = "station"
            results["station_code"] = station_code

            # 找到车站名称
            station_name = station_code
            if self.stations:
                for s in self.stations:
                    if hasattr(s, 'station_code') and s.station_code == station_code:
                        station_name = s.station_name
                        break
            results["station_name"] = station_name

            # 找到在该车站停靠的列车
            trains_at_station = []
            for train in self.trains:
                if hasattr(train, 'schedule') and hasattr(train.schedule, 'stops'):
                    for stop in train.schedule.stops:
                        if stop.station_code == station_code:
                            trains_at_station.append({
                                "train_id": train.train_id,
                                "arrival_time": stop.arrival_time,
                                "departure_time": stop.departure_time,
                                "is_stopped": stop.is_stopped
                            })
                            break

            results["trains"] = trains_at_station[:20]  # 最多返回20列
            results["total_trains"] = len(trains_at_station)

        computation_time = time.time() - start_time

        if train_id:
            message = f"列车 {train_id} 时刻表查询完成"
        elif station_code:
            message = f"车站 {station_code} 时刻表查询完成"
        else:
            return DispatchSkillOutput(
                optimized_schedule={},
                delay_statistics={},
                computation_time=computation_time,
                success=False,
                message="请提供列车ID或车站编码",
                skill_name=self.name
            )

        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics=results,
            computation_time=computation_time,
            success=True,
            message=message,
            skill_name=self.name
        )


# ============================================
# 工厂函数
# ============================================

def create_skills(trains=None, stations=None) -> Dict[str, BaseDispatchSkill]:
    """
    创建Skills工厂函数

    Args:
        trains: 列车列表
        stations: 车站列表

    Returns:
        Dict[str, BaseDispatchSkill]: Skills字典
    """
    return {
        # 调度技能
        "temporary_speed_limit_skill": TemporarySpeedLimitSkill(trains, stations),
        "sudden_failure_skill": SuddenFailureSkill(trains, stations),
        "section_interrupt_skill": SectionInterruptSkill(trains, stations),
        # 查询技能
        "get_train_status": GetTrainStatusSkill(trains, stations),
        "query_timetable": QueryTimetableSkill(trains, stations)
    }


def execute_skill(
    skill_name: str,
    skills: Dict[str, BaseDispatchSkill],
    train_ids: List[str],
    station_codes: List[str],
    delay_injection: Dict[str, Any],
    optimization_objective: str = "min_max_delay",
    **kwargs
) -> DispatchSkillOutput:
    """
    执行指定的Skill

    Args:
        skill_name: Skill名称
        skills: Skills字典
        train_ids: 列车ID列表
        station_codes: 车站编码列表
        delay_injection: 延误注入数据
        optimization_objective: 优化目标
        **kwargs: 额外参数，用于查询类技能

    Returns:
        DispatchSkillOutput: 执行结果
    """
    if skill_name not in skills:
        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics={},
            computation_time=0.0,
            success=False,
            message=f"Skill '{skill_name}' 不存在",
            skill_name=skill_name
        )

    skill = skills[skill_name]
    return skill.execute(
        train_ids=train_ids,
        station_codes=station_codes,
        delay_injection=delay_injection,
        optimization_objective=optimization_objective,
        **kwargs
    )
