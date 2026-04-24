# -*- coding: utf-8 -*-
"""
第二层：Agent 规划层（专家级重构版）
基于 Function Calling 的 LLM Agent，具备完整的调度决策能力：

  态势感知 → 策略制定 → 求解执行 → 方案对比 → 最终决策

工具集设计（三大类、5个工具）：
  感知工具：assess_impact, get_train_status, query_timetable
  求解工具：run_solver, compare_strategies

无需微调。Agent 通过工具调用链自主完成从分析到求解的全流程。

设计原则：
  1. 求解器选择由 Agent 基于 assess_impact 数据自主决策，不做硬编码映射
  2. compare_strategies 支持多方案对比，Agent 可选最优
  3. 安全校验在工具层兜底（区间封锁→FCFS，信息不完整→FCFS）
  4. 输出格式兼容工作流引擎，agent_executed_solve=True 时跳过 L3
"""

import logging
import json
import time as _time
from typing import Dict, Any, List, Optional
from datetime import datetime

from models.workflow_models import AccidentCard
from railway_agent.adapters.llm_adapter import get_llm_caller
from railway_agent.rag_retriever import get_retriever
from config import LLMConfig

logger = logging.getLogger(__name__)


class Layer2Planner:
    """
    L2 Agent 规划层

    与旧版的核心区别：
    - 旧版：LLM 输出 JSON（solver名字）→ 手动解析 → 传给 L3
    - 新版：LLM Agent 自主调用 5 个工具，完成从分析到求解的全流程
    - 新增 assess_impact（态势感知）和 compare_strategies（方案对比）
    """

    MAX_AGENT_STEPS = 8

    def __init__(self, trains=None, stations=None):
        self.trains = trains
        self.stations = stations
        self._llm_caller = None
        self._accident_card = None
        self._tools = self._build_tools_schema()
        self._system_prompt = self._build_system_prompt()

    def _get_llm_caller(self):
        if self._llm_caller is None:
            self._llm_caller = get_llm_caller()
        return self._llm_caller

    def _ensure_data_loaded(self):
        if not self.trains or not self.stations:
            from models.data_loader import load_trains, load_stations
            self.trains = self.trains or load_trains()
            self.stations = self.stations or load_stations()

    def _retrieve_rag_knowledge(self, accident_card: AccidentCard) -> str:
        """
        从RAG知识库检索相关领域知识

        Args:
            accident_card: 事故卡片

        Returns:
            str: 格式化的知识内容，如果无结果则返回空字符串
        """
        try:
            retriever = get_retriever()

# 构建查询文本
            query_parts = []
            if accident_card.scene_category:
                query_parts.append(accident_card.scene_category)
            if accident_card.fault_type:
                query_parts.append(accident_card.fault_type)
            if accident_card.affected_section:
                query_parts.append(accident_card.affected_section)
            if accident_card.location_code:
                query_parts.append(accident_card.location_code)

            query = " ".join(query_parts) if query_parts else "铁路调度"

            # 检索知识
            documents = retriever.retrieve(query, top_k=2)

            if not documents:
                return ""

            # 格式化输出
            knowledge_parts = []
            for doc in documents:
                content = doc.get("content", "")
                key = doc.get("key", "")
                if content:
                    knowledge_parts.append(f"【{key}】\n{content}")

            return "\n\n".join(knowledge_parts)

        except Exception as e:
            logger.warning(f"[L2] RAG知识检索失败: {e}")
            return ""

    def _build_output(self, final_response: Optional[Dict], solver_results: List[Dict],
                      agent_trace: List[Dict], accident_card: AccidentCard,
                      response_source: str) -> Dict[str, Any]:
        """
        构建L2规划层的输出结果

        Args:
            final_response: LLM最终响应
            solver_results: 求解结果列表（包含 run_solver 和 compare_strategies 的执行结果）
            agent_trace: Agent执行追踪
            accident_card: 事故卡片
            response_source: 响应来源

        Returns:
            Dict: 符合工作流引擎要求的输出格式
        """
        # 判断是否执行了求解
        agent_executed_solve = len(solver_results) > 0 and any(
            r.get("success", False) for r in solver_results
        )

        # 从最终响应中提取规划意图
        planning_intent = self._extract_planning_intent(final_response, accident_card)

        # 【关键修复】提取最优求解器和配置
        # 优先从 compare_strategies 的 best_solution 中提取
        preferred_solver = "fcfs"
        solver_config = {}
        best_result = None

        # 查找 compare_strategies 结果（包含 best_solution 的）
        for sr in reversed(solver_results):
            if sr.get("best_solution"):
                best_result = sr["best_solution"]
                preferred_solver = best_result.get("solver", "fcfs")
                solver_config = {"optimization_objective": sr.get("optimization_objective", "min_total_delay")}
                break
            elif sr.get("solver") and not sr.get("strategies_tested"):
                # run_solver 的结果
                if sr.get("success"):
                    best_result = sr
                    preferred_solver = sr.get("solver", "fcfs")
                    break

        # 确定技能调度
        skill_dispatch = self._determine_skill_dispatch(solver_results, accident_card)

        # 规划决策（供 L3 使用）
        planner_decision = {
            "preferred_solver": preferred_solver,
            "solver_config": solver_config,
            "solver_results": solver_results,
            "agent_trace": agent_trace,
            "response_source": response_source,
            "agent_executed_solve": agent_executed_solve
        }

        # 构建输出
        result = {
            "success": True,
            "planning_intent": planning_intent,
            "skill_dispatch": skill_dispatch,
            "planner_decision": planner_decision,
            "agent_executed_solve": agent_executed_solve,
        }

        # 如果Agent已执行求解，添加最优求解结果
        if agent_executed_solve and best_result:
            affected = []
            opt_schedule = best_result.get("optimized_schedule", {})
            if opt_schedule and isinstance(opt_schedule, dict):
                affected = [tid for tid, stops in opt_schedule.items()
                            if isinstance(stops, list) and any(
                                s.get("delay_seconds", 0) > 0 for s in stops if isinstance(s, dict))]
            if not affected:
                affected = best_result.get("affected_trains", [])

            result["skill_execution_result"] = {
                "success": best_result.get("success", False),
                "total_delay_minutes": best_result.get("total_delay_minutes", 0),
                "max_delay_minutes": best_result.get("max_delay_minutes", 0),
                "avg_delay_minutes": best_result.get("avg_delay_minutes", 0),
                "affected_trains_count": best_result.get("affected_trains_count", 0),
                "affected_trains": affected,
                "solving_time_seconds": best_result.get("solving_time_seconds", 0),
                "solving_time": best_result.get("solving_time_seconds", 0),  # 前端兼容
                "solver": best_result.get("solver", "unknown"),
                "skill_name": best_result.get("solver", "unknown"),  # 前端兼容
                "adjustments": best_result.get("adjustments", []),
                "optimized_schedule": opt_schedule,
                "on_time_rate": best_result.get("on_time_rate", 1.0),
            }
            result["solver_response"] = best_result

        return result

    def _extract_planning_intent(self, final_response: Optional[Dict],
                                  accident_card: AccidentCard) -> str:
        """从LLM响应中提取规划意图"""
        if final_response is None:
            return f"处理{accident_card.scene_category}场景"

        try:
            # 尝试从响应内容中提取关键决策
            content = final_response.get("assistant_message", {}).get("content", "")
            if content:
                # 取前200字符作为规划意图
                return content[:200] if len(content) > 200 else content
        except Exception:
            pass

        return f"处理{accident_card.scene_category}场景"

    def _determine_skill_dispatch(self, solver_results: List[Dict],
                                   accident_card: AccidentCard) -> str:
        """确定技能调度类型"""
        if not solver_results:
            return "analyze_only"

        # 检查是否有成功的求解结果
        has_success = any(r.get("success", False) for r in solver_results)
        if has_success:
            return "execute_and_compare"

        return "analyze_only"

    def _rule_fallback(self, accident_card: AccidentCard) -> Dict[str, Any]:
        """规则回退（LLM不可用时）"""
        logger.info("[L2 Agent] 使用规则回退模式")

        # 根据场景类型选择求解器
        scene = accident_card.scene_category
        if "临时限速" in scene:
            skill_dispatch = "mip_scheduler"
        elif "突发故障" in scene:
            skill_dispatch = "fcfs_scheduler"
        elif "区间封锁" in scene:
            skill_dispatch = "noop_scheduler"
        else:
            skill_dispatch = "fcfs_scheduler"

        return {
            "success": True,
            "planning_intent": f"规则模式：选择{skill_dispatch}进行求解",
            "skill_dispatch": skill_dispatch,
            "planner_decision": {
                "mode": "rule_fallback",
                "agent_executed_solve": False
            },
            "agent_executed_solve": False,
        }

    # ================================================================
    # 对外接口
    # ================================================================

    def execute(self, accident_card: AccidentCard, enable_rag: bool = True, previous_feedback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        logger.info("[L2 Agent] 启动专家级 Agent 规划")
        self._accident_card = accident_card
        self._ensure_data_loaded()

        # 构建初始消息
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": self._build_scenario_text(accident_card)}
        ]

        # 【反射重规划】如果提供了前一轮的反馈，告知Agent需要调整策略
        if previous_feedback:
            reason = previous_feedback.get("rollback_reason", "")
            fixes = previous_feedback.get("suggested_fixes", [])
            fix_text = "\n".join(f"- {f}" for f in fixes) if fixes else ""
            reflection_msg = (
                f"【系统反馈：前一轮方案未通过评估，需要重新规划】\n"
                f"未通过原因：{reason}\n"
                f"改进建议：\n{fix_text}\n\n"
                f"请根据以上反馈调整你的策略选择，尝试使用不同的求解器或调整参数，生成更优方案。"
            )
            messages.append({"role": "user", "content": reflection_msg})
            logger.info(f"[L2 Agent] 接收反射反馈，原因: {reason}")

        # RAG 增强
        if enable_rag:
            rag_content = self._retrieve_rag_knowledge(accident_card)
            if rag_content:
                messages.append({
                    "role": "user",
                    "content": f"【调度知识参考】\n{rag_content}\n\n请结合以上知识做决策。"
                })

        # Agent 循环
        agent_trace = []
        solver_results = []
        final_response = None
        response_source = "Agent模式"

        try:
            llm = self._get_llm_caller()
        except Exception as e:
            logger.warning(f"[L2 Agent] LLM不可用，使用规则回退: {e}")
            return self._rule_fallback(accident_card)

        for step in range(self.MAX_AGENT_STEPS):
            try:
                response = llm.call_with_tools(
                    messages=messages,
                    tools=self._tools,
                    max_tokens=1024,
                    temperature=0.2
                )
            except Exception as e:
                logger.error(f"[L2 Agent] 第{step+1}步 LLM调用失败: {e}")
                if LLMConfig.FORCE_LLM_MODE:
                    raise RuntimeError(f"[L2 Agent] LLM调用失败: {e}") from e
                break

            messages.append(response["assistant_message"])
            final_response = response

            if not response["tool_calls"]:
                logger.info(f"[L2 Agent] 完成，共 {step+1} 步")
                break

            for tc in response["tool_calls"]:
                tool_name = tc["name"]
                try:
                    args = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
                except json.JSONDecodeError:
                    args = {}

                try:
                    result = self._execute_tool(tool_name, args)
                    success = result.get("success", True)
                except Exception as e:
                    logger.error(f"[L2 Agent] 工具异常 {tool_name}: {e}")
                    result = {"success": False, "error": str(e)}
                    success = False

                # 追踪所有求解结果
                if tool_name == "run_solver":
                    solver_results.append(result)
                elif tool_name == "compare_strategies" and result.get("best_solution"):
                    solver_results.append(result["best_solution"])
                    # 也记录对比中的其他方案
                    for r in result.get("results", []):
                        if r.get("success") and r != result.get("best_solution"):
                            solver_results.append(r)

                agent_trace.append({
                    "step": step + 1,
                    "tool": tool_name,
                    "arguments": args,
                    "success": success
                })

                # 【修复】截断tool result防止LLM输入超限（>200KB）
                truncated_result = self._truncate_tool_result_for_llm(result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(truncated_result, ensure_ascii=False, default=str)
                })

        return self._build_output(final_response, solver_results, agent_trace, accident_card, response_source)

    # ================================================================
    # 工具定义（Function Calling Schema）
    # ================================================================

    def _build_tools_schema(self) -> List[Dict]:
        return [
            # ---- 感知工具 ----
            {
                "type": "function",
                "function": {
                    "name": "assess_impact",
                    "description": (
                        "评估事故的全局影响。分析直接影响列车数、延误传播风险、"
                        "即将到达的列车数，返回量化的紧急程度和策略建议。"
                        "建议在决策前首先调用此工具获取数据支撑。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_train_status",
                    "description": "查询指定列车的运行状态、停站信息和时刻表。用于了解受影响列车的详细运行计划。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "train_id": {
                                "type": "string",
                                "description": "列车号，如 G1563、D1234"
                            }
                        },
                        "required": ["train_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "query_timetable",
                    "description": "查询车站的时刻表，了解当前线路列车运行密度。用于判断高峰/平峰时段。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "station_code": {
                                "type": "string",
                                "description": "车站代码，如 SJP、BDD"
                            }
                        },
                        "required": ["station_code"]
                    }
                }
            },
            # ---- 求解工具 ----
            {
                "type": "function",
                "function": {
                    "name": "run_solver",
                    "description": (
                        "执行单个求解器进行调度优化。可精确控制求解器类型、优化目标和参数。"
                        "MIP适合小规模非紧急场景（全局最优但慢），FCFS适合紧急响应（秒级）。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "solver": {
                                "type": "string",
                                "enum": ["mip", "fcfs", "max_delay_first", "hierarchical"],
                                "description": "求解器类型：mip(混合整数规划全局最优), fcfs(先到先服务快速), max_delay_first(延误优先), hierarchical(分层求解-自动选择)"
                            },
                            "optimization_objective": {
                                "type": "string",
                                "enum": ["min_max_delay", "min_total_delay", "min_avg_delay"],
                                "description": "优化目标（仅MIP生效）：min_max_delay=最小化最大延误(默认), min_total_delay=最小化总延误"
                            },
                            "time_limit": {
                                "type": "integer",
                                "description": "MIP求解时间上限（秒），范围30-600，默认120"
                            },
                            "optimality_gap": {
                                "type": "number",
                                "description": "MIP最优性间隙，范围0.01-0.1，默认0.05"
                            }
                        },
                        "required": ["solver"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "compare_strategies",
                    "description": (
                        "基于场景特征和优化目标，通过规则推荐最优求解器及参数配置。"
                        "不实际执行求解器，只做智能推荐，将推荐结果供下游调度引擎执行。"
                        "适用于需要快速确定求解策略的场景。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "strategies": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "要对比的求解器列表，如 ['fcfs', 'mip', 'max_delay_first']。不传则自动选择。"
                            },
                            "optimization_objective": {
                                "type": "string",
                                "enum": ["min_max_delay", "min_total_delay", "min_avg_delay"],
                                "description": "优化目标"
                            },
                            "time_budget": {
                                "type": "integer",
                                "description": "对比总时间预算（秒），默认300秒"
                            }
                        },
                        "required": []
                    }
                }
            }
        ]

    # ================================================================
    # System Prompt（专家级调度知识）
    # ================================================================

    def _build_system_prompt(self) -> str:
        return """你是一名中国高铁调度智能体（Agent）。你具备从态势感知到方案执行的完整调度决策能力。

## 工作流程

**第一步：态势感知** — 调用 assess_impact 获取事故影响的量化分析
**第二步：信息补充** — 如需了解更多细节，调用 get_train_status 或 query_timetable
**第三步：策略执行** — 调用 run_solver 或 compare_strategies 执行求解
**第四步：审查结果** — 检查求解结果，不满意可换策略重试
**第五步：总结** — 用文字说明你的决策过程、选择的理由、最终结果

## 求解器特点（基于真实高铁调度场景）

| 求解器 | 耗时 | 核心特点 | 最佳场景 |
|--------|------|---------|---------|
| **mip** | 30-300秒 | 混合整数规划(PuLP/CBC)，9类约束，全局最优 | 列车≤10列，非紧急，有时间等求解 |
| **fcfs** | <1秒 | 先到先服务+停站/运行冗余恢复，延误自然传播 | 紧急响应，大规模(>10列)，信息不完整 |
| **max_delay_first** | <1秒 | 迭代压缩延误最大列车的停站时间 | 多列车不同程度延误，需优先减少最大延误 |
| **hierarchical** | 1-60秒 | 分层求解(FCFS+MIP联动)，自动判断是否需要MIP | 通用场景，推荐作为默认选择，解决MIP规模问题 |

## 决策建议（基于 assess_impact 返回的 urgency）

- **urgency=low**（影响≤3列，延误≤15分）→ hierarchical（自动选择最优策略）
- **urgency=medium**（影响≤8列，延误≤30分）→ hierarchical（自动FCFS/MIP联动）
- **urgency=high**（影响≤15列）→ hierarchical 或 fcfs（保证响应速度）
- **urgency=critical**（影响>15列或延误>60分）→ fcfs（保障安全为第一要务）

## 优化目标选择（默认：min_total_delay）

你必须根据用户query自动识别优化意图，选择对应的 optimization_objective：

- **min_total_delay**（默认）：最小化总延误，整体系统效率最优。当用户没有明确指定优化目标时使用此默认。
- **min_max_delay**：最小化最大延误，关注极端延误列车。当用户query中出现"最大延误""最严重""重点列车""关键列车"等关键词时选择。
- **min_avg_delay**：最小化平均延误，关注整体服务水平。当用户query中出现"平均延误""整体服务水平""大多数列车"等关键词时选择。

**自动识别规则**：
1. 用户query包含"最大""最坏""极端""重点保障" → min_max_delay
2. 用户query包含"平均""整体""大多数""普遍" → min_avg_delay
3. 用户query未明确提及或无明显偏向 → min_total_delay（默认）

**重要**：compare_strategies 和 run_solver 调用时，必须显式传入识别到的 optimization_objective 参数。

## MIP 参数调优

- **time_limit**：紧急60秒，一般120秒，充裕300秒
- **optimality_gap**：快速0.1，平衡0.05，高精度0.01（但耗时长）

## 安全约束（工具层强制执行，不可违反）

1. 区间封锁 → 强制 FCFS
2. 信息不完整 → 强制 FCFS
3. MIP time_limit 范围 30-600秒
4. MIP optimality_gap 范围 0.01-0.1

## 输出要求

最终回复应包含：
- 选择的求解器和理由
- 如果做了对比，说明各方案的结果对比
- 最终求解结果（总延误、最大延误、求解耗时）
- 对方案的评价"""

    # ================================================================
    # 场景描述
    # ================================================================

    def _build_scenario_text(self, card: AccidentCard) -> str:
        lines = ["【事故场景信息】"]
        lines.append(f"- 场景类型: {card.scene_category}")

        if card.fault_type and card.fault_type != "未知":
            lines.append(f"- 故障类型: {card.fault_type}")

        loc = card.location_name or card.location_code or "未知位置"
        loc_type = "区间" if card.location_type == "section" else "车站"
        lines.append(f"- 事故位置: {loc}（{loc_type}）")

        n = len(card.affected_train_ids) if card.affected_train_ids else 0
        lines.append(f"- 受影响列车数: {n}列")
        if card.affected_train_ids:
            lines.append(f"- 受影响车次: {', '.join(card.affected_train_ids[:15])}")

        d = card.expected_duration
        if d:
            lines.append(f"- 预计延误: {d}分钟")
            if d <= 10:
                lines.append("- 延误等级: 轻微（≤10分钟）")
            elif d <= 30:
                lines.append("- 延误等级: 一般（10-30分钟）")
            elif d <= 60:
                lines.append("- 延误等级: 较大（30-60分钟），需优先处理")
            else:
                lines.append("- 延误等级: 严重（>60分钟），需立即响应")

        lines.append(f"- 信息完整性: {'完整' if card.is_complete else '不完整'}")
        if card.missing_fields:
            lines.append(f"- 缺失信息: {', '.join(card.missing_fields)}")

        h = datetime.now().hour
        if 0 <= h < 6:
            lines.append("- 当前时段: 天窗期（0:00-6:00），列车稀疏")
        elif 6 <= h < 9:
            lines.append("- 当前时段: 早高峰前（6:00-9:00），密度逐步增加")
        elif 9 <= h < 14:
            lines.append("- 当前时段: 日间运营（9:00-14:00），密度较高")
        elif 14 <= h < 18:
            lines.append("- 当前时段: 下午运营（14:00-18:00），全天密度最高")
        elif 18 <= h < 22:
            lines.append("- 当前时段: 晚间运营（18:00-22:00），密度逐步下降")
        else:
            lines.append("- 当前时段: 深夜（22:00-24:00），即将进入天窗期")

        if card.scene_category == "区间封锁":
            lines.append("- 安全约束: 区间封锁，系统将强制使用FCFS求解器")
        elif card.scene_category == "临时限速":
            lines.append("- 特殊约束: 区间限速，列车运行时间增加，延误可能传播")

        lines.append("")
        lines.append("请先调用 assess_impact 评估影响，再制定调度策略。")

        return "\n".join(lines)

    # ================================================================
    # Tool Result 截断（防止LLM输入超限 >200KB）
    # ================================================================

    def _truncate_tool_result_for_llm(self, result: Dict) -> Dict:
        """
        截断tool result中的大型字段，防止LLM输入长度超限。
        保留metrics摘要，移除完整时刻表数据。
        """
        import copy
        truncated = copy.deepcopy(result)

        # 截断 run_solver / compare_strategies 中的完整时刻表
        if "optimized_schedule" in truncated:
            schedule = truncated["optimized_schedule"]
            if isinstance(schedule, dict) and schedule:
                # 只保留前3列受影响列车的摘要
                summary = {}
                count = 0
                for tid, stops in schedule.items():
                    delays = [s.get("delay_seconds", 0) for s in stops if isinstance(s, dict)]
                    max_d = max(delays) if delays else 0
                    if max_d > 0 and count < 3:
                        summary[tid] = f"max_delay={max_d}s, {len(stops)} stations"
                        count += 1
                truncated["optimized_schedule"] = summary if summary else f"{len(schedule)} trains (truncated)"
            else:
                truncated["optimized_schedule"] = "(truncated)"

        # 截断 compare_strategies 中的 results 列表
        if "results" in truncated and isinstance(truncated["results"], list):
            for r in truncated["results"]:
                if isinstance(r, dict) and "optimized_schedule" in r:
                    r["optimized_schedule"] = "(truncated)"

        # 截断 best_solution
        if "best_solution" in truncated and isinstance(truncated["best_solution"], dict):
            if "optimized_schedule" in truncated["best_solution"]:
                truncated["best_solution"]["optimized_schedule"] = "(truncated)"

        return truncated

    # ================================================================
    # 工具执行分发
    # ================================================================

    def _execute_tool(self, name: str, args: Dict) -> Dict:
        dispatch = {
            "assess_impact": self._tool_assess_impact,
            "get_train_status": self._tool_get_train_status,
            "query_timetable": self._tool_query_timetable,
            "run_solver": self._tool_run_solver,
            "compare_strategies": self._tool_compare_strategies,
        }
        handler = dispatch.get(name)
        if handler:
            return handler(args)
        return {"success": False, "error": f"未知工具: {name}"}

    # ================================================================
    # 感知工具
    # ================================================================

    def _tool_assess_impact(self, args: Dict) -> Dict:
        """
        事故态势感知（返回定性事实，由 Agent 自主推理决策）

        设计原则：
        - 返回客观事实和数据，不做决策建议
        - urgency 等级作为参考指标保留，但不绑定策略
        - 新增 affected_trains_detail 提供 Agent 推理所需的列车信息
        """
        card = self._accident_card
        affected = card.affected_train_ids or []
        location = card.location_code or ""
        delay = card.expected_duration or 10
        direct_count = len(affected)

        # 统计即将到达事故地点的列车
        approaching = 0
        nearby_trains = []
        if location and self.trains:
            for train in self.trains:
                # 获取列车ID（支持对象和字典两种格式）
                train_id = getattr(train, 'train_id', None) if hasattr(train, 'train_id') else train.get('train_id')
                if not train_id:
                    continue

                if train_id in affected:
                    continue

                # 获取schedule（支持对象和字典两种格式）
                schedule = getattr(train, 'schedule', None) if hasattr(train, 'schedule') else train.get('schedule')
                if schedule:
                    stops = getattr(schedule, 'stops', None) if hasattr(schedule, 'stops') else schedule.get('stops')
                    if stops:
                        for stop in stops:
                            # 获取站点代码
                            station_code = getattr(stop, 'station_code', None) if hasattr(stop, 'station_code') else stop.get('station_code')
                            if station_code == location:
                                approaching += 1
                                nearby_trains.append(train_id)
                                break

        total = direct_count + approaching

        # 时段判断
        hour = datetime.now().hour
        is_peak = 9 <= hour <= 18
        is_window = 0 <= hour < 6

        # 参考性紧急程度（仅作为数据维度之一，不做决策绑定）
        if is_window:
            urgency = "low"
        elif total <= 3 and delay <= 15:
            urgency = "low"
        elif total <= 8 and delay <= 30:
            urgency = "medium"
        elif total <= 15 and delay <= 60:
            urgency = "high"
        else:
            urgency = "critical"
        if is_peak and urgency in ("low", "medium") and total > 5:
            urgency = "high"

        # 受影响列车详情（供 Agent 推理使用）
        affected_detail = []
        for tid in affected[:10]:
            for t in (self.trains or []):
                # 获取列车ID（支持对象和字典两种格式）
                t_id = getattr(t, 'train_id', None) if hasattr(t, 'train_id') else t.get('train_id')
                if t_id == tid:
                    train_type = getattr(t, 'train_type', '未知') if hasattr(t, 'train_type') else t.get('train_type', '未知')
                    affected_detail.append({
                        "train_id": tid,
                        "train_type": train_type
                    })
                    break

        return {
            "success": True,
            "directly_affected": direct_count,
            "approaching_trains": approaching,
            "nearby_train_ids": nearby_trains[:10],
            "total_potentially_affected": total,
            "base_delay_minutes": delay,
            "is_peak_hours": is_peak,
            "is_window_period": is_window,
            "urgency_reference": urgency,
            "affected_trains_detail": affected_detail,
            "scene_category": card.scene_category,
            "location_type": card.location_type,
            "is_complete": card.is_complete
        }

    def _tool_get_train_status(self, args: Dict) -> Dict:
        train_id = args.get("train_id", "")
        if not train_id:
            return {"success": False, "error": "缺少参数 train_id"}

        for t in (self.trains or []):
            if hasattr(t, 'train_id') and t.train_id == train_id:
                info = {"train_id": t.train_id, "train_type": getattr(t, 'train_type', '未知')}
                if hasattr(t, 'schedule') and hasattr(t.schedule, 'stops'):
                    stops = t.schedule.stops
                    if isinstance(stops, (list, tuple)):
                        info["total_stops"] = len(stops)
                        info["stops"] = [
                            {
                                "station_code": s.station_code,
                                "station_name": s.station_name,
                                "arrival_time": s.arrival_time,
                                "departure_time": s.departure_time,
                                "is_stopped": s.is_stopped
                            }
                            for s in stops[:8]
                        ]
                return {"success": True, "data": info}
        return {"success": False, "error": f"未找到列车 {train_id}"}

    def _tool_query_timetable(self, args: Dict) -> Dict:
        station_code = args.get("station_code", "")
        if not station_code:
            return {"success": False, "error": "缺少参数 station_code"}

        trains_at = []
        if self.trains:
            for train in self.trains:
                if hasattr(train, 'schedule') and hasattr(train.schedule, 'stops'):
                    for stop in train.schedule.stops:
                        if stop.station_code == station_code:
                            trains_at.append({
                                "train_id": train.train_id,
                                "train_type": getattr(train, 'train_type', '未知'),
                                "arrival_time": stop.arrival_time,
                                "departure_time": stop.departure_time,
                                "is_stopped": stop.is_stopped
                            })
                            break

        station_name = station_code
        for s in (self.stations or []):
            if hasattr(s, 'station_code') and s.station_code == station_code:
                station_name = s.station_name
                break

        return {
            "success": True,
            "data": {
                "station_code": station_code,
                "station_name": station_name,
                "total_trains": len(trains_at),
                "is_dense": len(trains_at) > 15,
                "trains": trains_at[:30]
            }
        }

    # ================================================================
    # 求解工具
    # ================================================================

    def _tool_run_solver(self, args: Dict) -> Dict:
        """执行单个求解器（参数化）"""
        solver_name = args.get("solver", "fcfs")
        objective = args.get("optimization_objective", "min_total_delay")  # 【专家优化】默认优化总延误
        time_limit = args.get("time_limit", 120)
        gap = args.get("optimality_gap", 0.05)

        # 安全校验
        time_limit = max(30, min(600, int(time_limit)))
        gap = max(0.01, min(0.1, round(float(gap), 2)))
        if objective not in ["min_max_delay", "min_total_delay", "min_avg_delay"]:
            objective = "min_max_delay"

        return self._execute_single_solver(solver_name, objective, time_limit, gap)

    def _tool_compare_strategies(self, args: Dict) -> Dict:
        """
        【智能对比】根据优化目标和问题规模动态选择并执行多个求解器

        Agent 自主决策体现：
        1. 根据优化目标（min_max_delay/min_total_delay/min_avg_delay）动态调整权重
        2. 根据问题规模智能选择对比策略
        3. 实际执行多个求解器，综合评分并选择最优方案
        4. 返回最优结果供 Agent 决策
        """
        card = self._accident_card
        strategies = args.get("strategies")
        objective = args.get("optimization_objective", "min_total_delay")
        time_budget = args.get("time_budget", 300)

        # === 智能决策：根据优化目标和问题规模选择对比策略 ===
        affected_count = len(card.affected_train_ids or [])
        expected_delay = card.expected_duration or 10
        is_large_scale = affected_count > 10 or expected_delay > 30
        is_emergency = expected_delay > 60 or card.scene_category == "区间封锁"

        if strategies is None:
            if card.scene_category == "区间封锁" or is_emergency:
                strategies = ["fcfs"]
                logger.info(f"[智能对比] 区间封锁/紧急情况 → 仅FCFS（安全约束）")
            elif objective == "min_max_delay":
                if is_large_scale:
                    strategies = ["max_delay_first", "hierarchical", "fcfs"]
                    logger.info(f"[智能对比] 大规模+min_max_delay → max_delay_first + hierarchical + fcfs")
                else:
                    strategies = ["max_delay_first", "mip", "fcfs"]
                    logger.info(f"[智能对比] 小规模+min_max_delay → max_delay_first + MIP + fcfs")
            elif objective == "min_total_delay" or objective == "min_avg_delay":
                if is_large_scale:
                    strategies = ["hierarchical", "mip", "fcfs"]
                    logger.info(f"[智能对比] 大规模+min_avg/total_delay → hierarchical + MIP + fcfs")
                else:
                    strategies = ["mip", "hierarchical", "fcfs"]
                    logger.info(f"[智能对比] 小规模+min_avg/total_delay → MIP + hierarchical + fcfs")
            else:
                if is_large_scale:
                    strategies = ["hierarchical", "mip", "fcfs"]
                    logger.info(f"[智能对比] 默认+大规模 → hierarchical + MIP + fcfs")
                else:
                    strategies = ["mip", "hierarchical", "fcfs"]
                    logger.info(f"[智能对比] 默认+小规模 → MIP + hierarchical + fcfs")

        # === 执行求解器并收集结果 ===
        results = []
        start = _time.time()

        for solver_name in strategies:
            if _time.time() - start > time_budget:
                results.append({
                    "solver": solver_name,
                    "success": False,
                    "error": f"超过时间预算 {time_budget}秒，跳过"
                })
                logger.warning(f"[智能对比] 求解器 {solver_name} 超时")
                continue

            # 根据求解器类型动态调整参数
            if solver_name == "mip":
                tl = 60
                if objective == "min_max_delay":
                    gap = 0.05
                else:
                    gap = 0.1
                logger.info(f"[智能对比参数] {solver_name}: time_limit={tl}s, gap={gap}, objective={objective}")
            elif solver_name == "hierarchical":
                tl = 60
                gap = None
                logger.info(f"[智能对比参数] {solver_name}: time_limit={tl}s, objective={objective}")
            elif solver_name == "max_delay_first":
                tl = None
                gap = None
                logger.info(f"[智能对比参数] {solver_name}: 无时间限制, objective={objective}")
            else:  # fcfs
                tl = None
                gap = None
                logger.info(f"[智能对比参数] {solver_name}: 无时间限制, objective={objective}")

            try:
                result = self._execute_single_solver(solver_name, objective, tl, gap)
                results.append(result)
                logger.info(f"[智能对比结果] {solver_name}: 总延误={result.get('total_delay_minutes')}分, "
                           f"最大延误={result.get('max_delay_minutes')}分, "
                           f"晚点列车平均延误={result.get('avg_delay_minutes')}分/{result.get('affected_trains_count', 0)}列, "
                           f"耗时={result.get('solving_time_seconds')}秒")
            except Exception as e:
                logger.error(f"[智能对比异常] {solver_name}: {e}")
                results.append({"solver": solver_name, "success": False, "error": str(e)})

        # === 根据优化目标动态计算综合得分 ===
        successful = [r for r in results if r.get("success")]

        if not successful:
            return {
                "success": True,
                "strategies_tested": len(results),
                "results": results,
                "best_solution": None,
                "best_solver": None,
                "comparison_summary": " | ".join([f"{r['solver']}: 失败({r.get('error', '未知')})" for r in results]),
                "optimization_objective": objective,
                "reasoning": "所有求解器执行失败"
            }

        for r in successful:
            max_delay = r.get("max_delay_minutes", 0)
            avg_delay = r.get("avg_delay_minutes", 0)
            total_delay = r.get("total_delay_minutes", 0)
            affected_count = r.get("affected_trains_count", 0)
            on_time_rate = r.get("on_time_rate", 1.0)

            # 归一化得分（0-100，越低越好），阈值与 comparator.py 对齐
            max_delay_score = min(max_delay / 30 * 100, 100)
            avg_delay_score = min(avg_delay / 30 * 100, 100)
            total_delay_score = min(total_delay / 120 * 100, 100)
            affected_score = min(affected_count / 10 * 100, 100)
            on_time_score = (1 - on_time_rate) * 100

            if objective == "min_max_delay":
                composite_score = (
                    max_delay_score * 0.40 +
                    total_delay_score * 0.15 +
                    avg_delay_score * 0.10 +
                    affected_score * 0.15 +
                    on_time_score * 0.20
                )
            elif objective == "min_avg_delay":
                composite_score = (
                    avg_delay_score * 0.35 +
                    total_delay_score * 0.20 +
                    max_delay_score * 0.10 +
                    affected_score * 0.15 +
                    on_time_score * 0.20
                )
            else:  # min_total_delay 或默认
                composite_score = (
                    total_delay_score * 0.35 +
                    max_delay_score * 0.15 +
                    avg_delay_score * 0.10 +
                    affected_score * 0.15 +
                    on_time_score * 0.25
                )

            r["composite_score"] = round(composite_score, 2)

        # 按综合得分排序（越低越好）
        successful.sort(key=lambda r: r.get("composite_score", 9999))
        best = successful[0] if successful else None

        # 构建对比摘要
        summary_parts = []
        for r in successful:
            summary_parts.append(
                f"{r['solver']}: 最大延误{r.get('max_delay_minutes', 0):.2f}分, "
                f"晚点列车平均{r.get('avg_delay_minutes', 0):.2f}分/{r.get('affected_trains_count', 0)}列, "
                f"总延误{r.get('total_delay_minutes', 0):.2f}分, "
                f"受影响{r.get('affected_trains_count', 0)}列, "
                f"耗时{r.get('solving_time_seconds', 0):.2f}秒, "
                f"得分={r.get('composite_score', 0):.2f}"
            )

        for r in results:
            if not r.get("success"):
                summary_parts.append(f"{r['solver']}: 失败({r.get('error', '未知')})")

        if best:
            logger.info(f"[智能对比结论] 最优方案: {best['solver']} (综合得分={best.get('composite_score', 0):.1f}, "
                       f"优化目标={objective})")

        return {
            "success": True,
            "strategies_tested": len(results),
            "results": results,
            "best_solution": best,
            "best_solver": best["solver"] if best else None,
            "comparison_summary": " | ".join(summary_parts),
            "optimization_objective": objective,
            "reasoning": f"根据优化目标'{objective}'对比{len(strategies)}个策略，{best['solver'] if best else '无'}最优"
        }

    def _execute_single_solver(self, solver_name: str, objective: str = "min_total_delay",
                         time_limit: int = 120, gap: float = 0.05) -> Dict:
        """
        执行单个求解器，返回标准化结果（使用 Scheduler 系统）
        
        Args:
            solver_name: 求解器名称
            objective: 优化目标
            time_limit: MIP时间限制
            gap: MIP最优性间隙
        
Returns:
            Dict: 标准化的求解结果
        """
        from scheduler_comparison.scheduler_interface import SchedulerRegistry
        from models.data_loader import get_trains_pydantic, get_stations_pydantic
        from models.data_models import DelayInjection, InjectedDelay, DelayLocation, ScenarioType

        # 加载完整数据 - 使用 Pydantic 格式（调度器需要）
        trains = get_trains_pydantic()
        stations = get_stations_pydantic()

        # 构建DelayInjection
        card = self._accident_card
        location_code = card.location_code or ""
        delay_seconds = int(card.expected_duration * 60) if card.expected_duration else 600

        # 构建注入的延误列表 - 支持字典和Pydantic模型两种格式
        injected_delays = []
        affected_train_ids = card.affected_train_ids or []

        for train_id in affected_train_ids:
            # 确定延误位置
            loc_type = card.location_type or "station"
            station = location_code

            injected_delays.append(InjectedDelay(
                train_id=train_id,
                location=DelayLocation(
                    location_type=loc_type,
                    station_code=station
                ),
                initial_delay_seconds=delay_seconds,
                timestamp=datetime.now().isoformat()
))

        delay_injection = DelayInjection(
            scenario_type=card.scene_type,
            scenario_id=card.scene_id,
            injected_delays=injected_delays,
            affected_trains=card.affected_train_ids or []
        )

        # 通过SchedulerRegistry获取求解器
        try:
            result_scheduler = SchedulerRegistry.create(
                solver_name, trains, stations,
                time_limit=time_limit,
                optimality_gap=gap
            )
        except Exception as e:
            logger.error(f"[L2 Agent] 创建求解器 {solver_name} 失败: {e}")
            return {
                "solver": solver_name,
                "success": False,
                "error": f"创建求解器失败: {str(e)}",
                "total_delay_minutes": 0,
                "max_delay_minutes": 0,
                "avg_delay_minutes": 0,
                "solving_time_seconds": 0,
                "affected_trains_count": 0
            }
        
        # 执行求解
        try:
            result = result_scheduler.solve(delay_injection, objective)
        except Exception as e:
            logger.error(f"[L2 Agent] {solver_name} 求解异常: {e}")
            return {
                "solver": solver_name,
                "success": False,
                "error": f"求解异常: {str(e)}",
                "total_delay_minutes": 0,
                "max_delay_minutes": 0,
                "avg_delay_minutes": 0,
                "solving_time_seconds": 0,
                "affected_trains_count": 0
            }
        
# 转换结果格式
        if result.success:
            metrics = result.metrics
            total_s = metrics.total_delay_seconds
            max_s = metrics.max_delay_seconds
            avg_s = metrics.avg_delay_seconds
            comp_t = metrics.computation_time

            logger.debug(
                f"[L2 Agent] {solver_name} 完成: "
                f"成功={result.success}, "
                f"总延误={total_s//60}分钟, "
                f"最大延误={max_s//60}分钟"
            )

            return {
                "solver": solver_name,
                "success": True,
                "total_delay_minutes": round(total_s / 60, 2),
                "max_delay_minutes": round(max_s / 60, 2) if max_s else 0,
                "avg_delay_minutes": round(avg_s / 60, 2) if avg_s else 0,
                "solving_time_seconds": round(comp_t, 2),
                "affected_trains_count": metrics.affected_trains_count,
                "on_time_rate": metrics.on_time_rate,
                "optimized_schedule": result.optimized_schedule
            }
        else:
            logger.debug(f"[L2 Agent] {solver_name} 完成: 成功={result.success}")
            return {
                "solver": solver_name,
                "success": False,
                "error": result.message,
                "total_delay_minutes": 0,
                "max_delay_minutes": 0,
                "avg_delay_minutes": 0,
                "solving_time_seconds": 0,
                "affected_trains_count": 0
            }

    # ================================================================
    # 求解引擎（共享逻辑）
    # ================================================================
