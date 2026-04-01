# -*- coding: utf-8 -*-
"""
工作流 dry-run 测试
测试工作流引擎的 dry-run 模式
"""

import sys

# 添加项目根目录到 Python 路径
sys.path.insert(0, 'e:/LLM-TTRA/test-agent/railway_dispatch')

from railway_agent.workflow_engine import run_workflow


def test_workflow_dry_run_temporary_speed_limit():
    """测试临时限速场景的 dry-run 模式"""
    # 构造临时限速输入
    raw_input = {
        "scene_type": "temporary_speed_limit",
        "scene_id": "test_tsl_001",
        "description": "测试临时限速场景",
        "location": {
            "section": "TJG-BJS",
            "station_code": "TJG"
        },
        "time_info": {
            "start_time": "08:00:00",
            "duration_minutes": 30
        }
    }

    # 调用 run_workflow（dry-run 模式）
    result = run_workflow(raw_input, dry_run=True)

    # 断言验证
    assert result.success, "工作流执行应该成功"
    assert result.scene_spec is not None, "scene_spec 不应该为 None"
    assert result.scene_spec.scene_type == "temporary_speed_limit", "场景类型应该匹配"
    assert result.task_plan is not None, "task_plan 不应该为 None"
    assert len(result.task_plan.subtasks) > 0, "subtasks 数量应该大于 0"
    assert "scene_spec" in result.debug_trace or "task_plan" in result.debug_trace, \
        "debug_trace 应该包含 scene_spec 或 task_plan"
    assert result.message == "Dry-run completed successfully", "消息应该匹配"


def test_workflow_dry_run_with_trains():
    """测试带列车数据的 dry-run 模式"""
    # 构造带列车数据的输入
    raw_input = {
        "scene_type": "sudden_failure",
        "scene_id": "test_sf_001",
        "description": "测试突发故障场景",
        "location": {
            "station_code": "BJS",
            "failure_type": "vehicle_breakdown"
        },
        "time_info": {
            "occurrence_time": "09:30:00"
        }
    }

    # 构造简单的列车数据
    trains = [
        {"train_id": "G101", "train_type": "高速动车组"},
        {"train_id": "G102", "train_type": "高速动车组"}
    ]

    # 调用 run_workflow（dry-run 模式，带数据）
    result = run_workflow(raw_input, trains=trains, stations=None, dry_run=True)

    # 断言验证
    assert result.success, "工作流执行应该成功"
    assert result.scene_spec is not None, "scene_spec 不应该为 None"
    assert result.task_plan is not None, "task_plan 不应该为 None"
    assert len(result.task_plan.subtasks) > 0, "subtasks 数量应该大于 0"


if __name__ == "__main__":
    # 直接运行测试
    test_workflow_dry_run_temporary_speed_limit()
    print("test_workflow_dry_run_temporary_speed_limit 通过!")
    test_workflow_dry_run_with_trains()
    print("test_workflow_dry_run_with_trains 通过!")
    print("所有测试通过!")