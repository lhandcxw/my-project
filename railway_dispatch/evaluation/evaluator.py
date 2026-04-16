# -*- coding: utf-8 -*-
"""
铁路调度系统 - 高铁客运专线评估模块
针对高铁客运专线场景设计的专业评估指标
"""

from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import json

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================
# 高铁客运专线专用评估指标
# =========================================

class DelayLevel(str, Enum):
    """高铁延误等级（按铁路行业标准）"""
    NORMAL = "normal"           # 正常（（< 3分钟）
    SLIGHT = "slight"           # 轻微延误（3-5分钟）
    MODERATE = "moderate"       # 中度延误（5-15分钟）
    SEVERE = "severe"           # 严重延误（15-30分钟）
    EXTREME = "extreme"         # 极严重延误（> 30分钟）


@dataclass
class HighSpeedMetrics:
    """
    高铁客运专线核心评估指标
    只关注运营技术维度，不考虑旅客服务维度
    """
    # === 延误控制指标（核心）===
    max_delay_seconds: int = 0                    # 最大延误（秒）- 最关键指标
    avg_delay_seconds: float = 0.0                # 平均延误（秒）
    total_delay_seconds: int = 0                  # 总延误（秒）
    
    # === 延误传播指标（关键）===
    affected_trains_count: int = 0                # 受影响列车数
    propagation_depth: int = 0                    # 传播深度（影响车站数）
    propagation_breadth: int = 0                  # 传播广度（影响列车数）
    propagation_coefficient: float = 0.0          # 传播系数（>1表示放大）
    
    # === 延误分布指标 ===
    normal_count: int = 0                         # 正常数量（（<3分钟）
    slight_count: int = 0                         # 轻微延误数量（3-5分钟）
    moderate_count: int = 0                       # 中度延误数量（5-15分钟）
    severe_count: int = 0                         # 严重延误数量（15-30分钟）
    extreme_count: int = 0                        # 极严重延误数量（>30分钟）
    
    # === 运营恢复指标 ===
    recovery_rate: float = 0.0                    # 恢复率（已恢复时间/总延误）
    recovery_time_estimate: int = 0               # 预计恢复时间（秒）
    
    # === 约束满足指标 ===
    headway_violations: int = 0                   # 追踪间隔违反次数
    stop_time_violations: int = 0                 # 停站时间违反次数
    section_time_violations: int = 0              # 区间运行时间违反次数
    
    # === 计算性能指标 ===
    computation_time: float = 0.0                 # 计算时间（秒）
    solver_status: str = "unknown"                # 求解器状态
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            # 延误控制
            "max_delay_seconds": self.max_delay_seconds,
            "max_delay_minutes": round(self.max_delay_seconds / 60, 1),
            "avg_delay_seconds": round(self.avg_delay_seconds, 1),
            "avg_delay_minutes": round(self.avg_delay_seconds / 60, 1),
            "total_delay_minutes": round(self.total_delay_seconds / 60, 1),
            
            # 传播控制
            "affected_trains_count": self.affected_trains_count,
            "propagation_depth": self.propagation_depth,
            "propagation_breadth": self.propagation_breadth,
            "propagation_coefficient": round(self.propagation_coefficient, 2),
            
            # 延误分布
            "delay_distribution": {
                "normal": self.normal_count,
                "slight": self.slight_count,
                "moderate": self.moderate_count,
                "severe": self.severe_count,
                "extreme": self.extreme_count
            },
            
            # 运营恢复
            "recovery_rate": round(self.recovery_rate * 100, 1),
            "recovery_time_estimate_minutes": round(self.recovery_time_estimate / 60, 1),
            
            # 约束满足
            "constraint_violations": {
                "headway": self.headway_violations,
                "stop_time": self.stop_time_violations,
                "section_time": self.section_time_violations,
                "total": self.headway_violations + self.stop_time_violations + self.section_time_violations
            },
            
            # 计算性能
            "computation_time": round(self.computation_time, 3),
            "solver_status": self.solver_status
        }
    
    def get_summary(self) -> str:
        """获取指标摘要"""
        return (
            f"最大延误: {self.max_delay_seconds // 60}分钟 | "
            f"平均延误: {self.avg_delay_seconds / 60:.1f}分钟 | "
            f"受影响列车: {self.affected_trains_count}列 | "
            f"传播系数: {self.propagation_coefficient:.2f} | "
            f"约束违反: {self.headway_violations + self.stop_time_violations + self.section_time_violations}次"
        )


@dataclass
class HighSpeedEvaluationResult:
    """高铁客运专线评估结果"""
    success: bool
    proposed_metrics: HighSpeedMetrics
    baseline_metrics: HighSpeedMetrics
    
    # 改进幅度
    max_delay_improvement: float = 0.0            # 最大延误改进百分比
    avg_delay_improvement: float = 0.0            # 平均延误改进百分比
    propagation_improvement: float = 0.0          # 传播控制改进百分比
    
    # 评估结论
    is_better_than_baseline: bool = False
    recommended_output: bool = False
    
    # 专业建议
    recommendations: List[str] = field(default_factory=list)
    risk_level: str = "low"                       # low/medium/high/critical
    
    def to_report(self) -> str:
        """生成专业评估报告"""
        lines = [
            "=" * 60,
            "高铁客运专线 - 调度方案评估报告",
            "=" * 60,
            "",
            f"【评估结论】{'通过' if self.recommended_output else '不通过'}",
            f"【风险等级】{self.risk_level}",
            f"【优于基线】{'是' if self.is_better_than_baseline else '否'}",
            "",
            "【优化方案指标】",
            f"  最大延误: {self.proposed_metrics.max_delay_seconds // 60} 分钟 (改进: {self.max_delay_improvement:+.1f}%)",
            f"  平均延误: {self.proposed_metrics.avg_delay_seconds / 60:.1f} 分钟 (改进: {self.avg_delay_improvement:+.1f}%)",
            f"  受影响列车: {self.proposed_metrics.affected_trains_count} 列",
            f"  传播系数: {self.proposed_metrics.propagation_coefficient:.2f} (改进: {self.propagation_improvement:+.1f}%)",
            f"  约束违反: {self.proposed_metrics.headway_violations + self.proposed_metrics.stop_time_violations + self.proposed_metrics.section_time_violations} 次",
            "",
            "【延误分布】",
            f"  正常(<3min): {self.proposed_metrics.normal_count}",
            f"  轻微(3-5min): {self.proposed_metrics.slight_count}",
            f"  中度(5-15min): {self.proposed_metrics.moderate_count}",
            f"  严重(15-30min): {self.proposed_metrics.severe_count}",
            f"  极严重(>30min): {self.proposed_metrics.extreme_count}",
            "",
            "【专业建议】"
        ]
        for rec in self.recommendations:
            lines.append(f"  • {rec}")
        lines.append("=" * 60)
        return "\n".join(lines)


class HighSpeedEvaluator:
    """
    高铁客运专线专用评估器
    针对高铁运营特点设计，关注延误控制和传播抑制
    """
    
    # 高铁延误等级阈值（秒）
    DELAY_THRESHOLDS = {
        "normal": 180,      # 3分钟
        "slight": 300,      # 5分钟
        "moderate": 900,    # 15分钟
        "severe": 1800      # 30分钟
    }
    
    def __init__(self, baseline_strategy: str = "no_adjustment"):
        self.baseline_strategy = baseline_strategy
    
    def compare(
        self,
        proposed_schedule: Dict[str, List[Dict]],
        original_schedule: Dict[str, List[Dict]],
        delay_injection: Dict[str, Any],
        computation_time: float = 0.0,
        solver_status: str = "unknown"
    ) -> HighSpeedEvaluationResult:
        """
        比较优化方案与基线方案（与 evaluate 方法相同，用于兼容接口）
        """
        return self.evaluate(
            proposed_schedule=proposed_schedule,
            original_schedule=original_schedule,
            delay_injection=delay_injection,
            computation_time=computation_time,
            solver_status=solver_status
        )

    def evaluate(
        self,
        proposed_schedule: Dict[str, List[Dict]],
        original_schedule: Dict[str, List[Dict]],
        delay_injection: Dict[str, Any],
        computation_time: float = 0.0,
        solver_status: str = "unknown"
    ) -> HighSpeedEvaluationResult:
        """
        评估调度方案（高铁客运专线专用）
        
        Args:
            proposed_schedule: 优化后的时刻表
            original_schedule: 原始时刻表
            delay_injection: 延误注入数据
            computation_time: 计算时间
            solver_status: 求解器状态
        
        Returns:
            HighSpeedEvaluationResult: 高铁专用评估结果
        """
        # 计算优化方案指标
        proposed_metrics = self._calculate_metrics(
            proposed_schedule, delay_injection, computation_time, solver_status
        )
        
        # 计算基线方案指标
        baseline_schedule = self._generate_baseline(original_schedule, delay_injection)
        baseline_metrics = self._calculate_metrics(
            baseline_schedule, delay_injection, 0.0, "baseline"
        )
        
        # 计算改进幅度
        max_delay_improvement = self._calculate_improvement(
            proposed_metrics.max_delay_seconds,
            baseline_metrics.max_delay_seconds
        )
        avg_delay_improvement = self._calculate_improvement(
            proposed_metrics.avg_delay_seconds,
            baseline_metrics.avg_delay_seconds
        )
        propagation_improvement = self._calculate_improvement(
            baseline_metrics.propagation_coefficient - proposed_metrics.propagation_coefficient,
            baseline_metrics.propagation_coefficient
        )
        
        # 判断是否优于基线
        is_better = (
            proposed_metrics.max_delay_seconds < baseline_metrics.max_delay_seconds or
            proposed_metrics.avg_delay_seconds < baseline_metrics.avg_delay_seconds or
            proposed_metrics.propagation_coefficient < baseline_metrics.propagation_coefficient
        )
        
        # 确定风险等级
        risk_level = self._determine_risk_level(proposed_metrics)
        
        # 生成专业建议
        recommendations = self._generate_recommendations(
            proposed_metrics, baseline_metrics, is_better
        )
        
        # 是否推荐输出
        recommended_output = is_better and risk_level != "critical"
        
        return HighSpeedEvaluationResult(
            success=True,
            proposed_metrics=proposed_metrics,
            baseline_metrics=baseline_metrics,
            max_delay_improvement=max_delay_improvement,
            avg_delay_improvement=avg_delay_improvement,
            propagation_improvement=propagation_improvement,
            is_better_than_baseline=is_better,
            recommended_output=recommended_output,
            recommendations=recommendations,
            risk_level=risk_level
        )
    
    def _calculate_metrics(
        self,
        schedule: Dict[str, List[Dict]],
        delay_injection: Dict[str, Any],
        computation_time: float,
        solver_status: str
    ) -> HighSpeedMetrics:
        """计算高铁专用指标"""
        all_delays = []
        delay_by_train = {}
        delay_by_station = {}
        
        # 延误等级计数
        normal_count = 0
        slight_count = 0
        moderate_count = 0
        severe_count = 0
        extreme_count = 0
        
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
                
                # 延误等级分类
                if delay < self.DELAY_THRESHOLDS["normal"]:
                    normal_count += 1
                elif delay < self.DELAY_THRESHOLDS["slight"]:
                    slight_count += 1
                elif delay < self.DELAY_THRESHOLDS["moderate"]:
                    moderate_count += 1
                elif delay < self.DELAY_THRESHOLDS["severe"]:
                    severe_count += 1
                else:
                    extreme_count += 1
            
            if train_delays:
                delay_by_train[train_id] = {
                    "max": max(train_delays),
                    "count": len(train_delays)
                }
        
        # 基础统计
        if all_delays:
            max_delay = max(all_delays)
            avg_delay = sum(all_delays) / len(all_delays)
            total_delay = sum(all_delays)
            affected_trains = len([t for t, d in delay_by_train.items() if d["max"] > 0])
        else:
            max_delay = 0
            avg_delay = 0.0
            total_delay = 0
            affected_trains = 0
        
        # 传播分析
        propagation_depth = max(
            (len([s for s in stops if s.get("delay_seconds", 0) > 0])
             for stops in schedule.values()),
            default=0
        )
        propagation_breadth = affected_trains
        
        # 计算传播系数（受影响列车数 / 初始延误列车数）
        initial_affected = len(delay_injection.get("injected_delays", []))
        if initial_affected > 0:
            propagation_coefficient = affected_trains / initial_affected
        else:
            propagation_coefficient = 0.0
        
        # 恢复率估计（简化：假设每分钟恢复10%）
        recovery_rate = min(1.0, (max_delay / 60) * 0.1) if max_delay > 0 else 0.0
        recovery_time_estimate = int(max_delay * (1 - recovery_rate))
        
        return HighSpeedMetrics(
            max_delay_seconds=int(max_delay),
            avg_delay_seconds=float(avg_delay),
            total_delay_seconds=int(total_delay),
            affected_trains_count=affected_trains,
            propagation_depth=propagation_depth,
            propagation_breadth=propagation_breadth,
            propagation_coefficient=propagation_coefficient,
            normal_count=normal_count,
            slight_count=slight_count,
            moderate_count=moderate_count,
            severe_count=severe_count,
            extreme_count=extreme_count,
            recovery_rate=recovery_rate,
            recovery_time_estimate=recovery_time_estimate,
            computation_time=computation_time,
            solver_status=solver_status
        )
    
    def _generate_baseline(
        self,
        original_schedule: Dict[str, List[Dict]],
        delay_injection: Dict[str, Any]
    ) -> Dict[str, List[Dict]]:
        """生成基线方案（不调整）"""
        baseline_schedule = {}
        
        for train_id, stops in original_schedule.items():
            baseline_stops = []
            for stop in stops:
                # 检查该站是否有延误注入
                delay = 0
                for injected in delay_injection.get("injected_delays", []):
                    if injected.get("train_id") == train_id:
                        location = injected.get("location", {})
                        if stop.get("station_code") == location.get("station_code"):
                            delay = injected.get("initial_delay_seconds", 0)
                            break
                
                baseline_stops.append({
                    **stop,
                    "delay_seconds": delay
                })
            
            baseline_schedule[train_id] = baseline_stops
        
        return baseline_schedule
    
    def _calculate_improvement(self, proposed: float, baseline: float) -> float:
        """计算改进百分比"""
        if baseline == 0:
            return 0.0 if proposed == 0 else 100.0
        return ((baseline - proposed) / baseline) * 100
    
    def _determine_risk_level(self, metrics: HighSpeedMetrics) -> str:
        """确定风险等级"""
        # 极严重延误 > 30分钟
        if metrics.extreme_count > 0 or metrics.max_delay_seconds > 1800:
            return "critical"
        # 严重延误 > 15分钟
        if metrics.severe_count > 0 or metrics.max_delay_seconds > 900:
            return "high"
        # 中度延误 > 5分钟 或 传播系数 > 2
        if metrics.moderate_count > 0 or metrics.propagation_coefficient > 2:
            return "medium"
        return "low"
    
    def _generate_recommendations(
        self,
        proposed: HighSpeedMetrics,
        baseline: HighSpeedMetrics,
        is_better: bool
    ) -> List[str]:
        """生成专业建议"""
        recommendations = []
        
        if not is_better:
            recommendations.append("优化方案未能改善延误，建议检查约束设置")
            return recommendations
        
        # 延误控制建议
        if proposed.max_delay_seconds > 900:  # > 15分钟
            recommendations.append("最大延误超过15分钟，建议启用应急预案")
        elif proposed.max_delay_seconds > 300:  # > 5分钟
            recommendations.append("最大延误超过5分钟，需密切关注延误传播")
        
        # 传播控制建议
        if proposed.propagation_coefficient > 3:
            recommendations.append("延误传播严重，建议采取隔离措施")
        elif proposed.propagation_coefficient > 1.5:
            recommendations.append("延误有扩散趋势，建议压缩后续列车停站时间")
        
        # 约束违反建议
        total_violations = (
            proposed.headway_violations +
            proposed.stop_time_violations +
            proposed.section_time_violations
        )
        if total_violations > 0:
            recommendations.append(f"存在{total_violations}处约束违反，需人工复核")
        
        # 恢复建议
        if proposed.recovery_time_estimate > 1800:  # > 30分钟
            recommendations.append("预计恢复时间超过30分钟，建议调整后续交路")
        
        if not recommendations:
            recommendations.append("方案可行，建议执行")
        
        return recommendations


# 保持向后兼容的别名
BaselineComparator = HighSpeedEvaluator
EvaluationResult = HighSpeedEvaluationResult
EvaluationMetrics = HighSpeedMetrics
Evaluator = HighSpeedEvaluator  # 添加 Evaluator 别名以兼容旧代码


# 测试代码
if __name__ == "__main__":
    from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
    
    use_real_data(True)
    trains = get_trains_pydantic()[:5]
    stations = get_stations_pydantic()
    
    # 构建测试数据
    original = {}
    proposed = {}
    
    for train in trains:
        stops = []
        if train.schedule and train.schedule.stops:
            for stop in train.schedule.stops:
                if hasattr(stop, 'station_code'):
                    stops.append({
                        "station_code": stop.station_code,
                        "station_name": getattr(stop, 'station_name', stop.station_code),
                        "arrival_time": stop.arrival_time,
                        "departure_time": stop.departure_time,
                        "delay_seconds": 0
                    })
        original[train.train_id] = stops
        # 模拟优化结果：延误减少50%
        proposed[train.train_id] = [
            {**s, "delay_seconds": 300 if i == 0 else 0}
            for i, s in enumerate(stops)
        ]
    
    # 延误注入
    first_train = trains[0].train_id if trains else "G1563"
    first_station = trains[0].schedule.stops[0].station_code if trains and trains[0].schedule.stops else "SJP"
    delay_injection = {
        "injected_delays": [
            {"train_id": first_train, "location": {"station_code": first_station}, "initial_delay_seconds": 600}
        ]
    }
    
    # 评估
    evaluator = HighSpeedEvaluator()
    result = evaluator.evaluate(proposed, original, delay_injection, computation_time=1.5, solver_status="Optimal")
    
    print(result.to_report())
