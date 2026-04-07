# -*- coding: utf-8 -*-
"""
LLM 提取器
仅补全规则未确定字段，必须输出严格 JSON
"""

from typing import Dict, Any, Optional
import json
import logging
from datetime import datetime

from models.common_enums import SceneTypeCode, FaultTypeCode
from railway_agent.llm_workflow_engine import get_llm_caller

logger = logging.getLogger(__name__)

# 简化的 LLM 提取 prompt
LLM_EXTRACTOR_PROMPT = """
从铁路调度描述中提取信息。只输出JSON。

描述：{user_input}

已知信息：{known_info}

输出格式（严格JSON，只输出一行）：
{{"scene_type":"TEMP_SPEED_LIMIT","fault_type":"RAIN","station_code":"XSD","delay_seconds":600}}

scene_type 可选: TEMP_SPEED_LIMIT, SUDDEN_FAILURE, SECTION_INTERRUPT
fault_type 可选: RAIN, EQUIPMENT_FAILURE, SIGNAL_FAILURE, CATENARY_FAILURE
station_code: 站码如 XSD
delay_seconds: 延误秒数
"""


class LLMExtractor:
    """
    LLM 提取器
    仅在规则提取无法确定所有字段时调用 LLM
    """
    
    def __init__(self):
        self.llm_caller = None
    
    def _get_llm_caller(self):
        """获取 LLM 调用器"""
        if self.llm_caller is None:
            try:
                from railway_agent.llm_workflow_engine import get_llm_caller
                self.llm_caller = get_llm_caller()
            except Exception as e:
                logger.warning(f"获取LLM调用器失败: {e}")
        return self.llm_caller
    
    def extract(self, user_input: str, known_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        使用 LLM 补全缺失信息
        
        Args:
            user_input: 用户输入文本
            known_info: 已知信息（来自规则提取）
            
        Returns:
            Dict: LLM 提取的信息 或 None
        """
        # 检查是否需要 LLM
        missing_fields = self._check_missing_fields(known_info)
        
        if not missing_fields:
            logger.info("规则提取已完整，无需LLM")
            return None
        
        logger.info(f"需要LLM补充字段: {missing_fields}")
        
        # 构建 prompt
        known_info_str = json.dumps(known_info, ensure_ascii=False, indent=2)
        prompt = LLM_EXTRACTOR_PROMPT.format(
            user_input=user_input,
            known_info=known_info_str
        )
        
        # 调用 LLM
        llm = self._get_llm_caller()
        if llm is None:
            logger.warning("LLM不可用，返回None")
            return None
        
        try:
            response = llm.call(prompt, max_tokens=256)
            logger.info(f"LLM响应: {response[:200]}...")
            
            # 解析 JSON
            json_str = self._extract_json(response)
            if json_str:
                result = json.loads(json_str)
                return result
            else:
                logger.warning("LLM响应无法解析为JSON")
                return None
                
        except Exception as e:
            logger.error(f"LLM提取失败: {e}")
            return None
    
    def _check_missing_fields(self, known_info: Dict[str, Any]) -> list:
        """检查缺失字段"""
        required = ["scene_type", "station_code"]
        missing = []
        
        for field in required:
            if not known_info.get(field):
                missing.append(field)
        
        return missing
    
    def _extract_json(self, text: str) -> Optional[str]:
        """从文本中提取 JSON"""
        # 尝试从 markdown 代码块提取
        if '```json' in text:
            return text.split('```json')[1].split('```')[0].strip()
        if '```' in text:
            return text.split('```')[1].split('```')[0].strip()
        
        # 尝试直接解析
        if '{' in text and '}' in text:
            start = text.find('{')
            end = text.rfind('}')
            if start >= 0 and end > start:
                return text[start:end+1]
        
        return None


# 全局实例
_llm_extractor: Optional[LLMExtractor] = None


def get_llm_extractor() -> LLMExtractor:
    """获取 LLM 提取器实例"""
    global _llm_extractor
    if _llm_extractor is None:
        _llm_extractor = LLMExtractor()
    return _llm_extractor