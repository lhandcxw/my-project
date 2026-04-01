# -*- coding: utf-8 -*-
"""
工作流数据模型模块
定义统一中间模型，用于工作流骨架
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime


class SceneType(str, Enum):
    """场景类型枚举"""
    TEMPORARY_SPEED_LIMIT = "temporary_speed_limit"
    SUDDEN_FAILURE = "sudden_failure"
    SECTION_INTERRUPT = "section_interrupt"


class SceneSpec(BaseModel):
    """
    场景规格模型
    描述铁路调度场景的基本信息
    """
    scene_type: str = Field(description="场景类型: temporary_speed_limit/sudden_failure/section_interrupt")
    scene_id: str = Field(description="场景唯一标识")
    description: str = Field(default="", description="场景描述")
    location: Dict[str, Any] = Field(default_factory=dict, description="位置信息")
    time_info: Dict[str, Any] = Field(default_factory=dict, description="时间信息")
    extra_params: Dict[str, Any] = Field(default_factory=dict, description="额外参数")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")


class AffectedTrain(BaseModel):
    """受影响列车"""
    train_id: str = Field(description="列车ID")
    reason: str = Field(default="", description="受影响原因")
    impact_level: str = Field(default="unknown", description="影响等级")


class DispatchContext(BaseModel):
    """
    调度上下文模型
    包含调度所需的全部上下文信息
    """
    scene_spec: SceneSpec = Field(description="场景规格")
    trains: List[Dict[str, Any]] = Field(default_factory=list, description="列车数据列表")
    stations: List[Dict[str, Any]] = Field(default_factory=list, description="车站数据列表")
    affected_trains: List[AffectedTrain] = Field(default_factory=list, description="受影响列车列表")
    data_loader_info: Optional[Dict[str, Any]] = Field(default=None, description="数据加载器信息")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")


class SubTask(BaseModel):
    """
    子任务模型
    工作流中的最小执行单元
    """
    task_id: str = Field(description="子任务ID")
    task_type: str = Field(description="子任务类型")
    description: str = Field(default="", description="子任务描述")
    input_data: Dict[str, Any] = Field(default_factory=dict, description="输入数据")
    output_data: Dict[str, Any] = Field(default_factory=dict, description="输出数据")
    status: str = Field(default="pending", description="状态: pending/running/completed/failed")
    error: Optional[str] = Field(default=None, description="错误信息")


class TaskPlan(BaseModel):
    """
    任务计划模型
    描述完整的工作流任务规划
    """
    task_id: str = Field(description="任务ID")
    scene_spec: SceneSpec = Field(description="场景规格")
    subtasks: List[SubTask] = Field(default_factory=list, description="子任务列表")
    status: str = Field(default="planned", description="状态: planned/running/completed/failed")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")


class SolverRequest(BaseModel):
    """
    求解器请求模型
    发送给求解器的输入数据
    """
    scene_spec: SceneSpec = Field(description="场景规格")
    dispatch_context: DispatchContext = Field(description="调度上下文")
    solver_config: Dict[str, Any] = Field(default_factory=dict, description="求解器配置")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")


class SolverResult(BaseModel):
    """
    求解器结果模型
    求解器返回的调度结果
    """
    success: bool = Field(description="是否成功")
    schedule: List[Dict[str, Any]] = Field(default_factory=list, description="调度结果")
    metrics: Dict[str, Any] = Field(default_factory=dict, description="评估指标")
    solving_time_seconds: float = Field(default=0.0, description="求解耗时(秒)")
    solver_type: str = Field(default="unknown", description="求解器类型")
    error_message: Optional[str] = Field(default=None, description="错误信息")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")


class ValidationIssue(BaseModel):
    """验证问题"""
    severity: str = Field(description="严重程度: warning/error")
    issue_type: str = Field(description="问题类型")
    description: str = Field(description="问题描述")
    location: Dict[str, Any] = Field(default_factory=dict, description="位置信息")
    suggestion: str = Field(default="", description="修复建议")


class ValidationReport(BaseModel):
    """验证报告"""
    is_valid: bool = Field(description="是否通过验证")
    issues: List[ValidationIssue] = Field(default_factory=list, description="问题列表")
    passed_rules: List[str] = Field(default_factory=list, description="通过规则列表")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")


class WorkflowResult(BaseModel):
    """
    工作流结果模型
    工作流执行的最终输出
    """
    success: bool = Field(description="是否成功")
    scene_spec: Optional[SceneSpec] = Field(default=None, description="场景规格")
    task_plan: Optional[TaskPlan] = Field(default=None, description="任务计划")
    solver_result: Optional[SolverResult] = Field(default=None, description="求解器结果")
    validation_report: Optional[ValidationReport] = Field(default=None, description="验证报告")
    debug_trace: Dict[str, Any] = Field(default_factory=dict, description="调试追踪信息")
    message: str = Field(default="", description="结果消息")
    error: Optional[str] = Field(default=None, description="错误信息")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")