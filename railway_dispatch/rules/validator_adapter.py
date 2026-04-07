# -*- coding: utf-8 -*-
"""
Validator 适配器模块
包装现有 validator 为工作流可用的接口
"""

from typing import Optional, Dict, Any

from models.workflow_models import SolverResult, DispatchContext


def validate_solver_result(
    solver_result: Optional[SolverResult],
    dispatch_context: Optional[DispatchContext] = None
) -> Dict[str, Any]:
    """
    验证求解器结果

    Args:
        solver_result: 求解器结果
        dispatch_context: 调度上下文（可选）

    Returns:
        dict: 验证报告
        {
            "is_feasible": bool,
            "issues": list,
            "warnings": list
        }
    """
    issues = []
    warnings = []

    # 1. 检查 solver_result 是否存在
    if solver_result is None:
        return {
            "is_feasible": False,
            "issues": ["solver_result is None"],
            "warnings": []
        }

    # 2. 检查 success 标志
    if not solver_result.success:
        issues.append("solver_result.success is False")
        return {
            "is_feasible": False,
            "issues": issues,
            "warnings": warnings
        }

    # 3. 检查 schedule 是否为空（兼容 dict 格式）
    schedule = solver_result.schedule
    if not schedule or (isinstance(schedule, list) and len(schedule) == 0):
        issues.append("schedule is empty")
        return {
            "is_feasible": False,
            "issues": issues,
            "warnings": warnings
        }

    # 如果是 dict 格式，转换为 list 格式进行验证
    if isinstance(schedule, dict):
        # dict 格式：{"train_id": [...]}
        if len(schedule) == 0:
            issues.append("schedule dict is empty")
            return {"is_feasible": False, "issues": issues, "warnings": warnings}
        # dict 格式认为是有效的
        warnings.append("schedule is in dict format (legacy)")

    # 4. 检查 metrics
    if not solver_result.metrics:
        warnings.append("metrics is missing")
    else:
        # 检查关键指标
        max_delay = solver_result.metrics.get("max_delay_seconds", 0)
        if max_delay < 0:
            issues.append("max_delay_seconds is negative")
        if max_delay > 7200:  # 超过2小时
            warnings.append(f"max_delay_seconds is very large: {max_delay}")

        avg_delay = solver_result.metrics.get("avg_delay_seconds", 0)
        if avg_delay < 0:
            issues.append("avg_delay_seconds is negative")

    # 5. 检查求解时间
    if solver_result.solving_time_seconds > 300:  # 超过5分钟
        warnings.append(f"solving_time_seconds is large: {solver_result.solving_time_seconds}")

    return {
        "is_feasible": len(issues) == 0,
        "issues": issues,
        "warnings": warnings
    }


def create_validation_report(
    validation_result: Dict[str, Any],
    solver_result: Optional[SolverResult] = None
) -> Dict[str, Any]:
    """
    创建验证报告（适配 WorkflowResult 格式）

    Args:
        validation_result: validate_solver_result 的结果
        solver_result: 原始求解器结果

    Returns:
        dict: 结构化验证报告
    """
    return {
        "is_valid": validation_result["is_feasible"],
        "issues": [
            {"severity": "error", "description": issue}
            for issue in validation_result["issues"]
        ],
        "warnings": [
            {"severity": "warning", "description": warn}
            for warn in validation_result["warnings"]
        ],
        "passed_rules": ["success_flag", "non_empty_schedule", "valid_metrics"]
                      if validation_result["is_feasible"] else [],
        "metadata": {
            "solver_type": solver_result.solver_type if solver_result else "unknown",
            "solving_time": solver_result.solving_time_seconds if solver_result else 0
        }
    }