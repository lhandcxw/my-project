# -*- coding: utf-8 -*-
"""
Evaluator 适配器
统一方案评估接口
"""

from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class EvaluatorAdapter:
    """
    Evaluator 适配器
    封装方案评估，提供统一的接口
    """
    
    def evaluate(
        self,
        solver_result: Any,
        baseline_result: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        评估调度方案
        
        Args:
            solver_result: 求解结果
            baseline_result: 基线结果（可选）
            
        Returns:
            Dict: 评估报告
        """
        logger.info("EvaluatorAdapter 执行评估")
        
        # 提取评估指标
        metrics = {}
        if solver_result and hasattr(solver_result, 'metrics'):
            metrics = solver_result.metrics or {}
        
        total_delay = metrics.get("total_delay_seconds", 0) // 60
        max_delay = metrics.get("max_delay_seconds", 0) // 60
        
        # 构建评估报告
        evaluation = {
            "is_feasible": True,
            "total_delay_minutes": total_delay,
            "max_delay_minutes": max_delay,
            "solving_time_seconds": solver_result.solving_time_seconds if solver_result else 0,
            "solver_type": solver_result.solver_type if solver_result else "unknown",
            "risk_warnings": [],
            "constraint_satisfaction": {},
            "baseline_comparison": None
        }
        
        # 与基线对比
        if baseline_result:
            baseline_metrics = baseline_result.metrics or {}
            baseline_total_delay = baseline_metrics.get("total_delay_seconds", 0) // 60
            evaluation["baseline_comparison"] = {
                "improvement": baseline_total_delay - total_delay,
                "improvement_rate": (baseline_total_delay - total_delay) / baseline_total_delay if baseline_total_delay > 0 else 0
            }
        
        return evaluation
    
    def evaluate_with_llm(
        self,
        solver_result: Any,
        user_prompt: str = ""
    ) -> Dict[str, Any]:
        """
        使用 LLM 辅助评估
        
        Args:
            solver_result: 求解结果
            user_prompt: 用户提示
            
        Returns:
            Dict: 评估报告
        """
        logger.info("EvaluatorAdapter 执行 LLM 辅助评估")
        
        # 先做基础评估
        base_evaluation = self.evaluate(solver_result)
        
        # TODO: 调用 LLM 生成解释和风险提示
        # 这里暂时只返回基础评估
        return base_evaluation


# 全局实例
_evaluator_adapter: Optional[EvaluatorAdapter] = None


def get_evaluator_adapter() -> EvaluatorAdapter:
    """获取 Evaluator 适配器实例"""
    global _evaluator_adapter
    if _evaluator_adapter is None:
        _evaluator_adapter = EvaluatorAdapter()
    return _evaluator_adapter