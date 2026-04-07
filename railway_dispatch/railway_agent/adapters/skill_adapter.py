# -*- coding: utf-8 -*-
"""
Skill 适配器
统一技能调用接口
"""

from typing import Optional, Dict, Any
import logging

from models.common_enums import PlanningIntentCode

logger = logging.getLogger(__name__)


class SkillAdapter:
    """
    Skill 适配器
    封装技能调用，提供统一的接口
    """
    
    # 场景类型到技能的映射
    SCENE_TO_SKILL = {
        "TEMP_SPEED_LIMIT": "TemporarySpeedLimitSkill",
        "SUDDEN_FAILURE": "SuddenFailureSkill",
        "SECTION_INTERRUPT": "SectionBlockSkill"
    }
    
    def get_skill_name(self, scene_type: str) -> str:
        """
        根据场景类型获取技能名称
        
        Args:
            scene_type: 场景类型代码
            
        Returns:
            str: 技能名称
        """
        return self.SCENE_TO_SKILL.get(scene_type, "DefaultSkill")
    
    def execute_skill(
        self,
        skill_name: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        执行技能
        
        Args:
            skill_name: 技能名称
            context: 技能上下文
            
        Returns:
            Dict: 技能执行结果
        """
        logger.info(f"SkillAdapter 执行技能: {skill_name}")
        
        # TODO: 实际调用技能
        # 这里暂时返回占位结果
        return {
            "skill_name": skill_name,
            "executed": True,
            "result": {}
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
            PlanningIntentCode.RECALCULATE_CORRIDOR: "TemporarySpeedLimitSkill",
            PlanningIntentCode.RECOVER_FROM_DISRUPTION: "SuddenFailureSkill",
            PlanningIntentCode.HANDLE_SECTION_BLOCK: "SectionBlockSkill"
        }
        return mapping.get(planning_intent, "DefaultSkill")


# 全局实例
_skill_adapter: Optional[SkillAdapter] = None


def get_skill_adapter() -> SkillAdapter:
    """获取 Skill 适配器实例"""
    global _skill_adapter
    if _skill_adapter is None:
        _skill_adapter = SkillAdapter()
    return _skill_adapter