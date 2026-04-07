# -*- coding: utf-8 -*-
"""
完整性门禁
判断 CanonicalDispatchRequest 是否可进入 solver
"""

from typing import List, Dict, Any
import logging

from models.preprocess_models import CanonicalDispatchRequest, CompletenessInfo
from models.common_enums import SceneTypeCode

logger = logging.getLogger(__name__)


class CompletenessGate:
    """
    完整性门禁
    检查请求是否满足进入求解器的条件
    """
    
    # 必要字段定义
    REQUIRED_FIELDS = {
        "scene_type_code": "场景类型",
        "location": "位置信息",
        "location.station_code": "车站代码"
    }
    
    # 各场景的额外必要字段
    SCENE_REQUIREMENTS = {
        SceneTypeCode.TEMP_SPEED_LIMIT: ["speed_limit_kph"],  # 临时限速需要限速值
        SceneTypeCode.SUDDEN_FAILURE: [],                      # 突发故障只需要基本信息
        SceneTypeCode.SECTION_INTERRUPT: []                    # 区间封锁只需要基本信息
    }
    
    def check(self, canonical_request: CanonicalDispatchRequest) -> CompletenessInfo:
        """
        检查请求完整性
        
        Args:
            canonical_request: 标准化调度请求
            
        Returns:
            CompletenessInfo: 完整性判定结果
        """
        logger.info(f"CompletenessGate 检查请求: {canonical_request.request_id}")
        
        missing_fields = []
        
        # 检查场景类型
        if not canonical_request.scene_type_code:
            missing_fields.append("scene_type_code")
        
        # 检查位置信息
        if not canonical_request.location:
            missing_fields.append("location")
        elif not canonical_request.location.station_code:
            missing_fields.append("location.station_code")
        
        # 检查列车信息
        if not canonical_request.affected_train_ids:
            missing_fields.append("affected_train_ids")
        
        # 根据场景类型检查额外字段
        if canonical_request.scene_type_code:
            extra_required = self.SCENE_REQUIREMENTS.get(
                canonical_request.scene_type_code, 
                []
            )
            for field in extra_required:
                if field == "speed_limit_kph":
                    if not canonical_request.speed_limit_kph:
                        missing_fields.append("speed_limit_kph")
        
        # 判断是否可进入求解器
        can_enter_solver = len(missing_fields) == 0
        
        reason = f"缺少字段: {', '.join(missing_fields)}" if missing_fields else "信息完整"
        
        logger.info(f"CompletenessGate 结果: can_enter_solver={can_enter_solver}, missing={missing_fields}")
        
        return CompletenessInfo(
            can_enter_solver=can_enter_solver,
            missing_fields=missing_fields,
            reason=reason
        )
    
    def update_request(self, canonical_request: CanonicalDispatchRequest) -> CanonicalDispatchRequest:
        """
        门禁检查并更新请求的完整性信息
        
        Args:
            canonical_request: 标准化调度请求
            
        Returns:
            CanonicalDispatchRequest: 更新后的请求（包含完整性判定）
        """
        completeness = self.check(canonical_request)
        
        # 更新请求的 completeness 字段
        canonical_request.completeness = completeness
        
        return canonical_request


# 全局实例
_completeness_gate: CompletenessGate = None


def get_completeness_gate() -> CompletenessGate:
    """获取完整性门禁实例"""
    global _completeness_gate
    if _completeness_gate is None:
        _completeness_gate = CompletenessGate()
    return _completeness_gate