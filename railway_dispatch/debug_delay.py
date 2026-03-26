#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调试延误注入
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.data_models import DelayInjection
from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data

def main():
    """主函数"""
    print("=" * 60)
    print("调试延误注入")
    print("=" * 60)

    # 加载数据
    use_real_data(True)
    trains = get_trains_pydantic()[:3]
    stations = get_stations_pydantic()

    print(f"\n列车信息:")
    for train in trains:
        print(f"  {train.train_id}:")
        print(f"    停靠站: {[stop.station_code for stop in train.schedule.stops[:3]]}")

    # 创建延误注入
    print(f"\n创建延误注入...")
    test_train = trains[0]
    test_station = stations[0]

    print(f"  测试列车: {test_train.train_id}")
    print(f"  测试车站: {test_station.station_code}")
    print(f"  延误时间: 600秒 (10分钟)")

    delay_injection = DelayInjection.create_sudden_failure(
        scenario_id="TEST_001",
        train_id=test_train.train_id,
        delay_seconds=600,
        station_code=test_station.station_code,
        failure_type="vehicle_breakdown",
        repair_time=60
    )

    print(f"\n延误注入对象:")
    print(f"  scenario_type: {delay_injection.scenario_type}")
    print(f"  scenario_id: {delay_injection.scenario_id}")
    print(f"  injected_delays: {len(delay_injection.injected_delays)}")

    for injected in delay_injection.injected_delays:
        print(f"\n  延误详情:")
        print(f"    train_id: {injected.train_id}")
        print(f"    location: {injected.location}")
        print(f"    initial_delay_seconds: {injected.initial_delay_seconds}")
        print(f"    timestamp: {injected.timestamp}")

    # 转换为dict
    delay_dict = delay_injection.model_dump()
    print(f"\n转换为dict后的延误注入:")
    print(f"  scenario_type: {delay_dict.get('scenario_type')}")
    print(f"  affected_trains: {delay_dict.get('affected_trains')}")
    print(f"  injected_delays: {len(delay_dict.get('injected_delays', []))}")

    for injected in delay_dict.get('injected_delays', []):
        print(f"\n  延误详情(dict):")
        print(f"    train_id: {injected.get('train_id')}")
        print(f"    location: {injected.get('location')}")
        print(f"    initial_delay_seconds: {injected.get('initial_delay_seconds')}")

    # 检查车站是否在trains的停靠站中
    print(f"\n验证延误应用:")
    train = test_train
    station_code = test_station.station_code

    print(f"  检查 {train.train_id} 是否停靠在 {station_code}:")
    for stop in train.schedule.stops:
        if stop.station_code == station_code:
            print(f"    ✓ 是的，停靠在 {station_code}")
            print(f"    计划到达时间: {stop.arrival_time}")
            print(f"    计划发车时间: {stop.departure_time}")
            print(f"    延误后到达时间应 >= {stop.arrival_time} + 10分钟")
            print(f"    延误后发车时间应 >= {stop.departure_time} + 10分钟")
            break
    else:
        print(f"    ✗ 不停靠在 {station_code}")

if __name__ == "__main__":
    main()
