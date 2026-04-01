# -*- coding: utf-8 -*-
"""
求解器注册器模块
管理求解器注册和选择
"""

from typing import Dict, Optional, Type
import logging

from solver.base_solver import BaseSolver, SolverRequest, SolverResponse

logger = logging.getLogger(__name__)


class SolverRegistry:
    """
    求解器注册器
    管理所有可用求解器，提供选择功能
    """

    _solvers: Dict[str, BaseSolver] = {}
    _solver_classes: Dict[str, Type[BaseSolver]] = {}

    @classmethod
    def register(cls, name: str, solver: BaseSolver):
        """
        注册求解器实例

        Args:
            name: 求解器名称
            solver: 求解器实例
        """
        cls._solvers[name] = solver
        logger.info(f"Registered solver: {name}")

    @classmethod
    def register_class(cls, name: str, solver_class: Type[BaseSolver]):
        """
        注册求解器类

        Args:
            name: 求解器名称
            solver_class: 求解器类
        """
        cls._solver_classes[name] = solver_class
        logger.info(f"Registered solver class: {name}")

    @classmethod
    def get_solver(cls, name: str) -> Optional[BaseSolver]:
        """
        获取求解器实例

        Args:
            name: 求解器名称

        Returns:
            BaseSolver: 求解器实例，如果不存在返回 None
        """
        return cls._solvers.get(name)

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
        根据场景类型选择求解器
        初版使用规则选择，不依赖LLM

        Args:
            scene_type: 场景类型
            config: 配置参数（可选）

        Returns:
            BaseSolver: 选中的求解器实例，如果无匹配返回 None
        """
        config = config or {}

        # 规则选择策略
        solver_name = None

        # 根据场景类型选择求解器
        if scene_type == "temporary_speed_limit":
            # 临时限速：使用 MIP（更精确）
            solver_name = "mip"
        elif scene_type == "sudden_failure":
            # 突发故障：使用 FCFS（快速响应）
            solver_name = "fcfs"
        elif scene_type == "section_interrupt":
            # 区间中断：使用 MIP（优化能力强）
            solver_name = "mip"
        else:
            # 默认使用 FCFS
            solver_name = "fcfs"

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
    获取默认求解器注册器（已预注册 FCFS 和 MIP）

    Returns:
        SolverRegistry: 求解器注册器实例
    """
    # 如果还没有注册过求解器，则注册
    if not SolverRegistry.list_solvers():
        from solver.fcfs_adapter import FCFSSolverAdapter
        from solver.mip_adapter import MIPSolverAdapter

        # 注册求解器类
        SolverRegistry.register_class("fcfs", FCFSSolverAdapter)
        SolverRegistry.register_class("mip", MIPSolverAdapter)

    return SolverRegistry