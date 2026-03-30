# -*- coding: utf-8 -*-
"""
铁路调度系统 - 评估指标定义模块
定义完整的调度评估指标体系
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
import json


class MetricCategory(str, Enum):
    """指标类别"""
    DELAY = "delay"           # 延误相关指标
    EFFICIENCY = "efficiency" # 效率相关指标
    RELIABILITY = "reliability"  # 可靠性指标
    RESOURCE = "resource"     # 资源利用指标
    COMPUTATION = "computation"  # 计算资源指标


class MetricImportance(str, Enum):
    """指标重要性等级"""
    CRITICAL = "critical"    # 关键指标（必须满足）
    HIGH = "high"            # 高重要性
    MEDIUM = "medium"        # 中等重要性
    LOW = "low"              # 低重要性


@dataclass
class MetricsWeight:
    """
    指标权重配置
    用于根据用户偏好调整各指标的重要性
    """
    max_delay_weight: float = 1.0           # 最大延误权重
    avg_delay_weight: float = 1.0           # 平均延误权重
    total_delay_weight: float = 0.8         # 总延误权重
    affected_trains_weight: float = 0.7     # 受影响列车数权重
    computation_time_weight: float = 0.3    # 计算时间权重
    on_time_rate_weight: float = 0.6        # 准点率权重
    delay_spread_weight: float = 0.5        # 延误扩散度权重
    resource_utilization_weight: float = 0.4  # 资源利用率权重
    
    def normalize(self) -> 'MetricsWeight':
        """归一化权重，使总和为1"""
        total = (
            self.max_delay_weight + 
            self.avg_delay_weight + 
            self.total_delay_weight +
            self.affected_trains_weight + 
            self.computation_time_weight +
            self.on_time_rate_weight +
            self.delay_spread_weight +
            self.resource_utilization_weight
        )
        if total == 0:
            return self
        return MetricsWeight(
            max_delay_weight=self.max_delay_weight / total,
            avg_delay_weight=self.avg_delay_weight / total,
            total_delay_weight=self.total_delay_weight / total,
            affected_trains_weight=self.affected_trains_weight / total,
            computation_time_weight=self.computation_time_weight / total,
            on_time_rate_weight=self.on_time_rate_weight / total,
            delay_spread_weight=self.delay_spread_weight / total,
            resource_utilization_weight=self.resource_utilization_weight / total
        )
    
    @classmethod
    def for_min_max_delay(cls) -> 'MetricsWeight':
        """优先最小化最大延误的权重配置"""
        return cls(
            max_delay_weight=2.0,
            avg_delay_weight=0.5,
            total_delay_weight=0.3,
            affected_trains_weight=0.5,
            computation_time_weight=0.1,
            on_time_rate_weight=0.3,
            delay_spread_weight=0.2,
            resource_utilization_weight=0.1
        ).normalize()
    
    @classmethod
    def for_min_avg_delay(cls) -> 'MetricsWeight':
        """优先最小化平均延误的权重配置"""
        return cls(
            max_delay_weight=0.5,
            avg_delay_weight=2.0,
            total_delay_weight=0.8,
            affected_trains_weight=0.7,
            computation_time_weight=0.1,
            on_time_rate_weight=0.5,
            delay_spread_weight=0.3,
            resource_utilization_weight=0.1
        ).normalize()
    
    @classmethod
    def for_balance(cls) -> 'MetricsWeight':
        """均衡考虑各项指标的权重配置"""
        return cls(
            max_delay_weight=1.0,
            avg_delay_weight=1.0,
            total_delay_weight=1.0,
            affected_trains_weight=1.0,
            computation_time_weight=0.5,
            on_time_rate_weight=1.0,
            delay_spread_weight=0.8,
            resource_utilization_weight=0.6
        ).normalize()
    
    @classmethod
    def for_real_time(cls) -> 'MetricsWeight':
        """实时调度场景的权重配置（重视计算速度）"""
        return cls(
            max_delay_weight=0.8,
            avg_delay_weight=0.8,
            total_delay_weight=0.5,
            affected_trains_weight=0.6,
            computation_time_weight=2.0,
            on_time_rate_weight=0.5,
            delay_spread_weight=0.3,
            resource_utilization_weight=0.2
        ).normalize()
    
    @classmethod
    def from_user_preference(cls, preference: str) -> 'MetricsWeight':
        """
        根据用户偏好字符串创建权重配置
        
        Args:
            preference: 用户偏好描述，支持：
                - "min_max_delay" / "最小最大延误"
                - "min_avg_delay" / "最小平均延误"
                - "balance" / "均衡"
                - "real_time" / "实时调度"
        
        Returns:
            对应的权重配置
        """
        preference_map = {
            "min_max_delay": cls.for_min_max_delay,
            "最小最大延误": cls.for_min_max_delay,
            "min_avg_delay": cls.for_min_avg_delay,
            "最小平均延误": cls.for_min_avg_delay,
            "balance": cls.for_balance,
            "均衡": cls.for_balance,
            "real_time": cls.for_real_time,
            "实时调度": cls.for_real_time
        }
        return preference_map.get(preference, cls.for_balance)()


@dataclass
class EvaluationMetrics:
    """
    完整的评估指标集
    包含所有维度的评估数据
    """
    # 基础延误指标
    max_delay_seconds: int = 0                    # 最大延误（秒）
    avg_delay_seconds: float = 0.0                # 平均延误（秒）
    total_delay_seconds: int = 0                  # 总延误（秒）
    affected_trains_count: int = 0                # 受影响列车数
    
    # 扩展延误指标
    median_delay_seconds: float = 0.0             # 中位数延误
    delay_std_dev: float = 0.0                    # 延误标准差
    delay_variance: float = 0.0                   # 延误方差
    on_time_rate: float = 1.0                     # 准点率（延误<5分钟的比例）
    
    # 延误分布指标
    micro_delay_count: int = 0                    # 微小延误数量（<5分钟）
    small_delay_count: int = 0                    # 小延误数量（5-30分钟）
    medium_delay_count: int = 0                   # 中延误数量（30-100分钟）
    large_delay_count: int = 0                    # 大延误数量（>100分钟）
    
    # 效率指标
    total_compression_time: int = 0               # 总压缩时间（秒）
    recovery_rate: float = 0.0                    # 恢复率（已恢复时间/总延误）
    
    # 资源利用指标
    track_utilization_rate: float = 0.0           # 股道利用率
    schedule_stability: float = 1.0               # 时刻表稳定性
    
    # 计算资源指标
    computation_time: float = 0.0                 # 计算时间（秒）
    
    # 延误传播指标
    delay_propagation_depth: int = 0              # 延误传播深度（影响车站数）
    delay_propagation_breadth: int = 0            # 延误传播广度（影响列车数）
    
    # 详细数据
    delay_by_train: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    delay_by_station: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            # 基础延误指标
            "max_delay_seconds": self.max_delay_seconds,
            "max_delay_minutes": round(self.max_delay_seconds / 60, 2),
            "avg_delay_seconds": round(self.avg_delay_seconds, 2),
            "avg_delay_minutes": round(self.avg_delay_seconds / 60, 2),
            "total_delay_seconds": self.total_delay_seconds,
            "total_delay_minutes": round(self.total_delay_seconds / 60, 2),
            "affected_trains_count": self.affected_trains_count,
            
            # 扩展指标
            "median_delay_seconds": round(self.median_delay_seconds, 2),
            "delay_std_dev": round(self.delay_std_dev, 2),
            "on_time_rate": round(self.on_time_rate * 100, 2),  # 百分比
            
            # 延误分布
            "delay_distribution": {
                "micro": self.micro_delay_count,
                "small": self.small_delay_count,
                "medium": self.medium_delay_count,
                "large": self.large_delay_count
            },
            
            # 效率指标
            "recovery_rate": round(self.recovery_rate * 100, 2),
            
            # 计算资源
            "computation_time": round(self.computation_time, 4),
            
            # 延误传播
            "propagation": {
                "depth": self.delay_propagation_depth,
                "breadth": self.delay_propagation_breadth
            }
        }
    
    def get_summary(self) -> str:
        """获取指标摘要字符串"""
        return (
            f"最大延误: {self.max_delay_seconds // 60}分钟 | "
            f"平均延误: {self.avg_delay_seconds / 60:.1f}分钟 | "
            f"受影响列车: {self.affected_trains_count}列 | "
            f"准点率: {self.on_time_rate * 100:.1f}% | "
            f"计算时间: {self.computation_time:.2f}秒"
        )


class MetricsDefinition:
    """
    指标定义类
    提供指标的计算、验证和分析功能
    """
    
    # 延误等级阈值（秒）
    DELAY_THRESHOLDS = {
        "micro": 300,      # 5分钟
        "small": 1800,     # 30分钟
        "medium": 6000     # 100分钟
    }
    
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
            original_schedule: 原始时刻表（可选）
            computation_time: 计算时间
        
        Returns:
            EvaluationMetrics: 完整的评估指标
        """
        all_delays = []
        delay_by_train = {}
        delay_by_station = {}
        
        on_time_threshold = cls.DELAY_THRESHOLDS["micro"]  # 5分钟
        on_time_count = 0
        total_stops = 0
        
        # 延误等级计数
        micro_count = 0
        small_count = 0
        medium_count = 0
        large_count = 0
        
        for train_id, stops in schedule.items():
            train_delays = []
            for stop in stops:
                delay = stop.get("delay_seconds", 0)
                if delay > 0:
                    all_delays.append(delay)
                    train_delays.append(delay)
                    
                    # 按车站统计
                    station_code = stop.get("station_code", "UNKNOWN")
                    if station_code not in delay_by_station:
                        delay_by_station[station_code] = []
                    delay_by_station[station_code].append(delay)
                
                total_stops += 1
                if delay <= on_time_threshold:
                    on_time_count += 1
                
                # 延误等级分类
                if delay > 0:
                    if delay < cls.DELAY_THRESHOLDS["micro"]:
                        micro_count += 1
                    elif delay < cls.DELAY_THRESHOLDS["small"]:
                        small_count += 1
                    elif delay < cls.DELAY_THRESHOLDS["medium"]:
                        medium_count += 1
                    else:
                        large_count += 1
            
            # 按列车统计
            if train_delays:
                delay_by_train[train_id] = {
                    "max": max(train_delays),
                    "avg": sum(train_delays) / len(train_delays),
                    "total": sum(train_delays),
                    "count": len(train_delays)
                }
            else:
                delay_by_train[train_id] = {"max": 0, "avg": 0, "total": 0, "count": 0}
        
        # 计算基础统计
        if all_delays:
            max_delay = max(all_delays)
            avg_delay = sum(all_delays) / len(all_delays)
            total_delay = sum(all_delays)
            affected_trains = len([d for d in delay_by_train.values() if d["max"] > 0])
            
            # 中位数和标准差
            sorted_delays = sorted(all_delays)
            n = len(sorted_delays)
            median_delay = sorted_delays[n // 2] if n % 2 else (sorted_delays[n // 2 - 1] + sorted_delays[n // 2]) / 2
            
            variance = sum((d - avg_delay) ** 2 for d in all_delays) / len(all_delays)
            std_dev = variance ** 0.5
        else:
            max_delay = 0
            avg_delay = 0.0
            total_delay = 0
            affected_trains = 0
            median_delay = 0.0
            variance = 0.0
            std_dev = 0.0
        
        # 准点率
        on_time_rate = on_time_count / total_stops if total_stops > 0 else 1.0
        
        # 延误传播分析
        propagation_depth = cls._calculate_propagation_depth(schedule)
        propagation_breadth = affected_trains
        
        return EvaluationMetrics(
            max_delay_seconds=int(max_delay),
            avg_delay_seconds=float(avg_delay),
            total_delay_seconds=int(total_delay),
            affected_trains_count=affected_trains,
            median_delay_seconds=float(median_delay),
            delay_std_dev=float(std_dev),
            delay_variance=float(variance),
            on_time_rate=on_time_rate,
            micro_delay_count=micro_count,
            small_delay_count=small_count,
            medium_delay_count=medium_count,
            large_delay_count=large_count,
            computation_time=computation_time,
            delay_propagation_depth=propagation_depth,
            delay_propagation_breadth=propagation_breadth,
            delay_by_train=delay_by_train,
            delay_by_station={k: {"delays": v, "max": max(v), "avg": sum(v) / len(v)} 
                            for k, v in delay_by_station.items()}
        )
    
    @classmethod
    def _calculate_propagation_depth(cls, schedule: Dict[str, List[Dict]]) -> int:
        """计算延误传播深度"""
        max_depth = 0
        for train_id, stops in schedule.items():
            delay_stations = sum(1 for s in stops if s.get("delay_seconds", 0) > 0)
            max_depth = max(max_depth, delay_stations)
        return max_depth
    
    @classmethod
    def compare_metrics(
        cls,
        metrics_a: EvaluationMetrics,
        metrics_b: EvaluationMetrics,
        weights: Optional[MetricsWeight] = None
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
            weights = MetricsWeight.for_balance()
        
        # 计算各指标的相对差异
        def relative_diff(a, b, lower_is_better=True):
            if b == 0:
                return 0 if a == 0 else (100 if lower_is_better else -100)
            diff = (a - b) / b * 100
            return diff if lower_is_better else -diff
        
        comparison = {
            "max_delay_diff": relative_diff(
                metrics_a.max_delay_seconds, metrics_b.max_delay_seconds
            ),
            "avg_delay_diff": relative_diff(
                metrics_a.avg_delay_seconds, metrics_b.avg_delay_seconds
            ),
            "total_delay_diff": relative_diff(
                metrics_a.total_delay_seconds, metrics_b.total_delay_seconds
            ),
            "affected_trains_diff": relative_diff(
                metrics_a.affected_trains_count, metrics_b.affected_trains_count
            ),
            "computation_time_diff": relative_diff(
                metrics_a.computation_time, metrics_b.computation_time
            ),
            "on_time_rate_diff": relative_diff(
                metrics_a.on_time_rate, metrics_b.on_time_rate, lower_is_better=False
            )
        }
        
        # 计算加权得分（越小越好）
        normalized_weights = weights.normalize()
        score_a = (
            metrics_a.max_delay_seconds * normalized_weights.max_delay_weight +
            metrics_a.avg_delay_seconds * normalized_weights.avg_delay_weight +
            metrics_a.total_delay_seconds * normalized_weights.total_delay_weight +
            metrics_a.affected_trains_count * 60 * normalized_weights.affected_trains_weight +  # 转换为秒为单位
            metrics_a.computation_time * 60 * normalized_weights.computation_time_weight +  # 转换为秒为单位
            (1 - metrics_a.on_time_rate) * 3600 * normalized_weights.on_time_rate_weight  # 转换为秒为单位
        )
        
        score_b = (
            metrics_b.max_delay_seconds * normalized_weights.max_delay_weight +
            metrics_b.avg_delay_seconds * normalized_weights.avg_delay_weight +
            metrics_b.total_delay_seconds * normalized_weights.total_delay_weight +
            metrics_b.affected_trains_count * 60 * normalized_weights.affected_trains_weight +
            metrics_b.computation_time * 60 * normalized_weights.computation_time_weight +
            (1 - metrics_b.on_time_rate) * 3600 * normalized_weights.on_time_rate_weight
        )
        
        comparison["weighted_score_a"] = score_a
        comparison["weighted_score_b"] = score_b
        comparison["better_option"] = "A" if score_a < score_b else "B"
        
        return comparison
    
    @classmethod
    def generate_recommendation(
        cls,
        metrics: EvaluationMetrics,
        weights: MetricsWeight,
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
        
        if weights.max_delay_weight > 0.2:
            reasons.append(f"最大延误为 {metrics.max_delay_seconds // 60} 分钟")
        
        if weights.avg_delay_weight > 0.2:
            reasons.append(f"平均延误为 {metrics.avg_delay_seconds / 60:.1f} 分钟")
        
        if weights.affected_trains_weight > 0.2:
            reasons.append(f"影响 {metrics.affected_trains_count} 列列车")
        
        if weights.on_time_rate_weight > 0.2:
            reasons.append(f"准点率 {metrics.on_time_rate * 100:.1f}%")
        
        if weights.computation_time_weight > 0.2:
            reasons.append(f"计算时间 {metrics.computation_time:.2f} 秒")
        
        return f"{scheduler_name}方案：{', '.join(reasons)}"


# 测试代码
if __name__ == "__main__":
    # 测试指标计算
    test_schedule = {
        "G1001": [
            {"station_code": "BJX", "delay_seconds": 0},
            {"station_code": "TJG", "delay_seconds": 300},
            {"station_code": "NJH", "delay_seconds": 600}
        ],
        "G1002": [
            {"station_code": "BJX", "delay_seconds": 120},
            {"station_code": "TJG", "delay_seconds": 420}
        ]
    }
    
    metrics = MetricsDefinition.calculate_metrics(test_schedule, computation_time=0.5)
    print("评估指标:")
    print(json.dumps(metrics.to_dict(), indent=2, ensure_ascii=False))
    print(f"\n摘要: {metrics.get_summary()}")
