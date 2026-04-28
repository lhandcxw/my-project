# -*- coding: utf-8 -*-
"""
铁路调度系统 - MaxDelayFirst（最大延误优先）调度器模块
优先处理延误最大的列车，尽可能减少最大延误
"""

from typing import List, Dict, Any, Optional
import time
import logging

from solver.base import BaseSolver, SolveResult
from models.data_models import Train, Station, DelayInjection
from config import DispatchEnvConfig

logger = logging.getLogger(__name__)


class MaxDelayFirstScheduler(BaseSolver):
    """
    最大延误优先调度器（Max-Delay First）
    优先处理延误最大的列车，尽可能减少最大延误
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        headway_time: int = None,
        min_stop_time: int = None,
        max_stop_compression: int = None,
        max_compression_per_step: int = None,
        stop_time_redundancy_ratio: float = None,
        running_time_redundancy_ratio: float = None,
        optimization_objective: str = "min_total_delay",
        **kwargs
    ):
        super().__init__(trains, stations, headway_time, min_stop_time)

        # 【公平性修复】冗余比例统一使用全局约束配置，
        # 不再使用 solver.max_delay_first 的独立默认值 1.0
        if stop_time_redundancy_ratio is None:
            stop_time_redundancy_ratio = DispatchEnvConfig.stop_time_redundancy_ratio()
        if running_time_redundancy_ratio is None:
            running_time_redundancy_ratio = DispatchEnvConfig.running_time_redundancy_ratio()
        if max_stop_compression is None:
            max_stop_compression = DispatchEnvConfig.get("solver.max_delay_first.max_stop_compression", 60)
        if max_compression_per_step is None:
            max_compression_per_step = DispatchEnvConfig.get("solver.max_delay_first.max_compression_per_step", 30)

        self.max_stop_compression = max_stop_compression
        self.max_compression_per_step = max_compression_per_step
        self.stop_time_redundancy_ratio = stop_time_redundancy_ratio
        self.running_time_redundancy_ratio = running_time_redundancy_ratio
        self.optimization_objective = optimization_objective
        self.line_posts = {s.station_code for s in stations if s.track_count == 0}
        self.original_running_times = self._load_original_running_times()

    def _get_original_section_time(self, from_station: str, to_station: str) -> int:
        """获取指定区间的原始平均运行时间"""
        key = (from_station, to_station)
        default_time = DispatchEnvConfig.get("constraints.default_min_section_time", 600)
        return self.original_running_times.get(key, default_time)

    def solve(
        self,
        delay_injection: DelayInjection,
        objective: str = "min_total_delay",
        solver_config: Dict = None
    ) -> SolveResult:
        """
        执行调度优化 - 最大延误优先策略
        """
        start_time = time.time()

        if solver_config and isinstance(solver_config, dict):
            self.optimization_objective = solver_config.get("optimization_objective", self.optimization_objective)

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

        # Step 3: 延误传播 - 按车站处理追踪间隔
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

            trains_at_station.sort(key=lambda x: x['original_departure'])
            last_departures = [0] * track_count

            for idx, train_info in enumerate(trains_at_station):
                train_id = train_info['train_id']
                train = next((t for t in self.trains if t.train_id == train_id), None)
                track_id = idx % track_count
                current_arr, current_dep = schedule[(train_id, station_code)]
                earliest_available = last_departures[track_id]
                required_dep = max(current_dep, earliest_available + self.headway_time)
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

                last_departures[track_id] = max(
                    last_departures[track_id],
                    schedule[(train_id, station_code)][1]
                )

        # Step 3.5: 顺序重调优化
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
                track_id = idx % track_count
                current_arr, current_dep = schedule[(train_id, station_code)]
                earliest_available = last_departures[track_id]
                required_dep = max(current_dep, earliest_available + self.headway_time)
                delay_needed = required_dep - current_dep

                if delay_needed > 0:
                    stations_for_train = self._get_stations_for_train(train)
                    try:
                        idx_in_route = stations_for_train.index(station_code)
                        for i in range(idx_in_route, len(stations_for_train)):
                            sc = stations_for_train[i]
                            arr, dep = schedule[(train_id, sc)]
                            schedule[(train_id, sc)] = [arr + delay_needed, dep + delay_needed]
                    except ValueError:
                        pass

                last_departures[track_id] = max(
                    last_departures[track_id],
                    schedule[(train_id, station_code)][1]
                )

        # Step 4: 贪心优化 - 优先处理延误最大的列车
        optimization_objective = getattr(self, 'optimization_objective', 'min_total_delay')
        max_iterations = 10

        for iteration in range(max_iterations):
            train_current_delays = {}
            for train_id in affected_trains:
                train = next((t for t in self.trains if t.train_id == train_id), None)
                if train is None:
                    continue
                max_train_delay = 0
                for sc in self._get_stations_for_train(train):
                    _, dep = schedule[(train_id, sc)]
                    original_dep = self._time_to_seconds(
                        next(s.departure_time for s in train.schedule.stops if s.station_code == sc)
                    )
                    max_train_delay = max(max_train_delay, max(0, dep - original_dep))
                train_current_delays[train_id] = max_train_delay

            if optimization_objective == "min_total_delay":
                train_delay_count = {}
                for train_id in affected_trains:
                    train = next((t for t in self.trains if t.train_id == train_id), None)
                    if train is None:
                        continue
                    delay_count = sum(
                        1 for sc in self._get_stations_for_train(train)
                        if schedule[(train_id, sc)][1] > self._time_to_seconds(
                            next(s for s in train.schedule.stops if s.station_code == sc).departure_time
                        )
                    )
                    train_delay_count[train_id] = delay_count

                max_count = max(train_delay_count.values()) if train_delay_count else 0
                candidates = [t for t, c in train_delay_count.items() if c == max_count]
                max_delay_train = candidates[0] if candidates else None
                max_delay = train_current_delays.get(max_delay_train, 0) if max_delay_train else 0
            else:
                max_delay_train = None
                max_delay = 0
                for train_id, delay in train_current_delays.items():
                    if delay > max_delay:
                        max_delay = delay
                        max_delay_train = train_id

            if max_delay_train is None or max_delay == 0:
                break

            train = next((t for t in self.trains if t.train_id == max_delay_train), None)
            if train is None:
                continue

            recovered_time = 0
            train_stations = self._get_stations_for_train(train)

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
                        original_dep = self._time_to_seconds(
                            next(s.departure_time for s in train.schedule.stops if s.station_code == sc)
                        )
                        new_dep = max(new_dep, arr, original_dep)
                        actual_compress = dep - new_dep
                        if actual_compress > 0:
                            schedule[(max_delay_train, sc)] = [arr, new_dep]
                            recovered_time += actual_compress
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
                current_running = arr_next - dep_current
                min_running = self._get_min_section_time(sc, sc_next)
                if current_running > min_running:
                    redundancy = current_running - min_running
                    compress = min(int(redundancy * self.running_time_redundancy_ratio),
                                  self.max_compression_per_step,
                                  max_delay - recovered_time)
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
            message="最大延误优先调度器：优先减少最大延误"
        )
