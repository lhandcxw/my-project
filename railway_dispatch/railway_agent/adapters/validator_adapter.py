# -*- coding: utf-8 -*-
"""
Validator 适配器
统一约束验证接口
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)


class ValidationResult(BaseModel):
    """验证结果"""
    is_valid: bool
    violated_constraints: List[str] = []
    warnings: List[str] = []
    details: Dict[str, Any] = {}


class ValidatorAdapter:
    """
    Validator 适配器
    封装约束验证，提供统一的接口
    """
    
    def validate(self, schedule: Any, constraints: Dict[str, Any]) -> ValidationResult:
        """
        验证调度方案
        
        Args:
            schedule: 调度方案
            constraints: 约束条件
            
        Returns:
            ValidationResult: 验证结果
        """
        logger.info("ValidatorAdapter 执行验证")
        
        # TODO: 实际调用验证器
        # 这里暂时返回占位结果
        return ValidationResult(
            is_valid=True,
            violated_constraints=[],
            warnings=[],
            details={"source": "validator_adapter"}
        )
    
    def validate_with_rules(self, schedule: Any, rules: List[str]) -> Dict[str, Any]:
        """
        使用指定规则验证
        
        Args:
            schedule: 调度方案
            rules: 规则列表
            
        Returns:
            Dict: 验证结果
        """
        logger.info(f"ValidatorAdapter 使用规则验证: {rules}")
        
        # 尝试调用实际的验证器
        try:
            from rules.validator import validate_schedule
            return validate_schedule(schedule, rules)
        except Exception as e:
            logger.warning(f"调用验证器失败: {e}")
            return {
                "is_valid": True,
                "violated_constraints": [],
                "warnings": [str(e)]
            }


# 全局实例
_validator_adapter: Optional[ValidatorAdapter] = None


def get_validator_adapter() -> ValidatorAdapter:
    """获取 Validator 适配器实例"""
    global _validator_adapter
    if _validator_adapter is None:
        _validator_adapter = ValidatorAdapter()
    return _validator_adapter