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
    """
    场景类型枚举（固定枚举值，不漂移）
    基于真实数据：13个车站的京广高铁北京西→安阳东
    """
    TEMPORARY_SPEED_LIMIT = "临时限速"      # 临时限速：如暴雨限速60km/h
    SUDDEN_FAILURE = "突发故障"            # 突发故障：设备/信号/接触网故障
    SECTION_INTERRUPT = "区间封锁"          # 区间封锁：区间中断无法通行


# ============== 第一层：数据建模层模型 ==============

class AccidentCard(BaseModel):
    """
    事故卡片模型
    第一层输出：从调度员自然语言中提取的故障信息
    结合真实数据特征（147列列车/13个车站的京广高铁）
    """
    fault_type: str = Field(description="原始故障类型: 暴雨/设备故障/接触网/信号故障等")
    scene_category: str = Field(description="场景类别: 临时限速/突发故障/区间封锁（枚举值固定）")
    start_time: Optional[datetime] = Field(default=None, description="开始时间")
    expected_duration: Optional[float] = Field(default=None, description="预计持续时长(分钟)")
    affected_section: str = Field(default="", description="直接影响区段: 如BJX-DJK, XSD-BDD")

    # 位置信息（结合真实数据：13个车站+区间）
    location_type: str = Field(default="station", description="位置类型: station(车站) / section(区间)")
    location_code: str = Field(default="", description="位置编码: 如XSD, BDD, DJK-GBD")
    location_name: str = Field(default="", description="位置名称: 如徐水东, 保定东")

    # 影响范围（结合真实数据：147列列车）
    affected_train_ids: List[str] = Field(default_factory=list, description="受影响的列车ID列表")
    affected_train_count: int = Field(default=0, description="受影响列车数量")

    # 严重程度
    fault_severity: str = Field(default="minor", description="故障严重程度: minor/major/critical")

    # 信息完整性判定
    is_complete: bool = Field(default=False, description="信息是否完整")
    missing_fields: List[str] = Field(default_factory=list, description="缺失信息列表")

    # 判定规则（导师建议）：
    # 可以进入求解条件：
    #   已确定：scene_category + start_time + affected_section
    #   且运行状态快照包含：列车编号 + 当前位置 + 当前晚点 + 车站容量 + 区间状态 + headway


class NetworkSnapshot(BaseModel):
    """
    网络快照模型
    从原始运行图中切取的子图（由 SnapshotBuilder 确定性构建）
    结合真实数据特征（13个车站的京广高铁走廊）
    v3.2: 新增 candidate_train_ids, excluded_train_ids, selection_reason
    """
    snapshot_time: datetime = Field(description="快照时刻")

    # 求解窗口配置（根据观察走廊+规划时间窗）
    solving_window: Dict[str, Any] = Field(
        default_factory=dict,
        description="求解窗口: {corridor_id, window_start, window_end, selection_reason}"
    )

    # 候选列车（由 SnapshotBuilder 确定）
    candidate_train_ids: List[str] = Field(
        default_factory=list,
        description="候选调整列车ID列表"
    )
    excluded_train_ids: List[str] = Field(
        default_factory=list,
        description="排除的列车ID列表（如已通过的列车）"
    )

    # 窗口内列车集合（从147列中裁剪）
    # 注意：这是"候选调整列车集合"，不是"最终受影响列车"
    trains: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="窗口内列车: [{train_id, current_position, current_delay, ...}]"
    )
    train_count: int = Field(default=0, description="窗口内列车数量")

    # 车站容量（13个车站的真实容量）
    stations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="相关车站: [{station_code, station_name, track_count, current_occupancy}]"
    )

    # 区间状态（12个区间：BJX-DJK, DJK-ZBD, ...）
    sections: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="相关区间: [{section_id, from_station, to_station, status, remaining_capacity}]"
    )

    # 区间追踪间隔（headway约束）
    headways: Dict[str, str] = Field(
        default_factory=dict,
        description="追踪间隔: {section_key: headway_value} 如 {BJX-DJK: 180}"
    )

    # 当前晚点情况
    current_delays: Dict[str, float] = Field(
        default_factory=dict,
        description="当前晚点(秒): {train_id: delay_seconds}"
    )


class DispatchContextMetadata(BaseModel):
    """
    调度上下文元数据
    包含是否可以进入求解的判定信息
    """
    can_solve: bool = Field(default=False, description="是否可以进入求解")
    missing_info: List[str] = Field(default_factory=list, description="缺失信息")
    planning_horizon: Optional[Dict[str, Any]] = Field(default=None, description="规划时域")
    observation_corridor: Optional[str] = Field(default=None, description="观察走廊")


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
    结合真实数据特征（147列高速动车组/13个车站）
    """
    # 第一层新增字段
    accident_card: Optional[AccidentCard] = Field(default=None, description="事故卡片")
    network_snapshot: Optional[NetworkSnapshot] = Field(default=None, description="网络快照")
    dispatch_context_metadata: Optional[DispatchContextMetadata] = Field(default=None, description="调度上下文元数据(含求解判定)")

    scene_spec: SceneSpec = Field(description="场景规格")

    # 真实数据特征（147列高速动车组）
    trains: List[Any] = Field(default_factory=list, description="列车数据列表（G开头车次, 5-9停靠站)")
    train_count: int = Field(default=0, description="列车总数(147)")

    # 真实数据特征（13个车站）
    stations: List[Any] = Field(default_factory=list, description="车站数据列表(BJX→AYD)")
    station_count: int = Field(default=0, description="车站总数(13)")

    # 受影响列车（从147列中识别）
    affected_trains: List[AffectedTrain] = Field(default_factory=list, description="受影响列车列表")

    # 数据加载器信息
    data_loader_info: Optional[Dict[str, Any]] = Field(default=None, description="数据加载器信息")

    # 运行图信息（用于子图切割）
    timetable_info: Optional[Dict[str, Any]] = Field(default=None, description="运行图信息")

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
    # 第四层新增字段
    evaluation_report: Optional["EvaluationReport"] = Field(default=None, description="方案评估报告")
    ranking_result: Optional["RankingResult"] = Field(default=None, description="方案排序结果")
    structured_output: Optional["StructuredOutput"] = Field(default=None, description="结构化调度结果")
    rollback_feedback: Optional["RollbackFeedback"] = Field(default=None, description="回退反馈单")
    debug_trace: Dict[str, Any] = Field(default_factory=dict, description="调试追踪信息")
    message: str = Field(default="", description="结果消息")
    error: Optional[str] = Field(default=None, description="错误信息")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")


# ============== 第四层：结果输出与评估层模型 ==============

class EvaluationReport(BaseModel):
    """
    方案评估单
    第四层输出：对候选方案的多维度评估
    """
    solution_id: str = Field(description="方案编号")
    is_feasible: bool = Field(description="是否可行")
    total_delay_minutes: float = Field(default=0.0, description="总晚点(分钟)")
    max_delay_minutes: float = Field(default=0.0, description="最大晚点(分钟)")
    solving_time_seconds: float = Field(default=0.0, description="运行时间(秒)")
    risk_warnings: List[str] = Field(default_factory=list, description="风险提示")
    constraint_satisfaction: Dict[str, bool] = Field(default_factory=dict, description="约束满足情况")
    llm_summary: str = Field(default="", description="LLM生成的评估摘要")
    feasibility_score: float = Field(default=0.8, description="可行性评分(0-1)")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")


class CandidateSolution(BaseModel):
    """
    候选方案
    包含方案编号、来源skill、调度方案等
    """
    solution_id: str = Field(description="方案编号")
    source_skill: str = Field(description="来源skill")
    adjusted_schedule: List[Dict[str, Any]] = Field(default_factory=list, description="调整后的运行方案")
    objective_value: Optional[float] = Field(default=None, description="目标函数值")
    constraint_satisfaction: Dict[str, bool] = Field(default_factory=dict, description="约束满足情况")


class SolutionSummary(BaseModel):
    """
    求解结果摘要
    第四层输出：推荐的候选方案及说明
    """
    recommended_solution: Optional[str] = Field(default=None, description="推荐候选方案编号")
    alternative_solutions: List[str] = Field(default_factory=list, description="备选方案编号列表")
    explanation: str = Field(default="", description="求解说明")


class RankingResult(BaseModel):
    """
    方案排序结果
    第四层输出：推荐方案、备选方案、排序依据
    """
    recommended_solution: Optional[CandidateSolution] = Field(default=None, description="推荐方案")
    alternative_solutions: List[CandidateSolution] = Field(default_factory=list, description="备选方案列表")
    ranking_criteria: str = Field(description="排序依据")
    ranking_details: List[Dict[str, Any]] = Field(default_factory=list, description="排序详情")


class StructuredOutput(BaseModel):
    """
    结构化调度结果
    第四层输出：最终输出给调度员的结果
    """
    solution_id: str = Field(description="方案编号")
    adjusted_schedule: List[Dict[str, Any]] = Field(default_factory=list, description="调整后的运行方案")
    key_actions: List[str] = Field(default_factory=list, description="关键调整动作")
    impact_description: str = Field(default="", description="主要影响说明")


class RollbackFeedback(BaseModel):
    """
    回退反馈单
    第四层输出：是否需要重新求解及原因
    """
    needs_rerun: bool = Field(default=False, description="是否需要重新求解")
    rollback_reason: str = Field(default="", description="回退原因")
    suggested_fixes: List[str] = Field(default_factory=list, description="建议修复项")