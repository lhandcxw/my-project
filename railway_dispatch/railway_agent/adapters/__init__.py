# -*- coding: utf-8 -*-
"""
适配器模块
精简版 - 仅保留核心LLM适配器
"""

from railway_agent.adapters.llm_adapter import get_llm_caller
from railway_agent.adapters.llm_prompt_adapter import LLMPromptAdapter, get_llm_prompt_adapter

__all__ = [
    "get_llm_caller",
    "LLMPromptAdapter",
    "get_llm_prompt_adapter",
]
