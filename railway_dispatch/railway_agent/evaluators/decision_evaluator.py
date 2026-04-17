# -*- coding: utf-8 -*-
"""
决策效果评估器
评估L2的决策质量，为微调数据集提供标签
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class DecisionQuality(Enum):
    """决策质量等级"""
    EXCELLENT = "excellent"  # 优秀（综合得分≥0.9）
    GOOD = "good"  # 良好（0.7≤得分<0.9）
    FAIR = "fair"  # 一般（0.5≤得分<0.7）
    POOR = "poor"  # 较差（得分<0.5）


@dataclass
class DecisionEvaluation:
    """决策评估结果"""
    decision_id: str  # 决策ID
    scene_category: str  # 场景类型
    scene_features: Dict[str, Any]  # 场景特征
    llm_decision: Dict[str, Any]  # LLM决策
    execution_results: List[Dict[str, Any]]  # 各solver执行结果
    final_choice: str  # 最终选择的solver

    # 评估指标
    quality_score: float  # 综合质量分数（0-1）
    quality_label: DecisionQuality  # 质量等级

    # 分维度得分
    scenario_match_score: float  # 场景匹配度
    delay_optimization_score: float  # 延误优化度
    efficiency_score: float  # 效率得分
    robustness_score: float  # 鲁棒性得分

    # 详细评估
    evaluation_details: Dict[str, Any]  # 详细评估说明
    improvement_baselines: Dict[str, float]  # 相对基线的提升

    # 标注信息（用于微调）
    is_positive_sample: bool  # 是否为正样本
    label_reason: str  # 标注理由
    human_feedback: Optional[str] = None  # 人工反馈（未来扩展）


class DecisionEvaluator:
    """
    决策效果评估器

    职责：
    1. 评估L2决策的质量
    2. 生成微调数据集的标签
    3. 记录决策溯源信息
    4. 提供"场景→决策→效果"的完整数据链
    """

    def __init__(self):
        self._evaluations: List[DecisionEvaluation] = []
        self._baseline_cache: Dict[str, Dict[str, Any]] = {}

    def evaluate_decision(
        self,
        scene_features: Dict[str, Any],
        llm_decision: Dict[str, Any],
        execution_results: List[Dict[str, Any]],
        final_choice: str,
        scenario_context: Optional[Dict[str, Any]] = None
    ) -> DecisionEvaluation:
        """
        评估决策质量

        Args:
            scene_features: 场景特征
            llm_decision: LLM决策
            execution_results: 各solver执行结果
            final_choice: 最终选择的solver
            scenario_context: 场景上下文（可选）

        Returns:
            DecisionEvaluation: 决策评估结果
        """
        decision_id = f"decision_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

        # 1. 场景匹配度评估（选的solver是否适合场景）
        scenario_match_score = self._evaluate_scenario_match(
            scene_features, llm_decision, final_choice
        )

        # 2. 延误优化度评估（实际延误指标）
        delay_optimization_score = self._evaluate_delay_optimization(
            execution_results, final_choice
        )

        # 3. 效率得分（求解时间是否符合预期）
        efficiency_score = self._evaluate_efficiency(
            execution_results, final_choice, llm_decision
        )

        # 4. 鲁棒性得分（决策是否稳定，是否有多候选方案）
        robustness_score = self._evaluate_robustness(
            execution_results, final_choice, llm_decision
        )

        # 综合得分（加权）
        weights = {
            "scenario_match": 0.35,  # 场景匹配最重要
            "delay_optimization": 0.40,  # 延误优化次之
            "efficiency": 0.15,  # 效率
            "robustness": 0.10  # 鲁棒性
        }

        quality_score = (
            scenario_match_score * weights["scenario_match"] +
            delay_optimization_score * weights["delay_optimization"] +
            efficiency_score * weights["efficiency"] +
            robustness_score * weights["robustness"]
        )

        # 质量等级
        if quality_score >= 0.9:
            quality_label = DecisionQuality.EXCELLENT
        elif quality_score >= 0.7:
            quality_label = DecisionQuality.GOOD
        elif quality_score >= 0.5:
            quality_label = DecisionQuality.FAIR
        else:
            quality_label = DecisionQuality.POOR

        # 计算相对基线的提升
        improvement_baselines = self._calculate_improvements(
            execution_results, final_choice
        )

        # 评估详情
        evaluation_details = {
            "场景匹配度": {
                "score": scenario_match_score,
                "explanation": self._explain_scenario_match(scene_features, final_choice)
            },
            "延误优化度": {
                "score": delay_optimization_score,
                "explanation": self._explain_delay_optimization(execution_results, final_choice)
            },
            "效率得分": {
                "score": efficiency_score,
                "explanation": self._explain_efficiency(execution_results, final_choice)
            },
            "鲁棒性得分": {
                "score": robustness_score,
                "explanation": self._explain_robustness(execution_results, final_choice)
            }
        }

        # 判断是否为正样本（用于微调）
        is_positive_sample = quality_score >= 0.7  # 质量良好及以上为正样本
        label_reason = f"综合得分{quality_score:.2f}，{quality_label.value}级决策"

        # 构建评估结果
        evaluation = DecisionEvaluation(
            decision_id=decision_id,
            scene_category=scene_features.get("场景类型", "unknown"),
            scene_features=scene_features,
            llm_decision=llm_decision,
            execution_results=execution_results,
            final_choice=final_choice,
            quality_score=quality_score,
            quality_label=quality_label,
            scenario_match_score=scenario_match_score,
            delay_optimization_score=delay_optimization_score,
            efficiency_score=efficiency_score,
            robustness_score=robustness_score,
            evaluation_details=evaluation_details,
            improvement_baselines=improvement_baselines,
            is_positive_sample=is_positive_sample,
            label_reason=label_reason
        )

        # 保存评估记录
        self._evaluations.append(evaluation)

        # 记录日志
        self._log_evaluation(evaluation)

        return evaluation

    def _evaluate_scenario_match(
        self,
        scene_features: Dict[str, Any],
        llm_decision: Dict[str, Any],
        final_choice: str
    ) -> float:
        """评估solver选择是否匹配场景特征"""
        scene_type = scene_features.get("场景类型", "")
        train_count = self._extract_train_count(scene_features)
        delay_level = self._extract_delay_level(scene_features)
        time_period = self._extract_time_period(scene_features)

        score = 1.0  # 初始满分

        # 规则1：区间封锁→应该选noop或fcfs
        if scene_type == "区间封锁":
            if final_choice not in ["noop", "fcfs"]:
                score -= 0.4  # 严重不匹配

        # 规则2：突发故障+严重延误+列车密集→应该选fcfs
        if scene_type == "突发故障" and delay_level == "严重" and train_count > 10:
            if final_choice == "mip":
                score -= 0.3  # MIP太慢

        # 规则3：临时限速+小规模+平峰期→应该选mip
        if scene_type == "临时限速" and train_count <= 5 and time_period == "平峰期":
            if final_choice != "mip":
                score -= 0.2  # MIP更优但没选

        # 规则4：高峰期+紧急→应该选fcfs（响应快）
        if time_period in ["高峰期", "运营初期"] and delay_level in ["较大", "严重"]:
            if final_choice == "mip":
                score -= 0.15

        return max(0.0, min(1.0, score))

    def _evaluate_delay_optimization(
        self,
        execution_results: List[Dict[str, Any]],
        final_choice: str
    ) -> float:
        """评估延误优化效果"""
        # 找到最终选择的solver的结果
        final_result = None
        for result in execution_results:
            if result.get("solver_name") == final_choice and result.get("success"):
                final_result = result
                break

        if not final_result:
            return 0.0  # 求解失败

        # 找到所有成功的结果
        successful_results = [
            r for r in execution_results
            if r.get("success") and isinstance(r, dict)
        ]

        if not successful_results:
            return 0.0

        # 基于延误指标评估
        final_max_delay = final_result.get("max_delay_minutes", 999)
        final_total_delay = final_result.get("total_delay_minutes", 999)

        # 找到最优的延误指标
        best_max_delay = min(r.get("max_delay_minutes", 999) for r in successful_results)
        best_total_delay = min(r.get("total_delay_minutes", 999) for r in successful_results)

        # 归一化得分（越接近最优越高）
        max_delay_score = 1.0 - (final_max_delay - best_max_delay) / max(final_max_delay, 1)
        total_delay_score = 1.0 - (final_total_delay - best_total_delay) / max(final_total_delay, 1)

        # 综合得分（最大延误权重更高）
        score = max_delay_score * 0.6 + total_delay_score * 0.4

        return max(0.0, min(1.0, score))

    def _evaluate_efficiency(
        self,
        execution_results: List[Dict[str, Any]],
        final_choice: str,
        llm_decision: Dict[str, Any]
    ) -> float:
        """评估效率（求解时间）"""
        final_result = None
        for result in execution_results:
            if result.get("solver_name") == final_choice and result.get("success"):
                final_result = result
                break

        if not final_result:
            return 0.0

        # 获取LLM预测的求解时间
        solver_config = llm_decision.get("solver_config", {})
        expected_time = solver_config.get("time_limit", 120)  # 默认120秒
        actual_time = final_result.get("solving_time", 0)

        # 求解时间应该在预期范围内（不超过预期1.5倍）
        if actual_time <= expected_time:
            score = 1.0
        elif actual_time <= expected_time * 1.5:
            score = 0.8
        elif actual_time <= expected_time * 2.0:
            score = 0.5
        else:
            score = 0.2  # 严重超时

        return score

    def _evaluate_robustness(
        self,
        execution_results: List[Dict[str, Any]],
        final_choice: str,
        llm_decision: Dict[str, Any]
    ) -> float:
        """评估鲁棒性（决策的稳定性）"""
        # 1. 是否提供了多个候选solver（表明LLM考虑了多种可能性）
        solver_candidates = llm_decision.get("solver_candidates", [])
        if len(solver_candidates) >= 2:
            score = 1.0
        elif len(solver_candidates) == 1:
            score = 0.6
        else:
            score = 0.2

        # 2. 最终选择的solver是否在候选列表中
        if final_choice in solver_candidates:
            score *= 1.0  # 不扣分
        else:
            score *= 0.5  # 扣分

        return score

    def _calculate_improvements(
        self,
        execution_results: List[Dict[str, Any]],
        final_choice: str
    ) -> Dict[str, float]:
        """计算相对基线的提升"""
        final_result = None
        for result in execution_results:
            if result.get("solver_name") == final_choice and result.get("success"):
                final_result = result
                break

        if not final_result:
            return {}

        improvements = {}
        final_max_delay = final_result.get("max_delay_minutes", 0)
        final_total_delay = final_result.get("total_delay_minutes", 0)

        # 对比每个基线solver
        for result in execution_results:
            if not result.get("success") or result.get("solver_name") == final_choice:
                continue

            solver_name = result.get("solver_name", "unknown")
            baseline_max = result.get("max_delay_minutes", 0)
            baseline_total = result.get("total_delay_minutes", 0)

            if baseline_max > 0:
                max_delay_improvement = (baseline_max - final_max_delay) / baseline_max * 100
                improvements[f"vs_{solver_name}_max_delay"] = round(max_delay_improvement, 1)

            if baseline_total > 0:
                total_delay_improvement = (baseline_total - final_total_delay) / baseline_total * 100
                improvements[f"vs_{solver_name}_total_delay"] = round(total_delay_improvement, 1)

        return improvements

    def _extract_train_count(self, scene_features: Dict[str, Any]) -> int:
        """提取列车数量"""
        for key, value in scene_features.items():
            if "列车" in key and isinstance(value, int):
                return value
            if "受影响列车数" in key and isinstance(value, int):
                return value
        return 5  # 默认值

    def _extract_delay_level(self, scene_features: Dict[str, Any]) -> str:
        """提取延误等级"""
        for key, value in scene_features.items():
            if "延误等级" in key:
                return value
        return "一般"  # 默认值

    def _extract_time_period(self, scene_features: Dict[str, Any]) -> str:
        """提取运营时段"""
        for key, value in scene_features.items():
            if "时段" in key:
                return value
        return "平峰期"  # 默认值

    def _explain_scenario_match(
        self,
        scene_features: Dict[str, Any],
        final_choice: str
    ) -> str:
        """解释场景匹配度"""
        scene_type = scene_features.get("场景类型", "")
        train_count = self._extract_train_count(scene_features)

        explanations = []
        if scene_type == "临时限速":
            if final_choice == "mip":
                explanations.append(f"临时限速场景选MIP是合适的（列车数{train_count}）")
            else:
                explanations.append(f"临时限速场景建议选MIP，但选了{final_choice}")

        elif scene_type == "突发故障":
            if final_choice == "fcfs":
                explanations.append("突发故障场景选FCFS是合适的（快速响应）")
            else:
                explanations.append(f"突发故障场景建议选FCFS，但选了{final_choice}")

        return "；".join(explanations) if explanations else "场景匹配"

    def _explain_delay_optimization(
        self,
        execution_results: List[Dict[str, Any]],
        final_choice: str
    ) -> str:
        """解释延误优化度"""
        final_result = None
        for result in execution_results:
            if result.get("solver_name") == final_choice and result.get("success"):
                final_result = result
                break

        if not final_result:
            return "求解失败，无法评估"

        max_delay = final_result.get("max_delay_minutes", 0)
        total_delay = final_result.get("total_delay_minutes", 0)

        return f"最大延误{max_delay}分，总延误{total_delay}分"

    def _explain_efficiency(
        self,
        execution_results: List[Dict[str, Any]],
        final_choice: str
    ) -> str:
        """解释效率得分"""
        final_result = None
        for result in execution_results:
            if result.get("solver_name") == final_choice and result.get("success"):
                final_result = result
                break

        if not final_result:
            return "求解失败"

        solving_time = final_result.get("solving_time", 0)
        return f"求解耗时{solving_time:.2f}秒"

    def _explain_robustness(
        self,
        execution_results: List[Dict[str, Any]],
        final_choice: str
    ) -> str:
        """解释鲁棒性得分"""
        return "提供了多候选方案" if len(execution_results) > 1 else "单一方案"

    def _log_evaluation(self, evaluation: DecisionEvaluation):
        """记录评估日志"""
        logger.info("=" * 70)
        logger.info("【L2决策效果评估】")
        logger.info(f"  决策ID: {evaluation.decision_id}")
        logger.info(f"  场景类型: {evaluation.scene_category}")
        logger.info(f"  最终选择: {evaluation.final_choice}")
        logger.info(f"  综合得分: {evaluation.quality_score:.2f} / 1.0")
        logger.info(f"  质量等级: {evaluation.quality_label.value}")
        logger.info(f"  是否正样本: {evaluation.is_positive_sample}")
        logger.info(f"  标注理由: {evaluation.label_reason}")
        logger.info("")
        logger.info("【分维度得分】")
        logger.info(f"  场景匹配度: {evaluation.scenario_match_score:.2f}")
        logger.info(f"  延误优化度: {evaluation.delay_optimization_score:.2f}")
        logger.info(f"  效率得分: {evaluation.efficiency_score:.2f}")
        logger.info(f"  鲁棒性得分: {evaluation.robustness_score:.2f}")
        logger.info("")
        if evaluation.improvement_baselines:
            logger.info("【相对基线提升】")
            for baseline, improvement in evaluation.improvement_baselines.items():
                logger.info(f"  {baseline}: {improvement:+.1f}%")
        logger.info("=" * 70)

    def get_positive_samples(self) -> List[DecisionEvaluation]:
        """获取所有正样本"""
        return [e for e in self._evaluations if e.is_positive_sample]

    def get_negative_samples(self) -> List[DecisionEvaluation]:
        """获取所有负样本"""
        return [e for e in self._evaluations if not e.is_positive_sample]

    def export_for_finetuning(self, filepath: str):
        """导出微调数据集"""
        import json
        from pathlib import Path

        Path(filepath).parent.mkdir(parents=True, exist_ok=True)

        finetuning_data = []
        for evaluation in self._evaluations:
            # 构建训练样本
            sample = {
                # 输入：场景特征
                "input": {
                    "场景特征": evaluation.scene_features,
                    "场景类别": evaluation.scene_category
                },
                # 输出：最优决策
                "output": {
                    "solver_suggestion": evaluation.llm_decision.get("solver_suggestion"),
                    "solver_candidates": evaluation.llm_decision.get("solver_candidates"),
                    "solver_config": evaluation.llm_decision.get("solver_config"),
                    "objective_weights": evaluation.llm_decision.get("objective_weights"),
                    "reasoning": evaluation.llm_decision.get("reasoning")
                },
                # 标签：决策质量
                "label": {
                    "quality_score": evaluation.quality_score,
                    "quality_label": evaluation.quality_label.value,
                    "is_positive_sample": evaluation.is_positive_sample,
                    "label_reason": evaluation.label_reason
                },
                # 元数据
                "metadata": {
                    "decision_id": evaluation.decision_id,
                    "final_choice": evaluation.final_choice,
                    "evaluation_details": evaluation.evaluation_details,
                    "improvement_baselines": evaluation.improvement_baselines,
                    "timestamp": datetime.now().isoformat()
                }
            }
            finetuning_data.append(sample)

        # 导出为JSONL格式
        with open(filepath, 'w', encoding='utf-8') as f:
            for sample in finetuning_data:
                f.write(json.dumps(sample, ensure_ascii=False) + '\n')

        logger.info(f"导出微调数据集: {len(finetuning_data)} 条样本 → {filepath}")


# 全局实例
_evaluator: Optional[DecisionEvaluator] = None


def get_decision_evaluator() -> DecisionEvaluator:
    """获取全局决策评估器实例"""
    global _evaluator
    if _evaluator is None:
        _evaluator = DecisionEvaluator()
    return _evaluator
