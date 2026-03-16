# -*- coding: utf-8 -*-
"""
铁路调度系统 - 约束规则验证模块
用于验证调度方案是否满足所有约束条件
"""

from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


# ============================================
# 常量定义（与 rules/README.md 保持一致）
# ============================================

class DelayLevel(str, Enum):
    """延误等级"""
    MICRO = "0"    # [0, 5) 分钟
    SMALL = "5"    # [5, 30) 分钟
    MEDIUM = "30"  # [30, 100) 分钟
    LARGE = "100"  # [100, +∞) 分钟


class ScenarioType(str, Enum):
    """场景类型"""
    TEMPORARY_SPEED_LIMIT = "temporary_speed_limit"
    SUDDEN_FAILURE = "sudden_failure"
    SECTION_INTERRUPT = "section_interrupt"


# 追踪间隔时间（秒）
HEADWAY_TIME = 600  # 10分钟

# 站台占用时间（秒）
PLATFORM_OCCUPANCY_TIME = 300  # 5分钟

# 冗余时间约束（秒）
MAX_STATION_SLACK = 300
MAX_SECTION_SLACK = 180
TOTAL_SLACK = 480

# 标准区间运行时间（秒）
STANDARD_SECTION_TIMES = {
    ("BJP", "TJG"): 900,   # 15分钟
    ("TJG", "JNZ"): 2400, # 40分钟
    ("JNZ", "NJH"): 4200, # 70分钟
    ("NJH", "SHH"): 3600, # 60分钟
}

# 系统规模约束
MAX_STATIONS = 10
MAX_TRAINS = 20


# ============================================
# 数据类定义
# ============================================

@dataclass
class ValidationResult:
    """验证结果"""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    metrics: Dict[str, Any]

    def __bool__(self):
        return self.is_valid


@dataclass
class DelayInfo:
    """延误信息"""
    train_id: str
    station_code: str
    delay_seconds: int

    @property
    def delay_minutes(self) -> float:
        return self.delay_seconds / 60

    @property
    def level(self) -> DelayLevel:
        mins = self.delay_minutes
        if mins < 5:
            return DelayLevel.MICRO
        elif mins < 30:
            return DelayLevel.SMALL
        elif mins < 100:
            return DelayLevel.MEDIUM
        else:
            return DelayLevel.LARGE


# ============================================
# 验证函数
# ============================================

def time_to_seconds(time_str: str) -> int:
    """时间字符串转秒数"""
    h, m, s = map(int, time_str.split(':'))
    return h * 3600 + m * 60 + s


def seconds_to_time(seconds: int) -> str:
    """秒数转时间字符串"""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def calculate_delay_level(delay_seconds: int) -> DelayLevel:
    """计算延误等级"""
    delay_mins = delay_seconds / 60
    if delay_mins < 5:
        return DelayLevel.MICRO
    elif delay_mins < 30:
        return DelayLevel.SMALL
    elif delay_mins < 100:
        return DelayLevel.MEDIUM
    else:
        return DelayLevel.LARGE


def get_min_section_time(from_station: str, to_station: str) -> int:
    """获取最小区间运行时间（标准时间的90%）"""
    standard = STANDARD_SECTION_TIMES.get((from_station, to_station), 1800)
    return int(standard * 0.9)


def validate_schedule(
    schedule: Dict[str, List[Dict]],
    station_codes: List[str],
    train_data: Optional[List[Dict]] = None
) -> ValidationResult:
    """
    验证调度方案是否满足所有约束

    Args:
        schedule: 调度方案 {train_id: [{station_code, arrival_time, departure_time, ...}]}
        station_codes: 车站编码列表
        train_data: 原始列车数据（可选，用于对比）

    Returns:
        ValidationResult: 验证结果
    """
    errors = []
    warnings = []
    metrics = {}

    # 1. 验证时间单调性
    time_monotonicity_errors = validate_time_monotonicity(schedule)
    errors.extend(time_monotonicity_errors)

    # 2. 验证追踪间隔约束
    headway_errors = validate_headway(schedule, station_codes)
    errors.extend(headway_errors)

    # 3. 验证区间运行时间约束
    section_time_errors = validate_section_times(schedule)
    errors.extend(section_time_errors)

    # 4. 计算延误统计
    delay_stats = calculate_delay_statistics(schedule, train_data)
    metrics.update(delay_stats)

    # 5. 检查系统规模
    if len(schedule) > MAX_TRAINS:
        warnings.append(f"列车数量 {len(schedule)} 超过建议最大值 {MAX_TRAINS}")

    if len(station_codes) > MAX_STATIONS:
        warnings.append(f"车站数量 {len(station_codes)} 超过建议最大值 {MAX_STATIONS}")

    is_valid = len(errors) == 0

    return ValidationResult(
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
        metrics=metrics
    )


def validate_time_monotonicity(schedule: Dict[str, List[Dict]]) -> List[str]:
    """验证时间单调性：同一列车的到达时间 <= 发车时间，后续车站到达时间 >= 前一站发车时间"""
    errors = []

    for train_id, stops in schedule.items():
        for i, stop in enumerate(stops):
            # 检查到达时间 <= 发车时间
            arr = time_to_seconds(stop["arrival_time"])
            dep = time_to_seconds(stop["departure_time"])

            if arr > dep:
                errors.append(
                    f"列车 {train_id} 在车站 {stop['station_code']}: "
                    f"到达时间 {stop['arrival_time']} 晚于发车时间 {stop['departure_time']}"
                )

            # 检查后续车站
            if i < len(stops) - 1:
                next_stop = stops[i + 1]
                next_arr = time_to_seconds(next_stop["arrival_time"])

                if dep > next_arr:
                    errors.append(
                        f"列车 {train_id}: 从 {stop['station_code']} 发车时间 {stop['departure_time']} "
                        f"晚于下一站 {next_stop['station_code']} 到达时间 {next_stop['arrival_time']}"
                    )

    return errors


def validate_headway(
    schedule: Dict[str, List[Dict]],
    station_codes: List[str],
    headway_time: int = HEADWAY_TIME
) -> List[str]:
    """验证追踪间隔约束：在同一车站，后车必须晚于前车发车"""
    errors = []

    # 按发车时间排序所有列车
    for station in station_codes:
        train_departures = []

        for train_id, stops in schedule.items():
            for stop in stops:
                if stop["station_code"] == station:
                    dep_time = time_to_seconds(stop["departure_time"])
                    train_departures.append((train_id, dep_time))
                    break

        # 按发车时间排序
        train_departures.sort(key=lambda x: x[1])

        # 检查追踪间隔
        for i in range(1, len(train_departures)):
            prev_train, prev_time = train_departures[i - 1]
            curr_train, curr_time = train_departures[i]

            if curr_time - prev_time < headway_time:
                errors.append(
                    f"车站 {station}: 列车 {prev_train} 和 {curr_train} "
                    f"追踪间隔 {curr_time - prev_time}秒 少于要求的 {headway_time}秒"
                )

    return errors


def validate_section_times(
    schedule: Dict[str, List[Dict]],
    min_section_times: Optional[Dict[Tuple[str, str], int]] = None
) -> List[str]:
    """验证区间运行时间约束"""
    errors = []

    if min_section_times is None:
        # 使用默认的最小区间运行时间
        min_section_times = {
            (from_s, to_s): get_min_section_time(from_s, to_s)
            for from_s, to_s in STANDARD_SECTION_TIMES.keys()
        }

    for train_id, stops in schedule.items():
        for i in range(len(stops) - 1):
            curr_station = stops[i]["station_code"]
            next_station = stops[i + 1]["station_code"]

            curr_dep = time_to_seconds(stops[i]["departure_time"])
            next_arr = time_to_seconds(stops[i + 1]["arrival_time"])

            section_time = next_arr - curr_dep
            min_time = min_section_times.get((curr_station, next_station), 0)

            if section_time < min_time:
                errors.append(
                    f"列车 {train_id}: 区间 {curr_station}->{next_station} "
                    f"运行时间 {section_time}秒 少于最低要求 {min_time}秒"
                )

    return errors


def calculate_delay_statistics(
    schedule: Dict[str, List[Dict]],
    original_schedule: Optional[Dict[str, List[Dict]]] = None
) -> Dict[str, Any]:
    """计算延误统计"""
    delays = []
    delay_by_train = {}
    delay_by_station = {}

    for train_id, stops in schedule.items():
        train_delays = []

        for stop in stops:
            delay = stop.get("delay_seconds", 0)
            if delay > 0:
                delays.append(delay)
                train_delays.append(delay)

                station = stop["station_code"]
                if station not in delay_by_station:
                    delay_by_station[station] = []
                delay_by_station[station].append(delay)

        if train_delays:
            delay_by_train[train_id] = {
                "max": max(train_delays),
                "avg": sum(train_delays) / len(train_delays),
                "total": sum(train_delays)
            }
        else:
            delay_by_train[train_id] = {"max": 0, "avg": 0, "total": 0}

    # 按等级统计
    level_counts = {
        "MICRO": 0,
        "SMALL": 0,
        "MEDIUM": 0,
        "LARGE": 0
    }

    for delay in delays:
        level = calculate_delay_level(delay)
        level_counts[level.value] += 1

    stats = {
        "total_delays": len(delays),
        "trains_with_delays": sum(1 for d in delay_by_train.values() if d["max"] > 0),
        "delay_by_train": delay_by_train,
        "delay_by_station": {
            s: {"max": max(d), "avg": sum(d) / len(d), "count": len(d)}
            for s, d in delay_by_station.items()
        },
        "delay_level_distribution": level_counts
    }

    if delays:
        stats["max_delay"] = max(delays)
        stats["avg_delay"] = sum(delays) / len(delays)
        stats["total_delay"] = sum(delays)
    else:
        stats["max_delay"] = 0
        stats["avg_delay"] = 0
        stats["total_delay"] = 0

    return stats


def validate_scenario_params(scenario: Dict[str, Any]) -> ValidationResult:
    """验证场景参数"""
    errors = []
    warnings = []

    scenario_type = scenario.get("scenario_type")

    if scenario_type == ScenarioType.TEMPORARY_SPEED_LIMIT:
        required_params = ["limit_speed_kmh", "duration_minutes", "affected_section"]
        for param in required_params:
            if param not in scenario.get("scenario_params", {}):
                errors.append(f"临时限速场景缺少必要参数: {param}")

        # 检查限速值合理性
        limit_speed = scenario.get("scenario_params", {}).get("limit_speed_kmh", 0)
        if limit_speed > 350:
            warnings.append(f"限速值 {limit_speed} km/h 超过高铁最大设计速度")

    elif scenario_type == ScenarioType.SUDDEN_FAILURE:
        required_params = ["failure_type", "estimated_repair_time"]
        for param in required_params:
            if param not in scenario.get("scenario_params", {}):
                errors.append(f"突发故障场景缺少必要参数: {param}")

    elif scenario_type == ScenarioType.SECTION_INTERRUPT:
        warnings.append("区间中断场景当前版本暂不支持")

    is_valid = len(errors) == 0

    return ValidationResult(
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
        metrics={}
    )


def check_constraint_satisfaction(
    schedule: Dict[str, List[Dict]],
    constraints: Dict[str, Any]
) -> Dict[str, bool]:
    """
    检查调度方案是否满足指定约束

    Args:
        schedule: 调度方案
        constraints: 约束字典，如 {"headway": 600, "min_section_time": True}

    Returns:
        约束满足情况字典
    """
    results = {}

    if "headway" in constraints:
        headway_errors = validate_headway(schedule, [], constraints["headway"])
        results["headway"] = len(headway_errors) == 0

    if "min_section_time" in constraints:
        section_errors = validate_section_times(schedule)
        results["min_section_time"] = len(section_errors) == 0

    if "time_monotonicity" in constraints:
        monotonicity_errors = validate_time_monotonicity(schedule)
        results["time_monotonicity"] = len(monotonicity_errors) == 0

    return results


# ============================================
# 测试
# ============================================

if __name__ == "__main__":
    # 测试验证功能
    print("=== 测试约束验证 ===")

    # 有效的调度方案
    valid_schedule = {
        "G1001": [
            {"station_code": "BJP", "arrival_time": "08:00:00", "departure_time": "08:10:00", "delay_seconds": 0},
            {"station_code": "TJG", "arrival_time": "08:25:00", "departure_time": "08:30:00", "delay_seconds": 0},
            {"station_code": "JNZ", "arrival_time": "09:10:00", "departure_time": "09:15:00", "delay_seconds": 0},
        ]
    }

    result = validate_schedule(valid_schedule, ["BJP", "TJG", "JNZ"])
    print(f"有效方案验证: {result.is_valid}")
    print(f"错误: {result.errors}")

    # 无效的调度方案（追踪间隔不足）
    invalid_schedule = {
        "G1001": [
            {"station_code": "BJP", "arrival_time": "08:00:00", "departure_time": "08:10:00", "delay_seconds": 0},
            {"station_code": "TJG", "arrival_time": "08:25:00", "departure_time": "08:30:00", "delay_seconds": 0},
        ],
        "G1002": [
            {"station_code": "BJP", "arrival_time": "08:15:00", "departure_time": "08:18:00", "delay_seconds": 0},  # 发车时间太接近
            {"station_code": "TJG", "arrival_time": "08:33:00", "departure_time": "08:38:00", "delay_seconds": 0},
        ]
    }

    result = validate_schedule(invalid_schedule, ["BJP", "TJG"])
    print(f"\n无效方案验证: {result.is_valid}")
    print(f"错误: {result.errors}")

    # 测试延误等级计算
    print("\n=== 测试延误等级 ===")
    test_delays = [0, 180, 600, 1800, 3600, 7200]
    for delay in test_delays:
        level = calculate_delay_level(delay)
        print(f"延误 {delay}秒 ({delay/60:.1f}分钟) -> {level.value}级")
