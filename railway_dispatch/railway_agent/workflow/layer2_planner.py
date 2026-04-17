# -*- coding: utf-8 -*-
"""
第二层：Planner层（智能决策层）
根据事故场景特征，LLM自主决策求解策略、参数配置
规则仅做安全校验和兜底
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from models.workflow_models import AccidentCard
from models.prompts import PromptContext
from railway_agent.adapters.llm_prompt_adapter import get_llm_prompt_adapter
from config import LLMConfig

logger = logging.getLogger(__name__)


class Layer2Planner:
    """
    第二层：Planner层（简化版）

    核心职责：
    1. 从AccidentCard中提取真实铁路场景特征
    2. 将场景特征注入Prompt，让LLM自主决策求解策略
    3. LLM输出：solver选择、参数配置、决策理由
    4. 规则仅做安全校验（solver有效性、参数范围）
    """

    def __init__(self):
        self.prompt_adapter = get_llm_prompt_adapter()

    def execute(
        self,
        accident_card: AccidentCard,
        enable_rag: bool = True
    ) -> Dict[str, Any]:
        """执行第二层规划"""
        logger.info("[L2] Planner层 - 智能决策模式")

        # 构建真实铁路场景特征
        scenario_features = self._build_scenario_features(accident_card)
        logger.info(f"[L2] 场景特征:\n{scenario_features}")

        # 构建Prompt上下文（注入场景特征）
        context = PromptContext(
            request_id=f"{accident_card.scene_category}_{accident_card.location_code}",
            scene_category=accident_card.scene_category,
            accident_card=accident_card.model_dump(),
            network_snapshot={},
            dispatch_context={},
            variables={"scenario_features": scenario_features}
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
            reasoning = response.parsed_output.get("reasoning", "")
            llm_solver_suggestion = response.parsed_output.get("solver_suggestion", "")
            solver_config_raw = response.parsed_output.get("solver_config", {})
        else:
            if LLMConfig.FORCE_LLM_MODE:
                raise RuntimeError("[L2] LLM决策失败，实验中止（FORCE_LLM_MODE=true）")
            logger.warning("[L2] LLM决策失败，使用规则回退")
            planning_intent = self._get_default_intent(accident_card.scene_category)
            problem_desc = f"LLM决策失败，使用默认intent: {planning_intent}"
            reasoning = "规则回退"
            llm_solver_suggestion = ""
            solver_config_raw = {}

        # 构建skill_dispatch（LLM建议 + 规则安全校验）
        skill_dispatch, preferred_solver = self._build_skill_dispatch(
            planning_intent,
            accident_card.scene_category,
            llm_solver_suggestion
        )

        # 构建并校验solver配置（参数范围安全校验）
        solver_config = self._build_solver_config(solver_config_raw, accident_card.scene_category)

        # 判断响应来源
        is_mock = "[MOCK]" in (response.model_used or "")
        response_source = "规则回退" if not response.is_valid else ("模拟响应" if is_mock else "LLM智能决策")
        logger.info(f"[L2] 完成: intent={planning_intent}, solver={preferred_solver}, 来源={response_source}")

        # 打印决策详情
        # 精简日志：只保留关键决策信息
        logger.debug(f"[L2] 规划意图={planning_intent}, solver={preferred_solver}, 来源={response_source}")
        logger.debug(f"[L2] 求解参数: {solver_config}")

        return {
            "planning_intent": planning_intent,
            "skill_dispatch": skill_dispatch,
            "planner_decision": {
                "planning_intent": planning_intent,
                "intent_label": self._get_intent_label(planning_intent),
                "preferred_solver": preferred_solver,
                "solver_config": solver_config,
                "suggested_window_minutes": None,
                "affected_corridor_hint": f"{accident_card.location_code}_corridor" if accident_card.location_code else None,
                "need_user_clarification": False,
                "confidence": 0.9 if response.is_valid else 0.5,
                "reasoning": reasoning
            },
            "问题描述": problem_desc,
            "reasoning": reasoning,
            "llm_response": response.raw_response if response.raw_response else "",
            "llm_response_type": response.model_used,
            "_response_source": response_source
        }

    def _build_scenario_features(self, accident_card: AccidentCard) -> str:
        """
        构建真实铁路场景特征描述
        基于中国高铁运营实际，不使用"早晚高峰"等城市轨道概念
        """
        features = []
        card = accident_card

        # 1. 场景类型
        features.append(f"- 场景类型: {card.scene_category}")

        # 2. 故障类型
        if card.fault_type and card.fault_type != "未知":
            features.append(f"- 故障类型: {card.fault_type}")

        # 3. 位置信息
        location_desc = card.location_name or card.location_code or "未知位置"
        loc_type = "区间" if card.location_type == "section" else "车站"
        features.append(f"- 事故位置: {location_desc}（{loc_type}）")

        # 4. 受影响列车
        affected_count = len(card.affected_train_ids) if card.affected_train_ids else 0
        features.append(f"- 受影响列车数: {affected_count}列")
        if card.affected_train_ids:
            features.append(f"- 受影响车次: {', '.join(card.affected_train_ids[:10])}")

        # 5. 预计延误时长
        duration = card.expected_duration
        if duration:
            features.append(f"- 预计延误: {duration}分钟")
            if duration <= 10:
                features.append("- 延误等级: 轻微延误（≤10分钟）")
            elif duration <= 30:
                features.append("- 延误等级: 一般延误（10-30分钟）")
            elif duration <= 60:
                features.append("- 延误等级: 较大延误（30-60分钟），需优先处理")
            else:
                features.append("- 延误等级: 严重延误（>60分钟），需立即响应")

        # 6. 信息完整性
        features.append(f"- 信息完整性: {'完整' if card.is_complete else '不完整'}")
        if card.missing_fields:
            features.append(f"- 缺失信息: {', '.join(card.missing_fields)}")

        # 7. 运营时段判断（基于高铁实际，24小时运营）
        now = datetime.now()
        current_hour = now.hour
        if 0 <= current_hour < 6:
            features.append("- 当前时段: 天窗期（0:00-6:00），线路检修时段，列车稀疏")
            features.append("- 线路能力: 低（天窗期几乎无列车运行）")
        elif 6 <= current_hour < 9:
            features.append("- 当前时段: 运营初期（6:00-9:00），列车逐步发出")
            features.append("- 线路能力: 中低（列车密度逐步增加）")
        elif 9 <= current_hour < 14:
            features.append("- 当前时段: 日间运营（9:00-14:00），列车密度较高")
            features.append("- 线路能力: 中高（多方向列车交叉运行）")
        elif 14 <= current_hour < 18:
            features.append("- 当前时段: 下午运营（14:00-18:00），全天列车密度最高")
            features.append("- 线路能力: 高（上下行列车密集交会）")
        elif 18 <= current_hour < 22:
            features.append("- 当前时段: 晚间运营（18:00-22:00），列车密度逐步下降")
            features.append("- 线路能力: 中（列车陆续终到）")
        else:
            features.append("- 当前时段: 深夜运营（22:00-24:00），准备进入天窗期")
            features.append("- 线路能力: 低（即将进入天窗期）")

        # 8. 场景特殊性提示
        if card.scene_category == "区间封锁":
            features.append("- 特殊约束: 区间封锁，该区段所有列车无法通行，需要绕行或等待")
        elif card.scene_category == "临时限速":
            features.append("- 特殊约束: 区间限速，列车运行时间增加，需重新计算到达时间")
        elif card.scene_category == "突发故障":
            if duration and duration > 30:
                features.append("- 特殊约束: 故障修复时间较长，可能引发大规模列车积压")

        return "\n".join(features)

    def _get_default_intent(self, scene_category: str) -> str:
        """根据场景类型获取默认intent（规则回退）"""
        intent_mapping = {
            "临时限速": "recalculate_corridor_schedule",
            "突发故障": "recover_from_disruption",
            "区间封锁": "handle_section_block"
        }
        return intent_mapping.get(scene_category, "recalculate_corridor_schedule")

    def _get_intent_label(self, planning_intent: str) -> str:
        """获取intent的中文标签"""
        label_mapping = {
            "recalculate_corridor_schedule": "重新计算走廊时刻表",
            "recover_from_disruption": "从中断恢复",
            "handle_section_block": "处理区间封锁"
        }
        return label_mapping.get(planning_intent, planning_intent)

    def _build_solver_config(self, raw_config: Dict, scene_category: str) -> Dict[str, Any]:
        """
        构建并校验solver配置
        LLM建议的参数经过安全范围校验，超出范围的截断到边界值
        """
        config = {}

        if raw_config and isinstance(raw_config, dict):
            # time_limit校验：范围30-600秒
            time_limit = raw_config.get("time_limit")
            if time_limit is not None:
                try:
                    time_limit = int(time_limit)
                    config["time_limit"] = max(30, min(600, time_limit))
                except (ValueError, TypeError):
                    config["time_limit"] = 120
                logger.debug(f"[L2] LLM建议time_limit={time_limit}, 校验后={config['time_limit']}")

            # optimality_gap校验：范围0.01-0.1
            gap = raw_config.get("optimality_gap")
            if gap is not None:
                try:
                    gap = float(gap)
                    config["optimality_gap"] = max(0.01, min(0.1, round(gap, 2)))
                except (ValueError, TypeError):
                    config["optimality_gap"] = 0.05
                logger.debug(f"[L2] LLM建议optimality_gap={gap}, 校验后={config['optimality_gap']}")

            # optimization_objective
            obj = raw_config.get("optimization_objective")
            if obj and obj in ["min_max_delay", "min_total_delay", "min_avg_delay"]:
                config["optimization_objective"] = obj
        else:
            # 使用场景默认配置
            if scene_category == "临时限速":
                config = {"time_limit": 120, "optimality_gap": 0.05, "optimization_objective": "min_max_delay"}
            elif scene_category == "突发故障":
                config = {"time_limit": 60, "optimality_gap": 0.1, "optimization_objective": "min_total_delay"}
            else:
                config = {"time_limit": 30, "optimality_gap": 0.1, "optimization_objective": "min_max_delay"}

        return config

    def _build_skill_dispatch(
        self,
        planning_intent: str,
        scene_category: str,
        llm_solver_suggestion: str = ""
    ) -> tuple:
        """
        构建skill_dispatch
        优先使用LLM建议的solver，规则仅做安全校验
        """
        valid_solvers = ["mip", "fcfs", "max_delay_first", "noop"]

        # 区间封锁特殊处理
        if scene_category == "区间封锁":
            main_skill = "fcfs"  # 区间封锁使用FCFS
            preferred_solver = "fcfs"
        elif llm_solver_suggestion and llm_solver_suggestion in valid_solvers:
            # LLM有效建议
            main_skill = llm_solver_suggestion
            logger.debug(f"[L2] 使用LLM建议的solver: {main_skill}")
            preferred_solver = main_skill
        else:
            # 回退到intent映射
            main_skill = self._intent_to_solver(planning_intent)
            preferred_solver = main_skill if main_skill in valid_solvers else "fcfs"

        skill_dispatch = {
            "是否进入技能求解": True,
            "主技能": main_skill,
            "辅助技能": [],
            "调用顺序": [main_skill],
            "阻塞项": [],
            "需补充信息": []
        }

        return skill_dispatch, preferred_solver

    def _intent_to_solver(self, planning_intent: str) -> str:
        """将intent映射到求解器"""
        intent_solver_map = {
            "recalculate_corridor_schedule": "mip",
            "recover_from_disruption": "fcfs",
            "handle_section_block": "fcfs"
        }
        return intent_solver_map.get(planning_intent, "mip")
