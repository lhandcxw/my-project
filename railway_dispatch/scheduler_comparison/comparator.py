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
    HighSpeedMetricsWeight as MetricsWeight
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
            "=" * 100,
            f"{'调度器比较结果':^98}",
            "=" * 100,
            "{:<6}{:<18}{:<14}{:<18}{:<14}{:<14}{:<12}".format("排名", "调度器", "最大延误", "晚点列车平均延误", "总延误", "受影响列车", "计算时间"),
            "-" * 100
        ]

        for r in sorted(self.results, key=lambda x: x.rank):
            m = r.result.metrics
            winner_mark = " ★" if r.is_winner else ""
            # 【专家修复】格式化小数位（统一保留2位小数）
            max_delay_min = m.max_delay_seconds / 60
            avg_delay_min = m.avg_delay_seconds / 60
            total_delay_min = m.total_delay_seconds / 60

            max_delay_str = f"{max_delay_min:.2f}分钟"
            # 【关键修复】avg_delay 明确标注分母（晚点列车数），避免调度员误解
            avg_delay_str = f"{avg_delay_min:.2f}分/{m.affected_trains_count}列"
            total_delay_str = f"{total_delay_min:.2f}分钟"

            lines.append(
                f"{r.rank:<6}{r.scheduler_name:<18}"
                f"{max_delay_str:<14}"
                f"{avg_delay_str:<18}"
                f"{total_delay_str:<14}"
                f"{m.affected_trains_count}列{' ' * 10}"
                f"{m.computation_time:.2f}秒{winner_mark}"
            )

        lines.append("=" * 100)
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
        default_criteria: ComparisonCriteria = ComparisonCriteria.MIN_TOTAL_DELAY  # 【专家优化】默认最小化总延误
    ):
        """
        初始化比较器

        Args:
            trains: 列车列表
            stations: 车站列表
            default_criteria: 默认比较准则（默认：最小化总延误）

        专家说明：
        默认使用 MIN_TOTAL_DELAY 是因为：
        1. 总延误是高铁运营的核心KPI（中国铁路总调度系统标准）
        2. 反映整体系统效率：列车周转、资源利用率、运营成本
        3. 京广高铁等高密度线路：总延误是调度决策的主要依据
        4. 参考文献：《高速铁路列车运行图编制理论与方法》
        5. 与服务质量、旅客满意度直接相关
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
            # 只用传入的名称作为key，避免重复注册
            # 【修复】移除重复注册：scheduler.name 会导致同一调度器被注册两次
            if name not in self._schedulers:
                self._schedulers[name] = scheduler
            else:
                logger.debug(f"调度器 {name} 已存在，跳过注册")
        return scheduler
    
    def get_scheduler(self, name: str) -> Optional[BaseScheduler]:
        """获取已注册的调度器"""
        return self._schedulers.get(name)
    
    def list_schedulers(self) -> List[str]:
        """列出所有已注册的调度器"""
        return list(self._schedulers.keys())
    
    def _get_weights_for_criteria(self, criteria: ComparisonCriteria, optimization_objective: str = None) -> MetricsWeight:
        """【专家修复】根据比较准则和优化目标动态获取权重配置（真正的智能化）"""
        
        if criteria == ComparisonCriteria.MIN_MAX_DELAY:
            # 【智能化】用户明确要求最小化最大延误
            # max_delay_first的优势：专门优化最大延误
            return MetricsWeight(
                max_delay_weight=2.0,       # 最高权重
                avg_delay_weight=0.5,       # 较低
                total_delay_weight=0.3,     # 低
                affected_trains_weight=0.5, # 较低
                propagation_depth_weight=0.3,  # 低
                propagation_breadth_weight=0.3,  # 低
                computation_time_weight=0.2,  # 不关心速度
                delay_variance_weight=0.3,
                recovery_rate_weight=0.3,
                on_time_rate_weight=0.4
            ).normalize()
        
        elif criteria == ComparisonCriteria.MIN_AVG_DELAY:
            # 【智能化】用户明确要求最小化平均延误
            # mip/hierarchical的优势：平均延误控制更精确
            return MetricsWeight.for_min_avg_delay()
        
        elif criteria == ComparisonCriteria.MIN_TOTAL_DELAY:
            # 【智能化】用户明确要求最小化总延误
            return MetricsWeight.for_min_total_delay()
        
        elif criteria == ComparisonCriteria.REAL_TIME:
            # 【智能化】用户明确要求实时（求解速度快）
            # fcfs的优势：最快（毫秒级）
            # max_delay_first：较快（秒级）
            return MetricsWeight(
                max_delay_weight=0.5,       # 较低
                avg_delay_weight=0.5,       # 较低
                total_delay_weight=0.3,     # 低
                affected_trains_weight=0.5, # 较低
                propagation_depth_weight=0.3,  # 低
                propagation_breadth_weight=0.3,  # 低
                computation_time_weight=3.0,  # 最高权重（强调速度）
                delay_variance_weight=0.3,
                recovery_rate_weight=0.3,
                on_time_rate_weight=0.4
            ).normalize()
        
        elif criteria == ComparisonCriteria.BALANCED:
            # 【专家优化】均衡模式：默认优化总延误（符合高铁调度实际）
            if optimization_objective == "min_max_delay":
                # 用户明确要求最小化最大延误（覆盖默认）
                return MetricsWeight(
                    max_delay_weight=2.0,
                    avg_delay_weight=0.5,
                    total_delay_weight=0.3,
                    affected_trains_weight=0.5,
                    propagation_depth_weight=0.4,
                    propagation_breadth_weight=0.3,
                    computation_time_weight=0.1,
                    delay_variance_weight=0.3,
                    recovery_rate_weight=0.3,
                    on_time_rate_weight=0.4
                ).normalize()
            elif optimization_objective == "min_total_delay":
                # 用户明确要求最小化总延误（默认，权重最高）
                return MetricsWeight.for_min_total_delay()
            elif optimization_objective == "min_avg_delay":
                # 用户明确要求最小化平均延误
                return MetricsWeight.for_min_avg_delay()
            else:
                # 标准均衡（默认优化总延误）
                return MetricsWeight.for_min_total_delay()
        
        elif criteria == ComparisonCriteria.MIN_AFFECTED_TRAINS:
            # 【智能化】用户明确要求最小化受影响列车数
            # mip/hierarchical的优势：传播控制更好
            return MetricsWeight(
                max_delay_weight=0.8,
                avg_delay_weight=0.8,
                total_delay_weight=0.3,
                affected_trains_weight=3.0,  # 最高权重
                propagation_depth_weight=2.0,  # 高
                propagation_breadth_weight=1.5,  # 较高
                computation_time_weight=0.3,
                delay_variance_weight=0.5,
                recovery_rate_weight=0.5,
                on_time_rate_weight=0.6
            ).normalize()
        
        else:
            return MetricsWeight.for_balanced()
    
    def _calculate_score(
        self,
        metrics: EvaluationMetrics,
        weights: MetricsWeight,
        total_trains: int = 0
    ) -> float:
        """
        计算综合得分（高铁延误指标简化版，归一化）

        只关注延误相关指标：
        - 最大延误（关键：高铁调度安全）
        - 平均延误（关键：整体服务水平）= 总延误 / 延误列车数
        - 准点率（关键：运营质量）
        - 受影响列车数（关键：延误传播控制）

        分数范围：0-100分（越低越好）
        """
        # 归一化权重
        nw = weights.normalize()

        # 【修复】avg_delay 已改为晚点列车平均延误，直接使用
        avg_delay_minutes = metrics.avg_delay_seconds / 60

        # 将各指标归一化到0-100范围（越低越好）
        # === 阈值说明 ===
        # 最大延误阈值：30分钟为满分（100分）
        max_delay_threshold = 30  # 分钟
        max_delay_score = min(metrics.max_delay_seconds / 60 / max_delay_threshold * 100, 100)

        # 平均延误阈值：30分钟（晚点列车平均延误阈值）
        avg_delay_threshold = 30  # 分钟
        avg_delay_score = min(avg_delay_minutes / avg_delay_threshold * 100, 100)

        # 【关键修复】总延误阈值：120分钟（系统总延误阈值）
        total_delay_threshold = 120  # 分钟
        total_delay_score = min((metrics.total_delay_seconds / 60) / total_delay_threshold * 100, 100)

        # 准点率：100%为0分，0%为100分
        on_time_score = (1 - metrics.on_time_rate) * 100

        # 受影响列车阈值：10列为满分（高铁场景合理值，147列线路）
        affected_threshold = 10
        affected_score = min(metrics.affected_trains_count / affected_threshold * 100, 100)

        # 计算时间惩罚（超过60秒开始惩罚，鼓励快速响应但不过度惩罚MIP）
        computation_time_score = min(metrics.computation_time / 60 * 100, 100)

        # 加权综合得分（越低越好）【关键修复：加入总延误得分】
        score = (
            max_delay_score * nw.max_delay_weight +
            avg_delay_score * nw.avg_delay_weight +
            total_delay_score * nw.total_delay_weight +
            on_time_score * nw.on_time_rate_weight +
            affected_score * nw.affected_trains_weight +
            computation_time_score * nw.computation_time_weight
        )

        return round(score, 2)
    
    def compare_all(
        self,
        delay_injection,
        criteria: Optional[ComparisonCriteria] = None,
        scheduler_names: Optional[List[str]] = None,
        objective: str = "min_total_delay"  # 【专家优化】默认优化总延误
    ) -> MultiComparisonResult:
        """
        比较所有调度器【专家修复版：支持动态权重调整】

        Args:
            delay_injection: 延误注入场景
            criteria: 比较准则（默认：MIN_TOTAL_DELAY，优化总延误）
            scheduler_names: 要比较的调度器列表（默认：全部）
            objective: 优化目标（默认：min_total_delay，可改为min_max_delay/min_avg_delay）

        专家说明：
        默认 objective = "min_total_delay" 符合高铁调度实际：
        1. 总延误是高铁运营核心KPI
        2. 反映整体系统效率和运营成本
        3. 与服务质量、旅客满意度直接相关
        """
        start_time = time.time()
        criteria = criteria or self.default_criteria
        # 【专家修复】传入优化目标进行动态权重调整
        weights = self._get_weights_for_criteria(criteria, objective)
        
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
                logger.debug(f"执行调度器: {name}")
                result = scheduler.solve(delay_injection, objective)
                
                if result.success:
                    # 【专家修复】使用动态权重计算得分
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
                import traceback
                logger.error(f"调度器 {name} 执行异常: {e}")
                logger.error(f"详细堆栈: {traceback.format_exc()}")
        
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
        objective: str = "min_total_delay"
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
    include_fsfs: bool = False,  # 已移除：与noop行为相似
    include_mip: bool = True,
    include_hierarchical: bool = True,  # 分层求解器（FCFS+MIP混合）
    include_rl: bool = False,
    include_noop: bool = True,
    include_max_delay_first: bool = True,
    include_spt: bool = False,  # 已移除：不符合高铁实际
    include_srpt: bool = False,  # 已移除：不符合高铁实际
    include_eaf: bool = False,  # 可选：最早到站优先
    **kwargs
) -> SchedulerComparator:
    """
    创建比较器并注册调度器

    Args:
        trains: 列车列表
        stations: 车站列表
        include_fcfs: 是否包含FCFS调度器（先到先服务）
        include_fsfs: 是否包含FSFS调度器（先计划先服务，已移除）
        include_mip: 是否包含MIP调度器（混合整数规划）
        include_hierarchical: 是否包含分层求解器（FCFS+MIP混合，推荐）
        include_rl: 是否包含强化学习调度器
        include_noop: 是否包含基线调度器（不做调整）
        include_max_delay_first: 是否包含最大延误优先调度器
        include_spt: 是否包含SPT调度器（最短处理时间优先，已移除）
        include_srpt: 是否包含SRPT调度器（最短剩余处理时间优先，已移除）
        include_eaf: 是否包含最早到站优先调度器（可选）
        **kwargs: 其他参数

    Returns:
        配置好的比较器
    """
    comparator = SchedulerComparator(trains, stations)

    if include_fcfs:
        comparator.register_scheduler_by_name("fcfs", **kwargs)

    # FSFS已移除，如需基线对比请使用noop调度器
    if include_fsfs:
        logger.warning("FSFS调度器已移除，建议使用noop调度器作为基线对比")
        comparator.register_scheduler_by_name("noop", **kwargs)

    if include_mip:
        comparator.register_scheduler_by_name("mip", **kwargs)

    if include_hierarchical:
        comparator.register_scheduler_by_name("hierarchical", **kwargs)

    if include_rl:
        comparator.register_scheduler_by_name("rl", **kwargs)

    if include_noop:
        comparator.register_scheduler_by_name("noop", **kwargs)

    if include_max_delay_first:
        comparator.register_scheduler_by_name("max_delay_first", **kwargs)

    # SPT、SRPT已移除：不符合高铁按图行车原则
    if include_spt or include_srpt:
        logger.warning("SPT/SRPT调度器已移除，不符合高铁实际调度场景")

    if include_eaf:
        comparator.register_scheduler_by_name("eaf", **kwargs)

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
