# -*- coding: utf-8 -*-
"""
评估模块
"""

from .evaluator import (
    HighSpeedEvaluator,
    HighSpeedMetrics,
    HighSpeedEvaluationResult,
    DelayLevel,
    # 向后兼容的别名
    BaselineComparator,
    EvaluationResult,
    EvaluationMetrics,
    Evaluator
)

__all__ = [
    'HighSpeedEvaluator',
    'HighSpeedMetrics',
    'HighSpeedEvaluationResult',
    'DelayLevel',
    'BaselineComparator',
    'EvaluationResult',
    'EvaluationMetrics',
    'Evaluator'
]
