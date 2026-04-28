# -*- coding: utf-8 -*-
"""
策略引擎

【实现类型】规则驱动（安全层）
【设计定位】独立于 LLM 的规则安全层，对 L4 评估结果做最终决策。
  LLM 无权覆盖 PolicyEngine 的决策结果，确保系统安全性和可审计性。
【规则内容】基于场景类型的阈值判定（max_delay_minutes, min_feasibility_score, max_risk_level）

根据结构化评估结果做最终决策（是否采用主解/回退基线/重新求解）
v3.2: 阈值参数化
"""

from typing import Dict, Any, List, Optional
import logging
import json
import os

from models.common_enums import PolicyDecisionType
from models.preprocess_models import PolicyDecision

logger = logging.getLogger(__name__)


# ============== 配置参数 ==============

# 默认阈值配置
DEFAULT_POLICY_THRESHOLDS = {
    "TEMP_SPEED_LIMIT": {
        "max_delay_minutes": 30,
        "min_feasibility_score": 0.5,
        "max_risk_level": "medium"
    },
    "SUDDEN_FAILURE": {
        "max_delay_minutes": 45,
        "min_feasibility_score": 0.4,
        "max_risk_level": "medium"
    },
    "SECTION_INTERRUPT": {
        "max_delay_minutes": 60,
        "min_feasibility_score": 0.3,
        "max_risk_level": "high"
    },
    "UNKNOWN": {
        "max_delay_minutes": 30,
        "min_feasibility_score": 0.5,
        "max_risk_level": "medium"
    }
}

# 全局配置（可从环境变量或配置文件覆盖）
POLICY_THRESHOLDS = DEFAULT_POLICY_THRESHOLDS


def load_policy_thresholds_from_env():
    """从环境变量加载策略阈值配置"""
    global POLICY_THRESHOLDS
    
    # 可以通过环境变量覆盖
    max_delay = os.environ.get("POLICY_MAX_DELAY_MINUTES")
    if max_delay:
        for scene in POLICY_THRESHOLDS:
            POLICY_THRESHOLDS[scene]["max_delay_minutes"] = int(max_delay)
    
    min_score = os.environ.get("POLICY_MIN_FEASIBILITY_SCORE")
    if min_score:
        for scene in POLICY_THRESHOLDS:
            POLICY_THRESHOLDS[scene]["min_feasibility_score"] = float(min_score)


class PolicyEngine:
    """
    策略引擎（【规则安全层】）
    根据评估结果做最终决策，LLM 无权覆盖决策结果。
    本层为纯规则实现，独立于 LLM，确保决策的可审计性和安全性。
    v3.2: 阈值从配置读取
    """
    
    def __init__(self, thresholds: Optional[Dict[str, Dict]] = None):
        """
        初始化策略引擎
        
        Args:
            thresholds: 可选的阈值配置，默认使用全局配置
        """
        self.thresholds = thresholds or POLICY_THRESHOLDS
    
    def _get_thresholds(self, scene_type: str) -> Dict[str, Any]:
        """获取指定场景类型的阈值配置"""
        return self.thresholds.get(scene_type, self.thresholds.get("UNKNOWN", DEFAULT_POLICY_THRESHOLDS["UNKNOWN"]))
    
    def make_decision(
        self,
        is_successful: bool,
        validation_result: Optional[Dict[str, Any]] = None,
        evaluation_result: Optional[Dict[str, Any]] = None,
        solver_metrics: Optional[Dict[str, Any]] = None,
        risk_warnings: Optional[List[str]] = None,
        llm_suggestion: Optional[str] = None,
        scene_type: str = "UNKNOWN"
    ) -> PolicyDecision:
        """
        根据评估结果做最终决策
        
        决策规则（优先级从高到低）：
        1. 求解失败 -> RERUN
        2. 验证失败 -> FALLBACK
        3. 评估不可行 -> FALLBACK
        4. 最大延误过大 -> FALLBACK（阈值来自配置）
        5. 有严重风险警告 -> FALLBACK
        6. LLM 建议回退 -> FALLBACK（仅供参考）
        7. 默认 -> ACCEPT
        
        Args:
            is_successful: 求解是否成功
            validation_result: 验证结果
            evaluation_result: 评估结果
            solver_metrics: 求解器指标
            risk_warnings: 风险警告列表
            llm_suggestion: LLM 建议（仅供参考，不能覆盖决策）
            scene_type: 场景类型（用于读取配置）
            
        Returns:
            PolicyDecision: 策略决策
        """
        logger.info(f"PolicyEngine 开始决策, scene_type={scene_type}")
        
        # 获取场景类型的阈值配置
        scene_thresholds = self._get_thresholds(scene_type)
        max_delay_threshold = scene_thresholds.get("max_delay_minutes", 30)
        min_feasibility_score = scene_thresholds.get("min_feasibility_score", 0.5)
        
        # 规则1：求解失败 -> RERUN
        if not is_successful:
            logger.info("决策: RERUN (求解失败)")
            return PolicyDecision(
                decision=PolicyDecisionType.RERUN,
                reason="求解执行失败",
                confidence=1.0,
                suggested_fixes=["检查求解器配置", "尝试其他求解器", "检查输入数据"]
            )
        
        # 规则2：验证失败 -> FALLBACK
        if validation_result and not validation_result.get("is_valid", True):
            violated = validation_result.get("violated_constraints", [])
            logger.info(f"决策: FALLBACK (验证失败): {violated}")
            return PolicyDecision(
                decision=PolicyDecisionType.FALLBACK,
                reason=f"约束验证失败: {', '.join(violated)}",
                confidence=0.95,
                suggested_fixes=["调整调度方案", "放松约束条件"]
            )
        
        # 获取评估指标
        is_feasible = True
        max_delay = 0
        feasibility_score = 1.0
        
        if evaluation_result:
            is_feasible = evaluation_result.get("is_feasible", True)
            max_delay = evaluation_result.get("max_delay_minutes", 0)
            feasibility_score = evaluation_result.get("feasibility_score", 1.0)
        
        # 规则3：评估不可行 -> FALLBACK
        if not is_feasible:
            logger.info(f"决策: FALLBACK (评估不可行): is_feasible={is_feasible}")
            return PolicyDecision(
                decision=PolicyDecisionType.FALLBACK,
                reason="评估判定方案不可行",
                confidence=0.9,
                suggested_fixes=["重新求解", "调整约束"]
            )
        
        # 规则4：评估可行评分过低 -> RERUN
        if feasibility_score < min_feasibility_score:
            logger.info(f"决策: RERUN (可行评分过低): {feasibility_score:.2f}")
            return PolicyDecision(
                decision=PolicyDecisionType.RERUN,
                reason=f"可行评分过低 ({feasibility_score:.2f})",
                confidence=0.8,
                suggested_fixes=["补充更多约束条件", "调整求解时间限制"]
            )
        
        # 规则5：最大延误过大 -> FALLBACK
        if max_delay > max_delay_threshold:
            logger.info(f"决策: FALLBACK (最大延误过大): {max_delay}分钟")
            return PolicyDecision(
                decision=PolicyDecisionType.FALLBACK,
                reason=f"最大延误过大 ({max_delay}分钟 > {max_delay_threshold}分钟)",
                confidence=0.85,
                suggested_fixes=["等待延误自然消除", "调整列车发车间隔"]
            )
        
        # 规则6：有严重风险警告 -> FALLBACK
        severe_warnings = []
        if risk_warnings:
            severe_warnings = [w for w in risk_warnings if any(kw in w for kw in ["严重", "危险", "critical", "danger"])]
        
        if severe_warnings:
            logger.info(f"决策: FALLBACK (严重风险): {severe_warnings}")
            return PolicyDecision(
                decision=PolicyDecisionType.FALLBACK,
                reason=f"存在严重风险: {', '.join(severe_warnings)}",
                confidence=0.9,
                suggested_fixes=["人工干预确认", "降低风险后再执行"]
            )
        
        # 规则7：LLM 建议仅供参考，不能覆盖决策
        # 如果 LLM 建议与规则决策冲突，以规则为准
        if llm_suggestion:
            logger.info(f"LLM 建议: {llm_suggestion}（仅供参考，不影响决策）")
        
        # 规则8：默认 -> ACCEPT
        logger.info("决策: ACCEPT (默认采用主解)")
        return PolicyDecision(
            decision=PolicyDecisionType.ACCEPT,
            reason="方案可行，采用主解",
            confidence=0.8,
            suggested_fixes=[]
        )
    
    def decide_with_workflow_result(
        self,
        workflow_result: Any
    ) -> PolicyDecision:
        """
        根据工作流结果做决策
        
        Args:
            workflow_result: 工作流结果对象
            
        Returns:
            PolicyDecision: 策略决策
        """
        # 提取必要信息
        is_successful = getattr(workflow_result, 'success', False)
        
        # 提取评估报告
        eval_report = getattr(workflow_result, 'evaluation_report', None)
        evaluation_result = None
        if eval_report:
            evaluation_result = {
                "is_feasible": getattr(eval_report, 'is_feasible', True),
                "total_delay_minutes": getattr(eval_report, 'total_delay_minutes', 0),
                "max_delay_minutes": getattr(eval_report, 'max_delay_minutes', 0),
                "risk_warnings": getattr(eval_report, 'risk_warnings', [])
            }
        
        # 提取求解指标
        solver_result = getattr(workflow_result, 'solver_result', None)
        solver_metrics = None
        if solver_result:
            solver_metrics = getattr(solver_result, 'metrics', None) or {}
        
        # 提取风险警告
        risk_warnings = []
        if evaluation_result:
            risk_warnings = evaluation_result.get("risk_warnings", [])
        
        # 提取 LLM 摘要（仅供参考）
        llm_summary = getattr(workflow_result, 'llm_summary', None)
        
        return self.make_decision(
            is_successful=is_successful,
            validation_result=None,  # 【设计说明】验证由 L3 求解层和 L4 评估层负责，PolicyEngine 基于评估结果做最终决策
            evaluation_result=evaluation_result,
            solver_metrics=solver_metrics,
            risk_warnings=risk_warnings,
            llm_suggestion=llm_summary
        )


# 全局实例
_policy_engine: Optional[PolicyEngine] = None


def get_policy_engine() -> PolicyEngine:
    """获取策略引擎实例"""
    global _policy_engine
    if _policy_engine is None:
        _policy_engine = PolicyEngine()
    return _policy_engine