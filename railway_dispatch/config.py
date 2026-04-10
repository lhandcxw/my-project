# -*- coding: utf-8 -*-
"""
铁路调度系统 - 统一配置中心
集中管理所有配置，避免分散和重复

注意：当前开发阶段使用直接配置，项目结束后将改为环境变量
"""

import os
import sys
from typing import Optional


class LLMConfig:
    """
    LLM 配置（统一LLM驱动架构）

    支持两种调用方式：
    1. API调用阿里云模型（DashScope）
    2. 调用微调后的本地模型
    """
    # LLM提供方式: "dashscope" | "local"
    PROVIDER = "dashscope"

    # ========== 方式1：阿里云 DashScope API ==========
    DASHSCOPE_API_KEY = "sk-bcf1668108cd4708b2f113d5073e42d4"  # 请填写您的DashScope API Key
    DASHSCOPE_MODEL = "qwen3.6-plus"
    DASHSCOPE_ENABLE_THINKING = False  # qwen3.6-plus 不支持深度思考模式

    # ========== 方式2：本地微调模型 ==========
    # 支持以下本地模型框架：
    # - Ollama (本地推理)
    # - vLLM (高性能推理)
    # - Transformers (原生加载)

    # Ollama 配置
    OLLAMA_BASE_URL = "http://localhost:11434"
    OLLAMA_MODEL = "qwen2.5:1.5b"  # 或微调后的模型名称，如 "qwen-finetuned"

    # vLLM 配置（高性能推理）
    VLLM_BASE_URL = "http://localhost:8000/v1"
    VLLM_MODEL = "qwen-finetuned"  # 微调后的模型路径

    # Transformers 原生加载（用于微调模型）
    TRANSFORMERS_MODEL_PATH = ""  # 微调模型路径，如 "./models/qwen-finetuned"
    TRANSFORMERS_DEVICE = "cuda"  # "cuda" | "cpu"
    TRANSFORMERS_MAX_LENGTH = 4096

    # ========== 通用配置 ==========
    FORCE_LLM_MODE = True  # 允许LLM失败时使用规则回退（生产环境推荐）

    # OpenAI 配置（兼容性）
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
            "dashscope": "阿里云DashScope API",
            "local": "本地微调模型（Ollama/vLLM/Transformers）",
            "ollama": "Ollama本地模型",
            "vllm": "vLLM高性能推理",
            "transformers": "Transformers原生加载",
            "openai": "OpenAI"
        }
        return provider_map.get(cls.PROVIDER, cls.PROVIDER)


class AppConfig:
    """应用配置"""
    # Web 服务配置
    WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
    WEB_PORT = int(os.getenv("WEB_PORT", "8081"))

    # Agent 模式: "dashscope" | "local" | "auto"
    # 说明：
    # - dashscope: 使用阿里云API（默认）
    # - local: 使用本地微调模型
    # - auto: 自动选择（优先本地，失败则用API）
    AGENT_MODE = os.getenv("AGENT_MODE", "dashscope")

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


def validate_config():
    """验证配置完整性，失败时明确报错并终止"""
    errors = []

    # 验证LLM配置
    if LLMConfig.PROVIDER == "dashscope":
        if not LLMConfig.DASHSCOPE_API_KEY:
            errors.append("DASHSCOPE_API_KEY 未设置，请在 config.py 中填写")

    if errors:
        error_msg = "配置验证失败，请检查以下问题：\n" + "\n".join(f"  - {e}" for e in errors)
        print(error_msg, file=sys.stderr)
        sys.exit(1)

    # 启动时打印配置摘要
    print(get_config_summary(), file=sys.stderr)


def get_config() -> Config:
    """获取配置实例（兼容旧接口）"""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance


def get_config_summary() -> str:
    """获取配置摘要"""
    provider_name = LLMConfig.get_provider_name()

    if LLMConfig.PROVIDER == "dashscope":
        api_key_status = "已设置" if LLMConfig.DASHSCOPE_API_KEY else "未设置（请在config.py中填写）"
        thinking_support = "支持" if is_thinking_supported() else "不支持"
        thinking_config = "开启" if LLMConfig.DASHSCOPE_ENABLE_THINKING else "关闭"

        llm_info = f"""
  - 提供商: {provider_name}
  - 模型: {LLMConfig.DASHSCOPE_MODEL}
  - API Key: {api_key_status}
  - 思考模式配置: {thinking_config}
  - 模型思考支持: {thinking_support}"""
    else:  # 本地模型
        ollama_status = "可用" if LLMConfig.PROVIDER in ["local", "ollama"] else "未使用"
        vllm_status = "可用" if LLMConfig.PROVIDER in ["local", "vllm"] else "未使用"
        transformers_status = "可用" if LLMConfig.PROVIDER in ["local", "transformers"] else "未使用"

        llm_info = f"""
  - 提供商: {provider_name}
  - Ollama模型: {LLMConfig.OLLAMA_MODEL} ({ollama_status})
  - vLLM模型: {LLMConfig.VLLM_MODEL} ({vllm_status})
  - Transformers路径: {LLMConfig.TRANSFORMERS_MODEL_PATH or '未配置'} ({transformers_status})"""

    return f"""
========================================
        系统配置摘要
========================================
LLM配置:{llm_info}
  - 强制LLM模式: {'是' if LLMConfig.FORCE_LLM_MODE else '否'}

应用配置:
  - Agent模式: {AppConfig.AGENT_MODE}
  - 使用真实数据: {AppConfig.USE_REAL_DATA}
  - 日志级别: {AppConfig.LOG_LEVEL}

服务配置:
  - 地址: http://{AppConfig.WEB_HOST}:{AppConfig.WEB_PORT}
========================================
架构说明：
  - 移除RuleAgent，统一使用LLM驱动
  - 支持阿里云API和本地微调模型
  - 完整L1-L4工作流
========================================
"""


def is_thinking_supported() -> bool:
    """检查当前模型是否支持深度思考模式"""
    supported_models = ["qwen-max", "qwen3-72b", "qwen3-14b"]
    model_lower = LLMConfig.DASHSCOPE_MODEL.lower()
    return any(m in model_lower for m in supported_models)


if __name__ == "__main__":
    validate_config()
