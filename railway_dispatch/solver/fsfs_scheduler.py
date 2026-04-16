# -*- coding: utf-8 -*-
"""
铁路调度系统 - FSFS（先计划先服务）调度器模块
First-Scheduled-First-Served: 严格遵循原始运行图的计划顺序

专家视角的核心设计理念：
1. 计划顺序不可侵犯：严格按照原始运行图的计划发车/通过时间排序
2. 相对优先级固定：原计划先发的列车，调整后仍然先发
3. 越行关系不变：原计划被越行的列车，调整后仍然被越行
4. 停站方案不变：保持原计划的停站时间和停站模式
5. 整体时间平移：仅对受扰动列车做整体时间平移，不改变其内部结构

与FCFS的关键区别：
- FCFS：按实际到达顺序，可能改变原计划的发车顺序（快车可能超越慢车）
- FSFS：严格按计划顺序，保持原计划的相对优先级和越行关系
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


class FSFSScheduler:
    """
    先计划先服务调度器（First-Scheduled-First-Served）
    
    核心调度策略：
    1. 严格按计划顺序：按原始运行图的计划发车时间排序处理列车
    2. 保持相对优先级：原计划先发的列车永远先处理
    3. 保持越行关系：不改变原计划的列车超越关系
    4. 整体时间平移：受扰动列车整体平移，不改变停站方案和区间运行时分
    5. 延误传播：后续列车因追踪间隔被延误，但保持原计划顺序
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        headway_time: int = None,  # 追踪间隔
        min_stop_time: int = None,  # 最小停站时间
    ):
        # 使用配置文件中的默认值
        if headway_time is None:
            headway_time = DispatchEnvConfig.headway_time()
        if min_stop_time is None:
            min_stop_time = DispatchEnvConfig.min_stop_time()
            
        self.trains = trains
        self.stations = stations
        self.headway_time = headway_time
        self.min_stop_time = min_stop_time

        self.station_codes = [s.station_code for s in stations]
        self.station_names = {s.station_code: s.station_name for s in stations}
        self.station_track_count = {s.station_code: s.track_count for s in stations}
        
        # 预计算每个车站的计划发车顺序（FSFS核心：固定顺序）
        self._precompute_station_scheduled_order()

    def _precompute_station_scheduled_order(self):
        """
        预计算每个车站的计划发车顺序
        这是FSFS的核心：严格按照原始运行图的计划顺序
        """
        self.station_scheduled_order = {}  # {station_code: [(train_id, scheduled_departure_sec), ...]}
        
        for station in self.stations:
            station_code = station.station_code
            trains_at_station = []
            
            for train in self.trains:
                if not train.schedule or not train.schedule.stops:
                    continue
                    
                for stop in train.schedule.stops:
                    if stop.station_code == station_code:
                        # 使用原始计划发车时间
                        dep_sec = self._time_to_seconds(stop.departure_time)
                        trains_at_station.append({
                            'train_id': train.train_id,
                            'scheduled_departure': dep_sec,
                            'scheduled_arrival': self._time_to_seconds(stop.arrival_time)
                        })
                        break
            
            # 按计划发车时间排序（FSFS核心：计划顺序不可改变）
            trains_at_station.sort(key=lambda x: (x['scheduled_departure'], x['scheduled_arrival']))
            self.station_scheduled_order[station_code] = trains_at_station
            
            logger.debug(f"[FSFS] 车站 {station_code} 的计划顺序: "
                        f"{[t['train_id'] for t in trains_at_station]}")

    def _get_stations_for_train(self, train: Train) -> List[str]:
        """获取列车经停的车站列表"""
        if not train.schedule or not train.schedule.stops or not isinstance(train.schedule.stops, (list, tuple)):
            return []
        return [stop.station_code for stop in train.schedule.stops if hasattr(stop, 'station_code')]

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

    def _get_original_stop_duration(self, train: Train, station_code: str) -> int:
        """获取列车在指定站的原始停站时间（秒）"""
        if not train.schedule or not train.schedule.stops or not isinstance(train.schedule.stops, (list, tuple)):
            return 0
        for stop in train.schedule.stops:
            if stop.station_code == station_code:
                arr = self._time_to_seconds(stop.arrival_time)
                dep = self._time_to_seconds(stop.departure_time)
                return dep - arr
        return 0

    def _get_original_interval_time(self, train: Train, from_station: str, to_station: str) -> int:
        """获取列车在两站之间的原始区间运行时间（秒）"""
        if not train.schedule or not train.schedule.stops:
            return 0
            
        stops = train.schedule.stops
        for i in range(len(stops) - 1):
            if stops[i].station_code == from_station and stops[i+1].station_code == to_station:
                dep = self._time_to_seconds(stops[i].departure_time)
                arr = self._time_to_seconds(stops[i+1].arrival_time)
                return arr - dep
        return 0

    def solve(self, delay_injection: DelayInjection, objective: str = "min_max_delay") -> SolveResult:
        """
        使用FSFS策略求解调度问题
        
        核心逻辑：
        1. 严格按计划顺序处理列车
        2. 仅对受扰动列车做整体时间平移
        3. 保持原计划的停站方案和区间运行时分
        4. 保持原计划的相对优先级和越行关系
        
        Args:
            delay_injection: 延误注入信息
            objective: 优化目标
            
        Returns:
            SolveResult: 调度结果
        """
        start_time = time.time()

        # Step 1: 初始化调度时刻表（复制原始计划）
        schedule = {}  # {(train_id, station_code): [arrival_seconds, departure_seconds]}
        
        for train in self.trains:
            if not train.schedule or not train.schedule.stops:
                continue
            for stop in train.schedule.stops:
                station_code = stop.station_code
                arr_sec = self._time_to_seconds(stop.arrival_time)
                dep_sec = self._time_to_seconds(stop.departure_time)
                schedule[(train.train_id, station_code)] = [arr_sec, dep_sec]

        # Step 2: 识别受扰动列车并应用初始延误
        affected_trains = set()  # 被初始延误直接影响的列车
        train_total_delays = {}  # {train_id: total_delay_seconds} 每列车的整体平移量
        
        for injected in delay_injection.injected_delays:
            train_id = injected.train_id
            station_code = injected.location.station_code or "BJX"
            initial_delay = injected.initial_delay_seconds
            
            if (train_id, station_code) in schedule:
                affected_trains.add(train_id)
                
                # 计算该列车从延误站开始的整体平移量
                train = next((t for t in self.trains if t.train_id == train_id), None)
                if train:
                    stations_for_train = self._get_stations_for_train(train)
                    try:
                        idx = stations_for_train.index(station_code)
                        # 记录该列车的整体延误量
                        if train_id not in train_total_delays:
                            train_total_delays[train_id] = {}
                        
                        # 从延误站开始，所有后续站点都加上延误（整体平移）
                        for i in range(idx, len(stations_for_train)):
                            sc = stations_for_train[i]
                            arr, dep = schedule[(train_id, sc)]
                            schedule[(train_id, sc)] = [arr + initial_delay, dep + initial_delay]
                            train_total_delays[train_id][sc] = initial_delay
                            
                    except ValueError:
                        logger.warning(f"[FSFS] 车站 {station_code} 不在列车 {train_id} 的路线中")

        # Step 3: FSFS核心 - 按计划顺序处理追踪间隔
        # 关键：严格按照预计算的计划顺序处理，不因实际延误而改变顺序
        for station in self.stations:
            station_code = station.station_code
            track_count = self.station_track_count.get(station_code, 1)
            
            # 跳过线路所（股道数为0表示不能停站）
            if track_count == 0:
                logger.debug(f"[FSFS] 跳过线路所 {station_code}")
                continue
            
            # 获取该站的计划顺序（FSFS核心：固定顺序）
            scheduled_order = self.station_scheduled_order.get(station_code, [])
            if not scheduled_order:
                continue
            
            # 按计划顺序处理每列车的追踪间隔
            # 使用多股道：每股道维护一个最后发车时间
            track_last_departures = [0] * track_count
            
            for idx, train_info in enumerate(scheduled_order):
                train_id = train_info['train_id']
                scheduled_departure = train_info['scheduled_departure']
                
                # 获取当前列车的当前调度时间
                if (train_id, station_code) not in schedule:
                    continue
                    
                current_arr, current_dep = schedule[(train_id, station_code)]
                
                # 按计划顺序分配股道（保持原计划的股道分配逻辑）
                assigned_track = idx % track_count
                
                # 检查追踪间隔约束
                # FSFS：后车必须等待前车（按计划顺序的前车）
                last_dep_on_track = track_last_departures[assigned_track]
                required_dep = max(current_dep, last_dep_on_track + self.headway_time)
                delay_needed = required_dep - current_dep
                
                if delay_needed > 0:
                    # 需要传播延误：对该列车从当前站开始做整体平移
                    train = next((t for t in self.trains if t.train_id == train_id), None)
                    if train:
                        stations_for_train = self._get_stations_for_train(train)
                        try:
                            station_idx = stations_for_train.index(station_code)
                            # 整体平移：后续所有站点统一加上延误量
                            for i in range(station_idx, len(stations_for_train)):
                                sc = stations_for_train[i]
                                if (train_id, sc) in schedule:
                                    arr, dep = schedule[(train_id, sc)]
                                    schedule[(train_id, sc)] = [arr + delay_needed, dep + delay_needed]
                                    
                                    # 记录延误传播
                                    if train_id not in train_total_delays:
                                        train_total_delays[train_id] = {}
                                    current_delay = train_total_delays[train_id].get(sc, 0)
                                    train_total_delays[train_id][sc] = current_delay + delay_needed
                                    
                        except ValueError:
                            pass
                
                # 更新该股道的最后发车时间
                updated_dep = schedule[(train_id, station_code)][1]
                track_last_departures[assigned_track] = updated_dep

        # Step 4: FSFS约束检查 - 确保停站方案和区间运行时分不变
        # 这是FSFS的核心约束：保持原计划的内部结构
        for train_id, delays_by_station in train_total_delays.items():
            train = next((t for t in self.trains if t.train_id == train_id), None)
            if not train:
                continue
                
            stations_for_train = self._get_stations_for_train(train)
            
            # 检查并强制保持停站时间不变
            for i, station_code in enumerate(stations_for_train):
                if (train_id, station_code) not in schedule:
                    continue
                    
                original_stop_duration = self._get_original_stop_duration(train, station_code)
                if original_stop_duration <= 0:
                    continue  # 通过站不停
                    
                current_arr, current_dep = schedule[(train_id, station_code)]
                current_stop_duration = current_dep - current_arr
                
                # FSFS约束：停站时间必须等于原计划
                if current_stop_duration != original_stop_duration:
                    # 调整到达时间以保持停站时间不变
                    new_arr = current_dep - original_stop_duration
                    schedule[(train_id, station_code)][0] = new_arr
                    
                    # 如果需要，调整前一站的到达时间（但不改变区间运行时分）
                    if i > 0:
                        prev_station = stations_for_train[i - 1]
                        if (train_id, prev_station) in schedule:
                            prev_arr, prev_dep = schedule[(train_id, prev_station)]
                            # 保持区间运行时分不变
                            original_interval = self._get_original_interval_time(train, prev_station, station_code)
                            if original_interval > 0:
                                required_prev_dep = new_arr - original_interval
                                if required_prev_dep >= prev_arr:
                                    schedule[(train_id, prev_station)][1] = required_prev_dep

        # Step 5: 构建最终结果
        optimized_schedule = {}
        all_delays = []
        final_affected_trains = set()

        for train in self.trains:
            train_schedule = []
            train_has_delay = False

            if not train.schedule or not train.schedule.stops:
                continue
                
            for stop in train.schedule.stops:
                station_code = stop.station_code
                if (train.train_id, station_code) not in schedule:
                    continue
                    
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

        # 构建详细消息
        message = "FSFS调度成功（严格按计划顺序）"
        if affected_trains:
            message += f"\n初始延误列车: {', '.join(affected_trains)}"
        propagated_trains = final_affected_trains - affected_trains
        if propagated_trains:
            message += f"\n延误传播列车: {', '.join(propagated_trains)}"

        return SolveResult(
            success=True,
            optimized_schedule=optimized_schedule,
            delay_statistics={
                "max_delay_seconds": int(max_delay_val),
                "avg_delay_seconds": float(avg_delay),
                "total_delay_seconds": int(sum(all_delays)),
                "affected_trains_count": len(final_affected_trains),
                "initial_affected_count": len(affected_trains),
                "propagated_count": len(propagated_trains)
            },
            computation_time=time.time() - start_time,
            message=message
        )


def create_fsfs_scheduler(trains: List[Train], stations: List[Station]) -> FSFSScheduler:
    """
    创建FSFS调度器实例

    Args:
        trains: 列车列表
        stations: 车站列表

    Returns:
        FSFSScheduler: FSFS调度器实例
    """
    return FSFSScheduler(trains, stations)


if __name__ == "__main__":
    from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
    from models.data_models import InjectedDelay, DelayLocation, ScenarioType

    use_real_data(True)
    trains = get_trains_pydantic()[:30]
    stations = get_stations_pydantic()

    print("=" * 70)
    print("FSFS调度器测试 - 先计划先服务（严格按计划顺序）")
    print("=" * 70)
    print("\n核心特性：")
    print("1. 严格按计划发车顺序处理列车")
    print("2. 保持原计划的相对优先级和越行关系")
    print("3. 仅对受扰动列车做整体时间平移")
    print("4. 不改变原计划的停站方案和区间运行时分")
    print("=" * 70)

    scheduler = create_fsfs_scheduler(trains, stations)

    # 测试场景：G1563在保定东延误20分钟
    delay_injection = DelayInjection(
        scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
        scenario_id="FSFS_TEST",
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

    print(f"\n测试场景 - G1563在保定东延误20分钟:")
    print(f"{'='*70}")
    print(f"求解状态: {result.message}")
    if result.success:
        print(f"\n延误统计:")
        print(f"  最大延误: {result.delay_statistics['max_delay_seconds']//60} 分钟")
        print(f"  平均延误: {result.delay_statistics['avg_delay_seconds']/60:.2f} 分钟")
        print(f"  总延误: {result.delay_statistics['total_delay_seconds']//60} 分钟")
        print(f"  受影响列车总数: {result.delay_statistics['affected_trains_count']}")
        print(f"  初始延误列车: {result.delay_statistics.get('initial_affected_count', 0)}")
        print(f"  延误传播列车: {result.delay_statistics.get('propagated_count', 0)}")
        print(f"  计算时间: {result.computation_time:.4f}秒")

        # 显示G1563的详细时刻表
        print(f"\nG1563 的调整后时刻表:")
        print(f"{'-'*70}")
        for stop in result.optimized_schedule.get("G1563", []):
            delay_info = f"(延误 {stop['delay_seconds']//60} 分钟)" if stop['delay_seconds'] > 0 else "(准点)"
            print(f"  {stop['station_name']:8s}: 到达 {stop['original_arrival']} -> {stop['arrival_time']}, "
                  f"发车 {stop['original_departure']} -> {stop['departure_time']} {delay_info}")

    print(f"\n{'='*70}")
    print("与FCFS的关键区别对比:")
    print("-" * 70)
    print("FCFS (先到先服务):")
    print("  - 按实际到达/通过顺序调度")
    print("  - 允许快车超越慢车（改变原计划顺序）")
    print("  - 可能改变原计划的越行关系")
    print()
    print("FSFS (先计划先服务):")
    print("  - 严格按计划发车顺序调度")
    print("  - 保持原计划的相对优先级")
    print("  - 保持原计划的越行关系不变")
    print("  - 仅做整体时间平移，不改变内部结构")
    print("=" * 70)
