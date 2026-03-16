# -*- coding: utf-8 -*-
"""
铁路调度系统 - 评估系统模块
对应架构文档第7节：评估系统设计
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import json

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class EvaluationMetrics:
    """评估指标"""
    max_delay_seconds: int
    avg_delay_seconds: float
    total_delay_seconds: int
    affected_trains_count: int


@dataclass
class ComparisonResult:
    """对比结果"""
    is_better_than_baseline: bool
    max_delay_improvement: float  # 百分比
    avg_delay_improvement: float  # 百分比
    recommended_output: bool


@dataclass
class EvaluationResult:
    """评估结果"""
    success: bool
    proposed_metrics: EvaluationMetrics
    baseline_metrics: EvaluationMetrics
    comparison: ComparisonResult
    recommendations: List[str]


class BaselineComparator:
    """
    基线对比器
    对比优化方案与基线策略的效果
    """

    BASELINE_STRATEGIES = {
        "first_come_first_serve": "先到先服务",
        "priority_based": "基于优先级",
        "no_adjustment": "不调整"
    }

    def __init__(self, baseline_strategy: str = "no_adjustment"):
        """
        初始化对比器

        Args:
            baseline_strategy: 基线策略名称
        """
        self.baseline_strategy = baseline_strategy

    def _generate_baseline(
        self,
        original_schedule: Dict[str, List[Dict]],
        delay_injection: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        生成基线调度方案

        Args:
            original_schedule: 原始时刻表
            delay_injection: 延误注入数据

        Returns:
            Dict: 基线方案的时刻表和统计
        """
        if self.baseline_strategy == "no_adjustment":
            # 不调整策略：保持原始时刻表，延误不变
            baseline_schedule = {}
            for train_id, stops in original_schedule.items():
                baseline_stops = []
                for stop in stops:
                    # 检查该站是否有延误
                    delay = 0
                    for injected in delay_injection.get("injected_delays", []):
                        if injected["train_id"] == train_id:
                            if stop["station_code"] == injected.get("location", {}).get("station_code"):
                                delay = injected.get("initial_delay_seconds", 0)
                                break

                    baseline_stops.append({
                        "station_code": stop["station_code"],
                        "station_name": stop.get("station_name", stop["station_code"]),
                        "arrival_time": stop.get("original_arrival", stop["arrival_time"]),
                        "departure_time": stop.get("original_departure", stop["departure_time"]),
                        "delay_seconds": delay
                    })

                baseline_schedule[train_id] = baseline_stops

            return baseline_schedule
        else:
            # 其他基线策略暂未实现
            return original_schedule

    def _calculate_metrics(
        self,
        schedule: Dict[str, List[Dict]]
    ) -> EvaluationMetrics:
        """计算评估指标"""
        all_delays = []

        for train_id, stops in schedule.items():
            for stop in stops:
                delay = stop.get("delay_seconds", 0)
                if delay > 0:
                    all_delays.append(delay)

        return EvaluationMetrics(
            max_delay_seconds=max(all_delays) if all_delays else 0,
            avg_delay_seconds=sum(all_delays) / len(all_delays) if all_delays else 0,
            total_delay_seconds=sum(all_delays),
            affected_trains_count=len(set(
                stop.get("train_id", train_id)
                for train_id, stops in schedule.items()
                for stop in stops
                if stop.get("delay_seconds", 0) > 0
            ))
        )

    def compare(
        self,
        proposed_schedule: Dict[str, List[Dict]],
        original_schedule: Dict[str, List[Dict]],
        delay_injection: Dict[str, Any]
    ) -> EvaluationResult:
        """
        对比评估流程：
        1. 生成基线调度方案
        2. 计算各项评估指标
        3. 输出对比结果

        Args:
            proposed_schedule: 优化后的时刻表
            original_schedule: 原始时刻表
            delay_injection: 延误注入数据

        Returns:
            EvaluationResult: 评估结果
        """
        # Step 1: 生成基线方案
        baseline_schedule = self._generate_baseline(original_schedule, delay_injection)

        # Step 2: 计算各项指标
        proposed_metrics = self._calculate_metrics(proposed_schedule)
        baseline_metrics = self._calculate_metrics(baseline_schedule)

        # Step 3: 计算改进幅度
        if baseline_metrics.max_delay_seconds > 0:
            max_delay_improvement = (
                (baseline_metrics.max_delay_seconds - proposed_metrics.max_delay_seconds)
                / baseline_metrics.max_delay_seconds * 100
            )
        else:
            max_delay_improvement = 0

        if baseline_metrics.avg_delay_seconds > 0:
            avg_delay_improvement = (
                (baseline_metrics.avg_delay_seconds - proposed_metrics.avg_delay_seconds)
                / baseline_metrics.avg_delay_seconds * 100
            )
        else:
            avg_delay_improvement = 0

        # Step 4: 判断是否优于基线
        is_better = (
            proposed_metrics.max_delay_seconds < baseline_metrics.max_delay_seconds or
            proposed_metrics.avg_delay_seconds < baseline_metrics.avg_delay_seconds
        )

        # Step 5: 生成建议
        recommendations = []
        if max_delay_improvement > 0:
            recommendations.append(f"最大延误减少了 {max_delay_improvement:.1f}%")
        if avg_delay_improvement > 0:
            recommendations.append(f"平均延误减少了 {avg_delay_improvement:.1f}%")
        if not is_better:
            recommendations.append("优化方案未能改善延误，建议调整调度策略")

        comparison = ComparisonResult(
            is_better_than_baseline=is_better,
            max_delay_improvement=max_delay_improvement,
            avg_delay_improvement=avg_delay_improvement,
            recommended_output=is_better
        )

        return EvaluationResult(
            success=True,
            proposed_metrics=proposed_metrics,
            baseline_metrics=baseline_metrics,
            comparison=comparison,
            recommendations=recommendations
        )

    def format_result(self, result: EvaluationResult) -> str:
        """格式化评估结果为人类可读文本"""
        return f"""
========================================
        铁路调度 - 评估报告
========================================

【优化方案指标】
- 最大延误时间: {result.proposed_metrics.max_delay_seconds} 秒 ({result.proposed_metrics.max_delay_seconds/60:.1f} 分钟)
- 平均延误时间: {result.proposed_metrics.avg_delay_seconds:.2f} 秒 ({result.proposed_metrics.avg_delay_seconds/60:.2f} 分钟)
- 总延误时间: {result.proposed_metrics.total_delay_seconds} 秒 ({result.proposed_metrics.total_delay_seconds/60:.1f} 分钟)
- 受影响列车数: {result.proposed_metrics.affected_trains_count}

【基线方案指标（不调整）】
- 最大延误时间: {result.baseline_metrics.max_delay_seconds} 秒 ({result.baseline_metrics.max_delay_seconds/60:.1f} 分钟)
- 平均延误时间: {result.baseline_metrics.avg_delay_seconds:.2f} 秒 ({result.baseline_metrics.avg_delay_seconds/60:.2f} 分钟)

【对比分析】
- 最大延误改进: {result.comparison.max_delay_improvement:.1f}%
- 平均延误改进: {result.comparison.avg_delay_improvement:.1f}%
- 优于基线: {'是' if result.comparison.is_better_than_baseline else '否'}
- 推荐输出: {'是' if result.comparison.recommended_output else '否'}

【建议】
{chr(10).join(['- ' + r for r in result.recommendations])}

========================================
        """


class Evaluator:
    """
    评估器
    综合评估调度方案
    """

    def __init__(self, baseline_strategy: str = "no_adjustment"):
        self.comparator = BaselineComparator(baseline_strategy)

    def evaluate(
        self,
        proposed_schedule: Dict[str, List[Dict]],
        original_schedule: Dict[str, List[Dict]],
        delay_injection: Dict[str, Any]
    ) -> EvaluationResult:
        """
        评估调度方案

        Args:
            proposed_schedule: 优化后的时刻表
            original_schedule: 原始时刻表
            delay_injection: 延误注入数据

        Returns:
            EvaluationResult: 评估结果
        """
        return self.comparator.compare(
            proposed_schedule, original_schedule, delay_injection
        )

    def evaluate_multiple_objectives(
        self,
        proposed_schedules: Dict[str, Dict[str, List[Dict]]],
        original_schedule: Dict[str, List[Dict]],
        delay_injection: Dict[str, Any]
    ) -> Dict[str, EvaluationResult]:
        """
        评估多个优化目标的方案

        Args:
            proposed_schedules: 多个优化方案 {objective_name: schedule}
            original_schedule: 原始时刻表
            delay_injection: 延误注入数据

        Returns:
            Dict[str, EvaluationResult]: 各方案的评估结果
        """
        results = {}

        for objective, schedule in proposed_schedules.items():
            results[objective] = self.evaluate(
                schedule, original_schedule, delay_injection
            )

        return results

    def select_best(
        self,
        results: Dict[str, EvaluationResult],
        objective: str = "max_delay"
    ) -> tuple[str, EvaluationResult]:
        """
        选择最佳方案

        Args:
            results: 评估结果字典
            objective: 选择标准 ("max_delay" 或 "avg_delay")

        Returns:
            tuple: (最佳方案名称, 评估结果)
        """
        if objective == "max_delay":
            best_name = min(
                results.keys(),
                key=lambda k: results[k].proposed_metrics.max_delay_seconds
            )
        else:  # avg_delay
            best_name = min(
                results.keys(),
                key=lambda k: results[k].proposed_metrics.avg_delay_seconds
            )

        return best_name, results[best_name]


# 测试代码
if __name__ == "__main__":
    # 示例数据
    original = {
        "G1001": [
            {"station_code": "BJP", "station_name": "北京西", "arrival_time": "08:00:00", "departure_time": "08:10:00", "delay_seconds": 0},
            {"station_code": "TJG", "station_name": "天津西", "arrival_time": "08:35:00", "departure_time": "08:40:00", "delay_seconds": 600},
            {"station_code": "JNZ", "station_name": "济南西", "arrival_time": "09:45:00", "departure_time": "09:50:00", "delay_seconds": 600},
        ],
        "G1003": [
            {"station_code": "BJP", "station_name": "北京西", "arrival_time": "08:20:00", "departure_time": "08:30:00", "delay_seconds": 0},
            {"station_code": "TJG", "station_name": "天津西", "arrival_time": "08:55:00", "departure_time": "09:00:00", "delay_seconds": 900},
            {"station_code": "JNZ", "station_name": "济南西", "arrival_time": "10:05:00", "departure_time": "10:10:00", "delay_seconds": 900},
        ]
    }

    optimized = {
        "G1001": [
            {"station_code": "BJP", "station_name": "北京西", "arrival_time": "08:00:00", "departure_time": "08:10:00", "delay_seconds": 0},
            {"station_code": "TJG", "station_name": "天津西", "arrival_time": "08:35:00", "departure_time": "08:50:00", "delay_seconds": 600},
            {"station_code": "JNZ", "station_name": "济南西", "arrival_time": "09:55:00", "departure_time": "10:00:00", "delay_seconds": 600},
        ],
        "G1003": [
            {"station_code": "BJP", "station_name": "北京西", "arrival_time": "08:20:00", "departure_time": "08:30:00", "delay_seconds": 0},
            {"station_code": "TJG", "station_name": "天津西", "arrival_time": "08:55:00", "departure_time": "09:15:00", "delay_seconds": 900},
            {"station_code": "JNZ", "station_name": "济南西", "arrival_time": "10:20:00", "departure_time": "10:25:00", "delay_seconds": 900},
        ]
    }

    delay_injection = {
        "injected_delays": [
            {"train_id": "G1001", "location": {"station_code": "TJG"}, "initial_delay_seconds": 600},
            {"train_id": "G1003", "location": {"station_code": "TJG"}, "initial_delay_seconds": 900}
        ]
    }

    # 评估
    evaluator = Evaluator()
    result = evaluator.evaluate(optimized, original, delay_injection)

    # 输出报告
    print(evaluator.format_result(result))
