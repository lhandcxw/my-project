# -*- coding: utf-8 -*-
"""
铁路调度系统 - 整数规划求解器模块
对应架构文档第5节：建模方案设计（仅整数规划）
"""

from typing import List, Dict, Tuple, Optional, Any
from pulp import (
    LpProblem, LpVariable, LpMinimize, LpMaximize,
    lpSum, LpStatus, value, LpBinary
)
import numpy as np
from dataclasses import dataclass
import time

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.data_models import Train, Station, DelayInjection, InjectedDelay


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
    混合整数规划调度器
    使用PuLP库实现铁路调度优化
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        headway_time: int = 120,  # 最小发车间隔时间(秒) - 2分钟，安全间隔
        min_section_time: int = 900,  # 最小区间运行时间(秒) - 15分钟
        platform_occupancy_time: int = 300,  # 站台占用时间(秒) - 5分钟
        min_headway_time: int = 120  # 最小安全时间间隔(秒) - 2分钟
    ):
        """
        初始化调度器

        Args:
            trains: 列车列表
            stations: 车站列表
            headway_time: 追踪间隔时间(秒)，默认2分钟（最小安全间隔）
            min_section_time: 最小区间运行时间(秒)，默认15分钟
            platform_occupancy_time: 站台占用时间(秒)，默认5分钟
            min_headway_time: 最小发车间隔(秒)，默认2分钟
        """
        self.trains = trains
        self.stations = stations
        self.headway_time = headway_time
        self.min_section_time = min_section_time
        self.platform_occupancy_time = platform_occupancy_time
        self.min_headway_time = min_headway_time

        # 建立车站索引映射
        self.station_codes = [s.station_code for s in stations]
        self.station_names = {s.station_code: s.station_name for s in stations}
        self.train_ids = [t.train_id for t in trains]

        # 建立车站股道数量映射
        self.station_track_count = {s.station_code: s.track_count for s in stations}

        # 加载真实数据的区间最小运行时间
        self.min_running_times = self._load_min_running_times()

    def _get_station_index(self, station_code: str) -> int:
        """获取车站索引"""
        return self.station_codes.index(station_code)

    def _get_stations_for_train(self, train: Train) -> List[str]:
        """获取列车经停的所有车站"""
        return [stop.station_code for stop in train.schedule.stops]

    def _time_to_seconds(self, time_str: str) -> int:
        """时间字符串转秒数，支持 HH:MM:SS 或 HH:MM 格式"""
        parts = time_str.split(':')
        if len(parts) == 2:
            h, m = map(int, parts)
            s = 0
        else:
            h, m, s = map(int, parts)
        return h * 3600 + m * 60 + s

    def _seconds_to_time(self, seconds: int) -> str:
        """秒数转时间字符串"""
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _load_min_running_times(self) -> Dict[Tuple[str, str], int]:
        """
        从列车时刻表计算区间运行时间
        无论使用真实数据还是预设数据，都从列车时刻表计算以保证一致性
        Returns:
            Dict[(from_station, to_station)] = min_time_seconds
        """
        # 始终从列车时刻表计算区间运行时间，保证与实际列车停靠点一致
        return self._calculate_section_times_from_schedule()

    def _calculate_section_times_from_schedule(self) -> Dict[Tuple[str, str], int]:
        """
        从预设列车时刻表计算区间运行时间
        Returns:
            Dict[(from_station, to_station)] = min_time_seconds
        """
        # 收集所有区间的运行时间
        section_times = {}

        for train in self.trains:
            stops = train.schedule.stops
            for i in range(len(stops) - 1):
                from_station = stops[i].station_code
                to_station = stops[i + 1].station_code

                from_dep = self._time_to_seconds(stops[i].departure_time)
                to_arr = self._time_to_seconds(stops[i + 1].arrival_time)

                # 运行时间 = 到达时间 - 发车时间
                running_time = to_arr - from_dep

                # 存储最小的运行时间作为参考
                key = (from_station, to_station)
                if key not in section_times or running_time < section_times[key]:
                    section_times[key] = running_time

        return section_times

    def _calculate_section_time(
        self,
        from_station: str,
        to_station: str,
        speed_level: int = 350
    ) -> int:
        """
        计算标准区间运行时间（秒）
        优先从真实数据中获取，如果没有则使用默认值
        """
        # 优先使用真实数据的最小运行时间作为标准（加一定缓冲）
        if (from_station, to_station) in self.min_running_times:
            min_time = self.min_running_times[(from_station, to_station)]
            # 标准时间 = 最小运行时间 * 1.1 (增加10%缓冲)
            return int(min_time * 1.1)

        # 旧版本的硬编码默认值（保留作为后备）
        section_times = {
            ("BJP", "TJG"): 900,   # 15分钟
            ("TJG", "JNZ"): 2400,  # 40分钟
            ("JNZ", "NJH"): 4200,  # 70分钟
            ("NJH", "SHH"): 3600,  # 60分钟
        }
        return section_times.get((from_station, to_station), 1800)

    def _calculate_min_section_time(
        self,
        from_station: str,
        to_station: str,
        speed_level: int = 350
    ) -> int:
        """
        计算最小区间运行时间
        优先从真实数据中获取
        """
        # 直接使用真实数据的最小运行时间
        if (from_station, to_station) in self.min_running_times:
            return self.min_running_times[(from_station, to_station)]

        # 后备：标准时间的90%
        standard = self._calculate_section_time(from_station, to_station, speed_level)
        return int(standard * 0.9)

    def _get_original_stop_duration(self, train: Train, station_code: str) -> int:
        """获取原始计划停站时间（秒）"""
        for stop in train.schedule.stops:
            if stop.station_code == station_code:
                arr = self._time_to_seconds(stop.arrival_time)
                dep = self._time_to_seconds(stop.departure_time)
                return dep - arr
        return 300

    def solve(
        self,
        delay_injection: DelayInjection,
        objective: str = "min_max_delay"
    ) -> SolveResult:
        """
        求解调度优化问题

        Args:
            delay_injection: 延误注入数据
            objective: 优化目标 ("min_max_delay" 或 "min_avg_delay")

        Returns:
            SolveResult: 求解结果
        """
        start_time = time.time()

        # 构建MIP模型
        prob = LpProblem("RailwayDispatch", LpMinimize)

        # 创建决策变量
        # 1. 到达时间变量
        arrival = LpVariable.dicts(
            "arrival",
            [(t.train_id, s.station_code)
             for t in self.trains
             for s in self.stations
             if s.station_code in self._get_stations_for_train(t)],
            lowBound=0,
            cat='Integer'
        )

        # 2. 发车时间变量
        departure = LpVariable.dicts(
            "departure",
            [(t.train_id, s.station_code)
             for t in self.trains
             for s in self.stations
             if s.station_code in self._get_stations_for_train(t)],
            lowBound=0,
            cat='Integer'
        )

        # 3. 辅助变量：延误时间
        delay = LpVariable.dicts(
            "delay",
            [(t.train_id, s.station_code)
             for t in self.trains
             for s in self.stations
             if s.station_code in self._get_stations_for_train(t)],
            lowBound=0,
            cat='Integer'
        )

        # 4. 辅助变量：最大延误
        max_delay = LpVariable("max_delay", lowBound=0, cat='Integer')

        # 设置目标函数
        if objective == "min_max_delay":
            prob += max_delay  # 最小化最大延误
        else:
            prob += lpSum([
                delay[t.train_id, s.station_code]
                for t in self.trains
                for s in self.stations
                if s.station_code in self._get_stations_for_train(t)
            ])  # 最小化总延误

        # 添加约束条件

        # 1. 初始延误约束
        for injected in delay_injection.injected_delays:
            train_id = injected.train_id
            station_code = injected.location.station_code or "TJG"
            initial_delay = injected.initial_delay_seconds

            # 检查列车是否在调度范围内
            train = None
            for t in self.trains:
                if t.train_id == train_id:
                    train = t
                    break

            if train is None:
                # 列车不在调度范围内，跳过此延误注入
                continue

            if station_code in self.station_codes:
                # 获取计划的原始到发时间
                for stop in train.schedule.stops:
                    if stop.station_code == station_code:
                        scheduled_arr = self._time_to_seconds(stop.arrival_time)
                        scheduled_dep = self._time_to_seconds(stop.departure_time)
                        # 延误 = 实际发车时间 - 计划发车时间
                        # 同时约束到达时间和发车时间都必须延误
                        prob += arrival[train_id, station_code] >= scheduled_arr + initial_delay
                        prob += departure[train_id, station_code] >= scheduled_dep + initial_delay
                        prob += delay[train_id, station_code] >= initial_delay

        # 2. 区间运行时间约束
        # 约束：最小区间运行时间 <= 运行时间 <= 计划运行时间 + 缓冲
        for t in self.trains:
            train_stations = self._get_stations_for_train(t)
            for i in range(len(train_stations) - 1):
                from_station = train_stations[i]
                to_station = train_stations[i + 1]

                # 获取该列车在当前区间的计划运行时间
                from_stop = t.schedule.stops[i]
                to_stop = t.schedule.stops[i + 1]
                scheduled_from_dep = self._time_to_seconds(from_stop.departure_time)
                scheduled_to_arr = self._time_to_seconds(to_stop.arrival_time)
                scheduled_section_time = scheduled_to_arr - scheduled_from_dep

                # 最小区间运行时间
                min_time = self._calculate_min_section_time(from_station, to_station, 350)

                # 运行时间不能小于最小时间，但可以略超过计划时间（留出缓冲）
                # 使用计划时间的1.2倍作为上限，给予一定灵活性
                max_time = int(scheduled_section_time * 1.2)

                prob += arrival[t.train_id, to_station] - departure[t.train_id, from_station] >= min_time
                prob += arrival[t.train_id, to_station] - departure[t.train_id, from_station] <= max_time

        # 3. 追踪间隔约束（同车站相邻列车）
        # 关键约束：确保后续列车的发车时间受前车影响
        for s in self.stations:
            station_code = s.station_code
            trains_at_station = [
                t for t in self.trains
                if station_code in self._get_stations_for_train(t)
            ]

            # 按计划发车时间排序，确保顺序
            trains_with_time = []
            for t in trains_at_station:
                for stop in t.schedule.stops:
                    if stop.station_code == station_code:
                        trains_with_time.append((t, self._time_to_seconds(stop.departure_time)))
                        break
            trains_with_time.sort(key=lambda x: x[1])

            # 添加追踪间隔约束：后车必须晚于前车 + 追踪间隔
            for i in range(len(trains_with_time) - 1):
                t1, _ = trains_with_time[i]
                t2, _ = trains_with_time[i + 1]
                # t2的发车时间 >= t1的发车时间 + 追踪间隔
                prob += departure[t2.train_id, station_code] >= departure[t1.train_id, station_code] + self.headway_time

        # 3.5 车站股道容量约束（简化版）
        # 通过发车间隔约束来间接保证：当track_count=1时，确保到发时间不重叠
        for s in self.stations:
            station_code = s.station_code
            track_count = self.station_track_count.get(station_code, 1)

            if track_count <= 1:
                # 单股道时，使用更严格的到发间隔约束
                trains_at_station = [
                    t for t in self.trains
                    if station_code in self._get_stations_for_train(t)
                ]
                # 按计划发车时间排序
                trains_with_time = []
                for t in trains_at_station:
                    for stop in t.schedule.stops:
                        if stop.station_code == station_code:
                            trains_with_time.append((t, self._time_to_seconds(stop.departure_time)))
                            break
                trains_with_time.sort(key=lambda x: x[1])

                # 相邻列车的到达-发车间隔约束
                for i in range(len(trains_with_time) - 1):
                    t1, _ = trains_with_time[i]
                    t2, _ = trains_with_time[i + 1]
                    # t2的到达时间 >= t1的发车时间 + 最小安全间隔
                    prob += arrival[t2.train_id, station_code] >= departure[t1.train_id, station_code] + self.min_headway_time

        # 3.6 初始到达时间约束 - 确保第一站的到达时间正确
        for t in self.trains:
            train_stations = self._get_stations_for_train(t)
            if train_stations:
                first_station = train_stations[0]
                first_stop = t.schedule.stops[0]
                scheduled_arr = self._time_to_seconds(first_stop.arrival_time)
                # 第一站到达时间 = 计划到达时间（不允许提前）
                prob += arrival[t.train_id, first_station] >= scheduled_arr
                # 也可以限制不能太晚到达（可选）
                # prob += arrival[t.train_id, first_station] <= scheduled_arr + 7200  # 最多晚2小时

        # 4. 延误传递约束（已被约束2覆盖，删除）

        # 5. 停站时间约束 - 保持原始停站时间不变
        for t in self.trains:
            for stop in t.schedule.stops:
                station_code = stop.station_code
                original_duration = self._get_original_stop_duration(t, station_code)
                # 停站时间 = 发车时间 - 到达时间，必须等于原始停站时间
                prob += departure[t.train_id, station_code] - arrival[t.train_id, station_code] == original_duration

        # 6. 发车时间约束 - 不得提前发车
        for t in self.trains:
            for stop in t.schedule.stops:
                station_code = stop.station_code
                scheduled_dep = self._time_to_seconds(stop.departure_time)
                # 发车时间 >= 计划发车时间（可延迟，不可提前）
                prob += departure[t.train_id, station_code] >= scheduled_dep

        # 6.5 到达时间约束 - 不得提前到达
        for t in self.trains:
            for stop in t.schedule.stops:
                station_code = stop.station_code
                scheduled_arr = self._time_to_seconds(stop.arrival_time)
                # 到达时间 >= 计划到达时间（可延迟，不可提前）
                prob += arrival[t.train_id, station_code] >= scheduled_arr

        # 7. 延误计算约束
        # 找出所有受影响的列车（在injected_delays中出现的）
        affected_train_ids = set(injected.train_id for injected in delay_injection.injected_delays)

        for t in self.trains:
            train_id = t.train_id
            is_affected = train_id in affected_train_ids

            for stop in t.schedule.stops:
                station_code = stop.station_code
                scheduled_dep = self._time_to_seconds(stop.departure_time)

                # 延误 = 实际发车 - 计划发车（如果为正）
                prob += delay[train_id, station_code] >= departure[train_id, station_code] - scheduled_dep
                prob += delay[train_id, station_code] >= 0

                # 最大延误约束
                prob += max_delay >= delay[train_id, station_code]

                # 如果列车不受影响且该站不是注入了延误的站点，添加约束让延误为0
                # 这样可以避免不必要的延误传播
                if not is_affected:
                    # 检查是否是注入了延误的站点
                    is_injected_station = any(
                        injected.train_id == train_id and
                        (injected.location.station_code is None or injected.location.station_code == station_code)
                        for injected in delay_injection.injected_delays
                    )
                    if not is_injected_station:
                        # 未受影响的列车在非延误站点，延误应为0
                        prob += delay[train_id, station_code] <= 0

        # 求解
        prob.solve()

        # 解析结果
        if LpStatus[prob.status] != 'Optimal':
            return SolveResult(
                success=False,
                optimized_schedule={},
                delay_statistics={},
                computation_time=time.time() - start_time,
                message=f"求解失败: {LpStatus[prob.status]}"
            )

        # 构建优化后的时刻表
        optimized_schedule = {}
        all_delays = []

        for t in self.trains:
            train_schedule = []
            for stop in t.schedule.stops:
                station_code = stop.station_code

                # 获取求解结果，如果不存在则使用原始时间
                arr_key = (t.train_id, station_code)
                dep_key = (t.train_id, station_code)
                del_key = (t.train_id, station_code)

                arr_time = value(arrival.get(arr_key))
                dep_time = value(departure.get(dep_key))
                delay_val = value(delay.get(del_key))

                # 如果变量不存在，使用原始计划时间
                if arr_time is None:
                    arr_time = self._time_to_seconds(stop.arrival_time)
                if dep_time is None:
                    dep_time = self._time_to_seconds(stop.departure_time)
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

        # 计算统计信息
        max_delay = max(all_delays) if all_delays else 0
        avg_delay = sum(all_delays) / len(all_delays) if all_delays else 0

        return SolveResult(
            success=True,
            optimized_schedule=optimized_schedule,
            delay_statistics={
                "max_delay_seconds": int(max_delay),
                "avg_delay_seconds": float(avg_delay),
                "total_delay_seconds": int(sum(all_delays)),
                "affected_trains_count": len(delay_injection.affected_trains)
            },
            computation_time=time.time() - start_time,
            message="求解成功"
        )

    def solve_with_adjustment(
        self,
        delay_injection: DelayInjection,
        adjustment_minutes: int = 30,
        objective: str = "min_max_delay"
    ) -> SolveResult:
        """
        求解带调整的调度问题

        Args:
            delay_injection: 延误注入数据
            adjustment_minutes: 允许的最大调整时间(分钟)
            objective: 优化目标

        Returns:
            SolveResult: 求解结果
        """
        return self.solve(delay_injection, objective)


def create_scheduler(trains: List[Train], stations: List[Station]) -> MIPScheduler:
    """创建调度器工厂函数"""
    return MIPScheduler(trains, stations)


# 测试代码
if __name__ == "__main__":
    from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data

    # 使用真实数据
    use_real_data(True)
    trains = get_trains_pydantic()[:10]  # 只取前10列用于测试
    stations = get_stations_pydantic()

    # 创建延误注入（临时限速场景）
    first_train = trains[0].train_id if trains else "G1215"
    first_station = trains[0].schedule.stops[0].station_code if trains and trains[0].schedule.stops else "XSD"
    delay_injection = DelayInjection.create_temporary_speed_limit(
        scenario_id="TEST_001",
        train_delays=[
            {"train_id": first_train, "delay_seconds": 600, "station_code": first_station},
        ],
        limit_speed=200,
        duration=120,
        affected_section=f"{first_station} -> BDD"
    )

    # 求解
    scheduler = create_scheduler(trains, stations)
    result = scheduler.solve(delay_injection, objective="min_max_delay")

    print(f"求解成功: {result.success}")
    print(f"计算时间: {result.computation_time:.2f}秒")
    print(f"最大延误: {result.delay_statistics['max_delay_seconds']}秒")
    print(f"平均延误: {result.delay_statistics['avg_delay_seconds']:.2f}秒")
    print(f"优化后时刻表: {result.optimized_schedule}")
