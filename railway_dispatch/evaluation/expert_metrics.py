# -*- coding: utf-8 -*-
"""
专家级评估指标模块
从列车调度专家角度定义的评估指标

当前max_delay和avg_delay设置合理性分析：
- max_delay（最大延误）：合理，反映最坏情况
- avg_delay（平均延误）：合理，反映整体性能
- 新增指标：延误公平性、鲁棒性、能耗、乘客满意度等
"""

from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import math
import logging

from config import DispatchEnvConfig

logger = logging.getLogger(__name__)


class TrainPriority(Enum):
    """列车优先级枚举"""
    HIGH_SPEED = 5      # 高铁/G字头
    EXPRESS = 4         # 动车/D字头
    NORMAL = 3          # 普速/K/T/Z字头
    FREIGHT = 2         # 货运
    LOCAL = 1           # 管内/通勤


@dataclass
class ExpertEvaluationMetrics:
    """
    专家级评估指标集
    
    从列车调度专家角度，评估调度方案需要关注：
    1. 延误控制（基础指标）
    2. 延误公平性（不同优先级列车的延误分布）
    3. 鲁棒性（对后续扰动的抵抗能力）
    4. 运营效率（区间利用率、停站时间合理性）
    5. 乘客体验（换乘衔接、关键列车准点率）
    6. 能耗指标（速度曲线平滑度、启停次数）
    """
    
    # ========== 基础延误指标 ==========
    max_delay_seconds: int = 0
    avg_delay_seconds: float = 0.0
    total_delay_seconds: int = 0
    affected_trains_count: int = 0
    
    # ========== 延误公平性指标 ==========
    # 基尼系数：衡量延误在不同列车间的分配公平性
    # 0 = 完全公平，1 = 完全不公平
    delay_gini_coefficient: float = 0.0
    
    # 优先级加权延误：高优先级列车的延误惩罚更高
    priority_weighted_delay: float = 0.0
    
    # 高优先级列车准点率（G/D字头）
    high_priority_on_time_rate: float = 1.0
    
    # ========== 鲁棒性指标 ==========
    # 缓冲时间充足率：时刻表中预留的缓冲时间比例
    buffer_time_ratio: float = 0.0
    
    # 关键路径冗余度：关键路径上的时间冗余
    critical_path_redundancy: float = 0.0
    
    # 恢复时间指数：预计恢复正常运行所需时间
    recovery_time_minutes: float = 0.0
    
    # ========== 运营效率指标 ==========
    # 区间利用率：实际运行时间与理论最小时间的比值
    section_utilization_ratio: float = 0.0
    
    # 停站时间合理性：实际停站时间与标准停站时间的偏差
    dwell_time_deviation: float = 0.0
    
    # 追踪间隔达标率：满足最小追踪间隔约束的比例
    headway_compliance_rate: float = 1.0
    
    # 站台容量利用率：各站站台使用率的平均值
    platform_utilization: float = 0.0
    
    # ========== 乘客体验指标 ==========
    # 换乘衔接成功率：与其他列车的换乘衔接成功比例
    connection_success_rate: float = 1.0
    
    # 首末班车准点率：首班车和末班车的准点情况
    first_last_train_on_time: float = 1.0
    
    # 长距离列车准点率：运行距离>500km的列车准点率
    long_distance_on_time_rate: float = 1.0
    
    # ========== 能耗指标 ==========
    # 速度曲线平滑度：速度变化的平滑程度（方差倒数）
    speed_smoothness: float = 0.0
    
    # 启停次数：不必要的启停次数
    unnecessary_stop_count: int = 0
    
    # 能耗效率指数：综合速度、加减速的能耗评估
    energy_efficiency_index: float = 1.0
    
    # ========== 综合评分 ==========
    # 总体专家评分（0-100）
    overall_expert_score: float = 0.0
    
    # 各维度评分
    delay_score: float = 0.0
    fairness_score: float = 0.0
    robustness_score: float = 0.0
    efficiency_score: float = 0.0
    passenger_score: float = 0.0
    energy_score: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            # 基础指标
            "max_delay_minutes": self.max_delay_seconds / 60,
            "avg_delay_minutes": self.avg_delay_seconds / 60,
            "total_delay_minutes": self.total_delay_seconds / 60,
            "affected_trains_count": self.affected_trains_count,
            
            # 公平性
            "delay_gini_coefficient": round(self.delay_gini_coefficient, 3),
            "priority_weighted_delay_minutes": round(self.priority_weighted_delay / 60, 2),
            "high_priority_on_time_rate": round(self.high_priority_on_time_rate, 3),
            
            # 鲁棒性
            "buffer_time_ratio": round(self.buffer_time_ratio, 3),
            "critical_path_redundancy": round(self.critical_path_redundancy, 3),
            "recovery_time_minutes": round(self.recovery_time_minutes, 1),
            
            # 运营效率
            "section_utilization_ratio": round(self.section_utilization_ratio, 3),
            "dwell_time_deviation_seconds": round(self.dwell_time_deviation, 1),
            "headway_compliance_rate": round(self.headway_compliance_rate, 3),
            "platform_utilization": round(self.platform_utilization, 3),
            
            # 乘客体验
            "connection_success_rate": round(self.connection_success_rate, 3),
            "first_last_train_on_time": round(self.first_last_train_on_time, 3),
            "long_distance_on_time_rate": round(self.long_distance_on_time_rate, 3),
            
            # 能耗
            "speed_smoothness": round(self.speed_smoothness, 3),
            "unnecessary_stop_count": self.unnecessary_stop_count,
            "energy_efficiency_index": round(self.energy_efficiency_index, 3),
            
            # 综合评分
            "overall_expert_score": round(self.overall_expert_score, 1),
            "delay_score": round(self.delay_score, 1),
            "fairness_score": round(self.fairness_score, 1),
            "robustness_score": round(self.robustness_score, 1),
            "efficiency_score": round(self.efficiency_score, 1),
            "passenger_score": round(self.passenger_score, 1),
            "energy_score": round(self.energy_score, 1),
        }


class ExpertMetricsCalculator:
    """
    专家级指标计算器
    
    从列车调度专家角度计算各项评估指标
    """
    
    # 列车优先级映射
    PRIORITY_MAP = {
        'G': TrainPriority.HIGH_SPEED,
        'D': TrainPriority.EXPRESS,
        'C': TrainPriority.EXPRESS,
        'Z': TrainPriority.NORMAL,
        'T': TrainPriority.NORMAL,
        'K': TrainPriority.NORMAL,
    }
    
    def __init__(self, trains: List[Any], stations: List[Any]):
        """
        初始化计算器
        
        Args:
            trains: 列车列表
            stations: 车站列表
        """
        self.trains = trains
        self.stations = stations
        self.station_codes = {s.station_code for s in stations}
        
    def calculate_all_metrics(
        self,
        optimized_schedule: Dict[str, List[Dict]],
        original_schedule: Dict[str, List[Dict]],
        delay_injection: Dict[str, Any],
        solving_time: float = 0.0
    ) -> ExpertEvaluationMetrics:
        """
        计算所有专家级指标
        
        Args:
            optimized_schedule: 优化后的时刻表
            original_schedule: 原始时刻表
            delay_injection: 延误注入信息
            solving_time: 求解时间
            
        Returns:
            ExpertEvaluationMetrics: 完整的专家级评估指标
        """
        metrics = ExpertEvaluationMetrics()
        
        # 基础延误指标
        self._calculate_basic_delay_metrics(metrics, optimized_schedule)
        
        # 延误公平性指标
        self._calculate_fairness_metrics(metrics, optimized_schedule)
        
        # 鲁棒性指标
        self._calculate_robustness_metrics(metrics, optimized_schedule, original_schedule)
        
        # 运营效率指标
        self._calculate_efficiency_metrics(metrics, optimized_schedule, original_schedule)
        
        # 乘客体验指标
        self._calculate_passenger_metrics(metrics, optimized_schedule, original_schedule)
        
        # 能耗指标
        self._calculate_energy_metrics(metrics, optimized_schedule, original_schedule)
        
        # 计算综合评分
        self._calculate_overall_scores(metrics)
        
        return metrics
    
    def _calculate_basic_delay_metrics(
        self,
        metrics: ExpertEvaluationMetrics,
        schedule: Dict[str, List[Dict]]
    ):
        """计算基础延误指标"""
        all_delays = []
        affected_count = 0
        
        for train_id, stops in schedule.items():
            train_has_delay = False
            for stop in stops:
                delay = stop.get("delay_seconds", 0)
                if delay > 0:
                    all_delays.append(delay)
                    train_has_delay = True
            if train_has_delay:
                affected_count += 1
        
        metrics.max_delay_seconds = max(all_delays) if all_delays else 0
        metrics.avg_delay_seconds = sum(all_delays) / len(all_delays) if all_delays else 0
        metrics.total_delay_seconds = sum(all_delays)
        metrics.affected_trains_count = affected_count
    
    def _calculate_fairness_metrics(
        self,
        metrics: ExpertEvaluationMetrics,
        schedule: Dict[str, List[Dict]]
    ):
        """计算延误公平性指标"""
        # 收集每列车的总延误
        train_delays = {}
        priority_weights = {}
        
        for train_id, stops in schedule.items():
            total_delay = sum(stop.get("delay_seconds", 0) for stop in stops)
            train_delays[train_id] = total_delay
            
            # 确定列车优先级
            priority = self._get_train_priority(train_id)
            priority_weights[train_id] = priority.value
        
        # 计算基尼系数
        if train_delays:
            metrics.delay_gini_coefficient = self._calculate_gini_coefficient(
                list(train_delays.values())
            )
        
        # 计算优先级加权延误
        weighted_delay = 0
        total_weight = 0
        for train_id, delay in train_delays.items():
            weight = priority_weights[train_id]
            weighted_delay += delay * weight
            total_weight += weight
        
        metrics.priority_weighted_delay = weighted_delay / total_weight if total_weight > 0 else 0
        
        # 计算高优先级列车准点率
        high_priority_trains = [
            tid for tid in train_delays.keys()
            if priority_weights[tid] >= TrainPriority.EXPRESS.value
        ]
        
        if high_priority_trains:
            on_time_threshold = DispatchEnvConfig.on_time_threshold_seconds()
            on_time_count = sum(
                1 for tid in high_priority_trains
                if train_delays[tid] < on_time_threshold
            )
            metrics.high_priority_on_time_rate = on_time_count / len(high_priority_trains)
    
    def _calculate_robustness_metrics(
        self,
        metrics: ExpertEvaluationMetrics,
        optimized_schedule: Dict[str, List[Dict]],
        original_schedule: Dict[str, List[Dict]]
    ):
        """计算鲁棒性指标"""
        # 计算缓冲时间比例
        total_buffer = 0
        total_running_time = 0
        
        for train_id in optimized_schedule:
            if train_id not in original_schedule:
                continue
                
            opt_stops = optimized_schedule[train_id]
            orig_stops = original_schedule[train_id]
            
            for i in range(len(opt_stops) - 1):
                # 计算区间运行时间
                opt_arr = self._time_to_seconds(opt_stops[i+1]["arrival_time"])
                opt_dep = self._time_to_seconds(opt_stops[i]["departure_time"])
                opt_run_time = opt_arr - opt_dep
                
                orig_arr = self._time_to_seconds(orig_stops[i+1]["arrival_time"])
                orig_dep = self._time_to_seconds(orig_stops[i]["departure_time"])
                orig_run_time = orig_arr - orig_dep
                
                if orig_run_time > 0:
                    buffer = orig_run_time - opt_run_time
                    if buffer > 0:
                        total_buffer += buffer
                    total_running_time += orig_run_time
        
        metrics.buffer_time_ratio = total_buffer / total_running_time if total_running_time > 0 else 0
        
        # 简化计算：恢复时间指数 = 最大延误 / 平均区间运行时间
        if metrics.max_delay_seconds > 0 and total_running_time > 0:
            avg_section_time = total_running_time / sum(
                len(optimized_schedule[tid]) - 1
                for tid in optimized_schedule
                if len(optimized_schedule[tid]) > 1
            ) if optimized_schedule else 1
            
            metrics.recovery_time_minutes = (metrics.max_delay_seconds / avg_section_time) * 5
    
    def _calculate_efficiency_metrics(
        self,
        metrics: ExpertEvaluationMetrics,
        optimized_schedule: Dict[str, List[Dict]],
        original_schedule: Dict[str, List[Dict]]
    ):
        """计算运营效率指标"""
        # 停站时间偏差
        dwell_deviations = []
        
        for train_id in optimized_schedule:
            if train_id not in original_schedule:
                continue
                
            opt_stops = optimized_schedule[train_id]
            orig_stops = original_schedule[train_id]
            
            for i, opt_stop in enumerate(opt_stops):
                if i >= len(orig_stops):
                    break
                    
                orig_stop = orig_stops[i]
                
                opt_arr = self._time_to_seconds(opt_stop["arrival_time"])
                opt_dep = self._time_to_seconds(opt_stop["departure_time"])
                opt_dwell = opt_dep - opt_arr
                
                orig_arr = self._time_to_seconds(orig_stop["arrival_time"])
                orig_dep = self._time_to_seconds(orig_stop["departure_time"])
                orig_dwell = orig_dep - orig_arr
                
                dwell_deviations.append(abs(opt_dwell - orig_dwell))
        
        metrics.dwell_time_deviation = sum(dwell_deviations) / len(dwell_deviations) if dwell_deviations else 0
        
        # 区间利用率（简化计算）
        total_opt_time = 0
        total_orig_time = 0
        
        for train_id in optimized_schedule:
            if train_id not in original_schedule:
                continue
                
            opt_stops = optimized_schedule[train_id]
            orig_stops = original_schedule[train_id]
            
            if len(opt_stops) >= 2 and len(orig_stops) >= 2:
                opt_start = self._time_to_seconds(opt_stops[0]["departure_time"])
                opt_end = self._time_to_seconds(opt_stops[-1]["arrival_time"])
                orig_start = self._time_to_seconds(orig_stops[0]["departure_time"])
                orig_end = self._time_to_seconds(orig_stops[-1]["arrival_time"])
                
                total_opt_time += opt_end - opt_start
                total_orig_time += orig_end - orig_start
        
        metrics.section_utilization_ratio = total_orig_time / total_opt_time if total_opt_time > 0 else 1.0
    
    def _calculate_passenger_metrics(
        self,
        metrics: ExpertEvaluationMetrics,
        optimized_schedule: Dict[str, List[Dict]],
        original_schedule: Dict[str, List[Dict]]
    ):
        """计算乘客体验指标"""
        # 首末班车准点率
        first_last_trains = []
        
        # 按首站发车时间排序
        train_first_deps = []
        for train_id, stops in original_schedule.items():
            if stops:
                dep_time = self._time_to_seconds(stops[0]["departure_time"])
                train_first_deps.append((train_id, dep_time))
        
        train_first_deps.sort(key=lambda x: x[1])
        
        if len(train_first_deps) >= 2:
            first_last_trains = [train_first_deps[0][0], train_first_deps[-1][0]]
        
        on_time_threshold = DispatchEnvConfig.on_time_threshold_seconds()
        if first_last_trains:
            on_time_count = 0
            for train_id in first_last_trains:
                if train_id in optimized_schedule:
                    max_delay = max(
                        stop.get("delay_seconds", 0)
                        for stop in optimized_schedule[train_id]
                    )
                    if max_delay < on_time_threshold:
                        on_time_count += 1
            
            metrics.first_last_train_on_time = on_time_count / len(first_last_trains)
        
        # 长距离列车准点率（简化：假设超过5站的为长距离）
        long_distance_trains = [
            tid for tid, stops in original_schedule.items()
            if len(stops) >= 5
        ]
        
        if long_distance_trains:
            on_time_count = 0
            for train_id in long_distance_trains:
                if train_id in optimized_schedule:
                    max_delay = max(
                        stop.get("delay_seconds", 0)
                        for stop in optimized_schedule[train_id]
                    )
                    if max_delay < on_time_threshold:
                        on_time_count += 1
            
            metrics.long_distance_on_time_rate = on_time_count / len(long_distance_trains)
    
    def _calculate_energy_metrics(
        self,
        metrics: ExpertEvaluationMetrics,
        optimized_schedule: Dict[str, List[Dict]],
        original_schedule: Dict[str, List[Dict]]
    ):
        """计算能耗指标"""
        # 计算速度平滑度（基于时间变化的方差）
        speed_variations = []
        unnecessary_stops = 0
        
        for train_id in optimized_schedule:
            if train_id not in original_schedule:
                continue
                
            opt_stops = optimized_schedule[train_id]
            orig_stops = original_schedule[train_id]
            
            for i in range(len(opt_stops) - 1):
                if i >= len(orig_stops) - 1:
                    break
                
                # 计算区间运行时间
                opt_arr = self._time_to_seconds(opt_stops[i+1]["arrival_time"])
                opt_dep = self._time_to_seconds(opt_stops[i]["departure_time"])
                opt_run_time = opt_arr - opt_dep
                
                orig_arr = self._time_to_seconds(orig_stops[i+1]["arrival_time"])
                orig_dep = self._time_to_seconds(orig_stops[i]["departure_time"])
                orig_run_time = orig_arr - orig_dep
                
                if orig_run_time > 0:
                    speed_ratio = orig_run_time / opt_run_time if opt_run_time > 0 else 1.0
                    speed_variations.append(speed_ratio)
                
                # 检查是否是不必要的停车（原时刻表通过，新时刻表停车）
                orig_dwell = orig_arr - orig_dep
                opt_dwell = opt_arr - opt_dep
                
                if orig_dwell <= 60 and opt_dwell > 120:  # 原停1分钟内，现停超过2分钟
                    unnecessary_stops += 1
        
        # 速度平滑度 = 1 / (1 + 变异系数)
        if speed_variations:
            mean_speed = sum(speed_variations) / len(speed_variations)
            variance = sum((x - mean_speed) ** 2 for x in speed_variations) / len(speed_variations)
            cv = math.sqrt(variance) / mean_speed if mean_speed > 0 else 0
            metrics.speed_smoothness = 1.0 / (1.0 + cv)
        
        metrics.unnecessary_stop_count = unnecessary_stops
        
        # 能耗效率指数（综合指标）
        # 考虑速度平滑度和不必要的停车
        metrics.energy_efficiency_index = (
            0.6 * metrics.speed_smoothness +
            0.4 * max(0, 1 - unnecessary_stops / max(len(optimized_schedule), 1))
        )
    
    def _calculate_overall_scores(self, metrics: ExpertEvaluationMetrics):
        """计算各维度综合评分（0-100分）"""
        # 延误评分：基于max_delay和avg_delay
        # 0延误=100分，每增加1分钟扣1分，最低0分
        max_delay_minutes = metrics.max_delay_seconds / 60
        avg_delay_minutes = metrics.avg_delay_seconds / 60
        
        metrics.delay_score = max(0, 100 - max_delay_minutes * 2 - avg_delay_minutes)
        
        # 公平性评分：基于基尼系数和高优先级准点率
        # 基尼系数0=100分，1=0分
        metrics.fairness_score = (
            0.5 * (1 - metrics.delay_gini_coefficient) * 100 +
            0.5 * metrics.high_priority_on_time_rate * 100
        )
        
        # 鲁棒性评分：基于缓冲时间和恢复时间
        metrics.robustness_score = (
            0.4 * min(100, metrics.buffer_time_ratio * 200) +
            0.6 * max(0, 100 - metrics.recovery_time_minutes)
        )
        
        # 效率评分：基于区间利用率和停站偏差
        dwell_penalty = min(50, metrics.dwell_time_deviation / 60)
        metrics.efficiency_score = (
            0.5 * min(100, metrics.section_utilization_ratio * 100) +
            0.5 * max(0, 100 - dwell_penalty)
        )
        
        # 乘客体验评分
        metrics.passenger_score = (
            0.3 * metrics.first_last_train_on_time * 100 +
            0.4 * metrics.long_distance_on_time_rate * 100 +
            0.3 * metrics.high_priority_on_time_rate * 100
        )
        
        # 能耗评分
        metrics.energy_score = metrics.energy_efficiency_index * 100
        
        # 总体专家评分：加权平均
        weights = {
            'delay': 0.25,
            'fairness': 0.15,
            'robustness': 0.20,
            'efficiency': 0.15,
            'passenger': 0.15,
            'energy': 0.10
        }
        
        metrics.overall_expert_score = (
            weights['delay'] * metrics.delay_score +
            weights['fairness'] * metrics.fairness_score +
            weights['robustness'] * metrics.robustness_score +
            weights['efficiency'] * metrics.efficiency_score +
            weights['passenger'] * metrics.passenger_score +
            weights['energy'] * metrics.energy_score
        )
    
    def _get_train_priority(self, train_id: str) -> TrainPriority:
        """获取列车优先级"""
        if not train_id:
            return TrainPriority.NORMAL
        
        prefix = train_id[0].upper() if train_id else 'K'
        return self.PRIORITY_MAP.get(prefix, TrainPriority.NORMAL)
    
    def _calculate_gini_coefficient(self, values: List[float]) -> float:
        """
        计算基尼系数
        
        Args:
            values: 数值列表
            
        Returns:
            float: 基尼系数（0-1）
        """
        if not values or sum(values) == 0:
            return 0.0
        
        n = len(values)
        sorted_values = sorted(values)
        cumsum = 0
        weighted_sum = 0
        
        for i, value in enumerate(sorted_values, 1):
            cumsum += value
            weighted_sum += i * value
        
        total = sum(sorted_values)
        if total == 0:
            return 0.0
        
        # 基尼系数公式
        gini = (2 * weighted_sum) / (n * total) - (n + 1) / n
        return max(0.0, min(1.0, gini))
    
    def _time_to_seconds(self, time_str: str) -> int:
        """将时间字符串转换为秒数"""
        if not time_str:
            return 0
        
        parts = time_str.split(':')
        if len(parts) == 2:
            h, m = map(int, parts)
            return h * 3600 + m * 60
        elif len(parts) == 3:
            h, m, s = map(int, parts)
            return h * 3600 + m * 60 + s
        return 0


class ExpertEvaluationReport:
    """
    专家级评估报告生成器
    生成人类可读的专家级评估报告
    """
    
    def __init__(self, metrics: ExpertEvaluationMetrics):
        self.metrics = metrics
    
    def generate_report(self) -> str:
        """生成专家级评估报告"""
        m = self.metrics
        
        report = f"""
========================================
      铁路调度 - 专家级评估报告
========================================

【总体评分】
综合专家评分: {m.overall_expert_score:.1f}/100

各维度评分:
  - 延误控制: {m.delay_score:.1f}/100
  - 延误公平性: {m.fairness_score:.1f}/100
  - 方案鲁棒性: {m.robustness_score:.1f}/100
  - 运营效率: {m.efficiency_score:.1f}/100
  - 乘客体验: {m.passenger_score:.1f}/100
  - 能耗效率: {m.energy_score:.1f}/100

【基础延误指标】
- 最大延误: {m.max_delay_seconds/60:.1f} 分钟
- 平均延误: {m.avg_delay_seconds/60:.2f} 分钟
- 总延误: {m.total_delay_seconds/60:.1f} 分钟
- 受影响列车数: {m.affected_trains_count}

【延误公平性分析】
- 延误基尼系数: {m.delay_gini_coefficient:.3f} (0=完全公平, 1=完全不公平)
- 优先级加权延误: {m.priority_weighted_delay/60:.2f} 分钟
- 高优先级列车准点率: {m.high_priority_on_time_rate*100:.1f}%

【鲁棒性分析】
- 缓冲时间比例: {m.buffer_time_ratio*100:.1f}%
- 预计恢复时间: {m.recovery_time_minutes:.1f} 分钟

【运营效率分析】
- 区间利用率: {m.section_utilization_ratio*100:.1f}%
- 停站时间偏差: {m.dwell_time_deviation:.1f} 秒
- 站台容量利用率: {m.platform_utilization*100:.1f}%

【乘客体验分析】
- 首末班车准点率: {m.first_last_train_on_time*100:.1f}%
- 长距离列车准点率: {m.long_distance_on_time_rate*100:.1f}%

【能耗分析】
- 速度曲线平滑度: {m.speed_smoothness:.3f}
- 不必要停车次数: {m.unnecessary_stop_count}
- 能耗效率指数: {m.energy_efficiency_index:.3f}

【专家点评】
{self._generate_expert_commentary()}

========================================
"""
        return report
    
    def _generate_expert_commentary(self) -> str:
        """生成专家点评"""
        comments = []
        m = self.metrics
        
        # 延误点评
        on_time_threshold = DispatchEnvConfig.on_time_threshold_seconds()
        if m.max_delay_seconds < on_time_threshold:
            comments.append("延误控制优秀，最大延误控制在准点阈值以内。")
        elif m.max_delay_seconds < 600:
            comments.append("延误控制良好，但仍有优化空间。")
        else:
            comments.append("延误较大，建议考虑调整发车顺序或增加缓冲时间。")
        
        # 公平性点评
        if m.delay_gini_coefficient < 0.2:
            comments.append("延误分配非常公平，各列车延误分布均衡。")
        elif m.delay_gini_coefficient < 0.4:
            comments.append("延误分配较为公平。")
        else:
            comments.append("延误分配不够公平，部分列车延误过大。")
        
        # 鲁棒性点评
        if m.buffer_time_ratio > 0.1:
            comments.append("方案预留了充足的缓冲时间，对后续扰动有较好的抵抗能力。")
        else:
            comments.append("缓冲时间较少，建议增加时间冗余以提高鲁棒性。")
        
        # 能耗点评
        if m.unnecessary_stop_count == 0:
            comments.append("运行曲线平滑，无不必要的停车，能耗控制良好。")
        else:
            comments.append(f"存在{m.unnecessary_stop_count}次不必要的停车，建议优化运行曲线以降低能耗。")
        
        return "\n".join(f"- {c}" for c in comments)


# 便捷函数
def calculate_expert_metrics(
    optimized_schedule: Dict[str, List[Dict]],
    original_schedule: Dict[str, List[Dict]],
    trains: List[Any],
    stations: List[Any],
    delay_injection: Dict[str, Any],
    solving_time: float = 0.0
) -> ExpertEvaluationMetrics:
    """
    便捷函数：计算专家级评估指标
    
    Args:
        optimized_schedule: 优化后的时刻表
        original_schedule: 原始时刻表
        trains: 列车列表
        stations: 车站列表
        delay_injection: 延误注入信息
        solving_time: 求解时间
        
    Returns:
        ExpertEvaluationMetrics: 专家级评估指标
    """
    calculator = ExpertMetricsCalculator(trains, stations)
    return calculator.calculate_all_metrics(
        optimized_schedule, original_schedule, delay_injection, solving_time
    )


def generate_expert_report(metrics: ExpertEvaluationMetrics) -> str:
    """
    便捷函数：生成专家级评估报告
    
    Args:
        metrics: 专家级评估指标
        
    Returns:
        str: 人类可读的专家级评估报告
    """
    report = ExpertEvaluationReport(metrics)
    return report.generate_report()
