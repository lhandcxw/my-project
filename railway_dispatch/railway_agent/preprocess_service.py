# -*- coding: utf-8 -*-
"""
预处理服务
整合所有预处理模块，提供统一的预处理入口
"""

from typing import Dict, Any, Optional
import logging

from models.common_enums import RequestSourceType
from models.preprocess_models import (
    RawUserRequest,
    CanonicalDispatchRequest,
    PreprocessDebugResponse
)

from railway_agent.preprocessing import (
    get_request_adapter,
    get_rule_extractor,
    get_alias_normalizer,
    get_llm_extractor,
    get_incident_builder,
    get_completeness_gate
)

logger = logging.getLogger(__name__)


class PreprocessService:
    """
    预处理服务
    整合所有预处理模块，提供统一的入口
    """
    
    def __init__(self):
        self.request_adapter = get_request_adapter()
        self.rule_extractor = get_rule_extractor()
        self.alias_normalizer = get_alias_normalizer()
        self.llm_extractor = get_llm_extractor()
        self.incident_builder = get_incident_builder()
        self.completeness_gate = get_completeness_gate()
    
    def preprocess(
        self,
        raw_input: Any,
        source_type: Optional[RequestSourceType] = None
    ) -> CanonicalDispatchRequest:
        """
        预处理入口
        
        处理流程：
        1. request_adapter 将不同输入源统一化
        2. rule_extractor 优先提取关键字段
        3. alias_normalizer 做归一化
        4. llm_extractor 仅补全规则未确定字段
        5. incident_builder 组装 CanonicalDispatchRequest
        6. completeness_gate 判断是否可进入 solver
        
        Args:
            raw_input: 原始输入
            source_type: 请求来源类型（可选，不提供则自动判断）
            
        Returns:
            CanonicalDispatchRequest: 标准化调度请求
        """
        logger.info(f"PreprocessService 开始预处理，输入类型: {type(raw_input)}")
        
        # Step 1: 构造 RawUserRequest
        if source_type:
            # 如果指定了来源类型，直接构造
            raw_request = RawUserRequest(
                source_type=source_type,
                raw_text=raw_input if isinstance(raw_input, str) else None,
                form_data=raw_input if isinstance(raw_input, dict) and source_type == RequestSourceType.FORM else None,
                json_data=raw_input if isinstance(raw_input, dict) and source_type == RequestSourceType.JSON else None
            )
        else:
            # 自动判断
            raw_request = self.request_adapter.adapt(raw_input)
        
        source_type = raw_request.source_type
        
        # Step 2: 规则提取
        raw_text = raw_request.raw_text or raw_request.form_data or raw_request.json_data
        extracted_info = {}
        
        if raw_text:
            if isinstance(raw_text, str):
                extracted_info = self.rule_extractor.extract(raw_text)
            elif isinstance(raw_text, dict):
                # 从表单数据提取
                extracted_info = self._extract_from_form(raw_text)
        
        logger.info(f"规则提取结果: {extracted_info}")
        
        # Step 3: 归一化
        if extracted_info.get("station_name"):
            normalized = self.alias_normalizer.normalize_station(extracted_info["station_name"])
            if normalized:
                extracted_info["station_code"] = normalized["station_code"]
                extracted_info["station_name_normalized"] = normalized["station_name"]
        
        if extracted_info.get("train_ids"):
            normalized_trains = []
            for train_no in extracted_info["train_ids"]:
                normalized = self.alias_normalizer.normalize_train(train_no)
                normalized_trains.append(normalized or train_no)
            extracted_info["train_ids_normalized"] = normalized_trains
        
        # Step 4: LLM 补全（如果需要）
        if isinstance(raw_text, str) and self._needs_llm_extraction(extracted_info):
            llm_result = self.llm_extractor.extract(raw_text, extracted_info)
            if llm_result:
                # 合并 LLM 结果
                for key, value in llm_result.items():
                    if value and not extracted_info.get(key):
                        extracted_info[key] = value
        
        # Step 5: 构建 CanonicalDispatchRequest
        canonical_request = self.incident_builder.build(
            raw_request=raw_text,
            source_type=source_type,
            extracted_info=extracted_info
        )
        
        # Step 6: 完整性门禁检查
        canonical_request = self.completeness_gate.update_request(canonical_request)
        
        logger.info(f"预处理完成: request_id={canonical_request.request_id}, can_enter_solver={canonical_request.completeness.can_enter_solver}")
        
        return canonical_request
    
    def _extract_from_form(self, form_data: Dict[str, Any]) -> Dict[str, Any]:
        """从表单数据提取"""
        result = {}
        
        # 场景类型
        scene_type = form_data.get("scene_type", "")
        if scene_type:
            result["scene_type"] = scene_type
        
        # 故障类型
        fault_type = form_data.get("fault_type", "")
        if fault_type:
            result["fault_type"] = fault_type
        
        # 车站
        station = form_data.get("location_code") or form_data.get("station_code", "")
        if station:
            result["station_name"] = station
        
        # 列车
        trains = form_data.get("affected_trains", [])
        if trains:
            result["train_ids"] = trains if isinstance(trains, list) else [trains]
        
        # 延误
        delay = form_data.get("delay_seconds") or form_data.get("delay_minutes")
        if delay:
            if isinstance(delay, str) and "分钟" in delay:
                result["delay_seconds"] = int(delay.replace("分钟", "")) * 60
            else:
                result["delay_seconds"] = int(delay)
        
        # 限速
        speed_limit = form_data.get("speed_limit_kph")
        if speed_limit:
            result["speed_limit_kph"] = int(speed_limit)
        
        result["confidence"] = 0.9  # 表单数据置信度较高
        
        return result
    
    def _needs_llm_extraction(self, extracted_info: Dict[str, Any]) -> bool:
        """判断是否需要 LLM 补全"""
        required = ["scene_type", "station_code"]
        for field in required:
            if not extracted_info.get(field):
                return True
        return False
    
    def preprocess_debug(
        self,
        raw_input: Any
    ) -> PreprocessDebugResponse:
        """
        预处理调试入口
        
        返回完整的预处理过程信息，用于调试
        
        Args:
            raw_input: 原始输入
            
        Returns:
            PreprocessDebugResponse: 调试响应
        """
        logger.info("PreprocessService 执行调试模式")
        
        # 完整预处理
        canonical_request = self.preprocess(raw_input)
        
        # 构建调试响应
        processing_steps = [
            "request_adapter.adapt()",
            "rule_extractor.extract()",
            "alias_normalizer.normalize()",
            "llm_extractor.extract() (if needed)",
            "incident_builder.build()",
            "completeness_gate.check()"
        ]
        
        debug_response = PreprocessDebugResponse(
            request_id=canonical_request.request_id,
            raw_user_request={
                "source_type": canonical_request.source_type.value,
                "raw_text": canonical_request.raw_text[:100] if canonical_request.raw_text else None
            },
            canonical_request=canonical_request.model_dump(mode='json'),
            evidence_list=[e.model_dump() for e in canonical_request.evidence],
            completeness=canonical_request.completeness.model_dump(),
            processing_steps=processing_steps
        )
        
        return debug_response


# 全局实例
_preprocess_service: Optional[PreprocessService] = None


def get_preprocess_service() -> PreprocessService:
    """获取预处理服务实例"""
    global _preprocess_service
    if _preprocess_service is None:
        _preprocess_service = PreprocessService()
    return _preprocess_service