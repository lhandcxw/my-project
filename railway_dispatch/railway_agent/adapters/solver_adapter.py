# -*- coding: utf-8 -*-
"""
Solver 适配器
统一求解器调用接口
"""

from typing import Optional, Dict, Any
import logging

from models.common_enums import SolverTypeCode, PlanningIntentCode

logger = logging.getLogger(__name__)


class SolverAdapter:
    """
    Solver 适配器
    封装求解器调用，提供统一的接口
    """
    
    def __init__(self):
        self._registry = None
    
    def _get_registry(self):
        """获取求解器注册表"""
        if self._registry is None:
            try:
                from solver.solver_registry import get_default_registry
                self._registry = get_default_registry()
            except Exception as e:
                logger.warning(f"获取求解器注册表失败: {e}")
        return self._registry
    
    def select_solver(
        self,
        planning_intent: Optional[PlanningIntentCode] = None,
        scene_type: Optional[str] = None,
        train_count: int = 0,
        is_complete: bool = True
    ) -> SolverTypeCode:
        """
        选择求解器
        
        Args:
            planning_intent: 计划意图
            scene_type: 场景类型
            train_count: 列车数量
            is_complete: 信息是否完整
            
        Returns:
            SolverTypeCode: 求解器类型
        """
        # 规则1：区间封锁 -> noop
        if scene_type == "SECTION_INTERRUPT" or planning_intent == PlanningIntentCode.HANDLE_SECTION_BLOCK:
            return SolverTypeCode.NOOP
        
        # 规则2：信息不完整 -> FCFS
        if not is_complete:
            return SolverTypeCode.FCFS
        
        # 规则3：列车数量少（<=3）且完整 -> MIP
        if train_count <= 3 and is_complete:
            return SolverTypeCode.MIP
        
        # 规则4：列车数量多 -> FCFS
        if train_count > 10:
            return SolverTypeCode.FCFS
        
        # 规则5：默认 -> MIP
        return SolverTypeCode.MIP
    
    def solve(
        self,
        solver_type: SolverTypeCode,
        request: Any
    ) -> Any:
        """
        执行求解
        
        Args:
            solver_type: 求解器类型
            request: 求解请求
            
        Returns:
            Any: 求解结果
        """
        logger.info(f"SolverAdapter 执行求解: {solver_type}")
        
        registry = self._get_registry()
        if registry is None:
            logger.error("求解器注册表不可用")
            return None
        
        # 获取求解器
        solver_name = solver_type.value
        solver = registry.get_solver(solver_name)
        
        if solver is None:
            # 尝试适配器
            try:
                if solver_type == SolverTypeCode.MIP:
                    from solver.mip_adapter import MIPSolverAdapter
                    solver = MIPSolverAdapter()
                elif solver_type == SolverTypeCode.FCFS:
                    from solver.fcfs_adapter import FCFSSolverAdapter
                    solver = FCFSSolverAdapter()
                elif solver_type == SolverTypeCode.MAX_DELAY_FIRST:
                    from solver.max_delay_first_adapter import MaxDelayFirstSolverAdapter
                    solver = MaxDelayFirstSolverAdapter()
                else:
                    logger.warning(f"未知的求解器类型: {solver_type}")
                    return None
            except Exception as e:
                logger.error(f"加载求解器失败: {e}")
                return None
        
        # 执行求解
        try:
            return solver.solve(request)
        except Exception as e:
            logger.error(f"求解执行失败: {e}")
            return None


# 全局实例
_solver_adapter: Optional[SolverAdapter] = None


def get_solver_adapter() -> SolverAdapter:
    """获取 Solver 适配器实例"""
    global _solver_adapter
    if _solver_adapter is None:
        _solver_adapter = SolverAdapter()
    return _solver_adapter