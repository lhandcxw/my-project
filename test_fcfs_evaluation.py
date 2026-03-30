# -*- coding: utf-8 -*-
"""
完整流程演示：FCFS调度器 + 评估系统
展示整个项目的完整流程：
1. 加载真实数据
2. 创建延误场景
3. 使用FCFS调度器进行调度
4. 使用评估系统评估结果
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'railway_dispatch'))

from railway_dispatch.models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
from railway_dispatch.models.data_models import InjectedDelay, DelayLocation, ScenarioType, DelayInjection
from railway_dispatch.solver.fcfs_scheduler import create_fcfs_scheduler, SolveResult
from railway_dispatch.evaluation.evaluator import Evaluator


def create_delay_injection_scenario():
    """
    创建延误注入场景 - 真实延误传播场景

    场景设计说明：
    1. 选择G1563在保定东延误20分钟（发车时间18:19）
    2. 选择紧随其后的G556等列车，这些列车在保定东的原始发车时间接近18:19
    3. 由于追踪间隔（3分钟），后续列车会被延误传播

    Returns:
        DelayInjection: 延误注入对象
    """
    return DelayInjection(
        scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
        scenario_id="FCFS_EVAL_TEST_001",
        injected_delays=[
            # G1563在保定东延误20分钟（造成后续多列列车延误传播）
            InjectedDelay(
                train_id="G1563",
                location=DelayLocation(
                    location_type="station",
                    station_code="BDD"
                ),
                initial_delay_seconds=1200,  # 20分钟 = 1200秒
                timestamp="2024-01-15T10:00:00Z"
            )
        ],
        affected_trains=["G1563"]  # 初始受影响列车，实际传播会更多
    )


def get_original_schedule_dict(trains):
    """
    获取原始时刻表字典（用于评估）

    Args:
        trains: 列车列表

    Returns:
        Dict: 原始时刻表字典
    """
    original_schedule = {}
    for train in trains:
        stops = []
        for stop in train.schedule.stops:
            stops.append({
                "station_code": stop.station_code,
                "station_name": stop.station_name,
                "arrival_time": stop.arrival_time,
                "departure_time": stop.departure_time,
                "original_arrival": stop.arrival_time,
                "original_departure": stop.departure_time,
                "delay_seconds": 0
            })
        original_schedule[train.train_id] = stops
    return original_schedule


def print_schedule_summary(solve_result: SolveResult, train_id: str = None):
    """
    打印调度结果摘要

    Args:
        solve_result: 调度结果
        train_id: 指定打印的列车ID（可选）
    """
    print("\n" + "=" * 80)
    print("调度结果摘要")
    print("=" * 80)

    stats = solve_result.delay_statistics
    print(f"\n【总体延误统计】")
    print(f"  最大延误时间: {stats['max_delay_seconds']} 秒 ({stats['max_delay_seconds']/60:.1f} 分钟)")
    print(f"  平均延误时间: {stats['avg_delay_seconds']:.2f} 秒 ({stats['avg_delay_seconds']/60:.2f} 分钟)")
    print(f"  总延误时间: {stats['total_delay_seconds']} 秒 ({stats['total_delay_seconds']/60:.1f} 分钟)")
    print(f"  受影响列车数: {stats['affected_trains_count']}")
    print(f"  计算时间: {solve_result.computation_time:.4f} 秒")

    # 如果指定了列车，显示详细时刻表
    if train_id and train_id in solve_result.optimized_schedule:
        print(f"\n【列车 {train_id} 详细时刻表】")
        print(f"  {'车站':<12} {'原始到达':<12} {'原始发车':<12} {'调整后到达':<12} {'调整后发车':<12} {'延误(秒)':<10}")
        print(f"  {'-'*80}")

        for stop in solve_result.optimized_schedule[train_id]:
            delay_str = f"{stop['delay_seconds']}" if stop['delay_seconds'] > 0 else "-"
            print(f"  {stop['station_name']:<12} {stop['original_arrival']:<12} {stop['original_departure']:<12} "
                  f"{stop['arrival_time']:<12} {stop['departure_time']:<12} {delay_str:<10}")

    # 显示所有有延误的列车
    print(f"\n【有延误的列车列表】")
    delayed_trains = []
    for train_id, stops in solve_result.optimized_schedule.items():
        max_train_delay = max(stop.get('delay_seconds', 0) for stop in stops)
        if max_train_delay > 0:
            delayed_trains.append((train_id, max_train_delay))

    if delayed_trains:
        delayed_trains.sort(key=lambda x: x[1], reverse=True)
        print(f"  {'列车ID':<12} {'最大延误(秒)':<15} {'最大延误(分钟)':<15}")
        print(f"  {'-'*50}")
        for train_id, delay_sec in delayed_trains:
            print(f"  {train_id:<12} {delay_sec:<15} {delay_sec/60:<15.1f}")
    else:
        print("  无")


def run_complete_evaluation():
    """
    运行完整的评估流程
    """
    print("\n" + "=" * 80)
    print("铁路调度系统 - FCFS调度器 + 评估系统 完整流程演示")
    print("=" * 80)

    # Step 1: 加载数据
    print("\n【Step 1】加载真实数据...")
    use_real_data(True)
    trains = get_trains_pydantic()[:30]  # 使用前30列列车
    stations = get_stations_pydantic()
    print(f"  已加载 {len(trains)} 列列车")
    print(f"  已加载 {len(stations)} 个车站")

    # Step 2: 创建延误场景
    print("\n【Step 2】创建延误场景...")
    delay_injection = create_delay_injection_scenario()
    print(f"  场景类型: {delay_injection.scenario_type}")
    print(f"  注入延误数: {len(delay_injection.injected_delays)}")
    for delay in delay_injection.injected_delays:
        print(f"    - 列车 {delay.train_id} 在 {delay.location.station_code} 延误 {delay.initial_delay_seconds/60:.1f} 分钟")

    # Step 3: 使用FCFS调度器进行调度
    print("\n【Step 3】使用FCFS调度器进行调度...")
    scheduler = create_fcfs_scheduler(trains, stations)
    solve_result = scheduler.solve(delay_injection)
    print(f"  {solve_result.message}")

    # Step 4: 打印调度结果摘要
    affected_train_id = delay_injection.injected_delays[0].train_id
    print_schedule_summary(solve_result, affected_train_id)

    # Step 5: 准备评估数据
    print("\n【Step 4】准备评估数据...")
    original_schedule = get_original_schedule_dict(trains)
    proposed_schedule = solve_result.optimized_schedule

    # 将delay_injection转换为字典格式
    delay_injection_dict = {
        "scenario_type": delay_injection.scenario_type,
        "scenario_id": delay_injection.scenario_id,
        "injected_delays": [
            {
                "train_id": d.train_id,
                "location": {
                    "location_type": d.location.location_type,
                    "station_code": d.location.station_code
                },
                "initial_delay_seconds": d.initial_delay_seconds,
                "timestamp": d.timestamp
            }
            for d in delay_injection.injected_delays
        ],
        "affected_trains": delay_injection.affected_trains
    }

    # Step 6: 使用评估系统进行评估
    print("\n【Step 5】使用评估系统进行评估...")
    evaluator = Evaluator(baseline_strategy="no_adjustment")
    evaluation_result = evaluator.evaluate(
        proposed_schedule,
        original_schedule,
        delay_injection_dict
    )

    # Step 7: 输出评估报告
    print(evaluator.comparator.format_result(evaluation_result))

    return solve_result, evaluation_result


def compare_fcfs_vs_mip():
    """
    对比FCFS和MIP两种调度器
    """
    print("\n" + "=" * 80)
    print("对比演示：FCFS vs MIP 调度器")
    print("=" * 80)

    # 加载数据
    use_real_data(True)
    trains = get_trains_pydantic()[:30]
    stations = get_stations_pydantic()

    # 创建延误场景
    delay_injection = DelayInjection(
        scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
        scenario_id="COMPARE_TEST",
        injected_delays=[
            InjectedDelay(
                train_id="G1563",
                location=DelayLocation(location_type="station", station_code="BDD"),
                initial_delay_seconds=1200,
                timestamp="2024-01-15T10:00:00Z"
            )
        ],
        affected_trains=["G1563"]
    )

    # FCFS调度
    print("\n【FCFS调度器】")
    fcfs_scheduler = create_fcfs_scheduler(trains, stations)
    fcfs_result = fcfs_scheduler.solve(delay_injection)
    print(f"  最大延误: {fcfs_result.delay_statistics['max_delay_seconds']//60} 分钟")
    print(f"  平均延误: {fcfs_result.delay_statistics['avg_delay_seconds']/60:.2f} 分钟")
    print(f"  计算时间: {fcfs_result.computation_time:.4f} 秒")

    # MIP调度
    print("\n【MIP调度器】")
    from solver.mip_scheduler import create_scheduler
    mip_scheduler = create_scheduler(trains, stations)
    mip_result = mip_scheduler.solve(delay_injection)
    print(f"  最大延误: {mip_result.delay_statistics['max_delay_seconds']//60} 分钟")
    print(f"  平均延误: {mip_result.delay_statistics['avg_delay_seconds']/60:.2f} 分钟")
    print(f"  计算时间: {mip_result.computation_time:.4f} 秒")

    # 对比
    print("\n【对比分析】")
    print(f"  最大延误:")
    print(f"    FCFS: {fcfs_result.delay_statistics['max_delay_seconds']} 秒")
    print(f"    MIP:  {mip_result.delay_statistics['max_delay_seconds']} 秒")
    print(f"    差异: {fcfs_result.delay_statistics['max_delay_seconds'] - mip_result.delay_statistics['max_delay_seconds']} 秒")

    print(f"  平均延误:")
    print(f"    FCFS: {fcfs_result.delay_statistics['avg_delay_seconds']:.2f} 秒")
    print(f"    MIP:  {mip_result.delay_statistics['avg_delay_seconds']:.2f} 秒")
    print(f"    差异: {fcfs_result.delay_statistics['avg_delay_seconds'] - mip_result.delay_statistics['avg_delay_seconds']:.2f} 秒")

    print(f"  计算时间:")
    print(f"    FCFS: {fcfs_result.computation_time:.4f} 秒")
    print(f"    MIP:  {mip_result.computation_time:.4f} 秒")
    print(f"    MIP比FCFS慢 {mip_result.computation_time/fcfs_result.computation_time:.1f} 倍")


if __name__ == "__main__":
    # 运行完整评估流程
    solve_result, evaluation_result = run_complete_evaluation()

    print("\n" + "=" * 80)
    print("演示完成！")
    print("=" * 80)
    print("\n您已经成功完成整个项目的流程：")
    print("  1. ✓ 加载真实数据")
    print("  2. ✓ 创建延误场景")
    print("  3. ✓ 使用FCFS调度器进行调度")
    print("  4. ✓ 使用评估系统评估结果")
    print("\n现在可以使用以下代码在其他地方调用：")
    print("  - FCFS调度器: from solver.fcfs_scheduler import create_fcfs_scheduler")
    print("  - 评估系统: from evaluation.evaluator import Evaluator")
