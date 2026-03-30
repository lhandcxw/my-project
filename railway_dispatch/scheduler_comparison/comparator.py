# -*- coding: utf-8 -*-
"""
铁路调度系统 - 调度方法比较模块
实现多调度器的对比、评分和最优方案选择
"""

from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import json
import time
import logging

from .metrics import (
    MetricsDefinition, 
    EvaluationMetrics, 
    MetricsWeight
)
from .scheduler_interface import (
    BaseScheduler, 
    SchedulerResult, 
    SchedulerType,
    SchedulerRegistry
)

logger = logging.getLogger(__name__)


class ComparisonCriteria(str, Enum):
    """比较准则"""
    MIN_MAX_DELAY = "min_max_delay"       # 最小化最大延误
    MIN_AVG_DELAY = "min_avg_delay"       # 最小化平均延误
    MIN_TOTAL_DELAY = "min_total_delay"   # 最小化总延误
    MAX_ON_TIME_RATE = "max_on_time_rate" # 最大化准点率
    MIN_AFFECTED_TRAINS = "min_affected_trains"  # 最小化受影响列车数
    BALANCED = "balanced"                 # 均衡考虑
    REAL_TIME = "real_time"               # 实时优先（计算速度）


@dataclass
class ComparisonResult:
    """
    单个调度器的比较结果
    """
    scheduler_name: str
    scheduler_type: SchedulerType
    result: SchedulerResult
    rank: int = 0                          # 排名
    score: float = 0.0                     # 综合得分（越小越好）
    is_winner: bool = False                # 是否为最优方案
    improvement_over_baseline: Dict[str, float] = field(default_factory=dict)  # 相对基线的改进
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "scheduler_name": self.scheduler_name,
            "scheduler_type": self.scheduler_type.value,
            "rank": self.rank,
            "score": round(self.score, 2),
            "is_winner": self.is_winner,
            "metrics": self.result.metrics.to_dict(),
            "improvement_over_baseline": self.improvement_over_baseline,
            "success": self.result.success,
            "message": self.result.message
        }


@dataclass
class MultiComparisonResult:
    """
    多调度器比较结果
    """
    success: bool
    criteria: ComparisonCriteria
    results: List[ComparisonResult]
    winner: Optional[ComparisonResult]
    baseline_metrics: Optional[EvaluationMetrics]
    computation_time: float
    recommendations: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "success": self.success,
            "criteria": self.criteria.value,
            "winner": self.winner.to_dict() if self.winner else None,
            "all_results": [r.to_dict() for r in self.results],
            "baseline_metrics": self.baseline_metrics.to_dict() if self.baseline_metrics else None,
            "computation_time": round(self.computation_time, 4),
            "recommendations": self.recommendations
        }
    
    def get_ranking_table(self) -> str:
        """生成排名表格"""
        lines = [
            "=" * 80,
            f"{'调度器比较结果':^78}",
            "=" * 80,
            f"{'排名':<6}{'调度器':<20}{'最大延误':<12}{'平均延误':<12}{'受影响列车':<12}{'计算时间':<12}",
            "-" * 80
        ]
        
        for r in sorted(self.results, key=lambda x: x.rank):
            m = r.result.metrics
            winner_mark = " ★" if r.is_winner else ""
            lines.append(
                f"{r.rank:<6}{r.scheduler_name:<20}"
                f"{m.max_delay_seconds // 60}分钟{'':<6}"
                f"{m.avg_delay_seconds / 60:.1f}分钟{'':<5}"
                f"{m.affected_trains_count}列{'':<8}"
                f"{m.computation_time:.2f}秒{winner_mark}"
            )
        
        lines.append("=" * 80)
        return "\n".join(lines)


class SchedulerComparator:
    """
    调度器比较器
    支持多调度器的对比评估和最优方案选择
    """
    
    def __init__(
        self,
        trains: List,
        stations: List,
        default_criteria: ComparisonCriteria = ComparisonCriteria.BALANCED
    ):
        """
        初始化比较器
        
        Args:
            trains: 列车列表
            stations: 车站列表
            default_criteria: 默认比较准则
        """
        self.trains = trains
        self.stations = stations
        self.default_criteria = default_criteria
        self._schedulers: Dict[str, BaseScheduler] = {}
    
    def register_scheduler(self, scheduler: BaseScheduler):
        """注册调度器"""
        self._schedulers[scheduler.name] = scheduler
    
    def register_scheduler_by_name(
        self,
        name: str,
        **kwargs
    ) -> Optional[BaseScheduler]:
        """通过名称注册调度器"""
        scheduler = SchedulerRegistry.create(name, self.trains, self.stations, **kwargs)
        if scheduler:
            self._schedulers[scheduler.name] = scheduler
        return scheduler
    
    def get_scheduler(self, name: str) -> Optional[BaseScheduler]:
        """获取已注册的调度器"""
        return self._schedulers.get(name)
    
    def list_schedulers(self) -> List[str]:
        """列出所有已注册的调度器"""
        return list(self._schedulers.keys())
    
    def _get_weights_for_criteria(self, criteria: ComparisonCriteria) -> MetricsWeight:
        """根据比较准则获取权重配置"""
        criteria_weight_map = {
            ComparisonCriteria.MIN_MAX_DELAY: MetricsWeight.for_min_max_delay,
            ComparisonCriteria.MIN_AVG_DELAY: MetricsWeight.for_min_avg_delay,
            ComparisonCriteria.BALANCED: MetricsWeight.for_balance,
            ComparisonCriteria.REAL_TIME: MetricsWeight.for_real_time
        }
        
        weight_func = criteria_weight_map.get(criteria, MetricsWeight.for_balance)
        return weight_func()
    
    def _calculate_score(
        self,
        metrics: EvaluationMetrics,
        weights: MetricsWeight
    ) -> float:
        """
        计算综合得分
        
        Args:
            metrics: 评估指标
            weights: 权重配置
        
        Returns:
            综合得分（越小越好）
        """
        # 归一化权重
        nw = weights.normalize()
        
        # 计算加权得分
        # 注意：所有指标都是越小越好，除了准点率
        score = (
            metrics.max_delay_seconds * nw.max_delay_weight +
            metrics.avg_delay_seconds * nw.avg_delay_weight +
            metrics.total_delay_seconds * nw.total_delay_weight * 0.1 +  # 缩放
            metrics.affected_trains_count * 60 * nw.affected_trains_weight +  # 转换为秒
            metrics.computation_time * 60 * nw.computation_time_weight +  # 转换为秒
            (1 - metrics.on_time_rate) * 3600 * nw.on_time_rate_weight  # 准点率转换为时间
        )
        
        return score
    
    def compare_all(
        self,
        delay_injection,
        criteria: Optional[ComparisonCriteria] = None,
        scheduler_names: Optional[List[str]] = None,
        objective: str = "min_max_delay"
    ) -> MultiComparisonResult:
        """
        比较所有调度器
        
        Args:
            delay_injection: 延误注入信息
            criteria: 比较准则
            scheduler_names: 要比较的调度器名称列表（None表示全部）
            objective: 优化目标
        
        Returns:
            MultiComparisonResult: 比较结果
        """
        start_time = time.time()
        criteria = criteria or self.default_criteria
        weights = self._get_weights_for_criteria(criteria)
        
        # 确定要比较的调度器
        if scheduler_names:
            schedulers_to_compare = {
                name: s for name, s in self._schedulers.items() 
                if name in scheduler_names or s.scheduler_type.value in scheduler_names
            }
        else:
            schedulers_to_compare = self._schedulers
        
        if not schedulers_to_compare:
            return MultiComparisonResult(
                success=False,
                criteria=criteria,
                results=[],
                winner=None,
                baseline_metrics=None,
                computation_time=time.time() - start_time,
                recommendations=["没有可用的调度器进行比较"]
            )
        
        # 执行所有调度器并收集结果
        results: List[ComparisonResult] = []
        
        for name, scheduler in schedulers_to_compare.items():
            try:
                logger.info(f"执行调度器: {name}")
                result = scheduler.solve(delay_injection, objective)
                
                if result.success:
                    score = self._calculate_score(result.metrics, weights)
                    comparison_result = ComparisonResult(
                        scheduler_name=name,
                        scheduler_type=scheduler.scheduler_type,
                        result=result,
                        score=score
                    )
                    results.append(comparison_result)
                else:
                    logger.warning(f"调度器 {name} 执行失败: {result.message}")
                    
            except Exception as e:
                logger.error(f"调度器 {name} 执行异常: {e}")
        
        if not results:
            return MultiComparisonResult(
                success=False,
                criteria=criteria,
                results=[],
                winner=None,
                baseline_metrics=None,
                computation_time=time.time() - start_time,
                recommendations=["所有调度器执行失败"]
            )
        
        # 计算基线指标（所有方案的均值或最差方案）
        baseline_metrics = self._calculate_baseline(results)
        
        # 计算相对基线的改进
        for r in results:
            r.improvement_over_baseline = self._calculate_improvement(
                r.result.metrics, baseline_metrics
            )
        
        # 排序并确定排名
        results.sort(key=lambda x: x.score)
        for i, r in enumerate(results):
            r.rank = i + 1
        
        # 确定最优方案
        winner = results[0] if results else None
        if winner:
            winner.is_winner = True
        
        # 生成建议
        recommendations = self._generate_recommendations(results, criteria, winner)
        
        return MultiComparisonResult(
            success=True,
            criteria=criteria,
            results=results,
            winner=winner,
            baseline_metrics=baseline_metrics,
            computation_time=time.time() - start_time,
            recommendations=recommendations
        )
    
    def _calculate_baseline(self, results: List[ComparisonResult]) -> EvaluationMetrics:
        """计算基线指标"""
        if not results:
            return EvaluationMetrics()
        
        # 使用所有方案的平均值作为基线
        n = len(results)
        return EvaluationMetrics(
            max_delay_seconds=sum(r.result.metrics.max_delay_seconds for r in results) // n,
            avg_delay_seconds=sum(r.result.metrics.avg_delay_seconds for r in results) / n,
            total_delay_seconds=sum(r.result.metrics.total_delay_seconds for r in results) // n,
            affected_trains_count=sum(r.result.metrics.affected_trains_count for r in results) // n,
            on_time_rate=sum(r.result.metrics.on_time_rate for r in results) / n,
            computation_time=sum(r.result.metrics.computation_time for r in results) / n
        )
    
    def _calculate_improvement(
        self,
        metrics: EvaluationMetrics,
        baseline: EvaluationMetrics
    ) -> Dict[str, float]:
        """计算相对基线的改进百分比"""
        def calc_improvement(current, base, lower_is_better=True):
            if base == 0:
                return 0.0
            diff = (base - current) / base * 100
            return diff if lower_is_better else -diff
        
        return {
            "max_delay_improvement": calc_improvement(
                metrics.max_delay_seconds, baseline.max_delay_seconds
            ),
            "avg_delay_improvement": calc_improvement(
                metrics.avg_delay_seconds, baseline.avg_delay_seconds
            ),
            "total_delay_improvement": calc_improvement(
                metrics.total_delay_seconds, baseline.total_delay_seconds
            ),
            "affected_trains_improvement": calc_improvement(
                metrics.affected_trains_count, baseline.affected_trains_count
            ),
            "on_time_rate_improvement": calc_improvement(
                metrics.on_time_rate, baseline.on_time_rate, lower_is_better=False
            ),
            "computation_time_improvement": calc_improvement(
                metrics.computation_time, baseline.computation_time
            )
        }
    
    def _generate_recommendations(
        self,
        results: List[ComparisonResult],
        criteria: ComparisonCriteria,
        winner: Optional[ComparisonResult]
    ) -> List[str]:
        """生成推荐建议"""
        recommendations = []
        
        if not winner:
            recommendations.append("无法确定最优方案")
            return recommendations
        
        m = winner.result.metrics
        
        # 根据准则生成建议
        if criteria == ComparisonCriteria.MIN_MAX_DELAY:
            recommendations.append(
                f"推荐使用 {winner.scheduler_name}，最大延误为 {m.max_delay_seconds // 60} 分钟"
            )
        elif criteria == ComparisonCriteria.MIN_AVG_DELAY:
            recommendations.append(
                f"推荐使用 {winner.scheduler_name}，平均延误为 {m.avg_delay_seconds / 60:.1f} 分钟"
            )
        elif criteria == ComparisonCriteria.REAL_TIME:
            recommendations.append(
                f"推荐使用 {winner.scheduler_name}，计算时间为 {m.computation_time:.2f} 秒"
            )
        else:
            recommendations.append(
                f"推荐使用 {winner.scheduler_name}，综合得分最优"
            )
        
        # 添加详细说明
        if m.affected_trains_count > 0:
            recommendations.append(f"受影响列车: {m.affected_trains_count} 列")
        
        if m.on_time_rate < 1.0:
            recommendations.append(f"准点率: {m.on_time_rate * 100:.1f}%")
        
        # 比较分析
        if len(results) > 1:
            second = results[1]
            score_diff = second.score - winner.score
            if score_diff > 0:
                recommendations.append(
                    f"相比 {second.scheduler_name} 综合得分优 {score_diff:.1f} 分"
                )
        
        return recommendations
    
    def compare_two(
        self,
        scheduler_a_name: str,
        scheduler_b_name: str,
        delay_injection,
        criteria: Optional[ComparisonCriteria] = None
    ) -> Dict[str, Any]:
        """
        比较两个调度器
        
        Args:
            scheduler_a_name: 调度器A名称
            scheduler_b_name: 调度器B名称
            delay_injection: 延误注入信息
            criteria: 比较准则
        
        Returns:
            比较结果字典
        """
        result = self.compare_all(
            delay_injection,
            criteria=criteria,
            scheduler_names=[scheduler_a_name, scheduler_b_name]
        )
        
        if not result.success or len(result.results) < 2:
            return {
                "success": False,
                "message": "比较失败"
            }
        
        a_result = next((r for r in result.results if r.scheduler_name == scheduler_a_name), None)
        b_result = next((r for r in result.results if r.scheduler_name == scheduler_b_name), None)
        
        return {
            "success": True,
            "scheduler_a": a_result.to_dict() if a_result else None,
            "scheduler_b": b_result.to_dict() if b_result else None,
            "winner": result.winner.scheduler_name if result.winner else None,
            "recommendations": result.recommendations
        }
    
    def get_best_for_criteria(
        self,
        delay_injection,
        criteria: ComparisonCriteria,
        objective: str = "min_max_delay"
    ) -> Tuple[Optional[ComparisonResult], MultiComparisonResult]:
        """
        根据指定准则获取最优方案
        
        Args:
            delay_injection: 延误注入信息
            criteria: 比较准则
            objective: 优化目标
        
        Returns:
            (最优结果, 完整比较结果)
        """
        result = self.compare_all(delay_injection, criteria, objective=objective)
        return result.winner, result


def create_comparator(
    trains: List,
    stations: List,
    include_fcfs: bool = True,
    include_mip: bool = True,
    include_rl: bool = False,
    **kwargs
) -> SchedulerComparator:
    """
    创建比较器并注册调度器
    
    Args:
        trains: 列车列表
        stations: 车站列表
        include_fcfs: 是否包含FCFS调度器
        include_mip: 是否包含MIP调度器
        include_rl: 是否包含强化学习调度器
        **kwargs: 其他参数
    
    Returns:
        配置好的比较器
    """
    comparator = SchedulerComparator(trains, stations)
    
    if include_fcfs:
        comparator.register_scheduler_by_name("fcfs", **kwargs)
    
    if include_mip:
        comparator.register_scheduler_by_name("mip", **kwargs)
    
    if include_rl:
        comparator.register_scheduler_by_name("rl", **kwargs)
    
    return comparator


# 测试代码
if __name__ == "__main__":
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
    from models.data_models import InjectedDelay, DelayLocation, ScenarioType, DelayInjection
    
    use_real_data(True)
    trains = get_trains_pydantic()[:20]
    stations = get_stations_pydantic()
    
    # 创建比较器
    comparator = create_comparator(trains, stations)
    print(f"已注册调度器: {comparator.list_schedulers()}")
    
    # 创建延误场景
    delay_injection = DelayInjection(
        scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
        scenario_id="COMPARE_TEST",
        injected_delays=[
            InjectedDelay(
                train_id=trains[0].train_id,
                location=DelayLocation(
                    location_type="station",
                    station_code=trains[0].schedule.stops[0].station_code
                ),
                initial_delay_seconds=1200,  # 20分钟
                timestamp="2024-01-15T10:00:00Z"
            )
        ],
        affected_trains=[trains[0].train_id]
    )
    
    # 执行比较
    result = comparator.compare_all(delay_injection)
    print(result.get_ranking_table())
    
    if result.winner:
        print(f"\n最优方案: {result.winner.scheduler_name}")
        print(f"建议: {result.recommendations}")
