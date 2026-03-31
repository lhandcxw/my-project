# -*- coding: utf-8 -*-
"""
铁路调度系统 - NoOp（基线/无操作）调度器模块
不做任何调整，仅返回原始时刻表和初始延误
这是调度优化的基线/基准
"""

from typing import List, Dict, Any, Optional
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


class NoOpScheduler:
    """
    基线调度器（No-Op）
    不做任何调整，仅返回原始时刻表和初始延误
    这是调度优化的基线/基准，用于对比其他调度算法的效果
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        **kwargs
    ):
        self.trains = trains
        self.stations = stations
        self.station_names = {s.station_code: s.station_name for s in stations}

    def solve(
        self,
        delay_injection: DelayInjection,
        objective: str = "min_max_delay"
    ) -> SolveResult:
        """
        执行调度优化（NoOp不做任何优化）

        Args:
            delay_injection: 延误注入信息
            objective: 优化目标（NoOp忽略此参数）

        Returns:
            SolveResult: 调度结果
        """
        start_time = time.time()

        # 获取原始时刻表
        schedule = self.get_original_schedule()

        # 收集所有延误信息
        all_delays = []

        # 应用初始延误（只影响注入站及后续，不做传播）
        for injected in delay_injection.injected_delays:
            train_id = injected.train_id
            initial_delay = injected.initial_delay_seconds

            if train_id in schedule:
                # 找到注入站在列车时刻表中的位置
                train_stops = schedule[train_id]
                injected_station = injected.location.station_code

                found_injected = False
                for stop in train_stops:
                    if stop["station_code"] == injected_station:
                        # 从该站开始应用延误
                        stop["delay_seconds"] = initial_delay
                        all_delays.append(initial_delay)
                        found_injected = True
                    elif found_injected:
                        # 注入站之后的站点也受影响
                        stop["delay_seconds"] = initial_delay
                        all_delays.append(initial_delay)
                    else:
                        # 注入站之前的站点无延误
                        all_delays.append(0)

        # 计算延误统计（修正：只统计有延误的站点）
        affected_delays = [d for d in all_delays if d > 0]
        max_delay_val = max(affected_delays) if affected_delays else 0
        avg_delay = sum(affected_delays) / len(affected_delays) if affected_delays else 0

        delay_statistics = {
            "max_delay_seconds": int(max_delay_val),
            "avg_delay_seconds": float(avg_delay),
            "total_delay_seconds": int(sum(all_delays)),
            "affected_trains_count": len(set(i.train_id for i in delay_injection.injected_delays)),
            "on_time_rate": 1.0 if max_delay_val == 0 else (1.0 - max_delay_val / 3600)
        }

        computation_time = time.time() - start_time

        return SolveResult(
            success=True,
            optimized_schedule=schedule,
            delay_statistics=delay_statistics,
            computation_time=computation_time,
            message="基线调度器：仅应用初始延误，不做传播优化"
        )

    def get_original_schedule(self) -> Dict[str, List[Dict]]:
        """获取原始时刻表"""
        schedule = {}
        for train in self.trains:
            stops = []
            for stop in train.schedule.stops:
                stops.append({
                    "station_code": stop.station_code,
                    "station_name": stop.station_name,
                    "arrival_time": stop.arrival_time,
                    "departure_time": stop.departure_time,
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
    scheduler = NoOpScheduler(trains, stations)

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
                initial_delay_seconds=300,  # 5分钟延误
                timestamp="2024-01-01T10:00:00"
            )
        ],
        affected_trains=["G1215"]
    )

    # 执行调度
    result = scheduler.solve(delay_injection)
    print(f"成功: {result.success}")
    print(f"消息: {result.message}")
    print(f"延误统计: {result.delay_statistics}")