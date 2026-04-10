# -*- coding: utf-8 -*-
"""
预处理层数据模型
定义从原始输入到标准化请求的所有数据结构
"""

from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field
from datetime import datetime
import uuid

from models.common_enums import (
    SceneTypeCode,
    SceneTypeLabel,
    FaultTypeCode,
    RequestSourceType,
    PolicyDecisionType,
    SolverTypeCode,
    PlanningIntentCode
)


# ============== L0: 原始请求 ==============

class RawUserRequest(BaseModel):
    """原始用户请求 - Web 层构造"""
    source_type: RequestSourceType
    raw_text: Optional[str] = None
    form_data: Optional[Dict[str, Any]] = None
    json_data: Optional[Dict[str, Any]] = None
    timestamp: datetime = Field(default_factory=datetime.now)


# ============== L0: 标准化调度请求 ==============

class LocationInfo(BaseModel):
    """位置信息"""
    station_code: Optional[str] = None
    station_name: Optional[str] = None
    section_id: Optional[str] = None  # 区段ID，如 "XSD-BDD"


class CompletenessInfo(BaseModel):
    """完整性信息"""
    can_enter_solver: bool = False
    missing_fields: List[str] = Field(default_factory=list)
    reason: Optional[str] = None


class EvidenceInfo(BaseModel):
    """证据信息 - 来自规则提取或LLM"""
    source: str  # "rule_extractor" / "llm_extractor"
    field_name: str
    value: Any
    confidence: float = 1.0


class CanonicalDispatchRequest(BaseModel):
    """
    标准化调度请求 - 所有下游模块统一接收此格式
    不再接收自然语言或原始文本
    v3.2: 补全关键字段，成为唯一可信主中间态
    """
    schema_version: str = "dispatch_v3_2"
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    
    # 来源信息
    source_type: RequestSourceType
    raw_text: Optional[str] = None
    
    # 场景信息（英文 code + 中文 label）
    scene_type_code: Optional[SceneTypeCode] = None
    scene_type_label: Optional[str] = None
    fault_type: Optional[FaultTypeCode] = None
    
    # 时间信息
    event_time: Optional[str] = None  # 事件发生时间
    snapshot_time: Optional[str] = None  # 快照时间
    expected_duration_minutes: Optional[int] = None  # 预计持续分钟
    
    # 位置信息
    location: Optional[LocationInfo] = None
    
    # 列车信息
    affected_train_ids: List[str] = Field(default_factory=list)
    reported_delay_seconds: Optional[int] = None
    speed_limit_kph: Optional[int] = None
    
    # 严重程度
    fault_severity: Optional[str] = None  # minor/major/critical
    
    # 走廊提示
    corridor_hint: Optional[str] = None  # 如 "SJP-SJP"
    
    # 当前状态引用
    current_state_ref: Optional[str] = None
    
    # 归一化追踪 - 记录每个字段来源
    normalization_trace: List[Dict[str, Any]] = Field(default_factory=list)
    
    # 完整性判定
    completeness: CompletenessInfo
    
    # 证据列表
    evidence: List[EvidenceInfo] = Field(default_factory=list)
    
    # 置信度
    confidence: Optional[float] = None
    
    # 元数据
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ============== L2: Planner 决策 ==============

class PlannerDecision(BaseModel):
    """
    Planner 决策输出（增强版）
    L2 层根据 AccidentCard 输出的结构化决策
    """
    # 核心字段
    planning_intent: PlanningIntentCode
    intent_label: Optional[str] = None  # 中文描述

    # 求解器建议
    solver_candidates: List[str] = Field(default_factory=list, description="候选求解器列表（带排序）")
    preferred_solver: Optional[str] = Field(default=None, description="首选求解器（经过规则校验后可能不采纳）")

    # 目标权重（用于优化）
    objective_weights: Dict[str, float] = Field(
        default_factory=dict,
        description="优化目标权重：max_delay_weight, avg_delay_weight, affected_trains_weight, runtime_weight"
    )

    # 建议参数
    suggested_window_minutes: Optional[int] = Field(default=None, description="建议求解窗口（分钟）")
    affected_corridor_hint: Optional[str] = Field(default=None, description="建议关注的走廊")

    # 状态字段
    need_user_clarification: bool = Field(default=False, description="是否需要用户补充信息")
    confidence: float = Field(default=1.0, description="决策置信度")
    alternatives: List[str] = Field(default_factory=list, description="备选intent列表")

    # 说明
    reasoning: Optional[str] = None


# ============== L3: Solver 策略 ==============

class SolverPolicy(BaseModel):
    """Solver 策略选择"""
    solver_type: SolverTypeCode
    reasoning: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)


# ============== L4: Policy 决策 ==============

class PolicyDecision(BaseModel):
    """Policy Engine 决策"""
    decision: PolicyDecisionType
    reason: str
    confidence: float = 1.0
    suggested_fixes: List[str] = Field(default_factory=list)


# ============== 最终响应 ==============

class WorkflowResponse(BaseModel):
    """
    工作流最终响应 v3.2
    统一所有 API 的响应结构
    """
    # 统一字段
    success: bool
    request_id: str
    schema_version: str = "dispatch_v3_2"
    phase: str = "unknown"  # preprocess/modeling/planning/solving/evaluation
    
    # 流程控制
    can_proceed: bool = True
    needs_more_info: bool = False
    missing_fields: List[str] = Field(default_factory=list)
    
    scene_type_code: Optional[str] = None
    scene_type_label: Optional[str] = None
    
    # 标准化请求（来自 L0 预处理）
    canonical_request: Optional[Dict[str, Any]] = None
    
    # 调度结果
    schedule: Optional[List[Dict[str, Any]]] = None
    metrics: Optional[Dict[str, Any]] = None
    
    # 决策信息
    planner_decision: Optional[Dict[str, Any]] = None
    solver_policy: Optional[Dict[str, Any]] = None
    solver_result: Optional[Dict[str, Any]] = None  # 新增：求解结果
    policy_decision: Optional[Dict[str, Any]] = None
    
    # 验证与评估
    validation_report: Optional[Dict[str, Any]] = None  # 新增：验证报告
    evaluation_report: Optional[Dict[str, Any]] = None  # 新增：评估报告
    
    # LLM 摘要
    llm_summary: Optional[str] = None
    
    # 调试信息
    debug_trace: Optional[Dict[str, Any]] = None
    
    # 消息
    operator_message: str = ""  # 新增：操作员消息
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ============== 预处理调试响应 ==============

class PreprocessDebugResponse(BaseModel):
    """预处理调试响应"""
    request_id: str
    raw_user_request: Dict[str, Any]
    canonical_request: Dict[str, Any]
    evidence_list: List[Dict[str, Any]]
    completeness: Dict[str, Any]
    processing_steps: List[str] = Field(default_factory=list)