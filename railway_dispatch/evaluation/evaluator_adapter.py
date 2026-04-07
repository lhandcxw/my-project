# -*- coding: utf-8 -*-
"""
Evaluator 适配器模块
为工作流提供基线对比和结果选择功能
"""

from typing import Optional, Dict, Any, Tuple
import logging

from models.workflow_models import SolverResult, DispatchContext

logger = logging.getLogger(__name__)


# 简单基线结果（不做任何调整）
BASELINE_EMPTY_RESULT = {
    "schedule": {},
    "metrics": {
        "max_delay_seconds": 0,
        "avg_delay_seconds": 0,
        "total_delay_seconds": 0,
        "affected_trains_count": 0
    },
    "message": "baseline (no adjustment)"
}


def get_baseline_result(dispatch_context: Optional[DispatchContext]) -> Dict[str, Any]:
    """
    获取基线结果（不做任何调整）

    Args:
        dispatch_context: 调度上下文

    Returns:
        dict: 基线结果
    """
    # 从 dispatch_context 获取原始计划
    baseline = {
        "schedule": {},
        "metrics": {
            "max_delay_seconds": 0,
            "avg_delay_seconds": 0,
            "total_delay_seconds": 0,
            "affected_trains_count": 0
        },
        "message": "baseline (original schedule, no adjustment)"
    }

    # 如果有列车信息，统计原始延误
    if dispatch_context and dispatch_context.trains:
        # 假设原始时刻表就是基线（无调整）
        baseline["metrics"]["affected_trains_count"] = len(dispatch_context.trains)

    return baseline


def compare_with_baseline(
    primary_result: Optional[SolverResult],
    dispatch_context: Optional[DispatchContext] = None
) -> Dict[str, Any]:
    """
    将主求解器结果与基线对比

    Args:
        primary_result: 主求解器结果
        dispatch_context: 调度上下文

    Returns:
        dict: 对比结果
    """
    # 获取基线
    baseline = get_baseline_result(dispatch_context)

    # 如果没有主结果
    if primary_result is None or not primary_result.success:
        return {
            "primary_exists": False,
            "baseline_metrics": baseline["metrics"],
            "comparison": "primary_not_available",
            "reason": "primary solver result is None or failed"
        }

    primary_metrics = primary_result.metrics or {}
    baseline_metrics = baseline["metrics"] or {}

    # 比较关键指标（兼容 dict 格式）
    primary_max_delay = primary_metrics.get("max_delay_seconds", 0)
    baseline_max_delay = baseline_metrics.get("max_delay_seconds", 0)

    primary_avg_delay = primary_metrics.get("avg_delay_seconds", 0)
    baseline_avg_delay = baseline_metrics.get("avg_delay_seconds", 0)

    # 判断优劣
    # 如果 primary 没有延误（基线），则 primary 更好
    if primary_max_delay < baseline_max_delay:
        comparison = "primary_better"
        reason = f"max_delay improved: {baseline_max_delay} -> {primary_max_delay}"
    elif primary_max_delay > baseline_max_delay:
        comparison = "baseline_better"
        reason = f"max_delay worse: {baseline_max_delay} -> {primary_max_delay}"
    else:
        # 相等，看平均延误
        if primary_avg_delay <= baseline_avg_delay:
            comparison = "primary_better"
            reason = "max_delay equal, avg_delay improved"
        else:
            comparison = "baseline_better"
            reason = "max_delay equal, avg_delay worse"

    return {
        "primary_exists": True,
        "primary_metrics": primary_metrics,
        "baseline_metrics": baseline_metrics,
        "comparison": comparison,
        "reason": reason
    }


def select_safe_result(
    primary_result: Optional[SolverResult],
    baseline_result: Optional[Dict[str, Any]],
    validation_report: Optional[Dict[str, Any]]
) -> Tuple[Dict[str, Any], str]:
    """
    选择安全的结果（主求解器或基线）

    Args:
        primary_result: 主求解器结果
        baseline_result: 基线结果
        validation_report: 验证报告

    Returns:
        tuple: (selected_result, fallback_reason)
    """
    fallback_reason = "primary_better"

    # 1. 检查 validation
    if validation_report:
        is_valid = validation_report.get("is_valid", False)
        if not is_valid:
            issues = validation_report.get("issues", [])
            return baseline_result or BASELINE_EMPTY_RESULT, f"primary_infeasible: {issues}"

    # 2. 检查 primary 是否存在
    if primary_result is None or not primary_result.success:
        return baseline_result or BASELINE_EMPTY_RESULT, "primary_failed"

    # 3. 检查是否优于基线
    primary_metrics = primary_result.metrics or {}
    baseline_metrics = (baseline_result or {}).get("metrics", {})

    primary_max = primary_metrics.get("max_delay_seconds", 0)
    baseline_max = baseline_metrics.get("max_delay_seconds", 0)

    if primary_max <= baseline_max:
        # primary 更好或相等
        # 注意：相等时也选 primary，因为做了调整
        return {
            "schedule": primary_result.schedule,
            "metrics": primary_metrics,
            "solver_type": primary_result.solver_type,
            "message": "primary selected"
        }, "primary_better"
    else:
        # baseline 更好
        return baseline_result or BASELINE_EMPTY_RESULT, "baseline_only"


def create_comparison_report(
    comparison_result: Dict[str, Any],
    selected_result: Dict[str, Any],
    fallback_reason: str
) -> Dict[str, Any]:
    """
    创建对比报告（适配 WorkflowResult 格式）

    Args:
        comparison_result: compare_with_baseline 结果
        selected_result: select_safe_result 结果
        fallback_reason: 选择原因

    Returns:
        dict: 结构化对比报告
    """
    return {
        "comparison": comparison_result.get("comparison", "unknown"),
        "reason": comparison_result.get("reason", ""),
        "selected_solver": selected_result.get("solver_type", "baseline"),
        "fallback_reason": fallback_reason,
        "primary_metrics": comparison_result.get("primary_metrics", {}),
        "baseline_metrics": comparison_result.get("baseline_metrics", {})
    }