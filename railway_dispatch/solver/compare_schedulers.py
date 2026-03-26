# -*- coding: utf-8 -*-
"""
调度器对比演示：FCFS vs MIP
展示两种调度算法的性能和效果对比
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
from models.data_models import InjectedDelay, DelayLocation, ScenarioType, DelayInjection
from evaluation.evaluator import Evaluator


def compare_schedulers(delay_seconds=1200, train_id="G1563", station_code="BDD"):
    """
    对比FCFS和MIP两种调度器

    Args:
        delay_seconds: 注入的延误时间（秒）
        train_id: 延误的列车ID
        station_code: 延误发生的车站代码
    """
    print("=" * 80)
    print(f"调度器对比演示：FCFS vs MIP")
    print("=" * 80)

    # 加载数据
    print("\n【数据加载】")
    use_real_data(True)
    trains = get_trains_pydantic()[:30]
    stations = get_stations_pydantic()
    print(f"  列车数量: {len(trains)}")
    print(f"  车站数量: {len(stations)}")

    # 创建延误场景
    print(f"\n【延误场景】")
    print(f"  列车: {train_id}")
    print(f"  车站: {station_code}")
    print(f"  延误时间: {delay_seconds} 秒 ({delay_seconds/60:.1f} 分钟)")

    delay_injection = DelayInjection(
        scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
        scenario_id="COMPARE_TEST",
        injected_delays=[
            InjectedDelay(
                train_id=train_id,
                location=DelayLocation(location_type="station", station_code=station_code),
                initial_delay_seconds=delay_seconds,
                timestamp="2024-01-15T10:00:00Z"
            )
        ],
        affected_trains=[train_id]
    )

    # FCFS调度
    print(f"\n【FCFS调度器结果】")
    from fcfs_scheduler import create_fcfs_scheduler
    fcfs_scheduler = create_fcfs_scheduler(trains, stations)
    fcfs_result = fcfs_scheduler.solve(delay_injection)

    print(f"  求解状态: {fcfs_result.message}")
    print(f"  最大延误: {fcfs_result.delay_statistics['max_delay_seconds']} 秒 ({fcfs_result.delay_statistics['max_delay_seconds']/60:.1f} 分钟)")
    print(f"  平均延误: {fcfs_result.delay_statistics['avg_delay_seconds']:.2f} 秒 ({fcfs_result.delay_statistics['avg_delay_seconds']/60:.2f} 分钟)")
    print(f"  总延误: {fcfs_result.delay_statistics['total_delay_seconds']} 秒 ({fcfs_result.delay_statistics['total_delay_seconds']/60:.1f} 分钟)")
    print(f"  受影响列车: {fcfs_result.delay_statistics['affected_trains_count']}")
    print(f"  计算时间: {fcfs_result.computation_time:.4f} 秒")

    # MIP调度
    print(f"\n【MIP调度器结果】")
    from mip_scheduler import create_scheduler
    mip_scheduler = create_scheduler(trains, stations)
    mip_result = mip_scheduler.solve(delay_injection)

    print(f"  求解状态: {mip_result.message}")
    print(f"  最大延误: {mip_result.delay_statistics['max_delay_seconds']} 秒 ({mip_result.delay_statistics['max_delay_seconds']/60:.1f} 分钟)")
    print(f"  平均延误: {mip_result.delay_statistics['avg_delay_seconds']:.2f} 秒 ({mip_result.delay_statistics['avg_delay_seconds']/60:.2f} 分钟)")
    print(f"  总延误: {mip_result.delay_statistics['total_delay_seconds']} 秒 ({mip_result.delay_statistics['total_delay_seconds']/60:.1f} 分钟)")
    print(f"  受影响列车: {mip_result.delay_statistics['affected_trains_count']}")
    print(f"  计算时间: {mip_result.computation_time:.4f} 秒")

    # 对比分析
    print(f"\n【对比分析】")
    print(f"  最大延误对比:")
    print(f"    FCFS: {fcfs_result.delay_statistics['max_delay_seconds']} 秒")
    print(f"    MIP:  {mip_result.delay_statistics['max_delay_seconds']} 秒")
    max_delay_diff = fcfs_result.delay_statistics['max_delay_seconds'] - mip_result.delay_statistics['max_delay_seconds']
    if max_delay_diff > 0:
        print(f"    MIP改进: {-max_delay_diff} 秒 ({-max_delay_diff/fcfs_result.delay_statistics['max_delay_seconds']*100:.1f}%)")
    elif max_delay_diff < 0:
        print(f"    FCFS改进: {max_delay_diff} 秒 ({-max_delay_diff/mip_result.delay_statistics['max_delay_seconds']*100:.1f}%)")
    else:
        print(f"    两者相同")

    print(f"\n  平均延误对比:")
    print(f"    FCFS: {fcfs_result.delay_statistics['avg_delay_seconds']:.2f} 秒")
    print(f"    MIP:  {mip_result.delay_statistics['avg_delay_seconds']:.2f} 秒")
    avg_delay_diff = fcfs_result.delay_statistics['avg_delay_seconds'] - mip_result.delay_statistics['avg_delay_seconds']
    if avg_delay_diff > 0:
        print(f"    MIP改进: {-avg_delay_diff:.2f} 秒 ({-avg_delay_diff/fcfs_result.delay_statistics['avg_delay_seconds']*100:.1f}%)")
    elif avg_delay_diff < 0:
        print(f"    FCFS改进: {avg_delay_diff:.2f} 秒 ({-avg_delay_diff/mip_result.delay_statistics['avg_delay_seconds']*100:.1f}%)")
    else:
        print(f"    两者相同")

    print(f"\n  计算时间对比:")
    print(f"    FCFS: {fcfs_result.computation_time:.4f} 秒")
    print(f"    MIP:  {mip_result.computation_time:.4f} 秒")
    if mip_result.computation_time > fcfs_result.computation_time:
        speedup = mip_result.computation_time / fcfs_result.computation_time
        print(f"    MIP比FCFS慢 {speedup:.1f} 倍")
        print(f"    FCFS更快速")
    else:
        speedup = fcfs_result.computation_time / mip_result.computation_time
        print(f"    FCFS比MIP慢 {speedup:.1f} 倍")
        print(f"    MIP更快速")

    # 评估两种方案
    print(f"\n【方案评估】")

    # 准备原始时刻表
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

    # 准备延误注入字典
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

    # 评估FCFS方案
    evaluator = Evaluator(baseline_strategy="no_adjustment")
    fcfs_eval = evaluator.evaluate(
        fcfs_result.optimized_schedule,
        original_schedule,
        delay_injection_dict
    )

    # 评估MIP方案
    mip_eval = evaluator.evaluate(
        mip_result.optimized_schedule,
        original_schedule,
        delay_injection_dict
    )

    print(f"\n  FCFS方案 vs 基线:")
    print(f"    最大延误改进: {fcfs_eval.comparison.max_delay_improvement:.1f}%")
    print(f"    平均延误改进: {fcfs_eval.comparison.avg_delay_improvement:.1f}%")
    print(f"    优于基线: {'是' if fcfs_eval.comparison.is_better_than_baseline else '否'}")

    print(f"\n  MIP方案 vs 基线:")
    print(f"    最大延误改进: {mip_eval.comparison.max_delay_improvement:.1f}%")
    print(f"    平均延误改进: {mip_eval.comparison.avg_delay_improvement:.1f}%")
    print(f"    优于基线: {'是' if mip_eval.comparison.is_better_than_baseline else '否'}")

    # 总结
    print(f"\n【总结】")
    print(f"  性能方面:")
    print(f"    - FCFS计算速度快，适合实时调度")
    print(f"    - MIP计算速度慢，但可能找到更优解")

    print(f"\n  效果方面:")
    if fcfs_result.delay_statistics['max_delay_seconds'] <= mip_result.delay_statistics['max_delay_seconds']:
        print(f"    - 本次场景中FCFS与MIP效果相当或更好")
    else:
        print(f"    - 本次场景中MIP效果优于FCFS")

    print(f"\n  推荐使用:")
    print(f"    - 快速响应场景：使用FCFS调度器")
    print(f"    - 优化目标明确：使用MIP调度器")
    print(f"    - 可以混合使用：先用FCFS快速响应，再用MIP优化")

    return {
        'fcfs_result': fcfs_result,
        'mip_result': mip_result,
        'fcfs_eval': fcfs_eval,
        'mip_eval': mip_eval
    }


if __name__ == "__main__":
    # 运行对比演示
    results = compare_schedulers(delay_seconds=1200, train_id="G1563", station_code="BDD")
