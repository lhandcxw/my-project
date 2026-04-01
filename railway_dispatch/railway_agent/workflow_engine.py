# -*- coding: utf-8 -*-
"""
工作流引擎模块
负责串联整个工作流执行过程
"""

from typing import Optional, List, Dict, Any
import traceback
from datetime import datetime

from models.workflow_models import (
    SceneSpec,
    DispatchContext,
    TaskPlan,
    WorkflowResult,
    SolverResult,
    ValidationReport,
    ValidationIssue,
    SubTask
)

from railway_agent.context_builder import (
    build_scene_spec,
    build_dispatch_context,
    identify_affected_trains
)
from railway_agent.task_planner import plan_task

# 导入求解器模块
from solver.solver_registry import get_default_registry, SolverRegistry
from solver.base_solver import SolverRequest


def run_workflow(
    raw_input: dict,
    trains=None,
    stations=None,
    dry_run: bool = True
) -> WorkflowResult:
    """
    运行工作流

    Args:
        raw_input: 原始输入字典
        trains: 列车数据（可选）
        stations: 车站数据（可选）
        dry_run: 是否为 dry-run 模式

    Returns:
        WorkflowResult: 工作流执行结果
    """
    debug_trace = {}
    start_time = datetime.now()

    try:
        # Step 1: 构建场景规格
        scene_spec = build_scene_spec(raw_input)
        debug_trace["scene_spec"] = {
            "scene_type": scene_spec.scene_type,
            "scene_id": scene_spec.scene_id,
            "description": scene_spec.description
        }

        # Step 2: 构建调度上下文
        dispatch_context = build_dispatch_context(
            scene_spec=scene_spec,
            trains=trains,
            stations=stations,
            data_loader=None
        )
        debug_trace["dispatch_context"] = {
            "trains_count": len(dispatch_context.trains),
            "stations_count": len(dispatch_context.stations),
            "has_data_loader_info": dispatch_context.data_loader_info is not None
        }

        # Step 3: 识别受影响列车
        affected_trains_result = identify_affected_trains(scene_spec, dispatch_context)
        dispatch_context.affected_trains = affected_trains_result.get("affected_trains", [])
        debug_trace["affected_trains"] = {
            "count": len(dispatch_context.affected_trains),
            "rule": affected_trains_result.get("rule", "unknown")
        }

        # Step 4: 生成任务计划
        task_plan = plan_task(scene_spec, dispatch_context)
        debug_trace["task_plan"] = {
            "task_id": task_plan.task_id,
            "subtask_count": len(task_plan.subtasks),
            "subtask_types": [s.task_type for s in task_plan.subtasks]
        }

        # Dry-run 模式：返回占位结果
        if dry_run:
            return WorkflowResult(
                success=True,
                scene_spec=scene_spec,
                task_plan=task_plan,
                solver_result=None,
                validation_report=None,
                debug_trace=debug_trace,
                message="Dry-run completed successfully",
                metadata={
                    "dry_run": True,
                    "execution_time": (datetime.now() - start_time).total_seconds()
                }
            )

        # 非 dry-run 模式：调用真实求解器
        # Step 5: 获取求解器
        registry = get_default_registry()
        solver = registry.select_solver(scene_spec.scene_type)

        if solver is None:
            # 求解器选择失败，返回状态而非抛异常
            return WorkflowResult(
                success=False,
                scene_spec=scene_spec,
                task_plan=task_plan,
                solver_result=None,
                validation_report=None,
                debug_trace=debug_trace,
                message="求解器选择失败",
                error="solver_selection_failed",
                metadata={
                    "dry_run": False,
                    "execution_time": (datetime.now() - start_time).total_seconds()
                }
            )

        # Step 6: 构建求解器请求
        # 从 raw_input 中提取 injected_delays
        injected_delays = raw_input.get("injected_delays", [])

        solver_request = SolverRequest(
            scene_type=scene_spec.scene_type,
            scene_id=scene_spec.scene_id,
            trains=dispatch_context.trains,
            stations=dispatch_context.stations,
            injected_delays=injected_delays,
            solver_config=raw_input.get("solver_config", {}),
            metadata={
                "scenario_type": scene_spec.scene_type,
                "affected_trains": [at.train_id for at in dispatch_context.affected_trains]
            }
        )

        # Step 7: 执行求解
        solver_response = solver.solve(solver_request)

        # Step 8: 构建 SolverResult
        solver_result = SolverResult(
            success=solver_response.success,
            schedule=solver_response.schedule,
            metrics=solver_response.metrics,
            solving_time_seconds=solver_response.solving_time_seconds,
            solver_type=solver_response.solver_type,
            error_message=solver_response.error,
            metadata=solver_response.metadata
        )

        # 记录求解结果
        debug_trace["solver"] = {
            "solver_type": solver_response.solver_type,
            "status": solver_response.status,
            "success": solver_response.success,
            "solving_time": solver_response.solving_time_seconds
        }

        # 如果 solver 失败，返回 status="solver_failed"
        if solver_response.status == "solver_failed":
            return WorkflowResult(
                success=False,
                scene_spec=scene_spec,
                task_plan=task_plan,
                solver_result=solver_result,
                validation_report=None,
                debug_trace=debug_trace,
                message=solver_response.message or "求解器执行失败",
                error="solver_failed",
                metadata={
                    "dry_run": False,
                    "execution_time": (datetime.now() - start_time).total_seconds()
                }
            )

        # 成功
        return WorkflowResult(
            success=True,
            scene_spec=scene_spec,
            task_plan=task_plan,
            solver_result=solver_result,
            validation_report=None,
            debug_trace=debug_trace,
            message="工作流执行成功",
            metadata={
                "dry_run": False,
                "execution_time": (datetime.now() - start_time).total_seconds()
            }
        )

    except Exception as e:
        # 处理异常，但不抛出，转换为错误返回
        tb = traceback.format_exc()
        return WorkflowResult(
            success=False,
            scene_spec=None,
            task_plan=None,
            solver_result=None,
            validation_report=None,
            debug_trace=debug_trace,
            message=f"Workflow execution failed: {str(e)}",
            error=tb,
            metadata={
                "dry_run": dry_run,
                "execution_time": (datetime.now() - start_time).total_seconds()
            }
        )


def run_workflow_with_solver(
    raw_input: dict,
    solver,
    validator=None,
    trains=None,
    stations=None
) -> WorkflowResult:
    """
    运行工作流（包含真实 solver 调用）

    注意：此函数为后续扩展预留，当前版本不支持

    Args:
        raw_input: 原始输入字典
        solver: 求解器实例
        validator: 验证器实例（可选）
        trains: 列车数据（可选）
        stations: 车站数据（可选）

    Returns:
        WorkflowResult: 工作流执行结果
    """
    raise NotImplementedError(
        "run_workflow_with_solver is not implemented yet. "
        "This is reserved for future MARL solver integration."
    )