# -*- coding: utf-8 -*-
"""
第二层：Planner层（技能路由层）
根据事故卡片判断问题类型和处理意图，输出结构化 PlannerDecision
"""

import logging
from typing import Dict, Any
import json

from models.workflow_models import AccidentCard
from models.prompts import PromptContext
from railway_agent.adapters.llm_prompt_adapter import get_llm_prompt_adapter
from config import LLMConfig

logger = logging.getLogger(__name__)


class Layer2Planner:
    """
    第二层：Planner层
    使用LLM决策planning_intent和solver建议，规则仅做校验
    输出完整的 PlannerDecision 结构化对象
    """

    def __init__(self):
        """初始化第二层"""
        self.prompt_adapter = get_llm_prompt_adapter()

    def execute(
        self,
        accident_card: AccidentCard,
        enable_rag: bool = True
    ) -> Dict[str, Any]:
        """
        执行第二层规划

        Args:
            accident_card: 第一层生成的事故卡片
            enable_rag: 是否启用RAG

        Returns:
            Dict: 包含完整 PlannerDecision 结构化信息的字典
        """
        logger.debug("[L2] Planner层")

        # 构建Prompt上下文（仅使用accident_card）
        context = PromptContext(
            request_id=f"{accident_card.scene_category}_{accident_card.location_code}",
            scene_category=accident_card.scene_category,
            accident_card=accident_card.model_dump(),
            network_snapshot={},  # 暂不使用
            dispatch_context={}   # 暂不使用
        )

        # 调用LLM
        response = self.prompt_adapter.execute_prompt(
            template_id="l2_planner",
            context=context,
            enable_rag=enable_rag
        )

        # 处理响应
        if response.is_valid and response.parsed_output:
            planning_intent = response.parsed_output.get("planning_intent", "")
            problem_desc = response.parsed_output.get("问题描述", "")
            suggested_window = response.parsed_output.get("建议窗口", "")
            # 优先使用LLM建议的solver
            llm_solver_suggestion = response.parsed_output.get("solver_suggestion", "")
            # 提取 LLM 的 solver 候选列表
            solver_candidates_raw = response.parsed_output.get("solver_candidates", [])
            # 提取目标权重
            objective_weights_raw = response.parsed_output.get("objective_weights", {})
        else:
            # LLM失败
            if LLMConfig.FORCE_LLM_MODE:
                raise RuntimeError("[L2] LLM决策失败，实验中止（FORCE_LLM_MODE=true）")
            logger.warning("[L2] LLM决策失败，使用默认intent")
            planning_intent = self._get_default_intent(accident_card.scene_category)
            problem_desc = f"LLM决策失败，使用默认intent: {planning_intent}"
            suggested_window = ""
            llm_solver_suggestion = ""
            solver_candidates_raw = []
            objective_weights_raw = {}

        # 构建skill_dispatch（使用LLM建议，规则校验）
        skill_dispatch, solver_candidates, preferred_solver = self._build_skill_dispatch(
            planning_intent,
            accident_card.scene_category,
            llm_solver_suggestion,
            solver_candidates_raw
        )

        # 构建目标权重
        objective_weights = self._build_objective_weights(objective_weights_raw, accident_card.scene_category)

        # 判断响应来源
        is_mock = "[MOCK]" in response.model_used if response.model_used else False
        response_source = "模拟响应" if is_mock else "LLM输出"
        logger.info(f"[L2] 规划完成: intent={planning_intent}, 主solver={skill_dispatch['主技能']}")

        # 打印规划决策详情
        logger.info("=" * 50)
        logger.info("【L2规划决策】")
        logger.info(f"  规划意图: {planning_intent}")
        logger.info(f"  主技能: {skill_dispatch['主技能']}")
        logger.info(f"  候选求解器: {solver_candidates}")
        logger.info(f"  偏好求解器: {preferred_solver}")
        logger.info(f"  响应来源: {response_source}")
        logger.info("=" * 50)

        # 返回完整的 PlannerDecision 结构化信息
        return {
            "planning_intent": planning_intent,
            "skill_dispatch": skill_dispatch,
            # PlannerDecision 结构化字段
            "planner_decision": {
                "planning_intent": planning_intent,
                "intent_label": self._get_intent_label(planning_intent),
                "solver_candidates": solver_candidates,
                "preferred_solver": preferred_solver,
                "objective_weights": objective_weights,
                "suggested_window_minutes": int(suggested_window) if suggested_window and suggested_window.isdigit() else None,
                "affected_corridor_hint": f"{accident_card.location_code}_corridor" if accident_card.location_code else None,
                "need_user_clarification": False,
                "confidence": 0.9 if response.is_valid else 0.5,
                "reasoning": problem_desc
            },
            "问题描述": problem_desc,
            "建议窗口": suggested_window,
            "reasoning": response.raw_response if response.raw_response else "使用默认值",
            "llm_response": response.raw_response,
            "llm_response_type": response.model_used,
            "_response_source": response_source
        }

    def _get_default_intent(self, scene_category: str) -> str:
        """根据场景类型获取默认intent"""
        intent_mapping = {
            "临时限速": "recalculate_corridor_schedule",
            "突发故障": "recover_from_disruption",
            "区间封锁": "handle_section_block"
        }
        return intent_mapping.get(scene_category, "recalculate_corridor_schedule")

    def _get_intent_label(self, planning_intent: str) -> str:
        """获取 intent 的中文标签"""
        label_mapping = {
            "recalculate_corridor_schedule": "重新计算走廊时刻表",
            "recover_from_disruption": "从中断恢复",
            "handle_section_block": "处理区间封锁"
        }
        return label_mapping.get(planning_intent, planning_intent)

    def _build_objective_weights(self, raw_weights: Dict, scene_category: str) -> Dict[str, float]:
        """构建目标权重"""
        # 如果LLM提供了有效权重，使用LLM的
        if raw_weights and isinstance(raw_weights, dict):
            return {
                "max_delay_weight": float(raw_weights.get("max_delay_weight", 0.4)),
                "avg_delay_weight": float(raw_weights.get("avg_delay_weight", 0.3)),
                "affected_trains_weight": float(raw_weights.get("affected_trains_weight", 0.2)),
                "runtime_weight": float(raw_weights.get("runtime_weight", 0.1))
            }

        # 默认权重（根据场景类型）
        if scene_category == "临时限速":
            return {
                "max_delay_weight": 0.5,
                "avg_delay_weight": 0.3,
                "affected_trains_weight": 0.1,
                "runtime_weight": 0.1
            }
        elif scene_category == "突发故障":
            return {
                "max_delay_weight": 0.3,
                "avg_delay_weight": 0.4,
                "affected_trains_weight": 0.2,
                "runtime_weight": 0.1
            }
        else:
            return {
                "max_delay_weight": 0.4,
                "avg_delay_weight": 0.3,
                "affected_trains_weight": 0.2,
                "runtime_weight": 0.1
            }

    def _build_skill_dispatch(
        self,
        planning_intent: str,
        scene_category: str,
        llm_solver_suggestion: str = "",
        solver_candidates_raw: list = None
    ) -> tuple:
        """
        构建skill_dispatch（优先使用LLM建议，规则校验）

        Args:
            planning_intent: LLM决策的intent
            scene_category: 场景类型
            llm_solver_suggestion: LLM建议的solver
            solver_candidates_raw: LLM提供的候选solver列表

        Returns:
            tuple: (skill_dispatch, solver_candidates, preferred_solver)
        """
        # 优先使用LLM建议的solver（如果有效）
        valid_solvers = ["mip", "fcfs", "max_delay_first", "noop"]
        if llm_solver_suggestion and llm_solver_suggestion in valid_solvers:
            main_skill = llm_solver_suggestion
            logger.debug(f"[L2] 使用LLM建议的solver: {main_skill}")
            # 构建候选列表（LLM建议优先）
            solver_candidates = [main_skill] + [s for s in valid_solvers if s != main_skill]
            preferred_solver = main_skill
        else:
            # 强制使用LLM意图映射，不进行规则校验
            logger.debug("[L2] LLM未提供有效solver建议，基于LLM intent映射")
            main_skill = self._intent_to_solver(planning_intent)
            logger.debug(f"[L2] 基于LLM intent使用skill: {main_skill}")

            # valid_solvers只包含求解器（mip, fcfs, max_delay_first, noop）
            solver_candidates = ["mip", "fcfs", "max_delay_first", "noop"]
            preferred_solver = main_skill if main_skill in solver_candidates else "fcfs"

        # 如果有 LLM 提供的原始候选列表，使用它
        if solver_candidates_raw and isinstance(solver_candidates_raw, list):
            solver_candidates = [s for s in solver_candidates_raw if s in valid_solvers]
            if not solver_candidates:
                solver_candidates = [main_skill]
            if preferred_solver not in solver_candidates:
                solver_candidates.insert(0, preferred_solver)

        skill_dispatch = {
            "是否进入技能求解": True,
            "主技能": main_skill,
            "辅助技能": [],
            "调用顺序": [main_skill],
            "阻塞项": [],
            "需补充信息": []
        }

        return skill_dispatch, solver_candidates, preferred_solver

    def _intent_to_solver(self, planning_intent: str) -> str:
        """将intent映射到求解器"""
        intent_solver_map = {
            "recalculate_corridor_schedule": "mip",
            "recover_from_disruption": "fcfs",
            "handle_section_block": "section_interrupt"  # 区间封锁使用section_interrupt技能
        }
        return intent_solver_map.get(planning_intent, "mip")
