# -*- coding: utf-8 -*-
"""
通用枚举定义
所有内部业务枚举统一使用英文 code，中文仅用于展示层
"""

from enum import Enum
from typing import Literal


class SceneTypeCode(str, Enum):
    """场景类型英文代码 - 仅用于内部路由和判断"""
    TEMP_SPEED_LIMIT = "TEMP_SPEED_LIMIT"      # 临时限速
    SUDDEN_FAILURE = "SUDDEN_FAILURE"            # 突发故障
    SECTION_INTERRUPT = "SECTION_INTERRUPT"     # 区间封锁
    UNKNOWN = "UNKNOWN"                          # 未知


class SceneTypeLabel(str, Enum):
    """场景类型中文标签 - 仅用于展示层"""
    TEMP_SPEED_LIMIT = "临时限速"
    SUDDEN_FAILURE = "突发故障"
    SECTION_INTERRUPT = "区间封锁"
    UNKNOWN = "未知"


class FaultTypeCode(str, Enum):
    """故障类型英文代码"""
    RAIN = "RAIN"                    # 暴雨
    WIND = "WIND"                    # 大风
    SNOW = "SNOW"                    # 降雪
    EQUIPMENT_FAILURE = "EQUIPMENT_FAILURE"  # 设备故障
    SIGNAL_FAILURE = "SIGNAL_FAILURE"        # 信号故障
    CATENARY_FAILURE = "CATENARY_FAILURE"    # 接触网故障
    TRACK_CONDITION = "TRACK_CONDITION"      # 线路条件
    MANUAL_RESTRICTION = "MANUAL_RESTRICTION"  # 人工限速
    DELAY = "DELAY"                  # 延误（预计晚点）
    UNKNOWN = "UNKNOWN"              # 未知


class RequestSourceType(str, Enum):
    """请求来源类型"""
    NATURAL_LANGUAGE = "natural_language"
    FORM = "form"
    JSON = "json"


class PolicyDecisionType(str, Enum):
    """策略决策类型"""
    ACCEPT = "accept"           # 采用主解
    FALLBACK = "fallback"       # 回退基线
    RERUN = "rerun"             # 重新求解


class SolverTypeCode(str, Enum):
    """求解器类型代码"""
    MIP = "mip_scheduler"
    FCFS = "fcfs_scheduler"
    MAX_DELAY_FIRST = "max_delay_first_scheduler"
    NOOP = "noop_scheduler"


class PlanningIntentCode(str, Enum):
    """计划意图代码"""
    RECALCULATE_CORRIDOR = "recalculate_corridor_schedule"     # 重新计算区间时刻表
    RECOVER_FROM_DISRUPTION = "recover_from_disruption"       # 恢复故障后运行
    HANDLE_SECTION_BLOCK = "handle_section_block"             # 处理区间中断


# ============== 映射函数 ==============

def scene_code_to_label(code: SceneTypeCode) -> str:
    """场景代码转中文标签"""
    mapping = {
        SceneTypeCode.TEMP_SPEED_LIMIT: "临时限速",
        SceneTypeCode.SUDDEN_FAILURE: "突发故障",
        SceneTypeCode.SECTION_INTERRUPT: "区间封锁",
        SceneTypeCode.UNKNOWN: "未知"
    }
    return mapping.get(code, "未知")


def scene_label_to_code(label: str) -> SceneTypeCode:
    """场景中文标签转代码"""
    mapping = {
        "临时限速": SceneTypeCode.TEMP_SPEED_LIMIT,
        "突发故障": SceneTypeCode.SUDDEN_FAILURE,
        "区间封锁": SceneTypeCode.SECTION_INTERRUPT
    }
    return mapping.get(label, SceneTypeCode.UNKNOWN)


def fault_code_to_label(code: FaultTypeCode) -> str:
    """故障代码转中文标签"""
    mapping = {
        FaultTypeCode.RAIN: "暴雨",
        FaultTypeCode.WIND: "大风",
        FaultTypeCode.SNOW: "降雪",
        FaultTypeCode.EQUIPMENT_FAILURE: "设备故障",
        FaultTypeCode.SIGNAL_FAILURE: "信号故障",
        FaultTypeCode.CATENARY_FAILURE: "接触网故障",
        FaultTypeCode.TRACK_CONDITION: "线路条件",
        FaultTypeCode.MANUAL_RESTRICTION: "人工限速",
        FaultTypeCode.DELAY: "延误",
        FaultTypeCode.UNKNOWN: "未知"
    }
    return mapping.get(code, "未知")


def fault_label_to_code(label: str) -> FaultTypeCode:
    """故障中文标签转代码"""
    mapping = {
        "暴雨": FaultTypeCode.RAIN,
        "大风": FaultTypeCode.WIND,
        "降雪": FaultTypeCode.SNOW,
        "设备故障": FaultTypeCode.EQUIPMENT_FAILURE,
        "信号故障": FaultTypeCode.SIGNAL_FAILURE,
        "接触网故障": FaultTypeCode.CATENARY_FAILURE,
        "线路条件": FaultTypeCode.TRACK_CONDITION,
        "人工限速": FaultTypeCode.MANUAL_RESTRICTION,
        "延误": FaultTypeCode.DELAY,
        "预计晚点": FaultTypeCode.DELAY,
        "晚点": FaultTypeCode.DELAY
    }
    return mapping.get(label, FaultTypeCode.UNKNOWN)