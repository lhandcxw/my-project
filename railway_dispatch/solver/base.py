# -*- coding: utf-8 -*-
"""
求解器公共基类与工具模块
集中所有原始求解器（raw solver）的公共代码，消除重复
"""

from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass
import sys
import os

# 统一添加项目路径（避免每个文件重复 sys.path.append）
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from models.data_models import Train, Station, DelayInjection
from config import DispatchEnvConfig


@dataclass
class SolveResult:
    """统一的求解结果数据类"""
    success: bool
    optimized_schedule: Dict[str, List[Dict]]
    delay_statistics: Dict[str, Any]
    computation_time: float
    message: str = ""


class BaseSolver:
    """
    原始求解器公共基类

    提供所有求解器共用的基础设施：
    - 时间转换工具（时分秒 ↔ 秒）
    - 区间最小运行时间加载
    - 原始停站时间查询
    - 原始时刻表获取
    - 车站/列车基础索引构建
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        headway_time: Optional[int] = None,
        min_stop_time: Optional[int] = None,
        min_departure_interval: Optional[int] = None,
    ):
        self.trains = trains
        self.stations = stations
        self.headway_time = (
            headway_time
            if headway_time is not None
            else DispatchEnvConfig.headway_time()
        )
        self.min_stop_time = (
            min_stop_time
            if min_stop_time is not None
            else DispatchEnvConfig.min_stop_time()
        )
        self.min_departure_interval = (
            min_departure_interval
            if min_departure_interval is not None
            else DispatchEnvConfig.min_departure_interval()
        )

        # 通用索引（避免每个求解器重复构建）
        self.station_names = {s.station_code: s.station_name for s in stations}
        self.station_track_count = {s.station_code: s.track_count for s in stations}
        self.train_ids = [t.train_id for t in trains]
        self.station_codes = [s.station_code for s in stations]

        # 预加载区间最小运行时间
        self.min_running_times = self._load_min_running_times()

    # ------------------------------------------------------------------
    # 时间转换（静态方法，可直接类外调用）
    # ------------------------------------------------------------------
    @staticmethod
    def time_to_seconds(time_str: str) -> int:
        """将 HH:MM 或 HH:MM:SS 转换为秒数"""
        if not time_str:
            return 0
        parts = time_str.split(":")
        if len(parts) == 2:
            h, m = map(int, parts)
            return h * 3600 + m * 60
        elif len(parts) == 3:
            h, m, s = map(int, parts)
            return h * 3600 + m * 60 + s
        return 0

    @staticmethod
    def seconds_to_time(seconds: int) -> str:
        """将秒数转换为 HH:MM:SS"""
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    # 实例方法别名，保持旧代码兼容性
    _time_to_seconds = time_to_seconds
    _seconds_to_time = seconds_to_time

    # ------------------------------------------------------------------
    # 列车/车站辅助查询
    # ------------------------------------------------------------------
    def _get_stations_for_train(self, train: Train) -> List[str]:
        """获取列车经过的所有车站代码"""
        if (
            train.schedule
            and train.schedule.stops
            and isinstance(train.schedule.stops, (list, tuple))
        ):
            return [
                stop.station_code
                for stop in train.schedule.stops
                if hasattr(stop, "station_code")
            ]
        return []

    def _get_original_stop_duration(self, train: Train, station_code: str) -> int:
        """获取列车在指定车站的原始停站时间（秒）"""
        if not train.schedule or not train.schedule.stops:
            return DispatchEnvConfig.get("constraints.default_stop_time", 180)
        for stop in train.schedule.stops:
            if stop.station_code == station_code:
                if hasattr(stop, "stop_duration") and stop.stop_duration is not None:
                    return stop.stop_duration
                arr = self.time_to_seconds(stop.arrival_time)
                dep = self.time_to_seconds(stop.departure_time)
                return dep - arr
        return DispatchEnvConfig.get("constraints.default_stop_time", 180)

    # ------------------------------------------------------------------
    # 区间运行时间
    # ------------------------------------------------------------------
    def _load_min_running_times(self) -> Dict[Tuple[str, str], int]:
        """加载所有列车区间运行时间的最小值"""
        section_times: Dict[Tuple[str, str], int] = {}
        for train in self.trains:
            if (
                not train.schedule
                or not train.schedule.stops
                or not isinstance(train.schedule.stops, (list, tuple))
            ):
                continue
            stops = train.schedule.stops
            for i in range(len(stops) - 1):
                from_station = stops[i].station_code
                to_station = stops[i + 1].station_code
                from_dep = self.time_to_seconds(stops[i].departure_time)
                to_arr = self.time_to_seconds(stops[i + 1].arrival_time)
                running_time = to_arr - from_dep
                if running_time <= 0:
                    continue
                key = (from_station, to_station)
                if key not in section_times or running_time < section_times[key]:
                    section_times[key] = running_time
        return section_times

    def _get_min_section_time(self, from_station: str, to_station: str) -> int:
        """获取两站之间的最小区间运行时间"""
        return self.min_running_times.get(
            (from_station, to_station),
            DispatchEnvConfig.get("constraints.default_min_section_time", 600),
        )

    def _load_original_running_times(self) -> Dict[Tuple[str, str], int]:
        """加载区间原始运行时间（所有列车的平均值）"""
        section_times: Dict[Tuple[str, str], List[int]] = {}
        for train in self.trains:
            if (
                not train.schedule
                or not train.schedule.stops
                or not isinstance(train.schedule.stops, (list, tuple))
            ):
                continue
            stops = train.schedule.stops
            for i in range(len(stops) - 1):
                from_station = stops[i].station_code
                to_station = stops[i + 1].station_code
                from_dep = self.time_to_seconds(stops[i].departure_time)
                to_arr = self.time_to_seconds(stops[i + 1].arrival_time)
                running_time = to_arr - from_dep
                if running_time <= 0:
                    continue
                key = (from_station, to_station)
                section_times.setdefault(key, []).append(running_time)

        return {
            key: sum(values) // len(values)
            for key, values in section_times.items()
        }

    # ------------------------------------------------------------------
    # 原始时刻表
    # ------------------------------------------------------------------
    def get_original_schedule(self) -> Dict[str, List[Dict]]:
        """获取原始时刻表（所有 delay_seconds=0）"""
        schedule: Dict[str, List[Dict]] = {}
        for train in self.trains:
            stops = []
            if (
                train.schedule
                and train.schedule.stops
                and isinstance(train.schedule.stops, (list, tuple))
            ):
                for stop in train.schedule.stops:
                    if hasattr(stop, "station_code"):
                        stops.append(
                            {
                                "station_code": stop.station_code,
                                "station_name": getattr(
                                    stop, "station_name", stop.station_code
                                ),
                                "arrival_time": getattr(stop, "arrival_time", ""),
                                "departure_time": getattr(stop, "departure_time", ""),
                                "delay_seconds": 0,
                            }
                        )
            schedule[train.train_id] = stops
        return schedule
