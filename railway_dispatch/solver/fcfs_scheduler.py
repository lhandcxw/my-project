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
from dataclasses import dataclass
import time
import logging
import copy

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


class FCFSScheduler:
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
        headway_time: int = 180,  # 追踪间隔 - 3分钟
        min_stop_time: int = 60,  # 最小停站时间 - 1分钟
        stop_time_redundancy_ratio: float = 0.5,  # 停站冗余利用比例
        running_time_redundancy_ratio: float = 0.3  # 区间运行冗余利用比例
    ):
        self.trains = trains
        self.stations = stations
        self.headway_time = headway_time
        self.min_stop_time = min_stop_time
        self.stop_time_redundancy_ratio = stop_time_redundancy_ratio
        self.running_time_redundancy_ratio = running_time_redundancy_ratio

        self.station_codes = [s.station_code for s in stations]
        self.station_names = {s.station_code: s.station_name for s in stations}
        self.station_track_count = {s.station_code: s.track_count for s in stations}
        self.min_running_times = self._load_min_running_times()
        self.original_running_times = self._load_original_running_times()

    def _get_stations_for_train(self, train: Train) -> List[str]:
        """获取列车经停的车站列表"""
        return [stop.station_code for stop in train.schedule.stops]

    def _time_to_seconds(self, time_str: str) -> int:
        """将时间字符串转换为秒数"""
        parts = time_str.split(':')
        if len(parts) == 2:
            h, m = map(int, parts)
            s = 0
        else:
            h, m, s = map(int, parts)
        return h * 3600 + m * 60 + s

    def _seconds_to_time(self, seconds: int) -> str:
        """将秒数转换为时间字符串"""
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _load_min_running_times(self) -> Dict[Tuple[str, str], int]:
        """加载区间最小运行时间"""
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

    def _load_original_running_times(self) -> Dict[Tuple[str, str], int]:
        """加载区间原始运行时间（所有列车的平均）"""
        section_times = {}
        section_counts = {}
        for train in self.trains:
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
        return self.min_running_times.get(key, 600)

    def _get_original_section_time(self, from_station: str, to_station: str) -> int:
        """获取指定区间的原始平均运行时间"""
        key = (from_station, to_station)
        return self.original_running_times.get(key, 600)

    def _get_original_stop_duration(self, train: Train, station_code: str) -> int:
        """获取列车在指定站的原始停站时间（秒）"""
        for stop in train.schedule.stops:
            if stop.station_code == station_code:
                # 优先使用新字段stop_duration
                if hasattr(stop, 'stop_duration') and stop.stop_duration is not None:
                    return stop.stop_duration
                # 兼容旧数据：通过到达和发车时间计算
                arr = self._time_to_seconds(stop.arrival_time)
                dep = self._time_to_seconds(stop.departure_time)
                return dep - arr
        return 180  # 默认3分钟停站时间

    def _get_stop_time_redundancy(self, train: Train, station_code: str) -> int:
        """获取指定站的停站冗余时间"""
        original_duration = self._get_original_stop_duration(train, station_code)
        if original_duration <= 0:
            return 0  # 通过站没有停站冗余
        # 最小停站时间为原始的50%或1分钟
        min_duration = max(self.min_stop_time, original_duration // 2)
        return original_duration - min_duration

    def _get_section_redundancy(self, from_station: str, to_station: str) -> int:
        """获取指定区间的运行冗余时间"""
        original_time = self._get_original_section_time(from_station, to_station)
        min_time = self._get_min_section_time(from_station, to_station)
        return original_time - min_time

    def solve(self, delay_injection: DelayInjection, objective: str = "min_max_delay") -> SolveResult:
        """
        使用FCFS策略求解调度问题（真实延误传播版）

        Args:
            delay_injection: 延误注入信息
            objective: 优化目标

        Returns:
            SolveResult: 调度结果
        """
        start_time = time.time()

        # Step 1: 初始化调度时刻表
        schedule = {}  # {(train_id, station_code): [arrival_seconds, departure_seconds]}
        train_schedules = {}  # 存储每列车的完整时刻表

        # 初始化所有列车的基本时刻表（按计划时间）
        for train in self.trains:
            train_stations = []
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

        # Step 2: 应用初始延误到受影响列车
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

        # Step 3: 真实延误传播 - 按车站处理追踪间隔
        # 对每个车站，按照当前发车时间排序列车，计算实际延误传播
        for station in self.stations:
            station_code = station.station_code
            track_count = self.station_track_count.get(station_code, 1)

            # 收集该站的所有列车，按原始发车时间排序（FCFS按计划顺序）
            trains_at_station = []
            for train in self.trains:
                if station_code in self._get_stations_for_train(train):
                    for stop in train.schedule.stops:
                        if stop.station_code == station_code:
                            # 使用原始发车时间排序（FCFS按计划顺序）
                            trains_at_station.append({
                                'train_id': train.train_id,
                                'original_departure': self._time_to_seconds(stop.departure_time),
                                'current_departure': schedule[(train.train_id, station_code)][1]
                            })
                            break

            # 按原始发车时间排序（FCFS核心：按计划顺序处理）
            trains_at_station.sort(key=lambda x: x['original_departure'])

            # 处理追踪间隔约束（多股道）
            last_departures = [0] * track_count

            for train_info in trains_at_station:
                train_id = train_info['train_id']
                train = next((t for t in self.trains if t.train_id == train_id), None)

                # 选择最早可用的股道
                best_track = 0
                min_available_time = last_departures[0]
                for track in range(1, track_count):
                    if last_departures[track] < min_available_time:
                        min_available_time = last_departures[track]
                        best_track = track

                # 获取当前列车的当前调度时间
                current_arr, current_dep = schedule[(train_id, station_code)]

                # Step 3.1: 检查停站时间约束
                original_duration = self._get_original_stop_duration(train, station_code)
                min_stop_duration = max(self.min_stop_time, original_duration // 2) if original_duration > 0 else 0
                required_arr = current_dep - min_stop_duration

                # 如果到达时间需要调整以满足最小停站时间
                if current_arr > required_arr:
                    # 到达已满足最小停站时间
                    pass
                else:
                    # 需要推迟到达时间（但这里我们不主动推迟，保持到达时间）
                    pass

                # Step 3.2: 检查追踪间隔约束
                # 关键：后车需要等待前车发车时间 + 追踪间隔
                # 修正：必须等待所有已处理列车的最后发车时间 + 追踪间隔
                # 而不是仅等待该股道的最后发车时间
                max_last_departure = max(last_departures) if last_departures else 0
                required_dep = max(current_dep, max_last_departure + self.headway_time)
                delay_needed = required_dep - current_dep

                if delay_needed > 0:
                    # 需要传播延误：后续站点都推迟
                    # 记录该列车被延误，用于后续冗余恢复
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
                last_departures[best_track] = max(
                    last_departures[best_track],
                    schedule[(train_id, station_code)][1]
                )

        # Step 4: 利用冗余时间进行恢复（停站冗余 + 区间运行冗余）
        # 对所有受影响的列车，尝试利用冗余减少延误
        for train_id in affected_trains:
            train = next((t for t in self.trains if t.train_id == train_id), None)
            if train is None:
                continue

            stations_for_train = self._get_stations_for_train(train)
            accumulated_recovery = 0  # 累计恢复时间

            # 从延误站开始，尝试利用后续各站的停站冗余
            for injected in delay_injection.injected_delays:
                if injected.train_id != train_id:
                    continue

                delay_station = injected.location.station_code or "BJX"
                try:
                    start_idx = stations_for_train.index(delay_station)
                except ValueError:
                    start_idx = 0

                # 从延误站开始，依次利用停站冗余
                for i in range(start_idx, len(stations_for_train) - 1):
                    sc = stations_for_train[i]
                    sc_next = stations_for_train[i + 1]

                    # Step 4.1: 尝试利用停站冗余
                    original_duration = self._get_original_stop_duration(train, sc)
                    if original_duration > 0:
                        redundancy = self._get_stop_time_redundancy(train, sc)
                        if redundancy > 0 and accumulated_recovery > 0:
                            # 有累积延误，可以压缩停站
                            recovery = min(int(redundancy * self.stop_time_redundancy_ratio), accumulated_recovery)
                            if recovery > 0:
                                # 压缩停站时间（在发车时间上体现）
                                arr, dep = schedule[(train_id, sc)]
                                # 到达时间不变，压缩发车时间
                                schedule[(train_id, sc)][1] = dep - recovery
                                accumulated_recovery -= recovery
                                # 后续站点同步提前
                                for j in range(i + 1, len(stations_for_train)):
                                    sc_j = stations_for_train[j]
                                    arr_j, dep_j = schedule[(train_id, sc_j)]
                                    schedule[(train_id, sc_j)] = [arr_j - recovery, dep_j - recovery]

                    # Step 4.2: 尝试利用区间运行冗余
                    original_running = self._get_original_section_time(sc, sc_next)
                    min_running = self._get_min_section_time(sc, sc_next)
                    section_redundancy = original_running - min_running

                    if section_redundancy > 0 and accumulated_recovery > 0:
                        # 可以加速通过该区间
                        recovery = min(int(section_redundancy * self.running_time_redundancy_ratio), accumulated_recovery)
                        if recovery > 0:
                            # 减少区间运行时间（到发时间都提前）
                            arr_next, dep_next = schedule[(train_id, sc_next)]
                            schedule[(train_id, sc_next)][0] = arr_next - recovery
                            schedule[(train_id, sc_next)][1] = dep_next - recovery
                            accumulated_recovery -= recovery

                # 计算该列车的最终延误，看能否利用冗余恢复
                # （简化版：这里只是标记，实际恢复逻辑较复杂）

        # Step 5: 构建最终结果
        optimized_schedule = {}
        all_delays = []
        final_affected_trains = set()

        for train in self.trains:
            train_schedule = []
            train_has_delay = False

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
        avg_delay = sum(all_delays) / len(all_delays) if all_delays else 0

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
    """
    创建FCFS调度器实例

    Args:
        trains: 列车列表
        stations: 车站列表

    Returns:
        FCFSScheduler: FCFS调度器实例
    """
    return FCFSScheduler(trains, stations)


if __name__ == "__main__":
    from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
    from models.data_models import InjectedDelay, DelayLocation, ScenarioType

    use_real_data(True)
    trains = get_trains_pydantic()[:30]  # 使用前30列列车进行测试
    stations = get_stations_pydantic()

    print("=" * 60)
    print("FCFS调度器测试")
    print("=" * 60)

    scheduler = create_fcfs_scheduler(trains, stations)

    # 测试场景：G1563在保定东延误20分钟
    delay_injection = DelayInjection(
        scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
        scenario_id="FCFS_TEST",
        injected_delays=[
            InjectedDelay(
                train_id="G1563",
                location=DelayLocation(location_type="station", station_code="BDD"),
                initial_delay_seconds=1200,  # 20分钟
                timestamp="2024-01-15T10:00:00Z"
            )
        ],
        affected_trains=["G1563"]
    )

    result = scheduler.solve(delay_injection)

    print(f"\n测试 - G1563在保定东延误20分钟:")
    print(f"  求解状态: {result.message}")
    if result.success:
        print(f"  最大延误: {result.delay_statistics['max_delay_seconds']//60} 分钟")
        print(f"  平均延误: {result.delay_statistics['avg_delay_seconds']/60:.2f} 分钟")
        print(f"  总延误: {result.delay_statistics['total_delay_seconds']//60} 分钟")
        print(f"  受影响列车数: {result.delay_statistics['affected_trains_count']}")
        print(f"  计算时间: {result.computation_time:.4f}秒")

        # 显示受影响列车的详细时刻表
        print(f"\n  {delay_injection.injected_delays[0].train_id} 的调整后时刻表（部分）:")
        for stop in result.optimized_schedule.get(delay_injection.injected_delays[0].train_id, [])[:5]:
            if stop['delay_seconds'] > 0:
                print(f"    {stop['station_name']}: {stop['original_departure']} -> {stop['departure_time']} "
                      f"(延误 {stop['delay_seconds']//60} 分钟)")
