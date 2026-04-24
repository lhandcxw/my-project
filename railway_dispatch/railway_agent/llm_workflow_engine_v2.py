# -*- coding: utf-8 -*-
"""
LLM驱动的工作流引擎模块 v2.1（修正版）
基于适配器模式的重构版本

架构说明（与实现一致）：
- L0/L1: 预处理层 + 数据建模层 - Layer1DataModeling（构建 AccidentCard）
- SnapshotBuilder：构建 NetworkSnapshot（在 L1 完成后调用）
- L2：Planner层 - 使用 Layer2Planner
- L3：Solver执行层 - 使用 Layer3Solver
- L4：评估层 - 使用 Layer4Evaluation

正确的流程（与实现一致）：
  用户输入 -> L1 (AccidentCard) -> SnapshotBuilder (NetworkSnapshot) -> L2 -> L3 -> L4

注意：
- 文档中"L0 → SnapshotBuilder → L1"的描述是错误的
- 实际实现是：L1 完成后才能构建 Snapshot
- 统一的真相是：L0/L1 -> AccidentCard -> SnapshotBuilder -> L2 -> L3 -> L4

v2.1 修正：
- 修正文档描述以匹配实际实现
- 明确 SnapshotBuilder 为唯一构建 NetworkSnapshot 的入口
- L1 只负责数据建模（AccidentCard）
- 消除 NetworkSnapshot 的重复构建
"""

from typing import Dict, Any, Optional, List
import logging
from datetime import datetime
import uuid

from models.workflow_models import WorkflowResult, DispatchContextMetadata, SolverResult
from models.preprocess_models import WorkflowResponse
from models.common_enums import RequestSourceType  # 添加导入
from railway_agent.workflow import (
    Layer1DataModeling,
    Layer2Planner,
    Layer3Solver,
    Layer4Evaluation
)
from models.data_loader import load_trains, load_stations, get_trains_pydantic, get_stations_pydantic

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
        self.layer3 = Layer3Solver()
        self.layer4 = Layer4Evaluation()

        # 数据加载 - 同时加载字典格式和Pydantic格式
        # 字典格式：用于L2 Agent的工具调用
        # Pydantic格式：用于L3 Solver的调度器
        self.trains = load_trains()
        self.stations = load_stations()
        self.trains_pydantic = get_trains_pydantic()
        self.stations_pydantic = get_stations_pydantic()

        # L2 Agent 需要列车和车站数据来执行工具调用
        self.layer2 = Layer2Planner(trains=self.trains, stations=self.stations)

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
                logger.debug(f"[对话工作流] 恢复对话状态: {dialogue_id}")
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
            logger.debug("[对话工作流] ========== 步骤1：L1 数据建模层 ==========")
            l1_result = self.layer1.execute(
                user_input=combined_input,
                enable_rag=enable_rag
            )

            accident_card = l1_result["accident_card"]

            # 检查信息完整性
            if l1_result.get("needs_more_info", False):
                missing_fields = l1_result.get("missing_questions", [])
                missing_info = [q["question"] for q in missing_fields]
                logger.debug(f"[对话工作流] 信息不完整，缺少: {missing_fields}")

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
            logger.debug("[对话工作流] 信息完整，继续后续流程")
            self._dialogue_states[dialogue_id]["status"] = "info_complete"

            # 构建调度元数据（无需网络快照）
            dispatch_metadata = DispatchContextMetadata(
                can_solve=True,
                missing_info=[],
                observation_corridor=""
            )

            # 保存完整状态供后续步骤使用
            self._dialogue_states[dialogue_id].update({
                "accident_card": accident_card.model_dump(),
                "dispatch_metadata": dispatch_metadata.model_dump(),
                "l1_result": l1_result,
                "status": "l1_complete"
            })

            # 构建响应结果
            response = {
                "dialogue_id": dialogue_id,
                "status": "l1_complete",
                "message": "信息提取完成",
                "accident_card": accident_card.model_dump(),
                "can_proceed": True,
                "response_source": l1_result.get("response_source", "unknown")
            }

            # 添加调度员操作指南（如果存在）
            if "dispatcher_operations" in l1_result and l1_result["dispatcher_operations"]:
                response["dispatcher_operations"] = l1_result["dispatcher_operations"]
                # 添加格式化后的操作指南文本
                operations_guide = l1_result["dispatcher_operations"]
                response["dispatcher_operations_text"] = self._format_operations_guide(operations_guide)

            return response

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

    def get_dialogue_state(self, dialogue_id: str) -> Optional[Dict[str, Any]]:
        """获取对话状态"""
        return self._dialogue_states.get(dialogue_id)

    def clear_dialogue_state(self, dialogue_id: str):
        """清除对话状态"""
        if dialogue_id in self._dialogue_states:
            del self._dialogue_states[dialogue_id]
            logger.debug(f"[对话工作流] 清除对话状态: {dialogue_id}")

    def execute_full_workflow(
        self,
        user_input: str,
        canonical_request: Optional[Any] = None,
        enable_rag: bool = True
    ) -> WorkflowResult:
        """
        执行完整工作流（自适应反射架构：L1 → L2 → L3 → L4 → [反射 → 重规划]）

        行业最佳实践融合：
        - ReAct 循环：Agent 在 L2 可基于反馈重新推理
        - Reflection 模式：L4 评估不合格时触发 L2 重规划
        - 最大 2 次重规划迭代，防止无限循环
        - 自动选择最优迭代结果返回

        Args:
            user_input: 用户输入
            canonical_request: L0预处理结果（可选）
            enable_rag: 是否启用RAG

        Returns:
            WorkflowResult: 工作流结果
        """
        try:
            # 步骤1：L1 - 数据建模层
            logger.debug("========== 步骤1：L1 数据建模层 ==========")
            l1_result = self.layer1.execute(
                user_input=user_input,
                canonical_request=canonical_request,
                enable_rag=enable_rag
            )

            accident_card = l1_result["accident_card"]

            # 检查是否可以进入求解
            if not accident_card.is_complete:
                logger.debug(f"信息不完整，无法求解: {accident_card.missing_fields}")
                return self._build_incomplete_result(
                    user_input,
                    accident_card,
                    accident_card.missing_fields
                )

            # 多轮迭代求解（反射架构核心）
            iteration_results = []
            max_iterations = 3
            previous_feedback = None

            for iteration in range(max_iterations):
                logger.info(f"========== 迭代 {iteration + 1}/{max_iterations} ==========")

                # L2 - Agent规划层
                logger.debug(f"========== L2 Agent 规划层 (迭代 {iteration + 1}) ==========")
                l2_result = self.layer2.execute(
                    accident_card=accident_card,
                    enable_rag=enable_rag,
                    previous_feedback=previous_feedback
                )

                planning_intent = l2_result["planning_intent"]
                planner_decision = l2_result.get("planner_decision", {})
                agent_executed_solve = l2_result.get("agent_executed_solve", False)

                # L3 - Solver执行层
                if agent_executed_solve and l2_result.get("skill_execution_result"):
                    logger.info(f"[工作流 迭代 {iteration + 1}] L2 Agent 已完成求解，跳过 L3")
                    l3_result = {
                        "skill_execution_result": l2_result["skill_execution_result"],
                        "solver_response": l2_result.get("solver_response"),
                    }
                else:
                    logger.debug(f"========== L3 Solver执行层 (迭代 {iteration + 1}) ==========")
                    l3_result = self.layer3.execute(
                        planning_intent=planning_intent,
                        accident_card=accident_card,
                        trains=self.trains_pydantic,
                        stations=self.stations_pydantic,
                        planner_decision=planner_decision
                    )

                # L4 - 评估层
                logger.debug(f"========== L4 评估层 (迭代 {iteration + 1}) ==========")
                l4_result = self.layer4.execute(
                    skill_execution_result=l3_result["skill_execution_result"],
                    solver_response=l3_result.get("solver_response"),
                    enable_rag=enable_rag
                )

                # 保存本轮结果
                iteration_results.append({
                    "iteration": iteration + 1,
                    "l2_result": l2_result,
                    "l3_result": l3_result,
                    "l4_result": l4_result
                })

                # 检查是否需要反射重规划
                rollback = l4_result.get("rollback_feedback")
                needs_rerun = False
                if rollback:
                    if hasattr(rollback, 'needs_rerun'):
                        needs_rerun = rollback.needs_rerun
                    elif isinstance(rollback, dict):
                        needs_rerun = rollback.get("needs_rerun", False)

                if needs_rerun and iteration < max_iterations - 1:
                    reason = ""
                    fixes = []
                    if hasattr(rollback, 'rollback_reason'):
                        reason = rollback.rollback_reason
                        fixes = rollback.suggested_fixes if hasattr(rollback, 'suggested_fixes') else []
                    elif isinstance(rollback, dict):
                        reason = rollback.get("rollback_reason", "")
                        fixes = rollback.get("suggested_fixes", [])

                    previous_feedback = {
                        "rollback_reason": reason,
                        "suggested_fixes": fixes,
                        "iteration": iteration + 1
                    }
                    logger.info(f"[反射架构] 触发第 {iteration + 2} 轮重规划，原因: {reason}")
                else:
                    if needs_rerun:
                        logger.warning(f"[反射架构] 已达到最大迭代次数({max_iterations})，返回最后一轮结果")
                    break

            # 推断优化目标（从 L2 结果中提取，与 comparator.py 对齐）
            objective = "min_total_delay"
            for result in iteration_results:
                l2 = result.get("l2_result", {})
                planner_decision = l2.get("planner_decision", {})
                solver_config = planner_decision.get("solver_config", {})
                obj = solver_config.get("optimization_objective")
                if obj:
                    objective = obj
                    break

            # 从所有迭代中选择最优结果
            best_iteration = self._select_best_iteration(iteration_results, objective=objective)
            best = iteration_results[best_iteration]

            if len(iteration_results) > 1:
                logger.info(f"[反射架构] 共执行 {len(iteration_results)} 轮，选择第 {best_iteration + 1} 轮作为最优结果")

            return self._build_success_result(
                user_input=user_input,
                accident_card=accident_card,
                l1_result=l1_result,
                l2_result=best["l2_result"],
                l3_result=best["l3_result"],
                l4_result=best["l4_result"],
                iteration_count=len(iteration_results),
                best_iteration=best_iteration + 1
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
        enable_rag: bool = True,
        previous_feedback: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """仅执行L2层"""
        return self.layer2.execute(
            accident_card=accident_card,
            enable_rag=enable_rag,
            previous_feedback=previous_feedback
        )

    def execute_layer3(
        self,
        planning_intent: str,
        accident_card,
        trains: Optional[List[Any]] = None,
        stations: Optional[List[Any]] = None
    ) -> Dict[str, Any]:
        """仅执行L3层"""
        if trains is None:
            trains = self.trains_pydantic
        if stations is None:
            stations = self.stations_pydantic

        return self.layer3.execute(
            planning_intent=planning_intent,
            accident_card=accident_card,
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

    def _select_best_iteration(self, iteration_results: List[Dict[str, Any]], objective: str = "min_total_delay") -> int:
        """
        从多轮迭代中选择最优结果
        评分规则：根据优化目标动态调整权重，与 comparator.py 对齐（越低越好）

        权重配置：
        - min_total_delay（默认）: 总延误权重最高
        - min_max_delay: 最大延误权重最高
        - min_avg_delay: 平均延误权重最高
        """
        if not iteration_results:
            return 0
        if len(iteration_results) == 1:
            return 0

        # 根据优化目标动态权重（与 comparator.py _get_weights_for_criteria 对齐）
        if objective == "min_max_delay":
            weights = {
                "max_delay": 0.35, "total_delay": 0.15, "avg_delay": 0.10,
                "on_time": 0.20, "affected": 0.15, "propagation": 0.05
            }
        elif objective == "min_avg_delay":
            weights = {
                "avg_delay": 0.30, "total_delay": 0.20, "max_delay": 0.10,
                "on_time": 0.20, "affected": 0.15, "propagation": 0.05
            }
        else:  # min_total_delay 或其他
            weights = {
                "total_delay": 0.35, "max_delay": 0.15, "avg_delay": 0.10,
                "on_time": 0.20, "affected": 0.15, "propagation": 0.05
            }

        def _safe_get(obj, attr, default):
            if obj is None:
                return default
            if hasattr(obj, attr):
                return getattr(obj, attr, default)
            if isinstance(obj, dict):
                return obj.get(attr, default)
            return default

        scores = []
        for i, result in enumerate(iteration_results):
            l4 = result["l4_result"]
            eval_report = l4.get("evaluation_report")
            skill_result = result["l3_result"].get("skill_execution_result", {})

            # 提取指标（与 comparator.py _calculate_score 阈值对齐）
            max_delay = skill_result.get('max_delay_minutes', 0)
            avg_delay = skill_result.get('avg_delay_minutes', 0)
            total_delay = skill_result.get('total_delay_minutes', 0)
            affected_count = skill_result.get('affected_trains_count', 0)
            on_time_rate = _safe_get(eval_report, 'on_time_rate', 1.0)
            prop_coeff = _safe_get(eval_report, 'propagation_coefficient', 0.0)

            # 归一化得分（0-100，越低越好），阈值与 comparator.py 一致
            max_delay_score = min(max_delay / 30 * 100, 100)
            avg_delay_score = min(avg_delay / 30 * 100, 100)
            total_delay_score = min(total_delay / 120 * 100, 100)
            on_time_score = (1 - on_time_rate) * 100
            affected_score = min(affected_count / 10 * 100, 100)
            propagation_score = min(prop_coeff / 2 * 100, 100)

            score = (
                max_delay_score * weights["max_delay"] +
                avg_delay_score * weights["avg_delay"] +
                total_delay_score * weights["total_delay"] +
                on_time_score * weights["on_time"] +
                affected_score * weights["affected"] +
                propagation_score * weights["propagation"]
            )

            scores.append(score)
            logger.debug(f"[反射架构] 迭代 {i + 1} 评分: {score:.2f} (objective={objective})")

        best_idx = scores.index(min(scores))
        logger.info(f"[反射架构] 最优迭代: 第 {best_idx + 1} 轮 (objective={objective}, score={scores[best_idx]:.2f})")
        return best_idx

    def _build_success_result(
        self,
        user_input: str,
        accident_card,
        l1_result: Dict[str, Any],
        l2_result: Dict[str, Any],
        l3_result: Dict[str, Any],
        l4_result: Dict[str, Any],
        iteration_count: int = 1,
        best_iteration: int = 1
    ) -> WorkflowResult:
        """构建成功结果"""
        # 处理 solver_response 转换为 SolverResult
        solver_response = l3_result.get("solver_response")
        solver_result = None
        if solver_response:
            # 【修复】同时支持 schedule 和 optimized_schedule
            schedule = solver_response.get("schedule") or solver_response.get("optimized_schedule", [])
            # 确保 schedule 是有效的（不转换为 list）
            if not schedule:
                schedule = {}
            solver_result = SolverResult(
                success=solver_response.get("success", True),
                schedule=schedule,
                metrics=solver_response.get("metrics", {}),
                solving_time_seconds=solver_response.get("solving_time_seconds", 0.0),
                solver_type=solver_response.get("solver_type", "unknown"),
                error_message=solver_response.get("error")
            )

        # 处理 structured_output 和 rollback_feedback
        rollback_feedback = l4_result.get("rollback_feedback")
        structured_output = l4_result.get("structured_output")
        # 确保是 dict 格式
        if hasattr(rollback_feedback, 'model_dump'):
            rollback_feedback = rollback_feedback.model_dump()
        if hasattr(structured_output, 'model_dump'):
            structured_output = structured_output.model_dump()

        return WorkflowResult(
            success=True,
            scene_spec=None,
            task_plan=None,
            solver_result=solver_result,
            evaluation_report=l4_result.get("evaluation_report"),
            ranking_result=l4_result.get("ranking_result"),
            structured_output=structured_output,
            rollback_feedback=rollback_feedback,
            message=f"调度完成: {l4_result.get('policy_decision', {}).get('reason', '') if isinstance(l4_result.get('policy_decision'), dict) else '调度完成'}",
            debug_trace={
                "user_input": user_input,
                "accident_card": accident_card.model_dump(),
                "planning_intent": l2_result["planning_intent"],
                "skill_dispatch": l2_result["skill_dispatch"],
                "solver_result": l3_result["skill_execution_result"],
                "evaluation_report": l4_result.get("evaluation_report").model_dump() if l4_result.get("evaluation_report") else None,
                "policy_decision": l4_result.get("policy_decision"),
                "llm_summary": l4_result.get("llm_summary", ""),
                "natural_language_plan": l4_result.get("natural_language_plan", ""),
                "dispatcher_operations": l1_result.get("dispatcher_operations", {}),
                "reflection_info": {
                    "total_iterations": iteration_count,
                    "best_iteration": best_iteration,
                    "architecture": "Adaptive Reflective Dispatch Orchestrator (ARDO)"
                }
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
