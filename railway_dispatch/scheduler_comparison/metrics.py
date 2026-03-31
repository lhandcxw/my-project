# -*- coding: utf-8 -*-
"""
铁路调度系统 - 评估指标定义模块
定义完整的调度评估指标体系（专家版）

专家视角的指标设计：
1. 旅客服务指标：准点率、平均/最大延误、延误方差
2. 运营效率指标：受影响列车、传播控制、恢复能力
3. 资源利用指标：股道占用、咽喉区冲突
4. 计算性能指标：求解时间、稳定性
"""

from typing import Dict, List, Any, Optional, Tuple
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
    def for_real_time(cls) -> 'MetricsExpertWeight':
        """实时调度场景的权重配置（重视计算速度）"""
        return cls(
            max_delay_weight=0.8,
            avg_delay_weight=0.8,
            on_time_rate_weight=0.5,
            affected_trains_weight=0.6,
            delay_propagation_weight=0.3,
            total_delay_weight=0.5,
            track_conflict_weight=0.2,
            throat_conflict_weight=0.2,
            computation_time_weight=2.0,
            stability_weight=0.6
        ).normalize()

    @classmethod
    def for_high_speed(cls) -> 'MetricsExpertWeight':
        """
        高铁专用权重（简化版）
        只关注延误指标：最大延误、平均延误、准点率、受影响列车
        """
        return cls(
            max_delay_weight=3.0,        # 最大延误最重要
            avg_delay_weight=2.0,        # 平均延误次重要
            on_time_rate_weight=2.0,    # 准点率同等重要
            affected_trains_weight=1.5,  # 受影响列车（传播控制）
            delay_propagation_weight=1.0,
            total_delay_weight=0.5,
            track_conflict_weight=0.0,    # 忽略
            throat_conflict_weight=0.0,   # 忽略
            computation_time_weight=0.1,  # 非关键
            stability_weight=0.1
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
        
        # 计算基础统计（修正：平均延误只计算受影响的列车，避免分母被未受影响列车稀释）
        if all_delays:
            max_delay = max(all_delays)
            # 只计算有延误的站点的平均延误，更准确反映调度效果
            affected_delays = [d for d in all_delays if d > 0]
            if affected_delays:
                avg_delay = sum(affected_delays) / len(affected_delays)
            else:
                avg_delay = 0.0
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

    # 测试专家指标权重
    print("\n" + "=" * 60)
    print("专家指标权重配置")
    print("=" * 60)
    print(f"高铁客运专线: {MetricsExpertWeight.for_high_speed_passenger().to_summary()}")
    print(f"货运重载线路: {MetricsExpertWeight.for_freight_heavy().to_summary()}")
    print(f"城际通勤线路: {MetricsExpertWeight.for_intercity().to_summary()}")


# =========================================
# 专家级指标权重配置（新增）
# =========================================

class DispatchScenarioType(str, Enum):
    """调度场景类型"""
    HIGH_SPEED_PASSENGER = "high_speed_passenger"  # 高铁客运专线
    FREIGHT_HEAVY = "freight_heavy"               # 货运重载线路
    INTERCITY = "intercity"                       # 城际通勤线路
    MIXED = "mixed"                              # 客货混跑线路


@dataclass
class MetricsExpertWeight:
    """
    专家级指标权重配置
    根据不同调度场景类型设计的专业权重方案
    """
    # 旅客服务指标（最重要）
    max_delay_weight: float = 1.0                # 最大延误（关键：影响旅客出行）
    avg_delay_weight: float = 1.0                # 平均延误
    on_time_rate_weight: float = 1.0             # 准点率（关键：服务承诺）

    # 运营效率指标
    affected_trains_weight: float = 0.8          # 受影响列车数
    delay_propagation_weight: float = 1.0       # 延误传播控制（关键：避免链式反应）
    total_delay_weight: float = 0.5             # 总延误

    # 资源利用指标
    track_conflict_weight: float = 0.6          # 股道冲突
    throat_conflict_weight: float = 0.5         # 咽喉区冲突

    # 计算性能指标
    computation_time_weight: float = 0.3       # 计算时间
    stability_weight: float = 0.4               # 求解稳定性

    def normalize(self) -> 'MetricsExpertWeight':
        """归一化权重"""
        total = (
            self.max_delay_weight + self.avg_delay_weight + self.on_time_rate_weight +
            self.affected_trains_weight + self.delay_propagation_weight + self.total_delay_weight +
            self.track_conflict_weight + self.throat_conflict_weight +
            self.computation_time_weight + self.stability_weight
        )
        if total == 0:
            return self

        return MetricsExpertWeight(
            max_delay_weight=self.max_delay_weight / total,
            avg_delay_weight=self.avg_delay_weight / total,
            on_time_rate_weight=self.on_time_rate_weight / total,
            affected_trains_weight=self.affected_trains_weight / total,
            delay_propagation_weight=self.delay_propagation_weight / total,
            total_delay_weight=self.total_delay_weight / total,
            track_conflict_weight=self.track_conflict_weight / total,
            throat_conflict_weight=self.throat_conflict_weight / total,
            computation_time_weight=self.computation_time_weight / total,
            stability_weight=self.stability_weight / total
        )

    def to_summary(self) -> str:
        """转换为摘要字符串"""
        return (
            f"max_delay={self.max_delay_weight:.2f}, "
            f"avg_delay={self.avg_delay_weight:.2f}, "
            f"on_time_rate={self.on_time_rate_weight:.2f}, "
            f"propagation={self.delay_propagation_weight:.2f}"
        )

    @classmethod
    def for_high_speed_passenger(cls) -> 'MetricsExpertWeight':
        """
        高铁客运专线场景（最高优先级：旅客体验）
        特点：准点率要求高，延误传播控制关键
        """
        return cls(
            max_delay_weight=2.0,          # 最大延误最重要（影响高端旅客）
            avg_delay_weight=1.5,         # 平均延误很重要
            on_time_rate_weight=2.0,      # 准点率是服务承诺（>99%）
            affected_trains_weight=1.0,    # 受影响列车数
            delay_propagation_weight=1.5,  # 延误传播是链式反应的关键
            total_delay_weight=0.5,
            track_conflict_weight=0.5,
            throat_conflict_weight=0.5,
            computation_time_weight=0.2,  # 非实时要求
            stability_weight=0.3
        ).normalize()

    @classmethod
    def for_freight_heavy(cls) -> 'MetricsExpertWeight':
        """
        货运重载线路场景（最高优先级：运输能力）
        特点：允许一定延误，追求运输量最大化
        """
        return cls(
            max_delay_weight=0.8,          # 货运对延误容忍度较高
            avg_delay_weight=1.0,
            on_time_rate_weight=0.5,      # 货运不追求准点率
            affected_trains_weight=1.5,   # 货运列车周转重要
            delay_propagation_weight=0.8, # 传播控制相对不那么严格
            total_delay_weight=1.5,       # 总延误反映运输能力损失
            track_conflict_weight=1.0,    # 股道占用影响装卸
            throat_conflict_weight=0.8,
            computation_time_weight=0.3,
            stability_weight=0.5
        ).normalize()

    @classmethod
    def for_intercity(cls) -> 'MetricsExpertWeight':
        """
        城际通勤线路场景（平衡：效率+服务）
        特点：高密度发车，传播控制关键
        """
        return cls(
            max_delay_weight=1.5,
            avg_delay_weight=1.5,
            on_time_rate_weight=1.5,      # 通勤族对延误敏感
            affected_trains_weight=1.2,    # 影响后续所有列车
            delay_propagation_weight=2.0,  # 高密度下传播极快
            total_delay_weight=0.8,
            track_conflict_weight=0.8,
            throat_conflict_weight=0.6,
            computation_time_weight=0.5,   # 需要快速响应
            stability_weight=0.6
        ).normalize()

    @classmethod
    def from_scenario(cls, scenario: DispatchScenarioType) -> 'MetricsExpertWeight':
        """根据场景类型获取权重配置"""
        scenario_map = {
            DispatchScenarioType.HIGH_SPEED_PASSENGER: cls.for_high_speed_passenger,
            DispatchScenarioType.FREIGHT_HEAVY: cls.for_freight_heavy,
            DispatchScenarioType.INTERCITY: cls.for_intercity,
            DispatchScenarioType.MIXED: cls.for_high_speed_passenger  # 默认使用高铁配置
        }
        return scenario_map.get(scenario, cls.for_high_speed_passenger)()

    @classmethod
    def for_high_speed_simplified(cls) -> 'MetricsExpertWeight':
        """
        高铁简化版权重（只关注延误指标）
        用于高铁调度器对比评估
        """
        return cls(
            max_delay_weight=3.0,        # 最大延误最重要（高铁安全）
            avg_delay_weight=2.0,        # 平均延误反映整体水平
            on_time_rate_weight=2.5,     # 准点率是运营质量核心
            affected_trains_weight=1.5,   # 受影响列车反映传播控制
            delay_propagation_weight=1.0,
            total_delay_weight=0.3,
            track_conflict_weight=0.0,
            throat_conflict_weight=0.0,
            computation_time_weight=0.0,
            stability_weight=0.0
        ).normalize()


class ExpertEvaluationCriteria(str, Enum):
    """专家级评估准则"""
    PASSENGER_SERVICE = "passenger_service"     # 旅客服务优先
    TRANSPORT_CAPACITY = "transport_capacity"   # 运输能力优先
    PROPAGATION_CONTROL = "propagation_control"  # 延误传播控制
    REAL_TIME_RESPONSE = "real_time_response"   # 实时响应优先
    BALANCED = "balanced"                       # 均衡评估


@dataclass
class ExpertComparisonResult:
    """
    专家级比较结果
    包含多维度分析和专业建议
    """
    scheduler_name: str
    rank: int

    # 旅客服务维度
    passenger_score: float           # 旅客服务得分
    max_delay_minutes: int
    avg_delay_minutes: float
    on_time_rate_percent: float

    # 运营效率维度
    efficiency_score: float          # 运营效率得分
    affected_trains: int
    propagation_coefficient: float   # 传播系数（延误传播广度）
    total_delay_minutes: int

    # 资源利用维度
    resource_score: float            # 资源利用得分
    track_conflict_count: int        # 虚拟：股道冲突数
    throat_conflict_count: int       # 虚拟：咽喉区冲突数

    # 综合评分
    overall_score: float
    recommendation: str = ""

    def to_expert_report(self) -> str:
        """生成专家报告格式"""
        lines = [
            f"{'='*60}",
            f"专家评估报告 - {self.scheduler_name}",
            f"{'='*60}",
            f"",
            f"【排名】第 {self.rank} 名",
            f"【综合评分】{self.overall_score:.2f} 分",
            f"",
            f"▌ 旅客服务维度 (得分: {self.passenger_score:.1f})",
            f"   • 最大延误: {self.max_delay_minutes} 分钟",
            f"   • 平均延误: {self.avg_delay_minutes:.1f} 分钟",
            f"   • 准点率: {self.on_time_rate_percent:.1f}%",
            f"",
            f"▌ 运营效率维度 (得分: {self.efficiency_score:.1f})",
            f"   • 受影响列车: {self.affected_trains} 列",
            f"   • 传播系数: {self.propagation_coefficient:.2f}",
            f"   • 总延误: {self.total_delay_minutes} 分钟",
            f"",
            f"▌ 资源利用维度 (得分: {self.resource_score:.1f})",
            f"   • 综合利用率: {self.track_conflict_count:.0f}%",
            f"",
            f"【建议】{self.recommendation}",
            f"{'='*60}"
        ]
        return "\n".join(lines)


def calculate_expert_metrics(
    schedule: Dict[str, List[Dict]],
    original_schedule: Optional[Dict[str, List[Dict]]] = None,
    computation_time: float = 0.0,
    scenario: DispatchScenarioType = DispatchScenarioType.HIGH_SPEED_PASSENGER
) -> ExpertComparisonResult:
    """
    专家级指标计算
    根据不同场景类型计算专业评估结果
    """
    # 计算基础指标
    base_metrics = MetricsDefinition.calculate_metrics(schedule, original_schedule, computation_time)

    # 获取场景权重
    weights = MetricsExpertWeight.from_scenario(scenario)

    # 计算各维度得分
    # 旅客服务得分（100分制，越高越好）
    passenger_score = 100 - (
        min(base_metrics.max_delay_seconds / 1800, 1.0) * 30 +  # 最大延误扣分（最多30分）
        min(base_metrics.avg_delay_seconds / 600, 1.0) * 20 +    # 平均延误扣分
        (1 - base_metrics.on_time_rate) * 50                     # 准点率扣分
    )

    # 运营效率得分
    propagation_coefficient = base_metrics.delay_propagation_breadth / max(base_metrics.affected_trains_count, 1)
    efficiency_score = 100 - (
        min(base_metrics.affected_trains_count / 10, 1.0) * 30 +
        min(propagation_coefficient, 1.0) * 30 +
        min(base_metrics.total_delay_seconds / 3600, 1.0) * 40
    )

    # 资源利用得分（简化版）
    resource_score = 85.0  # 预留扩展

    # 综合评分（加权）
    overall_score = (
        passenger_score * weights.max_delay_weight +
        efficiency_score * weights.delay_propagation_weight +
        resource_score * weights.track_conflict_weight
    )

    return ExpertComparisonResult(
        scheduler_name="",  # 待填充
        rank=0,
        passenger_score=passenger_score,
        max_delay_minutes=base_metrics.max_delay_seconds // 60,
        avg_delay_minutes=base_metrics.avg_delay_seconds / 60,
        on_time_rate_percent=base_metrics.on_time_rate * 100,
        efficiency_score=efficiency_score,
        affected_trains=base_metrics.affected_trains_count,
        propagation_coefficient=propagation_coefficient,
        total_delay_minutes=base_metrics.total_delay_seconds // 60,
        resource_score=resource_score,
        track_conflict_count=85,
        throat_conflict_count=90,
        overall_score=overall_score,
        recommendation=""
    )
