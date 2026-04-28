# -*- coding: utf-8 -*-
"""
铁路调度系统 - 整数规划求解器模块（修正版）
解决了原版中的约束冲突问题，符合实际高铁运营规则
"""

from typing import List, Dict, Optional, Any
from pulp import (
    LpProblem, LpVariable, LpMinimize,
    lpSum, LpStatus, value
)
import time
import logging

from solver.base import BaseSolver, SolveResult
from models.data_models import Train, Station, DelayInjection
from config import DispatchEnvConfig

logger = logging.getLogger(__name__)


class MIPScheduler(BaseSolver):
    """
    修正版混合整数规划调度器
    主要修正:
    1. 取消区间运行时间上界，允许延误传播
    2. 只对非受影响列车的第一站约束延误为0
    3. 允许停站时间在合理范围内调整
    4. 追踪间隔调整为3分钟（符合高铁标准）
    5. 【公平性修复】多股道车站添加同股道追踪间隔约束
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        headway_time: int = None,
        min_stop_time: int = None,
        min_headway_time: int = None,
        min_departure_interval: int = None
    ):
        super().__init__(
            trains, stations, headway_time, min_stop_time, min_departure_interval
        )
        self.min_headway_time = (
            min_headway_time
            if min_headway_time is not None
            else DispatchEnvConfig.min_headway_time()
        )

    def solve(self, delay_injection: DelayInjection, objective: str = "min_total_delay", solver_config: Dict = None) -> SolveResult:
        """
        求解调度优化问题
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

        solver_config = solver_config or {}
        optimization_objective = solver_config.get("optimization_objective", objective)

        if optimization_objective == "min_max_delay":
            prob += max_delay
        elif optimization_objective == "min_total_delay":
            prob += lpSum([
                delay_arrival[t.train_id, s.station_code] +
                delay_departure[t.train_id, s.station_code]
                for t in self.trains
                for s in self.stations
                if s.station_code in self._get_stations_for_train(t)
            ])
        elif optimization_objective == "min_avg_delay":
            prob += lpSum([
                delay_arrival[t.train_id, s.station_code] +
                delay_departure[t.train_id, s.station_code]
                for t in self.trains
                for s in self.stations
                if s.station_code in self._get_stations_for_train(t)
            ])
        else:
            prob += max_delay

        # 收集受影响的列车信息
        affected_trains = set()
        injected_stations = {}
        invalid_train_ids = []

        # 1. 初始延误约束
        for injected in delay_injection.injected_delays:
            train_id = injected.train_id
            station_code = injected.location.station_code or "BJX"
            initial_delay = injected.initial_delay_seconds

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
                        prob += arrival[train_id, station_code] >= scheduled_arr + initial_delay
                        found_station = True
                        break

                if not found_station:
                    logger.warning(f"[MIP] 列车 {train_id} 不经过车站 {station_code}，跳过延误约束")
                    affected_trains.discard(train_id)
                    if (train_id, station_code) in injected_stations:
                        del injected_stations[(train_id, station_code)]

        if not affected_trains:
            error_msg = f"所有受影响的列车ID {invalid_train_ids} 都不在时刻表数据中" if invalid_train_ids else "没有指定受影响的列车"
            logger.error(error_msg)
            return SolveResult(
                success=False,
                optimized_schedule={},
                delay_statistics={},
                computation_time=time.time() - start_time,
                message=error_msg
            )

        # 2. 区间运行时间约束
        station_codes_set = set(self.station_codes)
        for t in self.trains:
            train_stations = self._get_stations_for_train(t)
            for i in range(len(train_stations) - 1):
                from_station = train_stations[i]
                to_station = train_stations[i + 1]

                if from_station not in station_codes_set or to_station not in station_codes_set:
                    continue
                
                to_track_count = self.station_track_count.get(to_station, 1)
                if to_track_count == 0:
                    continue

                min_time = self._get_min_section_time(from_station, to_station)
                min_safe_time = int(min_time * DispatchEnvConfig.min_section_time_ratio())
                prob += arrival[t.train_id, to_station] - departure[t.train_id, from_station] >= min_safe_time

        # 3. 追踪间隔约束（已修复多股道 headway bug）
        for s in self.stations:
            station_code = s.station_code
            track_count = self.station_track_count.get(station_code, 1)

            if track_count == 0:
                continue

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

            # 相邻列车约束
            for i in range(len(trains_with_time) - 1):
                t1, _ = trains_with_time[i]
                t2, _ = trains_with_time[i + 1]

                if track_count == 1:
                    prob += departure[t2.train_id, station_code] >= departure[t1.train_id, station_code] + self.headway_time
                else:
                    prob += departure[t2.train_id, station_code] >= departure[t1.train_id, station_code] + self.min_departure_interval

            # 同股道追踪间隔
            if track_count > 1:
                for i in range(len(trains_with_time) - track_count):
                    t1, _ = trains_with_time[i]
                    t2, _ = trains_with_time[i + track_count]
                    prob += departure[t2.train_id, station_code] >= departure[t1.train_id, station_code] + self.headway_time

        # 5. 第一站到达时间约束
        for t in self.trains:
            if not t.schedule or not t.schedule.stops or not isinstance(t.schedule.stops, (list, tuple)):
                continue

            train_stations = self._get_stations_for_train(t)
            if not train_stations:
                continue

            first_station = train_stations[0]
            first_stop = t.schedule.stops[0]
            scheduled_arr = self._time_to_seconds(first_stop.arrival_time)

            has_injection = any(inj_train_id == t.train_id for inj_train_id, _ in injected_stations.keys())

            if not has_injection and (t.train_id, first_station) not in injected_stations:
                prob += arrival[t.train_id, first_station] >= scheduled_arr
            else:
                logger.debug(f"[MIP] {t.train_id} 受影响，允许在第一站 {first_station} 延误")

        # 6. 停站时间约束
        for t in self.trains:
            if not t.schedule or not t.schedule.stops:
                continue
            for stop in t.schedule.stops:
                station_code = stop.station_code
                if station_code not in station_codes_set:
                    continue

                original_duration = self._get_original_stop_duration(t, station_code)
                if original_duration == 0:
                    prob += departure[t.train_id, station_code] >= arrival[t.train_id, station_code]
                else:
                    prob += departure[t.train_id, station_code] - arrival[t.train_id, station_code] >= self.min_stop_time

        # 7. 发车时间约束（禁止提前发车）
        for t in self.trains:
            if not t.schedule or not t.schedule.stops:
                continue
            for stop in t.schedule.stops:
                station_code = stop.station_code
                if station_code not in station_codes_set:
                    continue
                scheduled_dep = self._time_to_seconds(stop.departure_time)
                prob += departure[t.train_id, station_code] >= scheduled_dep

        # 8. 到达时间约束（禁止提前到站）
        for t in self.trains:
            if not t.schedule or not t.schedule.stops:
                continue
            for stop in t.schedule.stops:
                station_code = stop.station_code
                if station_code not in station_codes_set:
                    continue
                scheduled_arr = self._time_to_seconds(stop.arrival_time)
                prob += arrival[t.train_id, station_code] >= scheduled_arr

        # 9. 延误计算约束
        for t in self.trains:
            if not t.schedule or not t.schedule.stops:
                continue

            train_id = t.train_id
            is_affected = train_id in affected_trains
            train_stations = self._get_stations_for_train(t)

            for stop in t.schedule.stops:
                station_code = stop.station_code
                if station_code not in station_codes_set:
                    continue

                scheduled_arr = self._time_to_seconds(stop.arrival_time)
                scheduled_dep = self._time_to_seconds(stop.departure_time)

                prob += delay_arrival[train_id, station_code] >= arrival[train_id, station_code] - scheduled_arr
                prob += delay_arrival[train_id, station_code] >= 0
                prob += max_delay >= delay_arrival[train_id, station_code]

                prob += delay_departure[train_id, station_code] >= departure[train_id, station_code] - scheduled_dep
                prob += delay_departure[train_id, station_code] >= 0
                prob += max_delay >= delay_departure[train_id, station_code]

        # 求解
        from pulp import PULP_CBC_CMD

        if solver_config:
            max_solve_time = solver_config.get("time_limit")
            optimality_gap = solver_config.get("optimality_gap")
            optimization_objective = solver_config.get("optimization_objective", optimization_objective)

            if max_solve_time is not None:
                try:
                    max_solve_time = int(max_solve_time)
                    max_solve_time = max(
                        DispatchEnvConfig.mip_min_time_limit(),
                        min(DispatchEnvConfig.mip_max_time_limit(), max_solve_time)
                    )
                except (ValueError, TypeError):
                    max_solve_time = DispatchEnvConfig.solver_time_limit()
            else:
                max_solve_time = DispatchEnvConfig.solver_time_limit()

            if optimality_gap is not None:
                try:
                    optimality_gap = float(optimality_gap)
                    optimality_gap = max(
                        DispatchEnvConfig.mip_min_optimality_gap(),
                        min(DispatchEnvConfig.mip_max_optimality_gap(), round(optimality_gap, 2))
                    )
                except (ValueError, TypeError):
                    optimality_gap = DispatchEnvConfig.solver_optimality_gap()
            else:
                optimality_gap = DispatchEnvConfig.solver_optimality_gap()
        else:
            max_solve_time = DispatchEnvConfig.solver_time_limit()
            optimality_gap = DispatchEnvConfig.solver_optimality_gap()

        solver = PULP_CBC_CMD(msg=False, timeLimit=max_solve_time, gapRel=optimality_gap)
        prob.solve(solver)

        # 检查求解状态
        status = LpStatus[prob.status]
        if status != 'Optimal':
            if status == 'Not Solved' or status == 'Undefined':
                logger.warning(f"[MIP] 求解器未能在{max_solve_time}秒内找到解，状态: {status}")
                return SolveResult(
                    success=False,
                    optimized_schedule={},
                    delay_statistics={},
                    computation_time=time.time() - start_time,
                    message=f"求解超时或失败: {status}"
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
                try:
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
                                arr_key = (t.train_id, station_code)
                                dep_key = (t.train_id, station_code)
                                if arr_key not in arrival or dep_key not in departure:
                                    continue

                                opt_arr = int(value(arrival[arr_key]))
                                opt_dep = int(value(departure[dep_key]))
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

        # 解析最优结果
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
                delay_dep_key = (t.train_id, station_code)

                arr_var = arrival.get(arr_key)
                dep_var = departure.get(dep_key)
                delay_dep_var = delay_departure.get(delay_dep_key)

                arr_time = value(arr_var) if arr_var is not None else None
                dep_time = value(dep_var) if dep_var is not None else None
                delay_val = value(delay_dep_var) if delay_dep_var is not None else 0

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
                    "delay_seconds": int(delay_val)
                })

            optimized_schedule[t.train_id] = train_schedule
            if train_has_delay:
                final_affected_trains.add(t.train_id)

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
            message="求解成功"
        )

    def solve_with_adjustment(self, delay_injection: DelayInjection, adjustment_minutes: int = 30, objective: str = "min_max_delay") -> SolveResult:
        return self.solve(delay_injection, objective)


def create_mip_scheduler(trains: List[Train], stations: List[Station]) -> MIPScheduler:
    """创建MIP调度器实例"""
    return MIPScheduler(trains, stations)
