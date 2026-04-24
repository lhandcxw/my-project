# -*- coding: utf-8 -*-
"""
基础求解器接口模块
【已废弃】请使用 scheduler_comparison.scheduler_interface.BaseScheduler

废弃原因：
1. 架构重复：与 Scheduler 系统的 BaseScheduler 功能重叠
2. 维护困难：需要同时维护两套接口
3. 接口不一致：导致使用困惑

替代方案：
使用 scheduler_comparison.scheduler_interface.BaseScheduler

迁移日期：2026-04-21
计划完全移除日期：2026-06-01
"""

import warnings
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

# 添加废弃警告
warnings.warn(
    "BaseSolver/SolverRequest/SolverResponse已废弃，请使用BaseScheduler/DelayInjection/SchedulerResult（scheduler_comparison）",
    DeprecationWarning,
    stacklevel=2
)


class SolverRequest(BaseModel):
    """
    求解器请求模型
    """
    scene_type: str = Field(description="场景类型")
    scene_id: str = Field(description="场景ID")
    trains: list = Field(default_factory=list, description="列车数据")
    stations: list = Field(default_factory=list, description="车站数据")
    injected_delays: list = Field(default_factory=list, description="注入的延误")
    solver_config: Dict[str, Any] = Field(default_factory=dict, description="求解器配置")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")


class SolverResponse(BaseModel):
    """
    求解器响应模型
    """
    success: bool = Field(description="是否成功")
    status: str = Field(default="success", description="状态: success/solver_failed/error")
    schedule: Dict[str, Any] = Field(default_factory=dict, description="调度结果")
    metrics: Dict[str, Any] = Field(default_factory=dict, description="评估指标")
    solving_time_seconds: float = Field(default=0.0, description="求解耗时")
    solver_type: str = Field(default="unknown", description="求解器类型")
    message: str = Field(default="", description="结果消息")
    error: Optional[str] = Field(default=None, description="错误信息")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")


class BaseSolver(ABC):
    """
    基础求解器抽象类
    所有求解器必须实现 solve 方法
    """

    @abstractmethod
    def solve(self, request: SolverRequest) -> SolverResponse:
        """
        执行求解

        Args:
            request: 求解器请求

        Returns:
            SolverResponse: 求解结果
        """
        pass

    def get_solver_type(self) -> str:
        """
        获取求解器类型

        Returns:
            str: 求解器类型名称
        """
        return self.__class__.__name__.replace("Solver", "").lower()