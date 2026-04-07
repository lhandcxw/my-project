# -*- coding: utf-8 -*-
"""
RuleAgent 到 WorkflowEngine 的桥接模块
用于在 RULE_AGENT_USE_WORKFLOW=True 时让 RuleAgent 走新工作流
"""

import os
import logging
from typing import Dict, Any, Optional, Union

logger = logging.getLogger(__name__)

# Feature flag
RULE_WORKFLOW_BRIDGE_ENABLED = os.getenv("RULE_AGENT_USE_WORKFLOW", "0") == "1"


def build_raw_input_from_rule_request(
    user_input: Union[dict, str],
    extra_context: dict = None
) -> dict:
    """
    把 RuleAgent 识别的场景信息转换成 workflow_engine 所需 raw_input

    Args:
        user_input: 用户输入（dict 或 str）
        extra_context: 额外上下文

    Returns:
        dict: workflow_engine 可接受的 raw_input
    """
    extra_context = extra_context or {}

    # 如果是字符串，尝试解析为 dict
    if isinstance(user_input, str):
        # 简单尝试：如果是 JSON 字符串则解析
        try:
            import json
            user_input = json.loads(user_input)
        except:
            # 否则创建最小结构
            user_input = {
                "description": user_input,
                "scene_type": "temporary_speed_limit"  # 默认
            }

    # 兼容 RuleAgent 的 delay_injection 格式
    # RuleAgent 的 analyze() 方法传入的 delay_injection 包含:
    # - scenario_type
    # - scenario_id
    # - injected_delays
    # - affected_trains
    # - scenario_params
    scene_type = user_input.get("scenario_type", extra_context.get("scene_type", ""))
    injected_delays = user_input.get("injected_delays", extra_context.get("injected_delays", []))
    affected_trains = user_input.get("affected_trains", extra_context.get("affected_trains", []))
    scenario_params = user_input.get("scenario_params", extra_context.get("scenario_params", {}))

    # 如果 injected_delays 为空但有 affected_trains，构建一个默认的 injected_delay
    if not injected_delays and affected_trains:
        # 从 affected_trains 构建基本的 injected_delays
        for train_id in affected_trains:
            injected_delays.append({
                "train_id": train_id,
                "location": {"location_type": "section", "station_code": ""},
                "initial_delay_seconds": 0,
                "timestamp": "2024-01-01T00:00:00Z"
            })

    # 构建 raw_input
    raw_input = {
        "scene_type": scene_type,
        "scene_id": user_input.get("scenario_id", extra_context.get("scene_id", f"rule_{id(user_input)}")),
        "description": user_input.get("description", extra_context.get("description", "")),
        "location": user_input.get("location", extra_context.get("location", {})),
        "time_info": user_input.get("time_info", extra_context.get("time_info", {})),
        "injected_delays": injected_delays,
        "affected_trains": affected_trains,
        "metadata": {
            "source": "rule_workflow_bridge",
            "scenario_params": scenario_params,
            "extra_context": extra_context
        }
    }

    # 检查缺失字段
    missing_fields = []
    if not raw_input["scene_type"]:
        missing_fields.append("scene_type")
    # 不再强制要求 injected_delays，因为可能通过 affected_trains 构建

    if missing_fields:
        raw_input["metadata"]["missing_fields"] = missing_fields
        logger.warning(f"Rule workflow bridge: missing fields = {missing_fields}")

    # section_interrupt 特殊处理：允许返回占位结果
    if raw_input["scene_type"] == "section_interrupt":
        raw_input["metadata"]["placeholder"] = True

    return raw_input


def run_rule_workflow_bridge(
    user_input: Union[dict, str],
    trains=None,
    stations=None,
    dry_run: bool = False
) -> tuple:
    """
    运行 RuleAgent 到 Workflow 的桥接

    Args:
        user_input: 用户输入
        trains: 列车数据（可选，未提供时自动加载真实数据）
        stations: 车站数据（可选，未提供时自动加载真实数据）
        dry_run: 是否 dry-run 模式

    Returns:
        tuple: (workflow_result, fallback_triggered)
        - workflow_result: WorkflowResult 或 None（fallback 时）
        - fallback_triggered: 是否触发了 fallback
    """
    from railway_agent.workflow_engine import run_workflow

    # 如果没有传入 trains/stations，自动加载真实数据
    if trains is None or stations is None:
        try:
            from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
            # 确保使用真实数据模式
            use_real_data(True)

            if trains is None:
                trains = get_trains_pydantic()
            if stations is None:
                stations = get_stations_pydantic()

            logger.info(f"Bridge auto-loaded real data: {len(trains)} trains, {len(stations)} stations")
        except Exception as e:
            logger.warning(f"Bridge failed to auto-load data: {e}")

    # 构建 raw_input
    extra_context = {}
    if isinstance(user_input, dict) and "affected_trains" in user_input:
        # 从 RuleAgent 的 delay_injection 提取信息
        if "injected_delays" in user_input:
            extra_context["injected_delays"] = user_input["injected_delays"]
        if "scenario_params" in user_input:
            extra_context["location"] = {"params": user_input["scenario_params"]}

    raw_input = build_raw_input_from_rule_request(user_input, extra_context)

    try:
        # 调用 workflow_engine
        result = run_workflow(
            raw_input=raw_input,
            trains=trains,
            stations=stations,
            dry_run=dry_run
        )

        if result.success:
            logger.info("Rule workflow bridge: success")
            return result, False
        else:
            # workflow 失败，触发 fallback
            logger.warning(f"Rule workflow bridge: failed, message={result.message}")
            return None, True

    except Exception as e:
        logger.exception(f"Rule workflow bridge exception: {e}")
        return None, True


def is_bridge_enabled() -> bool:
    """检查桥接是否启用"""
    return RULE_WORKFLOW_BRIDGE_ENABLED


def map_workflow_result_to_agent_result(workflow_result) -> Dict[str, Any]:
    """
    将 WorkflowResult 映射为 RuleAgent 的 AgentResult 格式

    Args:
        workflow_result: WorkflowResult

    Returns:
        dict: 类似 AgentResult 的字典
    """
    if workflow_result is None:
        return {
            "success": False,
            "error": "workflow_fallback"
        }

    # 映射字段
    result = {
        "success": workflow_result.success,
        "recognized_scenario": workflow_result.scene_spec.scene_type if workflow_result.scene_spec else "unknown",
        "selected_skill": "workflow_skill",
        "reasoning": f"Workflow执行: {workflow_result.message}",
        "computation_time": workflow_result.metadata.get("execution_time", 0),
        "workflow_result": workflow_result
    }

    # 如果有 solver_result，映射调度结果
    if workflow_result.solver_result:
        # 将 schedule 从 List 转换为 Dict 格式 (train_id -> stops)
        schedule_list = workflow_result.solver_result.schedule
        schedule_dict = {}
        if isinstance(schedule_list, list):
            for item in schedule_list:
                train_id = item.get("train_id", "unknown")
                if train_id not in schedule_dict:
                    schedule_dict[train_id] = []
                # 移除 train_id 字段，只保留停站信息
                stop_info = {k: v for k, v in item.items() if k != "train_id"}
                schedule_dict[train_id].append(stop_info)

        # 创建兼容的 dispatch_result 对象
        class DispatchResultCompat:
            def __init__(self, data):
                self.success = data.get("success", False)
                self.schedule = data.get("schedule", {})
                self.delay_statistics = data.get("delay_statistics", {})
                self.message = data.get("message", "")
                # 兼容旧属性 - 需要 Dict 格式
                self.optimized_schedule = self.schedule
                self.computation_time = 0.0

        result["dispatch_result"] = DispatchResultCompat({
            "success": workflow_result.solver_result.success,
            "schedule": schedule_dict,  # 使用转换后的字典格式
            "delay_statistics": workflow_result.solver_result.metrics,
            "message": workflow_result.solver_result.error_message or "success"
        })

    return result