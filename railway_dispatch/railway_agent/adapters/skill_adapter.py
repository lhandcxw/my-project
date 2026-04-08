# -*- coding: utf-8 -*-
"""
Skill 适配器（新架构 - v2）
统一技能调用接口，实际调用技能实现
"""

from typing import Optional, Dict, Any
import logging

from models.common_enums import PlanningIntentCode
from .skill_registry import get_skill_registry

logger = logging.getLogger(__name__)


class SkillAdapter:
    """
    Skill 适配器（新架构 - v2）
    封装技能调用，提供统一的接口

    v2 更新：实际调用技能实现，不再返回占位结果
    """

    # 场景类型到技能的映射
    SCENE_TO_SKILL = {
        "临时限速": "temporary_speed_limit_skill",
        "TEMP_SPEED_LIMIT": "temporary_speed_limit_skill",
        "突发故障": "sudden_failure_skill",
        "SUDDEN_FAILURE": "sudden_failure_skill",
        "区间封锁": "section_interrupt_skill",
        "SECTION_INTERRUPT": "section_interrupt_skill"
    }

    def __init__(self, trains=None, stations=None):
        """
        初始化技能适配器

        Args:
            trains: 列车列表
            stations: 车站列表
        """
        self.skill_registry = get_skill_registry(trains, stations)

    def get_skill_name(self, scene_type: str) -> str:
        """
        根据场景类型获取技能名称

        Args:
            scene_type: 场景类型代码

        Returns:
            str: 技能名称
        """
        return self.SCENE_TO_SKILL.get(scene_type, "temporary_speed_limit_skill")

    def execute_skill(
        self,
        skill_name: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        执行技能

        Args:
            skill_name: 技能名称
            context: 技能上下文，包含:
                - train_ids: 列车ID列表
                - station_codes: 车站编码列表
                - delay_injection: 延误注入数据
                - optimization_objective: 优化目标
                - **kwargs: 额外参数（用于查询类技能）

        Returns:
            Dict: 技能执行结果
        """
        logger.info(f"SkillAdapter 执行技能: {skill_name}")

        # 调用技能注册表执行
        result = self.skill_registry.execute(skill_name, context)

        # 转换为字典格式
        return {
            "skill_name": skill_name,
            "executed": True,
            "success": result.success,
            "message": result.message,
            "computation_time": result.computation_time,
            "delay_statistics": result.delay_statistics,
            "optimized_schedule": result.optimized_schedule
        }

    def intent_to_skill(self, planning_intent: PlanningIntentCode) -> str:
        """
        根据 planning intent 获取技能

        Args:
            planning_intent: 计划意图

        Returns:
            str: 技能名称
        """
        mapping = {
            PlanningIntentCode.RECALCULATE_CORRIDOR: "temporary_speed_limit_skill",
            PlanningIntentCode.RECOVER_FROM_DISRUPTION: "sudden_failure_skill",
            PlanningIntentCode.HANDLE_SECTION_BLOCK: "section_interrupt_skill"
        }
        return mapping.get(planning_intent, "temporary_speed_limit_skill")


# 全局实例
_skill_adapter: Optional[SkillAdapter] = None


def get_skill_adapter(trains=None, stations=None) -> SkillAdapter:
    """
    获取 Skill 适配器实例

    Args:
        trains: 列车列表（仅第一次初始化时使用）
        stations: 车站列表（仅第一次初始化时使用）

    Returns:
        SkillAdapter: 技能适配器实例
    """
    global _skill_adapter
    if _skill_adapter is None:
        _skill_adapter = SkillAdapter(trains, stations)
    return _skill_adapter