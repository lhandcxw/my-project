# -*- coding: utf-8 -*-
"""
基础求解器接口模块
定义统一求解器接口
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


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