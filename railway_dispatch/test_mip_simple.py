#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简单测试MIP求解器
验证MIP求解器是否正常工作
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.data_models import DelayInjection, InjectedDelay, DelayLocation
from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
from solver.mip_scheduler import MIPScheduler

def main():
    """主函数"""
    print("=" * 60)
    print("MIP求解器测试")
    print("=" * 60)

    # 加载数据
    print("\n1. 加载数据...")
    use_real_data(True)
    trains = get_trains_pydantic()[:10]  # 只用10列列车测试
    stations = get_stations_pydantic()

    print(f"   ✓ 加载了 {len(trains)} 列列车")
    print(f"   ✓ 加载了 {len(stations)} 个车站")
    print(f"   ✓ 测试列车: {[t.train_id for t in trains[:3]]}")

    # 创建调度器
    print("\n2. 创建MIP调度器...")
    scheduler = MIPScheduler(trains, stations)
    print("   ✓ MIP调度器创建成功")

    # 创建延误注入
    print("\n3. 创建延误注入...")
    test_train = trains[0]
    test_station = stations[0]

    delay_injection = DelayInjection.create_sudden_failure(
        scenario_id="TEST_001",
        train_id=test_train.train_id,
        delay_seconds=600,  # 10分钟延误
        station_code=test_station.station_code,
        failure_type="vehicle_breakdown",
        repair_time=60
    )
    print(f"   ✓ 延误注入: {test_train.train_id} 在 {test_station.station_code} 延误 10 分钟")

    # 求解
    print("\n4. 执行MIP求解...")
    print("   (这可能需要一些时间...)")
    result = scheduler.solve(delay_injection, objective="min_max_delay")

    print(f"\n5. 求解结果:")
    print(f"   成功: {result.success}")
    print(f"   计算时间: {result.computation_time:.2f}秒")
    print(f"   消息: {result.message}")

    if result.success:
        print(f"\n6. 延误统计:")
        stats = result.delay_statistics
        print(f"   最大延误: {stats.get('max_delay_seconds')}秒 ({stats.get('max_delay_seconds', 0)/60:.1f}分钟)")
        print(f"   平均延误: {stats.get('avg_delay_seconds'):.2f}秒")
        print(f"   总延误: {stats.get('total_delay_seconds')}秒 ({stats.get('total_delay_seconds', 0)/60:.1f}分钟)")
        print(f"   影响列车数: {stats.get('affected_trains_count')}")

        print(f"\n7. 优化后的时刻表:")
        print(f"   包含 {len(result.optimized_schedule)} 列列车")

        # 检查每个列车是否有延误
        has_delay = 0
        for train_id, schedule in result.optimized_schedule.items():
            max_delay = max([s.get('delay_seconds', 0) for s in schedule])
            if max_delay > 0:
                has_delay += 1
                if has_delay <= 3:  # 只显示前3列有延误的列车
                    print(f"\n   列车 {train_id}:")
                    for stop in schedule:
                        delay = stop.get('delay_seconds', 0)
                        if delay > 0:
                            print(f"      {stop['station_code']}: 延误 {delay}秒 ({delay/60:.1f}分钟)")

        print(f"\n   共有 {has_delay} 列列车有延误")

        if has_delay > 0:
            print("\n✓ MIP求解器工作正常！优化后的时刻表包含了延误调整。")
        else:
            print("\n✗ 警告：没有检测到延误！MIP可能没有正确应用延误约束。")

        return 0
    else:
        print(f"\n✗ 求解失败: {result.message}")
        return 1

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
