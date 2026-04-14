# -*- coding: utf-8 -*-
"""
第三层：求解技能层
根据L2的planning_intent选择并执行求解器
"""

import logging
from typing import Dict, Any, List, Optional

from models.workflow_models import AccidentCard
from models.common_enums import SolverTypeCode
from solver.solver_registry import get_default_registry
from solver.base_solver import SolverRequest

logger = logging.getLogger(__name__)


class Layer3Solver:
    """
    第三层：求解技能层 (L3 Solver Layer)
    
    职责：
    1. 根据L2输出的planning_intent和事故卡片选择最合适的求解器
    2. 构建求解请求（使用整个时刻表作为基准，而非仅网络快照中的列车）
    3. 执行求解并返回结果
    4. 处理求解过程中的异常和错误
    
    注意：
    - L3不使用LLM，完全基于规则选择求解器
    - 求解时使用全部列车数据（整个时刻表），确保考虑所有列车的相互影响
    - 网络快照仅用于参考，不限制求解范围
    """

    def __init__(self):
        """初始化第三层"""
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
        """
        执行第三层求解

        Args:
            planning_intent: 第二层输出的技能意图
            accident_card: 事故卡片
            trains: 列车数据（可选，默认使用完整时刻表）
            stations: 车站数据（可选，默认使用完整时刻表）
            planner_decision: Planner决策（可选，包含 solver_candidates, preferred_solver 等）
            network_snapshot: 网络快照（可选）

        Returns:
            Dict: 包含求解结果的字典
        """
        logger.info("[L3] 求解技能层")

        # 选择求解器（优先使用 L2 的 preferred_solver，再经过规则校验）
        main_skill = self.select_solver(
            planning_intent=planning_intent,
            scene_category=accident_card.scene_category,
            train_count=len(accident_card.affected_train_ids) if accident_card.affected_train_ids else 0,
            is_complete=accident_card.is_complete,
            planner_decision=planner_decision
        )

        logger.info(f"[L3] 主技能: {main_skill}")

        # 获取求解器实例（所有场景统一处理）
        solver = self._get_solver(main_skill)
        if solver is None:
            return self._build_error_result(main_skill, f"无法获取求解器: {main_skill}")

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

            # 构建solver_response字典，包含优化后的时刻表
            solver_response_dict = solver_response.model_dump() if hasattr(solver_response, 'model_dump') else {
                "success": solver_response.success,
                "status": solver_response.status,
                "total_delay_minutes": total_delay,
                "max_delay_minutes": max_delay,
                "message": solver_response.message,
                "optimized_schedule": solver_response.schedule if hasattr(solver_response, 'schedule') else {}
            }
            
            # 获取原始时刻表用于基线对比
            original_schedule = {}
            if trains:
                try:
                    for t in trains:
                        if hasattr(t, 'train_id'):
                            original_schedule[t.train_id] = t.model_dump() if hasattr(t, 'model_dump') else t
                        elif isinstance(t, dict):
                            train_id = t.get('train_id', t.get('id', 'unknown'))
                            original_schedule[train_id] = t
                except Exception as e:
                    logger.warning(f"构建原始时刻表时出错: {e}")
            
            # 从accident_card提取位置信息
            location_code = accident_card.location_code if accident_card.location_code else ""
            location_name = accident_card.location_name if accident_card.location_name else ""
            location_type = accident_card.location_type if accident_card.location_type else "station"
            
            # 构建延误注入信息用于基线对比
            # 从accident_card获取延误时间（分钟转秒）
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
                    "affected_trains": accident_card.affected_train_ids if accident_card.affected_train_ids else [],
                    "original_schedule": original_schedule,  # 添加原始时刻表用于基线对比
                    "delay_injection": delay_injection_info,  # 添加延误注入信息
                    "_response_source": "rule_based_solver",
                    "_response_note": "【规则执行】L3层使用规则选择并执行求解器，不涉及LLM"
                },
                "solver_response": solver_response_dict,
                "schedule": solver_response.schedule if hasattr(solver_response, 'schedule') else {},
                "metrics": solver_response.metrics if hasattr(solver_response, 'metrics') else {},
                "llm_response": f"【规则执行】执行{main_skill}求解器，状态: {solver_response.status} (L3层不使用LLM)"
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
        选择求解器（优先考虑 L2 的 preferred_solver，再经过规则校验）

        Args:
            planning_intent: 技能意图
            scene_category: 场景类型
            train_count: 列车数量
            is_complete: 信息是否完整
            planner_decision: L2 输出的 PlannerDecision 结构化信息

        Returns:
            str: 求解器名称
        """
        logger.info("[L3] 选择求解器")

        # 规则1：区间封锁 -> FCFS
        if scene_category == "区间封锁" or planning_intent == "handle_section_block":
            logger.info("[L3] 规则1：区间封锁，强制使用 fcfs")
            return "fcfs"

        # 规则2：信息不完整 -> FCFS（无法被覆盖）
        if not is_complete:
            logger.info("[L3] 规则2：信息不完整，强制使用 fcfs")
            return "fcfs"

        # 尝试使用 L2 的 preferred_solver
        preferred_solver = None
        if planner_decision and isinstance(planner_decision, dict):
            preferred_solver = planner_decision.get("preferred_solver")
            solver_candidates = planner_decision.get("solver_candidates", [])

            if preferred_solver:
                logger.info(f"[L3] L2 建议的 preferred_solver: {preferred_solver}")
                logger.info(f"[L3] L2 建议的 solver_candidates: {solver_candidates}")

                # 验证 preferred_solver 是否有效
                valid_solvers = ["mip", "fcfs", "max_delay_first", "noop"]
                if preferred_solver in valid_solvers:
                    # 进一步规则校验
                    if scene_category == "临时限速" and preferred_solver == "mip":
                        logger.info("[L3] L2 建议通过：临时限速 -> mip")
                        return preferred_solver
                    elif scene_category == "突发故障" and preferred_solver == "fcfs":
                        logger.info("[L3] L2 建议通过：突发故障 -> fcfs")
                        return preferred_solver
                    else:
                        # 如果场景与 solver 不匹配，记录警告但继续使用建议的 solver
                        logger.warning(f"[L3] L2 建议的 solver ({preferred_solver}) 与场景 ({scene_category}) 不匹配，但仍使用建议的 solver")
                        return preferred_solver
                else:
                    logger.warning(f"[L3] L2 建议的 preferred_solver ({preferred_solver}) 不是有效的 solver，使用规则校验")

        # 规则3：列车数量少（<=3）且完整 -> MIP
        if train_count <= 3 and is_complete:
            logger.info("[L3] 规则3：列车数<=3，使用 mip")
            return "mip"

        # 规则4：列车数量多 -> FCFS
        if train_count > 10:
            logger.info("[L3] 规则4：列车数>10，使用 fcfs")
            return "fcfs"

        # 规则5：默认 -> MIP
        logger.info("[L3] 规则5：默认使用 mip")
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
        affected_trains = accident_card.affected_train_ids if hasattr(accident_card, 'affected_train_ids') and accident_card.affected_train_ids else ["G1563"]
        location_code = accident_card.location_code if accident_card.location_code else "SJP"
        # 默认延误时间从配置读取（10分钟）
        from config import DispatchEnvConfig
        default_delay = DispatchEnvConfig.get("constraints.default_min_section_time", 600)
        delay_seconds = int(accident_card.expected_duration * 60) if accident_card.expected_duration else default_delay

        injected_delays = []
        for train_id in affected_trains:
            injected_delays.append({
                "train_id": train_id,
                "location": {"location_type": "station", "station_code": location_code},
                "initial_delay_seconds": delay_seconds,
                "timestamp": "2024-01-15T10:00:00"
            })

        # 使用全部列车数据（整个时刻表）进行调度，而不是仅使用快照中的列车
        # 这样可以确保求解器考虑所有列车的相互影响
        from models.data_loader import load_trains, load_stations
        all_trains = load_trains()
        all_stations = load_stations()

        # 将场景类别转换为scenario_type
        scenario_type = self._map_scene_to_scenario_type(accident_card.scene_category)

        return SolverRequest(
            scene_type=scenario_type,
            scene_id="llm_workflow_001",
            trains=all_trains,
            stations=all_stations,
            injected_delays=injected_delays,
            solver_config={},
            metadata={
                "accident_card": accident_card.model_dump(),
                "original_trains_count": len(trains),
                "all_trains_count": len(all_trains),
                "note": "使用整个时刻表作为调度基准",
                "scenario_type": scenario_type
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
                "original_schedule": {},  # 添加空原始时刻表
                "delay_injection": {"injected_delays": []}  # 添加空延误注入信息
            },
            "solver_response": None,
            "llm_response": f"执行失败: {error_message}"
        }
