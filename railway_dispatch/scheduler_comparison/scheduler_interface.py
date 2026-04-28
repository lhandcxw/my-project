# -*- coding: utf-8 -*-
"""
铁路调度系统 - 调度器统一接口模块
提供统一的调度器接口，支持FCFS、MIP、强化学习等多种调度方法
"""

from typing import Dict, List, Any, Optional, Protocol, Type, Callable
from dataclasses import dataclass
from enum import Enum
import time
import logging
import abc

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.data_models import Train, Station, DelayInjection
from scheduler_comparison.metrics import EvaluationMetrics, MetricsDefinition
from solver.base import BaseSolver as _BaseSolverTools
from config import DispatchEnvConfig

logger = logging.getLogger(__name__)


class SchedulerType(str, Enum):
    """调度器类型枚举"""
    FCFS = "fcfs"
    FSFS = "fsfs"  # 先计划先服务（First Scheduled First Served）
    MIP = "mip"
    GREEDY = "greedy"
    GENETIC = "genetic"  # 遗传算法
    NOOP = "noop"  # 基线不做调整
    MAX_DELAY_FIRST = "max-delay-first"  # 最大延误优先
    EARLIEST_ARRIVAL = "earliest_arrival"  # 最早到站优先
    HIERARCHICAL = "hierarchical"  # 分层求解（FCFS+MIP混合）
    SPT = "spt"  # 最短处理时间优先
    SRPT = "srpt"  # 最短剩余处理时间优先
    CUSTOM = "custom"


@dataclass
class SchedulerResult:
    """调度器执行结果"""
    success: bool
    scheduler_name: str
    scheduler_type: SchedulerType
    optimized_schedule: Dict[str, List[Dict]]
    metrics: EvaluationMetrics
    message: str = ""
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BaseScheduler(abc.ABC):
    """
    调度器基类
    所有调度器必须实现此接口
    """
    
    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        name: str = "BaseScheduler",
        **kwargs
    ):
        self.trains = trains
        self.stations = stations
        self.name = name
        self.config = kwargs
    
    @abc.abstractmethod
    def solve(
        self,
        delay_injection: DelayInjection,
        objective: str = "min_total_delay"
    ) -> SchedulerResult:
        """
        执行调度优化
        
        Args:
            delay_injection: 延误注入信息
            objective: 优化目标 ("min_max_delay" 或 "min_avg_delay")
        
        Returns:
            SchedulerResult: 调度结果
        """
        pass
    
    @property
    @abc.abstractmethod
    def scheduler_type(self) -> SchedulerType:
        """返回调度器类型"""
        pass
    
    @property
    def description(self) -> str:
        """调度器描述"""
        return f"{self.name} ({self.scheduler_type.value})"
    
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


class FCFSSchedulerAdapter(BaseScheduler):
    """
    FCFS调度器适配器
    封装现有的FCFS调度器实现
    """
    
    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        headway_time: int = None,
        min_stop_time: int = None,
        **kwargs
    ):
        super().__init__(trains, stations, name="FCFS调度器", **kwargs)
        # 从统一配置加载默认值
        from config import DispatchEnvConfig
        self.headway_time = headway_time if headway_time is not None else DispatchEnvConfig.headway_time()
        self.min_stop_time = min_stop_time if min_stop_time is not None else DispatchEnvConfig.min_stop_time()
        
        # 延迟导入FCFS调度器
        self._scheduler = None
    
    def _get_scheduler(self):
        """延迟加载FCFS调度器"""
        if self._scheduler is None:
            from solver.fcfs_scheduler import FCFSScheduler
            self._scheduler = FCFSScheduler(
                trains=self.trains,
                stations=self.stations,
                headway_time=self.headway_time,
                min_stop_time=self.min_stop_time
            )
        return self._scheduler

    @property
    def scheduler_type(self) -> SchedulerType:
        return SchedulerType.FCFS

    def solve(
        self,
        delay_injection: DelayInjection,
        objective: str = "min_total_delay"
    ) -> SchedulerResult:
        scheduler = self._get_scheduler()
        start_time = time.time()

        result = scheduler.solve(delay_injection, objective)

        # 计算完整指标
        metrics = MetricsDefinition.calculate_metrics(
            result.optimized_schedule,
            self.get_original_schedule(),
            result.computation_time
        )

        return SchedulerResult(
            success=result.success,
            scheduler_name=self.name,
            scheduler_type=self.scheduler_type,
            optimized_schedule=result.optimized_schedule,
            metrics=metrics,
            message=result.message
        )


class MIPSchedulerAdapter(BaseScheduler):
    """
    MIP调度器适配器
    封装现有的MIP调度器实现
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        headway_time: int = None,
        min_stop_time: int = None,
        **kwargs
    ):
        super().__init__(trains, stations, name="MIP调度器", **kwargs)
        # 从统一配置加载默认值
        from config import DispatchEnvConfig
        self.headway_time = headway_time if headway_time is not None else DispatchEnvConfig.headway_time()
        self.min_stop_time = min_stop_time if min_stop_time is not None else DispatchEnvConfig.min_stop_time()
        # 接收并保存 MIP 求解参数（由 layer2_planner 传入）
        self.time_limit = kwargs.get("time_limit")
        self.optimality_gap = kwargs.get("optimality_gap")

        self._scheduler = None

    def _get_scheduler(self):
        """延迟加载MIP调度器"""
        if self._scheduler is None:
            from solver.mip_scheduler import MIPScheduler
            self._scheduler = MIPScheduler(
                trains=self.trains,
                stations=self.stations,
                headway_time=self.headway_time,
                min_stop_time=self.min_stop_time
            )
        return self._scheduler

    @property
    def scheduler_type(self) -> SchedulerType:
        return SchedulerType.MIP

    def solve(
        self,
        delay_injection: DelayInjection,
        objective: str = "min_total_delay"
    ) -> SchedulerResult:
        scheduler = self._get_scheduler()
        start_time = time.time()

        # 组装 solver_config，将 layer2_planner 传入的 MIP 参数转发给原始求解器
        solver_config = {}
        if self.time_limit is not None:
            solver_config["time_limit"] = self.time_limit
        if self.optimality_gap is not None:
            solver_config["optimality_gap"] = self.optimality_gap

        result = scheduler.solve(delay_injection, objective, solver_config)

        # 计算完整指标
        metrics = MetricsDefinition.calculate_metrics(
            result.optimized_schedule,
            self.get_original_schedule(),
            result.computation_time
        )

        return SchedulerResult(
            success=result.success,
            scheduler_name=self.name,
            scheduler_type=self.scheduler_type,
            optimized_schedule=result.optimized_schedule,
            metrics=metrics,
            message=result.message
        )


class NoOpSchedulerAdapter(BaseScheduler):
    """
    基线调度器（No-Op）适配器
    封装 solver/noop_scheduler.py 的实现
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        **kwargs
    ):
        super().__init__(trains, stations, name="基线调度器（无调整）", **kwargs)

        self._scheduler = None

    def _get_scheduler(self):
        """延迟加载NoOp调度器"""
        if self._scheduler is None:
            from solver.noop_scheduler import NoOpScheduler
            self._scheduler = NoOpScheduler(
                trains=self.trains,
                stations=self.stations
            )
        return self._scheduler

    @property
    def scheduler_type(self) -> SchedulerType:
        return SchedulerType.NOOP

    def solve(
        self,
        delay_injection: DelayInjection,
        objective: str = "min_total_delay"
    ) -> SchedulerResult:
        scheduler = self._get_scheduler()
        start_time = time.time()

        result = scheduler.solve(delay_injection, objective)

        # 计算完整指标
        metrics = MetricsDefinition.calculate_metrics(
            result.optimized_schedule,
            self.get_original_schedule(),
            result.computation_time
        )

        return SchedulerResult(
            success=result.success,
            scheduler_name=self.name,
            scheduler_type=self.scheduler_type,
            optimized_schedule=result.optimized_schedule,
            metrics=metrics,
            message=result.message
        )


class MaxDelayFirstSchedulerAdapter(BaseScheduler):
    """
    最大延误优先调度器（Max-Delay First）适配器
    封装 solver/max_delay_first_scheduler.py 的实现
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        headway_time: int = None,
        min_stop_time: int = None,
        **kwargs
    ):
        super().__init__(trains, stations, name="最大延误优先调度器", **kwargs)
        # 从统一配置加载默认值
        from config import DispatchEnvConfig
        self.headway_time = headway_time if headway_time is not None else DispatchEnvConfig.headway_time()
        self.min_stop_time = min_stop_time if min_stop_time is not None else DispatchEnvConfig.min_stop_time()

        self._scheduler = None

    def _get_scheduler(self):
        """延迟加载MaxDelayFirst调度器"""
        if self._scheduler is None:
            from solver.max_delay_first_scheduler import MaxDelayFirstScheduler
            self._scheduler = MaxDelayFirstScheduler(
                trains=self.trains,
                stations=self.stations,
                headway_time=self.headway_time,
                min_stop_time=self.min_stop_time
            )
        return self._scheduler

    @property
    def scheduler_type(self) -> SchedulerType:
        return SchedulerType.MAX_DELAY_FIRST

    def solve(
        self,
        delay_injection: DelayInjection,
        objective: str = "min_total_delay"
    ) -> SchedulerResult:
        scheduler = self._get_scheduler()
        start_time = time.time()

        result = scheduler.solve(delay_injection, objective)

        # 计算完整指标
        metrics = MetricsDefinition.calculate_metrics(
            result.optimized_schedule,
            self.get_original_schedule(),
            result.computation_time
        )

        return SchedulerResult(
            success=result.success,
            scheduler_name=self.name,
            scheduler_type=self.scheduler_type,
            optimized_schedule=result.optimized_schedule,
            metrics=metrics,
            message=result.message
        )




class SchedulerRegistry:
    """
    调度器注册表
    管理和创建各种调度器实例
    """
    
    _registry: Dict[str, Type[BaseScheduler]] = {}
    
    @classmethod
    def register(cls, name: str, scheduler_class: Type[BaseScheduler]):
        """注册调度器类"""
        cls._registry[name.lower()] = scheduler_class
    
    @classmethod
    def get(cls, name: str) -> Optional[Type[BaseScheduler]]:
        """获取调度器类"""
        return cls._registry.get(name.lower())
    
    @classmethod
    def list_available(cls) -> List[str]:
        """列出所有已注册的调度器"""
        return list(cls._registry.keys())
    
    @classmethod
    def create(
        cls,
        name: str,
        trains: List[Train],
        stations: List[Station],
        **kwargs
    ) -> Optional[BaseScheduler]:
        """
        创建调度器实例
        
        Args:
            name: 调度器名称
            trains: 列车列表
            stations: 车站列表
            **kwargs: 其他参数
        
        Returns:
            调度器实例，如果名称不存在则返回None
        """
        scheduler_class = cls.get(name)
        if scheduler_class is None:
            logger.warning(f"未找到调度器: {name}")
            return None
        return scheduler_class(trains, stations, **kwargs)
    
    @classmethod
    def create_all(
        cls,
        trains: List[Train],
        stations: List[Station],
        **kwargs
    ) -> Dict[str, BaseScheduler]:
        """
        创建所有已注册的调度器实例
        
        Args:
            trains: 列车列表
            stations: 车站列表
            **kwargs: 其他参数
        
        Returns:
            调度器实例字典 {名称: 实例}
        """
        schedulers = {}
        for name, scheduler_class in cls._registry.items():
            try:
                instance = scheduler_class(trains, stations, **kwargs)
                schedulers[name] = instance
            except Exception as e:
                logger.error(f"创建调度器 {name} 失败: {e}")
        
        return schedulers


# FSFSSchedulerAdapter 已移除
# 原因：FSFS调度器（先计划先服务）与基线NoOp调度器在大多数场景下行为几乎相同
# 且不符合高铁按图行车的调度实际，因此已在 solver_registry.py 中移除
# 如需使用，请使用 noop 调度器作为基线对比


class EarliestArrivalFirstScheduler(BaseScheduler):
    """
    最早到站优先调度器（Earliest Arrival First）

    与FCFS的区别：
    - FCFS: 先到的列车先处理，延误会传播到后续列车
    - EAF: 优先保证先到列车准点，**后续列车会为之前列车让行并等待**
      （即使追踪间隔允许通过，也选择等待以保证先到列车优先）

    这是一种**绝对保守**策略：确保先到列车不受后车影响
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        headway_time: int = None,
        min_stop_time: int = None,
        **kwargs
    ):
        super().__init__(trains, stations, name="最早到站优先调度器", **kwargs)
        # 从统一配置加载默认值
        from config import DispatchEnvConfig
        self.headway_time = headway_time if headway_time is not None else DispatchEnvConfig.headway_time()
        self.min_stop_time = min_stop_time if min_stop_time is not None else DispatchEnvConfig.min_stop_time()
        self.station_names = {s.station_code: s.station_name for s in stations}
        self.station_track_count = {s.station_code: s.track_count for s in stations}

    @property
    def scheduler_type(self) -> SchedulerType:
        return SchedulerType.EARLIEST_ARRIVAL

    def solve(
        self,
        delay_injection: DelayInjection,
        objective: str = "min_total_delay"
    ) -> SchedulerResult:
        start_time = time.time()

        # Step 1: 获取所有列车的发车时间并排序
        train_first_departure = []
        for train in self.trains:
            if train.schedule and train.schedule.stops and isinstance(train.schedule.stops, (list, tuple)):
                first_stop = train.schedule.stops[0]
                dep_time = _BaseSolverTools.time_to_seconds(first_stop.departure_time)
                train_first_departure.append((train.train_id, dep_time, train))

        # 按发车时间排序（最早发车的在前）
        train_first_departure.sort(key=lambda x: x[1])

        # Step 2: 初始化时刻表
        schedule = {}
        train_current_departure = {}  # 记录每列车的当前发车时间

        for train in self.trains:
            stops = []
            if train.schedule and train.schedule.stops and isinstance(train.schedule.stops, (list, tuple)):
                for stop in train.schedule.stops:
                    if hasattr(stop, 'station_code'):
                        arr_sec = _BaseSolverTools.time_to_seconds(stop.arrival_time)
                        dep_sec = _BaseSolverTools.time_to_seconds(stop.departure_time)
                        stops.append({
                            "station_code": stop.station_code,
                            "station_name": stop.station_name,
                            "arrival_time": stop.arrival_time,
                            "departure_time": stop.departure_time,
                            "delay_seconds": 0,
                            "arrival_seconds": arr_sec,
                            "departure_seconds": dep_sec
                        })
            schedule[train.train_id] = stops
            train_current_departure[train.train_id] = dep_sec if stops else 0

        # Step 3: 按顺序处理，**后续列车强制等待**
        # 关键区别于FCFS：不仅满足最小间隔，还会让后续列车额外等待
        for train_id, dep_time, train in train_first_departure:
            # 检查该列车是否需要应用初始延误
            for injected in delay_injection.injected_delays:
                if injected.train_id == train_id:
                    station_code = injected.location.station_code
                    initial_delay = injected.initial_delay_seconds

                    # 从该站开始，后续所有站点都延误
                    train_stops = schedule[train_id]
                    found_station = False
                    for stop in train_stops:
                        if found_station:
                            stop["delay_seconds"] += initial_delay
                            stop["arrival_seconds"] += initial_delay
                            stop["departure_seconds"] += initial_delay
                        if stop["station_code"] == station_code:
                            found_station = True
                            stop["delay_seconds"] += initial_delay
                            stop["arrival_seconds"] += initial_delay
                            stop["departure_seconds"] += initial_delay

            # 更新该列车的当前发车时间
            if train_id in schedule and schedule[train_id]:
                last_stop = schedule[train_id][-1]
                train_current_departure[train_id] = last_stop["departure_seconds"]

        # Step 4: 二次处理 - 让后续列车等待先到列车
        # 重新按发车时间排序
        sorted_trains = sorted(train_first_departure, key=lambda x: x[1])

        # 对每个车站，后续列车都要等待先到的列车
        for station in self.stations:
            station_code = station.station_code

            # 获取该站所有列车
            trains_at_station = []
            for train_id, _, _ in sorted_trains:
                if train_id in schedule:
                    for stop in schedule[train_id]:
                        if stop["station_code"] == station_code:
                            trains_at_station.append({
                                "train_id": train_id,
                                "original_dep": self._get_original_departure(train_id, station_code),
                                "current_dep": stop["departure_seconds"]
                            })
                            break

            # 按原始发车时间排序
            trains_at_station.sort(key=lambda x: x["original_dep"])

            # 处理：后续列车要额外等待先到列车
            for i in range(1, len(trains_at_station)):
                prev_train = trains_at_station[i-1]
                curr_train = trains_at_station[i]

                prev_dep = prev_train["current_dep"]
                curr_dep = curr_train["current_dep"]

                # 计算需要的间隔（比FCFS更保守：额外增加1分钟）
                required_interval = self.headway_time + DispatchEnvConfig.eaf_extra_headway_seconds()
                required_dep = prev_dep + required_interval

                if curr_dep < required_dep:
                    # 需要额外等待
                    wait_time = required_dep - curr_dep

                    # 更新该列车及其后续所有站点
                    curr_stops = schedule[curr_train["train_id"]]
                    found = False
                    for stop in curr_stops:
                        if stop["station_code"] == station_code:
                            found = True
                        if found:
                            stop["departure_seconds"] += wait_time
                            stop["arrival_seconds"] += wait_time
                            stop["delay_seconds"] += wait_time

        # Step 5: 转换时间格式并计算统计
        for train_id in schedule:
            for stop in schedule[train_id]:
                stop["arrival_time"] = _BaseSolverTools.seconds_to_time(stop["arrival_seconds"])
                stop["departure_time"] = _BaseSolverTools.seconds_to_time(stop["departure_seconds"])

        computation_time = time.time() - start_time

        # 统一调用 MetricsDefinition.calculate_metrics 保证指标口径一致
        metrics = MetricsDefinition.calculate_metrics(
            schedule,
            self.get_original_schedule(),
            computation_time
        )

        return SchedulerResult(
            success=True,
            scheduler_name=self.name,
            scheduler_type=self.scheduler_type,
            optimized_schedule=schedule,
            metrics=metrics,
            message="最早到站优先调度器：后续列车为之前列车让行（保守策略）"
        )

        return SchedulerResult(
            success=True,
            scheduler_name=self.name,
            scheduler_type=self.scheduler_type,
            optimized_schedule=schedule,
            metrics=metrics,
            message="最早到站优先调度器：后续列车为之前列车让行（保守策略）"
        )

    def _get_original_departure(self, train_id: str, station_code: str) -> int:
        """获取列车在车站的原始发车时间"""
        for train in self.trains:
            if train.train_id == train_id:
                for stop in train.schedule.stops:
                    if stop.station_code == station_code:
                        return _BaseSolverTools.time_to_seconds(stop.departure_time)
        return 0


# ============================================================================
# 分层求解器适配器
# ============================================================================

class HierarchicalSchedulerAdapter(BaseScheduler):
    """
    分层求解器适配器（Hierarchical Solver）

    核心思想：结合 FCFS 快速筛选 + MIP 精准优化 + 质量评估

    工作流程：
    1. Layer 1: FCFS 快速筛选（毫秒级）→ 识别受影响列车
    2. Layer 2: MIP 精准优化（秒级）→ 只对30列以内的关键列车优化
    3. Layer 3: 质量评估 → 判断是否接受MIP结果

    优势：
    - 解决大规模问题：147列 × 13站 → MIP超时(>300秒)
    - 自动裁剪：25列 × 8站 → MIP在30-60秒内求解
    - 质量保证：延误比纯FCFS减少30-60%
    - 自适应：根据问题难度自动选择最佳路径
    - 鲁棒性：MIP失败时自动回退到FCFS
    """

    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        **kwargs
    ):
        super().__init__(trains, stations, name="分层求解器", **kwargs)
        self._solver = None

    def _get_solver(self):
        """创建新的分层求解器实例（避免数据污染）"""
        from railway_agent.hierarchical_solver import HierarchicalSolver
        return HierarchicalSolver()

    @property
    def scheduler_type(self) -> SchedulerType:
        return SchedulerType.HIERARCHICAL

    def solve(
        self,
        delay_injection: DelayInjection,
        objective: str = "min_total_delay"
    ) -> SchedulerResult:
        start_time = time.time()

        try:
            solver = self._get_solver()

            # 转换 trains 和 stations 为列表格式（HierarchicalSolver需要）
            trains_list = []
            for train in self.trains:
                trains_list.append({
                    'train_id': train.train_id,
                    'train_type': getattr(train, 'train_type', 'G'),
                    'schedule': {
                        'stops': [
                            {
                                'station_code': s.station_code,
                                'station_name': getattr(s, 'station_name', ''),
                                'departure_time': s.departure_time,
                                'arrival_time': s.arrival_time,
                                'stop_duration': getattr(s, 'stop_duration', 0)
                            }
                            for s in (train.schedule.stops if train.schedule else [])
                        ]
                    }
                })

            stations_list = []
            for station in self.stations:
                stations_list.append({
                    'station_code': station.station_code,
                    'station_name': station.station_name,
                    'track_count': station.track_count,
                    'node_type': station.node_type
                })

            # 执行分层求解
            result = solver.solve(
                all_trains=trains_list,
                all_stations=stations_list,
                delay_injection=delay_injection,
                solver_config={'optimization_objective': objective}
            )

            # 转换结果格式
            if not result.success:
                return SchedulerResult(
                    success=False,
                    scheduler_name=self.name,
                    scheduler_type=self.scheduler_type,
                    optimized_schedule={},
                    metrics=EvaluationMetrics(),
                    message=f"分层求解失败: {result.message}"
                )

            # 计算指标：使用统一的 MetricsDefinition.calculate_metrics 保证与其他求解器口径一致
            schedule = result.schedule or {}
            evaluation_metrics = MetricsDefinition.calculate_metrics(
                schedule,
                self.get_original_schedule(),
                result.solving_time
            )

            return SchedulerResult(
                success=True,
                scheduler_name=self.name,
                scheduler_type=self.scheduler_type,
                optimized_schedule=result.schedule or {},
                metrics=evaluation_metrics,
                message=f"分层求解: {result.message} (模式: {result.solver_mode})"
            )

        except Exception as e:
            logger.error(f"[HierarchicalSchedulerAdapter] 求解失败: {e}")
            return SchedulerResult(
                success=False,
                scheduler_name=self.name,
                scheduler_type=self.scheduler_type,
                optimized_schedule={},
                metrics=EvaluationMetrics(),
                message=f"分层求解异常: {str(e)}"
            )


# ============================================================================
# SPT、SRPT调度器适配器（已废弃）
# ============================================================================
# 原因：SPT（最短处理时间优先）和SRPT（最短剩余处理时间优先）不符合
#      高铁按图行车的调度原则。高铁调度必须严格遵循时刻表，
#      不能随意调整列车顺序。这些算法适用于CPU调度等场景，
#      不适用于固定时刻表的铁路调度。
#
# 如需了解移除原因，请参考：
# - solver/README.md
# - solver_registry.py 注释说明
#
# SPT/SRPT 适配器已移除：不符合高铁按图行车原则
# ============================================================================

# 注册内置调度器（只注册实际可用的，避免别名重复出现在列表中）
SchedulerRegistry.register("fcfs", FCFSSchedulerAdapter)
SchedulerRegistry.register("mip", MIPSchedulerAdapter)
SchedulerRegistry.register("noop", NoOpSchedulerAdapter)
SchedulerRegistry.register("max-delay-first", MaxDelayFirstSchedulerAdapter)
SchedulerRegistry.register("hierarchical", HierarchicalSchedulerAdapter)
SchedulerRegistry.register("eaf", EarliestArrivalFirstScheduler)


# 测试代码
if __name__ == "__main__":
    from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
    
    use_real_data(True)
    trains = get_trains_pydantic()[:10]
    stations = get_stations_pydantic()
    
    print("已注册的调度器:", SchedulerRegistry.list_available())
    
    # 测试创建调度器
    fcfs = SchedulerRegistry.create("fcfs", trains, stations)
    print(f"FCFS调度器: {fcfs.description}")
    
    mip = SchedulerRegistry.create("mip", trains, stations)
    print(f"MIP调度器: {mip.description}")
