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
        logger.debug(f"Registered solver: {name}")

    @classmethod
    def register_class(cls, name: str, solver_class: Type[BaseSolver]):
        """
        注册求解器类

        Args:
            name: 求解器名称
            solver_class: 求解器类
        """
        cls._solver_classes[name] = solver_class
        logger.debug(f"Registered solver class: {name}")

    @classmethod
    def get_solver(cls, name: str) -> Optional[BaseSolver]:
        """
        获取求解器实例

        Args:
            name: 求解器名称

        Returns:
            BaseSolver: 求解器实例，如果不存在返回 None
        """
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
        根据场景类型选择求解器
        从配置文件读取默认求解器配置

        Args:
            scene_type: 场景类型
            config: 配置参数（可选）

        Returns:
            BaseSolver: 选中的求解器实例，如果无匹配返回 None
        """
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
    获取默认求解器注册器（已预注册 FCFS、MIP、MaxDelayFirst、FSFS、SRPT、SPT、NoOp）

    Returns:
        SolverRegistry: 求解器注册器实例
    """
    # 如果还没有注册过求解器，则注册
    if not SolverRegistry.list_solvers():
        from solver.fcfs_adapter import FCFSSolverAdapter
        from solver.mip_adapter import MIPSolverAdapter
        from solver.max_delay_first_adapter import MaxDelayFirstSolverAdapter
        from solver.fsfs_adapter import FSFSSolverAdapter
        from solver.srpt_adapter import SRPTSolverAdapter
        from solver.spt_adapter import SPTSolverAdapter
        from solver.noop_adapter import NoOpSolverAdapter

        # 注册求解器类
        SolverRegistry.register_class("fcfs", FCFSSolverAdapter)
        SolverRegistry.register_class("mip", MIPSolverAdapter)
        SolverRegistry.register_class("max_delay_first", MaxDelayFirstSolverAdapter)
        SolverRegistry.register_class("fsfs", FSFSSolverAdapter)
        SolverRegistry.register_class("srpt", SRPTSolverAdapter)
        SolverRegistry.register_class("spt", SPTSolverAdapter)
        SolverRegistry.register_class("noop", NoOpSolverAdapter)

    return SolverRegistry