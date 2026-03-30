# -*- coding: utf-8 -*-
"""
铁路调度系统 - 调度方法比较模块
支持FCFS、MIP、强化学习等多种调度方法的对比和优选
"""

from .metrics import MetricsDefinition, EvaluationMetrics, MetricsWeight
from .comparator import (
    SchedulerComparator, 
    ComparisonResult, 
    MultiComparisonResult,
    ComparisonCriteria,
    create_comparator
)
from .scheduler_interface import (
    BaseScheduler, 
    SchedulerRegistry, 
    SchedulerResult,
    FCFSSchedulerAdapter, 
    MIPSchedulerAdapter,
    ReinforcementLearningSchedulerAdapter
)
from .llm_adapter import LLMOutputAdapter, LLMOutputFormat

# 兼容性导入 - SchedulerType 也从 scheduler_interface 导入
from .scheduler_interface import SchedulerType

__all__ = [
    # 指标定义
    'MetricsDefinition',
    'EvaluationMetrics', 
    'MetricsWeight',
    # 比较器
    'SchedulerComparator',
    'ComparisonResult',
    'MultiComparisonResult',
    'ComparisonCriteria',
    'create_comparator',
    # 调度器类型
    'SchedulerType',
    'SchedulerResult',
    # 调度器接口
    'BaseScheduler',
    'SchedulerRegistry',
    'FCFSSchedulerAdapter',
    'MIPSchedulerAdapter',
    'ReinforcementLearningSchedulerAdapter',
    # LLM适配器
    'LLMOutputAdapter',
    'LLMOutputFormat'
]
