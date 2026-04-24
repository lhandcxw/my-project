# -*- coding: utf-8 -*-
"""
铁路调度系统 - 整数规划求解器模块（修正版）
解决了原版中的约束冲突问题，符合实际高铁运营规则
"""

from typing import List, Dict, Tuple, Optional, Any
from pulp import (
    LpProblem, LpVariable, LpMinimize,
    lpSum, LpStatus, value
)
from dataclasses import dataclass
import time
import logging

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


class MIPScheduler:
    """
    修正版混合整数规划调度器
    主要修正:
    1. 取消区间运行时间上界，允许延误传播
    2. 只对非受影响列车的第一站约束延误为0
    3. 允许停站时间在合理范围内调整
    4. 追踪间隔调整为3分钟（符合高铁标准）
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        headway_time: int = None,  # 追踪间隔 - 从配置读取
        min_stop_time: int = None,   # 最小停站时间 - 从配置读取
        min_headway_time: int = None  # 最小安全间隔 - 从配置读取
    ):
        # 从统一配置加载默认值
        if headway_time is None:
            headway_time = DispatchEnvConfig.headway_time()
        if min_stop_time is None:
            min_stop_time = DispatchEnvConfig.min_stop_time()
        if min_headway_time is None:
            min_headway_time = DispatchEnvConfig.min_headway_time()

        self.trains = trains
        self.stations = stations
        self.headway_time = headway_time
        self.min_stop_time = min_stop_time
        self.min_headway_time = min_headway_time

        self.station_codes = [s.station_code for s in stations]
        self.station_names = {s.station_code: s.station_name for s in stations}
        self.train_ids = [t.train_id for t in trains]
        self.station_track_count = {s.station_code: s.track_count for s in stations}
        self.min_running_times = self._load_min_running_times()

    def _get_stations_for_train(self, train: Train) -> List[str]:
        if train.schedule and train.schedule.stops and isinstance(train.schedule.stops, (list, tuple)):
            return [stop.station_code for stop in train.schedule.stops if hasattr(stop, 'station_code')]
        return []

    def _time_to_seconds(self, time_str: str) -> int:
        parts = time_str.split(':')
        if len(parts) == 2:
            h, m = map(int, parts)
            s = 0
        else:
            h, m, s = map(int, parts)
        return h * 3600 + m * 60 + s

    def _seconds_to_time(self, seconds: int) -> str:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _load_min_running_times(self) -> Dict[Tuple[str, str], int]:
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

    def _get_min_section_time(self, from_station: str, to_station: str) -> int:
        key = (from_station, to_station)
        # 从配置读取默认区间运行时间（默认10分钟）
        default_section_time = DispatchEnvConfig.get("defaults.section_running_time", 600)
        return self.min_running_times.get(key, default_section_time)

    def _get_original_stop_duration(self, train: Train, station_code: str) -> int:
        """获取列车在指定站的原始停站时间（秒）"""
        if not train.schedule or not train.schedule.stops or not isinstance(train.schedule.stops, (list, tuple)):
            return 180  # 默认3分钟停站时间
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

    def solve(self, delay_injection: DelayInjection, objective: str = "min_total_delay", solver_config: Dict = None) -> SolveResult:
        """
        求解调度优化问题

        Args:
            delay_injection: 延误注入
            objective: 优化目标（已废弃，使用solver_config.optimization_objective）
            solver_config: L2智能决策传递的求解器配置
                - time_limit: 求解时间上限（秒）
                - optimality_gap: 最优性间隙
                - optimization_objective: 优化目标
        """
        start_time = time.time()
        prob = LpProblem("RailwayDispatch", LpMinimize)

        # 决策变量
        arrival = LpVariable.dicts(
            "arrival",
            [(t.train_id, s.station_code)
             for t in self.trains
             for s in self.stations
             if s.station_code in self._get_stations_for_train(t)],
            lowBound=0, cat='Integer'
        )

        departure = LpVariable.dicts(
            "departure",
            [(t.train_id, s.station_code)
             for t in self.trains
             for s in self.stations
             if s.station_code in self._get_stations_for_train(t)],
            lowBound=0, cat='Integer'
        )

        # 【专家修复】区分到达延误和发车延误
        # 高铁调度中，到达延误和发车延误可能不同，需要分别计算
        delay_arrival = LpVariable.dicts(
            "delay_arrival",
            [(t.train_id, s.station_code)
             for t in self.trains
             for s in self.stations
             if s.station_code in self._get_stations_for_train(t)],
            lowBound=0, cat='Integer'
        )

        delay_departure = LpVariable.dicts(
            "delay_departure",
            [(t.train_id, s.station_code)
             for t in self.trains
             for s in self.stations
             if s.station_code in self._get_stations_for_train(t)],
            lowBound=0, cat='Integer'
        )

        max_delay = LpVariable("max_delay", lowBound=0, cat='Integer')

        # 解析solver_config（L2智能决策传递的参数）
        solver_config = solver_config or {}
        optimization_objective = solver_config.get("optimization_objective", objective)

        # 目标函数（使用L2推荐的优化目标）
        # 【专家修复】使用到达延误和发车延误的最大值作为优化目标
        if optimization_objective == "min_max_delay":
            prob += max_delay
        elif optimization_objective == "min_total_delay":
            # 【专家修复】总延误 = 到达延误总和 + 发车延误总和
            prob += lpSum([
                delay_arrival[t.train_id, s.station_code] +
                delay_departure[t.train_id, s.station_code]
                for t in self.trains
                for s in self.stations
                if s.station_code in self._get_stations_for_train(t)
            ])
        elif optimization_objective == "min_avg_delay":
            # 平均延误 = 总延误 / 列车数，最小化平均延误等价于最小化总延误
            prob += lpSum([
                delay_arrival[t.train_id, s.station_code] +
                delay_departure[t.train_id, s.station_code]
                for t in self.trains
                for s in self.stations
                if s.station_code in self._get_stations_for_train(t)
            ])
        else:
            # 默认使用最大延误作为目标
            prob += max_delay

        # =========================================
        # 约束条件（修正版）
        # =========================================

        # 收集受影响的列车信息
        affected_trains = set()
        injected_stations = {}  # {(train_id, station_code): initial_delay}
        invalid_train_ids = []  # 记录无效的列车ID

        # 1. 初始延误约束（修正：约束到达时间，允许MIP压缩停站/区间时间恢复）
        # 原理：初始延误是"列车晚到"，到达时间必须晚于计划+初始延误。
        # 但发车时间 = 到达 + 停站时间，MIP可通过压缩停站时间和区间运行时间
        # 来减少发车延误，与FCFS/MaxDelayFirst的冗余恢复机制对齐。
        for injected in delay_injection.injected_delays:
            train_id = injected.train_id
            station_code = injected.location.station_code or "BJX"
            initial_delay = injected.initial_delay_seconds

            # 验证列车ID是否存在
            train = next((t for t in self.trains if t.train_id == train_id), None)
            if train is None:
                logger.warning(f"列车ID {train_id} 不在时刻表数据中，跳过此列车")
                invalid_train_ids.append(train_id)
                continue

            affected_trains.add(train_id)
            injected_stations[(train_id, station_code)] = initial_delay

            if station_code in self.station_codes and train.schedule and train.schedule.stops and isinstance(train.schedule.stops, (list, tuple)):
                found_station = False
                for stop in train.schedule.stops:
                    if stop.station_code == station_code:
                        scheduled_arr = self._time_to_seconds(stop.arrival_time)
                        # 【关键修复】约束到达时间 >= 计划到达 + 初始延误
                        # 这样发车时间由到达+停站决定，MIP可压缩停站时间恢复部分延误
                        prob += arrival[train_id, station_code] >= scheduled_arr + initial_delay
                        found_station = True
                        logger.debug(f"[MIP] 添加初始到达延误约束: {train_id} 在 {station_code} 到达延误 {initial_delay}秒")
                        break

                if not found_station:
                    # 列车不经过该车站，记录警告但不跳过
                    logger.warning(f"[MIP] 列车 {train_id} 不经过车站 {station_code}，跳过延误约束")
                    affected_trains.discard(train_id)
                    if (train_id, station_code) in injected_stations:
                        del injected_stations[(train_id, station_code)]

        # 检查是否有有效的受影响列车
        if not affected_trains:
            if invalid_train_ids:
                error_msg = f"所有受影响的列车ID {invalid_train_ids} 都不在时刻表数据中"
            else:
                error_msg = "没有指定受影响的列车"
            logger.error(error_msg)
            return SolveResult(
                success=False,
                optimized_schedule={},
                delay_statistics={},
                computation_time=time.time() - start_time,
                message=error_msg
            )

# 2. 区间运行时间约束（修正：允许压缩，但要有下限）
        # 高铁可以在区间内适当提速或压缩运行时间，但不能低于最小安全运行时间
        station_codes_set = set(self.station_codes)  # MIP窗口内的车站
        for t in self.trains:
            train_stations = self._get_stations_for_train(t)
            for i in range(len(train_stations) - 1):
                from_station = train_stations[i]
                to_station = train_stations[i + 1]

                # 【关键修复】只处理窗口内的站点对
                if from_station not in station_codes_set or to_station not in station_codes_set:
                    continue
                
                # 如果下一站是线路所，跳过
                to_track_count = self.station_track_count.get(to_station, 1)
                if to_track_count == 0:
                    continue

                min_time = self._get_min_section_time(from_station, to_station)
                min_safe_time = int(min_time * 0.8)
                prob += arrival[t.train_id, to_station] - departure[t.train_id, from_station] >= min_safe_time

        # 3. 追踪间隔约束（所有车站）
        # 修正：无论是单股道还是多股道，都需要添加追踪间隔约束
        # 多股道车站使用简化模型：按原始顺序依次发车（避免冲突）
        # 注意：线路所(node_type=line_post, track_count=0)不停站，跳过约束
        for s in self.stations:
            station_code = s.station_code
            track_count = self.station_track_count.get(station_code, 1)

            # 跳过线路所（track_count=0表示线路所，不停站）
            if track_count == 0:
                logger.debug(f"[MIP] 跳过线路所 {station_code} 的追踪间隔约束（不停站）")
                continue

            # 收集该站所有列车，按原始发车时间排序
            trains_at_station = [t for t in self.trains if station_code in self._get_stations_for_train(t)]

            trains_with_time = []
            for t in trains_at_station:
                if not t.schedule or not t.schedule.stops or not isinstance(t.schedule.stops, (list, tuple)):
                    continue
                for stop in t.schedule.stops:
                    if stop.station_code == station_code:
                        trains_with_time.append((t, self._time_to_seconds(stop.departure_time)))
                        break
            trains_with_time.sort(key=lambda x: x[1])

            # 建立追踪间隔约束
            # 核心修复：只对同一股道的列车添加追踪间隔约束
            # 原因：多股道大站（如11-track SJP）不同股道的列车可并行作业，
            # 硬性的全局追踪间隔会导致不必要的延误传播
            for i in range(len(trains_with_time) - 1):
                t1, _ = trains_with_time[i]
                t2, _ = trains_with_time[i + 1]

                if track_count == 1:
                    # 单股道：严格追踪间隔
                    prob += departure[t2.train_id, station_code] >= departure[t1.train_id, station_code] + self.headway_time
                else:
                    # 【关键修复】只对同一股道的列车添加追踪间隔约束
                    # 按原始发车顺序静态分配股道，与FCFS保持一致
                    track_i = i % track_count
                    track_j = (i + 1) % track_count

                    if track_i == track_j:
                        # 同一股道，需满足追踪间隔（180秒）
                        prob += departure[t2.train_id, station_code] >= departure[t1.train_id, station_code] + self.headway_time
                    # 不同股道：不添加硬性追踪间隔约束
                    # 大型车站不同股道的列车可在咽喉区并行作业

        # 5. 第一站到达时间约束（修正：允许受影响列车在注入站延误）
        for t in self.trains:
            if not t.schedule or not t.schedule.stops or not isinstance(t.schedule.stops, (list, tuple)):
                continue

            train_stations = self._get_stations_for_train(t)
            if not train_stations:
                continue

            first_station = train_stations[0]
            first_stop = t.schedule.stops[0]
            scheduled_arr = self._time_to_seconds(first_stop.arrival_time)

            # 检查该列车是否有任何延误注入
            has_injection = any(inj_train_id == t.train_id for inj_train_id, _ in injected_stations.keys())

            # 如果第一站不是延误注入站且列车没有受影响，才约束
            if not has_injection and (t.train_id, first_station) not in injected_stations:
                prob += arrival[t.train_id, first_station] >= scheduled_arr
            else:
                # 列车受影响，允许在第一站延误
                logger.debug(f"[MIP] {t.train_id} 受影响，允许在第一站 {first_station} 延误")

# 6. 停站时间约束（专家修复版：更合理的最小停站时间计算）
        station_codes_set = set(self.station_codes)  # MIP窗口内的车站
        for t in self.trains:
            if not t.schedule or not t.schedule.stops:
                continue
            for stop in t.schedule.stops:
                station_code = stop.station_code

                # 【关键修复】只处理窗口内的车站
                if station_code not in station_codes_set:
                    continue

                original_duration = self._get_original_stop_duration(t, station_code)

                # 通过站（原计划停站时间为0）允许0停站时间
                if original_duration == 0:
                    # 通过站：到达时间等于发车时间（允许0停站）
                    prob += departure[t.train_id, station_code] >= arrival[t.train_id, station_code]
                else:
                    # 【修复】最小停站时间统一使用绝对最小值，与FCFS/MaxDelayFirst一致
                    # 原因：MIP需要与启发式算法在同一起跑线对比，不应额外收紧约束
                    min_stop = self.min_stop_time
                    prob += departure[t.train_id, station_code] - arrival[t.train_id, station_code] >= min_stop

# 7. 发车时间约束（专家修复：禁止提前发车）
        # 【专家修复】高铁实际运营中不允许提前发车
        # 原因：提前发车可能引发安全风险，且不符合运营规范
        station_codes_set = set(self.station_codes)  # MIP窗口内的车站
        for t in self.trains:
            if not t.schedule or not t.schedule.stops:
                continue
            for stop in t.schedule.stops:
                station_code = stop.station_code

                # 【关键修复】只处理窗口内的车站
                if station_code not in station_codes_set:
                    continue

                scheduled_dep = self._time_to_seconds(stop.departure_time)
                # 【修复】不允许提前发车
                prob += departure[t.train_id, station_code] >= scheduled_dep

        # 8. 到达时间约束（专家修复：禁止提前到站）
        # 【专家修复】同样不允许提前到站
        for t in self.trains:
            if not t.schedule or not t.schedule.stops:
                continue
            for stop in t.schedule.stops:
                station_code = stop.station_code

                # 【关键修复】只处理窗口内的车站
                if station_code not in station_codes_set:
                    continue

                scheduled_arr = self._time_to_seconds(stop.arrival_time)
                # 【修复】不允许提前到站
                prob += arrival[t.train_id, station_code] >= scheduled_arr

# 9. 延误计算约束（专家修复：区分到达延误和发车延误）
        station_codes_set = set(self.station_codes)  # MIP窗口内的车站
        for t in self.trains:
            if not t.schedule or not t.schedule.stops:
                continue

            train_id = t.train_id
            is_affected = train_id in affected_trains
            train_stations = self._get_stations_for_train(t)

            for stop in t.schedule.stops:
                station_code = stop.station_code

                # 【关键修复】只处理窗口内的车站
                if station_code not in station_codes_set:
                    continue

                scheduled_arr = self._time_to_seconds(stop.arrival_time)
                scheduled_dep = self._time_to_seconds(stop.departure_time)

                # 【专家修复】到达延误 = 实际到达 - 计划到达
                prob += delay_arrival[train_id, station_code] >= arrival[train_id, station_code] - scheduled_arr
                prob += delay_arrival[train_id, station_code] >= 0
                prob += max_delay >= delay_arrival[train_id, station_code]

                # 【专家修复】发车延误 = 实际发车 - 计划发车
                prob += delay_departure[train_id, station_code] >= departure[train_id, station_code] - scheduled_dep
                prob += delay_departure[train_id, station_code] >= 0
                prob += max_delay >= delay_departure[train_id, station_code]

                # 【修复】不再强制约束非受影响列车的第一站延误为0
                # 原因：在多列车高密度场景下，headway约束可能迫使非受影响列车
                # 在第一站就产生传播延误。强制延误为0会导致约束冲突（Infeasible）。
                # if not is_affected:
                #     if train_stations and station_code == train_stations[0]:
                #         prob += delay_arrival[train_id, station_code] <= 0
                #         prob += delay_departure[train_id, station_code] <= 0

        # 求解（优先使用L2智能决策的参数，否则使用配置文件默认值）
        from pulp import PULP_CBC_CMD

        # 1. 优先使用L2推荐的参数
        if solver_config:
            max_solve_time = solver_config.get("time_limit")
            optimality_gap = solver_config.get("optimality_gap")
            optimization_objective = solver_config.get("optimization_objective", optimization_objective)

            # 参数安全校验（确保在合理范围内）
            if max_solve_time is not None:
                try:
                    max_solve_time = int(max_solve_time)
                    max_solve_time = max(30, min(600, max_solve_time))
                except (ValueError, TypeError):
                    max_solve_time = DispatchEnvConfig.solver_time_limit()
            else:
                max_solve_time = DispatchEnvConfig.solver_time_limit()

            if optimality_gap is not None:
                try:
                    optimality_gap = float(optimality_gap)
                    optimality_gap = max(0.01, min(0.1, round(optimality_gap, 2)))
                except (ValueError, TypeError):
                    optimality_gap = DispatchEnvConfig.solver_optimality_gap()
            else:
                optimality_gap = DispatchEnvConfig.solver_optimality_gap()

            logger.info(f"[MIP] 使用L2智能决策参数: time_limit={max_solve_time}s, optimality_gap={optimality_gap}, objective={optimization_objective}")
        else:
            # 2. 使用配置文件默认值
            max_solve_time = DispatchEnvConfig.solver_time_limit()
            optimality_gap = DispatchEnvConfig.solver_optimality_gap()
            logger.info(f"[MIP] 使用配置默认参数: time_limit={max_solve_time}s, optimality_gap={optimality_gap}")

        solver = PULP_CBC_CMD(msg=False, timeLimit=max_solve_time, gapRel=optimality_gap)
        prob.solve(solver)

        # 检查求解状态
        status = LpStatus[prob.status]
        if status != 'Optimal':
            # 如果不是最优解，但找到了可行解，也可以接受
            if status == 'Not Solved' or status == 'Undefined':
                logger.warning(f"[MIP] 求解器未能在{max_solve_time}秒内找到解，状态: {status}")
                return SolveResult(
                    success=False,
                    optimized_schedule={},
                    delay_statistics={},
                    computation_time=time.time() - start_time,
                    message=f"求解超时或失败: {status}（可能因规模过大或约束冲突）"
                )
            elif status == 'Infeasible':
                logger.warning(f"[MIP] 问题不可行，可能是约束冲突或车站代码不匹配")
                return SolveResult(
                    success=False,
                    optimized_schedule={},
                    delay_statistics={},
                    computation_time=time.time() - start_time,
message=f"求解失败: {status}（约束冲突，建议使用其他调度器）"
                )
            else:
                logger.debug(f"[MIP] 求解状态: {status}，尝试解析结果")
                # 对于其他状态，继续解析，但设置success为False
                try:
                    # 解析结果
                    optimized_schedule = {}
                    all_delays = []
                    final_affected_trains = set()

                    for t in self.trains:
                        train_schedule = []
                        train_has_delay = False
                        if not t.schedule or not t.schedule.stops or not isinstance(t.schedule.stops, (list, tuple)):
                            continue

                        for stop in t.schedule.stops:
                            station_code = stop.station_code
                            scheduled_arr = self._time_to_seconds(stop.arrival_time)
                            scheduled_dep = self._time_to_seconds(stop.departure_time)

                            try:
                                # 检查变量是否存在
                                arr_key = (t.train_id, station_code)
                                dep_key = (t.train_id, station_code)
                                if arr_key not in arrival or dep_key not in departure:
                                    # 变量不存在（车站不在列车时刻表中），跳过
                                    continue

                                opt_arr = int(value(arrival[arr_key]))
                                opt_dep = int(value(departure[dep_key]))
                                # 【修复】统一使用发车延误，与FCFS/MaxDelayFirst口径一致
                                delay_sec = max(0, opt_dep - scheduled_dep)

                                train_schedule.append({
                                    "station_code": station_code,
                                    "station_name": self.station_names.get(station_code, station_code),
                                    "arrival_time": self._seconds_to_time(opt_arr),
                                    "departure_time": self._seconds_to_time(opt_dep),
                                    "original_arrival": stop.arrival_time,
                                    "original_departure": stop.departure_time,
                                    "delay_seconds": int(delay_sec)
                                })

                                all_delays.append(delay_sec)
                                if delay_sec > 0:
                                    train_has_delay = True
                            except Exception as e:
                                logger.warning(f"解析变量失败: {e}")
                                continue

                        if train_schedule:
                            optimized_schedule[t.train_id] = train_schedule
                        if train_has_delay:
                            final_affected_trains.add(t.train_id)

                    # 计算延误统计
                    max_delay_val = max(all_delays) if all_delays else 0
                    # 【修复】avg_delay 使用受影响列车数作为分母，与高铁调度行业标准一致
                    avg_delay = sum(all_delays) / len(final_affected_trains) if final_affected_trains else 0

                    logger.debug(f"[MIP] 部分解析成功: {len(optimized_schedule)}列车, 最大延误: {max_delay_val}秒")
                    return SolveResult(
                        success=True,  # 即使不是最优，只要有解就标记为成功
                        optimized_schedule=optimized_schedule,
                        delay_statistics={
                            "max_delay_seconds": int(max_delay_val),
                            "avg_delay_seconds": float(avg_delay),
                            "total_delay_seconds": int(sum(all_delays)),
                            "affected_trains_count": len(final_affected_trains)
                        },
                        computation_time=time.time() - start_time,
                        message=f"求解状态: {status}（非最优解，但可作为参考）"
                    )
                except Exception as e:
                    logger.error(f"[MIP] 解析部分结果失败: {e}")
                    return SolveResult(
                        success=False,
                        optimized_schedule={},
                        delay_statistics={},
                        computation_time=time.time() - start_time,
                        message=f"解析失败: {str(e)}"
                    )

        # 解析结果
        optimized_schedule = {}
        all_delays = []
        final_affected_trains = set()

        for t in self.trains:
            train_schedule = []
            train_has_delay = False
            if not t.schedule or not t.schedule.stops or not isinstance(t.schedule.stops, (list, tuple)):
                continue
            for stop in t.schedule.stops:
                station_code = stop.station_code
                arr_key = (t.train_id, station_code)
                dep_key = (t.train_id, station_code)
                delay_arr_key = (t.train_id, station_code)
                delay_dep_key = (t.train_id, station_code)

                # 【关键修复】添加None检查，防止'NoneType' object has no attribute 'value'错误
                arr_var = arrival.get(arr_key)
                dep_var = departure.get(dep_key)
                delay_arr_var = delay_arrival.get(delay_arr_key)
                delay_dep_var = delay_departure.get(delay_dep_key)

                if arr_var is not None:
                    arr_time = value(arr_var)
                else:
                    arr_time = None

                if dep_var is not None:
                    dep_time = value(dep_var)
                else:
                    dep_time = None

                if delay_dep_var is not None:
                    delay_val = value(delay_dep_var)
                else:
                    delay_val = 0

                if arr_time is None:
                    arr_time = self._time_to_seconds(stop.arrival_time)
                if dep_time is None:
                    dep_time = self._time_to_seconds(stop.departure_time)

                all_delays.append(delay_val)
                if delay_val > 0:
                    train_has_delay = True

                train_schedule.append({
                    "station_code": station_code,
                    "station_name": self.station_names.get(station_code, station_code),
                    "arrival_time": self._seconds_to_time(int(arr_time)),
                    "departure_time": self._seconds_to_time(int(dep_time)),
                    "original_arrival": stop.arrival_time,
                    "original_departure": stop.departure_time,
                    "delay_seconds": int(delay_val)  # 使用发车延误
                })

            optimized_schedule[t.train_id] = train_schedule
            if train_has_delay:
                final_affected_trains.add(t.train_id)

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
            message="求解成功"
        )

    def solve_with_adjustment(self, delay_injection: DelayInjection, adjustment_minutes: int = 30, objective: str = "min_max_delay") -> SolveResult:
        return self.solve(delay_injection, objective)


def create_scheduler(trains: List[Train], stations: List[Station]) -> MIPScheduler:
    return MIPScheduler(trains, stations)


if __name__ == "__main__":
    from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
    from models.data_models import InjectedDelay, DelayLocation, ScenarioType

    use_real_data(True)
    trains = get_trains_pydantic()[:30]
    stations = get_stations_pydantic()

    print("=" * 60)
    print("修正版MIP调度器测试")
    print("=" * 60)

    scheduler = create_scheduler(trains, stations)

    # 测试: G1563在保定东延误20分钟
    delay_injection = DelayInjection(
        scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
        scenario_id="TEST",
        injected_delays=[
            InjectedDelay(
                train_id="G1563",
                location=DelayLocation(location_type="station", station_code="BDD"),
                initial_delay_seconds=1200,
                timestamp="2024-01-15T10:00:00Z"
            )
        ],
        affected_trains=["G1563"]
    )

    result = scheduler.solve(delay_injection, "min_max_delay")
    print(f"\n测试 - G1563在保定东延误20分钟:")
    print(f"  求解状态: {result.message}")
    if result.success:
        print(f"  最大延误: {result.delay_statistics['max_delay_seconds']//60} 分钟")
        print(f"  计算时间: {result.computation_time:.2f}秒")
