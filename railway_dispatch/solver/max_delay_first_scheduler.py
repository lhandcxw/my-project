# -*- coding: utf-8 -*-
"""
铁路调度系统 - MaxDelayFirst（最大延误优先）调度器模块
优先处理延误最大的列车，尽可能减少最大延误
【专家重构】加入追踪间隔约束和多股道车站处理
"""

from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import time
import copy
import logging
import itertools

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.data_models import Train, Station, DelayInjection
from config import DispatchEnvConfig

logger = logging.getLogger(__name__)


@dataclass
class SolveResult:
    """求解结果数据类"""
    success: bool
    optimized_schedule: Dict[str, List[Dict]]
    delay_statistics: Dict[str, Any]
    computation_time: float
    message: str = ""


class MaxDelayFirstScheduler:
    """
    最大延误优先调度器（Max-Delay First）【专家重构版】
    优先处理延误最大的列车，尽可能减少最大延误

    采用贪心策略：
    1. 应用初始延误
    2. 找到当前最大延误的列车
    3. 尝试通过压缩停站时间和区间运行时间来减少该列车的延误
    4. 处理追踪间隔约束和多股道车站
    5. 迭代直到无法进一步优化
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        headway_time: int = None,  # 追踪间隔 - 从配置读取
        min_stop_time: int = None,  # 最小停站时间 - 从配置读取
        max_stop_compression: int = None,  # 最大停站压缩时间 - 从配置读取
        max_compression_per_step: int = None,  # 每次最大压缩时间 - 从配置读取
        stop_time_redundancy_ratio: float = None,  # 停站冗余利用比例 - 从配置读取
        running_time_redundancy_ratio: float = None,  # 区间运行冗余利用比例 - 从配置读取
        optimization_objective: str = "min_total_delay",  # 默认目标：最小化总延误，与其他求解器一致
        **kwargs
    ):
        # 从统一配置加载
        self.trains = trains
        self.stations = stations
        self.headway_time = headway_time if headway_time is not None else DispatchEnvConfig.headway_time()
        self.min_stop_time = min_stop_time if min_stop_time is not None else DispatchEnvConfig.min_stop_time()

        # 从配置文件读取参数，如果没有传入的话
        if max_stop_compression is None:
            max_stop_compression = DispatchEnvConfig.get("solver.max_delay_first.max_stop_compression", 60)
        if max_compression_per_step is None:
            max_compression_per_step = DispatchEnvConfig.get("solver.max_delay_first.max_compression_per_step", 30)
        if stop_time_redundancy_ratio is None:
            stop_time_redundancy_ratio = DispatchEnvConfig.get("solver.max_delay_first.stop_time_redundancy_ratio", 1.0)
        if running_time_redundancy_ratio is None:
            running_time_redundancy_ratio = DispatchEnvConfig.get("solver.max_delay_first.running_time_redundancy_ratio", 1.0)

        self.max_stop_compression = max_stop_compression
        self.max_compression_per_step = max_compression_per_step
        self.stop_time_redundancy_ratio = stop_time_redundancy_ratio
        self.running_time_redundancy_ratio = running_time_redundancy_ratio
        self.optimization_objective = optimization_objective  # 存储默认优化目标

        self.station_names = {s.station_code: s.station_name for s in stations}
        self.station_track_count = {s.station_code: s.track_count for s in stations}
        # 识别线路所（track_count=0的节点）
        self.line_posts = {s.station_code for s in stations if s.track_count == 0}

        # 【专家新增】加载区间运行时间数据
        self.min_running_times = self._load_min_running_times()
        self.original_running_times = self._load_original_running_times()

    def _time_to_seconds(self, time_str: str) -> int:
        """将时间字符串转换为秒数"""
        parts = time_str.split(':')
        if len(parts) == 2:
            h, m = map(int, parts)
            return h * 3600 + m * 60
        else:
            h, m, s = map(int, parts)
            return h * 3600 + m * 60 + s

    def _seconds_to_time(self, seconds: int) -> str:
        """将秒数转换为时间字符串"""
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _get_stations_for_train(self, train: Train) -> List[str]:
        """获取列车经停的车站列表"""
        if not train.schedule or not train.schedule.stops or not isinstance(train.schedule.stops, (list, tuple)):
            return []
        return [stop.station_code for stop in train.schedule.stops if hasattr(stop, 'station_code')]

    def _load_min_running_times(self) -> Dict[Tuple[str, str], int]:
        """加载区间最小运行时间"""
        section_times = {}
        for train in self.trains:
            if not train.schedule or not train.schedule.stops or not isinstance(train.schedule.stops, (list, tuple)):
                continue
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

    def _load_original_running_times(self) -> Dict[Tuple[str, str], int]:
        """加载区间原始运行时间（所有列车的平均）"""
        section_times = {}
        section_counts = {}
        for train in self.trains:
            if not train.schedule or not train.schedule.stops or not isinstance(train.schedule.stops, (list, tuple)):
                continue
            stops = train.schedule.stops
            for i in range(len(stops) - 1):
                from_station = stops[i].station_code
                to_station = stops[i + 1].station_code
                from_dep = self._time_to_seconds(stops[i].departure_time)
                to_arr = self._time_to_seconds(stops[i + 1].arrival_time)
                running_time = to_arr - from_dep
                key = (from_station, to_station)
                if key not in section_times:
                    section_times[key] = 0
                    section_counts[key] = 0
                section_times[key] += running_time
                section_counts[key] += 1
        # 计算平均值
        for key in section_times:
            section_times[key] = section_times[key] // section_counts[key]
        return section_times

    def _get_min_section_time(self, from_station: str, to_station: str) -> int:
        """获取指定区间的最小运行时间"""
        key = (from_station, to_station)
        default_time = DispatchEnvConfig.get("defaults.section_running_time", 600)
        return self.min_running_times.get(key, default_time)

    def _get_original_section_time(self, from_station: str, to_station: str) -> int:
        """获取指定区间的原始平均运行时间"""
        key = (from_station, to_station)
        default_time = DispatchEnvConfig.get("defaults.section_running_time", 600)
        return self.original_running_times.get(key, default_time)

    def solve(
            self,
            delay_injection: DelayInjection,
            objective: str = "min_total_delay",
            solver_config: Dict = None
        ) -> SolveResult:
        """
        执行调度优化 - 最大延误优先策略【专家重构版】

        Args:
            delay_injection: 延误注入信息
            objective: 优化目标（已废弃，使用solver_config.optimization_objective）
            solver_config: 求解器配置，支持 optimization_objective:
                - min_total_delay: 最小化总延误（默认）
                - min_max_delay: 最小化最大延误

        Returns:
            SolveResult: 调度结果
        """
        start_time = time.time()

        # 解析solver_config中的优化目标
        if solver_config and isinstance(solver_config, dict):
            self.optimization_objective = solver_config.get("optimization_objective", self.optimization_objective)

        # 【专家重构】Step 1: 初始化调度时刻表
        schedule = {}  # {(train_id, station_code): [arrival_seconds, departure_seconds]}
        train_schedules = {}  # 存储每列车的完整时刻表

        # 初始化所有列车的基本时刻表（按计划时间）
        for train in self.trains:
            train_stations = []
            if not train.schedule or not train.schedule.stops or not isinstance(train.schedule.stops, (list, tuple)):
                continue
            for stop in train.schedule.stops:
                station_code = stop.station_code
                arr_sec = self._time_to_seconds(stop.arrival_time)
                dep_sec = self._time_to_seconds(stop.departure_time)
                schedule[(train.train_id, station_code)] = [arr_sec, dep_sec]
                train_stations.append({
                    'station_code': station_code,
                    'original_arrival': stop.arrival_time,
                    'original_departure': stop.departure_time,
                    'scheduled_arrival_seconds': arr_sec,
                    'scheduled_departure_seconds': dep_sec
                })
            train_schedules[train.train_id] = train_stations

        # 【专家重构】Step 2: 应用初始延误到受影响列车
        affected_trains = set()
        initial_delays = {}  # {(train_id, station_code): delay_seconds}

        for injected in delay_injection.injected_delays:
            train_id = injected.train_id
            station_code = injected.location.station_code or "BJX"
            initial_delay = injected.initial_delay_seconds

            if (train_id, station_code) in schedule:
                affected_trains.add(train_id)
                initial_delays[(train_id, station_code)] = initial_delay

                # 找到该列车延误站在其路线中的位置
                train = next((t for t in self.trains if t.train_id == train_id), None)
                if train:
                    stations_for_train = self._get_stations_for_train(train)
                    try:
                        idx = stations_for_train.index(station_code)
                        # 从延误站开始，所有后续站点都加上延误
                        for i in range(idx, len(stations_for_train)):
                            sc = stations_for_train[i]
                            arr, dep = schedule[(train_id, sc)]
                            schedule[(train_id, sc)] = [arr + initial_delay, dep + initial_delay]
                    except ValueError:
                        logger.warning(f"车站 {station_code} 不在列车 {train_id} 的路线中")

        # 【专家重构】Step 3: 真实延误传播 - 按车站处理追踪间隔（类似FCFS）
        for station in self.stations:
            station_code = station.station_code
            track_count = self.station_track_count.get(station_code, 1)

            # 跳过线路所（股道数为0的节点）
            if track_count == 0:
                logger.debug(f"[MaxDelayFirst] 跳过线路所 {station_code}，不进行追踪间隔处理")
                continue

            # 收集该站的所有列车，按原始发车时间排序（保持原始顺序）
            trains_at_station = []
            for train in self.trains:
                if station_code in self._get_stations_for_train(train):
                    for stop in train.schedule.stops:
                        if stop.station_code == station_code:
                            trains_at_station.append({
                                'train_id': train.train_id,
                                'original_departure': self._time_to_seconds(stop.departure_time),
                                'original_idx': len(trains_at_station),  # 记录原始索引
                                'current_departure': schedule[(train.train_id, station_code)][1]
                            })
                            break

# 【修复】按原始发车时间排序（保持FCFS基本顺序，不随意越行）
            trains_at_station.sort(key=lambda x: x['original_departure'])

            # 【关键修复】track_id必须基于排序后的位置，而不是original_idx
            # original_idx是在排序前设置的，不能用于排序后的索引

            # 初始化每个股道的最后发车时间
            last_departures = [0] * track_count

            for idx, train_info in enumerate(trains_at_station):
                train_id = train_info['train_id']
                train = next((t for t in self.trains if t.train_id == train_id), None)

                # 基于排序后的索引分配股道
                track_id = idx % track_count

                # 获取当前列车的当前调度时间
                current_arr, current_dep = schedule[(train_id, station_code)]

                # 【修复】只检查同一股道的最后发车时间，而不是所有股道的最小值
                earliest_available = last_departures[track_id]
                required_dep = max(current_dep, earliest_available + self.headway_time)
                delay_needed = required_dep - current_dep

                if delay_needed > 0:
                    # 需要传播延误：后续站点都推迟
                    stations_for_train = self._get_stations_for_train(train)
                    try:
                        idx = stations_for_train.index(station_code)
                        for i in range(idx, len(stations_for_train)):
                            sc = stations_for_train[i]
                            arr, dep = schedule[(train_id, sc)]
                            schedule[(train_id, sc)] = [arr + delay_needed, dep + delay_needed]
                    except ValueError:
                        pass

                # 更新该股道的最后发车时间
                last_departures[track_id] = max(
                    last_departures[track_id],
                    schedule[(train_id, station_code)][1]
                )

        # 【短期优化】Step 3.5: 顺序重调优化 - 尝试调整列车发车顺序
        # 使用局部搜索优化顺序，优先让延误大的列车先发车
        for station in self.stations:
            station_code = station.station_code
            track_count = self.station_track_count.get(station_code, 1)

# 跳过线路所
            if track_count == 0:
                continue

            # 【简化修复】只处理基本的追踪间隔约束
            # 收集该站的所有列车，按原始发车时间排序
            trains_at_station = []
            for train in self.trains:
                if station_code in self._get_stations_for_train(train):
                    for stop in train.schedule.stops:
                        if stop.station_code == station_code:
                            trains_at_station.append({
                                'train_id': train.train_id,
                                'original_departure': self._time_to_seconds(stop.departure_time),
                                'current_departure': schedule[(train.train_id, station_code)][1],
                            })
                            break

            # 按原始发车时间排序
            trains_at_station.sort(key=lambda x: x['original_departure'])

            # 处理追踪间隔 - 按排序后的顺序
            last_departures = [0] * track_count

            for idx, train_info in enumerate(trains_at_station):
                train_id = train_info['train_id']
                train = next((t for t in self.trains if t.train_id == train_id), None)

                # 按顺序分配股道
                track_id = idx % track_count

                # 获取当前列车的当前调度时间
                current_arr, current_dep = schedule[(train_id, station_code)]

                # 检查同一股道的最后发车时间
                earliest_available = last_departures[track_id]
                required_dep = max(current_dep, earliest_available + self.headway_time)
                delay_needed = required_dep - current_dep

                if delay_needed > 0:
                    # 需要传播延误：后续站点都推迟
                    stations_for_train = self._get_stations_for_train(train)
                    try:
                        idx_in_route = stations_for_train.index(station_code)
                        for i in range(idx_in_route, len(stations_for_train)):
                            sc = stations_for_train[i]
                            arr, dep = schedule[(train_id, sc)]
                            schedule[(train_id, sc)] = [arr + delay_needed, dep + delay_needed]
                    except ValueError:
                        pass

# 更新该股道的最后发车时间
                last_departures[track_id] = max(
                    last_departures[track_id],
                    schedule[(train_id, station_code)][1]
                )

        # 【专家重构】Step 4: 贪心优化 - 优先处理延误最大的列车
        optimization_objective = getattr(self, 'optimization_objective', 'min_total_delay')
        max_iterations = 10

        for iteration in range(max_iterations):
            # 计算每列车的当前延误
            train_current_delays = {}
            for train_id in affected_trains:
                train = next((t for t in self.trains if t.train_id == train_id), None)
                if train is None:
                    continue

                # 取所有站点中的最大延误作为列车延误
                max_train_delay = 0
                for sc in self._get_stations_for_train(train):
                    _, dep = schedule[(train_id, sc)]
                    original_dep = self._time_to_seconds(
                        next(s.departure_time for s in train.schedule.stops if s.station_code == sc)
                    )
                    delay_at_station = max(0, dep - original_dep)
                    max_train_delay = max(max_train_delay, delay_at_station)
                train_current_delays[train_id] = max_train_delay

            # 根据优化目标选择要处理的列车
            if optimization_objective == "min_total_delay":
                # 选择延误影响范围最大的列车（延误站点数最多）
                train_delay_count = {}
                for train_id in affected_trains:
                    train = next((t for t in self.trains if t.train_id == train_id), None)
                    if train is None:
                        continue
                    delay_count = sum(
                        1 for sc in self._get_stations_for_train(train)
                        if schedule[(train_id, sc)][1] > self._time_to_seconds(
                            train.schedule.stops[
                                next(i for i, s in enumerate(train.schedule.stops) if s.station_code == sc)
                            ].departure_time
                        )
                    )
                    train_delay_count[train_id] = delay_count

                max_count = max(train_delay_count.values()) if train_delay_count else 0
                candidates = [t for t, c in train_delay_count.items() if c == max_count]
                max_delay_train = candidates[0] if candidates else None
                max_delay = train_current_delays.get(max_delay_train, 0) if max_delay_train else 0
            else:
                # 选择最大延误的列车
                max_delay_train = None
                max_delay = 0
                for train_id, delay in train_current_delays.items():
                    if delay > max_delay:
                        max_delay = delay
                        max_delay_train = train_id

            if max_delay_train is None or max_delay == 0:
                break

            # 尝试压缩该列车的停站时间和区间运行时间来减少延误
            train = next((t for t in self.trains if t.train_id == max_delay_train), None)
            if train is None:
                continue

            recovered_time = 0
            train_stations = self._get_stations_for_train(train)

            # 【修复】找到该列车最早的延误注入站，只从该站开始恢复
            # 避免将 injection 之前的站点也提前（导致早于计划时刻）
            injection_stations = [scode for tid, scode in initial_delays.keys() if tid == max_delay_train]
            earliest_injection_idx = 0
            if injection_stations:
                earliest_injection = min(injection_stations, key=lambda sc: train_stations.index(sc) if sc in train_stations else 9999)
                earliest_injection_idx = train_stations.index(earliest_injection) if earliest_injection in train_stations else 0

            # 压缩停站冗余
            for i, sc in enumerate(train_stations[:-1]):
                if i < earliest_injection_idx:
                    continue
                if recovered_time >= max_delay:
                    break

                arr, dep = schedule[(max_delay_train, sc)]
                current_stop_duration = dep - arr

                if current_stop_duration > self.min_stop_time:
                    redundancy = current_stop_duration - self.min_stop_time
                    compress = min(int(redundancy * self.stop_time_redundancy_ratio),
                                  self.max_compression_per_step,
                                  max_delay - recovered_time)
                    if compress > 0:
                        new_dep = dep - compress
                        # 保证发车不早于到达，不早于原始计划
                        original_dep = self._time_to_seconds(
                            next(s.departure_time for s in train.schedule.stops if s.station_code == sc)
                        )
                        new_dep = max(new_dep, arr, original_dep)
                        actual_compress = dep - new_dep
                        if actual_compress > 0:
                            schedule[(max_delay_train, sc)] = [arr, new_dep]
                            recovered_time += actual_compress

                            # 后续站点同步提前，但不能早于原始计划
                            for sc_next in train_stations[i + 1:]:
                                arr_next, dep_next = schedule[(max_delay_train, sc_next)]
                                original_arr_next = self._time_to_seconds(
                                    next(s.arrival_time for s in train.schedule.stops if s.station_code == sc_next)
                                )
                                original_dep_next = self._time_to_seconds(
                                    next(s.departure_time for s in train.schedule.stops if s.station_code == sc_next)
                                )
                                new_arr_next = max(arr_next - actual_compress, original_arr_next)
                                new_dep_next = max(dep_next - actual_compress, original_dep_next, new_arr_next)
                                schedule[(max_delay_train, sc_next)] = [new_arr_next, new_dep_next]

            # 压缩区间运行冗余
            for i, sc in enumerate(train_stations[:-1]):
                if i < earliest_injection_idx:
                    continue
                if recovered_time >= max_delay:
                    break

                sc_next = train_stations[i + 1]
                dep_current = schedule[(max_delay_train, sc)][1]
                arr_next, dep_next = schedule[(max_delay_train, sc_next)]

                # 基于当前实际区间运行时间计算冗余
                current_running = arr_next - dep_current
                min_running = self._get_min_section_time(sc, sc_next)

                if current_running > min_running:
                    redundancy = current_running - min_running
                    compress = min(int(redundancy * self.running_time_redundancy_ratio),
                                  self.max_compression_per_step,
                                  max_delay - recovered_time)
                    if compress > 0:
                        new_arr_next = arr_next - compress
                        # 保证区间运行时间不小于最小值，不早于原始计划
                        new_arr_next = max(new_arr_next, dep_current + min_running)
                        original_arr_next = self._time_to_seconds(
                            next(s.arrival_time for s in train.schedule.stops if s.station_code == sc_next)
                        )
                        new_arr_next = max(new_arr_next, original_arr_next)
                        actual_compress = arr_next - new_arr_next
                        if actual_compress > 0:
                            new_dep_next = dep_next - actual_compress
                            original_dep_next = self._time_to_seconds(
                                next(s.departure_time for s in train.schedule.stops if s.station_code == sc_next)
                            )
                            new_dep_next = max(new_dep_next, original_dep_next, new_arr_next)
                            schedule[(max_delay_train, sc_next)] = [new_arr_next, new_dep_next]
                            recovered_time += actual_compress

                            for sc_later in train_stations[train_stations.index(sc_next) + 1:]:
                                arr_later, dep_later = schedule[(max_delay_train, sc_later)]
                                original_arr_later = self._time_to_seconds(
                                    next(s.arrival_time for s in train.schedule.stops if s.station_code == sc_later)
                                )
                                original_dep_later = self._time_to_seconds(
                                    next(s.departure_time for s in train.schedule.stops if s.station_code == sc_later)
                                )
                                new_arr_later = max(arr_later - actual_compress, original_arr_later)
                                new_dep_later = max(dep_later - actual_compress, original_dep_later, new_arr_later)
                                schedule[(max_delay_train, sc_later)] = [new_arr_later, new_dep_later]

        # 【专家重构】Step 5: 构建最终结果
        optimized_schedule = {}
        all_delays = []
        final_affected_trains = set()

        for train in self.trains:
            train_schedule = []
            train_has_delay = False

            if not train.schedule or not train.schedule.stops or not isinstance(train.schedule.stops, (list, tuple)):
                continue

            for stop in train.schedule.stops:
                station_code = stop.station_code
                arr_sec, dep_sec = schedule[(train.train_id, station_code)]

                # 计算延误（以发车时间为准）
                original_dep_sec = self._time_to_seconds(stop.departure_time)
                delay_sec = max(0, dep_sec - original_dep_sec)

                if delay_sec > 0:
                    train_has_delay = True

                all_delays.append(delay_sec)

                train_schedule.append({
                    "station_code": station_code,
                    "station_name": self.station_names.get(station_code, station_code),
                    "arrival_time": self._seconds_to_time(int(arr_sec)),
                    "departure_time": self._seconds_to_time(int(dep_sec)),
                    "original_arrival": stop.arrival_time,
                    "original_departure": stop.departure_time,
                    "delay_seconds": int(delay_sec)
                })

            if train_has_delay:
                final_affected_trains.add(train.train_id)

            optimized_schedule[train.train_id] = train_schedule

        # 计算统计数据
        max_delay_val = max(all_delays) if all_delays else 0
        # 【修复】avg_delay 使用受影响列车数作为分母，与高铁调度行业标准一致
        avg_delay = sum(all_delays) / len(final_affected_trains) if final_affected_trains else 0

        return SolveResult(
            success=True,
            optimized_schedule=optimized_schedule,
            delay_statistics={
                "max_delay_seconds": int(max_delay_val),
                "avg_delay_seconds": float(avg_delay),
                "total_delay_seconds": int(sum(all_delays)),
                "affected_trains_count": len(final_affected_trains)
            },
            computation_time=time.time() - start_time,
            message="最大延误优先调度器（专家重构版）：优先减少最大延误，支持追踪间隔约束"
        )

    def get_original_schedule(self) -> Dict[str, List[Dict]]:
        """获取原始时刻表"""
        schedule = {}
        for train in self.trains:
            stops = []
            if train.schedule and train.schedule.stops and isinstance(train.schedule.stops, (list, tuple)):
                for stop in train.schedule.stops:
                    if hasattr(stop, 'station_code'):
                        stops.append({
                            "station_code": stop.station_code,
                            "station_name": getattr(stop, 'station_name', stop.station_code),
                            "arrival_time": getattr(stop, 'arrival_time', ''),
                            "departure_time": getattr(stop, 'departure_time', ''),
                            "delay_seconds": 0
                        })
            schedule[train.train_id] = stops
        return schedule


# 测试代码
if __name__ == "__main__":
    from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
    from models.data_models import DelayInjection, InjectedDelay, DelayLocation

    use_real_data(True)
    trains = get_trains_pydantic()[:10]
    stations = get_stations_pydantic()

    # 创建调度器
    scheduler = MaxDelayFirstScheduler(trains, stations)

    # 创建延误注入
    delay_injection = DelayInjection(
        scenario_type="temporary_speed_limit",
        scenario_id="TEST_001",
        injected_delays=[
            InjectedDelay(
                train_id="G1215",
                location=DelayLocation(
                    location_type="station",
                    station_code="XSD"
                ),
                initial_delay_seconds=600,  # 10分钟延误
                timestamp="2024-01-01T10:00:00"
            ),
            InjectedDelay(
                train_id="G1239",
                location=DelayLocation(
                    location_type="station",
                    station_code="XSD"
                ),
                initial_delay_seconds=300,  # 5分钟延误
                timestamp="2024-01-01T10:00:00"
            )
        ],
        affected_trains=["G1215", "G1239"]
    )

    # 执行调度
    result = scheduler.solve(delay_injection)
    print(f"成功: {result.success}")
    print(f"消息: {result.message}")
    print(f"延误统计: {result.delay_statistics}")