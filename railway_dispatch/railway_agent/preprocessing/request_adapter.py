# -*- coding: utf-8 -*-
"""
请求适配器
将不同输入源（自然语言/表单/JSON）统一转换为 RawUserRequest
"""

from typing import Any, Dict, Optional
from models.common_enums import RequestSourceType
from models.preprocess_models import RawUserRequest
import logging

logger = logging.getLogger(__name__)


class RequestAdapter:
    """
    请求适配器
    统一不同输入源的格式
    """
    
    def adapt(self, raw_input: Any) -> RawUserRequest:
        """
        将任意输入转换为 RawUserRequest
        
        Args:
            raw_input: 原始输入（str/dict）
            
        Returns:
            RawUserRequest: 标准化后的原始请求
        """
        logger.info(f"RequestAdapter 处理输入类型: {type(raw_input)}")
        
        if isinstance(raw_input, str):
            # 字符串输入 -> 自然语言
            return RawUserRequest(
                source_type=RequestSourceType.NATURAL_LANGUAGE,
                raw_text=raw_input
            )
        elif isinstance(raw_input, dict):
            # 字典输入 -> 判断类型
            source = raw_input.get("source_type", "")
            
            if source == "form" or source == "form_submit":
                return RawUserRequest(
                    source_type=RequestSourceType.FORM,
                    form_data=raw_input
                )
            elif source == "json" or source == "script_api":
                return RawUserRequest(
                    source_type=RequestSourceType.JSON,
                    json_data=raw_input
                )
            else:
                # 默认视为表单
                return RawUserRequest(
                    source_type=RequestSourceType.FORM,
                    form_data=raw_input
                )
        else:
            raise ValueError(f"不支持的输入类型: {type(raw_input)}")


# 全局实例
_request_adapter: Optional[RequestAdapter] = None


def get_request_adapter() -> RequestAdapter:
    """获取请求适配器实例"""
    global _request_adapter
    if _request_adapter is None:
        _request_adapter = RequestAdapter()
    return _request_adapter