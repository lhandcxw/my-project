# -*- coding: utf-8 -*-
"""
铁路调度系统 - 调度比较技能模块
实现多调度方法的比较和优选功能
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import time
import logging

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.data_models import Train, Station, DelayInjection
from railway_agent.dispatch_skills import DispatchSkillOutput, BaseDispatchSkill
from scheduler_comparison import (
    SchedulerComparator,
    ComparisonCriteria,
    create_comparator,
    LLMOutputAdapter,
    LLMOutputFormat,
    MetricsWeight
)

logger = logging.getLogger(__name__)


class SchedulerComparisonSkill(BaseDispatchSkill):
    """
    调度方法比较技能
    比较FCFS、MIP等多种调度方法，输出最优方案
    """
    
    name = "scheduler_comparison"
    description = """
    比较多种调度方法（FCFS、MIP等），根据用户偏好选择最优调度方案。
    
    适用于：需要综合比较不同调度策略的场景
    输入：延误场景、用户偏好准则
    输出：各调度方法的比较结果、推荐方案、详细指标对比
    """
    
    def __init__(self, trains: List[Train], stations: List[Station]):
        """
        初始化比较技能
        
        Args:
            trains: 列车列表
            stations: 车站列表
        """
        # 不需要调用父类的scheduler初始化
        self.trains = trains
        self.stations = stations
        self.comparator = create_comparator(trains, stations)
        self.llm_adapter = LLMOutputAdapter()
    
    def _parse_criteria(self, user_preference: str) -> ComparisonCriteria:
        """
        解析用户偏好为比较准则
        
        Args:
            user_preference: 用户偏好描述
        
        Returns:
            ComparisonCriteria: 比较准则
        """
        preference_map = {
            "min_max_delay": ComparisonCriteria.MIN_MAX_DELAY,
            "最小最大延误": ComparisonCriteria.MIN_MAX_DELAY,
            "最大延误": ComparisonCriteria.MIN_MAX_DELAY,
            "min_avg_delay": ComparisonCriteria.MIN_AVG_DELAY,
            "最小平均延误": ComparisonCriteria.MIN_AVG_DELAY,
            "平均延误": ComparisonCriteria.MIN_AVG_DELAY,
            "real_time": ComparisonCriteria.REAL_TIME,
            "实时": ComparisonCriteria.REAL_TIME,
            "快速": ComparisonCriteria.REAL_TIME,
            "balance": ComparisonCriteria.BALANCED,
            "均衡": ComparisonCriteria.BALANCED,
            "综合": ComparisonCriteria.BALANCED
        }
        
        for key, criteria in preference_map.items():
            if key in user_preference.lower():
                return criteria
        
        return ComparisonCriteria.BALANCED
    
    def execute(
        self,
        train_ids: List[str],
        station_codes: List[str],
        delay_injection: Dict[str, Any],
        optimization_objective: str = "min_max_delay"
    ) -> DispatchSkillOutput:
        """
        执行调度方法比较
        
        Args:
            train_ids: 受影响列车ID列表
            station_codes: 涉及的车站编码列表
            delay_injection: 延误注入数据
            optimization_objective: 优化目标
        
        Returns:
            DispatchSkillOutput: 比较结果
        """
        start_time = time.time()
        
        try:
            # 解析延误注入数据
            delay_obj = DelayInjection(**delay_injection)
            
            # 解析用户偏好（从scenario_params获取）
            user_preference = delay_injection.get(
                "scenario_params", {}
            ).get("user_preference", "balanced")
            
            criteria = self._parse_criteria(user_preference)
            
            # 执行比较
            result = self.comparator.compare_all(
                delay_obj,
                criteria=criteria,
                objective=optimization_objective
            )
            
            if not result.success:
                return DispatchSkillOutput(
                    optimized_schedule={},
                    delay_statistics={},
                    computation_time=time.time() - start_time,
                    success=False,
                    message="调度方法比较失败",
                    skill_name=self.name
                )
            
            # 获取最优方案的调度结果
            winner = result.winner
            optimized_schedule = winner.result.optimized_schedule if winner else {}
            
            # 生成详细报告
            markdown_report = self.llm_adapter.adapt(result, LLMOutputFormat.MARKDOWN)
            structured_output = self.llm_adapter.generate_structured_output(result)
            
            computation_time = time.time() - start_time
            
            # 构建扩展的延误统计（包含比较信息）
            delay_statistics = {
                # 最优方案指标
                "max_delay_seconds": winner.result.metrics.max_delay_seconds if winner else 0,
                "avg_delay_seconds": winner.result.metrics.avg_delay_seconds if winner else 0,
                "total_delay_seconds": winner.result.metrics.total_delay_seconds if winner else 0,
                "affected_trains_count": winner.result.metrics.affected_trains_count if winner else 0,
                "on_time_rate": winner.result.metrics.on_time_rate if winner else 1.0,
                "computation_time": computation_time,
                
                # 比较相关
                "comparison_criteria": criteria.value,
                "winner_scheduler": winner.scheduler_name if winner else None,
                "all_schedulers": [r.scheduler_name for r in result.results],
                "ranking": [
                    {
                        "rank": r.rank,
                        "scheduler": r.scheduler_name,
                        "max_delay_minutes": r.result.metrics.max_delay_seconds // 60,
                        "avg_delay_minutes": round(r.result.metrics.avg_delay_seconds / 60, 2),
                        "score": round(r.score, 2)
                    }
                    for r in sorted(result.results, key=lambda x: x.rank)
                ],
                "recommendations": result.recommendations
            }
            
            # 构建消息
            message = self._build_message(result)
            
            return DispatchSkillOutput(
                optimized_schedule=optimized_schedule,
                delay_statistics=delay_statistics,
                computation_time=computation_time,
                success=True,
                message=message,
                skill_name=self.name
            )
            
        except Exception as e:
            logger.exception(f"调度比较执行失败: {e}")
            return DispatchSkillOutput(
                optimized_schedule={},
                delay_statistics={},
                computation_time=time.time() - start_time,
                success=False,
                message=f"调度比较执行失败: {str(e)}",
                skill_name=self.name
            )
    
    def _build_message(self, result) -> str:
        """构建结果消息"""
        if not result.winner:
            return "无法确定最优调度方案"
        
        winner = result.winner
        m = winner.result.metrics
        
        parts = [
            f"调度方法比较完成",
            f"最优方案: {winner.scheduler_name}",
            f"最大延误: {m.max_delay_seconds // 60}分钟",
            f"平均延误: {m.avg_delay_seconds / 60:.1f}分钟",
            f"受影响列车: {m.affected_trains_count}列",
            f"准点率: {m.on_time_rate * 100:.1f}%"
        ]
        
        # 添加比较结果
        if len(result.results) > 1:
            second = sorted(result.results, key=lambda x: x.rank)[1]
            parts.append(f"相比{second.scheduler_name}综合得分优{second.score - winner.score:.1f}分")
        
        return " | ".join(parts)
    
    def compare_and_report(
        self,
        delay_injection: Dict[str, Any],
        user_preference: str = "balanced",
        output_format: str = "markdown"
    ) -> Dict[str, Any]:
        """
        执行比较并生成报告（供Agent调用）
        
        Args:
            delay_injection: 延误注入数据
            user_preference: 用户偏好
            output_format: 输出格式
        
        Returns:
            包含比较结果和报告的字典
        """
        delay_obj = DelayInjection(**delay_injection)
        criteria = self._parse_criteria(user_preference)
        
        result = self.comparator.compare_all(delay_obj, criteria=criteria)
        
        # 根据格式选择输出
        format_map = {
            "markdown": LLMOutputFormat.MARKDOWN,
            "json": LLMOutputFormat.JSON,
            "summary": LLMOutputFormat.SUMMARY,
            "detailed": LLMOutputFormat.DETAILED
        }
        fmt = format_map.get(output_format, LLMOutputFormat.MARKDOWN)
        
        return {
            "success": result.success,
            "report": self.llm_adapter.adapt(result, fmt),
            "structured": self.llm_adapter.generate_structured_output(result),
            "recommendations": result.recommendations,
            "winner": result.winner.scheduler_name if result.winner else None
        }


@dataclass
class ComparisonSkillOutput(DispatchSkillOutput):
    """比较技能扩展输出"""
    comparison_report: str = ""
    all_schedulers_result: Dict[str, Any] = None
    llm_prompt: str = ""


def create_comparison_skill(trains: List[Train], stations: List[Station]) -> SchedulerComparisonSkill:
    """
    创建调度比较技能实例
    
    Args:
        trains: 列车列表
        stations: 车站列表
    
    Returns:
        SchedulerComparisonSkill: 比较技能实例
    """
    return SchedulerComparisonSkill(trains, stations)


# 测试代码
if __name__ == "__main__":
    from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
    from models.data_models import InjectedDelay, DelayLocation, ScenarioType
    
    use_real_data(True)
    trains = get_trains_pydantic()[:15]
    stations = get_stations_pydantic()
    
    skill = create_comparison_skill(trains, stations)
    
    # 测试比较
    delay_injection = {
        "scenario_type": "temporary_speed_limit",
        "scenario_id": "COMPARE_TEST",
        "injected_delays": [
            {
                "train_id": trains[0].train_id,
                "location": {
                    "location_type": "station",
                    "station_code": trains[0].schedule.stops[0].station_code
                },
                "initial_delay_seconds": 1200,
                "timestamp": "2024-01-15T10:00:00Z"
            }
        ],
        "affected_trains": [trains[0].train_id],
        "scenario_params": {
            "user_preference": "balanced"
        }
    }
    
    result = skill.execute(
        train_ids=[trains[0].train_id],
        station_codes=[s.station_code for s in stations],
        delay_injection=delay_injection
    )
    
    print(f"成功: {result.success}")
    print(f"消息: {result.message}")
    print(f"最优调度器: {result.delay_statistics.get('winner_scheduler')}")
    print(f"排名: {result.delay_statistics.get('ranking')}")
