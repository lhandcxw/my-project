# -*- coding: utf-8 -*-
"""
适配器模块
精简版 - 仅保留核心LLM适配器
"""

from railway_agent.adapters.llm_adapter import LLMAdapter, get_llm_adapter
from railway_agent.adapters.llm_prompt_adapter import LLMPromptAdapter, get_llm_prompt_adapter

__all__ = [
    "LLMAdapter",
    "get_llm_adapter",
    "LLMPromptAdapter",
    "get_llm_prompt_adapter",
]
