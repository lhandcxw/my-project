# -*- coding: utf-8 -*-
"""
LLM 适配器（新架构v2）
统一 LLM 调用接口 - 支持 OpenAI、Ollama 和阿里云
"""

from typing import Optional, Dict, Any
import logging
import json
import os

# 导入统一配置
from config import LLMConfig

logger = logging.getLogger(__name__)


# 简化的 LLMCaller 兼容类
class LLMCaller:
    """简化的 LLM 调用器（兼容性）- 支持 OpenAI、Ollama 和阿里云"""

    def __init__(self):
        self._openai_client = None
        self._ollama_client = None
        self._dashscope_client = None

    @property
    def DEFAULT_PROVIDER(self) -> str:
        return LLMConfig.PROVIDER

    @property
    def DASHSCOPE_API_KEY(self) -> str:
        return LLMConfig.DASHSCOPE_API_KEY

    @property
    def DASHSCOPE_MODEL(self) -> str:
        return LLMConfig.DASHSCOPE_MODEL

    @property
    def DASHSCOPE_ENABLE_THINKING(self) -> bool:
        return LLMConfig.DASHSCOPE_ENABLE_THINKING

    @property
    def OLLAMA_BASE_URL(self) -> str:
        return LLMConfig.OLLAMA_BASE_URL

    @property
    def OLLAMA_MODEL(self) -> str:
        return LLMConfig.OLLAMA_MODEL

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

    def _get_dashscope_client(self):
        """获取阿里云 DashScope 客户端"""
        if self._dashscope_client is None:
            try:
                from openai import OpenAI
                self._dashscope_client = OpenAI(
                    api_key=self.DASHSCOPE_API_KEY,
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
                )
                logger.info(f"阿里云 DashScope 客户端初始化成功，使用模型: {self.DASHSCOPE_MODEL}")
            except Exception as e:
                logger.warning(f"无法初始化阿里云客户端: {e}")
                self._dashscope_client = None
        return self._dashscope_client

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
        elif provider == "dashscope":
            return self._call_dashscope(prompt, max_tokens, temperature)
        else:
            return self._call_openai(prompt, max_tokens, temperature)

    def _call_ollama(self, prompt: str, max_tokens: int, temperature: float) -> tuple:
        """调用 Ollama 本地模型"""
        client = self._get_ollama_client()

        if client is None:
            raise RuntimeError("Ollama 客户端不可用")

        try:
            # 增加超时和重试逻辑
            import time
            max_retries = 2
            for attempt in range(max_retries):
                try:
                    response = client.chat.completions.create(
                        model=self.OLLAMA_MODEL,
                        messages=[
                            {"role": "system", "content": "你是一个专业的铁路调度助手。请严格按照要求输出JSON格式。"},
                            {"role": "user", "content": prompt}
                        ],
                        max_tokens=max_tokens,
                        temperature=temperature,
                        timeout=60  # 60秒超时
                    )
                    content = response.choices[0].message.content
                    if content and content.strip():
                        return (content, f"ollama:{self.OLLAMA_MODEL}")
                    else:
                        logger.warning(f"Ollama 返回空响应 (尝试 {attempt + 1}/{max_retries})")
                        if attempt < max_retries - 1:
                            time.sleep(1)
                            continue
                except Exception as inner_e:
                    logger.warning(f"Ollama 调用异常 (尝试 {attempt + 1}/{max_retries}): {inner_e}")
                    if attempt < max_retries - 1:
                        time.sleep(1)
                        continue
                    raise

            # 所有重试都失败
            raise RuntimeError("Ollama 多次调用失败")

        except Exception as e:
            logger.error(f"Ollama 调用失败: {e}")
            raise RuntimeError(f"Ollama 调用失败: {e}") from e

    def _call_openai(self, prompt: str, max_tokens: int, temperature: float) -> tuple:
        """调用 OpenAI API"""
        client = self._get_openai_client()

        if client is None:
            raise RuntimeError("OpenAI 客户端不可用")

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
            raise RuntimeError(f"OpenAI 调用失败: {e}") from e

    def _call_dashscope(self, prompt: str, max_tokens: int, temperature: float) -> tuple:
        """调用阿里云 DashScope (qwen-max/qwen3.5-27b)"""
        client = self._get_dashscope_client()

        if client is None:
            raise RuntimeError("阿里云客户端不可用")

        # 验证API Key已设置
        if not self.DASHSCOPE_API_KEY:
            raise RuntimeError("DASHSCOPE_API_KEY未设置，请配置环境变量")

        try:
            # 构建请求参数
            request_params = {
                "model": self.DASHSCOPE_MODEL,
                "messages": [
                    {"role": "system", "content": "你是一个专业的铁路调度助手。请严格按照要求输出JSON格式。"},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": max_tokens,
                "temperature": temperature
            }

            # 启用深度思考模式（如果配置允许）
            # 注意：只有特定模型支持enable_thinking参数
            if self.DASHSCOPE_ENABLE_THINKING:
                model_lower = self.DASHSCOPE_MODEL.lower()
                # 目前只有qwen-max和qwen3-72b等特定模型支持enable_thinking
                # qwen3.5-27b和qwen3.6-plus均不支持
                supported_thinking_models = ["qwen-max", "qwen3-72b", "qwen3-14b"]
                is_supported = any(m in model_lower for m in supported_thinking_models)
                
                if is_supported:
                    request_params["enable_thinking"] = True
                    logger.info(f"[DashScope] 启用深度思考模式")
                else:
                    logger.info(f"[DashScope] 当前模型 {self.DASHSCOPE_MODEL} 不支持深度思考模式，跳过")

            response = client.chat.completions.create(**request_params)

            content = response.choices[0].message.content
            if content and content.strip():
                # 记录真实API调用成功
                thinking_status = "开启" if self.DASHSCOPE_ENABLE_THINKING else "关闭"
                logger.info(f"[DashScope API] 成功调用模型: {self.DASHSCOPE_MODEL} (思考模式: {thinking_status})")
                return (content, f"{self.DASHSCOPE_MODEL}")
            else:
                raise RuntimeError("DashScope 返回空响应")
        except Exception as e:
            logger.error(f"阿里云 DashScope 调用失败: {e}")
            raise RuntimeError(f"阿里云 DashScope 调用失败: {e}") from e


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
            raise RuntimeError(f"LLM 调用失败: {e}") from e
    
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
