# -*- coding: utf-8 -*-
"""
铁路调度系统 - Skills模块
对应架构文档第6节：Skills开发规范
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import json
import time

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.data_models import Train, Station, DelayInjection
from solver.mip_scheduler import MIPScheduler, SolveResult


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


class BaseDispatchSkill:
    """铁路调度Skill基类"""

    name: str = "base_dispatch_skill"
    description: str = "基础调度Skill"

    def __init__(self, scheduler: MIPScheduler):
        self.scheduler = scheduler

    def _parse_delay_injection(self, delay_data: Dict[str, Any]) -> DelayInjection:
        """解析延误注入数据"""
        return DelayInjection(**delay_data)

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay"
    ) -> DispatchSkillOutput:
        """执行调度Skill"""
        raise NotImplementedError


class TemporarySpeedLimitSkill(BaseDispatchSkill):
    """
    临时限速场景调度Skill
    适用于：铁路线路临时限速导致的多列列车延误调整
    """

    name = "temporary_speed_limit_dispatch"
    description = """
    处理临时限速场景的列车调度

    适用于：铁路线路临时限速导致的多列列车延误调整
    输入：受影响列车列表、限速区段、限速值、持续时间
    输出：调整后的时刻表和延误统计
    """

    def __init__(self, scheduler: MIPScheduler):
        super().__init__(scheduler)

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay"
    ) -> DispatchSkillOutput:
        """
        临时限速调度逻辑：

        1. 提取限速参数
        2. 应用MIP求解调整方案
        3. 输出最优调度方案

        Args:
            train_ids: 受影响列车ID列表
            station_codes: 涉及的车站编码列表
            delay_injection: 延误注入数据字典
            optimization_objective: 优化目标

        Returns:
            DispatchSkillOutput: 调度结果
        """
        start_time = time.time()

        # Step 1: 解析延误注入数据
        delay_obj = self._parse_delay_injection(delay_injection)

        # Step 2: 提取限速参数（如果有）
        speed_limit = delay_injection.get("scenario_params", {}).get("limit_speed_kmh", 200)
        affected_section = delay_injection.get("scenario_params", {}).get("affected_section", "")

        # Step 3: MIP求解
        result = self.scheduler.solve(delay_obj, optimization_objective)

        computation_time = time.time() - start_time

        return DispatchSkillOutput(
            optimized_schedule=result.optimized_schedule,
            delay_statistics=result.delay_statistics,
            computation_time=computation_time + result.computation_time,
            success=result.success,
            message=f"临时限速调度完成。限速值: {speed_limit}km/h, 影响区段: {affected_section}",
            skill_name=self.name
        )


class SuddenFailureSkill(BaseDispatchSkill):
    """
    突发故障场景调度Skill
    适用于：列车设备故障、区间占用等单列车故障场景
    """

    name = "sudden_failure_dispatch"
    description = """
    处理突发故障场景的列车调度

    适用于：列车设备故障、区间占用等单列车故障场景
    输入：故障列车信息、故障位置、预计恢复时间
    输出：调整后的时刻表和延误统计
    """

    def __init__(self, scheduler: MIPScheduler):
        super().__init__(scheduler)

    def _analyze_delay_propagation(
        self,
        failure_train: str,
        delay_seconds: int
    ) -> Dict[str, Any]:
        """
        分析延误传播

        Args:
            failure_train: 故障列车ID
            delay_seconds: 延误时间

        Returns:
            Dict: 传播分析结果
        """
        # 简化版：延误会传播到后续列车
        propagation_factor = 0.5  # 传播系数
        return {
            "failure_train": failure_train,
            "initial_delay": delay_seconds,
            "propagation_factor": propagation_factor,
            "estimated_affected_trains": 2
        }

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay"
    ) -> DispatchSkillOutput:
        """
        突发故障调度逻辑：

        1. 识别故障列车和位置
        2. 计算故障导致的延误传播
        3. 应用优化求解

        Args:
            train_ids: 受影响列车ID列表
            station_codes: 涉及的车站编码列表
            delay_injection: 延误注入数据字典
            optimization_objective: 优化目标

        Returns:
            DispatchSkillOutput: 调度结果
        """
        start_time = time.time()

        # Step 1: 解析延误注入数据
        delay_obj = self._parse_delay_injection(delay_injection)

        # Step 2: 提取故障信息
        if delay_obj.injected_delays:
            failure_info = delay_obj.injected_delays[0]
            failure_train = failure_info.train_id
            failure_delay = failure_info.initial_delay_seconds

            # 分析延误传播
            propagation = self._analyze_delay_propagation(failure_train, failure_delay)
        else:
            propagation = {}

# Step 3: MIP求解
        result = self.scheduler.solve(delay_obj, optimization_objective)

        computation_time = time.time() - start_time

        # 调试输出
        if not result.success:
            print(f"⚠️ 调度失败：{result.message}")
        else:
            print("✅ 优化成功，变化如下：")
            for train_id, stops in result.optimized_schedule.items():
                changes = [s for s in stops if s['delay_seconds'] > 0]
                print(f"列车{train_id}：{len(changes)}个站点时刻变化")
                for stop in stops:
                    if stop['delay_seconds'] > 0:
                        print(f"  车站{stop['station_code']}: {stop['original_arrival']} → {stop['arrival_time']} (延误{stop['delay_seconds']}秒)")

        return DispatchSkillOutput(
            optimized_schedule=result.optimized_schedule,
            delay_statistics=result.delay_statistics,
            computation_time=computation_time + result.computation_time,
            success=result.success,
            message=f"突发故障调度完成。故障列车: {failure_train}, 延误传播分析: {propagation}",
            skill_name=self.name
        )


class SectionInterruptSkill(BaseDispatchSkill):
    """
    区间中断场景调度Skill（预留扩展）
    注意：当前版本暂不实现，仅预留接口
    """

    name = "section_interrupt_dispatch"
    description = """
    处理区间中断场景的列车调度

    注意：当前版本暂不实现，仅预留接口
    适用于：线路中断、严重自然灾害等导致区间无法通行
    需要处理：车底调配、乘务员调整
    """

    def __init__(self, scheduler: MIPScheduler):
        super().__init__(scheduler)

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay"
    ) -> DispatchSkillOutput:
        """区间中断暂不支持"""
        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics={},
            computation_time=0.0,
            success=False,
            message="区间中断场景当前版本暂不支持",
            skill_name=self.name
        )


# ============================================
# 新增：查询类Skills
# ============================================

class GetTrainStatusSkill(BaseDispatchSkill):
    """
    列车状态查询技能
    查询指定列车的实时运行状态
    """

    name = "get_train_status"
    description = "查询指定列车的实时运行状态，包括位置、晚点、下一站等信息"

    def __init__(self, scheduler: MIPScheduler):
        super().__init__(scheduler)

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay",
        **kwargs
    ) -> DispatchSkillOutput:
        """
        查询列车状态

        Args:
            train_ids: 列车ID列表（这里只使用第一个）
            station_codes: 车站编码列表
            delay_injection: 可包含额外参数
            optimization_objective: 优化目标（忽略）
            **kwargs: 额外参数，如 train_id, include_position, include_delay

        Returns:
            DispatchSkillOutput: 列车状态信息
        """
        # 提取参数
        train_id = kwargs.get("train_id", train_ids[0] if train_ids else None)
        include_position = kwargs.get("include_position", True)
        include_delay = kwargs.get("include_delay", True)

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
        train = None
        for t in self.scheduler.trains:
            if t.train_id == train_id:
                train = t
                break

        if not train:
            return DispatchSkillOutput(
                optimized_schedule={},
                delay_statistics={},
                computation_time=0.0,
                success=False,
                message=f"未找到列车 {train_id}",
                skill_name=self.name
            )

        # 构建状态信息
        status_info = {
            "train_id": train.train_id,
            "train_type": train.train_type,
            "total_stops": len(train.schedule.stops),
            "first_station": train.schedule.stops[0].station_name if train.schedule.stops else None,
            "last_station": train.schedule.stops[-1].station_name if train.schedule.stops else None,
            "stops": []
        }

        # 添加每站信息
        for stop in train.schedule.stops:
            stop_info = {
                "station_code": stop.station_code,
                "station_name": stop.station_name,
                "arrival_time": stop.arrival_time,
                "departure_time": stop.departure_time,
                "is_stopped": stop.is_stopped
            }
            if include_position:
                stop_info["position"] = "已通过" if stop.is_stopped else "待停靠"
            status_info["stops"].append(stop_info)

        # 计算运行时间
        if train.schedule.stops:
            first = train.schedule.stops[0]
            last = train.schedule.stops[-1]
            status_info["total_run_time"] = f"{first.departure_time} -> {last.arrival_time}"

        message = f"列车 {train_id} 状态查询完成，共{len(train.schedule.stops)}站"

        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics=status_info,
            computation_time=0.0,
            success=True,
            message=message,
            skill_name=self.name
        )


class AnalyzeDelayPropagationSkill(BaseDispatchSkill):
    """
    晚点传播分析技能
    分析晚点对后续列车的影响范围和程度
    """

    name = "analyze_delay_propagation"
    description = "分析晚点对后续列车的影响范围和程度，返回受影响列车列表和传播层级"

    def __init__(self, scheduler: MIPScheduler):
        super().__init__(scheduler)

    def _calculate_propagation(
        self,
        delayed_train_id: str,
        delay_minutes: int,
        depth: int
    ) -> Dict[str, Any]:
        """
        计算晚点传播

        Args:
            delayed_train_id: 晚点列车ID
            delay_minutes: 晚点时间（分钟）
            depth: 传播深度

        Returns:
            Dict: 传播分析结果
        """
        # 找到晚点列车
        delayed_train = None
        for train in self.scheduler.trains:
            if train.train_id == delayed_train_id:
                delayed_train = train
                break

        if not delayed_train:
            return {"error": f"未找到列车 {delayed_train_id}"}

        # 获取晚点列车的停靠站信息
        delayed_stations = [s.station_code for s in delayed_train.schedule.stops]

        # 找到受影响的列车
        affected_trains = []
        propagation_trains = []

        for train in self.scheduler.trains:
            if train.train_id == delayed_train_id:
                continue

            train_stations = [s.station_code for s in train.schedule.stops]

            # 检查是否有重叠的车站
            common_stations = set(delayed_stations) & set(train_stations)

            if common_stations:
                # 找到晚点列车之后的第一个重叠站
                delayed_idx = 0
                for i, s in enumerate(delayed_train.schedule.stops):
                    if s.station_code in common_stations:
                        delayed_idx = i
                        break

                # 找到受影响列车在重叠站的到发时间
                affected_station = list(common_stations)[0]
                for i, s in enumerate(train.schedule.stops):
                    if s.station_code == affected_station:
                        # 简化计算：假设传播系数为0.5
                        propagated_delay = int(delay_minutes * 0.5)
                        propagation_trains.append({
                            "train_id": train.train_id,
                            "affected_station": affected_station,
                            "propagated_delay_minutes": propagated_delay,
                            "propagation_factor": 0.5
                        })
                        break

        return {
            "delayed_train": delayed_train_id,
            "original_delay_minutes": delay_minutes,
            "propagation_depth": depth,
            "affected_count": len(propagation_trains),
            "affected_trains": propagation_trains
        }

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay",
        **kwargs
    ) -> DispatchSkillOutput:
        """
        执行晚点传播分析

        Args:
            train_ids: 列车ID列表
            station_codes: 车站编码列表
            delay_injection: 可包含额外参数
            optimization_objective: 优化目标
            **kwargs: 额外参数，如 train_id, delay_minutes, propagation_depth

        Returns:
            DispatchSkillOutput: 晚点传播分析结果
        """
        train_id = kwargs.get("train_id", train_ids[0] if train_ids else None)
        delay_minutes = kwargs.get("delay_minutes", 10)
        propagation_depth = kwargs.get("propagation_depth", 3)

        if not train_id:
            return DispatchSkillOutput(
                optimized_schedule={},
                delay_statistics={},
                computation_time=0.0,
                success=False,
                message="请提供晚点列车ID",
                skill_name=self.name
            )

        result = self._calculate_propagation(train_id, delay_minutes, propagation_depth)

        if "error" in result:
            return DispatchSkillOutput(
                optimized_schedule={},
                delay_statistics={},
                computation_time=0.0,
                success=False,
                message=result["error"],
                skill_name=self.name
            )

        message = f"晚点传播分析完成：列车{train_id}晚点{delay_minutes}分钟，影响{result['affected_count']}列后续列车"

        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics=result,
            computation_time=0.0,
            success=True,
            message=message,
            skill_name=self.name
        )


class QueryTimetableSkill(BaseDispatchSkill):
    """
    时刻表查询技能
    查询列车时刻表或车站时刻表
    """

    name = "query_timetable"
    description = "查询列车或车站的时刻表信息"

    def __init__(self, scheduler: MIPScheduler):
        super().__init__(scheduler)

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay",
        **kwargs
    ) -> DispatchSkillOutput:
        """
        查询时刻表

        Args:
            train_ids: 列车ID列表
            station_codes: 车站编码列表
            delay_injection: 可包含额外参数
            optimization_objective: 优化目标
            **kwargs: 额外参数，如 train_id, station_code, timetable_type

        Returns:
            DispatchSkillOutput: 时刻表信息
        """
        train_id = kwargs.get("train_id")
        station_code = kwargs.get("station_code")
        timetable_type = kwargs.get("timetable_type", "plan")

        results = {
            "query_type": None,
            "timetable_type": timetable_type,
            "trains": []
        }

        # 按列车查询
        if train_id:
            results["query_type"] = "train"
            results["train_id"] = train_id

            train = None
            for t in self.scheduler.trains:
                if t.train_id == train_id:
                    train = t
                    break

            if train:
                results["train_type"] = train.train_type
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
            else:
                return DispatchSkillOutput(
                    optimized_schedule={},
                    delay_statistics={},
                    computation_time=0.0,
                    success=False,
                    message=f"未找到列车 {train_id}",
                    skill_name=self.name
                )

        # 按车站查询
        elif station_code:
            results["query_type"] = "station"
            results["station_code"] = station_code

            # 找到车站名称
            station_name = station_code
            for s in self.scheduler.stations:
                if s.station_code == station_code:
                    station_name = s.station_name
                    break
            results["station_name"] = station_name

            # 找到所有在该车站停靠的列车
            trains_at_station = []
            for train in self.scheduler.trains:
                for stop in train.schedule.stops:
                    if stop.station_code == station_code:
                        trains_at_station.append({
                            "train_id": train.train_id,
                            "arrival_time": stop.arrival_time,
                            "departure_time": stop.departure_time,
                            "is_stopped": stop.is_stopped
                        })
                        break  # 只取第一次停靠

            results["trains"] = trains_at_station
            results["total_trains"] = len(trains_at_station)

        else:
            return DispatchSkillOutput(
                optimized_schedule={},
                delay_statistics={},
                computation_time=0.0,
                success=False,
                message="请提供列车ID或车站编码",
                skill_name=self.name
            )

        # 构建消息
        if train_id:
            message = f"列车 {train_id} 时刻表查询完成，共{len(results.get('stops', []))}站"
        else:
            message = f"车站 {station_code} 时刻表查询完成，共{results.get('total_trains', 0)}列列车"

        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics=results,
            computation_time=0.0,
            success=True,
            message=message,
            skill_name=self.name
        )


class GetStationStatusSkill(BaseDispatchSkill):
    """
    车站状态查询技能
    查询车站的实时状态信息
    """

    name = "get_station_status"
    description = "查询车站的实时状态信息，包括到发线占用情况等"

    def __init__(self, scheduler: MIPScheduler):
        super().__init__(scheduler)

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay",
        **kwargs
    ) -> DispatchSkillOutput:
        """
        查询车站状态

        Args:
            train_ids: 列车ID列表
            station_codes: 车站编码列表
            delay_injection: 可包含额外参数
            optimization_objective: 优化目标
            **kwargs: 额外参数，如 station_code

        Returns:
            DispatchSkillOutput: 车站状态信息
        """
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

        # 查找车站
        station = None
        for s in self.scheduler.stations:
            if s.station_code == station_code:
                station = s
                break

        if not station:
            return DispatchSkillOutput(
                optimized_schedule={},
                delay_statistics={},
                computation_time=0.0,
                success=False,
                message=f"未找到车站 {station_code}",
                skill_name=self.name
            )

        # 统计在该车站停靠的列车
        trains_at_station = []
        for train in self.scheduler.trains:
            for stop in train.schedule.stops:
                if stop.station_code == station_code:
                    trains_at_station.append({
                        "train_id": train.train_id,
                        "arrival_time": stop.arrival_time,
                        "departure_time": stop.departure_time,
                        "is_stopped": stop.is_stopped
                    })
                    break

        # 模拟到发线占用（简化版本）
        track_count = station.track_count or 4
        occupied_tracks = min(len(trains_at_station), track_count)

        status_info = {
            "station_code": station.station_code,
            "station_name": station.station_name,
            "track_count": track_count,
            "occupied_tracks": occupied_tracks,
            "available_tracks": track_count - occupied_tracks,
            "trains_today": len(trains_at_station),
            "trains": trains_at_station[:10]  # 只返回前10列
        }

        message = f"车站 {station_code} 状态查询完成，到发线{occupied_tracks}/{track_count}占用"

        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics=status_info,
            computation_time=0.0,
            success=True,
            message=message,
            skill_name=self.name
        )


class AnalyzeCapacitySkill(BaseDispatchSkill):
    """
    运力分析技能
    分析区间的通过能力和冗余运力
    """

    name = "analyze_capacity"
    description = "分析区间的通过能力和冗余运力"

    def __init__(self, scheduler: MIPScheduler):
        super().__init__(scheduler)

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay",
        **kwargs
    ) -> DispatchSkillOutput:
        """
        分析运力

        Args:
            train_ids: 列车ID列表
            station_codes: 车站编码列表
            delay_injection: 可包含额外参数
            optimization_objective: 优化目标
            **kwargs: 额外参数，如 from_station, to_station, time_range

        Returns:
            DispatchSkillOutput: 运力分析结果
        """
        from_station = kwargs.get("from_station")
        to_station = kwargs.get("to_station")
        time_range = kwargs.get("time_range", "全天")

        if not from_station or not to_station:
            return DispatchSkillOutput(
                optimized_schedule={},
                delay_statistics={},
                computation_time=0.0,
                success=False,
                message="请提供起始站和终止站编码",
                skill_name=self.name
            )

        # 找到区间内的所有列车
        trains_in_section = []
        for train in self.scheduler.trains:
            train_stations = [s.station_code for s in train.schedule.stops]

            if from_station in train_stations and to_station in train_stations:
                from_idx = train_stations.index(from_station)
                to_idx = train_stations.index(to_station)

                if from_idx < to_idx:  # 确认方向正确
                    trains_in_section.append({
                        "train_id": train.train_id,
                        "from_station": from_station,
                        "to_station": to_station,
                        "stops_count": to_idx - from_idx + 1
                    })

        # 简化计算：假设追踪间隔3分钟，最大通过能力 = 60/3 = 20列/小时
        max_capacity_per_hour = 20
        current_trains = len(trains_in_section)

        # 估算可用运力（假设当前利用率70%）
        utilization_rate = 0.7
        available_capacity = int(max_capacity_per_hour * (1 - utilization_rate))

        capacity_info = {
            "from_station": from_station,
            "to_station": to_station,
            "time_range": time_range,
            "max_capacity_per_hour": max_capacity_per_hour,
            "current_trains": current_trains,
            "utilization_rate": utilization_rate,
            "available_capacity_per_hour": available_capacity,
            "trains_in_section": trains_in_section[:10],
            "total_trains_in_section": len(trains_in_section)
        }

        message = f"区间 {from_station}-{to_station} 运力分析完成，当前有{current_trains}列列车运行，利用率{utilization_rate*100:.0f}%"

        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics=capacity_info,
            computation_time=0.0,
            success=True,
            message=message,
            skill_name=self.name
        )


def create_skills(scheduler: MIPScheduler) -> Dict[str, BaseDispatchSkill]:
    """
    创建Skills工厂函数

    Args:
        scheduler: MIP调度器

    Returns:
        Dict[str, BaseDispatchSkill]: Skills字典
    """
    return {
        # 调度优化技能
        "temporary_speed_limit_skill": TemporarySpeedLimitSkill(scheduler),
        "sudden_failure_skill": SuddenFailureSkill(scheduler),
        "section_interrupt_skill": SectionInterruptSkill(scheduler),
        # 查询类技能
        "get_train_status": GetTrainStatusSkill(scheduler),
        "analyze_delay_propagation": AnalyzeDelayPropagationSkill(scheduler),
        "query_timetable": QueryTimetableSkill(scheduler),
        "get_station_status": GetStationStatusSkill(scheduler),
        "analyze_capacity": AnalyzeCapacitySkill(scheduler)
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


# 测试代码
if __name__ == "__main__":
    from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data

    # 使用真实数据
    use_real_data(True)
    trains = get_trains_pydantic()[:10]
    stations = get_stations_pydantic()

    # 创建调度器
    from solver.mip_scheduler import create_scheduler
    scheduler = create_scheduler(trains, stations)

    # 创建Skills
    skills = create_skills(scheduler)

    # 测试临时限速场景
    print("=== 测试临时限速场景 ===")
    first_train = trains[0].train_id if trains else "G1215"
    first_station = trains[0].schedule.stops[0].station_code if trains and trains[0].schedule.stops else "XSD"
    delay_injection = {
        "scenario_type": "temporary_speed_limit",
        "scenario_id": "TEST_001",
        "injected_delays": [
            {
                "train_id": first_train,
                "location": {"location_type": "station", "station_code": first_station},
                "initial_delay_seconds": 600,
                "timestamp": "2024-01-15T10:00:00Z"
            }
        ],
        "affected_trains": [first_train],
        "scenario_params": {
            "limit_speed_kmh": 200,
            "duration_minutes": 120,
            "affected_section": "TJG -> JNZ"
        }
    }

    # 使用真实数据的站点编码
    station_codes = [s.station_code for s in stations] if stations else ["XSD", "BDD", "DZD", "ZDJ", "SJP"]

    result = execute_skill(
        skill_name="temporary_speed_limit_skill",
        skills=skills,
        train_ids=[first_train],
        station_codes=station_codes,
        delay_injection=delay_injection
    )

    print(f"成功: {result.success}")
    print(f"消息: {result.message}")
    print(f"计算时间: {result.computation_time:.2f}秒")
    print(f"延误统计: {result.delay_statistics}")
