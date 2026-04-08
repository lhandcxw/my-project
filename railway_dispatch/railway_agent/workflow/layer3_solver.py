# -*- coding: utf-8 -*-
"""
第三层：求解技能层
根据L2的planning_intent选择并执行求解器
"""

import logging
from typing import Dict, Any, List

from models.workflow_models import AccidentCard, NetworkSnapshot
from models.common_enums import SolverTypeCode
from solver.solver_registry import get_default_registry
from solver.base_solver import SolverRequest

logger = logging.getLogger(__name__)


class Layer3Solver:
    """
    第三层：求解技能层
    根据L2的planning_intent选择求解器并执行
    """

    def __init__(self):
        """初始化第三层"""
        self.registry = None

    def execute(
        self,
        planning_intent: str,
        accident_card: AccidentCard,
        network_snapshot: NetworkSnapshot,
        trains: List[Any],
        stations: List[Any]
    ) -> Dict[str, Any]:
        """
        执行第三层求解

        Args:
            planning_intent: 第二层输出的技能意图
            accident_card: 事故卡片
            network_snapshot: 网络快照
            trains: 列车数据
            stations: 车站数据

        Returns:
            Dict: 包含求解结果的字典
        """
        logger.info("========== 第三层：求解技能层 ==========")

        # 选择求解器
        main_skill = self.select_solver(
            planning_intent=planning_intent,
            scene_category=accident_card.scene_category,
            train_count=network_snapshot.train_count if hasattr(network_snapshot, 'train_count') else 0,
            is_complete=accident_card.is_complete
        )

        logger.info(f"第三层执行: planning_intent={planning_intent}, 选择的求解器={main_skill}")

        # 获取求解器实例
        solver = self._get_solver(main_skill)
        if solver is None:
            return self._build_error_result(main_skill, "求解器初始化失败")

        # 构建求解请求
        solver_request = self._build_solver_request(accident_card, trains, stations)

        # 执行求解
        try:
            solver_response = solver.solve(solver_request)

            logger.info(f"第三层完成: 求解状态={solver_response.status}, 成功={solver_response.success}")

            # 提取指标
            metrics = solver_response.metrics or {}
            total_delay_seconds = metrics.get("total_delay_seconds", 0)
            max_delay_seconds = metrics.get("max_delay_seconds", 0)
            total_delay = total_delay_seconds // 60
            max_delay = max_delay_seconds // 60

            return {
                "skill_execution_result": {
                    "skill_name": main_skill,
                    "execution_status": solver_response.status,
                    "success": solver_response.success,
                    "solving_time": solver_response.solving_time_seconds,
                    "total_delay_minutes": total_delay,
                    "max_delay_minutes": max_delay
                },
                "solver_response": solver_response.model_dump() if hasattr(solver_response, 'model_dump') else {
                    "success": solver_response.success,
                    "status": solver_response.status,
                    "total_delay_minutes": total_delay,
                    "max_delay_minutes": max_delay,
                    "message": solver_response.message
                },
                "llm_response": f"执行{main_skill}，状态: {solver_response.status}"
            }

        except Exception as e:
            logger.error(f"第三层执行失败: {e}")
            return self._build_error_result(main_skill, str(e))

    def select_solver(
        self,
        planning_intent: str,
        scene_category: str,
        train_count: int,
        is_complete: bool
    ) -> str:
        """
        选择求解器（基于规则）

        Args:
            planning_intent: 技能意图
            scene_category: 场景类型
            train_count: 列车数量
            is_complete: 信息是否完整

        Returns:
            str: 求解器名称
        """
        # 规则1：区间封锁 -> noop
        if scene_category == "区间封锁" or planning_intent == "handle_section_block":
            return "noop"

        # 规则2：信息不完整 -> FCFS
        if not is_complete:
            return "fcfs"

        # 规则3：列车数量少（<=3）且完整 -> MIP
        if train_count <= 3 and is_complete:
            return "mip"

        # 规则4：列车数量多 -> FCFS
        if train_count > 10:
            return "fcfs"

        # 规则5：默认 -> MIP
        return "mip"

    def _get_solver(self, solver_name: str):
        """获取求解器实例"""
        if self.registry is None:
            self.registry = get_default_registry()

        solver = self.registry.get_solver(solver_name)
        if solver is not None:
            return solver

        # 尝试直接导入
        try:
            if solver_name == "mip":
                from solver.mip_adapter import MIPSolverAdapter
                return MIPSolverAdapter()
            elif solver_name == "fcfs":
                from solver.fcfs_adapter import FCFSSolverAdapter
                return FCFSSolverAdapter()
            elif solver_name == "max_delay_first":
                from solver.max_delay_first_adapter import MaxDelayFirstSolverAdapter
                return MaxDelayFirstSolverAdapter()
            elif solver_name == "noop":
                from solver.noop_adapter import NoOpSolverAdapter
                return NoOpSolverAdapter()
        except Exception as e:
            logger.error(f"加载求解器失败: {e}")

        return None

    def _build_solver_request(
        self,
        accident_card: AccidentCard,
        trains: List[Any],
        stations: List[Any]
    ) -> SolverRequest:
        """构建求解请求"""
        # 构建延误注入
        affected_trains = accident_card.affected_train_ids if hasattr(accident_card, 'affected_train_ids') and accident_card.affected_train_ids else ["G1215"]
        location_code = accident_card.location_code if accident_card.location_code else "SJP"
        delay_seconds = int(accident_card.expected_duration * 60) if accident_card.expected_duration else 600

        injected_delays = []
        for train_id in affected_trains:
            injected_delays.append({
                "train_id": train_id,
                "location": {"location_type": "station", "station_code": location_code},
                "initial_delay_seconds": delay_seconds,
                "timestamp": "2024-01-15T10:00:00"
            })

        return SolverRequest(
            scene_type=accident_card.scene_category,
            scene_id="llm_workflow_001",
            trains=trains,
            stations=stations,
            injected_delays=injected_delays,
            solver_config={},
            metadata={
                "accident_card": accident_card.model_dump()
            }
        )

    def _build_error_result(self, solver_name: str, error_message: str) -> Dict[str, Any]:
        """构建错误结果"""
        return {
            "skill_execution_result": {
                "skill_name": solver_name,
                "execution_status": "error",
                "success": False,
                "error_message": error_message,
                "total_delay_minutes": 0,
                "max_delay_minutes": 0
            },
            "solver_response": None,
            "llm_response": f"执行失败: {error_message}"
        }
