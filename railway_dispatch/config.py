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

    # ========== L1 数据建模层配置 ==========
    # L1 实体提取方式: "prompt" | "finetuned"
    # - prompt: 使用 Prompt 模板调用 LLM 提取（默认）
    # - finetuned: 使用微调后的模型提取
    L1_EXTRACTION_MODE = "prompt"

    # 微调模型配置（当 L1_EXTRACTION_MODE = "finetuned" 时使用）
    L1_FINETUNED_MODEL_PROVIDER = "ollama"  # "ollama" | "vllm" | "transformers"
    L1_FINETUNED_MODEL_NAME = "qwen2.5:1.5b"  # 微调后的模型名称或路径

    # ========== L1 数据建模层配置 ==========
    # L1 提取方式: "prompt" | "finetuned"
    # - prompt: 使用 Prompt 模板调用 LLM 提取（默认，当前使用）
    # - finetuned: 使用微调后的模型直接提取（训练完成后切换）
    L1_EXTRACTION_MODE = "prompt"

    # 微调模型配置（当 L1_EXTRACTION_MODE = "finetuned" 时使用）
    L1_FINETUNED_MODEL_PROVIDER = "ollama"  # "ollama" | "vllm" | "transformers"
    L1_FINETUNED_MODEL_NAME = "qwen2.5:1.5b"  # 微调后的模型名称
    L1_FINETUNED_MODEL_PATH = ""  # 本地模型路径（transformers 模式使用）
    L1_FINETUNED_API_URL = "http://localhost:11434"  # Ollama/vLLM API 地址
    L1_FINETUNED_TEMPERATURE = 0.0  # 微调模型温度（提取任务需要确定性）
    L1_FINETUNED_MAX_TOKENS = 512  # 微调模型最大输出长度

    # ========== 方式1：阿里云 DashScope API ==========
    # 注意：当前开发阶段使用直接配置，项目结束后将改为环境变量
    DASHSCOPE_API_KEY = "sk-bcf1668108cd4708b2f113d5073e42d4"  # 请在此填写您的DashScope API Key，例如："sk-xxx..."
    DASHSCOPE_MODEL = "glm-5"
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
    FORCE_LLM_MODE = True  # True=强制使用LLM，失败时报错；False=允许LLM失败时使用规则回退（调试模式）

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


class L1Config:
    """L1 数据建模层配置"""
    # L1 提取方式: "prompt" | "finetuned"
    # - prompt: 使用 Prompt 模板调用 LLM 提取（默认）
    # - finetuned: 使用微调后的模型直接提取
    USE_FINETUNED_MODEL = False  # 等价于 LLMConfig.L1_EXTRACTION_MODE == "finetuned"
    EXTRACTION_MODE = "prompt"

    # 微调模型配置
    FINETUNED_MODEL_PROVIDER = "ollama"  # "ollama" | "vllm" | "transformers"
    FINETUNED_MODEL_NAME = "qwen2.5:1.5b"
    FINETUNED_MODEL_BASE_URL = "http://localhost:11434"
    FINETUNED_TEMPERATURE = 0.0
    FINETUNED_MAX_TOKENS = 512

    # 失败回退配置
    FALLBACK_TO_PROMPT_ON_ERROR = True

    @classmethod
    def get_extraction_mode(cls) -> str:
        """获取当前提取模式"""
        return "finetuned" if cls.USE_FINETUNED_MODEL else "prompt"


class DispatchEnvConfig:
    """
    列车调度环境配置
    从YAML配置文件加载调度约束参数
    """
    _config_data = None

    @classmethod
    def _load_config(cls):
        """加载YAML配置文件"""
        if cls._config_data is not None:
            return cls._config_data

        import yaml
        config_path = os.path.join(os.path.dirname(__file__), "config", "dispatch_env.yaml")

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                cls._config_data = yaml.safe_load(f)
        except FileNotFoundError:
            # 如果配置文件不存在，使用默认配置
            cls._config_data = cls._get_default_config()
        except Exception as e:
            print(f"警告: 加载调度环境配置失败: {e}，使用默认配置", file=sys.stderr)
            cls._config_data = cls._get_default_config()

        return cls._config_data

    @classmethod
    def _get_default_config(cls):
        """获取默认配置"""
        return {
            "constraints": {
                "headway_time": 180,
                "min_stop_time": 60,
                "min_headway_time": 180,
                "stop_time_redundancy_ratio": 0.5,
                "running_time_redundancy_ratio": 0.3
            },
            "station_defaults": {
                "default_track_count": 2
            },
            "solver_settings": {
                "time_limit": 300,
                "optimality_gap": 0.01
            }
        }

    @classmethod
    def get(cls, key_path: str, default=None):
        """
        获取配置值

        Args:
            key_path: 点分隔的路径，如 "constraints.headway_time"
            default: 默认值

        Returns:
            配置值或默认值
        """
        config = cls._load_config()
        keys = key_path.split('.')
        value = config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    @classmethod
    def get_constraints(cls) -> dict:
        """获取所有约束配置"""
        return cls._load_config().get("constraints", {})

    @classmethod
    def get_station_defaults(cls) -> dict:
        """获取车站默认配置"""
        return cls._load_config().get("station_defaults", {})

    @classmethod
    def get_solver_settings(cls) -> dict:
        """获取求解器设置"""
        return cls._load_config().get("solver_settings", {})

    # 便捷属性
    @classmethod
    def headway_time(cls) -> int:
        """追踪间隔（秒）"""
        return cls.get("constraints.headway_time", 180)

    @classmethod
    def min_stop_time(cls) -> int:
        """最小停站时间（秒）"""
        return cls.get("constraints.min_stop_time", 60)

    @classmethod
    def min_headway_time(cls) -> int:
        """最小安全间隔（秒）"""
        return cls.get("constraints.min_headway_time", 180)

    @classmethod
    def stop_time_redundancy_ratio(cls) -> float:
        """停站冗余利用比例"""
        return cls.get("constraints.stop_time_redundancy_ratio", 0.5)

    @classmethod
    def running_time_redundancy_ratio(cls) -> float:
        """区间运行冗余利用比例"""
        return cls.get("constraints.running_time_redundancy_ratio", 0.3)

    @classmethod
    def min_section_time_ratio(cls) -> float:
        """最小区间运行时间系数（用于计算最小区间运行时间 = 标准时间 * 系数）"""
        return cls.get("constraints.min_section_time_ratio", 0.9)

    @classmethod
    def default_track_count(cls) -> int:
        """默认股道数量"""
        return cls.get("station_defaults.default_track_count", 2)

    @classmethod
    def solver_time_limit(cls) -> int:
        """求解器时间限制（秒）"""
        return cls.get("solver_settings.time_limit", 300)

    @classmethod
    def solver_optimality_gap(cls) -> float:
        """求解器最优性间隙"""
        return cls.get("solver_settings.optimality_gap", 0.01)

    # 新增：延误等级配置
    @classmethod
    def delay_levels(cls) -> dict:
        """延误等级定义"""
        return cls.get("delay_levels", {})

    @classmethod
    def get_delay_level_code(cls, delay_minutes: int) -> str:
        """根据延误分钟数获取延误等级代码"""
        levels = cls.delay_levels()
        for level_name, level_config in levels.items():
            min_min = level_config.get("min_minutes", 0)
            max_min = level_config.get("max_minutes", 9999)
            if min_min <= delay_minutes < max_min:
                return level_config.get("code", "0")
        return "0"

    # 新增：求解器配置
    @classmethod
    def solver_config(cls, solver_name: str = None) -> dict:
        """获取求解器配置"""
        if solver_name:
            return cls.get(f"solver.{solver_name}", {})
        return cls.get("solver", {})

    # 新增：场景类型配置
    @classmethod
    def scenario_config(cls, scenario_type: str = None) -> dict:
        """获取场景类型配置"""
        if scenario_type:
            return cls.get(f"scenario_types.{scenario_type}", {})
        return cls.get("scenario_types", {})

    @classmethod
    def get_default_solver(cls, scenario_type: str) -> str:
        """获取场景类型的默认求解器"""
        return cls.get(f"scenario_types.{scenario_type}.default_solver", "fcfs")

    @classmethod
    def scenario_temporary_speed_limit_default_speed(cls) -> int:
        """临时限速场景默认限速值（km/h）"""
        return cls.get("scenario_types.temporary_speed_limit.default_speed_kmh", 200)

    @classmethod
    def scenario_temporary_speed_limit_default_duration(cls) -> int:
        """临时限速场景默认持续时间（分钟）"""
        return cls.get("scenario_types.temporary_speed_limit.default_duration_minutes", 120)

    @classmethod
    def scenario_sudden_failure_default_repair_time(cls) -> int:
        """突发故障场景默认修复时间（分钟）"""
        return cls.get("scenario_types.sudden_failure.default_repair_time_minutes", 60)

    @classmethod
    def default_delay_seconds(cls) -> int:
        """默认延误时间（秒）"""
        return cls.get("constraints.default_delay_seconds", 600)  # 默认10分钟

    # 新增：系统限制
    @classmethod
    def system_limits(cls) -> dict:
        """系统规模限制"""
        return cls.get("system_limits", {})

    @classmethod
    def max_stations(cls) -> int:
        """最大车站数量"""
        return cls.get("system_limits.max_stations", 20)

    @classmethod
    def max_trains(cls) -> int:
        """最大列车数量"""
        return cls.get("system_limits.max_trains", 200)

    # 新增：验证器配置
    @classmethod
    def validator_config(cls) -> dict:
        """验证器配置"""
        return cls.get("validator", {})

    @classmethod
    def standard_section_times(cls) -> list:
        """标准区间运行时间"""
        return cls.get("validator.standard_section_times", [])

    # 新增：日志配置
    @classmethod
    def logging_config(cls) -> dict:
        """日志配置"""
        return cls.get("logging", {})

    @classmethod
    def log_level(cls) -> str:
        """日志级别"""
        return cls.get("logging.level", "INFO")

    @classmethod
    def verbose_solver(cls) -> bool:
        """是否记录详细求解过程"""
        return cls.get("logging.verbose_solver", False)

    @classmethod
    def verbose_llm(cls) -> bool:
        """是否记录LLM调用详情"""
        return cls.get("logging.verbose_llm", False)


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

    # 配置摘要由app.py统一打印，避免重复输出
    pass


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

    # L1 层配置信息
    l1_mode = "微调模型" if L1Config.USE_FINETUNED_MODEL else "Prompt"
    l1_info = f"""
L1数据建模层配置:
  - 实现方式: {l1_mode}"""
    if L1Config.USE_FINETUNED_MODEL:
        l1_info += f"""
  - 模型提供商: {L1Config.FINETUNED_MODEL_PROVIDER}
  - 模型名称: {L1Config.FINETUNED_MODEL_NAME}
  - 基础URL: {L1Config.FINETUNED_MODEL_BASE_URL}
  - 失败回退: {'是' if L1Config.FALLBACK_TO_PROMPT_ON_ERROR else '否'}"""

    return f"""
========================================
        系统配置摘要
========================================
LLM配置:{llm_info}
  - 强制LLM模式: {'是' if LLMConfig.FORCE_LLM_MODE else '否'}
{l1_info}

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
  - L1层支持Prompt和微调模型切换
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
