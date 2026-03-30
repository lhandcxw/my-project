# -*- coding: utf-8 -*-
"""
铁路调度系统 - 数据加载模块
统一读取真实数据(trains.json, stations.json)，所有程序都从这里读取数据

注意：
- data/ 目录包含处理后的真实列车和车站数据
- real_data/ 目录包含原始数据文件
"""

import json
import os
import csv
from typing import List, Dict, Any, Optional
from pathlib import Path

# 数据目录路径
DATA_DIR = Path(__file__).parent.parent / "data"
# 真实数据目录路径 (在项目根目录)
REAL_DATA_DIR = Path(__file__).parent.parent.parent / "real_data"

# 缓存已加载的数据
_cache = {}


def get_data_path(filename: str) -> Path:
    """获取数据文件路径"""
    return DATA_DIR / filename


def load_trains() -> List[Dict[str, Any]]:
    """
    加载列车数据
    Returns:
        List of train data dictionaries
    """
    if "trains" in _cache:
        return _cache["trains"]

    # 优先使用真实数据
    if is_using_real_data():
        return load_real_trains()

    train_file = get_data_path("trains.json")
    if not train_file.exists():
        raise FileNotFoundError(f"列车数据文件不存在: {train_file}")

    with open(train_file, "r", encoding="utf-8") as f:
        trains = json.load(f)

    _cache["trains"] = trains
    return trains


def load_stations() -> List[Dict[str, Any]]:
    """
    加载车站数据
    Returns:
        List of station data dictionaries
    """
    if "stations" in _cache:
        return _cache["stations"]

    # 优先使用真实数据
    if is_using_real_data():
        return load_real_stations()

    station_file = get_data_path("stations.json")
    if not station_file.exists():
        raise FileNotFoundError(f"车站数据文件不存在: {station_file}")

    with open(station_file, "r", encoding="utf-8") as f:
        stations = json.load(f)

    _cache["stations"] = stations
    return stations


def load_real_stations() -> List[Dict[str, Any]]:
    """
    从真实数据文件夹加载车站数据
    Returns:
        List of station data dictionaries
    """
    if "real_stations" in _cache:
        return _cache["real_stations"]

    station_file = REAL_DATA_DIR / "station_alias.json"
    if not station_file.exists():
        raise FileNotFoundError(f"真实车站数据文件不存在: {station_file}")

    with open(station_file, "r", encoding="utf-8") as f:
        content = f.read()
        # 修复中文逗号问题
        content = content.replace('，', ',')
        real_stations = json.loads(content)

    # 标准站码映射
    station_code_map = {
        '北京西': 'BJX',
        '杜家坎线路所': 'DJK',
        '涿州东': 'ZBD',
        '高碑店东': 'GBD',
        '徐水东': 'XSD',
        '保定东': 'BDD',
        '定州东': 'DZD',
        '正定机场': 'ZDJ',
        '石家庄': 'SJP',
        '高邑西': 'GYX',
        '邢台东': 'XTD',
        '邯郸东': 'HDD',
        '安阳东': 'AYD'
    }

    # 转换格式 - 使用简化的车站数据（无platforms等字段）
    stations = []
    for s in real_stations:
        station_name = s["station_name"]
        station_code = station_code_map.get(station_name, station_name[:3])
        track_count = s.get("track_count", 4) or 4

        station = {
            "station_code": station_code,
            "station_name": station_name,
            "track_count": track_count
        }
        stations.append(station)

    _cache["real_stations"] = stations
    return stations


def load_real_trains() -> List[Dict[str, Any]]:
    """
    从真实数据文件夹加载列车数据
    Returns:
        List of train data dictionaries
    """
    if "real_trains" in _cache:
        return _cache["real_trains"]

    # 加载车站数据以获取车站信息
    stations = load_real_stations()
    station_names = [s["station_name"] for s in stations]
    # 创建站名到站码的映射
    station_code_map = {s["station_name"]: s["station_code"] for s in stations}

    # 加载列车ID映射
    train_mapping_file = REAL_DATA_DIR / "train_id_mapping.csv"
    if not train_mapping_file.exists():
        raise FileNotFoundError(f"真实列车ID映射文件不存在: {train_mapping_file}")

    train_no_map = {}
    with open(train_mapping_file, "r", encoding="utf-8") as f:
        content = f.read()
        # 修复可能的BOM问题
        if content.startswith('\ufeff'):
            content = content[1:]
        reader = csv.DictReader(content.splitlines())
        for row in reader:
            if "train_id" in row and "train_no" in row:
                train_no_map[row["train_id"]] = row["train_no"]

    # 加载时刻表 - 尝试多个可能的文件名
    timetable_file = REAL_DATA_DIR / "plan_timetable (2).csv"
    if not timetable_file.exists():
        timetable_file = REAL_DATA_DIR / "plan_timetable.csv"
    if not timetable_file.exists():
        raise FileNotFoundError(f"真实时刻表文件不存在: {timetable_file}")

    trains = []
    with open(timetable_file, "r", encoding="utf-8") as f:
        content = f.read()
        # 修复可能的BOM问题
        if content.startswith('\ufeff'):
            content = content[1:]
        reader = csv.DictReader(content.splitlines())
        for row in reader:
            train_id = row["train_id"]
            train_no = train_no_map.get(train_id, f"G{train_id}")

            # 构建停靠站列表
            stops = []
            for i, station_name in enumerate(station_names, 1):
                station_key = f"station_{i}"
                arrival_key = f"{station_key}_A"
                departure_key = f"{station_key}_D"

                arrival_time = row.get(arrival_key, "").strip()
                departure_time = row.get(departure_key, "").strip()

                if arrival_time or departure_time:
                    stops.append({
                        "station_code": station_code_map.get(station_name, station_name[:3]),
                        "station_name": station_name,
                        "arrival_time": arrival_time if arrival_time else departure_time,
                        "departure_time": departure_time if departure_time else arrival_time
                    })

            if stops:
                trains.append({
                    "train_id": train_no,
                    "train_type": "高速动车组",
                    "schedule": {
                        "stops": stops
                    }
                })

    _cache["real_trains"] = trains
    return trains


def load_real_min_running_time() -> List[int]:
    """
    从真实数据文件夹加载区间最小运行时间
    Returns:
        List of minimum running times in minutes
    """
    if "real_min_running_time" in _cache:
        return _cache["real_min_running_time"]

    min_time_file = REAL_DATA_DIR / "min_running_time_matrix (2).csv"
    if not min_time_file.exists():
        min_time_file = REAL_DATA_DIR / "min_running_time_matrix.csv"
    if not min_time_file.exists():
        raise FileNotFoundError(f"真实最小运行时间文件不存在: {min_time_file}")

    min_times = []
    with open(min_time_file, "r", encoding="utf-8") as f:
        content = f.read()
        # 修复可能的BOM问题
        if content.startswith('\ufeff'):
            content = content[1:]
        reader = csv.reader(content.splitlines())
        next(reader)  # 跳过标题行
        for row in reader:
            if row and row[0]:
                min_times.append(int(row[0]))

    _cache["real_min_running_time"] = min_times
    return min_times


def get_real_data():
    """
    获取所有真实数据
    Returns:
        dict with trains, stations, min_running_time
    """
    return {
        "trains": load_real_trains(),
        "stations": load_real_stations(),
        "min_running_time": load_real_min_running_time()
    }


def use_real_data(enable: bool = True):
    """
    设置是否使用真实数据
    注意：此函数保留用于向后兼容，实际Always使用真实数据
    Args:
        enable: True使用真实数据（当前忽略此参数，始终使用真实数据）
    """
    # 始终使用真实数据
    clear_cache()
    _cache["use_real_data"] = True


def is_using_real_data() -> bool:
    """检查是否正在使用真实数据"""
    return _cache.get("use_real_data", False)


def _convert_scenario_station_names_to_codes(scenarios: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将场景中的车站名称转换为车站编码
    """
    # 获取站名到站码的映射
    stations = load_stations()
    station_name_to_code = {s["station_name"]: s["station_code"] for s in stations}

    # 也添加一些常见的站名映射（处理"XX站"的情况）
    for name, code in list(station_name_to_code.items()):
        station_name_to_code[name + "站"] = code

    converted_scenarios = []
    for scenario in scenarios:
        scenario = scenario.copy()
        if "injected_delays" in scenario:
            for delay in scenario["injected_delays"]:
                if "location" in delay:
                    loc = delay["location"]
                    # 转换 station_code
                    if loc.get("station_code") and loc["station_code"] in station_name_to_code:
                        loc["station_code"] = station_name_to_code[loc["station_code"]]
                    # 转换 section_from
                    if loc.get("section_from") and loc["section_from"] in station_name_to_code:
                        loc["section_from"] = station_name_to_code[loc["section_from"]]
                    # 转换 section_to
                    if loc.get("section_to") and loc["section_to"] in station_name_to_code:
                        loc["section_to"] = station_name_to_code[loc["section_to"]]

        # 转换 scenario_params 中的 affected_section
        if "scenario_params" in scenario:
            params = scenario["scenario_params"]
            if "affected_section" in params:
                section = params["affected_section"]
                if section and " -> " in section:
                    parts = section.split(" -> ")
                    new_parts = []
                    for part in parts:
                        part = part.strip()
                        if part in station_name_to_code:
                            new_parts.append(station_name_to_code[part])
                        else:
                            new_parts.append(part)
                    params["affected_section"] = " -> ".join(new_parts)

        converted_scenarios.append(scenario)

    return converted_scenarios


def load_scenarios(scenario_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    加载场景数据

    Args:
        scenario_type: 可选的场景类型过滤，如 "temporary_speed_limit" 或 "sudden_failure"

    Returns:
        List of scenario data dictionaries
    """
    scenarios_dir = DATA_DIR / "scenarios"

    if not scenarios_dir.exists():
        return []

    all_scenarios = []

    if scenario_type:
        # 加载指定类型
        scenario_file = scenarios_dir / f"{scenario_type}.json"
        if scenario_file.exists():
            with open(scenario_file, "r", encoding="utf-8") as f:
                all_scenarios = json.load(f)
    else:
        # 加载所有场景
        for scenario_file in scenarios_dir.glob("*.json"):
            with open(scenario_file, "r", encoding="utf-8") as f:
                scenarios = json.load(f)
                all_scenarios.extend(scenarios)

    # 转换站名到站码
    all_scenarios = _convert_scenario_station_names_to_codes(all_scenarios)

    return all_scenarios


def load_scenario_by_id(scenario_id: str) -> Optional[Dict[str, Any]]:
    """
    根据场景ID加载场景数据

    Args:
        scenario_id: 场景ID

    Returns:
        Scenario data dictionary or None if not found
    """
    scenarios = load_scenarios()
    for scenario in scenarios:
        if scenario.get("scenario_id") == scenario_id:
            return scenario
    return None


def get_station_names() -> Dict[str, str]:
    """
    获取车站编码到名称的映射

    Returns:
        Dict mapping station_code -> station_name
    """
    stations = load_stations()
    return {s["station_code"]: s["station_name"] for s in stations}


def get_station_codes() -> List[str]:
    """
    获取所有车站编码（按顺序）

    Returns:
        List of station codes
    """
    stations = load_stations()
    return [s["station_code"] for s in stations]


def get_train_ids() -> List[str]:
    """
    获取所有列车ID

    Returns:
        List of train IDs
    """
    trains = load_trains()
    return [t["train_id"] for t in trains]


def clear_cache():
    """清除数据缓存"""
    _cache.clear()


def reload_data():
    """重新加载所有数据"""
    clear_cache()
    load_trains()
    load_stations()
    load_scenarios()


# ============================================
# 便捷函数：创建Pydantic模型
# ============================================

def get_trains_pydantic():
    """
    获取Pydantic模型格式的列车数据
    Returns:
        List of Train objects
    """
    from models.data_models import Train, TrainSchedule, TrainStop

    trains_data = load_trains()
    trains = []

    for t in trains_data:
        stops = []
        for s in t["schedule"]["stops"]:
            # 检查字段是否存在，如果存在则使用实际值
            has_is_stopped = "is_stopped" in s
            has_stop_duration = "stop_duration" in s
            
            if has_is_stopped and has_stop_duration:
                # 两个字段都存在，直接使用
                is_stopped = s["is_stopped"]
                stop_duration = s["stop_duration"]
            elif has_stop_duration:
                # 只有stop_duration，用它来判断
                stop_duration = s["stop_duration"]
                is_stopped = stop_duration > 0
            elif has_is_stopped:
                # 只有is_stopped，计算stop_duration
                is_stopped = s["is_stopped"]
                # 计算停站时间
                arr_parts = s["arrival_time"].split(':')
                dep_parts = s["departure_time"].split(':')
                if len(arr_parts) == 2:
                    arr_sec = int(arr_parts[0]) * 3600 + int(arr_parts[1]) * 60
                else:
                    arr_sec = int(arr_parts[0]) * 3600 + int(arr_parts[1]) * 60 + int(arr_parts[2])
                if len(dep_parts) == 2:
                    dep_sec = int(dep_parts[0]) * 3600 + int(dep_parts[1]) * 60
                else:
                    dep_sec = int(dep_parts[0]) * 3600 + int(dep_parts[1]) * 60 + int(dep_parts[2])
                stop_duration = dep_sec - arr_sec
            else:
                # 都没有，根据到达和发车时间计算
                arr_parts = s["arrival_time"].split(':')
                dep_parts = s["departure_time"].split(':')
                if len(arr_parts) == 2:
                    arr_sec = int(arr_parts[0]) * 3600 + int(arr_parts[1]) * 60
                else:
                    arr_sec = int(arr_parts[0]) * 3600 + int(arr_parts[1]) * 60 + int(arr_parts[2])
                if len(dep_parts) == 2:
                    dep_sec = int(dep_parts[0]) * 3600 + int(dep_parts[1]) * 60
                else:
                    dep_sec = int(dep_parts[0]) * 3600 + int(dep_parts[1]) * 60 + int(dep_parts[2])
                stop_duration = dep_sec - arr_sec
                is_stopped = stop_duration > 0
            
            stops.append(
                TrainStop(
                    station_code=s["station_code"],
                    station_name=s["station_name"],
                    arrival_time=s["arrival_time"],
                    departure_time=s["departure_time"],
                    is_stopped=is_stopped,
                    stop_duration=stop_duration
                )
            )

        train = Train(
            train_id=t["train_id"],
            train_type=t.get("train_type", "高速动车组"),
            schedule=TrainSchedule(stops=stops)
        )
        trains.append(train)

    return trains


def get_stations_pydantic():
    """
    获取Pydantic模型格式的车站数据
    Returns:
        List of Station objects
    """
    from models.data_models import Station

    stations_data = load_stations()
    stations = []

    for s in stations_data:
        station = Station(
            station_code=s["station_code"],
            station_name=s["station_name"],
            track_count=s.get("track_count", 1)
        )
        stations.append(station)

    return stations


# ============================================
# 测试
# ============================================

if __name__ == "__main__":
    # 测试数据加载
    print("=== 测试数据加载 ===")

    trains = load_trains()
    print(f"列车数量: {len(trains)}")
    print(f"列车ID: {get_train_ids()}")

    stations = load_stations()
    print(f"车站数量: {len(stations)}")
    print(f"车站编码: {get_station_codes()}")
    print(f"车站名称映射: {get_station_names()}")

    scenarios = load_scenarios()
    print(f"场景数量: {len(scenarios)}")

    tsl_scenarios = load_scenarios("temporary_speed_limit")
    print(f"临时限速场景数量: {len(tsl_scenarios)}")

    sf_scenarios = load_scenarios("sudden_failure")
    print(f"突发故障场景数量: {len(sf_scenarios)}")

    # 测试Pydantic模型
    print("\n=== 测试Pydantic模型 ===")
    trains_pydantic = get_trains_pydantic()
    print(f"Train objects: {len(trains_pydantic)}")
    print(f"First train: {trains_pydantic[0].train_id}")

    stations_pydantic = get_stations_pydantic()
    print(f"Station objects: {len(stations_pydantic)}")
    print(f"First station: {stations_pydantic[0].station_code}")
