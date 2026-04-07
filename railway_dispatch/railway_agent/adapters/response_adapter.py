# -*- coding: utf-8 -*-
"""
Response 适配器
统一 API 响应格式
"""

from typing import Optional, Dict, Any, List
import logging
import json

from models.preprocess_models import WorkflowResponse
from models.common_enums import SceneTypeCode, PolicyDecisionType

logger = logging.getLogger(__name__)


class ResponseAdapter:
    """
    Response 适配器
    统一 API 响应格式
    """
    
    def build_success_response(
        self,
        request_id: str,
        scene_type_code: Optional[str] = None,
        scene_type_label: Optional[str] = None,
        schedule: Optional[List[Dict[str, Any]]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        planner_decision: Optional[Dict[str, Any]] = None,
        solver_policy: Optional[Dict[str, Any]] = None,
        policy_decision: Optional[Dict[str, Any]] = None,
        evaluation: Optional[Dict[str, Any]] = None,
        llm_summary: Optional[str] = None,
        debug_trace: Optional[Dict[str, Any]] = None,
        message: str = "成功"
    ) -> WorkflowResponse:
        """
        构建成功响应
        
        Returns:
            WorkflowResponse: 工作流响应
        """
        return WorkflowResponse(
            success=True,
            request_id=request_id,
            scene_type_code=scene_type_code,
            scene_type_label=scene_type_label,
            schedule=schedule,
            metrics=metrics,
            planner_decision=planner_decision,
            solver_policy=solver_policy,
            policy_decision=policy_decision,
            evaluation=evaluation,
            llm_summary=llm_summary,
            debug_trace=debug_trace,
            message=message
        )
    
    def build_error_response(
        self,
        request_id: str,
        error: str,
        message: str = "失败",
        scene_type_code: Optional[str] = None,
        scene_type_label: Optional[str] = None
    ) -> WorkflowResponse:
        """
        构建错误响应
        
        Returns:
            WorkflowResponse: 工作流响应
        """
        return WorkflowResponse(
            success=False,
            request_id=request_id,
            scene_type_code=scene_type_code,
            scene_type_label=scene_type_label,
            schedule=None,
            metrics=None,
            planner_decision=None,
            solver_policy=None,
            policy_decision=None,
            evaluation=None,
            llm_summary=None,
            debug_trace=None,
            message=message,
            error=error
        )
    
    def build_incomplete_response(
        self,
        request_id: str,
        missing_fields: List[str],
        scene_type_code: Optional[str] = None,
        scene_type_label: Optional[str] = None
    ) -> WorkflowResponse:
        """
        构建信息不完整响应
        
        Returns:
            WorkflowResponse: 工作流响应
        """
        return WorkflowResponse(
            success=False,
            request_id=request_id,
            scene_type_code=scene_type_code,
            scene_type_label=scene_type_label,
            schedule=None,
            metrics=None,
            planner_decision=None,
            solver_policy=None,
            policy_decision=None,
            evaluation=None,
            llm_summary=None,
            debug_trace=None,
            message=f"信息不完整，缺少字段: {', '.join(missing_fields)}",
            error="incomplete_request",
            metadata={"missing_fields": missing_fields}
        )
    
    def to_dict(self, response: WorkflowResponse) -> Dict[str, Any]:
        """
        转换为字典（用于 JSON 序列化）
        
        Args:
            response: 工作流响应
            
        Returns:
            Dict: 字典格式
        """
        return response.model_dump(mode='json')
    
    def to_json(self, response: WorkflowResponse) -> str:
        """
        转换为 JSON 字符串
        
        Args:
            response: 工作流响应
            
        Returns:
            str: JSON 字符串
        """
        return json.dumps(
            response.model_dump(mode='json'),
            ensure_ascii=False,
            indent=2
        )


# 全局实例
_response_adapter: Optional[ResponseAdapter] = None


def get_response_adapter() -> ResponseAdapter:
    """获取 Response 适配器实例"""
    global _response_adapter
    if _response_adapter is None:
        _response_adapter = ResponseAdapter()
    return _response_adapter