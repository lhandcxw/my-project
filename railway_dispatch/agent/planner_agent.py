# -*- coding: utf-8 -*-
"""
铁路调度系统 - Planner Agent模块
对应架构文档第4节：Planner Agent设计
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.data_models import (
    DelayInjection, ScenarioType, DelayLevel, InjectedDelay
)


@dataclass
class StrategyPlan:
    """策略规划结果"""
    modeling_approach: str  # "math_only"
    complexity: str  # "simple", "medium", "complex"
    recommended_skills: List[str]
    expected_outcome: Dict[str, Any]


@dataclass
class PlannerOutput:
    """Planner Agent输出"""
    recognized_scenario: ScenarioType
    delay_level: DelayLevel
    strategy_plan: StrategyPlan
    confidence_score: float
    reasoning: str


class PlannerAgent:
    """
    Planner Agent - 场景识别与策略规划

    功能：
    1. 场景识别：临时限速、突发故障、区间中断
    2. 延误分类：微小(0-5分钟)、小(5-30分钟)、中(30-100分钟)、大(100+分钟)
    3. 策略规划：推荐最优的建模方案
    """

    def __init__(self):
        """初始化Planner Agent"""
        pass

    def _classify_delay_level(self, total_delay_seconds: int) -> DelayLevel:
        """
        分类延误等级

        Args:
            total_delay_seconds: 总延误时间（秒）

        Returns:
            DelayLevel: 延误等级
        """
        delay_minutes = total_delay_seconds / 60

        if delay_minutes < 5:
            return DelayLevel.MICRO
        elif delay_minutes < 30:
            return DelayLevel.SMALL
        elif delay_minutes < 100:
            return DelayLevel.MEDIUM
        else:
            return DelayLevel.LARGE

    def _recognize_scenario(self, delay_injection: DelayInjection) -> tuple[ScenarioType, float]:
        """
        识别场景类型

        Args:
            delay_injection: 延误注入数据

        Returns:
            tuple: (场景类型, 置信度)
        """
        scenario_type = delay_injection.scenario_type
        affected_count = len(delay_injection.affected_trains)

        # 根据注入的延误数据识别场景
        if delay_injection.scenario_params.get("limit_speed_kmh"):
            # 临时限速场景
            return ScenarioType.TEMPORARY_SPEED_LIMIT, 0.95
        elif delay_injection.scenario_params.get("failure_type"):
            # 突发故障场景
            return ScenarioType.SUDDEN_FAILURE, 0.95
        elif affected_count > 5:
            # 可能是区间中断
            return ScenarioType.SECTION_INTERRUPT, 0.7
        else:
            # 默认作为突发故障处理
            return ScenarioType.SUDDEN_FAILURE, 0.8

    def _plan_strategy(
        self,
        scenario_type: ScenarioType,
        delay_level: DelayLevel
    ) -> StrategyPlan:
        """
        规划调度策略

        Args:
            scenario_type: 场景类型
            delay_level: 延误等级

        Returns:
            StrategyPlan: 策略规划结果
        """
        # 基于规则选择建模方案和Skills
        if scenario_type == ScenarioType.TEMPORARY_SPEED_LIMIT:
            if delay_level in [DelayLevel.MICRO, DelayLevel.SMALL]:
                return StrategyPlan(
                    modeling_approach="math_only",
                    complexity="simple",
                    recommended_skills=["temporary_speed_limit_skill"],
                    expected_outcome={"algorithm": "线性规划", "expected_time": "<1秒"}
                )
            else:
                return StrategyPlan(
                    modeling_approach="math_only",
                    complexity="medium",
                    recommended_skills=["temporary_speed_limit_skill"],
                    expected_outcome={"algorithm": "混合整数规划", "expected_time": "<5秒"}
                )

        elif scenario_type == ScenarioType.SUDDEN_FAILURE:
            if delay_level in [DelayLevel.MICRO, DelayLevel.SMALL]:
                return StrategyPlan(
                    modeling_approach="math_only",
                    complexity="simple",
                    recommended_skills=["sudden_failure_skill"],
                    expected_outcome={"algorithm": "整数规划", "expected_time": "<1秒"}
                )
            else:
                return StrategyPlan(
                    modeling_approach="math_only",
                    complexity="medium",
                    recommended_skills=["sudden_failure_skill"],
                    expected_outcome={"algorithm": "混合整数规划", "expected_time": "<5秒"}
                )

        elif scenario_type == ScenarioType.SECTION_INTERRUPT:
            return StrategyPlan(
                modeling_approach="not_supported",
                complexity="complex",
                recommended_skills=["section_interrupt_skill"],
                expected_outcome={"error": "暂不支持该场景"}
            )

        else:
            return StrategyPlan(
                modeling_approach="math_only",
                complexity="simple",
                recommended_skills=["sudden_failure_skill"],
                expected_outcome={"algorithm": "默认整数规划", "expected_time": "<1秒"}
            )

    def process(self, delay_injection: DelayInjection) -> PlannerOutput:
        """
        处理延误注入数据，输出调度策略

        Args:
            delay_injection: 延误注入数据

        Returns:
            PlannerOutput: Planner输出结果
        """
        # Step 1: 场景识别
        scenario_type, scenario_confidence = self._recognize_scenario(delay_injection)

        # Step 2: 计算总延误并分类
        total_delay = sum(d.initial_delay_seconds for d in delay_injection.injected_delays)
        delay_level = self._classify_delay_level(total_delay)

        # Step 3: 延误置信度（基于等级）
        level_confidence = {
            DelayLevel.MICRO: 0.9,
            DelayLevel.SMALL: 0.85,
            DelayLevel.MEDIUM: 0.8,
            DelayLevel.LARGE: 0.75
        }.get(delay_level, 0.8)

        # Step 4: 策略规划
        strategy_plan = self._plan_strategy(scenario_type, delay_level)

        # 综合置信度
        confidence_score = (scenario_confidence + level_confidence) / 2

        # 推理过程
        reasoning = f"""
        场景识别: {scenario_type.value} (置信度: {scenario_confidence:.2f})
        延误等级: {delay_level.value} (置信度: {level_confidence:.2f})
        受影响列车数: {len(delay_injection.affected_trains)}
        推荐策略: {strategy_plan.modeling_approach}
        预计复杂度: {strategy_plan.complexity}
        推荐Skills: {', '.join(strategy_plan.recommended_skills)}
        """

        return PlannerOutput(
            recognized_scenario=scenario_type,
            delay_level=delay_level,
            strategy_plan=strategy_plan,
            confidence_score=confidence_score,
            reasoning=reasoning.strip()
        )

    def explain(self, output: PlannerOutput) -> str:
        """
        解释Planner输出

        Args:
            output: Planner输出

        Returns:
            str: 人类可读的解释
        """
        level_desc = {
            DelayLevel.MICRO: "微小延误（0-5分钟）",
            DelayLevel.SMALL: "小延误（5-30分钟）",
            DelayLevel.MEDIUM: "中延误（30-100分钟）",
            DelayLevel.LARGE: "大延误（100分钟以上）"
        }

        scenario_desc = {
            ScenarioType.TEMPORARY_SPEED_LIMIT: "临时限速场景",
            ScenarioType.SUDDEN_FAILURE: "突发故障场景",
            ScenarioType.SECTION_INTERRUPT: "区间中断场景"
        }

        return f"""
========================================
        铁路调度 Planner 分析报告
========================================

场景类型: {scenario_desc.get(output.recognized_scenario, output.recognized_scenario.value)}
延误等级: {level_desc.get(output.delay_level, output.delay_level.value)}
置信度: {output.confidence_score:.2f}

策略规划:
  - 建模方案: {output.strategy_plan.modeling_approach}
  - 复杂度: {output.strategy_plan.complexity}
  - 推荐Skills: {', '.join(output.strategy_plan.recommended_skills)}
  - 预期结果: {output.strategy_plan.expected_outcome}

详细推理:
{output.reasoning}

========================================
        """


# LLM版本的Planner Agent（可选，需要LangChain和LLM支持）
class LLMPlannerAgent(PlannerAgent):
    """
    基于LLM的Planner Agent
    需要安装: pip install langchain langchain-openai
    """

    def __init__(self, llm=None):
        super().__init__()
        self.llm = llm

    def _create_prompt(self, delay_injection: DelayInjection) -> str:
        """创建LLM提示词"""
        return f"""
你是一个专业的铁路调度规划助手。根据以下延误场景信息，进行智能分析。

## 场景信息
- 场景类型: {delay_injection.scenario_type.value}
- 场景ID: {delay_injection.scenario_id}
- 受影响列车: {', '.join(delay_injection.affected_trains)}
- 场景参数: {delay_injection.scenario_params}

## 注入的延误
{chr(10).join([
    f"- {d.train_id}: 延误{d.initial_delay_seconds}秒, 位置: {d.location.station_code or d.location.section_id}"
    for d in delay_injection.injected_delays
])}

## 任务
请分析并输出JSON格式的结果：
{{
    "recognized_scenario": "场景类型",
    "delay_level": "延误等级 0/5/30/100",
    "modeling_approach": "推荐建模方案",
    "confidence": 0.0-1.0,
    "reasoning": "分析理由"
}}
"""

    def process_with_llm(self, delay_injection: DelayInjection) -> PlannerOutput:
        """使用LLM处理"""
        if self.llm is None:
            # 回退到规则版本
            return self.process(delay_injection)

        # 调用LLM
        prompt = self._create_prompt(delay_injection)
        response = self.llm.invoke(prompt)

        # 解析响应（简化版）
        # 实际实现需要更复杂的解析逻辑
        return self.process(delay_injection)


# 测试代码
if __name__ == "__main__":
    from models.data_models import DelayInjection

    # 测试案例1：临时限速场景
    print("=== 测试案例1: 临时限速场景 ===")
    delay_injection1 = DelayInjection.create_temporary_speed_limit(
        scenario_id="TEST_001",
        train_delays=[
            {"train_id": "G1001", "delay_seconds": 600, "station_code": "TJG"},
            {"train_id": "G1003", "delay_seconds": 900, "station_code": "TJG"}
        ],
        limit_speed=200,
        duration=120,
        affected_section="TJG -> JNZ"
    )

    agent = PlannerAgent()
    output1 = agent.process(delay_injection1)
    print(agent.explain(output1))

    # 测试案例2：突发故障场景
    print("\n=== 测试案例2: 突发故障场景 ===")
    delay_injection2 = DelayInjection.create_sudden_failure(
        scenario_id="TEST_002",
        train_id="G1005",
        delay_seconds=2400,
        station_code="TJG",
        failure_type="vehicle_breakdown",
        repair_time=60
    )

    output2 = agent.process(delay_injection2)
    print(agent.explain(output2))
