# -*- coding: utf-8 -*-
"""
第三层：求解技能层（简化版）
根据L2的智能决策选择并执行求解器，将L2推荐的参数传递给求解器
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from models.workflow_models import AccidentCard
from solver.solver_registry import get_default_registry
from solver.base_solver import SolverRequest
from config import DispatchEnvConfig

logger = logging.getLogger(__name__)


class Layer3Solver:
    """
    第三层：求解技能层 (L3 Solver Layer) - 简化版

    职责：
    1. 接收L2的智能决策（preferred_solver, solver_config）
    2. 将solver_config中的参数传递给求解器（time_limit等）
    3. 执行求解并返回结果
    """

    def __init__(self):
        self.registry = None

    def execute(
        self,
        planning_intent: str,
        accident_card: AccidentCard,
        trains: Optional[List[Any]] = None,
        stations: Optional[List[Any]] = None,
        planner_decision: Optional[Dict[str, Any]] = None,
        network_snapshot: Optional[Any] = None
    ) -> Dict[str, Any]:
        """执行第三层求解"""
        logger.debug("[L3] 求解技能层")

        # 从L2决策中提取solver配置
        solver_config = {}
        if planner_decision and isinstance(planner_decision, dict):
            solver_config = planner_decision.get("solver_config", {})

        # 选择求解器（优先L2推荐）
        main_skill = self.select_solver(
            planning_intent=planning_intent,
            scene_category=accident_card.scene_category,
            train_count=len(accident_card.affected_train_ids) if accident_card.affected_train_ids else 0,
            is_complete=accident_card.is_complete,
            planner_decision=planner_decision
        )

        logger.debug(f"[L3] 执行求解器: {main_skill}, 参数: {solver_config}")

        # 获取求解器实例
        solver = self._get_solver(main_skill)
        if solver is None:
            return self._build_error_result(main_skill, f"无法获取求解器: {main_skill}")

        # 构建求解请求（传入L2的solver_config）
        solver_request = self._build_solver_request(accident_card, trains, stations, solver_config)

        # 执行求解
        try:
            solver_response = solver.solve(solver_request)

            logger.info(f"[L3] 求解完成: solver={main_skill}, 状态={solver_response.status}, "
                       f"成功={solver_response.success}, 耗时={solver_response.solving_time_seconds:.2f}秒")

            # 提取指标
            metrics = solver_response.metrics or {}
            total_delay_seconds = metrics.get("total_delay_seconds", 0)
            max_delay_seconds = metrics.get("max_delay_seconds", 0)
            total_delay = total_delay_seconds // 60
            max_delay = max_delay_seconds // 60
            avg_delay = metrics.get("avg_delay_seconds", 0) / 60 if metrics.get("avg_delay_seconds") else 0
            affected_trains = len(accident_card.affected_train_ids) if accident_card.affected_train_ids else 0

            if solver_response.success:
                logger.info("=" * 50)
                logger.info("【L3求解结果】")
                logger.info(f"  求解器: {main_skill}")
                logger.info(f"  总延误: {total_delay}分钟")
                logger.info(f"  最大延误: {max_delay}分钟")
                logger.info(f"  平均延误: {avg_delay:.2f}分钟")
                logger.info(f"  影响列车数: {affected_trains}列")
                logger.info("=" * 50)

            # 构建solver_response字典
            solver_response_dict = solver_response.model_dump() if hasattr(solver_response, 'model_dump') else {
                "success": solver_response.success,
                "status": solver_response.status,
                "total_delay_minutes": total_delay,
                "max_delay_minutes": max_delay,
                "message": solver_response.message,
                "optimized_schedule": solver_response.schedule if hasattr(solver_response, 'schedule') else {}
            }

            # 提取位置信息
            location_code = accident_card.location_code or ""
            location_name = accident_card.location_name or ""
            location_type = accident_card.location_type or "station"

            # 构建延误注入信息
            delay_minutes = accident_card.expected_duration if accident_card.expected_duration else 10
            delay_seconds = delay_minutes * 60

            delay_injection_info = {
                "injected_delays": [
                    {
                        "train_id": train_id,
                        "location": {"location_type": location_type, "station_code": location_code},
                        "initial_delay_seconds": delay_seconds
                    }
                    for train_id in (accident_card.affected_train_ids if accident_card.affected_train_ids else [])
                ]
            }

            response_note = f"求解器: {main_skill}, 参数: {solver_config}"

            return {
                "skill_execution_result": {
                    "skill_name": main_skill,
                    "execution_status": solver_response.status,
                    "success": solver_response.success,
                    "solving_time": solver_response.solving_time_seconds,
                    "total_delay_minutes": total_delay,
                    "max_delay_minutes": max_delay,
                    "location": location_name or location_code or "未知位置",
                    "location_code": location_code,
                    "location_type": location_type,
                    "scenario_type": self._map_scene_to_scenario_type(accident_card.scene_category),
                    "affected_trains": accident_card.affected_train_ids or [],
                    "affected_trains_count": affected_trains,
                    "delay_injection": delay_injection_info,
                    "solver_config_used": solver_config,
                    "_response_note": response_note
                },
                "solver_response": solver_response_dict,
                "metrics": metrics,
                "llm_response": response_note
            }

        except Exception as e:
            logger.error(f"第三层执行失败: {e}")
            return self._build_error_result(main_skill, str(e))

    def select_solver(
        self,
        planning_intent: str,
        scene_category: str,
        train_count: int,
        is_complete: bool,
        planner_decision: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        选择求解器
        优先使用L2的preferred_solver（LLM智能决策），规则仅做安全兜底
        """
        logger.debug("[L3] 选择求解器")

        # 区间封锁：强制FCFS（安全约束，不可覆盖）
        if scene_category == "区间封锁" or planning_intent == "handle_section_block":
            logger.debug("[L3] 区间封锁场景，强制使用fcfs")
            return "fcfs"

        # 信息不完整：强制FCFS（无法精确优化）
        if not is_complete:
            logger.debug("[L3] 信息不完整，强制使用fcfs")
            return "fcfs"

        # 使用L2智能决策的preferred_solver
        if planner_decision and isinstance(planner_decision, dict):
            preferred_solver = planner_decision.get("preferred_solver")
            if preferred_solver:
                valid_solvers = ["mip", "fcfs", "max_delay_first", "noop"]
                if preferred_solver in valid_solvers:
                    logger.debug(f"[L3] 使用L2推荐的solver: {preferred_solver}")
                    return preferred_solver
                else:
                    logger.warning(f"[L3] L2推荐的solver无效: {preferred_solver}，回退到默认")

        # 规则兜底
        if train_count <= 3 and is_complete:
            logger.debug("[L3] 规则兜底: 列车数≤3，使用mip")
            return "mip"
        if train_count > 10:
            logger.debug("[L3] 规则兜底: 列车数>10，使用fcfs")
            return "fcfs"

        logger.debug("[L3] 规则兜底: 默认使用mip")
        return "mip"

    def _get_solver(self, solver_name: str):
        """获取求解器实例"""
        if self.registry is None:
            self.registry = get_default_registry()

        solver = self.registry.get_solver(solver_name)
        if solver is not None:
            return solver

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
        stations: List[Any],
        solver_config: Dict[str, Any] = None
    ) -> SolverRequest:
        """构建求解请求，将L2的solver_config合并传入"""
        solver_config = solver_config or {}

        # 构建延误注入
        affected_trains = accident_card.affected_train_ids if hasattr(accident_card, 'affected_train_ids') and accident_card.affected_train_ids else ["G1563"]
        location_code = accident_card.location_code if accident_card.location_code else "SJP"
        default_delay = DispatchEnvConfig.default_delay_seconds()
        delay_seconds = int(accident_card.expected_duration * 60) if accident_card.expected_duration else default_delay

        injected_delays = []
        for train_id in affected_trains:
            injected_delays.append({
                "train_id": train_id,
                "location": {"location_type": "station", "station_code": location_code},
                "initial_delay_seconds": delay_seconds,
                "timestamp": datetime.now().isoformat()
            })

        # 使用全部列车数据
        from models.data_loader import load_trains, load_stations
        all_trains = load_trains()
        all_stations = load_stations()

        scenario_type = self._map_scene_to_scenario_type(accident_card.scene_category)

        return SolverRequest(
            scene_type=scenario_type,
            scene_id="llm_workflow_001",
            trains=all_trains,
            stations=all_stations,
            injected_delays=injected_delays,
            solver_config=solver_config,
            metadata={
                "accident_card": accident_card.model_dump(),
                "scenario_type": scenario_type,
                "l2_solver_config": solver_config
            }
        )

    def _map_scene_to_scenario_type(self, scene_category: str) -> str:
        """将场景类别映射到场景类型"""
        mapping = {
            "临时限速": "temporary_speed_limit",
            "突发故障": "sudden_failure",
            "区间封锁": "section_interrupt"
        }
        return mapping.get(scene_category, "temporary_speed_limit")

    def _build_error_result(self, solver_name: str, error_message: str) -> Dict[str, Any]:
        """构建错误结果"""
        return {
            "skill_execution_result": {
                "skill_name": solver_name,
                "execution_status": "error",
                "success": False,
                "error_message": error_message,
                "total_delay_minutes": 0,
                "max_delay_minutes": 0,
                "location": "未知位置",
                "location_code": "",
                "location_type": "station",
                "scenario_type": "temporary_speed_limit",
                "affected_trains": [],
                "delay_injection": {"injected_delays": []},
                "solver_config_used": {}
            },
            "solver_response": None,
            "llm_response": f"执行失败: {error_message}"
        }
