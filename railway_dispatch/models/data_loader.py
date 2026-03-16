# -*- coding: utf-8 -*-
"""
铁路调度系统 - 数据加载模块
统一读取preset数据，所有程序都从这里读取数据
"""

import json
import os
from typing import List, Dict, Any, Optional
from pathlib import Path

# 数据目录路径
DATA_DIR = Path(__file__).parent.parent / "data"

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

    station_file = get_data_path("stations.json")
    if not station_file.exists():
        raise FileNotFoundError(f"车站数据文件不存在: {station_file}")

    with open(station_file, "r", encoding="utf-8") as f:
        stations = json.load(f)

    _cache["stations"] = stations
    return stations


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
    from models.data_models import Train, SlackTime, TrainSchedule, TrainStop

    trains_data = load_trains()
    trains = []

    for t in trains_data:
        stops = [
            TrainStop(
                station_code=s["station_code"],
                station_name=s["station_name"],
                arrival_time=s["arrival_time"],
                departure_time=s["departure_time"],
                platform=s["platform"]
            )
            for s in t["schedule"]["stops"]
        ]
        slack = SlackTime(**t.get("slack_time", {}))

        train = Train(
            train_id=t["train_id"],
            train_type=t.get("train_type", "高速动车组"),
            speed_level=t.get("speed_level", 350),
            schedule=TrainSchedule(stops=stops),
            slack_time=slack
        )
        trains.append(train)

    return trains


def get_stations_pydantic():
    """
    获取Pydantic模型格式的车站数据
    Returns:
        List of Station objects
    """
    from models.data_models import Station, Platform, ConnectionSection

    stations_data = load_stations()
    stations = []

    for s in stations_data:
        platforms = [Platform(**p) for p in s.get("platforms", [])]
        connections = [ConnectionSection(**c) for c in s.get("connection_sections", [])]

        station = Station(
            station_code=s["station_code"],
            station_name=s["station_name"],
            track_count=s.get("track_count", 1),
            platforms=platforms,
            connection_sections=connections
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
