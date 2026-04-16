# -*- coding: utf-8 -*-
"""
铁路调度系统 - 高铁客运专线评估指标模块
针对高铁客运专线特点设计的专业评估指标体系

高铁客运专线核心关注点：
1. 运行效率：最小化最大延误、控制延误传播
2. 调度质量：平均延误、延误分布均衡性
3. 运营稳定性：受影响列车数、传播深度控制
4. 计算性能：求解时间（用于实时调度决策）
"""

from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import json
import math


class MetricCategory(str, Enum):
    """指标类别"""
    DELAY = "delay"           # 延误相关指标
    EFFICIENCY = "efficiency" # 效率相关指标
    PROPAGATION = "propagation"  # 传播控制指标
    COMPUTATION = "computation"  # 计算资源指标


@dataclass
class HighSpeedMetricsWeight:
    """
    高铁客运专线指标权重配置
    针对高铁特点优化：重视延误控制和传播抑制
    """
    max_delay_weight: float = 1.0           # 最大延误权重（关键指标）
    avg_delay_weight: float = 0.8           # 平均延误权重
    total_delay_weight: float = 0.5         # 总延误权重
    affected_trains_weight: float = 0.9     # 受影响列车数权重（传播控制）
    propagation_depth_weight: float = 0.7   # 传播深度权重
    propagation_breadth_weight: float = 0.6 # 传播广度权重
    computation_time_weight: float = 0.3    # 计算时间权重
    delay_variance_weight: float = 0.4      # 延误方差权重（均衡性）
    recovery_rate_weight: float = 0.5       # 恢复率权重
    on_time_rate_weight: float = 0.6        # 准点率权重

    def normalize(self) -> 'HighSpeedMetricsWeight':
        """归一化权重，使总和为1"""
        total = (
            self.max_delay_weight +
            self.avg_delay_weight +
            self.total_delay_weight +
            self.affected_trains_weight +
            self.propagation_depth_weight +
            self.propagation_breadth_weight +
            self.computation_time_weight +
            self.delay_variance_weight +
            self.recovery_rate_weight +
            self.on_time_rate_weight
        )
        if total == 0:
            return self
        return HighSpeedMetricsWeight(
            max_delay_weight=self.max_delay_weight / total,
            avg_delay_weight=self.avg_delay_weight / total,
            total_delay_weight=self.total_delay_weight / total,
            affected_trains_weight=self.affected_trains_weight / total,
            propagation_depth_weight=self.propagation_depth_weight / total,
            propagation_breadth_weight=self.propagation_breadth_weight / total,
            computation_time_weight=self.computation_time_weight / total,
            delay_variance_weight=self.delay_variance_weight / total,
            recovery_rate_weight=self.recovery_rate_weight / total,
            on_time_rate_weight=self.on_time_rate_weight / total
        )
    
    @classmethod
    def for_min_max_delay(cls) -> 'HighSpeedMetricsWeight':
        """优先最小化最大延误（适合关键列车保障）"""
        return cls(
            max_delay_weight=3.0,
            avg_delay_weight=0.5,
            total_delay_weight=0.3,
            affected_trains_weight=0.5,
            propagation_depth_weight=0.4,
            propagation_breadth_weight=0.3,
            computation_time_weight=0.1,
            delay_variance_weight=0.2,
            recovery_rate_weight=0.3,
            on_time_rate_weight=0.4
        ).normalize()
    
    @classmethod
    def for_min_propagation(cls) -> 'HighSpeedMetricsWeight':
        """优先控制延误传播（适合高密度线路）"""
        return cls(
            max_delay_weight=1.0,
            avg_delay_weight=0.8,
            total_delay_weight=0.5,
            affected_trains_weight=2.0,
            propagation_depth_weight=1.5,
            propagation_breadth_weight=1.2,
            computation_time_weight=0.2,
            delay_variance_weight=0.5,
            recovery_rate_weight=0.8,
            on_time_rate_weight=0.7
        ).normalize()
    
    @classmethod
    def for_balanced(cls) -> 'HighSpeedMetricsWeight':
        """均衡配置（默认推荐）"""
        return cls(
            max_delay_weight=1.2,
            avg_delay_weight=1.0,
            total_delay_weight=0.6,
            affected_trains_weight=1.0,
            propagation_depth_weight=0.8,
            propagation_breadth_weight=0.6,
            computation_time_weight=0.4,
            delay_variance_weight=0.5,
            recovery_rate_weight=0.6,
            on_time_rate_weight=0.6
        ).normalize()
    
    @classmethod
    def for_real_time(cls) -> 'HighSpeedMetricsWeight':
        """实时调度场景（重视计算速度）"""
        return cls(
            max_delay_weight=1.0,
            avg_delay_weight=0.8,
            total_delay_weight=0.5,
            affected_trains_weight=0.8,
            propagation_depth_weight=0.5,
            propagation_breadth_weight=0.4,
            computation_time_weight=2.0,
            delay_variance_weight=0.3,
            recovery_rate_weight=0.4,
            on_time_rate_weight=0.5
        ).normalize()

    @classmethod
    def for_min_avg_delay(cls) -> 'HighSpeedMetricsWeight':
        """优先最小化平均延误（适合整体服务水平优化）"""
        return cls(
            max_delay_weight=1.0,
            avg_delay_weight=3.0,
            total_delay_weight=1.0,
            affected_trains_weight=0.5,
            propagation_depth_weight=0.4,
            propagation_breadth_weight=0.3,
            computation_time_weight=0.1,
            delay_variance_weight=0.5,
            recovery_rate_weight=0.4,
            on_time_rate_weight=0.8
        ).normalize()


@dataclass
class EvaluationMetrics:
    """
    高铁客运专线评估指标集
    专注于运行效率和传播控制
    """
    # === 核心延误指标 ===
    max_delay_seconds: int = 0                    # 最大延误（秒）- 最关键
    avg_delay_seconds: float = 0.0                # 平均延误（秒）
    total_delay_seconds: int = 0                  # 总延误（秒）
    median_delay_seconds: float = 0.0             # 中位数延误
    
    # === 延误分布指标 ===
    delay_variance: float = 0.0                   # 延误方差（均衡性）
    delay_std_dev: float = 0.0                    # 延误标准差
    
    # === 传播控制指标 ===
    affected_trains_count: int = 0                # 受影响列车数
    propagation_depth: int = 0                    # 传播深度（影响车站数）
    propagation_breadth: int = 0                  # 传播广度（影响列车数）
    propagation_coefficient: float = 0.0          # 传播系数（综合指标）
    
    # === 恢复能力指标 ===
    recovery_rate: float = 0.0                    # 恢复率（延误减少比例）
    
    # === 准点率指标 ===
    on_time_rate: float = 1.0                     # 准点率（延误延误<5分钟的比例）
    
    # === 计算性能指标 ===
    computation_time: float = 0.0                 # 计算时间（秒）
    
    # === 详细数据 ===
    delay_by_train: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    delay_by_station: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            # 核心延误指标
            "max_delay_seconds": self.max_delay_seconds,
            "max_delay_minutes": round(self.max_delay_seconds / 60, 2),
            "avg_delay_seconds": round(self.avg_delay_seconds, 2),
            "avg_delay_minutes": round(self.avg_delay_seconds / 60, 2),
            "total_delay_seconds": self.total_delay_seconds,
            "total_delay_minutes": round(self.total_delay_seconds / 60, 2),
            "median_delay_seconds": round(self.median_delay_seconds, 2),
            
            # 延误分布
            "delay_variance": round(self.delay_variance, 2),
            "delay_std_dev": round(self.delay_std_dev, 2),
            
            # 传播控制
            "affected_trains_count": self.affected_trains_count,
            "propagation_depth": self.propagation_depth,
            "propagation_breadth": self.propagation_breadth,
            "propagation_coefficient": round(self.propagation_coefficient, 3),
            
            # 恢复能力
            "recovery_rate": round(self.recovery_rate * 100, 2),
            
            # 准点率
            "on_time_rate": round(self.on_time_rate * 100, 2),
            
            # 计算性能
            "computation_time": round(self.computation_time, 4)
        }
    
    def get_summary(self) -> str:
        """获取指标摘要字符串"""
        return (
            f"最大延误: {self.max_delay_seconds // 60}分钟 | "
            f"平均延误: {self.avg_delay_seconds / 60:.1f}分钟 | "
            f"受影响列车: {self.affected_trains_count}列 | "
            f"传播系数: {self.propagation_coefficient:.2f} | "
            f"计算时间: {self.computation_time:.2f}秒"
        )
    
    def calculate_overall_score(self, weights: Optional[HighSpeedMetricsWeight] = None) -> float:
        """
        计算综合评分（百分制，越高越好）
        
        Args:
            weights: 指标权重配置
            
        Returns:
            综合评分（0-100）
        """
        if weights is None:
            weights = HighSpeedMetricsWeight.for_balanced()
        
        w = weights.normalize()
        
        # 计算各指标得分（越高越好，所以用100减去扣分）
        # 最大延误得分（基准30分钟）
        max_delay_score = max(0, 100 - (self.max_delay_seconds / 1800) * 100)
        
        # 平均延误得分（基准10分钟）
        avg_delay_score = max(0, 100 - (self.avg_delay_seconds / 600) * 100)
        
        # 传播控制得分（基准10列）
        propagation_score = max(0, 100 - (self.affected_trains_count / 10) * 100)
        
        # 传播深度得分（基准5站）
        depth_score = max(0, 100 - (self.propagation_depth / 5) * 100)
        
        # 恢复率得分（直接百分比）
        recovery_score = self.recovery_rate * 100
        
        # 延误均衡性得分（方差越小越好）
        variance_score = max(0, 100 - (self.delay_variance / 3600) * 100)
        
        # 加权综合
        overall_score = (
            max_delay_score * w.max_delay_weight +
            avg_delay_score * w.avg_delay_weight +
            propagation_score * w.affected_trains_weight +
            depth_score * w.propagation_depth_weight +
            recovery_score * w.recovery_rate_weight +
            variance_score * w.delay_variance_weight
        )
        
        return round(overall_score, 2)


class MetricsDefinition:
    """
    指标定义类
    提供指标的计算、验证和分析功能
    """
    
    @classmethod
    def calculate_metrics(
        cls,
        schedule: Dict[str, List[Dict]],
        original_schedule: Optional[Dict[str, List[Dict]]] = None,
        computation_time: float = 0.0
    ) -> EvaluationMetrics:
        """
        从调度方案计算完整指标
        
        Args:
            schedule: 优化后的时刻表
            original_schedule: 原始时刻表（可选，用于计算恢复率）
            computation_time: 计算时间
        
        Returns:
            EvaluationMetrics: 完整的评估指标
        """
        all_delays = []
        delay_by_train = {}
        delay_by_station = {}
        
        # 传播分析
        max_propagation_depth = 0
        affected_train_ids = set()
        
        for train_id, stops in schedule.items():
            train_delays = []
            delay_station_count = 0
            
            for stop in stops:
                delay = stop.get("delay_seconds", 0)
                if delay > 0:
                    all_delays.append(delay)
                    train_delays.append(delay)
                    delay_station_count += 1
                    affected_train_ids.add(train_id)
                    
                    # 按车站统计
                    station_code = stop.get("station_code", "UNKNOWN")
                    if station_code not in delay_by_station:
                        delay_by_station[station_code] = []
                    delay_by_station[station_code].append(delay)
            
            # 更新传播深度
            max_propagation_depth = max(max_propagation_depth, delay_station_count)
            
            # 按列车统计
            if train_delays:
                delay_by_train[train_id] = {
                    "max": max(train_delays),
                    "avg": sum(train_delays) / len(train_delays),
                    "total": sum(train_delays),
                    "count": len(train_delays),
                    "propagation_depth": delay_station_count
                }
            else:
                delay_by_train[train_id] = {
                    "max": 0, "avg": 0, "total": 0, "count": 0, "propagation_depth": 0
                }
        
        # 计算基础统计
        if all_delays:
            max_delay = max(all_delays)
            affected_delays = [d for d in all_delays if d > 0]
            avg_delay = sum(affected_delays) / len(affected_delays) if affected_delays else 0.0
            total_delay = sum(all_delays)
            
            # 中位数
            sorted_delays = sorted(all_delays)
            n = len(sorted_delays)
            median_delay = sorted_delays[n // 2] if n % 2 else (sorted_delays[n // 2 - 1] + sorted_delays[n // 2]) / 2
            
            # 方差和标准差
            if len(all_delays) > 1:
                variance = sum((d - avg_delay) ** 2 for d in all_delays) / len(all_delays)
                std_dev = math.sqrt(variance)
            else:
                variance = 0.0
                std_dev = 0.0
        else:
            max_delay = 0
            avg_delay = 0.0
            total_delay = 0
            median_delay = 0.0
            variance = 0.0
            std_dev = 0.0
        
        # 传播系数 = 传播深度 / 平均延误（反映延误传播效率）
        propagation_coefficient = (
            max_propagation_depth / (avg_delay / 60) if avg_delay > 0 else 0
        )
        
        # 计算恢复率（如果有原始时刻表对比）
        recovery_rate = 0.0
        if original_schedule:
            original_delays = []
            for train_id, stops in original_schedule.items():
                for stop in stops:
                    delay = stop.get("delay_seconds", 0)
                    if delay > 0:
                        original_delays.append(delay)
            
            original_total = sum(original_delays) if original_delays else 1
            if original_total > 0:
                recovery_rate = max(0, (original_total - total_delay) / original_total)
        
        return EvaluationMetrics(
            max_delay_seconds=int(max_delay),
            avg_delay_seconds=float(avg_delay),
            total_delay_seconds=int(total_delay),
            median_delay_seconds=float(median_delay),
            delay_variance=float(variance),
            delay_std_dev=float(std_dev),
            affected_trains_count=len(affected_train_ids),
            propagation_depth=max_propagation_depth,
            propagation_breadth=len(affected_train_ids),
            propagation_coefficient=float(propagation_coefficient),
            recovery_rate=float(recovery_rate),
            computation_time=computation_time,
            delay_by_train=delay_by_train,
            delay_by_station={k: {"delays": v, "max": max(v), "avg": sum(v) / len(v)} 
                            for k, v in delay_by_station.items() if v}
        )
    
    @classmethod
    def compare_metrics(
        cls,
        metrics_a: EvaluationMetrics,
        metrics_b: EvaluationMetrics,
        weights: Optional[HighSpeedMetricsWeight] = None
    ) -> Dict[str, Any]:
        """
        比较两组指标
        
        Args:
            metrics_a: 方案A的指标
            metrics_b: 方案B的指标
            weights: 指标权重（可选）
        
        Returns:
            比较结果字典
        """
        if weights is None:
            weights = HighSpeedMetricsWeight.for_balanced()
        
        w = weights.normalize()
        
        # 计算综合得分
        score_a = metrics_a.calculate_overall_score(weights)
        score_b = metrics_b.calculate_overall_score(weights)
        
        # 计算各指标差异
        def calc_diff(a, b, lower_is_better=True):
            if b == 0:
                return 0 if a == 0 else (100 if lower_is_better else -100)
            diff = (a - b) / b * 100
            return diff if lower_is_better else -diff
        
        comparison = {
            "overall_score_a": score_a,
            "overall_score_b": score_b,
            "better_option": "A" if score_a > score_b else "B",
            "score_diff": score_a - score_b,
            
            # 各指标对比
            "max_delay_diff": calc_diff(
                metrics_a.max_delay_seconds, metrics_b.max_delay_seconds
            ),
            "avg_delay_diff": calc_diff(
                metrics_a.avg_delay_seconds, metrics_b.avg_delay_seconds
            ),
            "affected_trains_diff": calc_diff(
                metrics_a.affected_trains_count, metrics_b.affected_trains_count
            ),
            "propagation_depth_diff": calc_diff(
                metrics_a.propagation_depth, metrics_b.propagation_depth
            ),
            "recovery_rate_diff": calc_diff(
                metrics_a.recovery_rate, metrics_b.recovery_rate, lower_is_better=False
            ),
            "computation_time_diff": calc_diff(
                metrics_a.computation_time, metrics_b.computation_time
            )
        }
        
        return comparison
    
    @classmethod
    def generate_recommendation(
        cls,
        metrics: EvaluationMetrics,
        weights: HighSpeedMetricsWeight,
        scheduler_name: str
    ) -> str:
        """
        生成推荐理由说明
        
        Args:
            metrics: 评估指标
            weights: 使用的权重配置
            scheduler_name: 调度器名称
        
        Returns:
            推荐理由字符串
        """
        reasons = []
        w = weights.normalize()
        
        if w.max_delay_weight > 0.15:
            reasons.append(f"最大延误 {metrics.max_delay_seconds // 60} 分钟")
        
        if w.avg_delay_weight > 0.15:
            reasons.append(f"平均延误 {metrics.avg_delay_seconds / 60:.1f} 分钟")
        
        if w.affected_trains_weight > 0.15:
            reasons.append(f"影响 {metrics.affected_trains_count} 列列车")
        
        if w.propagation_depth_weight > 0.15:
            reasons.append(f"传播深度 {metrics.propagation_depth} 站")
        
        if w.recovery_rate_weight > 0.15:
            reasons.append(f"恢复率 {metrics.recovery_rate * 100:.1f}%")
        
        if w.computation_time_weight > 0.15:
            reasons.append(f"计算时间 {metrics.computation_time:.2f} 秒")
        
        return f"{scheduler_name}方案：{', '.join(reasons)}"


@dataclass
class HighSpeedEvaluationResult:
    """
    高铁客运专线评估结果
    包含多维度分析和专业建议
    """
    scheduler_name: str
    rank: int
    
    # 核心指标
    max_delay_minutes: int
    avg_delay_minutes: float
    total_delay_minutes: int
    
    # 传播控制指标
    affected_trains: int
    propagation_depth: int
    propagation_coefficient: float
    
    # 质量指标
    delay_variance: float
    recovery_rate: float
    
    # 性能指标
    computation_time: float
    
    # 综合评分
    overall_score: float
    recommendation: str = ""
    
    def to_report(self) -> str:
        """生成评估报告格式"""
        lines = [
            f"{'='*60}",
            f"高铁客运专线评估报告 - {self.scheduler_name}",
            f"{'='*60}",
            f"",
            f"【排名】第 {self.rank} 名",
            f"【综合评分】{self.overall_score:.2f} 分",
            f"",
            f"▌ 核心延误指标",
            f"   • 最大延误: {self.max_delay_minutes} 分钟",
            f"   • 平均延误: {self.avg_delay_minutes:.1f} 分钟",
            f"   • 总延误: {self.total_delay_minutes} 分钟",
            f"",
            f"▌ 传播控制指标",
            f"   • 受影响列车: {self.affected_trains} 列",
            f"   • 传播深度: {self.propagation_depth} 站",
            f"   • 传播系数: {self.propagation_coefficient:.2f}",
            f"",
            f"▌ 质量指标",
            f"   • 延误方差: {self.delay_variance:.2f}",
            f"   • 恢复率: {self.recovery_rate * 100:.1f}%",
            f"",
            f"▌ 性能指标",
            f"   • 计算时间: {self.computation_time:.2f} 秒",
            f"",
            f"【建议】{self.recommendation}",
            f"{'='*60}"
        ]
        return "\n".join(lines)


def evaluate_high_speed_schedule(
    schedule: Dict[str, List[Dict]],
    original_schedule: Optional[Dict[str, List[Dict]]] = None,
    computation_time: float = 0.0,
    weight_preference: str = "balanced"
) -> HighSpeedEvaluationResult:
    """
    高铁客运专线调度方案评估
    
    Args:
        schedule: 优化后的时刻表
        original_schedule: 原始时刻表（可选）
        computation_time: 计算时间
        weight_preference: 权重偏好 ("min_max_delay", "min_propagation", "balanced", "real_time")
    
    Returns:
        HighSpeedEvaluationResult: 评估结果
    """
    # 计算基础指标
    metrics = MetricsDefinition.calculate_metrics(schedule, original_schedule, computation_time)
    
    # 获取权重配置
    weight_map = {
        "min_max_delay": HighSpeedMetricsWeight.for_min_max_delay,
        "min_propagation": HighSpeedMetricsWeight.for_min_propagation,
        "balanced": HighSpeedMetricsWeight.for_balanced,
        "real_time": HighSpeedMetricsWeight.for_real_time
    }
    weights = weight_map.get(weight_preference, HighSpeedMetricsWeight.for_balanced)()
    
    # 计算综合评分
    overall_score = metrics.calculate_overall_score(weights)
    
    return HighSpeedEvaluationResult(
        scheduler_name="",
        rank=0,
        max_delay_minutes=metrics.max_delay_seconds // 60,
        avg_delay_minutes=metrics.avg_delay_seconds / 60,
        total_delay_minutes=metrics.total_delay_seconds // 60,
        affected_trains=metrics.affected_trains_count,
        propagation_depth=metrics.propagation_depth,
        propagation_coefficient=metrics.propagation_coefficient,
        delay_variance=metrics.delay_variance,
        recovery_rate=metrics.recovery_rate,
        computation_time=metrics.computation_time,
        overall_score=overall_score,
        recommendation=""
    )


# 测试代码
if __name__ == "__main__":
    # 测试指标计算
    test_schedule = {
        "G1001": [
            {"station_code": "BJX", "delay_seconds": 0},
            {"station_code": "SJP", "delay_seconds": 300},
            {"station_code": "BDD", "delay_seconds": 600}
        ],
        "G1002": [
            {"station_code": "BJX", "delay_seconds": 120},
            {"station_code": "SJP", "delay_seconds": 420}
        ]
    }

    metrics = MetricsDefinition.calculate_metrics(test_schedule, computation_time=0.5)
    print("高铁客运专线评估指标:")
    print(json.dumps(metrics.to_dict(), indent=2, ensure_ascii=False))
    print(f"\n摘要: {metrics.get_summary()}")
    print(f"\n综合评分: {metrics.calculate_overall_score():.2f} 分")
    
    # 测试不同权重配置
    print("\n" + "=" * 60)
    print("不同权重配置下的评分")
    print("=" * 60)
    
    configs = [
        ("最小化最大延误", HighSpeedMetricsWeight.for_min_max_delay()),
        ("最小化传播", HighSpeedMetricsWeight.for_min_propagation()),
        ("均衡配置", HighSpeedMetricsWeight.for_balanced()),
        ("实时调度", HighSpeedMetricsWeight.for_real_time())
    ]
    
    for name, weight in configs:
        score = metrics.calculate_overall_score(weight)
        print(f"{name}: {score:.2f} 分")
