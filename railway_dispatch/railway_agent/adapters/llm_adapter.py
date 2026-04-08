# -*- coding: utf-8 -*-
"""
LLM 适配器（新架构v2）
统一 LLM 调用接口 - 支持 OpenAI 和 Ollama
"""

from typing import Optional, Dict, Any
import logging
import json
import os

logger = logging.getLogger(__name__)


# 简化的 LLMCaller 兼容类
class LLMCaller:
    """简化的 LLM 调用器（兼容性）- 支持 OpenAI 和 Ollama"""

    # 默认使用 Ollama（本地模型）
    DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")

    def __init__(self):
        self._openai_client = None
        self._ollama_client = None

    def _get_openai_client(self):
        """获取 OpenAI 客户端"""
        if self._openai_client is None:
            try:
                from openai import OpenAI
                self._openai_client = OpenAI()
                logger.info("OpenAI 客户端初始化成功")
            except Exception as e:
                logger.warning(f"无法初始化OpenAI客户端: {e}")
                self._openai_client = None
        return self._openai_client

    def _get_ollama_client(self):
        """获取 Ollama 客户端（OpenAI 兼容模式）"""
        if self._ollama_client is None:
            try:
                from openai import OpenAI
                import os
                # 从环境变量获取 Ollama 地址，默认使用 localhost:11434
                ollama_host = os.getenv("OLLAMA_HOST", "localhost")
                ollama_port = os.getenv("OLLAMA_PORT", "11434")
                base_url = f"http://{ollama_host}:{ollama_port}/v1"

                self._ollama_client = OpenAI(
                    base_url=base_url,
                    api_key="ollama"  # Ollama 不需要真实 API key
                )
                logger.info(f"Ollama 客户端初始化成功: {base_url}")
            except Exception as e:
                logger.warning(f"无法初始化 Ollama 客户端: {e}")
                self._ollama_client = None
        return self._ollama_client

    def call(
        self,
        prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.7
    ) -> tuple:
        """
        调用 LLM

        Args:
            prompt: 提示文本
            max_tokens: 最大生成token数
            temperature: 温度参数

        Returns:
            tuple: (response_text, response_type)
        """
        # 根据配置选择 provider
        provider = self.DEFAULT_PROVIDER

        if provider == "ollama":
            return self._call_ollama(prompt, max_tokens, temperature)
        else:
            return self._call_openai(prompt, max_tokens, temperature)

    def _call_ollama(self, prompt: str, max_tokens: int, temperature: float) -> tuple:
        """调用 Ollama 本地模型"""
        client = self._get_ollama_client()

        if client is None:
            logger.warning("Ollama 客户端不可用，切换到模拟响应")
            return (self._get_mock_response(prompt), "mock")

        try:
            response = client.chat.completions.create(
                model=self.OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": "你是一个专业的铁路调度助手。"},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=max_tokens,
                temperature=temperature
            )
            return (response.choices[0].message.content, f"ollama:{self.OLLAMA_MODEL}")
        except Exception as e:
            logger.error(f"Ollama 调用失败: {e}")
            return (self._get_mock_response(prompt), "fallback")

    def _call_openai(self, prompt: str, max_tokens: int, temperature: float) -> tuple:
        """调用 OpenAI API"""
        client = self._get_openai_client()

        if client is None:
            # 如果无法初始化客户端，返回模拟响应
            return (self._get_mock_response(prompt), "mock")

        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "你是一个专业的铁路调度助手。"},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=max_tokens,
                temperature=temperature
            )
            return (response.choices[0].message.content, "openai")
        except Exception as e:
            logger.error(f"OpenAI 调用失败: {e}")
            return (self._get_mock_response(prompt), "fallback")

    def _get_mock_response(self, prompt: str) -> str:
        """获取模拟响应（当LLM调用失败时）"""
        # 根据提示词生成一些基本的模拟响应
        lower_prompt = prompt.lower()

        if "temporal_speed_limit" in lower_prompt or "temporary_speed_limit" in lower_prompt:
            return json.dumps({
                "scene_type": "temporal_speed_limit",
                "affected_train_ids": [],
                "affected_station_codes": [],
                "delay_minutes": 0,
                "reason": "模拟响应"
            })
        elif "sudden_failure" in lower_prompt:
            return json.dumps({
                "scene_type": "sudden_failure",
                "affected_train_ids": [],
                "affected_station_codes": [],
                "delay_minutes": 0,
                "reason": "模拟响应"
            })
        elif "train_status" in lower_prompt:
            return json.dumps({
                "query_type": "train_status",
                "train_ids": []
            })
        elif "timetable" in lower_prompt:
            return json.dumps({
                "query_type": "timetable",
                "station_codes": []
            })
        else:
            return json.dumps({
                "scene_type": "unknown",
                "reason": "模拟响应"
            })


# 延迟导入以避免循环依赖
_llm_caller = None

def get_llm_caller():
    """获取 LLM 调用器（延迟导入）"""
    global _llm_caller
    if _llm_caller is None:
        _llm_caller = LLMCaller()
    return _llm_caller


class LLMAdapter:
    """
    LLM 适配器（新架构v2）
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