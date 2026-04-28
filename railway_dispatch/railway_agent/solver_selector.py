# -*- coding: utf-8 -*-
"""
求解器选择与评分模块（Solver Selector）

【实现类型】规则驱动（推荐方法）+ 计算驱动（评分方法）
【设计定位】score_result 和 find_pareto_front 为数学计算（非AI）；
  recommend_solver 为基于场景特征的条件规则推荐，作为 LLM 决策的参考基准。
【实验阶段策略】L2 层优先使用 LLM Function Calling 选择求解器，
  SolverSelector.recommend_solver 仅作为规则对比基线或 LLM 失败时的兜底。

职责：
  1. 多目标评分：对求解结果进行基于优化目标的综合评分（计算驱动）
  2. Pareto分析：识别多目标空间中的非支配解集（计算驱动）
  3. 求解器推荐：基于AccidentCard特征和 urgency 推荐求解器与参数（规则驱动）

设计原则：
  - 高内聚：所有求解器选择逻辑集中于此，消除 layer2_planner/skills 中的重复/不一致
  - 低耦合：不依赖求解器执行，只接收结果字典做纯计算
  - 可测试：所有核心方法为纯函数，无副作用
"""

from typing import Dict, List, Any, Optional
import logging

from scheduler_comparison.metrics import HighSpeedMetricsWeight

logger = logging.getLogger(__name__)


class SolverSelector:
    """
    求解器选择器

    【实现类型】score_result/find_pareto_front 为数学计算；
      recommend_solver 为基于阈值的规则推荐（兜底用途）。
    统一求解器评分、Pareto分析和推荐逻辑。
    """

    # 归一化基准阈值（与 comparator.py / layer2_planner 对齐）
    THRESHOLDS = {
        "max_delay_minutes": 30.0,     # 最大延误30分钟为满分阈值
        "avg_delay_minutes": 30.0,     # 平均延误30分钟为满分阈值
        "total_delay_minutes": 120.0,  # 总延误120分钟为满分阈值
        "affected_trains_count": 10,   # 受影响10列为满分阈值
        "solving_time_seconds": 60.0,  # 求解60秒为满分阈值
        "on_time_rate": 1.0,           # 准点率，越高越好
    }

    @staticmethod
    def score_result(result: Dict[str, Any], objective: str = "min_total_delay") -> Dict[str, Any]:
        """
        对单个求解结果进行综合评分（越低越好）

        Args:
            result: 求解结果字典，需包含 metrics 相关字段
            objective: 优化目标，影响权重分配

        Returns:
            Dict: 原结果附加 "composite_score" 和各分项得分
        """
        # 复制结果，避免修改原始数据
        scored = dict(result)

        # 提取指标（防御性处理 None）
        max_delay = result.get("max_delay_minutes") or 0
        avg_delay = result.get("avg_delay_minutes") or 0
        total_delay = result.get("total_delay_minutes") or 0
        affected = result.get("affected_trains_count") or 0
        comp_time = result.get("solving_time_seconds") or 0
        on_time = result.get("on_time_rate")
        if on_time is None:
            on_time = 1.0

        th = SolverSelector.THRESHOLDS

        # 归一化（0-100，越低越好）
        max_delay_score = min(max_delay / th["max_delay_minutes"] * 100, 100)
        avg_delay_score = min(avg_delay / th["avg_delay_minutes"] * 100, 100)
        total_delay_score = min(total_delay / th["total_delay_minutes"] * 100, 100)
        affected_score = min(affected / th["affected_trains_count"] * 100, 100)
        comp_score = min(comp_time / th["solving_time_seconds"] * 100, 100)
        on_time_score = (1 - on_time) * 100

        # 获取权重
        weights = SolverSelector._resolve_weights(objective)

        composite = (
            max_delay_score * weights["max_delay_weight"] +
            avg_delay_score * weights["avg_delay_weight"] +
            total_delay_score * weights["total_delay_weight"] +
            affected_score * weights["affected_trains_weight"] +
            comp_score * weights["computation_time_weight"] +
            on_time_score * weights["on_time_rate_weight"]
        )

        scored["composite_score"] = round(composite, 2)
        scored["_score_breakdown"] = {
            "max_delay_score": round(max_delay_score, 2),
            "avg_delay_score": round(avg_delay_score, 2),
            "total_delay_score": round(total_delay_score, 2),
            "affected_score": round(affected_score, 2),
            "computation_score": round(comp_score, 2),
            "on_time_score": round(on_time_score, 2),
            "weights_used": objective,
        }
        return scored

    @staticmethod
    def _resolve_weights(objective: str) -> Dict[str, float]:
        """根据优化目标解析权重（归一化后）"""
        if objective == "min_max_delay":
            w = HighSpeedMetricsWeight.for_min_max_delay()
        elif objective == "min_avg_delay":
            w = HighSpeedMetricsWeight.for_min_avg_delay()
        elif objective == "min_total_delay":
            w = HighSpeedMetricsWeight.for_min_total_delay()
        elif objective == "real_time":
            w = HighSpeedMetricsWeight.for_real_time()
        elif objective == "min_propagation":
            w = HighSpeedMetricsWeight.for_min_propagation()
        else:
            w = HighSpeedMetricsWeight.for_balanced()

        return {
            "max_delay_weight": w.max_delay_weight,
            "avg_delay_weight": w.avg_delay_weight,
            "total_delay_weight": w.total_delay_weight,
            "affected_trains_weight": w.affected_trains_weight,
            "computation_time_weight": w.computation_time_weight,
            "on_time_rate_weight": w.on_time_rate_weight,
        }

    @staticmethod
    def find_pareto_front(
        results: List[Dict[str, Any]],
        objectives: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        找出Pareto最优解集（非支配解）

        默认在 (max_delay, avg_delay, total_delay, affected_trains, computation_time)
        五维空间中寻找非支配解。

        Args:
            results: 求解结果列表（每个结果应已含评分字段）
            objectives: 要最小化的指标字段名列表，默认五维

        Returns:
            Pareto最优解列表
        """
        if not results:
            return []

        if objectives is None:
            objectives = [
                "max_delay_minutes",
                "avg_delay_minutes",
                "total_delay_minutes",
                "affected_trains_count",
                "solving_time_seconds",
            ]

        # 过滤出所有目标字段都存在的结果
        valid_results = []
        for r in results:
            if all(r.get(k) is not None for k in objectives):
                valid_results.append(r)

        if not valid_results:
            return []

        def _dominates(a: Dict, b: Dict) -> bool:
            """判断a是否支配b（在所有目标上不差于b，且至少一个目标严格更好）"""
            strictly_better = False
            for key in objectives:
                av = a.get(key) or 0
                bv = b.get(key) or 0
                if av > bv:
                    return False
                if av < bv:
                    strictly_better = True
            return strictly_better

        pareto = []
        for r in valid_results:
            is_dominated = False
            for other in valid_results:
                if other is r:
                    continue
                if _dominates(other, r):
                    is_dominated = True
                    break
            if not is_dominated:
                pareto.append(r)

        # 按 composite_score 排序（如果存在）
        pareto.sort(key=lambda x: x.get("composite_score", 9999))
        return pareto

    @staticmethod
    def recommend_solver(
        accident_card_dict: Dict[str, Any],
        urgency: str = "medium",
        is_peak: bool = False,
        time_budget_seconds: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        【规则方法】基于事故特征推荐求解器和优化目标
        作为 LLM 决策的规则对比基线；LLM 失败时由 L2._rule_fallback 调用。

        Args:
            accident_card_dict: AccidentCard 的字典表示
            urgency: assess_impact 返回的紧急程度
            is_peak: 是否为高峰时段
            time_budget_seconds: 可用时间预算（秒）

        Returns:
            Dict: {
                "solver": str,
                "optimization_objective": str,
                "time_limit": int | None,
                "optimality_gap": float | None,
                "reasoning": str
            }
        """
        scene = accident_card_dict.get("scene_category", "")
        delay = accident_card_dict.get("expected_duration") or 10
        is_complete = accident_card_dict.get("is_complete", False)
        location_type = accident_card_dict.get("location_type", "station")
        affected_train_ids = accident_card_dict.get("affected_train_ids") or []

        # 安全约束（最高优先级）
        if scene == "区间封锁" or not is_complete:
            return {
                "solver": "fcfs",
                "optimization_objective": "min_total_delay",
                "time_limit": None,
                "optimality_gap": None,
                "reasoning": (
                    "区间封锁/信息不完整 → FCFS（安全兜底）"
                    if scene == "区间封锁" else "信息不完整 → FCFS（保守策略）"
                )
            }

        # 紧急程度判断
        is_emergency = urgency in ("high", "critical") or delay > 60
        is_large_scale = len(affected_train_ids) > 10 or delay > 30

        if is_emergency:
            solver = "fcfs"
            objective = "min_max_delay"
            time_limit = None
            gap = None
            reasoning = (
                f"紧急场景（urgency={urgency}, 延误={delay}分）"
                f"→ FCFS（秒级响应，优先控制最大延误）"
            )
        elif time_budget_seconds is not None and time_budget_seconds < 30:
            solver = "fcfs"
            objective = "min_total_delay"
            time_limit = None
            gap = None
            reasoning = f"时间预算仅{time_budget_seconds}秒 → FCFS（快速响应）"
        elif is_large_scale:
            solver = "hierarchical"
            objective = "min_total_delay"
            time_limit = 120
            gap = 0.05
            reasoning = (
                f"大规模影响（{len(affected_train_ids)}列/延误{delay}分）"
                f"→ Hierarchical（兼顾质量与速度）"
            )
        elif is_peak:
            solver = "hierarchical"
            objective = "min_total_delay"
            time_limit = 90
            gap = 0.05
            reasoning = "高峰时段 → Hierarchical（线路饱和，需快速高质量方案）"
        else:
            # 小规模非高峰
            if delay <= 15:
                solver = "mip"
                objective = "min_total_delay"
                time_limit = 120
                gap = 0.05
                reasoning = "小规模、轻微延误、平峰 → MIP（追求全局最优）"
            else:
                solver = "hierarchical"
                objective = "min_total_delay"
                time_limit = 120
                gap = 0.05
                reasoning = "小规模、中等延误 → Hierarchical（自动选择最优策略）"

        return {
            "solver": solver,
            "optimization_objective": objective,
            "time_limit": time_limit,
            "optimality_gap": gap,
            "reasoning": reasoning
        }
