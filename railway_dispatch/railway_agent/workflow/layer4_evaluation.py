# -*- coding: utf-8 -*-
"""
第四层：结果输出与评估层
评估调度方案，生成解释和风险提示
"""

import logging
from typing import Dict, Any

from models.workflow_models import EvaluationReport, RollbackFeedback, RankingResult
from models.prompts import PromptContext
from railway_agent.adapters.llm_prompt_adapter import get_llm_prompt_adapter
from railway_agent.policy_engine import PolicyEngine

logger = logging.getLogger(__name__)


class Layer4Evaluation:
    """
    第四层：评估层
    使用LLM生成解释和风险提示，PolicyEngine做最终决策
    """

    def __init__(self):
        """初始化第四层"""
        self.prompt_adapter = get_llm_prompt_adapter()
        self.policy_engine = PolicyEngine()

    def execute(
        self,
        skill_execution_result: Dict[str, Any],
        solver_response: Any,
        enable_rag: bool = False
    ) -> Dict[str, Any]:
        """
        执行第四层评估

        Args:
            skill_execution_result: 第三层执行结果
            solver_response: 求解器响应
            enable_rag: 是否启用RAG

        Returns:
            Dict: 包含评估报告和决策的字典
        """
        logger.info("[L4] 评估层")

        # 如果求解失败，直接返回回退反馈
        if not skill_execution_result.get("success", False):
            rollback_feedback = RollbackFeedback(
                needs_rerun=True,
                rollback_reason="求解执行失败",
                suggested_fixes=["检查求解器配置", "尝试其他求解器"]
            )

            logger.info("第四层完成: 需要回退（求解失败）")

            return {
                "evaluation_report": None,
                "ranking_result": None,
                "rollback_feedback": rollback_feedback,
                "llm_summary": "求解执行失败，无法生成摘要"
            }

        # 调用LLM生成评估
        evaluation_report = self._generate_llm_evaluation(
            skill_execution_result,
            solver_response,
            enable_rag
        )

        # PolicyEngine做最终决策
        policy_decision = self._make_policy_decision(
            evaluation_report,
            skill_execution_result
        )

        # 构建回退反馈
        rollback_feedback = self._build_rollback_feedback(policy_decision)

        logger.info(f"第四层完成: 决策={policy_decision.decision}")

        # 标记响应来源
        llm_response_type = evaluation_report.metadata.get("llm_response_type", "unknown") if evaluation_report else "unknown"
        if evaluation_report and "[MOCK]" in str(llm_response_type):
            logger.info("[L4] 使用的是模拟响应（非LLM生成）")

        return {
            "evaluation_report": evaluation_report,
            "ranking_result": None,  # 暂不实现排序
            "rollback_feedback": rollback_feedback,
            "policy_decision": policy_decision.model_dump(),
            "llm_summary": evaluation_report.llm_summary if evaluation_report else "评估失败",
            "llm_response_type": llm_response_type
        }

    def _generate_llm_evaluation(
        self,
        skill_execution_result: Dict[str, Any],
        solver_response: Any,
        enable_rag: bool
    ) -> EvaluationReport:
        """生成LLM评估"""
        # 构建Prompt上下文
        context = PromptContext(
            request_id="eval_001",
            execution_result=skill_execution_result,
            solver_result=solver_response.model_dump() if hasattr(solver_response, 'model_dump') else {}
        )

        # 调用LLM
        response = self.prompt_adapter.execute_prompt(
            template_id="l4_evaluation",
            context=context,
            enable_rag=enable_rag
        )

        # 构建评估报告
        if response.is_valid and response.parsed_output:
            llm_summary = response.parsed_output.get("llm_summary", "方案评估完成")
            risk_warnings = response.parsed_output.get("risk_warnings", [])
            feasibility_score = response.parsed_output.get("feasibility_score", 0.8)
            constraint_check = response.parsed_output.get("constraint_check", {})
        else:
            # LLM失败，使用默认值
            logger.warning("LLM评估失败，使用默认值")
            llm_summary = "LLM评估失败，使用默认评估"
            risk_warnings = []
            feasibility_score = 0.8
            constraint_check = {}

        # 从执行结果中提取指标
        total_delay = skill_execution_result.get("total_delay_minutes", 0)
        max_delay = skill_execution_result.get("max_delay_minutes", 0)
        solving_time = skill_execution_result.get("solving_time", 0.0)

        return EvaluationReport(
            solution_id="solution_001",
            is_feasible=feasibility_score >= 0.5,
            total_delay_minutes=float(total_delay),
            max_delay_minutes=float(max_delay),
            solving_time_seconds=float(solving_time),
            risk_warnings=risk_warnings,
            constraint_satisfaction=constraint_check,
            llm_summary=llm_summary,
            feasibility_score=float(feasibility_score),
            metadata={"llm_response_type": response.model_used}
        )

    def _make_policy_decision(
        self,
        evaluation_report: EvaluationReport,
        skill_execution_result: Dict[str, Any]
    ):
        """使用PolicyEngine做决策"""
        # 构建评估结果字典
        evaluation_result = {
            "is_feasible": evaluation_report.is_feasible,
            "total_delay_minutes": evaluation_report.total_delay_minutes,
            "max_delay_minutes": evaluation_report.max_delay_minutes,
            "feasibility_score": getattr(evaluation_report, 'feasibility_score', 0.8)
        }

        # 构建求解器指标
        solver_metrics = {
            "solving_time": skill_execution_result.get("solving_time", 0.0)
        }

        # 调用PolicyEngine
        return self.policy_engine.make_decision(
            is_successful=skill_execution_result.get("success", True),
            validation_result=None,
            evaluation_result=evaluation_result,
            solver_metrics=solver_metrics,
            risk_warnings=evaluation_report.risk_warnings,
            llm_suggestion=evaluation_report.llm_summary,
            scene_type=self._infer_scene_type(evaluation_report)
        )

    def _infer_scene_type(self, evaluation_report: EvaluationReport) -> str:
        """推断场景类型"""
        # 简单推断：基于延误情况
        if evaluation_report.max_delay_minutes > 30:
            return "SUDDEN_FAILURE"
        elif evaluation_report.risk_warnings:
            return "SUDDEN_FAILURE"
        else:
            return "TEMP_SPEED_LIMIT"

    def _build_rollback_feedback(self, policy_decision) -> RollbackFeedback:
        """构建回退反馈"""
        from models.common_enums import PolicyDecisionType

        return RollbackFeedback(
            needs_rerun=(policy_decision.decision == PolicyDecisionType.RERUN),
            rollback_reason=policy_decision.reason,
            suggested_fixes=policy_decision.suggested_fixes
        )
