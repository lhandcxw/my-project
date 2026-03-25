# -*- coding: utf-8 -*-
"""
铁路调度系统 - Skills模块
对应架构文档第6节：Skills开发规范
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import json
import time

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.data_models import Train, Station, DelayInjection
from solver.mip_scheduler import MIPScheduler, SolveResult


@dataclass
class DispatchSkillInput:
    """调度Skill输入参数"""
    train_ids: List[str]
    station_codes: List[str]
    delay_injection: Dict[str, Any]
    optimization_objective: str = "min_max_delay"


@dataclass
class DispatchSkillOutput:
    """调度Skill输出结果"""
    optimized_schedule: Dict[str, List[Dict]]
    delay_statistics: Dict[str, Any]
    computation_time: float
    success: bool
    message: str = ""
    skill_name: str = ""


class BaseDispatchSkill:
    """铁路调度Skill基类"""

    name: str = "base_dispatch_skill"
    description: str = "基础调度Skill"

    def __init__(self, scheduler: MIPScheduler):
        self.scheduler = scheduler

    def _parse_delay_injection(self, delay_data: Dict[str, Any]) -> DelayInjection:
        """解析延误注入数据"""
        return DelayInjection(**delay_data)

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay"
    ) -> DispatchSkillOutput:
        """执行调度Skill"""
        raise NotImplementedError


class TemporarySpeedLimitSkill(BaseDispatchSkill):
    """
    临时限速场景调度Skill
    适用于：铁路线路临时限速导致的多列列车延误调整
    """

    name = "temporary_speed_limit_dispatch"
    description = """
    处理临时限速场景的列车调度

    适用于：铁路线路临时限速导致的多列列车延误调整
    输入：受影响列车列表、限速区段、限速值、持续时间
    输出：调整后的时刻表和延误统计
    """

    def __init__(self, scheduler: MIPScheduler):
        super().__init__(scheduler)

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay"
    ) -> DispatchSkillOutput:
        """
        临时限速调度逻辑：

        1. 提取限速参数
        2. 应用MIP求解调整方案
        3. 输出最优调度方案

        Args:
            train_ids: 受影响列车ID列表
            station_codes: 涉及的车站编码列表
            delay_injection: 延误注入数据字典
            optimization_objective: 优化目标

        Returns:
            DispatchSkillOutput: 调度结果
        """
        start_time = time.time()

        # Step 1: 解析延误注入数据
        delay_obj = self._parse_delay_injection(delay_injection)

        # Step 2: 提取限速参数（如果有）
        speed_limit = delay_injection.get("scenario_params", {}).get("limit_speed_kmh", 200)
        affected_section = delay_injection.get("scenario_params", {}).get("affected_section", "")

        # Step 3: MIP求解
        result = self.scheduler.solve(delay_obj, optimization_objective)

        computation_time = time.time() - start_time

        return DispatchSkillOutput(
            optimized_schedule=result.optimized_schedule,
            delay_statistics=result.delay_statistics,
            computation_time=computation_time + result.computation_time,
            success=result.success,
            message=f"临时限速调度完成。限速值: {speed_limit}km/h, 影响区段: {affected_section}",
            skill_name=self.name
        )


class SuddenFailureSkill(BaseDispatchSkill):
    """
    突发故障场景调度Skill
    适用于：列车设备故障、区间占用等单列车故障场景
    """

    name = "sudden_failure_dispatch"
    description = """
    处理突发故障场景的列车调度

    适用于：列车设备故障、区间占用等单列车故障场景
    输入：故障列车信息、故障位置、预计恢复时间
    输出：调整后的时刻表和延误统计
    """

    def __init__(self, scheduler: MIPScheduler):
        super().__init__(scheduler)

    def _analyze_delay_propagation(
        self,
        failure_train: str,
        delay_seconds: int
    ) -> Dict[str, Any]:
        """
        分析延误传播

        Args:
            failure_train: 故障列车ID
            delay_seconds: 延误时间

        Returns:
            Dict: 传播分析结果
        """
        # 简化版：延误会传播到后续列车
        propagation_factor = 0.5  # 传播系数
        return {
            "failure_train": failure_train,
            "initial_delay": delay_seconds,
            "propagation_factor": propagation_factor,
            "estimated_affected_trains": 2
        }

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay"
    ) -> DispatchSkillOutput:
        """
        突发故障调度逻辑：

        1. 识别故障列车和位置
        2. 计算故障导致的延误传播
        3. 应用优化求解

        Args:
            train_ids: 受影响列车ID列表
            station_codes: 涉及的车站编码列表
            delay_injection: 延误注入数据字典
            optimization_objective: 优化目标

        Returns:
            DispatchSkillOutput: 调度结果
        """
        start_time = time.time()

        # Step 1: 解析延误注入数据
        delay_obj = self._parse_delay_injection(delay_injection)

        # Step 2: 提取故障信息
        if delay_obj.injected_delays:
            failure_info = delay_obj.injected_delays[0]
            failure_train = failure_info.train_id
            failure_delay = failure_info.initial_delay_seconds

            # 分析延误传播
            propagation = self._analyze_delay_propagation(failure_train, failure_delay)
        else:
            propagation = {}

        # Step 3: MIP求解
        result = self.scheduler.solve(delay_obj, optimization_objective)

        computation_time = time.time() - start_time

        return DispatchSkillOutput(
            optimized_schedule=result.optimized_schedule,
            delay_statistics=result.delay_statistics,
            computation_time=computation_time + result.computation_time,
            success=result.success,
            message=f"突发故障调度完成。故障列车: {failure_train}, 延误传播分析: {propagation}",
            skill_name=self.name
        )


class SectionInterruptSkill(BaseDispatchSkill):
    """
    区间中断场景调度Skill（预留扩展）
    注意：当前版本暂不实现，仅预留接口
    """

    name = "section_interrupt_dispatch"
    description = """
    处理区间中断场景的列车调度

    注意：当前版本暂不实现，仅预留接口
    适用于：线路中断、严重自然灾害等导致区间无法通行
    需要处理：车底调配、乘务员调整
    """

    def __init__(self, scheduler: MIPScheduler):
        super().__init__(scheduler)

    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay"
    ) -> DispatchSkillOutput:
        """区间中断暂不支持"""
        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics={},
            computation_time=0.0,
            success=False,
            message="区间中断场景当前版本暂不支持",
            skill_name=self.name
        )


def create_skills(scheduler: MIPScheduler) -> Dict[str, BaseDispatchSkill]:
    """
    创建Skills工厂函数

    Args:
        scheduler: MIP调度器

    Returns:
        Dict[str, BaseDispatchSkill]: Skills字典
    """
    return {
        "temporary_speed_limit_skill": TemporarySpeedLimitSkill(scheduler),
        "sudden_failure_skill": SuddenFailureSkill(scheduler),
        "section_interrupt_skill": SectionInterruptSkill(scheduler)
    }


def execute_skill(
    skill_name: str,
    skills: Dict[str, BaseDispatchSkill],
    train_ids: List[str],
    station_codes: List[str],
    delay_injection: Dict[str, Any],
    optimization_objective: str = "min_max_delay"
) -> DispatchSkillOutput:
    """
    执行指定的Skill

    Args:
        skill_name: Skill名称
        skills: Skills字典
        train_ids: 列车ID列表
        station_codes: 车站编码列表
        delay_injection: 延误注入数据
        optimization_objective: 优化目标

    Returns:
        DispatchSkillOutput: 执行结果
    """
    if skill_name not in skills:
        return DispatchSkillOutput(
            optimized_schedule={},
            delay_statistics={},
            computation_time=0.0,
            success=False,
            message=f"Skill '{skill_name}' 不存在",
            skill_name=skill_name
        )

    skill = skills[skill_name]
    return skill.execute(
        train_ids=train_ids,
        station_codes=station_codes,
        delay_injection=delay_injection,
        optimization_objective=optimization_objective
    )


# 测试代码
if __name__ == "__main__":
    from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data

    # 使用真实数据
    use_real_data(True)
    trains = get_trains_pydantic()[:10]
    stations = get_stations_pydantic()

    # 创建调度器
    from solver.mip_scheduler import create_scheduler
    scheduler = create_scheduler(trains, stations)

    # 创建Skills
    skills = create_skills(scheduler)

    # 测试临时限速场景
    print("=== 测试临时限速场景 ===")
    first_train = trains[0].train_id if trains else "G1215"
    first_station = trains[0].schedule.stops[0].station_code if trains and trains[0].schedule.stops else "XSD"
    delay_injection = {
        "scenario_type": "temporary_speed_limit",
        "scenario_id": "TEST_001",
        "injected_delays": [
            {
                "train_id": first_train,
                "location": {"location_type": "station", "station_code": first_station},
                "initial_delay_seconds": 600,
                "timestamp": "2024-01-15T10:00:00Z"
            }
        ],
        "affected_trains": [first_train],
        "scenario_params": {
            "limit_speed_kmh": 200,
            "duration_minutes": 120,
            "affected_section": "TJG -> JNZ"
        }
    }

    # 使用真实数据的站点编码
    station_codes = [s.station_code for s in stations] if stations else ["XSD", "BDD", "DZD", "ZDJ", "SJP"]

    result = execute_skill(
        skill_name="temporary_speed_limit_skill",
        skills=skills,
        train_ids=[first_train],
        station_codes=station_codes,
        delay_injection=delay_injection
    )

    print(f"成功: {result.success}")
    print(f"消息: {result.message}")
    print(f"计算时间: {result.computation_time:.2f}秒")
    print(f"延误统计: {result.delay_statistics}")
