#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试调度流程
验证MIP求解器和运行图生成是否正常工作
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.data_models import DelayInjection, InjectedDelay, DelayLocation
from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data, get_train_ids
from solver.mip_scheduler import MIPScheduler
from railway_agent.qwen_agent import create_qwen_agent

def test_mip_solver():
    """测试MIP求解器"""
    print("=" * 60)
    print("测试MIP求解器")
    print("=" * 60)

    # 加载数据
    print("\n1. 加载数据...")
    use_real_data(True)
    trains = get_trains_pydantic()[:50]  # 限制为50列列车
    stations = get_stations_pydantic()

    print(f"   ✓ 加载了 {len(trains)} 列列车")
    print(f"   ✓ 加载了 {len(stations)} 个车站")

    # 创建调度器
    print("\n2. 创建MIP调度器...")
    scheduler = MIPScheduler(trains, stations)
    print("   ✓ MIP调度器创建成功")

    # 创建延误注入
    print("\n3. 创建延误注入...")
    delay_injection = DelayInjection.create_sudden_failure(
        scenario_id="TEST_001",
        train_id=trains[0].train_id,
        delay_seconds=600,  # 10分钟延误
        station_code=stations[0].station_code,
        failure_type="vehicle_breakdown",
        repair_time=60
    )
    print(f"   ✓ 延误注入: {trains[0].train_id} 在 {stations[0].station_code} 延误 10 分钟")
    print(f"   ✓ 延误注入对象: {delay_injection}")

    # 求解
    print("\n4. 执行MIP求解...")
    result = scheduler.solve(delay_injection, objective="min_max_delay")

    if result.success:
        print(f"   ✓ 求解成功!")
        print(f"   ✓ 计算时间: {result.computation_time:.2f}秒")
        print(f"   ✓ 最大延误: {result.delay_statistics.get('max_delay_seconds')}秒")
        print(f"   ✓ 平均延误: {result.delay_statistics.get('avg_delay_seconds'):.2f}秒")
        print(f"   ✓ 优化后时刻表包含 {len(result.optimized_schedule)} 列列车")

        # 检查优化后的时刻表
        print(f"\n5. 检查优化后的时刻表...")
        for train_id in list(result.optimized_schedule.keys())[:3]:  # 只显示前3列
            schedule = result.optimized_schedule[train_id]
            print(f"   列车 {train_id}:")
            for stop in schedule[:2]:  # 只显示前2个站
                delay = stop.get('delay_seconds', 0)
                if delay > 0:
                    print(f"      {stop['station_code']}: 延误 {delay}秒")

        return True
    else:
        print(f"   ✗ 求解失败: {result.message}")
        return False

def test_agent():
    """测试Agent"""
    print("\n" + "=" * 60)
    print("测试Agent")
    print("=" * 60)

    try:
        # 加载数据
        print("\n1. 加载数据...")
        use_real_data(True)
        trains = get_trains_pydantic()[:20]
        stations = get_stations_pydantic()

        # 创建调度器
        print("\n2. 创建调度器...")
        scheduler = MIPScheduler(trains, stations)

        # 创建Agent
        print("\n3. 创建Agent...")
        agent = create_qwen_agent("/data/wls/test-agent/Qwen3.5-4B", trains, stations)

        if agent is None:
            print("   ✗ Agent创建失败（可能是模型未加载）")
            print("   提示: 使用规则引擎模式")
            return False

        print("   ✓ Agent创建成功")

        # 创建延误注入
        print("\n4. 创建测试场景...")
        delay_injection = {
            "scenario_type": "sudden_failure",
            "scenario_id": "TEST_AGENT_001",
            "injected_delays": [{
                "train_id": trains[0].train_id,
                "location": {"location_type": "station", "station_code": stations[0].station_code},
                "initial_delay_seconds": 600,
                "timestamp": "2024-01-15T10:00:00Z"
            }],
            "affected_trains": [trains[0].train_id],
            "scenario_params": {
                "failure_type": "vehicle_breakdown",
                "estimated_repair_time": 60
            }
        }
        print(f"   ✓ 测试场景: {trains[0].train_id} 延误 10 分钟")

        # 执行分析
        print("\n5. 执行Agent分析...")
        result = agent.analyze(delay_injection)

        if result.success:
            print("   ✓ Agent分析成功!")
            print(f"   ✓ 识别场景: {result.recognized_scenario}")
            print(f"   ✓ 选择技能: {result.selected_skill}")

            if result.dispatch_result:
                dispatch = result.dispatch_result
                print(f"   ✓ 调度结果:")
                print(f"      - 消息: {dispatch.message}")
                print(f"      - 计算时间: {dispatch.computation_time:.2f}秒")
                print(f"      - 优化后时刻表包含 {len(dispatch.optimized_schedule)} 列列车")

                if dispatch.optimized_schedule:
                    print(f"\n6. 检查优化后的时刻表...")
                    for train_id in list(dispatch.optimized_schedule.keys())[:2]:
                        schedule = dispatch.optimized_schedule[train_id]
                        max_delay = max([s.get('delay_seconds', 0) for s in schedule])
                        if max_delay > 0:
                            print(f"   列车 {train_id}: 最大延误 {max_delay}秒")

            return True
        else:
            print(f"   ✗ Agent分析失败: {result.error_message}")
            return False

    except Exception as e:
        print(f"   ✗ Agent测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """主函数"""
    print("\n" + "=" * 60)
    print("调度系统测试")
    print("=" * 60)

    # 测试MIP求解器
    mip_success = test_mip_solver()

    # 测试Agent
    # agent_success = test_agent()  # 暂时注释，避免模型加载时间过长

    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    print(f"MIP求解器: {'✓ 通过' if mip_success else '✗ 失败'}")
    # print(f"Agent: {'✓ 通过' if agent_success else '✗ 失败'}")
    print("=" * 60)

    return 0 if mip_success else 1

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
        sys.exit(0)
