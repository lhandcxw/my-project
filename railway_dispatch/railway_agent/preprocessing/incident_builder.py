# -*- coding: utf-8 -*-
"""
事故卡片构建器
组装 CanonicalDispatchRequest
"""

from typing import Dict, Any, List, Optional
import logging

from models.common_enums import (
    SceneTypeCode, 
    SceneTypeLabel,
    FaultTypeCode,
    RequestSourceType,
    scene_code_to_label,
    fault_code_to_label
)
from models.preprocess_models import (
    CanonicalDispatchRequest,
    LocationInfo,
    CompletenessInfo,
    EvidenceInfo
)
from railway_agent.preprocessing.rule_extractor import get_rule_extractor
from railway_agent.preprocessing.alias_normalizer import get_alias_normalizer

logger = logging.getLogger(__name__)


class IncidentBuilder:
    """
    事故卡片构建器
    将提取的信息组装为 CanonicalDispatchRequest
    """
    
    def __init__(self):
        self.rule_extractor = get_rule_extractor()
        self.alias_normalizer = get_alias_normalizer()
    
    def build(
        self,
        raw_request: Any,
        source_type: RequestSourceType,
        extracted_info: Dict[str, Any]
    ) -> CanonicalDispatchRequest:
        """
        构建 CanonicalDispatchRequest
        
        Args:
            raw_request: 原始请求
            source_type: 请求来源类型
            extracted_info: 提取的信息
            
        Returns:
            CanonicalDispatchRequest: 标准化调度请求
        """
        logger.info("开始构建 CanonicalDispatchRequest")
        
        # 确定场景类型
        scene_type_code = self._resolve_scene_type(extracted_info)
        scene_type_label = scene_code_to_label(scene_type_code) if scene_type_code else None
        
        # 确定故障类型
        fault_type = extracted_info.get("fault_type", FaultTypeCode.UNKNOWN)
        
        # 处理位置信息
        location = self._resolve_location(extracted_info)
        
        # 处理列车信息
        affected_train_ids = self._resolve_train_ids(extracted_info)
        
        # 处理延误信息
        delay_seconds = extracted_info.get("delay_seconds")
        
        # 处理限速信息
        speed_limit_kph = extracted_info.get("speed_limit_kph")
        
        # 处理时间信息
        event_time = extracted_info.get("event_time")
        
        # 构建证据列表
        evidence_list = self._build_evidence(extracted_info)
        
        # 评估完整性
        completeness = self._assess_completeness(
            scene_type_code,
            location,
            affected_train_ids,
            delay_seconds
        )
        
        # 计算置信度
        confidence = extracted_info.get("confidence", 0.5)
        
        # 构建请求
        raw_text = None
        if source_type == RequestSourceType.NATURAL_LANGUAGE:
            raw_text = raw_request if isinstance(raw_request, str) else None
        elif source_type in (RequestSourceType.FORM, RequestSourceType.JSON):
            raw_text = str(raw_request) if raw_request else None
        
        canonical_request = CanonicalDispatchRequest(
            source_type=source_type,
            raw_text=raw_text,
            scene_type_code=scene_type_code,
            scene_type_label=scene_type_label,
            fault_type=fault_type,
            event_time=event_time,
            location=location,
            affected_train_ids=affected_train_ids,
            reported_delay_seconds=delay_seconds,
            speed_limit_kph=speed_limit_kph,
            completeness=completeness,
            evidence=evidence_list,
            confidence=confidence,
            metadata={
                "extracted_info": extracted_info,
                "source": "incident_builder"
            }
        )
        
        logger.info(f"CanonicalDispatchRequest 构建完成: scene={scene_type_code}, can_solver={completeness.can_enter_solver}")
        
        return canonical_request
    
    def _resolve_scene_type(self, info: Dict[str, Any]) -> Optional[SceneTypeCode]:
        """解析场景类型"""
        scene_type = info.get("scene_type")
        
        if not scene_type:
            # 根据故障类型推断
            fault_type = info.get("fault_type")
            if fault_type == FaultTypeCode.RAIN:
                return SceneTypeCode.TEMP_SPEED_LIMIT
            elif fault_type != FaultTypeCode.UNKNOWN:
                return SceneTypeCode.SUDDEN_FAILURE
            return None
        
        # 字符串转枚举
        if isinstance(scene_type, str):
            if scene_type == "TEMP_SPEED_LIMIT":
                return SceneTypeCode.TEMP_SPEED_LIMIT
            elif scene_type == "SUDDEN_FAILURE":
                return SceneTypeCode.SUDDEN_FAILURE
            elif scene_type == "SECTION_INTERRUPT":
                return SceneTypeCode.SECTION_INTERRUPT
        
        return None
    
    def _resolve_location(self, info: Dict[str, Any]) -> Optional[LocationInfo]:
        """解析位置信息"""
        station_name = info.get("station_name")
        
        if not station_name:
            return None
        
        # 归一化
        normalized = self.alias_normalizer.normalize_station(station_name)
        if normalized:
            return LocationInfo(
                station_code=normalized["station_code"],
                station_name=normalized["station_name"]
            )
        
        return LocationInfo(station_name=station_name)
    
    def _resolve_train_ids(self, info: Dict[str, Any]) -> List[str]:
        """解析列车ID列表"""
        train_ids = info.get("train_ids", [])
        resolved = []
        
        for train_no in train_ids:
            normalized = self.alias_normalizer.normalize_train(train_no)
            if normalized:
                resolved.append(normalized)
            else:
                resolved.append(train_no)
        
        return resolved
    
    def _build_evidence(self, info: Dict[str, Any]) -> List[EvidenceInfo]:
        """构建证据列表"""
        evidence = []
        
        # 添加各个字段的证据
        if info.get("train_ids"):
            evidence.append(EvidenceInfo(
                source="rule_extractor",
                field_name="affected_train_ids",
                value=info.get("train_ids"),
                confidence=info.get("confidence", 0.5)
            ))
        
        if info.get("station_name"):
            evidence.append(EvidenceInfo(
                source="rule_extractor",
                field_name="location",
                value=info.get("station_name"),
                confidence=info.get("confidence", 0.5)
            ))
        
        if info.get("delay_seconds"):
            evidence.append(EvidenceInfo(
                source="rule_extractor",
                field_name="reported_delay_seconds",
                value=info.get("delay_seconds"),
                confidence=info.get("confidence", 0.5)
            ))
        
        return evidence
    
    def _assess_completeness(
        self,
        scene_type: Optional[SceneTypeCode],
        location: Optional[LocationInfo],
        train_ids: List[str],
        delay_seconds: Optional[int]
    ) -> CompletenessInfo:
        """评估完整性"""
        missing_fields = []
        
        # 检查必要字段
        if not scene_type:
            missing_fields.append("scene_type")
        
        if not location or not location.station_code:
            missing_fields.append("location")
        
        if not train_ids:
            missing_fields.append("affected_train_ids")
        
        # 判断是否可进入求解器
        can_enter_solver = len(missing_fields) == 0
        
        return CompletenessInfo(
            can_enter_solver=can_enter_solver,
            missing_fields=missing_fields,
            reason=f"缺少字段: {', '.join(missing_fields)}" if missing_fields else "信息完整"
        )


# 全局实例
_incident_builder: Optional[IncidentBuilder] = None


def get_incident_builder() -> IncidentBuilder:
    """获取事故卡片构建器实例"""
    global _incident_builder
    if _incident_builder is None:
        _incident_builder = IncidentBuilder()
    return _incident_builder