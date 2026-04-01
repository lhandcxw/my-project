# -*- coding: utf-8 -*-
"""
上下文构建器模块
负责从原始输入构建场景规格和调度上下文
"""

from typing import Optional, List, Dict, Any
import uuid
from datetime import datetime

from models.workflow_models import (
    SceneSpec,
    SceneType,
    DispatchContext,
    AffectedTrain
)


def build_scene_spec(raw_input: dict) -> SceneSpec:
    """
    从原始输入构建场景规格

    Args:
        raw_input: 原始输入字典，包含场景信息

    Returns:
        SceneSpec: 场景规格对象
    """
    scene_type = raw_input.get("scene_type", "")
    scene_id = raw_input.get("scene_id", f"scene_{uuid.uuid4().hex[:8]}")
    description = raw_input.get("description", "")
    location = raw_input.get("location", {})
    time_info = raw_input.get("time_info", {})
    extra_params = raw_input.get("extra_params", {})
    metadata = raw_input.get("metadata", {})

    # 验证场景类型合法性
    valid_types = [e.value for e in SceneType]
    if scene_type not in valid_types:
        # 如果不合法，存入 extra_params
        extra_params = {**extra_params, "invalid_scene_type": scene_type}
        scene_type = ""

    return SceneSpec(
        scene_type=scene_type,
        scene_id=scene_id,
        description=description,
        location=location,
        time_info=time_info,
        extra_params=extra_params,
        metadata=metadata
    )


def build_dispatch_context(
    scene_spec: SceneSpec,
    trains=None,
    stations=None,
    data_loader=None
) -> DispatchContext:
    """
    构建调度上下文

    Args:
        scene_spec: 场景规格
        trains: 列车数据列表（可选）
        stations: 车站数据列表（可选）
        data_loader: 数据加载器（可选）

    Returns:
        DispatchContext: 调度上下文对象
    """
    # 处理列车数据
    trains_data = []
    if trains is not None:
        if isinstance(trains, list):
            trains_data = trains
        else:
            # 尝试转换为列表
            try:
                trains_data = list(trains)
            except Exception:
                trains_data = []

    # 处理车站数据
    stations_data = []
    if stations is not None:
        if isinstance(stations, list):
            stations_data = stations
        else:
            try:
                stations_data = list(stations)
            except Exception:
                stations_data = []

    # 如果有 data_loader，尝试加载数据
    data_loader_info = None
    if data_loader is not None:
        try:
            if not trains_data and hasattr(data_loader, 'get_trains_pydantic'):
                # 这里不实际调用，只是记录意图
                data_loader_info = {"intent": "load_trains", "status": "deferred"}
            if not stations_data and hasattr(data_loader, 'get_stations_pydantic'):
                data_loader_info = data_loader_info or {}
                data_loader_info["intent"] = "load_trains_and_stations"
                data_loader_info["status"] = "deferred"
        except Exception as e:
            data_loader_info = {"error": str(e), "status": "failed"}

    # 如果输入信息不足，返回缺失字段信息
    missing_info = {}
    if not trains_data:
        missing_info["trains"] = "not_provided"
    if not stations_data:
        missing_info["stations"] = "not_provided"

    metadata = {}
    if missing_info:
        metadata["missing_input_fields"] = missing_info

    return DispatchContext(
        scene_spec=scene_spec,
        trains=trains_data,
        stations=stations_data,
        affected_trains=[],  # 暂不填充，后续通过 identify_affected_trains 填充
        data_loader_info=data_loader_info,
        metadata=metadata
    )


def identify_affected_trains(
    scene_spec: SceneSpec,
    dispatch_context: DispatchContext
) -> Dict[str, Any]:
    """
    识别受影响的列车

    Args:
        scene_spec: 场景规格
        dispatch_context: 调度上下文

    Returns:
        dict: 包含受影响列车信息的字典
    """
    # 简单规则：基于场景类型和位置识别受影响列车
    affected_trains_list = []

    scene_type = scene_spec.scene_type
    location = scene_spec.location

    # 如果没有列车数据，返回空列表
    if not dispatch_context.trains:
        return {
            "affected_trains": [],
            "rule": "no_trains_data",
            "message": "没有列车数据，无法识别受影响列车"
        }

    # 基于场景类型的简单规则
    if scene_type == SceneType.TEMPORARY_SPEED_LIMIT.value:
        # 临时限速：根据位置信息简单匹配
        limit_section = location.get("section", "")
        for train in dispatch_context.trains:
            # 简单规则：所有列车都可能受影响
            affected_trains_list.append(AffectedTrain(
                train_id=train.get("train_id", "unknown"),
                reason="temporary_speed_limit",
                impact_level="medium"
            ))

    elif scene_type == SceneType.SUDDEN_FAILURE.value:
        # 突发故障：根据位置匹配
        failure_station = location.get("station_code", "")
        for train in dispatch_context.trains:
            # 简单规则：所有列车都可能受影响
            affected_trains_list.append(AffectedTrain(
                train_id=train.get("train_id", "unknown"),
                reason="sudden_failure",
                impact_level="high"
            ))

    elif scene_type == SceneType.SECTION_INTERRUPT.value:
        # 区间中断：根据区间信息匹配
        interrupt_section = location.get("section", "")
        for train in dispatch_context.trains:
            # 简单规则：所有列车都可能受影响
            affected_trains_list.append(AffectedTrain(
                train_id=train.get("train_id", "unknown"),
                reason="section_interrupt",
                impact_level="critical"
            ))

    else:
        # 未知场景类型，返回空列表
        return {
            "affected_trains": [],
            "rule": "unknown_scene_type",
            "message": f"未知场景类型: {scene_type}"
        }

    return {
        "affected_trains": affected_trains_list,
        "rule": f"simple_rule_for_{scene_type}",
        "total_count": len(affected_trains_list)
    }