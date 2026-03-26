# -*- coding: utf-8 -*-
"""
铁路调度系统 - 整数规划求解器模块（修正版）
解决了原版中的约束冲突问题，符合实际高铁运营规则
"""

from typing import List, Dict, Tuple, Optional, Any
from pulp import (
    LpProblem, LpVariable, LpMinimize,
    lpSum, LpStatus, value
)
from dataclasses import dataclass
import time
import logging

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.data_models import Train, Station, DelayInjection

logger = logging.getLogger(__name__)


@dataclass
class SolveResult:
    """求解结果数据类"""
    success: bool
    optimized_schedule: Dict[str, List[Dict]]
    delay_statistics: Dict[str, Any]
    computation_time: float
    message: str = ""


class MIPScheduler:
    """
    修正版混合整数规划调度器
    主要修正:
    1. 取消区间运行时间上界，允许延误传播
    2. 只对非受影响列车的第一站约束延误为0
    3. 允许停站时间在合理范围内调整
    4. 追踪间隔调整为3分钟（符合高铁标准）
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        headway_time: int = 180,  # 追踪间隔 - 3分钟（高铁设计标准）
        min_stop_time: int = 60,   # 最小停站时间 - 1分钟
        min_headway_time: int = 180  # 最小安全间隔 - 3分钟
    ):
        self.trains = trains
        self.stations = stations
        self.headway_time = headway_time
        self.min_stop_time = min_stop_time
        self.min_headway_time = min_headway_time

        self.station_codes = [s.station_code for s in stations]
        self.station_names = {s.station_code: s.station_name for s in stations}
        self.train_ids = [t.train_id for t in trains]
        self.station_track_count = {s.station_code: s.track_count for s in stations}
        self.min_running_times = self._load_min_running_times()

    def _get_stations_for_train(self, train: Train) -> List[str]:
        return [stop.station_code for stop in train.schedule.stops]

    def _time_to_seconds(self, time_str: str) -> int:
        parts = time_str.split(':')
        if len(parts) == 2:
            h, m = map(int, parts)
            s = 0
        else:
            h, m, s = map(int, parts)
        return h * 3600 + m * 60 + s

    def _seconds_to_time(self, seconds: int) -> str:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _load_min_running_times(self) -> Dict[Tuple[str, str], int]:
        section_times = {}
        for train in self.trains:
            stops = train.schedule.stops
            for i in range(len(stops) - 1):
                from_station = stops[i].station_code
                to_station = stops[i + 1].station_code
                from_dep = self._time_to_seconds(stops[i].departure_time)
                to_arr = self._time_to_seconds(stops[i + 1].arrival_time)
                running_time = to_arr - from_dep
                key = (from_station, to_station)
                if key not in section_times or running_time < section_times[key]:
                    section_times[key] = running_time
        return section_times

    def _get_min_section_time(self, from_station: str, to_station: str) -> int:
        key = (from_station, to_station)
        return self.min_running_times.get(key, 600)

    def _get_original_stop_duration(self, train: Train, station_code: str) -> int:
        for stop in train.schedule.stops:
            if stop.station_code == station_code:
                arr = self._time_to_seconds(stop.arrival_time)
                dep = self._time_to_seconds(stop.departure_time)
                return dep - arr
        return 180

    def solve(self, delay_injection: DelayInjection, objective: str = "min_max_delay") -> SolveResult:
        """求解调度优化问题"""
        start_time = time.time()
        prob = LpProblem("RailwayDispatch", LpMinimize)

        # 决策变量
        arrival = LpVariable.dicts(
            "arrival",
            [(t.train_id, s.station_code)
             for t in self.trains
             for s in self.stations
             if s.station_code in self._get_stations_for_train(t)],
            lowBound=0, cat='Integer'
        )

        departure = LpVariable.dicts(
            "departure",
            [(t.train_id, s.station_code)
             for t in self.trains
             for s in self.stations
             if s.station_code in self._get_stations_for_train(t)],
            lowBound=0, cat='Integer'
        )

        delay = LpVariable.dicts(
            "delay",
            [(t.train_id, s.station_code)
             for t in self.trains
             for s in self.stations
             if s.station_code in self._get_stations_for_train(t)],
            lowBound=0, cat='Integer'
        )

        max_delay = LpVariable("max_delay", lowBound=0, cat='Integer')

        # 目标函数
        if objective == "min_max_delay":
            prob += max_delay
        else:
            prob += lpSum([
                delay[t.train_id, s.station_code]
                for t in self.trains
                for s in self.stations
                if s.station_code in self._get_stations_for_train(t)
            ])

        # =========================================
        # 约束条件（修正版）
        # =========================================

        # 收集受影响的列车信息
        affected_trains = set()
        injected_stations = {}  # {(train_id, station_code): initial_delay}

        # 1. 初始延误约束（修正：只约束发车时间）
        for injected in delay_injection.injected_delays:
            train_id = injected.train_id
            station_code = injected.location.station_code or "BJX"
            initial_delay = injected.initial_delay_seconds

            train = next((t for t in self.trains if t.train_id == train_id), None)
            if train is None:
                continue

            affected_trains.add(train_id)
            injected_stations[(train_id, station_code)] = initial_delay

            if station_code in self.station_codes:
                for stop in train.schedule.stops:
                    if stop.station_code == station_code:
                        scheduled_dep = self._time_to_seconds(stop.departure_time)
                        # 只约束发车时间 >= 计划 + 初始延误
                        prob += departure[train_id, station_code] >= scheduled_dep + initial_delay
                        prob += delay[train_id, station_code] >= initial_delay
                        break

        # 2. 区间运行时间约束（修正：取消上界）
        for t in self.trains:
            train_stations = self._get_stations_for_train(t)
            for i in range(len(train_stations) - 1):
                from_station = train_stations[i]
                to_station = train_stations[i + 1]
                min_time = self._get_min_section_time(from_station, to_station)
                # 只约束下界，允许区间内降速等待
                prob += arrival[t.train_id, to_station] - departure[t.train_id, from_station] >= min_time

        # 3. 追踪间隔约束
        for s in self.stations:
            station_code = s.station_code
            trains_at_station = [t for t in self.trains if station_code in self._get_stations_for_train(t)]

            trains_with_time = []
            for t in trains_at_station:
                for stop in t.schedule.stops:
                    if stop.station_code == station_code:
                        trains_with_time.append((t, self._time_to_seconds(stop.departure_time)))
                        break
            trains_with_time.sort(key=lambda x: x[1])

            for i in range(len(trains_with_time) - 1):
                t1, _ = trains_with_time[i]
                t2, _ = trains_with_time[i + 1]
                prob += departure[t2.train_id, station_code] >= departure[t1.train_id, station_code] + self.headway_time

        # 4. 股道容量约束
        for s in self.stations:
            station_code = s.station_code
            if self.station_track_count.get(station_code, 1) <= 1:
                trains_at_station = [t for t in self.trains if station_code in self._get_stations_for_train(t)]
                trains_with_time = []
                for t in trains_at_station:
                    for stop in t.schedule.stops:
                        if stop.station_code == station_code:
                            trains_with_time.append((t, self._time_to_seconds(stop.departure_time)))
                            break
                trains_with_time.sort(key=lambda x: x[1])

                for i in range(len(trains_with_time) - 1):
                    t1, _ = trains_with_time[i]
                    t2, _ = trains_with_time[i + 1]
                    prob += arrival[t2.train_id, station_code] >= departure[t1.train_id, station_code] + self.min_headway_time

        # 5. 第一站到达时间约束（修正：允许受影响列车在注入站延误）
        for t in self.trains:
            train_stations = self._get_stations_for_train(t)
            if train_stations:
                first_station = train_stations[0]
                first_stop = t.schedule.stops[0]
                scheduled_arr = self._time_to_seconds(first_stop.arrival_time)
                # 如果第一站不是延误注入站，才约束
                if (t.train_id, first_station) not in injected_stations:
                    prob += arrival[t.train_id, first_station] >= scheduled_arr

        # 6. 停站时间约束（修正：通过站允许0停站时间）
        for t in self.trains:
            for stop in t.schedule.stops:
                station_code = stop.station_code
                original_duration = self._get_original_stop_duration(t, station_code)

                # 通过站（原计划停站时间为0）允许0停站时间
                if original_duration == 0:
                    # 通过站：到达时间等于发车时间（允许0停站）
                    prob += departure[t.train_id, station_code] >= arrival[t.train_id, station_code]
                else:
                    # 停车站：最小停站时间为原计划的50%或1分钟
                    min_stop = max(self.min_stop_time, original_duration // 2)
                    prob += departure[t.train_id, station_code] - arrival[t.train_id, station_code] >= min_stop

        # 7. 发车时间约束（不得提前）
        for t in self.trains:
            for stop in t.schedule.stops:
                station_code = stop.station_code
                scheduled_dep = self._time_to_seconds(stop.departure_time)
                prob += departure[t.train_id, station_code] >= scheduled_dep

        # 8. 到达时间约束（不得提前）
        for t in self.trains:
            for stop in t.schedule.stops:
                station_code = stop.station_code
                scheduled_arr = self._time_to_seconds(stop.arrival_time)
                prob += arrival[t.train_id, station_code] >= scheduled_arr

        # 9. 延误计算约束（修正：允许延误传播）
        for t in self.trains:
            train_id = t.train_id
            is_affected = train_id in affected_trains

            for stop in t.schedule.stops:
                station_code = stop.station_code
                scheduled_dep = self._time_to_seconds(stop.departure_time)

                prob += delay[train_id, station_code] >= departure[train_id, station_code] - scheduled_dep
                prob += delay[train_id, station_code] >= 0
                prob += max_delay >= delay[train_id, station_code]

                # 修正：只对非受影响列车的第一站约束延误为0
                if not is_affected:
                    train_stations = self._get_stations_for_train(t)
                    if train_stations and station_code == train_stations[0]:
                        prob += delay[train_id, station_code] <= 0

        # 求解
        prob.solve()

        if LpStatus[prob.status] != 'Optimal':
            return SolveResult(
                success=False,
                optimized_schedule={},
                delay_statistics={},
                computation_time=time.time() - start_time,
                message=f"求解失败: {LpStatus[prob.status]}"
            )

        # 解析结果
        optimized_schedule = {}
        all_delays = []

        for t in self.trains:
            train_schedule = []
            for stop in t.schedule.stops:
                station_code = stop.station_code
                arr_key = (t.train_id, station_code)
                dep_key = (t.train_id, station_code)
                del_key = (t.train_id, station_code)

                arr_time = value(arrival.get(arr_key))
                dep_time = value(departure.get(dep_key))
                delay_val = value(delay.get(del_key))

<<<<<<< HEAD
# 如果变量不存在，使用原始计划时间
                if arr_time is None or dep_time is None:
                    return SolveResult(
                        success=False,
                        optimized_schedule={},
                        delay_statistics={},
                        computation_time=time.time() - start_time,
                        message=f"求解变量缺失：列车{t.train_id}在站{station_code}"
                    )
=======
                if arr_time is None:
                    arr_time = self._time_to_seconds(stop.arrival_time)
                if dep_time is None:
                    dep_time = self._time_to_seconds(stop.departure_time)
>>>>>>> 860e9e3d031984e9febd5d412e3f4e6a48ca0914
                if delay_val is None:
                    delay_val = 0

                all_delays.append(delay_val)

                train_schedule.append({
                    "station_code": station_code,
                    "station_name": self.station_names.get(station_code, station_code),
                    "arrival_time": self._seconds_to_time(int(arr_time)),
                    "departure_time": self._seconds_to_time(int(dep_time)),
                    "original_arrival": stop.arrival_time,
                    "original_departure": stop.departure_time,
                    "delay_seconds": int(delay_val)
                })

            optimized_schedule[t.train_id] = train_schedule

        max_delay_val = max(all_delays) if all_delays else 0
        avg_delay = sum(all_delays) / len(all_delays) if all_delays else 0

        return SolveResult(
            success=True,
            optimized_schedule=optimized_schedule,
            delay_statistics={
                "max_delay_seconds": int(max_delay_val),
                "avg_delay_seconds": float(avg_delay),
                "total_delay_seconds": int(sum(all_delays)),
                "affected_trains_count": len(affected_trains)
            },
            computation_time=time.time() - start_time,
            message="求解成功"
        )

    def solve_with_adjustment(self, delay_injection: DelayInjection, adjustment_minutes: int = 30, objective: str = "min_max_delay") -> SolveResult:
        return self.solve(delay_injection, objective)


def create_scheduler(trains: List[Train], stations: List[Station]) -> MIPScheduler:
    return MIPScheduler(trains, stations)


if __name__ == "__main__":
    from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
    from models.data_models import InjectedDelay, DelayLocation, ScenarioType

    use_real_data(True)
    trains = get_trains_pydantic()[:30]
    stations = get_stations_pydantic()

    print("=" * 60)
    print("修正版MIP调度器测试")
    print("=" * 60)

    scheduler = create_scheduler(trains, stations)

    # 测试: G1563在保定东延误20分钟
    delay_injection = DelayInjection(
        scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
        scenario_id="TEST",
        injected_delays=[
            InjectedDelay(
                train_id="G1563",
                location=DelayLocation(location_type="station", station_code="BDD"),
                initial_delay_seconds=1200,
                timestamp="2024-01-15T10:00:00Z"
            )
        ],
        affected_trains=["G1563"]
    )

    result = scheduler.solve(delay_injection, "min_max_delay")
    print(f"\n测试 - G1563在保定东延误20分钟:")
    print(f"  求解状态: {result.message}")
    if result.success:
        print(f"  最大延误: {result.delay_statistics['max_delay_seconds']//60} 分钟")
        print(f"  计算时间: {result.computation_time:.2f}秒")
