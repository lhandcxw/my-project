#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试修复后的MIP求解器
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.data_models import DelayInjection
from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
from solver.mip_scheduler import MIPScheduler

def main():
    """主函数"""
    print("=" * 60)
    print("测试修复后的MIP求解器")
    print("=" * 60)

    # 加载数据
    use_real_data(True)
    trains = get_trains_pydantic()[:10]  # 只用10列列车测试
    stations = get_stations_pydantic()

    print(f"\n1. 加载数据:")
    print(f"   {len(trains)} 列列车")

    # 创建调度器
    print(f"\n2. 创建调度器...")
    scheduler = MIPScheduler(trains, stations)

    # 创建延误注入 - 使用列车的第一个停靠站
    print(f"\n3. 创建延误注入:")
    test_train = trains[0]
    first_station = test_train.schedule.stops[0].station_code

    print(f"   测试列车: {test_train.train_id}")
    print(f"   列车停靠站: {[stop.station_code for stop in test_train.schedule.stops[:3]]}")
    print(f"   选择延误车站: {first_station} (第一个停靠站)")

    delay_injection = DelayInjection.create_sudden_failure(
        scenario_id="TEST_001",
        train_id=test_train.train_id,
        delay_seconds=600,  # 10分钟延误
        station_code=first_station,
        failure_type="vehicle_breakdown",
        repair_time=60
    )

    print(f"\n4. 延误注入详情:")
    print(f"   train_id: {delay_injection.injected_delays[0].train_id}")
    print(f"   station_code: {delay_injection.injected_delays[0].location.station_code}")
    print(f"   initial_delay_seconds: {delay_injection.injected_delays[0].initial_delay_seconds}")

    # 求解
    print(f"\n5. 执行MIP求解...")
    result = scheduler.solve(delay_injection, objective="min_max_delay")

    print(f"\n6. 求解结果:")
    print(f"   成功: {result.success}")
    print(f"   计算时间: {result.computation_time:.2f}秒")
    print(f"   消息: {result.message}")

    if result.success:
        print(f"\n7. 延误统计:")
        stats = result.delay_statistics
        print(f"   最大延误: {stats.get('max_delay_seconds')}秒 ({stats.get('max_delay_seconds', 0)/60:.1f}分钟)")
        print(f"   平均延误: {stats.get('avg_delay_seconds'):.2f}秒")
        print(f"   总延误: {stats.get('total_delay_seconds')}秒 ({stats.get('total_delay_seconds', 0)/60:.1f}分钟)")

        print(f"\n8. 优化后的时刻表:")
        for train_id, schedule in result.optimized_schedule.items():
            max_delay = max([s.get('delay_seconds', 0) for s in schedule])
            if max_delay > 0:
                print(f"   列车 {train_id}: 最大延误 {max_delay}秒 ({max_delay/60:.1f}分钟)")
                for stop in schedule[:3]:  # 只显示前3个站
                    delay = stop.get('delay_seconds', 0)
                    if delay > 0:
                        print(f"      {stop['station_code']}: 计划{stop['original_arrival']} -> 实际{stop['arrival_time']}，延误{delay}秒")

        if stats.get('max_delay_seconds', 0) > 0:
            print("\n✓ 成功！MIP求解器正确应用了延误约束。")
            return 0
        else:
            print("\n✗ 失败：仍然没有检测到延误。")
            return 1
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
