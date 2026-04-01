# -*- coding: utf-8 -*-
"""
MIP 求解器适配器模块
将 MIP 调度器包装为统一接口
"""

import logging
import time
from typing import List, Dict, Any

from solver.base_solver import BaseSolver, SolverRequest, SolverResponse
from models.data_models import Train, Station, DelayInjection, InjectedDelay, DelayLocation

logger = logging.getLogger(__name__)


class MIPSolverAdapter(BaseSolver):
    """
    MIP 求解器适配器
    包装 MIPScheduler 为统一接口
    """

    def __init__(self):
        """初始化 MIP 适配器"""
        self._scheduler = None

    def _ensure_scheduler(self, trains: List, stations: List):
        """确保调度器已初始化"""
        if self._scheduler is None:
            # 导入并创建 MIP 调度器
            from solver.mip_scheduler import MIPScheduler
            # 转换数据
            train_objs = self._convert_trains(trains)
            station_objs = self._convert_stations(stations)
            self._scheduler = MIPScheduler(train_objs, station_objs)

    def _convert_trains(self, trains_data: List[Dict]) -> List[Train]:
        """将字典数据转换为 Train 对象"""
        trains = []
        for t in trains_data:
            if isinstance(t, Train):
                trains.append(t)
            elif isinstance(t, dict):
                try:
                    trains.append(Train(**t))
                except Exception as e:
                    logger.warning(f"Failed to convert train: {e}")
        return trains

    def _convert_stations(self, stations_data: List[Dict]) -> List[Station]:
        """将字典数据转换为 Station 对象"""
        stations = []
        for s in stations_data:
            if isinstance(s, Station):
                stations.append(s)
            elif isinstance(s, dict):
                try:
                    stations.append(Station(**s))
                except Exception as e:
                    logger.warning(f"Failed to convert station: {s}")
        return stations

    def _convert_delay_injection(self, request: SolverRequest) -> DelayInjection:
        """将请求转换为 DelayInjection"""
        injected_delays = []
        for delay_dict in request.injected_delays:
            if isinstance(delay_dict, InjectedDelay):
                injected_delays.append(delay_dict)
            elif isinstance(delay_dict, dict):
                try:
                    injected_delays.append(InjectedDelay(**delay_dict))
                except Exception as e:
                    logger.warning(f"Failed to convert delay: {e}")

        # 从 metadata 获取场景类型（直接使用小写值）
        scene_type = request.metadata.get("scenario_type", "temporary_speed_limit")

        return DelayInjection(
            scenario_type=scene_type,
            scenario_id=request.scene_id,
            injected_delays=injected_delays,
            affected_trains=[d.get("train_id", "") for d in request.injected_delays],
            scenario_params={}
        )

    def solve(self, request: SolverRequest) -> SolverResponse:
        """
        执行 MIP 求解

        Args:
            request: 求解器请求

        Returns:
            SolverResponse: 求解结果
        """
        start_time = time.time()

        try:
            # 确保调度器已初始化
            self._ensure_scheduler(request.trains, request.stations)

            # 转换延误注入
            delay_injection = self._convert_delay_injection(request)

            # 执行求解
            result = self._scheduler.solve(delay_injection)

            # 转换结果
            if result.success:
                return SolverResponse(
                    success=True,
                    status="success",
                    schedule=result.optimized_schedule,
                    metrics=result.delay_statistics,
                    solving_time_seconds=result.computation_time,
                    solver_type="mip",
                    message=result.message,
                    metadata={"original_result": "SolveResult"}
                )
            else:
                return SolverResponse(
                    success=False,
                    status="solver_failed",
                    message=result.message,
                    solver_type="mip",
                    metadata={"original_result": "SolveResult"}
                )

        except Exception as e:
            logger.exception(f"MIP solver error: {e}")
            return SolverResponse(
                success=False,
                status="solver_failed",
                message=f"MIP求解失败: {str(e)}",
                solver_type="mip",
                error=str(e)
            )

    def get_solver_type(self) -> str:
        return "mip"