# -*- coding: utf-8 -*-
"""
第二层：Planner层（技能路由层）
根据事故卡片判断问题类型和处理意图
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
            Dict: 包含planning_intent的字典
        """
        logger.info("[L2] Planner层")

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
        else:
            # LLM失败
            if LLMConfig.FORCE_LLM_MODE:
                raise RuntimeError("[L2] LLM决策失败，实验中止（FORCE_LLM_MODE=true）")
            logger.warning("[L2] LLM决策失败，使用默认intent")
            planning_intent = self._get_default_intent(accident_card.scene_category)
            problem_desc = f"LLM决策失败，使用默认intent: {planning_intent}"
            suggested_window = ""
            llm_solver_suggestion = ""

        # 构建skill_dispatch（使用LLM建议，规则校验）
        skill_dispatch = self._build_skill_dispatch(
            planning_intent,
            accident_card.scene_category,
            llm_solver_suggestion
        )

        # 判断响应来源
        is_mock = "[MOCK]" in response.model_used if response.model_used else False
        response_source = "模拟响应" if is_mock else "LLM输出"
        logger.info(f"[L2] 完成: planning_intent={planning_intent}, solver={skill_dispatch['主技能']}, 来源={response_source}")

        return {
            "planning_intent": planning_intent,
            "skill_dispatch": skill_dispatch,
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

    def _build_skill_dispatch(
        self,
        planning_intent: str,
        scene_category: str,
        llm_solver_suggestion: str = ""
    ) -> Dict[str, Any]:
        """
        构建skill_dispatch（优先使用LLM建议，规则校验）

        Args:
            planning_intent: LLM决策的intent
            scene_category: 场景类型
            llm_solver_suggestion: LLM建议的solver

        Returns:
            Dict: skill_dispatch字典
        """
        # 优先使用LLM建议的solver（如果有效）
        valid_solvers = ["mip", "fcfs", "max_delay_first", "noop"]
        if llm_solver_suggestion and llm_solver_suggestion in valid_solvers:
            main_skill = llm_solver_suggestion
            logger.info(f"[L2] 使用LLM建议的solver: {main_skill}")
        else:
            # 规则校验：根据场景类型确定主技能
            if scene_category == "临时限速":
                main_skill = "mip"
            elif scene_category == "突发故障":
                main_skill = "fcfs"
            elif scene_category == "区间封锁":
                main_skill = "noop"
            else:
                main_skill = self._intent_to_solver(planning_intent)
            logger.info(f"[L2] 使用规则确定的solver: {main_skill}")

        return {
            "是否进入技能求解": True,
            "主技能": main_skill,
            "辅助技能": [],
            "调用顺序": [main_skill],
            "阻塞项": [],
            "需补充信息": []
        }

    def _intent_to_solver(self, planning_intent: str) -> str:
        """将intent映射到求解器"""
        intent_solver_map = {
            "recalculate_corridor_schedule": "mip",
            "recover_from_disruption": "fcfs",
            "handle_section_block": "noop"
        }
        return intent_solver_map.get(planning_intent, "mip")
