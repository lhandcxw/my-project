# -*- coding: utf-8 -*-
"""
第三层：求解执行引擎（重构版 - 使用 Scheduler 系统）

职责边界（与 L2 Agent 清晰分离）：
  - L2 Agent：态势感知 + 策略决策 + 求解器选择 + 参数调优
  - L3 Solver：纯求解执行引擎，接收明确指令后执行求解

改造要点（2026-04-21 架构统一）：
  1. 移除 Solver 系统，统一使用 Scheduler 系统
  2. _get_scheduler 使用 SchedulerRegistry.create()
  3. 转换 accident_card 为 DelayInjection
  4. 转换 SchedulerResult 为工作流需要的格式
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from models.workflow_models import AccidentCard
from models.data_models import DelayInjection, InjectedDelay, DelayLocation

logger = logging.getLogger(__name__)


class Layer3Solver:
    """
    第三层：求解执行引擎 (L3 Solver Engine) - 使用 Scheduler 系统

    核心改变：
    - 旧版：使用 Solver 系统（solver.solver_registry）
    - 新版：使用 Scheduler 系统（scheduler_comparison.scheduler_interface）
    """

    def __init__(self):
        pass

    def execute(
        self,
        planning_intent: str,
        accident_card: AccidentCard,
        trains: Optional[List[Any]] = None,
        stations: Optional[List[Any]] = None,
        planner_decision: Optional[Dict[str, Any]] = None,
        network_snapshot: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        执行求解（回退模式入口）

        当 L2 Agent 未能完成求解时（agent_executed_solve=False），
        工作流引擎调用此方法作为回退执行路径。

        参数:
            planning_intent: L2 输出的规划意图
            accident_card: 事故信息卡
            trains: 列车数据（工作流传入）
            stations: 车站数据（工作流传入）
            planner_decision: L2 的 planner_decision，包含 preferred_solver 和 solver_config
            network_snapshot: 网络快照（可选），用于裁剪求解范围
        """
        logger.debug("[L3] 求解执行引擎启动（回退模式）")

        # 【修复】当 trains/stations 为 None 时，自动加载真实数据
        if trains is None or stations is None:
            from models.data_loader import get_trains_pydantic, get_stations_pydantic
            if trains is None:
                trains = get_trains_pydantic()
                logger.info(f"[L3] 自动加载列车数据: {len(trains)} 列")
            if stations is None:
                stations = get_stations_pydantic()
                logger.info(f"[L3] 自动加载车站数据: {len(stations)} 个")

        # 【新增】利用 NetworkSnapshot 裁剪数据范围，减少求解规模
        trains, stations = self._apply_snapshot_filter(trains, stations, network_snapshot)

        # 【修复】确保 trains/stations 是 Pydantic 对象（防止传入 dict）
        trains, stations = self._ensure_pydantic_objects(trains, stations)

        # 从 L2 决策中提取求解器名和配置
        scheduler_name = "fcfs"  # 默认安全选择
        objective = "min_max_delay"
        scheduler_config = {}

        if planner_decision and isinstance(planner_decision, dict):
            scheduler_name = planner_decision.get("preferred_solver", "fcfs")
            scheduler_config = planner_decision.get("solver_config", {})
            objective = scheduler_config.get("optimization_objective", "min_max_delay")

        logger.debug(f"[L3] 执行调度器: {scheduler_name}, 目标: {objective}, 参数: {scheduler_config}")

        # 获取调度器实例
        scheduler = self._get_scheduler(scheduler_name, trains, stations)
        if scheduler is None:
            return self._build_error_result(scheduler_name, f"无法获取调度器: {scheduler_name}")

        # 安全约束兜底
        if accident_card.scene_category == "区间封锁" and scheduler_name != "fcfs":
            logger.info(f"[L3] 安全约束：区间封锁，{scheduler_name} → fcfs")
            scheduler = self._get_scheduler("fcfs", trains, stations) or scheduler

        if not accident_card.is_complete and scheduler_name not in ("fcfs", "noop"):
            logger.info(f"[L3] 安全约束：信息不完整，{scheduler_name} → fcfs")
            scheduler = self._get_scheduler("fcfs", trains, stations) or scheduler

        # 构建 DelayInjection
        delay_injection = self._build_delay_injection(
            accident_card, scheduler_config
        )

        # 执行调度
        try:
            scheduler_result = scheduler.solve(delay_injection, objective=objective)

            logger.info(
                f"[L3] 调度完成: scheduler={scheduler_name}, "
                f"成功={scheduler_result.success}, "
                f"耗时={scheduler_result.metrics.computation_time:.2f}秒"
            )

            if scheduler_result.success:
                return self._build_success_result(
                    scheduler_name, accident_card, scheduler_result, scheduler_config
                )
            else:
                return self._build_error_result(
                    scheduler_name, scheduler_result.message
                )

        except Exception as e:
            logger.error(f"[L3] 调度执行失败: {e}")
            import traceback
            logger.error(f"详细堆栈: {traceback.format_exc()}")
            return self._build_error_result(scheduler_name, str(e))

    # ================================================================
    # Snapshot 数据裁剪（新增）
    # ================================================================

    def _apply_snapshot_filter(
        self,
        trains: List[Any],
        stations: List[Any],
        network_snapshot: Optional[Any]
    ) -> Tuple[List[Any], List[Any]]:
        """
        利用 NetworkSnapshot 裁剪求解数据范围

        策略：
        1. 如果 snapshot 存在且有 candidate_train_ids，从全量数据中筛选出候选列车
        2. 如果 snapshot 存在且有 stations，从全量数据中筛选出窗口内车站
        3. 如果筛选后数据为空，自动回退到全量数据（安全兜底）

        Args:
            trains: 全量列车数据
            stations: 全量车站数据
            network_snapshot: 网络快照（可选）

        Returns:
            Tuple[trains, stations]: 裁剪后的数据（可能仍为全量）
        """
        if not network_snapshot:
            return trains, stations

        # 筛选列车：使用 candidate_train_ids
        filtered_trains = trains
        if hasattr(network_snapshot, 'candidate_train_ids') and network_snapshot.candidate_train_ids:
            candidate_ids = set(network_snapshot.candidate_train_ids)
            filtered_trains = [
                t for t in trains
                if (hasattr(t, 'train_id') and t.train_id in candidate_ids)
                or (isinstance(t, dict) and t.get('train_id') in candidate_ids)
            ]
            if filtered_trains:
                logger.info(
                    f"[L3] Snapshot 列车裁剪: {len(filtered_trains)} 列 (原 {len(trains)} 列)"
                )
            else:
                logger.warning(
                    f"[L3] Snapshot 列车裁剪后为空，回退到全量 {len(trains)} 列"
                )
                filtered_trains = trains

        # 筛选车站：使用 snapshot.stations 中的 station_code
        filtered_stations = stations
        if hasattr(network_snapshot, 'stations') and network_snapshot.stations:
            window_codes = set()
            for s in network_snapshot.stations:
                if isinstance(s, dict):
                    code = s.get('station_code')
                    if code:
                        window_codes.add(code)
                elif hasattr(s, 'station_code'):
                    window_codes.add(s.station_code)

            if window_codes:
                filtered_stations = [
                    s for s in stations
                    if (hasattr(s, 'station_code') and s.station_code in window_codes)
                    or (isinstance(s, dict) and s.get('station_code') in window_codes)
                ]
                if filtered_stations:
                    logger.info(
                        f"[L3] Snapshot 车站裁剪: {len(filtered_stations)} 个 (原 {len(stations)} 个)"
                    )
                else:
                    logger.warning(
                        f"[L3] Snapshot 车站裁剪后为空，回退到全量 {len(stations)} 个"
                    )
                    filtered_stations = stations

        return filtered_trains, filtered_stations

    # ================================================================
    # 调度器加载（使用 Scheduler 系统）
    # ================================================================

    def _get_scheduler(self, scheduler_name: str, trains: List, stations: List):
        """
        获取调度器实例（使用 Scheduler 系统）

        支持的调度器：
        - fcfs: 先到先服务
        - mip: 混合整数规划
        - max-delay-first: 最大延误优先
        - noop: 基线（无调整）
        - eaf: 最早到站优先（可选）
        """
        try:
            from scheduler_comparison.scheduler_interface import SchedulerRegistry

            # 使用 SchedulerRegistry 创建调度器
            scheduler = SchedulerRegistry.create(scheduler_name, trains, stations)

            if scheduler:
                logger.debug(f"[L3] 成功创建调度器: {scheduler_name}")
            else:
                logger.warning(f"[L3] 调度器未注册: {scheduler_name}")

            return scheduler

        except Exception as e:
            logger.error(f"[L3] 加载调度器失败 {scheduler_name}: {e}")
            return None

    # ================================================================
    # 数据类型转换（防御性编程：防止传入 dict 而非 Pydantic 对象）
    # ================================================================

    def _ensure_pydantic_objects(self, trains: List[Any], stations: List[Any]):
        """
        确保 trains 和 stations 是 Pydantic 对象

        问题背景：
        - NetworkSnapshot.trains / NetworkSnapshot.stations 是 List[Dict]
        - 但 FCFSScheduler / MIPScheduler 等期望 List[Train] / List[Station]（Pydantic对象）
        - 如果传入 dict，会导致 AttributeError: 'dict' object has no attribute 'station_code'

        修复策略：
        - 检测到 dict 时，使用 Pydantic 模型构造函数转换
        - 已经是 Pydantic 对象则直接返回
        """
        from models.data_models import Train, Station, TrainSchedule, TrainStop

        # 处理 trains
        if trains and isinstance(trains[0], dict):
            logger.debug(f"[L3] trains 为 dict 列表，转换为 Pydantic Train 对象")
            pydantic_trains = []
            for t in trains:
                try:
                    # Pydantic v1 支持递归解析嵌套 BaseModel
                    train = Train(**t)
                    pydantic_trains.append(train)
                except Exception as e:
                    logger.warning(f"[L3] Train(**dict) 失败: {e}，尝试手动转换")
                    # 手动转换回退
                    schedule_data = t.get("schedule", {}) if isinstance(t.get("schedule"), dict) else {}
                    stops_data = schedule_data.get("stops", []) if isinstance(schedule_data, dict) else []
                    stops = []
                    for s in stops_data:
                        if not isinstance(s, dict):
                            continue
                        stops.append(TrainStop(
                            station_code=s.get("station_code", ""),
                            station_name=s.get("station_name", ""),
                            arrival_time=s.get("arrival_time", ""),
                            departure_time=s.get("departure_time", ""),
                            is_stopped=s.get("is_stopped", True),
                            stop_duration=s.get("stop_duration", 0)
                        ))
                    if stops:
                        pydantic_trains.append(Train(
                            train_id=t.get("train_id", ""),
                            train_type=t.get("train_type", "高速动车组"),
                            schedule=TrainSchedule(stops=stops)
                        ))
            trains = pydantic_trains
            logger.info(f"[L3] 列车数据转换完成: {len(trains)} 列 Pydantic 对象")

        # 处理 stations
        if stations and isinstance(stations[0], dict):
            logger.debug(f"[L3] stations 为 dict 列表，转换为 Pydantic Station 对象")
            pydantic_stations = []
            for s in stations:
                try:
                    station = Station(**s)
                    pydantic_stations.append(station)
                except Exception as e:
                    logger.warning(f"[L3] Station(**dict) 失败: {e}，使用手动转换")
                    pydantic_stations.append(Station(
                        station_code=s.get("station_code", ""),
                        station_name=s.get("station_name", ""),
                        track_count=s.get("track_count", 1),
                        node_type=s.get("node_type", "station")
                    ))
            stations = pydantic_stations
            logger.info(f"[L3] 车站数据转换完成: {len(stations)} 个 Pydantic 对象")

        return trains, stations

    # ================================================================
    # DelayInjection 构建
    # ================================================================

    def _build_delay_injection(
        self,
        accident_card: AccidentCard,
        scheduler_config: Dict[str, Any]
    ) -> DelayInjection:
        """
        构建 DelayInjection

        从 AccidentCard 转换为 DelayInjection
        """
        from config import DispatchEnvConfig

        # 提取受影响列车ID
        affected_train_ids = (
            accident_card.affected_train_ids
            if hasattr(accident_card, 'affected_train_ids') and accident_card.affected_train_ids
            else []
        )

        # 构建注入的延误列表
        injected_delays = []

        if affected_train_ids:
            # 使用第一个受影响列车（简化处理）
            train_id = affected_train_ids[0]

            # 确定延误时间（分钟 → 秒）
            delay_minutes = accident_card.expected_duration if accident_card.expected_duration else 15
            delay_seconds = int(delay_minutes * 60)

            # 确定位置
            location_code = accident_card.location_code or "SJP"

            injected_delays.append(InjectedDelay(
                train_id=train_id,
                location=DelayLocation(
                    location_type="station",
                    station_code=location_code
                ),
                initial_delay_seconds=delay_seconds,
                timestamp=datetime.now().isoformat()
            ))

        # 使用 AccidentCard 的统一接口获取场景类型
        scenario_type = accident_card.scene_type
        scenario_id = accident_card.scene_id

        return DelayInjection(
            scenario_type=scenario_type,
            scenario_id=scenario_id,
            injected_delays=injected_delays,
            affected_trains=affected_train_ids,
            scenario_params=scheduler_config
        )

    # ================================================================
    # 结果构建
    # ================================================================

    def _build_success_result(
        self,
        scheduler_name: str,
        accident_card: AccidentCard,
        scheduler_result,
        scheduler_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        构建成功结果
        """
        metrics = scheduler_result.metrics

        # 【修复】提取实际受影响的列车ID
        affected_train_ids = []
        if scheduler_result.optimized_schedule and isinstance(scheduler_result.optimized_schedule, dict):
            affected_train_ids = [
                tid for tid, stops in scheduler_result.optimized_schedule.items()
                if isinstance(stops, list) and any(
                    s.get("delay_seconds", 0) > 0 for s in stops if isinstance(s, dict)
                )
            ]

        return {
            "success": True,
            "skill_execution_result": {
                "success": True,
                "skill_name": scheduler_name,
                "total_delay_minutes": metrics.total_delay_seconds / 60,
                "max_delay_minutes": metrics.max_delay_seconds / 60,
                "avg_delay_minutes": metrics.avg_delay_seconds / 60,
                "affected_trains_count": metrics.affected_trains_count,
                "affected_trains": affected_train_ids,
                "solving_time_seconds": metrics.computation_time,
                "scheduler_name": scheduler_name,
                "adjustments": [],
                "optimized_schedule": scheduler_result.optimized_schedule
            },
            "solver_response": {
                "success": True,
                "skill_name": scheduler_name,
                "solver_type": scheduler_name,
                "optimized_schedule": scheduler_result.optimized_schedule,
                "solving_time_seconds": metrics.computation_time,
                "metrics": {
                    "max_delay_seconds": metrics.max_delay_seconds,
                    "avg_delay_seconds": metrics.avg_delay_seconds,
                    "total_delay_seconds": metrics.total_delay_seconds,
                    "affected_trains_count": metrics.affected_trains_count,
                    "on_time_rate": metrics.on_time_rate,
                    "computation_time": metrics.computation_time
                },
                "message": scheduler_result.message,
                "solver_config_used": scheduler_config
            }
        }

    def _build_error_result(
        self,
        scheduler_name: str,
        error_message: str
    ) -> Dict[str, Any]:
        """
        构建错误结果
        """
        return {
            "success": False,
            "skill_execution_result": {
                "success": False,
                "skill_name": scheduler_name,
                "total_delay_minutes": 0,
                "max_delay_minutes": 0,
                "avg_delay_minutes": 0,
                "affected_trains_count": 0,
                "solving_time_seconds": 0,
                "scheduler_name": scheduler_name,
                "error": error_message
            },
            "solver_response": {
                "success": False,
                "skill_name": scheduler_name,
                "solver_type": scheduler_name,
                "optimized_schedule": {},
                "solving_time_seconds": 0.0,
                "metrics": {
                    "max_delay_seconds": 0,
                    "avg_delay_seconds": 0,
                    "total_delay_seconds": 0,
                    "affected_trains_count": 0,
                    "on_time_rate": 1.0,
                    "computation_time": 0.0
                },
                "message": f"求解失败: {error_message}",
                "error": error_message
            }
        }