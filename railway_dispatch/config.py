# -*- coding: utf-8 -*-
"""
铁路调度系统 - 统一配置中心
集中管理所有配置，避免分散和重复

注意：当前使用硬编码变量方便开发调试，项目完成后改为环境变量
"""

import os
from typing import Optional


class LLMConfig:
    """LLM 配置"""
    # 默认使用阿里云 DashScope
    PROVIDER = "dashscope"
    
    # 阿里云 DashScope 配置 - 开发阶段直接填写，完成后改为环境变量
    DASHSCOPE_API_KEY = "sk-bcf1668108cd4708b2f113d5073e42d4"  
    DASHSCOPE_MODEL = "qwen3.5-27b"
    DASHSCOPE_ENABLE_THINKING = True
    
    # 实验模式配置 - 强制LLM模式，禁用规则回退
    FORCE_LLM_MODE = True
    
    # Ollama 配置
    OLLAMA_BASE_URL = "http://localhost:11434"
    OLLAMA_MODEL = "qwen2.5:1.5b"
    
    # OpenAI 配置
    OPENAI_API_KEY = ""
    OPENAI_MODEL = "gpt-4o"
    
    @classmethod
    def get_model_name(cls) -> str:
        """获取当前使用的模型名称"""
        if cls.PROVIDER == "dashscope":
            return f"dashscope:{cls.DASHSCOPE_MODEL}"
        elif cls.PROVIDER == "ollama":
            return f"ollama:{cls.OLLAMA_MODEL}"
        elif cls.PROVIDER == "openai":
            return f"openai:{cls.OPENAI_MODEL}"
        return f"unknown:{cls.PROVIDER}"
    
    @classmethod
    def get_provider_name(cls) -> str:
        """获取当前使用的提供商名称"""
        provider_map = {
            "dashscope": "阿里云DashScope",
            "ollama": "Ollama本地模型",
            "openai": "OpenAI"
        }
        return provider_map.get(cls.PROVIDER, cls.PROVIDER)


class AppConfig:
    """应用配置"""
    # Web 服务配置
    WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
    WEB_PORT = int(os.getenv("WEB_PORT", "8081"))
    
    # Agent 模式: "rule" | "qwen" | "auto"
    AGENT_MODE = os.getenv("AGENT_MODE", "qwen")
    
    # 数据配置
    USE_REAL_DATA = os.getenv("USE_REAL_DATA", "true").lower() == "true"
    
    # 日志配置
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


class SolverConfig:
    """求解器配置"""
    # 默认求解器
    DEFAULT_SOLVER = os.getenv("DEFAULT_SOLVER", "mip")
    
    # MIP 求解器配置
    MIP_TIME_LIMIT = int(os.getenv("MIP_TIME_LIMIT", "300"))  # 秒
    MIP_GAP = float(os.getenv("MIP_GAP", "0.01"))  # 1% gap


# 导出常用配置
llm_config = LLMConfig()
app_config = AppConfig()
solver_config = SolverConfig()

# 向后兼容的导出（供 web/app.py 使用）
AGENT_MODE = AppConfig.AGENT_MODE
LLM_CONFIG = {
    'provider': LLMConfig.PROVIDER,
    'api_key': LLMConfig.DASHSCOPE_API_KEY,
    'model': LLMConfig.DASHSCOPE_MODEL,
    'enable_thinking': LLMConfig.DASHSCOPE_ENABLE_THINKING
}


class Config:
    """统一配置类 - 兼容旧接口"""
    
    def __init__(self):
        self.llm_provider = LLMConfig.PROVIDER
        self.llm_model = LLMConfig.get_model_name()
        self.dashscope_api_key = LLMConfig.DASHSCOPE_API_KEY
        self.dashscope_model = LLMConfig.DASHSCOPE_MODEL
        self.dashscope_enable_thinking = LLMConfig.DASHSCOPE_ENABLE_THINKING
        self.ollama_base_url = LLMConfig.OLLAMA_BASE_URL
        self.ollama_model = LLMConfig.OLLAMA_MODEL
        self.openai_api_key = LLMConfig.OPENAI_API_KEY
        self.openai_model = LLMConfig.OPENAI_MODEL
        self.agent_mode = AppConfig.AGENT_MODE
        self.web_host = AppConfig.WEB_HOST
        self.web_port = AppConfig.WEB_PORT


# 全局配置实例
_config_instance = None


def get_config() -> Config:
    """获取配置实例（兼容旧接口）"""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance


def get_config_summary() -> str:
    """获取配置摘要"""
    return f"""
========================================
        系统配置摘要
========================================
LLM配置:
  - 提供商: {LLMConfig.get_provider_name()}
  - 模型: {LLMConfig.get_model_name()}
  - 思考模式: {'开启' if LLMConfig.DASHSCOPE_ENABLE_THINKING else '关闭'}

应用配置:
  - Agent模式: {AppConfig.AGENT_MODE}
  - 使用真实数据: {AppConfig.USE_REAL_DATA}
  - 日志级别: {AppConfig.LOG_LEVEL}

服务配置:
  - 地址: http://{AppConfig.WEB_HOST}:{AppConfig.WEB_PORT}
========================================
"""


if __name__ == "__main__":
    print(get_config_summary())
