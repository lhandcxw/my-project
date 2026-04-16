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
from scheduler_comparison.metrics import MetricsDefinition

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
        logger.debug("[L4] 评估层")

        # 如果求解失败，生成默认评估报告
        if not skill_execution_result.get("success", False):
            logger.warning("求解执行失败，生成默认评估报告")

            # 生成默认评估报告（包含高铁指标）
            default_evaluation_report = self._generate_default_evaluation_report(skill_execution_result, solver_response)

            rollback_feedback = RollbackFeedback(
                needs_rerun=True,
                rollback_reason="求解执行失败",
                suggested_fixes=["检查求解器配置", "尝试其他求解器"]
            )

            logger.info("第四层完成: 需要回退（求解失败），使用默认评估报告")

            # 打印默认评估结果
            logger.info("=" * 50)
            logger.info("【L4评估结果】(默认)")
            logger.info(f"  LLM摘要: {default_evaluation_report.llm_summary}")
            logger.info(f"  可行性得分: {default_evaluation_report.feasibility_score:.2f}")
            logger.info(f"  风险警告: {len(default_evaluation_report.risk_warnings)}项")
            logger.info("  高铁专用指标:")
            
            # 安全获取高铁指标（避免属性错误）
            on_time_rate = getattr(default_evaluation_report, 'on_time_rate', 1.0)
            punctuality_strict = getattr(default_evaluation_report, 'punctuality_strict', 1.0)
            delay_std_dev = getattr(default_evaluation_report, 'delay_std_dev', 0.0)
            delay_propagation_depth = getattr(default_evaluation_report, 'delay_propagation_depth', 0)
            delay_propagation_breadth = getattr(default_evaluation_report, 'delay_propagation_breadth', 0)
            evaluation_grade = getattr(default_evaluation_report, 'evaluation_grade', 'A')
            
            logger.info(f"    准点率: {on_time_rate * 100:.1f}%")
            logger.info(f"    严格准点率: {punctuality_strict * 100:.1f}%")
            logger.info(f"    延误标准差: {delay_std_dev:.2f}秒")
            logger.info(f"    传播深度: {delay_propagation_depth}站")
            logger.info(f"    传播广度: {delay_propagation_breadth}列")
            logger.info(f"    综合评级: {evaluation_grade}")
            logger.info("=" * 50)

            # 生成默认自然语言方案
            default_plan = self._generate_fallback_plan(skill_execution_result, solver_response)

            return {
                "evaluation_report": default_evaluation_report,
                "ranking_result": None,
                "rollback_feedback": rollback_feedback,
                "llm_summary": "求解执行失败，使用默认评估",
                "natural_language_plan": default_plan
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

        logger.info(f"[L4] 评估完成: 决策={policy_decision.decision}")

        # 打印评估结果摘要（确保总是打印）
        if evaluation_report:
            logger.info("=" * 50)
            logger.info("【L4评估结果】")
            logger.info(f"  LLM摘要: {evaluation_report.llm_summary}")
            logger.info(f"  可行性得分: {evaluation_report.feasibility_score:.2f}")
            logger.info(f"  风险警告: {len(evaluation_report.risk_warnings)}项")
            logger.info(f"  决策: {policy_decision.decision}")
            
            # 打印高铁专用指标（使用字典访问避免属性错误）
            logger.info("  高铁专用指标:")
            
            # 安全获取高铁指标（兼容新旧模型定义）
            if hasattr(evaluation_report, 'on_time_rate'):
                # 新模型定义（直接字段）
                on_time_rate = getattr(evaluation_report, 'on_time_rate', 1.0)
                punctuality_strict = getattr(evaluation_report, 'punctuality_strict', 1.0)
                delay_std_dev = getattr(evaluation_report, 'delay_std_dev', 0.0)
                delay_propagation_depth = getattr(evaluation_report, 'delay_propagation_depth', 0)
                delay_propagation_breadth = getattr(evaluation_report, 'delay_propagation_breadth', 0)
                evaluation_grade = getattr(evaluation_report, 'evaluation_grade', 'A')
            elif hasattr(evaluation_report, 'high_speed_metrics') and evaluation_report.high_speed_metrics:
                # 旧模型定义（通过high_speed_metrics字段）
                high_speed = evaluation_report.high_speed_metrics
                on_time_rate = getattr(high_speed, 'on_time_rate', 1.0)
                punctuality_strict = getattr(high_speed, 'punctuality_strict', 1.0)
                delay_std_dev = getattr(high_speed, 'delay_std_dev', 0.0)
                delay_propagation_depth = getattr(high_speed, 'delay_propagation_depth', 0)
                delay_propagation_breadth = getattr(high_speed, 'delay_propagation_breadth', 0)
                evaluation_grade = getattr(high_speed, 'evaluation_grade', 'A')
            else:
                # 默认值
                on_time_rate = 1.0
                punctuality_strict = 1.0
                delay_std_dev = 0.0
                delay_propagation_depth = 0
                delay_propagation_breadth = 0
                evaluation_grade = 'A'
            
            logger.info(f"    准点率: {on_time_rate * 100:.1f}%")
            logger.info(f"    严格准点率: {punctuality_strict * 100:.1f}%")
            logger.info(f"    延误标准差: {delay_std_dev:.2f}秒")
            logger.info(f"    传播深度: {delay_propagation_depth}站")
            logger.info(f"    传播广度: {delay_propagation_breadth}列")
            logger.info(f"    综合评级: {evaluation_grade}")
            logger.info("=" * 50)

        # 标记响应来源
        llm_response_type = evaluation_report.metadata.get("llm_response_type", "unknown") if evaluation_report else "unknown"
        if evaluation_report and "[MOCK]" in str(llm_response_type):
            logger.debug("[L4] 使用的是模拟响应（非LLM生成）")

        # 生成自然语言调度方案（添加调试日志）
        logger.debug("[L4] 开始生成自然语言调度方案...")
        try:
            natural_language_plan = self._generate_natural_language_plan(
                skill_execution_result,
                solver_response,
                evaluation_report
            )
            logger.debug(f"[L4] 自然语言调度方案生成完成，长度: {len(natural_language_plan) if natural_language_plan else 0}")
        except Exception as e:
            logger.error(f"[L4] 自然语言调度方案生成异常: {e}")
            import traceback
            logger.debug(f"堆栈: {traceback.format_exc()}")
            natural_language_plan = self._generate_fallback_plan(skill_execution_result, solver_response)

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
        avg_delay = skill_execution_result.get("avg_delay_minutes", 0)
        affected_trains = skill_execution_result.get("affected_trains_count", 0)
        solving_time = skill_execution_result.get("solving_time", 0.0)

        # 计算高铁专用指标
        high_speed_metrics = self._calculate_high_speed_metrics(
            skill_execution_result, solver_response
        )

        # 提取 LLM 返回的增强字段
        feasibility_risks = response.parsed_output.get("feasibility_risks", []) if response.parsed_output else []
        operational_risks = response.parsed_output.get("operational_risks", []) if response.parsed_output else []
        human_review_points = response.parsed_output.get("human_review_points", []) if response.parsed_output else []
        counterfactual_summary = response.parsed_output.get("counterfactual_summary", "") if response.parsed_output else ""
        why_not_other_solver = response.parsed_output.get("why_not_other_solver", "") if response.parsed_output else ""
        confidence = response.parsed_output.get("confidence", 0.8) if response.parsed_output else 0.8

        # 禁用基线对比功能（存在类型错误，暂时关闭）
        baseline_comparison = None
        logger.debug("[L4] 基线对比功能已禁用")

        # 计算高铁专用评估指标
        high_speed_metrics = self._calculate_high_speed_metrics(
            skill_execution_result, solver_response
        )

        return EvaluationReport(
            solution_id="solution_001",
            is_feasible=feasibility_score >= 0.5,
            total_delay_minutes=float(total_delay),
            max_delay_minutes=float(max_delay),
            solving_time_seconds=float(solving_time),
            affected_trains_count=int(affected_trains),  # 添加受影响列车数量
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
            # 高铁专用指标
            on_time_rate=high_speed_metrics.get("on_time_rate", 0.0),
            punctuality_strict=high_speed_metrics.get("punctuality_strict", 0.0),
            delay_std_dev=high_speed_metrics.get("delay_std_dev", 0.0),
            delay_propagation_depth=high_speed_metrics.get("delay_propagation_depth", 0),
            delay_propagation_breadth=high_speed_metrics.get("delay_propagation_breadth", 0),
            propagation_coefficient=high_speed_metrics.get("propagation_coefficient", 0.0),
            micro_delay_count=high_speed_metrics.get("micro_delay_count", 0),
            small_delay_count=high_speed_metrics.get("small_delay_count", 0),
            medium_delay_count=high_speed_metrics.get("medium_delay_count", 0),
            large_delay_count=high_speed_metrics.get("large_delay_count", 0),
            evaluation_grade=high_speed_metrics.get("evaluation_grade", "unknown"),
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
            
            # 确保original_schedule是字典类型
            if isinstance(original_schedule, str):
                try:
                    import json
                    original_schedule = json.loads(original_schedule)
                except:
                    original_schedule = {}
            elif not isinstance(original_schedule, dict):
                original_schedule = {}
            
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

            # HighSpeedEvaluationResult 的正确字段：
            # - proposed_metrics: HighSpeedMetrics
            # - baseline_metrics: HighSpeedMetrics
            # - max_delay_improvement, avg_delay_improvement, propagation_improvement
            # - is_better_than_baseline, recommended_output
            # - recommendations (注意：不是recommendations）

            proposed_metrics = getattr(eval_result, 'proposed_metrics', None)
            baseline_metrics = getattr(eval_result, 'baseline_metrics', None)
            max_delay_improvement = getattr(eval_result, 'max_delay_improvement', 0.0)
            avg_delay_improvement = getattr(eval_result, 'avg_delay_improvement', 0.0)
            is_better_than_baseline = getattr(eval_result, 'is_better_than_baseline', False)
            recommended_output = getattr(eval_result, 'recommended_output', False)
            recommendations = getattr(eval_result, 'recommendations', [])  # 修正拼写

            # 验证必要字段
            if not all([proposed_metrics, baseline_metrics]):
                logger.warning("基线对比结果缺少proposed_metrics或baseline_metrics")
                return None

            # 安全获取指标值（处理可能的字典或对象类型）
            def get_metric(obj, field, default=0):
                """安全获取指标值"""
                if obj is None:
                    return default
                
                # 处理字符串类型（JSON字符串）
                if isinstance(obj, str):
                    try:
                        import json
                        obj = json.loads(obj)
                        logger.info(f"成功解析JSON字符串: {field}")
                    except Exception as e:
                        logger.warning(f"无法解析JSON字符串: {e}")
                        return default
                
                if isinstance(obj, dict):
                    return obj.get(field, default)
                elif hasattr(obj, field):
                    # 尝试获取属性
                    attr_value = getattr(obj, field, default)
                    # 如果属性值仍然是字典，递归获取
                    if isinstance(attr_value, dict):
                        return attr_value.get(field, default)
                    return attr_value
                else:
                    logger.warning(f"无法从对象获取字段 {field}，类型: {type(obj)}")
                    return default

            # 验证proposed_metrics和baseline_metrics类型，处理JSON字符串
            if proposed_metrics is not None and isinstance(proposed_metrics, str):
                try:
                    import json
                    proposed_metrics = json.loads(proposed_metrics)
                    logger.info(f"成功解析proposed_metrics JSON字符串")
                except Exception as e:
                    logger.warning(f"无法解析proposed_metrics JSON字符串: {e}，设置为None")
                    proposed_metrics = None
            
            if baseline_metrics is not None and isinstance(baseline_metrics, str):
                try:
                    import json
                    baseline_metrics = json.loads(baseline_metrics)
                    logger.info(f"成功解析baseline_metrics JSON字符串")
                except Exception as e:
                    logger.warning(f"无法解析baseline_metrics JSON字符串: {e}，设置为None")
                    baseline_metrics = None

            if proposed_metrics is not None and not isinstance(proposed_metrics, (dict, object)):
                logger.warning(f"proposed_metrics类型错误: {type(proposed_metrics)}，设置为None")
                proposed_metrics = None

            if baseline_metrics is not None and not isinstance(baseline_metrics, (dict, object)):
                logger.warning(f"baseline_metrics类型错误: {type(baseline_metrics)}，设置为None")
                baseline_metrics = None

            if not all([proposed_metrics, baseline_metrics]):
                logger.warning("基线对比结果缺少proposed_metrics或baseline_metrics")
                return None

            # 转换为BaselineMetrics
            try:
                return BaselineMetrics(
                    max_delay_improvement=float(max_delay_improvement),
                    avg_delay_improvement=float(avg_delay_improvement),
                    is_better_than_baseline=bool(is_better_than_baseline),
                    # BaselineMetrics中没有recommended_output字段，忽略
                    proposed_max_delay_minutes=float(get_metric(proposed_metrics, 'max_delay_seconds', 0)) / 60.0,
                    proposed_avg_delay_minutes=float(get_metric(proposed_metrics, 'avg_delay_seconds', 0)) / 60.0,
                    proposed_total_delay_minutes=float(get_metric(proposed_metrics, 'total_delay_seconds', 0)) / 60.0,
                    baseline_max_delay_minutes=float(get_metric(baseline_metrics, 'max_delay_seconds', 0)) / 60.0,
                    baseline_avg_delay_minutes=float(get_metric(baseline_metrics, 'avg_delay_seconds', 0)) / 60.0,
                    baseline_total_delay_minutes=float(get_metric(baseline_metrics, 'total_delay_seconds', 0)) / 60.0
                )
            except Exception as e:
                logger.error(f"构建BaselineMetrics时出错: {e}")
                import traceback
                logger.debug(f"堆栈: {traceback.format_exc()}")
                return None

        except Exception as e:
            logger.warning(f"计算基线对比时出错: {e}")
            import traceback
            logger.debug(f"基线对比错误堆栈: {traceback.format_exc()}")
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

        # 调用PolicyEngine，优先使用L1层识别的场景类型
        scene_type = self._infer_scene_type(evaluation_report, skill_execution_result)

        return self.policy_engine.make_decision(
            is_successful=skill_execution_result.get("success", True),
            validation_result=None,
            evaluation_result=evaluation_result,
            solver_metrics=solver_metrics,
            risk_warnings=evaluation_report.risk_warnings,
            llm_suggestion=evaluation_report.llm_summary,
            scene_type=scene_type
        )

    def _infer_scene_type(self, evaluation_report: EvaluationReport, skill_execution_result: Dict[str, Any] = None) -> str:
        """
        获取场景类型（优先使用L1层识别结果）

        场景类型获取优先级：
        1. 优先使用L1层识别并通过L3传递的场景类型（准确）
        2. 如果L3层未传递场景类型，则基于延误时间和风险特征推断（兜底）
        3. 支持三种场景类型：临时限速、突发故障、区间封锁

        Args:
            evaluation_report: L4评估报告
            skill_execution_result: L3层的执行结果，包含L1层识别的场景类型

        Returns:
            str: 场景类型代码 (TEMP_SPEED_LIMIT/SUDDEN_FAILURE/SECTION_INTERRUPT)
        """
        # 优先级1：使用L3层传递的场景类型（来自L1层的准确识别）
        if skill_execution_result:
            scenario_type = skill_execution_result.get("scenario_type", "").lower()
            if scenario_type:
                # 映射到标准场景类型代码
                scenario_mapping = {
                    "temporary_speed_limit": "TEMP_SPEED_LIMIT",
                    "sudden_failure": "SUDDEN_FAILURE",
                    "section_interrupt": "SECTION_INTERRUPT"
                }
                if scenario_type in scenario_mapping:
                    logger.debug(f"[L4] 使用L1层识别的场景类型: {scenario_type} -> {scenario_mapping[scenario_type]}")
                    return scenario_mapping[scenario_type]

        # 优先级2：基于延误情况和风险特征推断（兜底方案）
        logger.warning("[L4] L3层未传递场景类型，使用规则推断（准确性较低）")
        max_delay = evaluation_report.max_delay_minutes if evaluation_report else 0
        risk_warnings = evaluation_report.risk_warnings if evaluation_report else []

        # 区间封锁特征：极大延误（>60分钟）或严重风险警告
        if max_delay > 60 or any("封锁" in str(w) or "中断" in str(w) for w in risk_warnings):
            logger.debug(f"[L4] 推断场景类型: SECTION_INTERRUPT (最大延误: {max_delay}分钟)")
            return "SECTION_INTERRUPT"
        # 突发故障特征：中等延误（30-60分钟）或一般风险
        elif max_delay > 30 or risk_warnings:
            logger.debug(f"[L4] 推断场景类型: SUDDEN_FAILURE (最大延误: {max_delay}分钟)")
            return "SUDDEN_FAILURE"
        # 临时限速特征：较小延误（<30分钟）
        else:
            logger.debug(f"[L4] 推断场景类型: TEMP_SPEED_LIMIT (最大延误: {max_delay}分钟)")
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
            logger.debug("[L4] 调用LLM生成自然语言调度方案...")
            response = self.prompt_adapter.execute_prompt(
                template_id="l4_natural_language_plan",
                context=context,
                enable_rag=False
            )
            logger.debug(f"[L4] LLM响应完成，valid={response.is_valid}")

            if response.is_valid and response.parsed_output:
                # 获取自然语言方案，处理可能的重复问题
                plan = response.parsed_output.get("natural_language_plan", "")

                # 如果返回的是字符串，直接返回；如果是其他类型，尝试转换
                if isinstance(plan, str):
                    # 处理转义的换行符
                    if "\\n" in plan:  # 注意双反斜杠匹配字面的 \n
                        # JSON解析后可能还有转义的换行符，需要解码
                        import codecs
                        try:
                            plan = codecs.decode(plan, 'unicode_escape')
                            logger.debug(f"[L4] 成功解码自然语言方案，移除转义字符")
                        except Exception as e:
                            logger.debug(f"[L4] 解码自然语言方案失败: {e}")
                            # 如果解码失败，尝试简单的替换
                            plan = plan.replace("\\n", "\n").replace("\\t", "\t")
                    # 此时plan中的换行符应该是真正的换行符了
                    logger.debug(f"[L4] 自然语言方案生成成功，长度: {len(plan)}")
                    return self._deduplicate_plan_content(plan)
                elif isinstance(plan, dict):
                    # 如果返回的是字典，提取内容
                    plan_str = str(plan)
                    if "\\n" in plan_str:
                        import codecs
                        try:
                            plan_str = codecs.decode(plan_str, 'unicode_escape')
                            logger.debug(f"[L4] 成功解码字典内容")
                        except Exception as e:
                            logger.debug(f"[L4] 解码字典内容失败: {e}")
                            plan_str = plan_str.replace("\\n", "\n")
                    logger.debug(f"[L4] 自然语言方案生成成功（字典转换），长度: {len(plan_str)}")
                    return self._deduplicate_plan_content(plan_str)
                else:
                    logger.debug(f"[L4] natural_language_plan类型异常: {type(plan)}，使用规则方案")
                    return self._generate_fallback_plan(skill_execution_result, solver_response)
            else:
                # 如果LLM失败，返回基于规则的简单描述
                logger.warning("[L4] LLM响应无效，使用规则方案")
                return self._generate_fallback_plan(skill_execution_result, solver_response)

        except Exception as e:
            logger.warning(f"[L4] 生成自然语言方案失败: {e}")
            import traceback
            logger.debug(f"[L4] 堆栈: {traceback.format_exc()}")
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

    def _deduplicate_plan_content(self, plan: str) -> str:
        """
        去除自然语言方案中的重复内容
        """
        if not plan:
            return plan
        
        # 按行分割
        lines = plan.split('\n')
        seen = set()
        result = []
        
        for line in lines:
            # 去除行首行尾空白
            stripped = line.strip()
            if not stripped:
                result.append(line)
                continue
            
            # 如果该行（去除空白后）已存在，则跳过
            if stripped in seen:
                continue
            
            seen.add(stripped)
            result.append(line)
        
        return '\n'.join(result)

    def _calculate_high_speed_metrics(
        self,
        skill_execution_result: Dict[str, Any],
        solver_response: Any
    ) -> Dict[str, Any]:
        """
        计算高铁客运专线专用评估指标
        
        专家级指标说明：
        1. 准点率：延误延误<5分钟的列车比例（高铁服务标准）
        2. 严格准点率：延误延误<3分钟的列车比例（高铁高标准）
        3. 延误标准差：反映延误分布的均衡性
        4. 传播深度：延误影响的车站数量
        5. 传播广度：受影响的列车数量
        6. 传播系数：传播深度/平均延误，反映传播效率
        7. 延误分级统计：微延误(<2min)、小延误(2-5min)、中延误(5-15min)、大延误(>15min)
        8. 综合评级：基于多维度指标的综合评价
        """
        try:
            # 从solver_response获取调度结果
            schedule = None
            if solver_response:
                if hasattr(solver_response, 'schedule'):
                    schedule = solver_response.schedule
                elif isinstance(solver_response, dict):
                    schedule = solver_response.get('schedule')
            
            if not schedule:
                return self._get_default_high_speed_metrics()
            
            # 收集所有延误数据
            all_delays = []
            delay_by_train = {}
            
            for train_id, stops in schedule.items():
                train_delays = []
                for stop in stops:
                    delay = stop.get("delay_seconds", 0)
                    if delay > 0:
                        all_delays.append(delay)
                        train_delays.append(delay)
                
                if train_delays:
                    delay_by_train[train_id] = {
                        "max": max(train_delays),
                        "avg": sum(train_delays) / len(train_delays),
                        "count": len(train_delays)
                    }
            
            if not all_delays:
                return self._get_default_high_speed_metrics()
            
            # 计算基础统计
            total_trains = len(schedule)
            affected_trains = len(delay_by_train)
            
            # 准点率计算（（<5分钟）
            on_time_count = sum(1 for d in all_delays if d < 300)
            on_time_rate = on_time_count / len(all_delays) if all_delays else 1.0
            
            # 严格准点率（（<3分钟）
            punctuality_strict_count = sum(1 for d in all_delays if d < 180)
            punctuality_strict = punctuality_strict_count / len(all_delays) if all_delays else 1.0
            
            # 延误标准差
            avg_delay = sum(all_delays) / len(all_delays)
            variance = sum((d - avg_delay) ** 2 for d in all_delays) / len(all_delays)
            delay_std_dev = variance ** 0.5
            
            # 传播深度（最大影响车站数）
            max_propagation_depth = max(
                (info.get("count", 0) for info in delay_by_train.values()),
                default=0
            )
            
            # 传播广度（受影响列车数）
            propagation_breadth = affected_trains
            
            # 传播系数 = 传播深度 / 平均延误(分钟)
            propagation_coefficient = (
                max_propagation_depth / (avg_delay / 60) if avg_delay > 0 else 0
            )
            
            # 延误分级统计
            micro_delay_count = sum(1 for d in all_delays if d < 120)  # <2分钟
            small_delay_count = sum(1 for d in all_delays if 120 <= d < 300)  # 2-5分钟
            medium_delay_count = sum(1 for d in all_delays if 300 <= d < 900)  # 5-15分钟
            large_delay_count = sum(1 for d in all_delays if d >= 900)  # >15分钟
            
            # 综合评级
            evaluation_grade = self._calculate_evaluation_grade(
                on_time_rate=on_time_rate,
                punctuality_strict=punctuality_strict,
                max_delay=max(all_delays) if all_delays else 0,
                affected_ratio=affected_trains / total_trains if total_trains > 0 else 0,
                propagation_coefficient=propagation_coefficient
            )
            
            return {
                "on_time_rate": round(on_time_rate, 3),
                "punctuality_strict": round(punctuality_strict, 3),
                "delay_std_dev": round(delay_std_dev, 2),
                "delay_propagation_depth": max_propagation_depth,
                "delay_propagation_breadth": propagation_breadth,
                "propagation_coefficient": round(propagation_coefficient, 3),
                "micro_delay_count": micro_delay_count,
                "small_delay_count": small_delay_count,
                "medium_delay_count": medium_delay_count,
                "large_delay_count": large_delay_count,
                "evaluation_grade": evaluation_grade,
                "total_trains": total_trains,
                "affected_trains": affected_trains
            }
            
        except Exception as e:
            logger.warning(f"计算高铁专用指标失败: {e}")
            return self._get_default_high_speed_metrics()
    
    def _get_default_high_speed_metrics(self) -> Dict[str, Any]:
        """获取默认的高铁专用指标"""
        return {
            "on_time_rate": 1.0,
            "punctuality_strict": 1.0,
            "delay_std_dev": 0.0,
            "delay_propagation_depth": 0,
            "delay_propagation_breadth": 0,
            "propagation_coefficient": 0.0,
            "micro_delay_count": 0,
            "small_delay_count": 0,
            "medium_delay_count": 0,
            "large_delay_count": 0,
            "evaluation_grade": "A",
            "total_trains": 0,
            "affected_trains": 0
        }
    
    def _calculate_evaluation_grade(
        self,
        on_time_rate: float,
        punctuality_strict: float,
        max_delay: int,
        affected_ratio: float,
        propagation_coefficient: float
    ) -> str:
        """
        计算综合评级
        
        评级标准（专家经验）：
        A级：准点率>95%，严格准点率>90%，最大延误延误<10分钟，影响比例比例<20%
        B级：准点率>85%，严格准点率>75%，最大延误延误<20分钟，影响比例比例<40%
        C级：准点率>70%，严格准点率>60%，最大延误延误<30分钟，影响比例比例<60%
        D级：其他情况
        """
        # 计算得分（百分制）
        score = 0
        
        # 准点率得分（权重30%）
        score += on_time_rate * 30
        
        # 严格准点率得分（权重25%）
        score += punctuality_strict * 25
        
        # 最大延误得分（权重20%）
        max_delay_minutes = max_delay / 60
        if max_delay_minutes < 10:
            score += 20
        elif max_delay_minutes < 20:
            score += 15
        elif max_delay_minutes < 30:
            score += 10
        else:
            score += 5
        
        # 影响比例得分（权重15%）
        score += (1 - affected_ratio) * 15
        
        # 传播系数得分（权重10%）
        if propagation_coefficient < 0.5:
            score += 10
        elif propagation_coefficient < 1.0:
            score += 7
        elif propagation_coefficient < 2.0:
            score += 4
        else:
            score += 1
        
        # 根据总分评级
        if score >= 90:
            return "A"
        elif score >= 75:
            return "B"
        elif score >= 60:
            return "C"
        else:
            return "D"

    def _generate_default_evaluation_report(
        self,
        skill_execution_result: Dict[str, Any],
        solver_response: Any
    ) -> EvaluationReport:
        """
        生成默认评估报告（当求解失败时使用）
        包含高铁专用指标
        """
        logger.info("[L4] 生成默认评估报告")

        # 从skill_execution_result中提取基本信息
        total_delay = skill_execution_result.get("total_delay_minutes", 0)
        max_delay = skill_execution_result.get("max_delay_minutes", 0)
        avg_delay = skill_execution_result.get("avg_delay_minutes", 0)
        affected_trains = skill_execution_result.get("affected_trains_count", 0)
        solving_time = skill_execution_result.get("solving_time", 0.0)

        # 计算默认高铁指标
        high_speed_metrics = self._get_default_high_speed_metrics()

        # 如果有solver_response，尝试计算一些指标
        if solver_response and hasattr(solver_response, 'schedule'):
            schedule = solver_response.schedule
            if schedule:
                high_speed_metrics["total_trains"] = len(schedule)
                high_speed_metrics["affected_trains"] = affected_trains

        # 构建默认评估报告
        return EvaluationReport(
            solution_id="solution_001",
            is_feasible=False,
            total_delay_minutes=float(total_delay),
            max_delay_minutes=float(max_delay),
            solving_time_seconds=float(solving_time),
            affected_trains_count=int(affected_trains),
            risk_warnings=["求解执行失败，结果可能不准确"],
            constraint_satisfaction={},
            llm_summary="求解执行失败，使用默认评估。建议检查约束条件或使用其他调度器。",
            feasibility_score=0.5,
            # 基线对比指标
            baseline_metrics=None,
            # 高铁专用指标（使用默认值）
            on_time_rate=high_speed_metrics.get("on_time_rate", 0.0),
            punctuality_strict=high_speed_metrics.get("punctuality_strict", 0.0),
            delay_std_dev=high_speed_metrics.get("delay_std_dev", 0.0),
            delay_propagation_depth=high_speed_metrics.get("delay_propagation_depth", 0),
            delay_propagation_breadth=high_speed_metrics.get("delay_propagation_breadth", 0),
            propagation_coefficient=high_speed_metrics.get("propagation_coefficient", 0.0),
            micro_delay_count=high_speed_metrics.get("micro_delay_count", 0),
            small_delay_count=high_speed_metrics.get("small_delay_count", 0),
            medium_delay_count=high_speed_metrics.get("medium_delay_count", 0),
            large_delay_count=high_speed_metrics.get("large_delay_count", 0),
            evaluation_grade=high_speed_metrics.get("evaluation_grade", "C"),
            # L4 增强字段
            feasibility_risks=["求解器执行失败"],
            operational_risks=[],
            human_review_points=["建议人工审核"],
            counterfactual_summary="由于求解失败，无法进行反事实分析",
            why_not_other_solver="未尝试其他求解器",
            confidence=0.3,
            metadata={"llm_response_type": "default_evaluation"}
        )
