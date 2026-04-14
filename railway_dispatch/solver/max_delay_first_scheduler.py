# -*- coding: utf-8 -*-
"""
铁路调度系统 - MaxDelayFirst（最大延误优先）调度器模块
优先处理延误最大的列车，尽可能减少最大延误
采用贪心策略：每次选择当前最大延误的列车进行调整
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import time
import copy
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


class MaxDelayFirstScheduler:
    """
    最大延误优先调度器（Max-Delay First）
    优先处理延误最大的列车，尽可能减少最大延误

    采用贪心策略：
    1. 应用初始延误
    2. 找到当前最大延误的列车
    3. 尝试通过压缩停站时间来减少该列车的延误
    4. 迭代直到无法进一步优化
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        headway_time: int = None,  # 追踪间隔 - 从配置读取
        min_stop_time: int = None,  # 最小停站时间 - 从配置读取
        max_stop_compression: int = 60,  # 最大停站压缩时间
        **kwargs
    ):
        # 从统一配置加载
        from config import DispatchEnvConfig

        self.trains = trains
        self.stations = stations
        self.headway_time = headway_time if headway_time is not None else DispatchEnvConfig.headway_time()
        self.min_stop_time = min_stop_time if min_stop_time is not None else DispatchEnvConfig.min_stop_time()
        self.max_stop_compression = max_stop_compression

        self.station_names = {s.station_code: s.station_name for s in stations}
        self.station_track_count = {s.station_code: s.track_count for s in stations}
        # 识别线路所（track_count=0的节点）
        self.line_posts = {s.station_code for s in stations if s.track_count == 0}

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

    def solve(
        self,
        delay_injection: DelayInjection,
        objective: str = "min_max_delay"
    ) -> SolveResult:
        """
        执行调度优化 - 最大延误优先策略

        Args:
            delay_injection: 延误注入信息
            objective: 优化目标 ("min_max_delay" 或 "min_avg_delay")

        Returns:
            SolveResult: 调度结果
        """
        start_time = time.time()

        # 初始化：应用初始延误
        schedule = self.get_original_schedule()

        # 记录每列车的当前延误
        train_delays = {}  # train_id -> current_delay
        train_original_dep = {}  # train_id -> {station_code: original_departure_seconds}

        # 应用初始延误
        for injected in delay_injection.injected_delays:
            train_id = injected.train_id
            station_code = injected.location.station_code
            initial_delay = injected.initial_delay_seconds

            if train_id in schedule:
                train_delays[train_id] = initial_delay
                train_original_dep[train_id] = {}

                # 从注入站开始，后续所有站点都延误
                train_stops = schedule[train_id]
                found_station = False

                # 记录原始发车时间
                for stop in train_stops:
                    train_original_dep[train_id][stop["station_code"]] = self._time_to_seconds(
                        stop.get("original_departure", stop["departure_time"])
                    )

                for stop in train_stops:
                    if found_station:
                        stop["delay_seconds"] = initial_delay
                    if stop["station_code"] == station_code:
                        found_station = True
                        stop["delay_seconds"] = initial_delay

        # 获取所有受影响的列车
        affected_trains = list(train_delays.keys())

        # 迭代优化：尝试通过压缩停站时间来减少最大延误
        # 同时考虑追踪间隔约束
        max_iterations = 10
        for iteration in range(max_iterations):
            max_delay_train = None
            max_delay = 0

            # 找到当前最大延误的列车
            for train_id in affected_trains:
                if train_delays.get(train_id, 0) > max_delay:
                    max_delay = train_delays.get(train_id, 0)
                    max_delay_train = train_id

            if max_delay_train is None or max_delay == 0:
                break

            # 尝试压缩该列车的停站时间来减少延误
            train_stops = schedule[max_delay_train]
            recovered_time = 0
            compression_applied = []  # 记录应用的压缩，用于调整实际时间

            for i, stop in enumerate(train_stops):
                if stop.get("delay_seconds", 0) > 0:
                    # 尝试压缩停站时间
                    max_compress = min(30, self.max_stop_compression - recovered_time)
                    if max_compress > 0:
                        # 压缩delay_seconds
                        old_delay = stop["delay_seconds"]
                        new_delay = max(0, old_delay - max_compress)
                        stop["delay_seconds"] = new_delay

                        actual_compress = old_delay - new_delay
                        recovered_time += actual_compress
                        compression_applied.append((i, actual_compress))

            # 实际调整时刻表（压缩发车时间）
            for idx, compress in compression_applied:
                stop = train_stops[idx]
                station_code = stop["station_code"]

                # 获取原始发车时间
                original_dep = train_original_dep[max_delay_train].get(station_code, 0)

                # 计算新的发车时间 = 原始发车时间 + 当前延误
                new_dep_sec = original_dep + stop["delay_seconds"]
                stop["departure_time"] = self._seconds_to_time(new_dep_sec)

                # 同步调整后续站点
                for j in range(idx + 1, len(train_stops)):
                    next_stop = train_stops[j]
                    original_next_dep = train_original_dep[max_delay_train].get(
                        next_stop["station_code"], 0
                    )
                    # 后续站点也受压缩影响
                    new_next_dep = original_next_dep + next_stop["delay_seconds"]
                    next_stop["departure_time"] = self._seconds_to_time(new_next_dep)
                    next_stop["arrival_time"] = self._seconds_to_time(
                        self._time_to_seconds(next_stop["arrival_time"]) - compress
                    )

            # 追踪间隔检查：检查压缩后是否会影响后续列车
            # 如果压缩导致发车时间提前，需要检查是否与后续列车冲突
            for stop in train_stops:
                station_code = stop["station_code"]
                current_dep = self._time_to_seconds(stop["departure_time"])

                # 检查后续列车的追踪间隔
                for other_train_id in affected_trains:
                    if other_train_id == max_delay_train:
                        continue
                    other_stops = schedule[other_train_id]
                    for other_stop in other_stops:
                        if other_stop["station_code"] == station_code:
                            other_dep = self._time_to_seconds(other_stop["departure_time"])
                            # 如果当前列车发车时间早于后续列车，但间隔小于headway
                            if current_dep < other_dep and other_dep - current_dep < self.headway_time:
                                # 调整后续列车
                                new_other_dep = current_dep + self.headway_time
                                other_stop["departure_time"] = self._seconds_to_time(new_other_dep)
                                other_delay = new_other_dep - train_original_dep[other_train_id].get(
                                    station_code, new_other_dep
                                )
                                if other_delay > 0:
                                    other_stop["delay_seconds"] = other_delay
                                    train_delays[other_train_id] = max(
                                        train_delays.get(other_train_id, 0),
                                        other_delay
                                    )
                            break

            # 更新该列车的延误
            train_delays[max_delay_train] = max(0, max_delay - recovered_time)

        # 计算延误统计
        all_delays = []
        for train_id, stops in schedule.items():
            for stop in stops:
                all_delays.append(stop.get("delay_seconds", 0))

        max_delay_val = max(all_delays) if all_delays else 0
        avg_delay = sum(all_delays) / len(all_delays) if all_delays else 0
        affected_count = len([t for t in affected_trains if train_delays.get(t, 0) > 0])

        delay_statistics = {
            "max_delay_seconds": int(max_delay_val),
            "avg_delay_seconds": float(avg_delay),
            "total_delay_seconds": int(sum(all_delays)),
            "affected_trains_count": affected_count,
            "on_time_rate": 1.0 - (affected_count / len(self.trains)) if self.trains else 1.0
        }

        computation_time = time.time() - start_time

        return SolveResult(
            success=True,
            optimized_schedule=schedule,
            delay_statistics=delay_statistics,
            computation_time=computation_time,
            message="最大延误优先调度器：优先减少最大延误"
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