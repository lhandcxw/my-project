# -*- coding: utf-8 -*-
"""
LLM 适配器
统一 LLM 调用接口
"""

from typing import Optional, Dict, Any
import logging

from railway_agent.llm_workflow_engine import get_llm_caller, LLMCaller

logger = logging.getLogger(__name__)


class LLMAdapter:
    """
    LLM 适配器
    封装 LLM 调用，提供统一的接口
    """
    
    def __init__(self):
        self._llm_caller: Optional[LLMCaller] = None
    
    def _get_llm_caller(self) -> LLMCaller:
        """获取 LLM 调用器"""
        if self._llm_caller is None:
            self._llm_caller = get_llm_caller()
        return self._llm_caller
    
    def call(
        self, 
        prompt: str, 
        max_tokens: int = 512, 
        temperature: float = 0.7
    ) -> str:
        """
        调用 LLM
        
        Args:
            prompt: 输入提示
            max_tokens: 最大生成 token 数
            temperature: 温度参数
            
        Returns:
            str: LLM 响应
        """
        llm = self._get_llm_caller()
        try:
            return llm.call(prompt, max_tokens, temperature)
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return ""
    
    def extract_json(self, response: str) -> Optional[Dict[str, Any]]:
        """
        从 LLM 响应中提取 JSON
        
        Args:
            response: LLM 响应
            
        Returns:
            Dict: 解析后的 JSON 或 None
        """
        import json
        
        # 尝试从 markdown 提取
        if '```json' in response:
            json_str = response.split('```json')[1].split('```')[0]
            try:
                return json.loads(json_str)
            except:
                pass
        
        if '```' in response:
            json_str = response.split('```')[1].split('```')[0]
            try:
                return json.loads(json_str)
            except:
                pass
        
        # 尝试直接解析
        if '{' in response:
            start = response.find('{')
            end = response.rfind('}')
            if start >= 0 and end > start:
                try:
                    return json.loads(response[start:end+1])
                except:
                    pass
        
        return None


# 全局实例
_llm_adapter: Optional[LLMAdapter] = None


def get_llm_adapter() -> LLMAdapter:
    """获取 LLM 适配器实例"""
    global _llm_adapter
    if _llm_adapter is None:
        _llm_adapter = LLMAdapter()
    return _llm_adapter