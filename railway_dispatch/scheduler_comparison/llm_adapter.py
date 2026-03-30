# -*- coding: utf-8 -*-
"""
铁路调度系统 - 大模型输出适配器
将比较结果转换为适合大模型理解和输出的格式
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum
import json

from .comparator import MultiComparisonResult, ComparisonResult
from .metrics import EvaluationMetrics, MetricsDefinition


class LLMOutputFormat(str, Enum):
    """大模型输出格式"""
    JSON = "json"                    # JSON格式
    MARKDOWN = "markdown"            # Markdown格式
    STRUCTURED_TEXT = "text"         # 结构化文本
    SUMMARY = "summary"              # 摘要格式
    DETAILED = "detailed"            # 详细格式


@dataclass
class LLMContext:
    """
    大模型上下文
    包含调度决策所需的所有信息
    """
    comparison_result: MultiComparisonResult
    user_preference: str              # 用户偏好描述
    scenario_description: str         # 场景描述
    decision_factors: List[str]       # 决策因素
    
    def to_prompt_context(self) -> str:
        """转换为Prompt上下文"""
        return f"""
## 调度场景
{self.scenario_description}

## 用户偏好
{self.user_preference}

## 比较结果摘要
- 最优方案: {self.comparison_result.winner.scheduler_name if self.comparison_result.winner else '无'}
- 比较准则: {self.comparison_result.criteria.value}
- 可选方案数: {len(self.comparison_result.results)}

## 决策因素
{chr(10).join(f'- {f}' for f in self.decision_factors)}
"""


class LLMOutputAdapter:
    """
    大模型输出适配器
    将调度比较结果转换为适合大模型输出和理解的格式
    """
    
    def __init__(self, output_format: LLMOutputFormat = LLMOutputFormat.MARKDOWN):
        self.output_format = output_format
    
    def adapt(
        self,
        comparison_result: MultiComparisonResult,
        format: Optional[LLMOutputFormat] = None
    ) -> str:
        """
        转换比较结果为指定格式
        
        Args:
            comparison_result: 比较结果
            format: 输出格式（None则使用实例默认格式）
        
        Returns:
            格式化后的输出字符串
        """
        format = format or self.output_format
        
        adapters = {
            LLMOutputFormat.JSON: self._to_json,
            LLMOutputFormat.MARKDOWN: self._to_markdown,
            LLMOutputFormat.STRUCTURED_TEXT: self._to_structured_text,
            LLMOutputFormat.SUMMARY: self._to_summary,
            LLMOutputFormat.DETAILED: self._to_detailed
        }
        
        adapter = adapters.get(format, self._to_markdown)
        return adapter(comparison_result)
    
    def _to_json(self, result: MultiComparisonResult) -> str:
        """转换为JSON格式"""
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    
    def _to_markdown(self, result: MultiComparisonResult) -> str:
        """转换为Markdown格式"""
        lines = [
            "# 铁路调度方案比较报告",
            "",
            "## 📊 比较概览",
            "",
            f"| 指标 | 值 |",
            f"|------|------|",
            f"| 比较准则 | {result.criteria.value} |",
            f"| 参与方案数 | {len(result.results)} |",
            f"| 计算时间 | {result.computation_time:.2f}秒 |",
            ""
        ]
        
        # 排名表格
        lines.extend([
            "## 🏆 方案排名",
            "",
            "| 排名 | 调度器 | 最大延误 | 平均延误 | 受影响列车 | 准点率 | 计算时间 |",
            "|------|--------|----------|----------|------------|--------|----------|"
        ])
        
        for r in sorted(result.results, key=lambda x: x.rank):
            m = r.result.metrics
            winner_mark = " ⭐" if r.is_winner else ""
            lines.append(
                f"| {r.rank} | {r.scheduler_name}{winner_mark} | "
                f"{m.max_delay_seconds // 60}分钟 | "
                f"{m.avg_delay_seconds / 60:.1f}分钟 | "
                f"{m.affected_trains_count}列 | "
                f"{m.on_time_rate * 100:.1f}% | "
                f"{m.computation_time:.2f}秒 |"
            )
        
        lines.append("")
        
        # 最优方案详情
        if result.winner:
            winner = result.winner
            m = winner.result.metrics
            
            lines.extend([
                "## ✅ 推荐方案",
                "",
                f"**{winner.scheduler_name}**",
                "",
                "### 延误指标",
                f"- 最大延误: **{m.max_delay_seconds // 60}分钟** ({m.max_delay_seconds}秒)",
                f"- 平均延误: **{m.avg_delay_seconds / 60:.1f}分钟** ({m.avg_delay_seconds:.1f}秒)",
                f"- 总延误: {m.total_delay_seconds // 60}分钟",
                f"- 受影响列车: {m.affected_trains_count}列",
                "",
                "### 质量指标",
                f"- 准点率: {m.on_time_rate * 100:.1f}%",
                f"- 延误恢复率: {m.recovery_rate * 100:.1f}%",
                "",
                "### 延误分布",
                f"- 微小延误(<5分钟): {m.micro_delay_count}次",
                f"- 小延误(5-30分钟): {m.small_delay_count}次",
                f"- 中延误(30-100分钟): {m.medium_delay_count}次",
                f"- 大延误(>100分钟): {m.large_delay_count}次",
                ""
            ])
            
            # 相对基线改进
            if winner.improvement_over_baseline:
                lines.extend([
                    "### 相对平均水平的改进",
                    ""
                ])
                imp = winner.improvement_over_baseline
                for key, value in imp.items():
                    if value != 0:
                        key_name = key.replace("_improvement", "").replace("_", " ")
                        sign = "+" if value > 0 else ""
                        lines.append(f"- {key_name}: {sign}{value:.1f}%")
                lines.append("")
        
        # 建议
        if result.recommendations:
            lines.extend([
                "## 💡 建议",
                ""
            ])
            for rec in result.recommendations:
                lines.append(f"- {rec}")
            lines.append("")
        
        return "\n".join(lines)
    
    def _to_structured_text(self, result: MultiComparisonResult) -> str:
        """转换为结构化文本格式"""
        lines = [
            "=" * 60,
            "铁路调度方案比较结果",
            "=" * 60,
            "",
            f"比较准则: {result.criteria.value}",
            f"参与方案数: {len(result.results)}",
            ""
        ]
        
        if result.winner:
            winner = result.winner
            m = winner.result.metrics
            lines.extend([
                "-" * 60,
                f"最优方案: {winner.scheduler_name}",
                "-" * 60,
                f"  最大延误: {m.max_delay_seconds // 60} 分钟",
                f"  平均延误: {m.avg_delay_seconds / 60:.1f} 分钟",
                f"  受影响列车: {m.affected_trains_count} 列",
                f"  准点率: {m.on_time_rate * 100:.1f}%",
                f"  计算时间: {m.computation_time:.2f} 秒",
                ""
            ])
        
        lines.append("排名:")
        for r in sorted(result.results, key=lambda x: x.rank):
            m = r.result.metrics
            mark = " ★" if r.is_winner else ""
            lines.append(
                f"  {r.rank}. {r.scheduler_name}: "
                f"最大延误 {m.max_delay_seconds // 60}分钟, "
                f"平均延误 {m.avg_delay_seconds / 60:.1f}分钟{mark}"
            )
        
        lines.extend(["", "=" * 60])
        return "\n".join(lines)
    
    def _to_summary(self, result: MultiComparisonResult) -> str:
        """转换为摘要格式"""
        if not result.winner:
            return "无法确定最优调度方案"
        
        winner = result.winner
        m = winner.result.metrics
        
        return (
            f"推荐使用 {winner.scheduler_name} 方案。"
            f"最大延误 {m.max_delay_seconds // 60} 分钟，"
            f"平均延误 {m.avg_delay_seconds / 60:.1f} 分钟，"
            f"影响 {m.affected_trains_count} 列列车，"
            f"准点率 {m.on_time_rate * 100:.1f}%。"
        )
    
    def _to_detailed(self, result: MultiComparisonResult) -> str:
        """转换为详细格式"""
        lines = [self._to_markdown(result)]
        
        # 添加所有方案的详细指标
        lines.extend([
            "",
            "---",
            "",
            "## 📋 所有方案详细指标",
            ""
        ])
        
        for r in result.results:
            m = r.result.metrics
            lines.extend([
                f"### {r.scheduler_name} (排名: {r.rank})",
                "",
                "```json",
                json.dumps(m.to_dict(), ensure_ascii=False, indent=2),
                "```",
                ""
            ])
        
        return "\n".join(lines)
    
    def generate_llm_prompt(
        self,
        comparison_result: MultiComparisonResult,
        user_query: str,
        additional_context: Optional[str] = None
    ) -> str:
        """
        生成供大模型理解和回答的Prompt
        
        Args:
            comparison_result: 比较结果
            user_query: 用户原始查询
            additional_context: 额外上下文
        
        Returns:
            大模型Prompt
        """
        # 获取比较结果的Markdown格式
        comparison_md = self._to_markdown(comparison_result)
        
        prompt = f"""你是一个专业的铁路调度助手。用户提出了一个调度需求，系统已经比较了多种调度方案。

## 用户需求
{user_query}

## 系统比较结果
{comparison_md}

## 任务
请根据以上比较结果，用通俗易懂的语言向用户解释：
1. 推荐使用哪个调度方案，以及为什么
2. 该方案的关键指标表现
3. 如果用户有特殊关注点（如计算速度、延误最小化等），给出相应建议

请用专业但易懂的语言回答，避免过多技术术语。如果需要解释某些指标，请简要说明其含义。
"""
        
        if additional_context:
            prompt += f"\n## 额外信息\n{additional_context}\n"
        
        return prompt
    
    def generate_structured_output(
        self,
        comparison_result: MultiComparisonResult
    ) -> Dict[str, Any]:
        """
        生成结构化输出，适合API返回或进一步处理
        
        Args:
            comparison_result: 比较结果
        
        Returns:
            结构化输出字典
        """
        if not comparison_result.winner:
            return {
                "success": False,
                "message": "无法确定最优方案",
                "recommendation": None
            }
        
        winner = comparison_result.winner
        m = winner.result.metrics
        
        return {
            "success": True,
            "recommendation": {
                "scheduler_name": winner.scheduler_name,
                "scheduler_type": winner.scheduler_type.value,
                "rank": winner.rank,
                "score": winner.score,
                "key_metrics": {
                    "max_delay_minutes": m.max_delay_seconds // 60,
                    "avg_delay_minutes": round(m.avg_delay_seconds / 60, 2),
                    "affected_trains": m.affected_trains_count,
                    "on_time_rate": round(m.on_time_rate * 100, 1)
                },
                "improvement": winner.improvement_over_baseline
            },
            "all_options": [
                {
                    "name": r.scheduler_name,
                    "type": r.scheduler_type.value,
                    "rank": r.rank,
                    "max_delay_minutes": r.result.metrics.max_delay_seconds // 60,
                    "avg_delay_minutes": round(r.result.metrics.avg_delay_seconds / 60, 2),
                    "computation_time": round(r.result.metrics.computation_time, 2)
                }
                for r in comparison_result.results
            ],
            "analysis": comparison_result.recommendations
        }


def create_llm_adapter(output_format: str = "markdown") -> LLMOutputAdapter:
    """
    创建大模型输出适配器
    
    Args:
        output_format: 输出格式名称
    
    Returns:
        适配器实例
    """
    format_map = {
        "json": LLMOutputFormat.JSON,
        "markdown": LLMOutputFormat.MARKDOWN,
        "text": LLMOutputFormat.STRUCTURED_TEXT,
        "summary": LLMOutputFormat.SUMMARY,
        "detailed": LLMOutputFormat.DETAILED
    }
    
    fmt = format_map.get(output_format.lower(), LLMOutputFormat.MARKDOWN)
    return LLMOutputAdapter(output_format=fmt)


# 测试代码
if __name__ == "__main__":
    # 模拟比较结果进行测试
    from dataclasses import dataclass
    from scheduler_comparison.metrics import EvaluationMetrics
    from scheduler_comparison.comparator import ComparisonResult
    from scheduler_comparison.scheduler_interface import SchedulerType, SchedulerResult
    
    # 创建模拟指标
    metrics_a = EvaluationMetrics(
        max_delay_seconds=600,
        avg_delay_seconds=180.0,
        total_delay_seconds=1800,
        affected_trains_count=3,
        on_time_rate=0.85,
        computation_time=0.5
    )
    
    metrics_b = EvaluationMetrics(
        max_delay_seconds=480,
        avg_delay_seconds=200.0,
        total_delay_seconds=2000,
        affected_trains_count=4,
        on_time_rate=0.80,
        computation_time=2.5
    )
    
    # 创建模拟结果
    result_a = SchedulerResult(
        success=True,
        scheduler_name="FCFS调度器",
        scheduler_type=SchedulerType.FCFS,
        optimized_schedule={},
        metrics=metrics_a,
        message="执行成功"
    )
    
    result_b = SchedulerResult(
        success=True,
        scheduler_name="MIP调度器",
        scheduler_type=SchedulerType.MIP,
        optimized_schedule={},
        metrics=metrics_b,
        message="执行成功"
    )
    
    # 创建比较结果
    comparison_a = ComparisonResult(
        scheduler_name="FCFS调度器",
        scheduler_type=SchedulerType.FCFS,
        result=result_a,
        rank=1,
        score=100.0,
        is_winner=True
    )
    
    comparison_b = ComparisonResult(
        scheduler_name="MIP调度器",
        scheduler_type=SchedulerType.MIP,
        result=result_b,
        rank=2,
        score=150.0,
        is_winner=False
    )
    
    multi_result = MultiComparisonResult(
        success=True,
        criteria="balanced",
        results=[comparison_a, comparison_b],
        winner=comparison_a,
        baseline_metrics=EvaluationMetrics(
            max_delay_seconds=540,
            avg_delay_seconds=190.0
        ),
        computation_time=3.0,
        recommendations=["推荐使用FCFS调度器"]
    )
    
    # 测试各种输出格式
    adapter = LLMOutputAdapter()
    
    print("=" * 60)
    print("Markdown格式:")
    print("=" * 60)
    print(adapter._to_markdown(multi_result))
    
    print("=" * 60)
    print("摘要格式:")
    print("=" * 60)
    print(adapter._to_summary(multi_result))
