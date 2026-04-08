# -*- coding: utf-8 -*-
"""
Prompt管理模块
提供统一的Prompt模板管理和调用接口
"""

from .prompt_manager import PromptManager, get_prompt_manager

__all__ = [
    'PromptManager',
    'get_prompt_manager'
]
