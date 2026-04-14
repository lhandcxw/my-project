# -*- coding: utf-8 -*-
"""
第四层：结果输出与评估层
评估调度方案，生成解释和风险提示
"""

import logging
from typing import Dict, Any

from models.workflow_models import EvaluationReport, RollbackFeedback, RankingResult, BaselineMetrics
from models.prompts import PromptContext
from railway_agent.adapters.llm_prompt_adapter import get_llm_prompt_adapter
from railway_agent.policy_engine import PolicyEngine
from evaluation.evaluator import BaselineComparator

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

        # 生成自然语言调度方案
        natural_language_plan = self._generate_natural_language_plan(
            skill_execution_result,
            solver_response,
            evaluation_report
        )

        return {
            "evaluation_report": evaluation_report,
            "ranking_result": None,  # 暂不实现排序
            "rollback_feedback": rollback_feedback,
            "policy_decision": policy_decision.model_dump(),
            "llm_summary": evaluation_report.llm_summary if evaluation_report else "评估失败",
            "llm_response_type": llm_response_type,
            "natural_language_plan": natural_language_plan  # 新增：自然语言调度方案
        }

    def _generate_llm_evaluation(
        self,
        skill_execution_result: Dict[str, Any],
        solver_response: Any,
        enable_rag: bool
    ) -> EvaluationReport:
        """生成LLM评估（集成BaselineComparator的数值对比）"""
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

        # 提取 LLM 返回的增强字段
        feasibility_risks = response.parsed_output.get("feasibility_risks", []) if response.parsed_output else []
        operational_risks = response.parsed_output.get("operational_risks", []) if response.parsed_output else []
        human_review_points = response.parsed_output.get("human_review_points", []) if response.parsed_output else []
        counterfactual_summary = response.parsed_output.get("counterfactual_summary", "") if response.parsed_output else ""
        why_not_other_solver = response.parsed_output.get("why_not_other_solver", "") if response.parsed_output else ""
        confidence = response.parsed_output.get("confidence", 0.8) if response.parsed_output else 0.8

        # 集成BaselineComparator的数值对比（如果有原始时刻表和延误注入信息）
        baseline_comparison = None
        try:
            baseline_comparison = self._calculate_baseline_comparison(
                skill_execution_result, solver_response
            )
        except Exception as e:
            logger.warning(f"基线对比计算失败: {e}")

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
            # L4 增强字段
            feasibility_risks=feasibility_risks,
            operational_risks=operational_risks,
            human_review_points=human_review_points,
            counterfactual_summary=counterfactual_summary,
            why_not_other_solver=why_not_other_solver,
            confidence=float(confidence),
            # 基线对比指标（集成evaluator.py）
            baseline_metrics=baseline_comparison,
            metadata={"llm_response_type": response.model_used}
        )

    def _calculate_baseline_comparison(
        self,
        skill_execution_result: Dict[str, Any],
        solver_response: Any = None
    ) -> BaselineMetrics:
        """
        计算与基线方案的对比指标
        集成BaselineComparator的数值计算能力
        """
        try:
            # 获取优化后的时刻表（从solver_response中获取schedule字段）
            optimized_schedule = None
            if solver_response:
                if hasattr(solver_response, 'schedule'):
                    optimized_schedule = solver_response.schedule
                elif isinstance(solver_response, dict):
                    optimized_schedule = solver_response.get('schedule')

            if not optimized_schedule:
                logger.warning("无法获取优化后的时刻表，跳过基线对比")
                return None

            # 从execution_result中提取原始时刻表和延误注入
            original_schedule = skill_execution_result.get('original_schedule', {})
            delay_injection = skill_execution_result.get('delay_injection', {})
            
            # 确保delay_injection是字典类型
            if isinstance(delay_injection, str):
                try:
                    import json
                    delay_injection = json.loads(delay_injection)
                except:
                    delay_injection = {"injected_delays": []}
            elif not isinstance(delay_injection, dict):
                delay_injection = {"injected_delays": []}

            if not original_schedule:
                logger.warning("无法获取原始时刻表，跳过基线对比")
                return None

            # 使用BaselineComparator计算基线对比
            comparator = BaselineComparator(baseline_strategy="no_adjustment")

            # 构建delay_injection字典格式
            delay_injection_dict = {
                "injected_delays": delay_injection.get('injected_delays', [])
            }

            # 计算评估结果
            eval_result = comparator.compare(
                proposed_schedule=optimized_schedule,
                original_schedule=original_schedule,
                delay_injection=delay_injection_dict
            )

            # 转换为BaselineMetrics
            return BaselineMetrics(
                max_delay_improvement=eval_result.comparison.max_delay_improvement,
                avg_delay_improvement=eval_result.comparison.avg_delay_improvement,
                is_better_than_baseline=eval_result.comparison.is_better_than_baseline,
                recommended_output=eval_result.comparison.recommended_output,
                proposed_max_delay=eval_result.proposed_metrics.max_delay_seconds / 60,  # 转换为分钟
                proposed_avg_delay=eval_result.proposed_metrics.avg_delay_seconds / 60,
                proposed_total_delay=eval_result.proposed_metrics.total_delay_seconds / 60,
                baseline_max_delay=eval_result.baseline_metrics.max_delay_seconds / 60,
                baseline_avg_delay=eval_result.baseline_metrics.avg_delay_seconds / 60,
                baseline_total_delay=eval_result.baseline_metrics.total_delay_seconds / 60,
                recommendations=eval_result.recommendations
            )

        except Exception as e:
            logger.warning(f"计算基线对比时出错: {e}")
            return None

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

    def _generate_natural_language_plan(
        self,
        skill_execution_result: Dict[str, Any],
        solver_response: Any,
        evaluation_report: EvaluationReport
    ) -> str:
        """
        生成自然语言调度方案
        使用LLM将数值化的调度结果转换为人类可读的调度指令
        """
        try:
            # 提取调度场景信息
            affected_trains = skill_execution_result.get("affected_trains", [])
            scene_type = skill_execution_result.get("scenario_type", "临时限速")
            delay_location = skill_execution_result.get("location", "未知位置")
            
            # 构建Prompt上下文
            context = PromptContext(
                request_id="nlp_plan_001",
                execution_result=skill_execution_result,
                solver_result=solver_response.model_dump() if hasattr(solver_response, 'model_dump') else {},
                variables={
                    "evaluation_summary": evaluation_report.llm_summary if evaluation_report else "",
                    "feasibility_score": str(evaluation_report.feasibility_score if evaluation_report else 0.8),
                    "affected_trains": ", ".join(affected_trains) if affected_trains else "G1563",
                    "scene_type": scene_type,
                    "delay_location": delay_location
                }
            )

            # 调用LLM生成自然语言方案
            response = self.prompt_adapter.execute_prompt(
                template_id="l4_natural_language_plan",
                context=context,
                enable_rag=False
            )

            if response.is_valid and response.parsed_output:
                return response.parsed_output.get("natural_language_plan", "")
            else:
                # 如果LLM失败，返回基于规则的简单描述
                return self._generate_fallback_plan(skill_execution_result, solver_response)

        except Exception as e:
            logger.warning(f"生成自然语言方案失败: {e}")
            return self._generate_fallback_plan(skill_execution_result, solver_response)

    def _generate_fallback_plan(
        self,
        skill_execution_result: Dict[str, Any],
        solver_response: Any
    ) -> str:
        """
        生成简单的回退方案（当LLM失败时使用）
        """
        try:
            total_delay = skill_execution_result.get("total_delay_minutes", 0)
            max_delay = skill_execution_result.get("max_delay_minutes", 0)
            affected_trains = skill_execution_result.get("affected_trains", [])

            plan_parts = []
            plan_parts.append(f"调度方案概要：")
            plan_parts.append(f"- 受影响列车：{', '.join(affected_trains) if affected_trains else '无'}")
            plan_parts.append(f"- 总延误：{total_delay}分钟")
            plan_parts.append(f"- 最大延误：{max_delay}分钟")
            plan_parts.append(f"- 建议：根据优化结果调整列车发车顺序，确保追踪间隔满足安全约束")

            return "\n".join(plan_parts)
        except Exception as e:
            logger.error(f"生成回退方案失败: {e}")
            return "调度方案生成失败，请查看详细调度数据"
