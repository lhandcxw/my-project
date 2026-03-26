#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试修复后的parse_user_prompt
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.data_loader import get_trains_pydantic, use_real_data
use_real_data(True)
trains = get_trains_pydantic()[:50]

# 模拟parse_user_prompt函数
def parse_user_prompt_test(prompt: str, trains) -> dict:
    import re

    prompt_lower = prompt.lower()

    # 检测场景类型
    if '限速' in prompt:
        scenario_type = 'temporary_speed_limit'
    elif '故障' in prompt or '设备故障' in prompt:
        scenario_type = 'sudden_failure'
    else:
        scenario_type = 'temporary_speed_limit'

    # 提取列车和延误信息
    train_pattern = r'([GDCTKZ]\d+)'
    delay_pattern = r'(\d+)\s*分钟'

    train_ids = re.findall(train_pattern, prompt)
    delays = re.findall(delay_pattern, prompt)

    # 如果没有提取到，使用默认
    if not train_ids:
        train_ids = ['G1001']
    if not delays:
        delays = ['600']

    # 提取车站信息
    station_name_to_code = {
        "北京西": "BJX", "bjx": "BJX",
        "杜家坎线路所": "DJK", "djk": "DJK",
        "涿州东": "ZBD", "zbd": "ZBD",
        "高碑店东": "GBD", "gbd": "GBD",
        "徐水东": "XSD", "xsd": "XSD",
        "保定东": "BDD", "bdd": "BDD",
        "定州东": "DZD", "dzd": "DZD",
        "正定机场": "ZDJ", "zdj": "ZDJ",
        "石家庄": "SJP", "sjp": "SJP",
        "高邑西": "GYX", "gyx": "GYX",
        "邢台东": "XTD", "xtd": "XTD",
        "邯郸东": "HDD", "hdd": "HDD",
        "安阳东": "AYD", "ayd": "AYD"
    }

    # 尝试从输入中提取车站
    detected_station_code = None
    for name, code in station_name_to_code.items():
        if name in prompt:
            detected_station_code = code
            break

    # 如果没有检测到车站，使用默认第一个车站
    if detected_station_code is None:
        detected_station_code = "BJX"

    # 构建DelayInjection
    injected_delays = []
    for i, train_id in enumerate(train_ids):
        delay_seconds = int(delays[i]) * 60 if i < len(delays) else 600

        # 验证列车是否停靠在选定的车站
        train = None
        for t in trains:
            if t.train_id == train_id:
                train = t
                break

        actual_station_code = detected_station_code
        if train:
            train_stations = [stop.station_code for stop in train.schedule.stops]
            if detected_station_code not in train_stations:
                actual_station_code = train.schedule.stops[0].station_code
                print(f"警告: 列车 {train_id} 不停靠在 {detected_station_code}，使用 {actual_station_code} 作为延误车站")

        injected_delays.append({
            "train_id": train_id,
            "location": {"location_type": "station", "station_code": actual_station_code},
            "initial_delay_seconds": delay_seconds,
            "timestamp": "2024-01-15T10:00:00Z"
        })

    return {
        "scenario_type": scenario_type,
        "injected_delays": injected_delays,
        "affected_trains": train_ids
    }

# 测试用例
test_cases = [
    "G1215在北京西延误10分钟",
    "G1215在徐水东延误15分钟",
    "G1001和G1003在保定东延误10分钟和20分钟",
    "G1005在石家庄发生设备故障"
]

print("=" * 60)
print("测试parse_user_prompt修复")
print("=" * 60)

for i, test_prompt in enumerate(test_cases):
    print(f"\n测试用例 {i+1}: {test_prompt}")
    result = parse_user_prompt_test(test_prompt, trains)
    print(f"  场景类型: {result['scenario_type']}")
    print(f"  影响列车: {result['affected_trains']}")
    print(f"  延误注入:")
    for delay in result['injected_delays']:
        print(f"    - {delay['train_id']} 在 {delay['location']['station_code']} 延误 {delay['initial_delay_seconds']}秒")
