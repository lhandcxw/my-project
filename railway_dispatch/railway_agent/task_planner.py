# -*- coding: utf-8 -*-
"""
任务规划器模块
基于模板生成任务计划，不依赖LLM
"""

import uuid
from typing import Dict, Any

from models.workflow_models import (
    SceneSpec,
    SceneType,
    TaskPlan,
    SubTask
)


# 定义子任务模板
SUBTASK_TEMPLATES = {
    SceneType.TEMPORARY_SPEED_LIMIT.value: [
        {
            "task_type": "collect_info",
            "description": "收集临时限速场景的基本信息",
            "input_keys": ["scene_spec", "location"],
            "output_keys": ["collected_info"]
        },
        {
            "task_type": "lookup_rule",
            "description": "查询临时限速相关的调度规则",
            "input_keys": ["scene_spec"],
            "output_keys": ["applicable_rules"]
        },
        {
            "task_type": "identify_affected_trains",
            "description": "识别受临时限速影响的列车",
            "input_keys": ["scene_spec", "dispatch_context"],
            "output_keys": ["affected_trains"]
        },
        {
            "task_type": "build_solver_request",
            "description": "构建求解器请求数据",
            "input_keys": ["scene_spec", "dispatch_context", "affected_trains"],
            "output_keys": ["solver_request"]
        },
        {
            "task_type": "run_solver",
            "description": "运行求解器生成调度方案",
            "input_keys": ["solver_request"],
            "output_keys": ["solver_result"]
        },
        {
            "task_type": "validate_solution",
            "description": "验证生成的调度方案是否满足规则",
            "input_keys": ["solver_result"],
            "output_keys": ["validation_report"]
        },
        {
            "task_type": "compare_with_baseline",
            "description": "与基准调度方案进行比较",
            "input_keys": ["solver_result"],
            "output_keys": ["comparison_result"]
        }
    ],
    SceneType.SUDDEN_FAILURE.value: [
        {
            "task_type": "collect_info",
            "description": "收集突发故障场景的基本信息",
            "input_keys": ["scene_spec", "location"],
            "output_keys": ["collected_info"]
        },
        {
            "task_type": "lookup_rule",
            "description": "查询突发故障相关的调度规则",
            "input_keys": ["scene_spec"],
            "output_keys": ["applicable_rules"]
        },
        {
            "task_type": "identify_affected_trains",
            "description": "识别受故障影响的列车",
            "input_keys": ["scene_spec", "dispatch_context"],
            "output_keys": ["affected_trains"]
        },
        {
            "task_type": "build_solver_request",
            "description": "构建求解器请求数据",
            "input_keys": ["scene_spec", "dispatch_context", "affected_trains"],
            "output_keys": ["solver_request"]
        },
        {
            "task_type": "run_solver",
            "description": "运行求解器生成调度方案",
            "input_keys": ["solver_request"],
            "output_keys": ["solver_result"]
        },
        {
            "task_type": "validate_solution",
            "description": "验证生成的调度方案",
            "input_keys": ["solver_result"],
            "output_keys": ["validation_report"]
        }
    ],
    SceneType.SECTION_INTERRUPT.value: [
        {
            "task_type": "collect_info",
            "description": "收集区间中断场景的基本信息",
            "input_keys": ["scene_spec", "location"],
            "output_keys": ["collected_info"]
        },
        {
            "task_type": "lookup_rule",
            "description": "查询区间中断相关的调度规则",
            "input_keys": ["scene_spec"],
            "output_keys": ["applicable_rules"]
        },
        {
            "task_type": "identify_affected_trains",
            "description": "识别受区间中断影响的列车",
            "input_keys": ["scene_spec", "dispatch_context"],
            "output_keys": ["affected_trains"]
        },
        {
            "task_type": "build_solver_request",
            "description": "构建求解器请求数据",
            "input_keys": ["scene_spec", "dispatch_context", "affected_trains"],
            "output_keys": ["solver_request"]
        },
        {
            "task_type": "run_solver",
            "description": "运行求解器生成调度方案",
            "input_keys": ["solver_request"],
            "output_keys": ["solver_result"]
        },
        {
            "task_type": "validate_solution",
            "description": "验证生成的调度方案",
            "input_keys": ["solver_result"],
            "output_keys": ["validation_report"]
        }
    ]
}


def plan_task(
    scene_spec: SceneSpec,
    dispatch_context: Any
) -> TaskPlan:
    """
    基于场景规格和调度上下文生成任务计划

    Args:
        scene_spec: 场景规格
        dispatch_context: 调度上下文

    Returns:
        TaskPlan: 任务计划对象
    """
    task_id = f"task_{uuid.uuid4().hex[:8]}"

    # 获取场景类型对应的子任务模板
    scene_type = scene_spec.scene_type
    template = SUBTASK_TEMPLATES.get(scene_type, [])

    # 如果没有匹配的模板，使用默认模板
    if not template:
        template = [
            {
                "task_type": "collect_info",
                "description": f"收集 {scene_type} 场景信息",
                "input_keys": ["scene_spec"],
                "output_keys": ["collected_info"]
            },
            {
                "task_type": "build_solver_request",
                "description": "构建求解器请求",
                "input_keys": ["scene_spec", "dispatch_context"],
                "output_keys": ["solver_request"]
            },
            {
                "task_type": "run_solver",
                "description": "运行求解器",
                "input_keys": ["solver_request"],
                "output_keys": ["solver_result"]
            }
        ]

    # 生成子任务列表
    subtasks = []
    for i, template_item in enumerate(template):
        subtask = SubTask(
            task_id=f"{task_id}_subtask_{i+1}",
            task_type=template_item["task_type"],
            description=template_item["description"],
            input_data={"required_keys": template_item.get("input_keys", [])},
            output_data={"expected_keys": template_item.get("output_keys", [])},
            status="pending"
        )
        subtasks.append(subtask)

    return TaskPlan(
        task_id=task_id,
        scene_spec=scene_spec,
        subtasks=subtasks,
        status="planned",
        metadata={
            "template_type": scene_type,
            "subtask_count": len(subtasks)
        }
    )