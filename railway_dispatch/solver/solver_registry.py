# -*- coding: utf-8 -*-
"""
求解器注册器模块
【已废弃】请使用 scheduler_comparison.scheduler_interface.SchedulerRegistry

废弃原因：
1. 架构重复：与 Scheduler 系统功能重叠
2. 维护困难：需要同时维护两套适配器
3. 接口不一致：导致使用困惑

替代方案：
使用 scheduler_comparison.scheduler_interface.SchedulerRegistry

迁移日期：2026-04-21
计划完全移除日期：2026-06-01
"""

import warnings
from typing import Dict, Optional, Type
import logging

from solver.base_solver import BaseSolver, SolverRequest, SolverResponse

logger = logging.getLogger(__name__)

# 添加废弃警告
warnings.warn(
    "SolverRegistry已废弃，请使用SchedulerRegistry。"
    "参考：scheduler_comparison.scheduler_interface.SchedulerRegistry",
    DeprecationWarning,
    stacklevel=2
)


class SolverRegistry:
    """
    求解器注册器【已废弃】

    请使用 scheduler_comparison.scheduler_interface.SchedulerRegistry 代替

    废弃原因：
    - 与 SchedulerRegistry（scheduler_comparison）功能重复
    - 导致架构混乱和维护困难
    - 两套系统接口不一致

    迁移示例：
        # 旧代码（废弃）
        from solver.solver_registry import SolverRegistry
        solver = SolverRegistry.get_solver("mip")

        # 新代码（推荐）
        from scheduler_comparison.scheduler_interface import SchedulerRegistry
        scheduler = SchedulerRegistry.create("mip", trains, stations)
    """

    _solvers: Dict[str, BaseSolver] = {}
    _solver_classes: Dict[str, Type[BaseSolver]] = {}

    @classmethod
    def register(cls, name: str, solver: BaseSolver):
        """
        注册求解器实例【已废弃】

        请使用 scheduler_comparison.scheduler_interface.SchedulerRegistry.register()
        """
        warnings.warn(
            f"SolverRegistry.register()已废弃，请使用SchedulerRegistry.register()：{name}",
            DeprecationWarning,
            stacklevel=2
        )
        cls._solvers[name] = solver
        logger.debug(f"Registered solver: {name}")

    @classmethod
    def register_class(cls, name: str, solver_class: Type[BaseSolver]):
        """
        注册求解器类【已废弃】

        请使用 scheduler_comparison.scheduler_interface.SchedulerRegistry.register()
        """
        warnings.warn(
            f"SolverRegistry.register_class()已废弃，请使用SchedulerRegistry.register()：{name}",
            DeprecationWarning,
            stacklevel=2
        )
        cls._solver_classes[name] = solver_class
        logger.debug(f"Registered solver class: {name}")

    @classmethod
    def get_solver(cls, name: str) -> Optional[BaseSolver]:
        """
        获取求解器实例【已废弃】

        请使用 scheduler_comparison.scheduler_interface.SchedulerRegistry.create()

        迁移示例：
            # 旧代码
            solver = SolverRegistry.get_solver("mip")

            # 新代码
            scheduler = SchedulerRegistry.create("mip", trains, stations)
        """
        warnings.warn(
            f"SolverRegistry.get_solver()已废弃，请使用SchedulerRegistry.create()：{name}",
            DeprecationWarning,
            stacklevel=2
        )
        # 先从实例字典获取
        solver = cls._solvers.get(name)
        if solver is not None:
            return solver

        # 如果实例不存在，尝试从类字典创建
        solver_class = cls._solver_classes.get(name)
        if solver_class:
            try:
                solver = solver_class()
                cls.register(name, solver)  # 缓存实例
                return solver
            except Exception as e:
                logger.warning(f"创建求解器实例失败 {name}: {e}")
                return None

        return None

    @classmethod
    def list_solvers(cls) -> list:
        """
        列出所有已注册的求解器

        Returns:
            list: 求解器名称列表
        """
        return list(cls._solvers.keys())

    @classmethod
    def select_solver(cls, scene_type: str, config: Dict = None) -> Optional[BaseSolver]:
        """
        根据场景类型选择求解器【已废弃】

        请直接创建调度器实例

        迁移示例：
            # 旧代码
            solver = SolverRegistry.select_solver("TEMP_SPEED_LIMIT")

            # 新代码
            from scheduler_comparison.comparator import create_comparator
            comparator = create_comparator(trains, stations)
            scheduler = comparator.get_scheduler("mip")
        """
        warnings.warn(
            "SolverRegistry.select_solver()已废弃，请直接创建调度器实例",
            DeprecationWarning,
            stacklevel=2
        )
        config = config or {}

        # 从配置读取默认求解器
        import sys
        import os
        sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from config import DispatchEnvConfig

        solver_name = DispatchEnvConfig.get_default_solver(scene_type)

        # 允许通过 config 覆盖
        if "solver" in config:
            solver_name = config["solver"]

        # 查找并返回求解器
        solver = cls.get_solver(solver_name)
        if solver is None:
            # 如果实例不存在，尝试创建
            solver_class = cls._solver_classes.get(solver_name)
            if solver_class:
                # 创建默认实例
                try:
                    solver = solver_class()
                    cls.register(solver_name, solver)
                except Exception as e:
                    logger.error(f"Failed to create solver {solver_name}: {e}")
                    return None

        if solver is None:
            logger.warning(f"No solver found for scene_type: {scene_type}")
        else:
            logger.info(f"Selected solver: {solver_name} for scene_type: {scene_type}")

        return solver


def get_default_registry() -> SolverRegistry:
    """
    获取默认求解器注册器【已废弃】

    替代方案：
        from scheduler_comparison.comparator import create_comparator
        comparator = create_comparator(trains, stations)

    注意：
    - 适配器层已移除：fcfs_adapter.py, mip_adapter.py, max_delay_first_adapter.py, noop_adapter.py
    - 核心调度器保留：fcfs_scheduler.py, mip_scheduler.py, max_delay_first_scheduler.py, noop_scheduler.py
    - 请使用 Scheduler 系统（scheduler_comparison/）进行调度器注册和管理

    Returns:
        SolverRegistry: 求解器注册器实例（废弃）
    """
    warnings.warn(
        "get_default_registry()已废弃，请使用create_comparator()",
        DeprecationWarning,
        stacklevel=2
    )
    # 如果还没有注册过求解器，则注册
    if not SolverRegistry.list_solvers():
        # 注意：适配器文件已删除，这里会报错
        # 这是故意为之，以强制用户迁移到 Scheduler 系统
        logger.warning(
            "Solver系统的适配器层已删除（fcfs_adapter.py, mip_adapter.py等）。"
            "请使用 scheduler_comparison.scheduler_interface.SchedulerRegistry"
        )

        # 不再注册任何求解器，强制迁移
        pass

    return SolverRegistry