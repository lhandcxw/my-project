# -*- coding: utf-8 -*-
"""
第四层：评估与方案生成层（重构版）

核心改造：
  1. 评估 prompt 专业化 — 提供京广高铁评估框架和标准
  2. 合并两次 LLM 调用为一次 — 评估+方案生成+调整指令一次完成
  3. schedule 转调整指令 — LLM 读取优化后时刻表，生成每列车的具体调整说明
  4. 方案对比与推荐 — 当有多方案结果时，LLM 自动生成对比分析

LLM 在本层的定位：
  - 不是"打分器"（PolicyEngine 已做阈值判断）
  - 而是"调度方案解释专家"——将数值结果转化为调度员可理解和汇报的材料
"""

import logging
import json
from typing import Dict, Any, Optional

from models.workflow_models import EvaluationReport, RollbackFeedback, RankingResult, BaselineMetrics
from models.prompts import PromptContext
from railway_agent.adapters.llm_prompt_adapter import get_llm_prompt_adapter
from railway_agent.adapters.llm_adapter import get_llm_caller
from railway_agent.policy_engine import PolicyEngine
from config import DispatchEnvConfig

logger = logging.getLogger(__name__)


class Layer4Evaluation:
    """
    第四层：评估与方案生成层（重构版）

    与旧版的核心区别：
    - 旧版：两次独立 LLM 调用（评估 + NL Plan），prompt 极其简陋
    - 新版：一次 LLM 调用完成评估 + 方案生成 + 调整指令 + 方案对比
    - prompt 包含京广高铁专业评估标准
    - 支持多方案对比（接收 L2 Agent 的 compare_strategies 结果）
    """

    def __init__(self):
        self.prompt_adapter = get_llm_prompt_adapter()
        self.policy_engine = PolicyEngine()

    def execute(
        self,
        skill_execution_result: Dict[str, Any],
        solver_response: Any,
        enable_rag: bool = False,
        comparison_results: Optional[list] = None
    ) -> Dict[str, Any]:
        """
        执行第四层评估

        Args:
            skill_execution_result: 求解执行结果
            solver_response: 求解器响应
            enable_rag: 是否启用RAG（保留接口）
            comparison_results: L2 Agent 的多方案对比结果（可选）
        """
        logger.debug("[L4] 评估与方案生成层")

        # 求解失败：生成默认报告
        if not skill_execution_result.get("success", False):
            logger.warning("[L4] 求解执行失败，生成默认评估报告")
            default_report = self._generate_default_evaluation_report(skill_execution_result, solver_response)
            return {
                "evaluation_report": default_report,
                "ranking_result": None,
                "rollback_feedback": RollbackFeedback(
                    needs_rerun=True,
                    rollback_reason="求解执行失败",
                    suggested_fixes=["检查求解器配置", "尝试其他求解器"]
                ),
                "llm_summary": "求解执行失败，使用默认评估",
                "natural_language_plan": self._generate_fallback_plan(skill_execution_result, solver_response)
            }

        # 构建评估上下文
        evaluation_context = self._build_evaluation_context(
            skill_execution_result, solver_response, comparison_results
        )

        # 计算高铁专用指标（规则计算，不走 LLM）
        high_speed_metrics = self._calculate_high_speed_metrics(
            skill_execution_result, solver_response
        )

        # 一次 LLM 调用：评估 + 方案生成 + 调整指令
        llm_result = self._generate_comprehensive_evaluation(evaluation_context)

        # 构建 EvaluationReport
        evaluation_report = self._build_evaluation_report(
            skill_execution_result, solver_response, high_speed_metrics, llm_result
        )

        # PolicyEngine 规则决策
        policy_decision = self._make_policy_decision(evaluation_report, skill_execution_result)
        rollback_feedback = self._build_rollback_feedback(policy_decision)

        # 日志输出
        self._log_evaluation_result(evaluation_report, policy_decision, high_speed_metrics)

        return {
            "evaluation_report": evaluation_report,
            "ranking_result": None,
            "rollback_feedback": rollback_feedback,
            "policy_decision": policy_decision.model_dump(),
            "llm_summary": evaluation_report.llm_summary,
            "llm_response_type": llm_result.get("response_type", "unknown"),
            "natural_language_plan": llm_result.get("natural_language_plan", ""),
            "comparison_analysis": llm_result.get("comparison_analysis", "")
        }

    # ================================================================
    # 核心评估（一次 LLM 调用）
    # ================================================================

    def _build_evaluation_context(
        self,
        skill_execution_result: Dict[str, Any],
        solver_response: Any,
        comparison_results: Optional[list] = None
    ) -> Dict[str, Any]:
        """构建完整的评估上下文（供 LLM 使用）"""
        ctx = {}

        # 基础指标
        ctx["total_delay_minutes"] = skill_execution_result.get("total_delay_minutes", 0)
        ctx["max_delay_minutes"] = skill_execution_result.get("max_delay_minutes", 0)
        ctx["avg_delay_minutes"] = skill_execution_result.get("avg_delay_minutes", 0)
        ctx["affected_trains_count"] = skill_execution_result.get("affected_trains_count", 0)
        ctx["affected_trains"] = skill_execution_result.get("affected_trains", [])
        ctx["solving_time"] = skill_execution_result.get("solving_time", 0)
        ctx["solver_name"] = skill_execution_result.get("skill_name", "未知")
        ctx["scenario_type"] = skill_execution_result.get("scenario_type", "未知")
        ctx["location"] = skill_execution_result.get("location", "未知位置")

        # 从 solver_response 提取优化后时刻表
        schedule = {}
        if solver_response:
            if hasattr(solver_response, 'schedule'):
                schedule = solver_response.schedule
            elif isinstance(solver_response, dict):
                # 【修复】同时支持 schedule 和 optimized_schedule
                schedule = solver_response.get('schedule') or solver_response.get('optimized_schedule', {})

        # 如果 schedule 为空，尝试从 skill_execution_result 提取
        if not schedule and skill_execution_result:
            schedule = skill_execution_result.get('optimized_schedule', {})

        # 提取受影响列车的具体调整（最多展示 5 列避免 context 过长）
        train_adjustments = {}
        affected_ids = set(ctx["affected_trains"]) if ctx["affected_trains"] else set()
        count = 0
        for train_id, stops in schedule.items():
            if count >= 5:
                break
            train_stops = []
            for stop in stops:
                delay_s = stop.get("delay_seconds", 0)
                if delay_s > 0:
                    train_stops.append({
                        "station_code": stop.get("station_code", ""),
                        "station_name": stop.get("station_name", ""),
                        "original_arrival": stop.get("original_arrival", ""),
                        "adjusted_arrival": stop.get("arrival_time", ""),
                        "delay_minutes": round(delay_s / 60, 1)
                    })
            if train_stops:
                train_adjustments[train_id] = train_stops
                count += 1
        ctx["train_adjustments"] = train_adjustments

        # 多方案对比数据
        if comparison_results:
            ctx["comparison_results"] = comparison_results
            ctx["has_comparison"] = True
        else:
            ctx["has_comparison"] = False

        return ctx

    def _generate_comprehensive_evaluation(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        一次 LLM 调用完成：
          1. 专业评估（基于京广高铁标准）
          2. 自然语言调度方案
          3. 具体调整指令（每列车+站点+时间）
          4. 多方案对比分析（如果有）
        """
        # 构建 user prompt
        user_content = self._build_evaluation_prompt(ctx)

        # 【修复】使用正确的LLM调用方式
        try:
            llm = get_llm_caller()

            # 方式1：使用 call_with_tools（支持messages参数）
            messages = [
                {"role": "system", "content": _EVALUATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ]
            response = llm.call_with_tools(
                messages=messages,
                max_tokens=1024,
                temperature=0.3
            )
            raw = response.get("content", "")

        except Exception as e:
            logger.warning(f"[L4] LLM 调用失败（使用call_with_tools）: {e}")
            try:
                # 方式2：回退到传统call方法（使用prompt字符串）
                full_prompt = f"{_EVALUATION_SYSTEM_PROMPT}\n\n{user_content}"
                response_text, _ = llm.call(
                    prompt=full_prompt,
                    max_tokens=1024,
                    temperature=0.3
                )
                raw = response_text
            except Exception as e2:
                logger.warning(f"[L4] LLM 调用失败（使用call）: {e2}")
                return {"natural_language_plan": "", "comparison_analysis": "", "response_type": "fallback"}

        if not raw:
            return {"natural_language_plan": "", "comparison_analysis": "", "response_type": "empty"}

        # 解析 LLM 输出（可能是 JSON 也可能是纯文本）
        return self._parse_llm_output(raw, ctx)

    def _build_evaluation_prompt(self, ctx: Dict[str, Any]) -> str:
        """构建评估 user prompt"""
        parts = []
        parts.append("【调度场景信息】")
        parts.append(f"- 场景类型: {ctx['scenario_type']}")
        parts.append(f"- 事故位置: {ctx['location']}")
        parts.append(f"- 使用求解器: {ctx['solver_name']}")
        parts.append(f"- 求解耗时: {ctx['solving_time']}秒")
        parts.append("")

        parts.append("【求解结果】")
        parts.append(f"- 受影响列车: {ctx['affected_trains_count']}列")
        if ctx["affected_trains"]:
            parts.append(f"- 受影响车次: {', '.join(ctx['affected_trains'][:15])}")
        parts.append(f"- 总延误: {ctx['total_delay_minutes']}分钟")
        parts.append(f"- 最大延误: {ctx['max_delay_minutes']}分钟")
        # 【修复】明确标注 avg_delay 为晚点列车平均延误，避免调度员误解
        affected_count = ctx.get('affected_trains_count', 0)
        parts.append(f"- 晚点列车平均延误: {ctx['avg_delay_minutes']}分钟 (共{affected_count}列晚点列车)")
        parts.append("")

        # 具体列车调整
        if ctx["train_adjustments"]:
            parts.append("【列车调整详情（优化后时刻表）】")
            for train_id, stops in ctx["train_adjustments"].items():
                parts.append(f"{train_id}:")
                for s in stops:
                    parts.append(
                        f"  {s['station_name']}({s['station_code']}): "
                        f"延误{s['delay_minutes']}分钟"
                    )
            parts.append("")

        # 多方案对比
        if ctx.get("has_comparison") and ctx.get("comparison_results"):
            parts.append("【多方案对比数据】")
            for r in ctx["comparison_results"]:
                if r.get("success"):
                    parts.append(
                        f"- {r.get('solver', '?')}: "
                        f"总延误{r.get('total_delay_minutes', '?')}分, "
                        f"最大延误{r.get('max_delay_minutes', '?')}分, "
                        f"耗时{r.get('solving_time_seconds', '?')}秒"
                    )
                else:
                    parts.append(f"- {r.get('solver', '?')}: 失败({r.get('error', '')})")
            parts.append("")

        parts.append("请根据以上数据生成评估报告。输出JSON格式。")

        return "\n".join(parts)

    def _parse_llm_output(self, raw: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """解析 LLM 输出（容错：JSON 或纯文本）"""
        # 尝试 JSON 解析
        try:
            # 去除可能的 markdown 代码块标记
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()

            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                result = {
                    "llm_summary": parsed.get("llm_summary", parsed.get("summary", "")),
                    "feasibility_score": float(parsed.get("feasibility_score", 0.8)),
                    "risk_warnings": parsed.get("risk_warnings", []),
                    "natural_language_plan": parsed.get("natural_language_plan", ""),
                    "comparison_analysis": parsed.get("comparison_analysis", ""),
                    "response_type": "json"
                }
                # 处理转义换行
                for key in ("natural_language_plan", "comparison_analysis", "llm_summary"):
                    if isinstance(result[key], str) and "\\n" in result[key]:
                        result[key] = result[key].replace("\\n", "\n").replace("\\t", "\t")
                return result
        except (json.JSONDecodeError, ValueError):
            pass

        # JSON 解析失败，直接使用纯文本作为方案
        logger.debug("[L4] LLM 输出非 JSON，使用纯文本")
        return {
            "llm_summary": raw[:200],
            "feasibility_score": 0.7,
            "risk_warnings": [],
            "natural_language_plan": raw,
            "comparison_analysis": "",
            "response_type": "text"
        }

    # ================================================================
    # EvaluationReport 构建
    # ================================================================

    def _build_evaluation_report(
        self,
        skill_execution_result: Dict[str, Any],
        solver_response: Any,
        high_speed_metrics: Dict[str, Any],
        llm_result: Dict[str, Any]
    ) -> EvaluationReport:
        """构建 EvaluationReport"""
        total_delay = skill_execution_result.get("total_delay_minutes", 0)
        max_delay = skill_execution_result.get("max_delay_minutes", 0)
        solving_time = skill_execution_result.get("solving_time", 0.0)
        affected_count = skill_execution_result.get("affected_trains_count", 0)

        llm_summary = llm_result.get("llm_summary", "评估完成")
        feasibility_score = llm_result.get("feasibility_score", 0.8)
        risk_warnings = llm_result.get("risk_warnings", [])

        # 【关键修复】优先使用 skill_execution_result 中的 affected_trains_count
        # 原因：_calculate_high_speed_metrics 从 solver_response.schedule 重新计算可能因数据格式问题导致不一致
        l4_affected_count = skill_execution_result.get("affected_trains_count", affected_count)
        if l4_affected_count != affected_count:
            logger.debug(f"[L4] affected_trains_count 修正: {affected_count} -> {l4_affected_count}")

        return EvaluationReport(
            solution_id="solution_001",
            is_feasible=float(feasibility_score) >= 0.5,
            total_delay_minutes=float(total_delay),
            max_delay_minutes=float(max_delay),
            solving_time_seconds=float(solving_time),
            affected_trains_count=int(l4_affected_count),
            risk_warnings=risk_warnings,
            constraint_satisfaction={},
            llm_summary=llm_summary,
            feasibility_score=float(feasibility_score),
            feasibility_risks=[],
            operational_risks=[],
            human_review_points=[],
            counterfactual_summary=llm_result.get("comparison_analysis", ""),
            why_not_other_solver="",
            confidence=float(feasibility_score),
            baseline_metrics=None,
            on_time_rate=high_speed_metrics.get("on_time_rate", 0.0),
            punctuality_strict=high_speed_metrics.get("punctuality_strict", 0.0),
            delay_std_dev=high_speed_metrics.get("delay_std_dev", 0.0),
            delay_propagation_depth=high_speed_metrics.get("delay_propagation_depth", 0),
            # 【关键修复】delay_propagation_breadth 使用 skill_execution_result 中的 affected_trains_count
            # 保证与求解器输出一致，避免 schedule 数据格式差异导致的不一致
            delay_propagation_breadth=int(l4_affected_count),
            propagation_coefficient=high_speed_metrics.get("propagation_coefficient", 0.0),
            micro_delay_count=high_speed_metrics.get("micro_delay_count", 0),
            small_delay_count=high_speed_metrics.get("small_delay_count", 0),
            medium_delay_count=high_speed_metrics.get("medium_delay_count", 0),
            large_delay_count=high_speed_metrics.get("large_delay_count", 0),
            evaluation_grade=high_speed_metrics.get("evaluation_grade", "unknown"),
            metadata={"llm_response_type": llm_result.get("response_type", "unknown")}
        )

    # ================================================================
    # PolicyEngine 决策（保持纯规则）
    # ================================================================

    def _make_policy_decision(self, evaluation_report, skill_execution_result):
        evaluation_result = {
            "is_feasible": evaluation_report.is_feasible,
            "total_delay_minutes": evaluation_report.total_delay_minutes,
            "max_delay_minutes": evaluation_report.max_delay_minutes,
            "feasibility_score": getattr(evaluation_report, 'feasibility_score', 0.8)
        }
        solver_metrics = {"solving_time": skill_execution_result.get("solving_time", 0.0)}
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

    def _infer_scene_type(self, evaluation_report, skill_execution_result=None):
        if skill_execution_result:
            scenario_type = skill_execution_result.get("scenario_type", "").lower()
            mapping = {
                "temporary_speed_limit": "TEMP_SPEED_LIMIT",
                "sudden_failure": "SUDDEN_FAILURE",
                "section_interrupt": "SECTION_INTERRUPT"
            }
            if scenario_type in mapping:
                return mapping[scenario_type]
        max_delay = evaluation_report.max_delay_minutes if evaluation_report else 0
        if max_delay > 60:
            return "SECTION_INTERRUPT"
        elif max_delay > 30:
            return "SUDDEN_FAILURE"
        return "TEMP_SPEED_LIMIT"

    def _build_rollback_feedback(self, policy_decision) -> RollbackFeedback:
        from models.common_enums import PolicyDecisionType
        return RollbackFeedback(
            needs_rerun=(policy_decision.decision == PolicyDecisionType.RERUN),
            rollback_reason=policy_decision.reason,
            suggested_fixes=policy_decision.suggested_fixes
        )

    def _log_evaluation_result(self, report, policy_decision, metrics):
        if not report:
            return
        logger.info("=" * 50)
        logger.info("【L4评估结果】")
        logger.info(f"  LLM摘要: {report.llm_summary}")
        logger.info(f"  可行性得分: {report.feasibility_score:.2f}")
        logger.info(f"  风险警告: {len(report.risk_warnings)}项")
        logger.info(f"  决策: {policy_decision.decision}")
        logger.info("  高铁专用指标:")
        logger.info(f"    准点率: {report.on_time_rate * 100:.1f}%")
        logger.info(f"    严格准点率: {report.punctuality_strict * 100:.1f}%")
        logger.info(f"    延误标准差: {report.delay_std_dev:.2f}秒")
        logger.info(f"    传播深度: {report.delay_propagation_depth}站")
        logger.info(f"    传播广度: {report.delay_propagation_breadth}列")
        logger.info(f"    综合评级: {report.evaluation_grade}")
        logger.info("=" * 50)

    # ================================================================
    # 回退方案（LLM 失败时使用）
    # ================================================================

    def _generate_fallback_plan(self, skill_execution_result, solver_response):
        try:
            total_delay = skill_execution_result.get("total_delay_minutes", 0)
            max_delay = skill_execution_result.get("max_delay_minutes", 0)
            affected_trains = skill_execution_result.get("affected_trains", [])
            solver_name = skill_execution_result.get("skill_name", "未知")

            lines = [
                "调度方案概要：",
                f"- 使用求解器: {solver_name}",
                f"- 受影响列车: {', '.join(affected_trains[:10]) if affected_trains else '无'}",
                f"- 总延误: {total_delay}分钟",
                f"- 最大延误: {max_delay}分钟",
                "- 建议: 根据优化结果调整列车发车顺序，确保追踪间隔满足安全约束"
            ]
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[L4] 生成回退方案失败: {e}")
            return "调度方案生成失败，请查看详细调度数据"

    def _generate_default_evaluation_report(self, skill_execution_result, solver_response):
        total_delay = skill_execution_result.get("total_delay_minutes", 0)
        max_delay = skill_execution_result.get("max_delay_minutes", 0)
        solving_time = skill_execution_result.get("solving_time", 0.0)
        affected = skill_execution_result.get("affected_trains_count", 0)
        return EvaluationReport(
            solution_id="solution_001", is_feasible=False,
            total_delay_minutes=float(total_delay), max_delay_minutes=float(max_delay),
            solving_time_seconds=float(solving_time), affected_trains_count=int(affected),
            risk_warnings=["求解执行失败"], constraint_satisfaction={},
            llm_summary="求解失败，建议检查约束条件或使用其他调度器。",
            feasibility_score=0.5, baseline_metrics=None,
            on_time_rate=1.0, punctuality_strict=1.0, delay_std_dev=0.0,
            delay_propagation_depth=0, delay_propagation_breadth=0, propagation_coefficient=0.0,
            micro_delay_count=0, small_delay_count=0, medium_delay_count=0, large_delay_count=0,
            evaluation_grade="C",
            feasibility_risks=["求解器执行失败"], operational_risks=[], human_review_points=[],
            counterfactual_summary="", why_not_other_solver="", confidence=0.3,
            metadata={"llm_response_type": "default_evaluation"}
        )

    # ================================================================
    # 高铁专用指标计算（规则，保持不变）
    # ================================================================

    def _calculate_high_speed_metrics(self, skill_execution_result, solver_response):
        try:
            schedule = None
            if solver_response:
                if hasattr(solver_response, 'schedule'):
                    schedule = solver_response.schedule
                elif isinstance(solver_response, dict):
                    # 支持 schedule 或 optimized_schedule
                    schedule = solver_response.get('schedule') or solver_response.get('optimized_schedule')
            if not schedule:
                return self._get_default_high_speed_metrics()

            # 【关键修复】指标计算必须基于所有列车，而不是仅延误站点
            total_trains = len(schedule)
            delay_by_train = {}
            train_max_delays = []
            all_delay_points = []

            for train_id, stops in schedule.items():
                train_delays = []
                for stop in stops:
                    delay = stop.get("delay_seconds", 0)
                    if delay > 0:
                        all_delay_points.append(delay)
                        train_delays.append(delay)
                if train_delays:
                    max_d = max(train_delays)
                    delay_by_train[train_id] = {
                        "max": max_d,
                        "avg": sum(train_delays) / len(train_delays),
                        "count": len(train_delays)
                    }
                    train_max_delays.append(max_d)
                else:
                    train_max_delays.append(0)

            affected_trains = len(delay_by_train)

            if not all_delay_points:
                return self._get_default_high_speed_metrics()

            # 准点率 = 最大延误 < 准点阈值的列车占比（基于所有列车）
            on_time_threshold = DispatchEnvConfig.on_time_threshold_seconds()
            on_time_rate = sum(1 for d in train_max_delays if d < on_time_threshold) / total_trains if total_trains > 0 else 1.0
            # 严格准点率 = 最大延误 < 3分钟(180秒) 的列车占比
            punctuality_strict = sum(1 for d in train_max_delays if d < 180) / total_trains if total_trains > 0 else 1.0

            # 标准差基于每列车的最大延误（包含0延误的列车，反映整体均衡性）
            avg_delay = sum(train_max_delays) / len(train_max_delays) if train_max_delays else 0.0
            if len(train_max_delays) > 1:
                variance = sum((d - avg_delay) ** 2 for d in train_max_delays) / len(train_max_delays)
                delay_std_dev = variance ** 0.5
            else:
                delay_std_dev = 0.0

            max_propagation = max((info["count"] for info in delay_by_train.values()), default=0)
            # 传播系数 = 传播深度 / 平均最大延误(分钟)
            propagation_coeff = max_propagation / (avg_delay / 60) if avg_delay > 0 else 0

            # 【统一】延误分级统计与 config/dispatch_env.yaml 保持一致
            levels = DispatchEnvConfig.delay_levels()
            micro_max = levels.get("micro", {}).get("max_minutes", 5) * 60
            small_max = levels.get("small", {}).get("max_minutes", 30) * 60
            medium_max = levels.get("medium", {}).get("max_minutes", 100) * 60
            micro = sum(1 for d in all_delay_points if 0 < d < micro_max)
            small = sum(1 for d in all_delay_points if micro_max <= d < small_max)
            medium = sum(1 for d in all_delay_points if small_max <= d < medium_max)
            large = sum(1 for d in all_delay_points if d >= medium_max)

            grade = self._calculate_evaluation_grade(
                on_time_rate, punctuality_strict,
                max(all_delay_points) if all_delay_points else 0,
                affected_trains / total_trains if total_trains > 0 else 0,
                propagation_coeff
            )
            return {
                "on_time_rate": round(on_time_rate, 3), "punctuality_strict": round(punctuality_strict, 3),
                "delay_std_dev": round(delay_std_dev, 2), "delay_propagation_depth": max_propagation,
                "delay_propagation_breadth": affected_trains, "propagation_coefficient": round(propagation_coeff, 3),
                "micro_delay_count": micro, "small_delay_count": small,
                "medium_delay_count": medium, "large_delay_count": large,
                "evaluation_grade": grade, "total_trains": total_trains, "affected_trains": affected_trains,
                "max_delay_seconds": max(all_delay_points) if all_delay_points else 0
            }
        except Exception as e:
            logger.warning(f"[L4] 计算高铁指标失败: {e}")
            return self._get_default_high_speed_metrics()

    def _get_default_high_speed_metrics(self):
        return {
            "on_time_rate": 1.0, "punctuality_strict": 1.0, "delay_std_dev": 0.0,
            "delay_propagation_depth": 0, "delay_propagation_breadth": 0, "propagation_coefficient": 0.0,
            "micro_delay_count": 0, "small_delay_count": 0, "medium_delay_count": 0, "large_delay_count": 0,
            "evaluation_grade": "A", "total_trains": 0, "affected_trains": 0
        }

    def _calculate_evaluation_grade(self, on_time_rate, punctuality_strict, max_delay, affected_ratio, propagation_coeff):
        score = 0
        score += on_time_rate * 30
        score += punctuality_strict * 25
        max_min = max_delay / 60
        score += 20 if max_min < 10 else (15 if max_min < 20 else (10 if max_min < 30 else 5))
        score += (1 - affected_ratio) * 15
        score += 10 if propagation_coeff < 0.5 else (7 if propagation_coeff < 1.0 else (4 if propagation_coeff < 2.0 else 1))
        if score >= 90: return "A"
        elif score >= 75: return "B"
        elif score >= 60: return "C"
        return "D"


# ================================================================
# L4 专业评估 System Prompt
# ================================================================

_EVALUATION_SYSTEM_PROMPT = """你是京广高铁（北京西→安阳东，13站，147列列车）高级调度评估专家。

## 你的职责
根据调度求解结果，生成专业的评估报告和调度方案文档。你的输出将直接用于调度决策汇报。

## 评估标准（基于京广高铁运营实际）

1. 正点率影响
   - 优秀：延误<5分钟的列车占比>90%
   - 良好：延误<5分钟的列车占比>75%
   - 需关注：延误>15分钟的列车超过3列

2. 最大延误控制
   - 优秀：单列车最大延误<10分钟
   - 可接受：单列车最大延误<20分钟
   - 需干预：单列车最大延误>30分钟

3. 延误均衡性
   - 延误是否集中在少数列车上（标准差越大越不均衡）
   - 理想状态：所有受影响列车延误相近

4. 执行可行性
   - 方案是否需要大量跨站协调
   - 追踪间隔是否满足最小安全间隔（3分钟）

## 输出要求

输出JSON，包含以下字段：

{
    "llm_summary": "2-3句话的方案总体评价（如：方案可行，总延误15分钟，最大延误8分钟，准点率92%，建议执行）",
    "feasibility_score": 0.85,
    "risk_warnings": ["风险项1", "风险项2"],
    "natural_language_plan": "完整的自然语言调度方案，包含：\\n【调整概述】原因+影响范围\\n【具体调整】每列车的调整详情（站点+时间变化）\\n【注意事项】安全要求+关键节点",
    "comparison_analysis": "如果有多个方案对比数据，生成对比分析（如无则留空字符串）"
}

## 多方案对比要求

当提供了多个方案的对比数据时，在 comparison_analysis 中：
1. 列出每个方案的关键指标
2. 分析各方案优劣
3. 给出明确的推荐结论和理由
4. 对比格式示例：
"方案对比分析：\\n- FCFS方案：总延误62分，最大延误18分，<1秒出解\\n- MIP方案：总延误45分，最大延误12分，45秒出解\\n推荐MIP方案：总延误减少27%，虽然求解耗时增加，但在当前非紧急场景下可接受。"

## 注意事项
- natural_language_plan 中的【具体调整】必须包含具体的站点和时刻变化
- 如果列车调整详情中给出了优化后时刻表数据，据此生成具体调整指令
- 风险警告要具体（如"G1563在石家庄站延误12分钟，可能影响后续D1234的发车"），不要泛泛而谈
- feasibility_score 基于整体方案质量给出 0-1 的评分"""
