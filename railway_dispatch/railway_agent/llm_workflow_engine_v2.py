# -*- coding: utf-8 -*-
"""
LLM驱动的工作流引擎模块 v2.1（修正版）
基于适配器模式的重构版本

架构说明（修正后）：
- L0：预处理层 - 在 preprocessing/ 模块实现
- SnapshotBuilder：构建 NetworkSnapshot（确定性逻辑）
- L1：数据建模层 - 使用 Layer1DataModeling（只构建 AccidentCard）
- L2：Planner层 - 使用 Layer2Planner
- L3：Solver执行层 - 使用 Layer3Solver
- L4：评估层 - 使用 Layer4Evaluation

v2.1 修正：
- 正确的流程：L0 → SnapshotBuilder → L1 → L2 → L3 → L4
- 明确 SnapshotBuilder 为唯一构建 NetworkSnapshot 的入口
- L1 只负责数据建模（AccidentCard）
- 消除 NetworkSnapshot 的重复构建
"""

from typing import Dict, Any, Optional, List
import logging
from datetime import datetime
import uuid

from models.workflow_models import WorkflowResult, DispatchContextMetadata
from models.preprocess_models import WorkflowResponse
from railway_agent.workflow import (
    Layer1DataModeling,
    Layer2Planner,
    Layer3Solver,
    Layer4Evaluation
)
from railway_agent.snapshot_builder import get_snapshot_builder
from models.data_loader import load_trains, load_stations

logger = logging.getLogger(__name__)


class LLMWorkflowEngineV2:
    """
    LLM工作流引擎 v2.2（对话式交互版）
    正确的流程：L0 → SnapshotBuilder → L1 → [信息补全对话] → L2 → L3 → L4

    v2.2更新：
    - 支持信息不完整时的对话补全
    - 增加对话状态管理
    """

    def __init__(self):
        """初始化工作流引擎"""
        self.layer1 = Layer1DataModeling()
        self.layer2 = Layer2Planner()
        self.layer3 = Layer3Solver()
        self.layer4 = Layer4Evaluation()
        self.snapshot_builder = get_snapshot_builder()

        # 数据加载
        self.trains = load_trains()
        self.stations = load_stations()

        # 对话状态存储（用于多轮对话）
        self._dialogue_states: Dict[str, Dict[str, Any]] = {}

    def start_dialogue_workflow(
        self,
        user_input: str,
        dialogue_id: Optional[str] = None,
        enable_rag: bool = True
    ) -> Dict[str, Any]:
        """
        启动对话式工作流（阶段1实现）

        流程：
        1. L1提取信息
        2. 检查信息完整性
        3. 如果完整，继续后续流程
        4. 如果不完整，返回询问问题，保存对话状态

        Args:
            user_input: 用户输入
            dialogue_id: 对话ID（用于多轮对话）
            enable_rag: 是否启用RAG

        Returns:
            Dict: 包含处理结果或询问问题的字典
        """
        try:
            # 如果有对话ID，尝试恢复之前的状态并合并信息
            if dialogue_id and dialogue_id in self._dialogue_states:
                logger.info(f"[对话工作流] 恢复对话状态: {dialogue_id}")
                previous_state = self._dialogue_states[dialogue_id]
                # 合并用户新输入到之前的信息中
                combined_input = self._merge_user_input(
                    previous_state.get("original_input", ""),
                    previous_state.get("missing_info", []),
                    user_input
                )
            else:
                # 新对话
                dialogue_id = dialogue_id or str(uuid.uuid4())
                combined_input = user_input
                self._dialogue_states[dialogue_id] = {
                    "dialogue_id": dialogue_id,
                    "original_input": user_input,
                    "status": "in_progress"
                }

            # 步骤1：L1数据建模
            logger.info("[对话工作流] ========== 步骤1：L1 数据建模层 ==========")
            l1_result = self.layer1.execute(
                user_input=combined_input,
                enable_rag=enable_rag
            )

            accident_card = l1_result["accident_card"]

            # 检查信息完整性
            if not accident_card.is_complete:
                # 信息不完整，保存状态并返回询问
                missing_fields = accident_card.missing_fields or []
                logger.info(f"[对话工作流] 信息不完整，缺少: {missing_fields}")

                # 保存对话状态
                self._dialogue_states[dialogue_id].update({
                    "accident_card": accident_card.model_dump(),
                    "missing_info": missing_fields,
                    "l1_result": l1_result,
                    "status": "waiting_for_info"
                })

                # 生成询问问题
                questions = self._generate_questions(missing_fields)

                return {
                    "dialogue_id": dialogue_id,
                    "status": "incomplete",
                    "message": f"信息不完整，请补充以下信息: {', '.join(missing_fields)}",
                    "questions": questions,
                    "current_accident_card": accident_card.model_dump(),
                    "response_source": l1_result.get("response_source", "unknown")
                }

            # 信息完整，继续后续流程
            logger.info("[对话工作流] 信息完整，继续后续流程")
            self._dialogue_states[dialogue_id]["status"] = "info_complete"

            # 构建NetworkSnapshot
            network_snapshot = self._build_network_snapshot(accident_card)

            # 构建调度元数据
            dispatch_metadata = DispatchContextMetadata(
                can_solve=True,
                missing_info=[],
                observation_corridor=network_snapshot.solving_window.get("observation_corridor", "")
            )

            # 保存完整状态供后续步骤使用
            self._dialogue_states[dialogue_id].update({
                "accident_card": accident_card.model_dump(),
                "network_snapshot": network_snapshot.model_dump(),
                "dispatch_metadata": dispatch_metadata.model_dump(),
                "l1_result": l1_result,
                "status": "l1_complete"
            })

            return {
                "dialogue_id": dialogue_id,
                "status": "l1_complete",
                "message": "信息提取完成",
                "accident_card": accident_card.model_dump(),
                "can_proceed": True,
                "response_source": l1_result.get("response_source", "unknown")
            }

        except Exception as e:
            logger.error(f"[对话工作流] 启动失败: {e}", exc_info=True)
            return {
                "dialogue_id": dialogue_id,
                "status": "error",
                "message": f"工作流启动失败: {str(e)}"
            }

    def continue_dialogue_workflow(
        self,
        dialogue_id: str,
        user_input: str,
        enable_rag: bool = True
    ) -> Dict[str, Any]:
        """
        继续对话式工作流（用户补充信息后调用）

        Args:
            dialogue_id: 对话ID
            user_input: 用户补充的信息
            enable_rag: 是否启用RAG

        Returns:
            Dict: 处理结果
        """
        # 检查对话状态是否存在
        if dialogue_id not in self._dialogue_states:
            return {
                "dialogue_id": dialogue_id,
                "status": "error",
                "message": "对话不存在或已过期"
            }

        # 重新调用start_dialogue_workflow，它会合并信息
        return self.start_dialogue_workflow(
            user_input=user_input,
            dialogue_id=dialogue_id,
            enable_rag=enable_rag
        )

    def _merge_user_input(
        self,
        original_input: str,
        missing_fields: List[str],
        new_input: str
    ) -> str:
        """
        合并用户原始输入和新补充的信息

        Args:
            original_input: 原始输入
            missing_fields: 之前缺失的字段
            new_input: 新输入

        Returns:
            str: 合并后的输入
        """
        # 简单合并：原始输入 + 新输入
        # 可以根据missing_fields做更智能的合并
        if not original_input:
            return new_input
        if not new_input:
            return original_input
        return f"{original_input} {new_input}"

    def _generate_questions(self, missing_fields: List[str]) -> List[str]:
        """
        根据缺失字段生成询问问题

        Args:
            missing_fields: 缺失字段列表

        Returns:
            List[str]: 问题列表
        """
        question_map = {
            "列车号": "请提供受影响的列车号（如G1563、D1234）：",
            "位置": "请提供事发位置或车站（如石家庄、保定东）：",
            "事件类型": "请描述事件类型（如大风、暴雨、设备故障）：",
            "延误时间": "请提供预计延误时间（分钟）："
        }

        questions = []
        for field in missing_fields:
            questions.append(question_map.get(field, f"请提供{field}："))

        return questions

    def _build_network_snapshot(self, accident_card):
        """构建网络快照"""
        from models.preprocess_models import CanonicalDispatchRequest, LocationInfo, CompletenessCheck

        temp_request = CanonicalDispatchRequest(
            request_id=str(uuid.uuid4()),
            scene_type_code="TEMP_SPEED_LIMIT" if accident_card.scene_category == "临时限速" else "SUDDEN_FAILURE",
            scene_type_label=accident_card.scene_category,
            location=LocationInfo(
                station_code=accident_card.location_code or "SJP",
                station_name=accident_card.location_name or "石家庄"
            ),
            affected_train_ids=accident_card.affected_train_ids or [],
            event_time=datetime.now().isoformat(),
            expected_duration_minutes=accident_card.expected_duration or 10,
            reported_delay_seconds=int((accident_card.expected_duration or 10) * 60),
            completeness={
                "can_enter_solver": accident_card.is_complete,
                "missing_fields": accident_card.missing_fields
            }
        )
        return self.snapshot_builder.build(temp_request)

    def get_dialogue_state(self, dialogue_id: str) -> Optional[Dict[str, Any]]:
        """获取对话状态"""
        return self._dialogue_states.get(dialogue_id)

    def clear_dialogue_state(self, dialogue_id: str):
        """清除对话状态"""
        if dialogue_id in self._dialogue_states:
            del self._dialogue_states[dialogue_id]
            logger.info(f"[对话工作流] 清除对话状态: {dialogue_id}")

    def execute_full_workflow(
        self,
        user_input: str,
        canonical_request: Optional[Any] = None,
        enable_rag: bool = True
    ) -> WorkflowResult:
        """
        执行完整工作流（修正流程：L0 → SnapshotBuilder → L1 → L2 → L3 → L4）

        Args:
            user_input: 用户输入
            canonical_request: L0预处理结果（可选）
            enable_rag: 是否启用RAG

        Returns:
            WorkflowResult: 工作流结果
        """
        try:
            # 步骤1：L1 - 数据建模层（只构建 AccidentCard）
            logger.info("========== 步骤1：L1 数据建模层 ==========")
            l1_result = self.layer1.execute(
                user_input=user_input,
                canonical_request=canonical_request,
                enable_rag=enable_rag
            )

            accident_card = l1_result["accident_card"]

            # 步骤2：SnapshotBuilder - 构建 NetworkSnapshot
            logger.info("========== 步骤2：SnapshotBuilder 构建 NetworkSnapshot ==========")
            if canonical_request:
                network_snapshot = self.snapshot_builder.build(canonical_request)
            else:
                # 如果没有 canonical_request，从用户输入构建一个临时的
                from models.preprocess_models import CanonicalDispatchRequest, LocationInfo, CompletenessInfo
                from datetime import datetime
                import uuid

                # 从accident_card提取信息构建canonical_request
                from models.common_enums import SceneTypeCode
                scene_type_code = SceneTypeCode.TEMP_SPEED_LIMIT if accident_card.scene_category == "临时限速" else SceneTypeCode.SUDDEN_FAILURE
                if accident_card.scene_category == "区间封锁":
                    scene_type_code = SceneTypeCode.SECTION_INTERRUPT
                    
                temp_request = CanonicalDispatchRequest(
                    request_id=str(uuid.uuid4()),
                    scene_type_code=scene_type_code,
                    scene_type_label=accident_card.scene_category,
                    location=LocationInfo(
                        station_code=accident_card.location_code or "SJP",
                        station_name=accident_card.location_name or "石家庄"
                    ),
                    affected_train_ids=accident_card.affected_train_ids,
                    event_time=datetime.now().isoformat(),
                    expected_duration_minutes=accident_card.expected_duration or 10,
                    reported_delay_seconds=int((accident_card.expected_duration or 10) * 60),
            completeness=CompletenessInfo(
                can_enter_solver=accident_card.is_complete,
                missing_fields=accident_card.missing_fields
            )
                )
                network_snapshot = self.snapshot_builder.build(temp_request)

            # 构建调度元数据
            dispatch_metadata = DispatchContextMetadata(
                can_solve=accident_card.is_complete,
                missing_info=accident_card.missing_fields,
                observation_corridor=network_snapshot.solving_window.get("observation_corridor", "")
            )

            # 检查是否可以进入求解
            if not dispatch_metadata.can_solve:
                logger.info(f"信息不完整，无法求解: {dispatch_metadata.missing_info}")
                return self._build_incomplete_result(
                    user_input,
                    accident_card,
                    dispatch_metadata.missing_info
                )

            # 步骤3：L2 - Planner层
            logger.info("========== 步骤3：L2 Planner层 ==========")
            l2_result = self.layer2.execute(
                accident_card=accident_card,
                network_snapshot=network_snapshot,
                dispatch_metadata=dispatch_metadata,
                enable_rag=enable_rag
            )

            planning_intent = l2_result["planning_intent"]
            skill_dispatch = l2_result["skill_dispatch"]

            # 步骤4：L3 - Solver执行层
            logger.info("========== 步骤4：L3 Solver执行层 ==========")
            l3_result = self.layer3.execute(
                planning_intent=planning_intent,
                accident_card=accident_card,
                network_snapshot=network_snapshot,
                trains=self.trains,
                stations=self.stations
            )

            # 步骤5：L4 - 评估层
            logger.info("========== 步骤5：L4 评估层 ==========")
            l4_result = self.layer4.execute(
                skill_execution_result=l3_result["skill_execution_result"],
                solver_response=l3_result.get("solver_response"),
                enable_rag=enable_rag
            )

            # 构建最终结果
            return self._build_success_result(
                user_input=user_input,
                accident_card=accident_card,
                network_snapshot=network_snapshot,
                l1_result=l1_result,
                l2_result=l2_result,
                l3_result=l3_result,
                l4_result=l4_result
            )

        except Exception as e:
            logger.error(f"工作流执行失败: {e}", exc_info=True)
            return self._build_error_result(str(e))

    def execute_layer1(
        self,
        user_input: str,
        canonical_request: Optional[Any] = None,
        enable_rag: bool = True
    ) -> Dict[str, Any]:
        """仅执行L1层"""
        return self.layer1.execute(
            user_input=user_input,
            canonical_request=canonical_request,
            enable_rag=enable_rag
        )

    def execute_layer2(
        self,
        accident_card,
        network_snapshot,
        dispatch_metadata,
        enable_rag: bool = True
    ) -> Dict[str, Any]:
        """仅执行L2层"""
        return self.layer2.execute(
            accident_card=accident_card,
            network_snapshot=network_snapshot,
            dispatch_metadata=dispatch_metadata,
            enable_rag=enable_rag
        )

    def execute_layer3(
        self,
        planning_intent: str,
        accident_card,
        network_snapshot,
        trains: Optional[List[Any]] = None,
        stations: Optional[List[Any]] = None
    ) -> Dict[str, Any]:
        """仅执行L3层"""
        if trains is None:
            trains = self.trains
        if stations is None:
            stations = self.stations

        return self.layer3.execute(
            planning_intent=planning_intent,
            accident_card=accident_card,
            network_snapshot=network_snapshot,
            trains=trains,
            stations=stations
        )

    def execute_layer4(
        self,
        skill_execution_result: Dict[str, Any],
        solver_response: Any,
        enable_rag: bool = False
    ) -> Dict[str, Any]:
        """仅执行L4层"""
        return self.layer4.execute(
            skill_execution_result=skill_execution_result,
            solver_response=solver_response,
            enable_rag=enable_rag
        )

    def _build_success_result(
        self,
        user_input: str,
        accident_card,
        network_snapshot,
        l1_result: Dict[str, Any],
        l2_result: Dict[str, Any],
        l3_result: Dict[str, Any],
        l4_result: Dict[str, Any]
    ) -> WorkflowResult:
        """构建成功结果"""
        return WorkflowResult(
            success=True,
            scene_spec=None,
            task_plan=None,
            solver_result=l3_result.get("solver_response"),
            evaluation_report=l4_result.get("evaluation_report"),
            ranking_result=l4_result.get("ranking_result"),
            structured_output=l4_result.get("rollback_feedback"),
            rollback_feedback=l4_result.get("rollback_feedback"),
            message=f"调度完成: {l4_result.get('policy_decision', {}).get('reason', '')}",
            debug_trace={
                "user_input": user_input,
                "accident_card": accident_card.model_dump(),
                "network_snapshot": network_snapshot.model_dump(),
                "planning_intent": l2_result["planning_intent"],
                "skill_dispatch": l2_result["skill_dispatch"],
                "solver_result": l3_result["skill_execution_result"],
                "evaluation_report": l4_result.get("evaluation_report").model_dump() if l4_result.get("evaluation_report") else None,
                "policy_decision": l4_result.get("policy_decision")
            }
        )

    def _build_incomplete_result(
        self,
        user_input: str,
        accident_card,
        missing_info: List[str]
    ) -> WorkflowResult:
        """构建信息不完整结果"""
        return WorkflowResult(
            success=False,
            message=f"信息不完整，缺少: {', '.join(missing_info)}",
            debug_trace={
                "user_input": user_input,
                "accident_card": accident_card.model_dump(),
                "missing_info": missing_info
            }
        )

    def _build_error_result(self, error_message: str) -> WorkflowResult:
        """构建错误结果"""
        return WorkflowResult(
            success=False,
            message=f"工作流执行失败: {error_message}",
            error=error_message,
            debug_trace={
                "error": error_message
            }
        )


# ============== 兼容性接口 ==============

def create_workflow_engine() -> LLMWorkflowEngineV2:
    """
    创建工作流引擎实例

    Returns:
        LLMWorkflowEngineV2: 工作流引擎实例
    """
    return LLMWorkflowEngineV2()


# 导出旧版本LLMCaller（保持向后兼容）
from .adapters.llm_adapter import LLMCaller, get_llm_caller
