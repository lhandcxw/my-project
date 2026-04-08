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
    LLM工作流引擎 v2.1（修正版）
    正确的流程：L0 → SnapshotBuilder → L1 → L2 → L3 → L4
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
                # 如果没有 canonical_request，使用默认时间窗口
                network_snapshot = self.snapshot_builder.build(
                    canonical_request,
                    time_window={"start": "06:00", "end": "24:00"}
                )

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
