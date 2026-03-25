# -*- coding: utf-8 -*-
"""
测试 MIP 调度器的完整功能
"""
import sys
sys.path.insert(0, '.')

from models.data_loader import use_real_data, get_trains_pydantic, get_stations_pydantic, load_scenarios
from solver.mip_scheduler import MIPScheduler
from models.data_models import DelayInjection, InjectedDelay, DelayLocation, ScenarioType

# 使用真实数据
use_real_data(True)

# 加载数据
trains = get_trains_pydantic()[:20]  # 限制数量以保证MIP可行
stations = get_stations_pydantic()

print(f"加载了 {len(trains)} 趟列车, {len(stations)} 个车站")

# 创建调度器
scheduler = MIPScheduler(trains, stations)

print("\n=== 测试1: 无延误情况 ===")
# 创建一个没有延误的调度场景
no_delay_injection = DelayInjection(
    scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
    scenario_id="TEST_NO_DELAY",
    injected_delays=[],
    affected_trains=[],
    scenario_params={}
)

# 只用前5趟列车测试
scheduler.trains = trains[:5]
result = scheduler.solve(no_delay_injection)
print(f"求解成功: {result.success}")
print(f"消息: {result.message}")
if result.success:
    print(f"最大延误: {result.delay_statistics.get('max_delay_seconds')}秒")
    print(f"平均延误: {result.delay_statistics.get('avg_delay_seconds')}秒")

print("\n=== 测试2: 单列晚点 ===")
# 创建单列晚点场景
single_delay = DelayInjection(
    scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
    scenario_id="TEST_SINGLE",
    injected_delays=[
        InjectedDelay(
            train_id=trains[0].train_id,
            location=DelayLocation(location_type="station", station_code=trains[0].schedule.stops[0].station_code),
            initial_delay_seconds=300,  # 5分钟延误
            timestamp="2024-01-15T10:00:00"
        )
    ],
    affected_trains=[trains[0].train_id],
    scenario_params={}
)

result = scheduler.solve(single_delay)
print(f"求解成功: {result.success}")
print(f"消息: {result.message}")
if result.success:
    print(f"最大延误: {result.delay_statistics.get('max_delay_seconds')}秒")
    print(f"平均延误: {result.delay_statistics.get('avg_delay_seconds')}秒")
    # 显示优化后的时刻表
    for train_id, schedule in result.optimized_schedule.items():
        print(f"\n列车 {train_id}:")
        for stop in schedule[:3]:  # 只显示前3站
            print(f"  {stop['station_code']}: 到达 {stop['arrival_time']}, 发车 {stop['departure_time']}, 延误 {stop['delay_seconds']}秒")

print("\n=== 测试3: 多列晚点（传播） ===")
# 创建多列晚点场景，测试延误传播
multi_delay = DelayInjection(
    scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
    scenario_id="TEST_MULTI",
    injected_delays=[
        InjectedDelay(
            train_id=trains[0].train_id,
            location=DelayLocation(location_type="station", station_code=trains[0].schedule.stops[0].station_code),
            initial_delay_seconds=600,  # 10分钟延误
            timestamp="2024-01-15T10:00:00"
        )
    ],
    affected_trains=[trains[0].train_id, trains[1].train_id],
    scenario_params={}
)

result = scheduler.solve(multi_delay)
print(f"求解成功: {result.success}")
print(f"消息: {result.message}")
if result.success:
    print(f"最大延误: {result.delay_statistics.get('max_delay_seconds')}秒")
    print(f"平均延误: {result.delay_statistics.get('avg_delay_seconds')}秒")
    # 显示优化后的时刻表
    for train_id in [trains[0].train_id, trains[1].train_id]:
        if train_id in result.optimized_schedule:
            schedule = result.optimized_schedule[train_id]
            print(f"\n列车 {train_id}:")
            for stop in schedule[:3]:
                print(f"  {stop['station_code']}: 到达 {stop['arrival_time']}, 发车 {stop['departure_time']}, 延误 {stop['delay_seconds']}秒")

print("\n=== 测试4: 使用真实场景数据 ===")
scenarios = load_scenarios("temporary_speed_limit")
if scenarios:
    scenario = scenarios[0]
    print(f"使用场景: {scenario['scenario_id']}")
    print(f"场景名称: {scenario['scenario_name']}")
    
    # 将场景转换为 DelayInjection
    injected_delays = []
    for d in scenario.get('injected_delays', []):
        injected_delays.append(InjectedDelay(
            train_id=d['train_id'],
            location=DelayLocation(**d['location']),
            initial_delay_seconds=d['initial_delay_seconds'],
            timestamp=d['timestamp']
        ))
    
    delay_injection = DelayInjection(
        scenario_type=ScenarioType(scenario['scenario_type']),
        scenario_id=scenario['scenario_id'],
        injected_delays=injected_delays,
        affected_trains=scenario.get('affected_trains', []),
        scenario_params=scenario.get('scenario_params', {})
    )
    
    result = scheduler.solve(delay_injection)
    print(f"求解成功: {result.success}")
    print(f"消息: {result.message}")
    if result.success:
        print(f"最大延误: {result.delay_statistics.get('max_delay_seconds')}秒")
        print(f"平均延误: {result.delay_statistics.get('avg_delay_seconds')}秒")

print("\n=== 测试完成 ===")
