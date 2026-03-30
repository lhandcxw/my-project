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

logger = logging.getLogger(__name__)


class SchedulerType(str, Enum):
    """调度器类型枚举"""
    FCFS = "fcfs"
    MIP = "mip"
    RL = "reinforcement_learning"  # 强化学习
    GREEDY = "greedy"
    GENETIC = "genetic"  # 遗传算法
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
        objective: str = "min_max_delay"
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


class FCFSSchedulerAdapter(BaseScheduler):
    """
    FCFS调度器适配器
    封装现有的FCFS调度器实现
    """
    
    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        headway_time: int = 180,
        min_stop_time: int = 60,
        **kwargs
    ):
        super().__init__(trains, stations, name="FCFS调度器", **kwargs)
        self.headway_time = headway_time
        self.min_stop_time = min_stop_time
        
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
        objective: str = "min_max_delay"
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
        headway_time: int = 180,
        min_stop_time: int = 60,
        **kwargs
    ):
        super().__init__(trains, stations, name="MIP调度器", **kwargs)
        self.headway_time = headway_time
        self.min_stop_time = min_stop_time
        
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
        objective: str = "min_max_delay"
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


class ReinforcementLearningSchedulerAdapter(BaseScheduler):
    """
    强化学习调度器适配器
    为后续强化学习算法预留接口
    """
    
    def __init__(
        self,
        trains: List[Train],
        stations: List[Station],
        model_path: Optional[str] = None,
        **kwargs
    ):
        super().__init__(trains, stations, name="强化学习调度器", **kwargs)
        self.model_path = model_path
        self._model = None
        self._is_available = False
    
    @property
    def scheduler_type(self) -> SchedulerType:
        return SchedulerType.RL
    
    @property
    def is_available(self) -> bool:
        """检查强化学习模型是否可用"""
        return self._is_available
    
    def load_model(self, model_path: str) -> bool:
        """
        加载强化学习模型
        
        Args:
            model_path: 模型路径
        
        Returns:
            是否加载成功
        """
        try:
            # TODO: 实现模型加载逻辑
            # 这里预留接口，具体实现根据使用的RL框架而定
            # 例如：使用stable-baselines3加载PPO模型
            # from stable_baselines3 import PPO
            # self._model = PPO.load(model_path)
            # self._is_available = True
            
            logger.warning("强化学习模型加载功能尚未实现")
            self._is_available = False
            return False
        except Exception as e:
            logger.error(f"加载强化学习模型失败: {e}")
            self._is_available = False
            return False
    
    def solve(
        self,
        delay_injection: DelayInjection,
        objective: str = "min_max_delay"
    ) -> SchedulerResult:
        start_time = time.time()
        
        if not self._is_available or self._model is None:
            # 如果模型不可用，返回模拟结果
            logger.warning("强化学习模型不可用，使用模拟结果")
            return self._generate_mock_result(delay_injection, start_time)
        
        try:
            # TODO: 实现实际的RL推理逻辑
            # observation = self._build_observation(delay_injection)
            # action, _ = self._model.predict(observation)
            # optimized_schedule = self._apply_action(action)
            
            pass
        except Exception as e:
            logger.error(f"强化学习推理失败: {e}")
            return SchedulerResult(
                success=False,
                scheduler_name=self.name,
                scheduler_type=self.scheduler_type,
                optimized_schedule={},
                metrics=EvaluationMetrics(),
                message=f"强化学习推理失败: {e}"
            )
    
    def _generate_mock_result(
        self,
        delay_injection: DelayInjection,
        start_time: float
    ) -> SchedulerResult:
        """生成模拟结果（用于测试和演示）"""
        # 简单模拟：保持原始时刻表
        schedule = self.get_original_schedule()
        
        # 应用初始延误
        for injected in delay_injection.injected_delays:
            train_id = injected.train_id
            station_code = injected.location.station_code
            delay = injected.initial_delay_seconds
            
            if train_id in schedule:
                for stop in schedule[train_id]:
                    if stop["station_code"] == station_code:
                        stop["delay_seconds"] = delay
                        break
        
        computation_time = time.time() - start_time
        metrics = MetricsDefinition.calculate_metrics(schedule, None, computation_time)
        
        return SchedulerResult(
            success=True,
            scheduler_name=self.name,
            scheduler_type=self.scheduler_type,
            optimized_schedule=schedule,
            metrics=metrics,
            message="强化学习调度器（模拟结果）"
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
        include_rl: bool = False,
        **kwargs
    ) -> Dict[str, BaseScheduler]:
        """
        创建所有已注册的调度器实例
        
        Args:
            trains: 列车列表
            stations: 车站列表
            include_rl: 是否包含强化学习调度器
            **kwargs: 其他参数
        
        Returns:
            调度器实例字典 {名称: 实例}
        """
        schedulers = {}
        for name, scheduler_class in cls._registry.items():
            # 跳过RL调度器（如果不需要）
            if name == "rl" and not include_rl:
                continue
            
            try:
                instance = scheduler_class(trains, stations, **kwargs)
                schedulers[name] = instance
            except Exception as e:
                logger.error(f"创建调度器 {name} 失败: {e}")
        
        return schedulers


# 注册内置调度器
SchedulerRegistry.register("fcfs", FCFSSchedulerAdapter)
SchedulerRegistry.register("mip", MIPSchedulerAdapter)
SchedulerRegistry.register("rl", ReinforcementLearningSchedulerAdapter)
SchedulerRegistry.register("reinforcement_learning", ReinforcementLearningSchedulerAdapter)


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
