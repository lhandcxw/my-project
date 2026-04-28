# -*- coding: utf-8 -*-
"""
铁路调度系统 - NoOp（基线/无操作）调度器模块
不做任何调整，仅返回原始时刻表和初始延误

这是调度优化的基线/基准，用于对比其他调度算法的效果
"""

from typing import List, Dict, Any
import time
import logging

from solver.base import BaseSolver, SolveResult
from models.data_models import Train, Station, DelayInjection
from config import DispatchEnvConfig

logger = logging.getLogger(__name__)


class NoOpScheduler(BaseSolver):
    """
    基线调度器（No-Op）
    不做任何调整，仅返回原始时刻表和初始延误
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        **kwargs
    ):
        # NoOp 不需要 headway/min_stop_time，但保持接口统一
        super().__init__(trains, stations, **kwargs)

    def solve(
        self,
        delay_injection: DelayInjection,
        objective: str = "min_total_delay"
    ) -> SolveResult:
        """
        执行调度优化（NoOp不做任何优化）
        """
        start_time = time.time()

        # 获取原始时刻表
        schedule = self.get_original_schedule()

        # 应用初始延误（只影响注入站及后续，不做传播）
        all_delays = []
        for injected in delay_injection.injected_delays:
            train_id = injected.train_id
            initial_delay = injected.initial_delay_seconds

            if train_id in schedule:
                train_stops = schedule[train_id]
                injected_station = injected.location.station_code

                found_injected = False
                for stop in train_stops:
                    if stop["station_code"] == injected_station:
                        stop["delay_seconds"] = initial_delay
                        all_delays.append(initial_delay)
                        found_injected = True
                    elif found_injected:
                        stop["delay_seconds"] = initial_delay
                        all_delays.append(initial_delay)
                    else:
                        all_delays.append(0)

        # 统计实际有延误的列车数（与FCFS/MaxDelayFirst口径一致）
        final_affected_trains = set()
        for train_id, train_stops in schedule.items():
            for stop in train_stops:
                if stop.get("delay_seconds", 0) > 0:
                    final_affected_trains.add(train_id)
                    break

        # 计算延误统计
        max_delay_val = max(all_delays) if all_delays else 0
        # 【修复】avg_delay 使用受影响列车的平均最大延误，与 MetricsDefinition.calculate_metrics() 口径一致
        affected_train_max_delays = []
        for train_id in final_affected_trains:
            train_delays = [s.get("delay_seconds", 0) for s in schedule[train_id]]
            affected_train_max_delays.append(max(train_delays))
        avg_delay = sum(affected_train_max_delays) / len(affected_train_max_delays) if affected_train_max_delays else 0

        # 【修复】on_time_rate 与统一标准一致：基于每列车最大延误 < 准点阈值视为准点
        on_time_threshold = DispatchEnvConfig.on_time_threshold_seconds()
        train_max_delays = []
        for train_id, train_stops in schedule.items():
            delays = [s.get("delay_seconds", 0) for s in train_stops]
            train_max_delays.append(max(delays) if delays else 0)
        on_time_count = sum(1 for d in train_max_delays if d < on_time_threshold)
        on_time_rate = on_time_count / len(train_max_delays) if train_max_delays else 1.0

        delay_statistics = {
            "max_delay_seconds": int(max_delay_val),
            "avg_delay_seconds": float(avg_delay),
            "total_delay_seconds": int(sum(all_delays)),
            "affected_trains_count": len(final_affected_trains),
            "on_time_rate": float(on_time_rate),
        }

        computation_time = time.time() - start_time

        return SolveResult(
            success=True,
            optimized_schedule=schedule,
            delay_statistics=delay_statistics,
            computation_time=computation_time,
            message="基线调度器：仅应用初始延误，不做传播优化",
        )
