# -*- coding: utf-8 -*-
"""
Phase A 验证脚本
验证工作流骨架的基本功能
"""

import sys
import traceback

# 添加项目根目录到 Python 路径
sys.path.insert(0, 'e:/LLM-TTRA/test-agent/railway_dispatch')

from railway_agent.workflow_engine import run_workflow


def test_temporary_speed_limit():
    """测试临时限速场景"""
    print("=" * 60)
    print("测试: temporary_speed_limit")
    print("=" * 60)

    raw_input = {
        "scene_type": "temporary_speed_limit",
        "scene_id": "test_tsl_001",
        "description": "临时限速测试场景",
        "location": {
            "section": "TJG-BJS",
            "station_code": "TJG"
        },
        "time_info": {
            "start_time": "08:00:00",
            "duration_minutes": 30
        }
    }

    try:
        result = run_workflow(raw_input, dry_run=True)

        print(f"Scene Type: {result.scene_spec.scene_type}")
        print(f"Task ID: {result.task_plan.task_id}")
        print(f"Subtasks 数量: {len(result.task_plan.subtasks)}")
        print(f"Debug Trace Keys: {list(result.debug_trace.keys())}")
        print(f"Success: {result.success}")
        print(f"Message: {result.message}")

        assert result.success, "工作流执行失败"
        assert result.scene_spec.scene_type == "temporary_speed_limit", "场景类型不匹配"
        assert len(result.task_plan.subtasks) > 0, "没有子任务"

        print("\n[PASS] temporary_speed_limit 测试通过")
        return True

    except Exception as e:
        print(f"\n[FAIL] temporary_speed_limit 测试失败: {str(e)}")
        traceback.print_exc()
        return False


def test_sudden_failure():
    """测试突发故障场景"""
    print("\n" + "=" * 60)
    print("测试: sudden_failure")
    print("=" * 60)

    raw_input = {
        "scene_type": "sudden_failure",
        "scene_id": "test_sf_001",
        "description": "突发故障测试场景",
        "location": {
            "station_code": "BJS",
            "failure_type": "vehicle_breakdown"
        },
        "time_info": {
            "occurrence_time": "09:30:00",
            "estimated_repair_time": 60
        }
    }

    try:
        result = run_workflow(raw_input, dry_run=True)

        print(f"Scene Type: {result.scene_spec.scene_type}")
        print(f"Task ID: {result.task_plan.task_id}")
        print(f"Subtasks 数量: {len(result.task_plan.subtasks)}")
        print(f"Debug Trace Keys: {list(result.debug_trace.keys())}")
        print(f"Success: {result.success}")
        print(f"Message: {result.message}")

        assert result.success, "工作流执行失败"
        assert result.scene_spec.scene_type == "sudden_failure", "场景类型不匹配"
        assert len(result.task_plan.subtasks) > 0, "没有子任务"

        print("\n[PASS] sudden_failure 测试通过")
        return True

    except Exception as e:
        print(f"\n[FAIL] sudden_failure 测试失败: {str(e)}")
        traceback.print_exc()
        return False


def test_section_interrupt():
    """测试区间中断场景"""
    print("\n" + "=" * 60)
    print("测试: section_interrupt")
    print("=" * 60)

    raw_input = {
        "scene_type": "section_interrupt",
        "scene_id": "test_si_001",
        "description": "区间中断测试场景",
        "location": {
            "section": "TJG-BJS",
            "interrupt_reason": "signal_failure"
        },
        "time_info": {
            "start_time": "10:00:00",
            "estimated_duration": 120
        }
    }

    try:
        result = run_workflow(raw_input, dry_run=True)

        print(f"Scene Type: {result.scene_spec.scene_type}")
        print(f"Task ID: {result.task_plan.task_id}")
        print(f"Subtasks 数量: {len(result.task_plan.subtasks)}")
        print(f"Debug Trace Keys: {list(result.debug_trace.keys())}")
        print(f"Success: {result.success}")
        print(f"Message: {result.message}")

        assert result.success, "工作流执行失败"
        assert result.scene_spec.scene_type == "section_interrupt", "场景类型不匹配"
        assert len(result.task_plan.subtasks) > 0, "没有子任务"

        print("\n[PASS] section_interrupt 测试通过")
        return True

    except Exception as e:
        print(f"\n[FAIL] section_interrupt 测试失败: {str(e)}")
        traceback.print_exc()
        return False


def main():
    """主函数"""
    print("=" * 60)
    print("Phase A 验证开始")
    print("=" * 60)

    results = []

    # 测试三个场景
    results.append(("temporary_speed_limit", test_temporary_speed_limit()))
    results.append(("sudden_failure", test_sudden_failure()))
    results.append(("section_interrupt", test_section_interrupt()))

    # 汇总结果
    print("\n" + "=" * 60)
    print("验证结果汇总")
    print("=" * 60)

    all_passed = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False

    print("=" * 60)

    if all_passed:
        print("\n[SUCCESS] 所有场景测试通过!")
        print("Phase A 验证完成，可以进入 Phase B")
        return 0
    else:
        print("\n[FAILED] 部分场景测试失败")
        print("请检查错误信息并修复问题")
        return 1


if __name__ == "__main__":
    sys.exit(main())