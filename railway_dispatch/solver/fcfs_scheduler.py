# -*- coding: utf-8 -*-
"""
铁路调度系统 - FCFS（先到先服务）调度器模块
实现列车调度的先到先服务策略

专家视角的真实调度逻辑：
1. 延误传播：后续列车因追踪间隔被延误
2. 停站冗余利用：压缩有富余的停站时间
3. 区间运行冗余：利用有富余的区间运行时间
4. 发车顺序调整：在约束允许范围内优化发车
"""

from typing import List, Dict, Tuple, Optional, Any
import time
import logging

from solver.base import BaseSolver, SolveResult
from models.data_models import Train, Station, DelayInjection
from config import DispatchEnvConfig

logger = logging.getLogger(__name__)


class FCFSScheduler(BaseSolver):
    """
    先到先服务调度器（First-Come-First-Serve）

    真实高铁调度策略：
    1. 受影响列车保持其延误，并向后传播
    2. 后续列车需要等待，以满足最小追踪间隔（延误传播）
    3. 利用停站冗余减少延误影响
    4. 利用区间运行冗余"追赶"时间
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        headway_time: int = None,
        min_stop_time: int = None,
        stop_time_redundancy_ratio: float = None,
        running_time_redundancy_ratio: float = None,
    ):
        super().__init__(trains, stations, headway_time, min_stop_time)

        if stop_time_redundancy_ratio is None:
            stop_time_redundancy_ratio = DispatchEnvConfig.stop_time_redundancy_ratio()
        if running_time_redundancy_ratio is None:
            running_time_redundancy_ratio = DispatchEnvConfig.running_time_redundancy_ratio()

        self.stop_time_redundancy_ratio = stop_time_redundancy_ratio
        self.running_time_redundancy_ratio = running_time_redundancy_ratio
        self.original_running_times = self._load_original_running_times()

    def _get_original_section_time(self, from_station: str, to_station: str) -> int:
        """获取指定区间的原始平均运行时间"""
        key = (from_station, to_station)
        default_time = DispatchEnvConfig.get("constraints.default_min_section_time", 600)
        return self.original_running_times.get(key, default_time)

    def _get_stop_time_redundancy(self, train: Train, station_code: str) -> int:
        """获取指定站的停站冗余时间"""
        original_duration = self._get_original_stop_duration(train, station_code)
        if original_duration <= 0:
            return 0
        min_stop_ratio = DispatchEnvConfig.get("constraints.min_stop_ratio", 0.5)
        min_duration = max(self.min_stop_time, int(original_duration * min_stop_ratio))
        return original_duration - min_duration

    def _get_section_redundancy(self, from_station: str, to_station: str) -> int:
        """获取指定区间的运行冗余时间"""
        original_time = self._get_original_section_time(from_station, to_station)
        min_time = self._get_min_section_time(from_station, to_station)
        return original_time - min_time

    def solve(self, delay_injection: DelayInjection, objective: str = "min_total_delay") -> SolveResult:
        """
        使用FCFS策略求解调度问题（真实延误传播版）
        """
        start_time = time.time()

        # Step 1: 初始化调度时刻表
        schedule = {}
        for train in self.trains:
            if not train.schedule or not train.schedule.stops or not isinstance(train.schedule.stops, (list, tuple)):
                continue
            for stop in train.schedule.stops:
                station_code = stop.station_code
                arr_sec = self._time_to_seconds(stop.arrival_time)
                dep_sec = self._time_to_seconds(stop.departure_time)
                schedule[(train.train_id, station_code)] = [arr_sec, dep_sec]

        # Step 2: 应用初始延误到受影响列车
        affected_trains = set()
        initial_delays = {}

        for injected in delay_injection.injected_delays:
            train_id = injected.train_id
            station_code = injected.location.station_code or "BJX"
            initial_delay = injected.initial_delay_seconds

            if (train_id, station_code) in schedule:
                affected_trains.add(train_id)
                initial_delays[(train_id, station_code)] = initial_delay

                train = next((t for t in self.trains if t.train_id == train_id), None)
                if train:
                    stations_for_train = self._get_stations_for_train(train)
                    try:
                        idx = stations_for_train.index(station_code)
                        for i in range(idx, len(stations_for_train)):
                            sc = stations_for_train[i]
                            arr, dep = schedule[(train_id, sc)]
                            schedule[(train_id, sc)] = [arr + initial_delay, dep + initial_delay]
                    except ValueError:
                        logger.warning(f"车站 {station_code} 不在列车 {train_id} 的路线中")

        # Step 3: 真实延误传播 - 按车站处理追踪间隔
        for station in self.stations:
            station_code = station.station_code
            track_count = self.station_track_count.get(station_code, 1)
            if track_count == 0:
                continue

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

            trains_at_station.sort(key=lambda x: x['original_departure'])
            last_departures = [0] * track_count

            for idx, train_info in enumerate(trains_at_station):
                train_id = train_info['train_id']
                train = next((t for t in self.trains if t.train_id == train_id), None)
                best_track = idx % track_count
                current_arr, current_dep = schedule[(train_id, station_code)]

                original_duration = self._get_original_stop_duration(train, station_code)
                min_stop_duration = self.min_stop_time if original_duration > 0 else 0

                track_available = last_departures[best_track]
                required_dep = max(current_dep, track_available + self.headway_time)
                delay_needed = required_dep - current_dep

                if delay_needed > 0:
                    stations_for_train = self._get_stations_for_train(train)
                    try:
                        idx_s = stations_for_train.index(station_code)
                        for i in range(idx_s, len(stations_for_train)):
                            sc = stations_for_train[i]
                            arr, dep = schedule[(train_id, sc)]
                            schedule[(train_id, sc)] = [arr + delay_needed, dep + delay_needed]
                    except ValueError:
                        pass

                last_departures[best_track] = max(
                    last_departures[best_track],
                    schedule[(train_id, station_code)][1]
                )

        # Step 3.5: 局部搜索优化发车顺序
        for station in self.stations:
            station_code = station.station_code
            track_count = self.station_track_count.get(station_code, 1)
            if track_count == 0:
                continue

            trains_at_station = []
            for train in self.trains:
                if station_code in self._get_stations_for_train(train):
                    for stop in train.schedule.stops:
                        if stop.station_code == station_code:
                            trains_at_station.append({
                                'train_id': train.train_id,
                                'original_departure': self._time_to_seconds(stop.departure_time),
                                'current_departure': schedule[(train.train_id, station_code)][1]
                            })
                            break

            if len(trains_at_station) < 3:
                continue

            max_iterations = min(
                DispatchEnvConfig.fcfs_local_search_max_iterations(),
                len(trains_at_station)
            )
            improved = True
            iteration = 0

            while improved and iteration < max_iterations:
                improved = False
                iteration += 1

                for i in range(len(trains_at_station) - 1):
                    before_delay = 0
                    for j in range(max(0, i - 1), min(len(trains_at_station), i + 3)):
                        train_id = trains_at_station[j]['train_id']
                        _, dep = schedule[(train_id, station_code)]
                        original_dep = trains_at_station[j]['original_departure']
                        before_delay += max(0, dep - original_dep)

                    trains_at_station[i], trains_at_station[i + 1] = \
                        trains_at_station[i + 1], trains_at_station[i]

                    last_departures_temp = [0] * track_count
                    new_schedule = schedule.copy()

                    for idx_t, t_info in enumerate(trains_at_station):
                        train_id = t_info['train_id']
                        train = next((t for t in self.trains if t.train_id == train_id), None)
                        if train is None:
                            continue
                        best_track = idx_t % track_count
                        current_arr, current_dep = new_schedule[(train_id, station_code)]
                        earliest_available = min(last_departures_temp) if last_departures_temp else 0
                        required_dep = max(current_dep, earliest_available + self.headway_time)

                        if required_dep > current_dep:
                            delay_needed = required_dep - current_dep
                            stations_for_train = self._get_stations_for_train(train)
                            try:
                                idx_train = stations_for_train.index(station_code)
                                for sc in stations_for_train[idx_train:]:
                                    arr, dep = new_schedule[(train_id, sc)]
                                    new_schedule[(train_id, sc)] = [arr + delay_needed, dep + delay_needed]
                            except ValueError:
                                pass

                        last_departures_temp[best_track] = max(
                            last_departures_temp[best_track],
                            new_schedule[(train_id, station_code)][1]
                        )

                    after_delay = 0
                    for j in range(max(0, i - 1), min(len(trains_at_station), i + 3)):
                        train_id = trains_at_station[j]['train_id']
                        _, dep = new_schedule[(train_id, station_code)]
                        original_dep = trains_at_station[j]['original_departure']
                        after_delay += max(0, dep - original_dep)

                    if after_delay < before_delay:
                        improved = True
                        schedule = new_schedule
                        break
                    else:
                        trains_at_station[i], trains_at_station[i + 1] = \
                            trains_at_station[i + 1], trains_at_station[i]

                if not improved:
                    break

        # Step 4: 利用冗余时间进行恢复
        for train_id in affected_trains:
            train = next((t for t in self.trains if t.train_id == train_id), None)
            if train is None:
                continue

            total_train_delay = 0
            for sc in self._get_stations_for_train(train):
                _, dep = schedule[(train_id, sc)]
                original_dep = self._time_to_seconds(
                    next(s.departure_time for s in train.schedule.stops if s.station_code == sc)
                )
                total_train_delay = max(total_train_delay, max(0, dep - original_dep))

            if total_train_delay > 0:
                available_recovery = 0
                for sc in self._get_stations_for_train(train)[:-1]:
                    original_duration = self._get_original_stop_duration(train, sc)
                    if original_duration > 0:
                        redundancy = self._get_stop_time_redundancy(train, sc)
                        available_recovery += int(redundancy * self.stop_time_redundancy_ratio)

                actual_recovery = min(available_recovery, total_train_delay)

                if actual_recovery > 0:
                    remaining_recovery = actual_recovery
                    train_stations = self._get_stations_for_train(train)

                    injection_stations = [scode for tid, scode in initial_delays.keys() if tid == train_id]
                    earliest_injection_idx = 0
                    if injection_stations:
                        earliest_injection = min(injection_stations, key=lambda sc: train_stations.index(sc) if sc in train_stations else 9999)
                        earliest_injection_idx = train_stations.index(earliest_injection) if earliest_injection in train_stations else 0

                    for sc in train_stations[:-1]:
                        if remaining_recovery <= 0:
                            break
                        sc_idx = train_stations.index(sc)
                        if sc_idx < earliest_injection_idx:
                            continue
                        arr, dep = schedule[(train_id, sc)]
                        current_stop_duration = dep - arr
                        if current_stop_duration > self.min_stop_time:
                            redundancy = current_stop_duration - self.min_stop_time
                            compress = min(int(redundancy * self.stop_time_redundancy_ratio), remaining_recovery)
                            if compress > 0:
                                new_dep = dep - compress
                                original_dep = self._time_to_seconds(
                                    next(s.departure_time for s in train.schedule.stops if s.station_code == sc)
                                )
                                new_dep = max(new_dep, arr, original_dep)
                                actual_compress = dep - new_dep
                                if actual_compress > 0:
                                    schedule[(train_id, sc)][1] = new_dep
                                    remaining_recovery -= actual_compress
                                    try:
                                        sc_idx = train_stations.index(sc)
                                        for sc_next in train_stations[sc_idx:]:
                                            if sc_next == sc:
                                                continue
                                            arr_next, dep_next = schedule[(train_id, sc_next)]
                                            original_arr_next = self._time_to_seconds(
                                                next(s.arrival_time for s in train.schedule.stops if s.station_code == sc_next)
                                            )
                                            original_dep_next = self._time_to_seconds(
                                                next(s.departure_time for s in train.schedule.stops if s.station_code == sc_next)
                                            )
                                            new_arr_next = max(arr_next - actual_compress, original_arr_next)
                                            new_dep_next = max(dep_next - actual_compress, original_dep_next, new_arr_next)
                                            schedule[(train_id, sc_next)] = [new_arr_next, new_dep_next]
                                    except ValueError:
                                        pass

                    for i, sc in enumerate(train_stations[:-1]):
                        if remaining_recovery <= 0:
                            break
                        if i < earliest_injection_idx:
                            continue
                        sc_next = train_stations[i + 1]
                        dep_current = schedule[(train_id, sc)][1]
                        arr_next, dep_next = schedule[(train_id, sc_next)]
                        current_running = arr_next - dep_current
                        min_running = self._get_min_section_time(sc, sc_next)

                        if current_running > min_running:
                            redundancy = current_running - min_running
                            compress = min(int(redundancy * self.running_time_redundancy_ratio), remaining_recovery)
                            if compress > 0:
                                new_arr_next = arr_next - compress
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
                                    schedule[(train_id, sc_next)] = [new_arr_next, new_dep_next]
                                    remaining_recovery -= actual_compress

        # Step 5: 构建最终结果
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

        max_delay_val = max(all_delays) if all_delays else 0
        # 【修复】avg_delay 使用受影响列车的平均最大延误，与 MetricsDefinition.calculate_metrics() 口径一致
        affected_train_max_delays = []
        for train_id in final_affected_trains:
            train_delays = [s.get("delay_seconds", 0) for s in optimized_schedule[train_id]]
            affected_train_max_delays.append(max(train_delays))
        avg_delay = sum(affected_train_max_delays) / len(affected_train_max_delays) if affected_train_max_delays else 0

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
            message="FCFS调度成功（带延误传播）"
        )


def create_fcfs_scheduler(trains: List[Train], stations: List[Station]) -> FCFSScheduler:
    """创建FCFS调度器实例"""
    return FCFSScheduler(trains, stations)
