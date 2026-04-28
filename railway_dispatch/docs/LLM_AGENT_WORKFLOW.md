# LLM-TTRA：LLM-Agent工作流设计

**文档版本**：2.0  
**日期**：2026-04-27  
**用途**：四层LLM-Agent工作流（L1-L4）、自适应反射调度编排器（ARDO）及Function Calling L2规划器的详细设计文档。目标读者：AI+OR混合系统审稿人。

---

## 1. 设计理念

核心设计问题是：*LLM应该做什么？经典算法应该做什么？*

我们的答案，源自系统迭代版本（v1.0至v8.1）的演进：

| 任务 | 分配对象 | 理由 | 实现类型 |
|------|---------|------|----------|
| 自然语言理解 | LLM（L1） | LLM擅长实体提取、消歧及处理口语化调度员语言 | **LLM驱动** |
| 策略性求解器选择 | LLM（L2） | 需要结合场景类型、紧急程度、时段、规模进行上下文推理 | **LLM驱动** |
| 数值优化 | OR求解器（L3） | MIP/FCFS保证约束满足；LLM不擅长精确数值优化 | **数学计算** |
| 解释与报告 | LLM（L4） | 需要将指标综合为自然语言叙述和可执行指令 | **LLM驱动** |
| 安全关键决策 | 规则引擎（PolicyEngine） | 硬约束（区间封锁→FCFS）不能依赖LLM可靠性 | **规则安全层** |
| 指标统计 | 规则计算 | 准点率、延误分级等基于阈值的数学统计 | **规则计算** |

这一划分遵循 **"策略交给认知，计算交给数字"** 原则。

**实验阶段原则**：所有认知决策（意图理解、实体提取、求解器选择、方案评估）均由LLM完成；规则仅用于数学计算（指标统计、Pareto分析）和安全兜底（策略引擎、约束验证）。

---

## 2. 工作流概览

### 2.1 完整数据流图

```
用户输入（自然语言）
    |
    v
+-------------------------------------------+
|  IntentRouter                             |
|  【LLM主 + 规则次】                       |
|  classify() --> LLM意图分类               |
|  classify_with_fallback() --> 规则兜底    |
+-------------------------------------------+
    |
    +-- query / chat / overview --> Light Mode (Function Calling)
    |
    +-- dispatch --> Heavy Mode (L1-L4完整工作流)
                          |
                          v
+-------------------------------------------+
|  L1: 数据建模层                           |
|  【LLM主 + 规则次】                       |
|  LLM提取 --> AccidentCard                 |
|  _fallback_extraction() --> 规则兜底      |
+-------------------------------------------+
    |
    v
+-------------------------------------------+
|  SB: 快照构建器                           |
|  【规则裁剪】                             |
|  时空窗口裁剪，147列 -> 候选集            |
+-------------------------------------------+
    |
    v
+-------------------------------------------+
|  L2: 策略规划层                           |
|  【LLM主 + 规则次】                       |
|  ReAct Agent + Function Calling           |
|  8工具：assess_impact / run_solver / ...  |
|  _rule_fallback() --> SolverSelector规则 |
+-------------------------------------------+
    |
    v
+-------------------------------------------+
|  L3: 求解执行层                           |
|  【数学计算 + 安全规则】                    |
|  SchedulerRegistry.create(solver_name)    |
|  安全约束：区间封锁强制FCFS               |
+-------------------------------------------+
    |
    v
+-------------------------------------------+
|  L4: 评估与方案生成层                     |
|  【LLM主 + 规则次】                       |
|  LLM综合评估 + 自然语言方案               |
|  _calculate_high_speed_metrics() 规则统计 |
+-------------------------------------------+
    |
    v
+-------------------------------------------+
|  PolicyEngine                             |
|  【规则安全层】                           |
|  基于阈值的 ACCEPT / FALLBACK / RERUN     |
|  LLM无权覆盖                              |
+-------------------------------------------+
    |
    +--[RERUN]--+ 反射循环(最多3轮) --> 回到 L2
    |
    +--[ACCEPT]--> WorkflowResult
```

### 2.2 反射循环（Reflection Loop）详细流程

```
迭代 i (i = 1, 2, 3)
    |
    v
L2.plan(previous_feedback=feedback_{i-1})
    |   【LLM驱动】Agent根据前一轮反馈调整策略
    |   若 feedback 存在，messages 追加系统反馈
    v
L3.execute(planner_decision)
    |   【数学计算】执行选定求解器
    v
L4.evaluate(solver_result)
    |   【LLM + 规则】LLM综合评估 + 规则指标计算
    v
PolicyEngine.make_decision(metrics)
    |   【规则安全层】阈值判定
    v
    +-- decision == RERUN 且 i < 3 --
    |       |
    |       v
    |   feedback_i = {
    |       "rollback_reason": reason,
    |       "suggested_fixes": fixes + policy_fixes,  // Fix 5: Policy反馈合并
    |       "iteration": i,
    |       "policy_override": True
    |   }
    |       |
    |       +--> 进入迭代 i+1
    |
    +-- decision != RERUN 或 i == 3 --
            |
            v
    记录 iteration_results[i]
            |
            v
    _select_best_iteration(iteration_results)
            |   【规则计算】按优化目标动态加权评分
            v
    WorkflowResult
```

---

## 3. L1：数据建模层

### 3.1 目的

将非结构化调度员输入转换为结构化`AccidentCard`。

### 3.2 输入/输出示例

**输入**："G1563在保定东站发生设备故障，预计延误30分钟"

**输出（AccidentCard）**：
```json
{
  "fault_type": "设备故障",
  "scene_category": "突发故障",
  "location_code": "BDD",
  "location_name": "保定东",
  "location_type": "station",
  "affected_train_ids": ["G1563"],
  "affected_train_count": 1,
  "expected_duration": 30,
  "fault_severity": "major",
  "is_complete": true,
  "missing_fields": []
}
```

### 3.3 实现方式

| 路径 | 实现类型 | 触发条件 | 说明 |
|------|----------|----------|------|
| **主路径** | LLM驱动 | 默认 | LLM调用，使用结构化输出提示（`l1_data_modeling`模板） |
| **回退路径** | 规则兜底 | LLM失败时 | `_fallback_extraction()`：基于关键词+正则提取 |
| **校验** | Pydantic | 始终 | Pydantic模型校验确保所有必填字段存在 |

### 3.4 关键技术细节：LLM提取 vs. 规则提取

v5.1+ 中，L0场景识别从硬编码规则迁移到LLM调用：
- LLM同时提取`scene_type`、`fault_type`、`station_code`
- 若LLM失败，回退到`_fallback_extraction()`关键词匹配
- 此设计收集SFT训练数据（`data/sft_train.jsonl`），用于未来模型微调

**实验阶段**：所有输入均走LLM提取，规则回退仅在LLM服务不可用时启用。

---

## 4. 快照构建器（SB）

### 4.1 目的

在L2/L3执行前，将完整问题（147列、13站）缩减为相关子问题。

### 4.2 重要性

若无快照裁剪：
- L2 Agent接收147列时刻表作为上下文，超出token限制
- L3 MIP求解器面临不可行的问题规模
- 响应时间超出调度员容忍度（目标：<60秒端到端）

### 4.3 裁剪算法

```
BuildSnapshot(AccidentCard ac)：
    incident_time = ac.start_time 或当前时间
    incident_station = ac.location_code
    incident_idx = incident车站在径路中的索引

    // 时间裁剪
    for 每列列车 t：
        if t 在 incident_time 之前已通过 incident_station：
            excluded.add(t)

    // 空间裁剪
    window_start = max(0, incident_idx - DEFAULT_RADIUS)
    window_end = min(m-1, incident_idx + DEFAULT_RADIUS)
    window_stations = stations[window_start:window_end+1]

    // 径路裁剪
    candidate_trains = [t for t in trains
                        if t.route 与 window_stations 相交
                        and t 不在 excluded 中]

    // 密度裁剪
    if len(candidate_trains) > MAX_CANDIDATES：
        candidate_trains = sort(candidate_trains,
                                key=proximity_to_incident)[:MAX_CANDIDATES]

    return NetworkSnapshot(
        candidate_train_ids=candidate_trains,
        excluded_train_ids=excluded,
        solving_window={corridor_id, window_start, window_end},
        trains=[crop(t, window_stations) for t in candidate_trains],
        stations=window_stations
    )
```

### 4.4 MIP专用快照（`snapshot_builder_mip.py`）

针对分层求解器，专用裁剪器进一步缩减至：
- 最多30列列车
- 最多8个车站
- 所有注入延误的列车强制包含

---

## 5. L2：策略规划层

### 5.1 目的

系统的"大脑"。给定AccidentCard和NetworkSnapshot，决定：
1. 运行哪个/哪些求解器
2. 使用什么优化目标
3. 运行单个求解器还是对比多个
4. 传递什么参数

### 5.2 架构：ReAct风格LLM Agent + Function Calling

与早期版本（v4.x及以前）L2输出JSON`preferred_solver`不同，当前L2是具备工具使用的完整Agent。

**Agent循环**：
```
for step in range(MAX_AGENT_STEPS=8)：
    response = llm.call_with_tools(messages, tools, temperature=0.2)
    if response.has_tool_calls()：
        results = execute_tool_calls(response.tool_calls)
        messages.append(tool_results)
    else：
        break  // Agent完成推理
```

### 5.3 工具定义

| 工具 | 类型 | 说明 | 实现类型 |
|------|------|------|----------|
| `assess_impact` | 感知 | 基于时刻表密度分析量化影响 | LLM驱动（调用后分析） |
| `get_train_status` | 感知 | 返回特定列车的当前延误状态 | 规则查询 |
| `query_timetable` | 感知 | 检索时刻表片段供检查 | 规则查询 |
| `quick_line_overview` | 感知 | 走廊密度与容量摘要 | 规则统计 |
| `check_impact_cascade` | 感知 | 使用经验公式估计传播 | 规则估算 |
| `generate_dispatch_notice` | 辅助 | 格式化调度通知文本 | LLM生成 |
| `run_solver` | 动作 | 执行指定名称的求解器 | 数学计算 |
| `compare_strategies` | 动作 | 运行多个求解器，用综合指标评分 | 数学计算 |

### 5.4 系统提示词工程

L2系统提示词包含：
- **求解器对比表**：MIP vs FCFS vs MaxDelayFirst vs Hierarchical，含特性与选择指南
- **场景到求解器映射**：临时限速→MIP；突发故障→FCFS；区间封锁→FCFS
- **安全约束**：区间封锁必须使用FCFS；信息不完整必须使用FCFS/NoOp
- **紧急程度上下文**：时段修正因子（高峰 vs 平峰）

### 5.5 智能策略对比

`compare_strategies`工具动态选择求解器组合：
- 若受影响列车≤3：对比 [MIP, FCFS, NoOp]
- 若受影响列车≤10：对比 [Hierarchical, MIP, FCFS, MaxDelayFirst]
- 若受影响列车>10：对比 [Hierarchical, FCFS, MaxDelayFirst, EAF]

避免在MIP明显会超时的情况下浪费时间。

### 5.6 规则兜底

若LLM不可用时，L2调用 `_rule_fallback()`：
- 使用 `SolverSelector.recommend_solver()` 基于场景特征做规则推荐
- 该路径在实验阶段不启用，仅作为系统健壮性保障

### 5.7 反射支持

若L4向工作流引擎发送`RollbackFeedback`，L2在其消息中追加`previous_feedback`：
```
上次尝试失败原因：{rollback_reason}
建议修复：{suggested_fixes}
请结合以上考虑重新规划。
```

### 5.8 L2 ReAct 循环与 Function Calling 详细流程图

```
+------------------------------------------------------------------+
|                        L2 策略规划层 (LLM Agent)                   |
|                     ReAct 循环 + Function Calling                  |
+------------------------------------------------------------------+
|                                                                  |
|  输入: AccidentCard + NetworkSnapshot + previous_feedback(若有)  |
|                                                                  |
|  +----------------------------------------------------------+   |
|  | Step 1-3: 感知与态势分析 (Observation / Reasoning)       |   |
|  |                                                          |   |
|  |  LLM -> assess_impact(密度分析)                          |   |
|  |       -> get_train_status(列车状态)                      |   |
|  |       -> query_timetable(时刻表检索)                     |   |
|  |       -> quick_line_overview(走廊概况)                   |   |
|  |       -> check_impact_cascade(传播估计)                  |   |
|  |                                                          |   |
|  |  【实现类型】工具执行为规则查询，策略推理为LLM驱动          |   |
|  +----------------------------------------------------------+   |
|                              |                                   |
|                              v                                   |
|  +----------------------------------------------------------+   |
|  | Step 4-6: 决策与执行 (Action)                              |   |
|  |                                                          |   |
|  |  LLM -> run_solver("mip"/"fcfs"/...) 或                  |   |
|  |       -> compare_strategies(["mip","fcfs","hierarchical"])|   |
|  |                                                          |   |
|  |  【实现类型】求解器执行为数学计算，求解器选择为LLM驱动      |   |
|  +----------------------------------------------------------+   |
|                              |                                   |
|                              v                                   |
|  +----------------------------------------------------------+   |
|  | Step 7-8: 方案生成与验证 (Verification)                   |   |
|  |                                                          |   |
|  |  LLM -> generate_dispatch_notice(格式化通知)            |   |
|  |       -> 内部验证: 检查solver输出是否完整                 |   |
|  |                                                          |   |
|  |  终止条件:                                               |   |
|  |    - LLM不再调用工具，直接输出最终决策                     |   |
|  |    - 达到 MAX_AGENT_STEPS=8                              |   |
|  |    - 连续2次工具调用失败                                   |   |
|  +----------------------------------------------------------+   |
|                              |                                   |
|                              v                                   |
|  输出: planner_decision {preferred_solver, objective, config}  |
|                                                                  |
+------------------------------------------------------------------+
```

**工具分类**：
- **感知类工具**（5个）：`assess_impact`, `get_train_status`, `query_timetable`, `quick_line_overview`, `check_impact_cascade`
- **动作类工具**（2个）：`run_solver`, `compare_strategies`
- **辅助类工具**（1个）：`generate_dispatch_notice`

---

## 6. L3：求解执行层

### 6.1 目的

执行L2选定的调度器。当L2未完成求解时作为兜底。

### 6.2 设计：调度器注册表模式

```python
class SchedulerRegistry：
    _schedulers = {
        "mip": MIPScheduler,
        "fcfs": FCFSScheduler,
        "max-delay-first": MaxDelayFirstScheduler,
        "hierarchical": HierarchicalSolver,
        "eaf": EAFScheduler,
        "noop": NoOpScheduler,
    }

    @classmethod
    def create(cls, name, trains, stations, **kwargs)：
        scheduler_class = cls._schedulers.get(name)
        return scheduler_class(trains, stations, **kwargs)
```

### 6.3 安全兜底（【规则安全层】）

1. 若`accident_card.scene_category == "区间封锁"`且选定求解器≠"fcfs"，强制覆盖为FCFS。
2. 若`not accident_card.is_complete`且选定求解器不在("fcfs", "noop")中，强制覆盖为FCFS。
3. 若trains/stations为None，自动从`data_loader`加载。

### 6.4 Pydantic防御性转换

L3包含`_ensure_pydantic_objects()`处理字典到Pydantic的转换，防止下游代码期望对象属性时发生`AttributeError`。

---

## 7. L4：评估与方案生成层

### 7.1 目的

将求解器输出转换为：
1. 定量评估报告（指标）
2. 自然语言调度方案
3. 每列列车的具体调整指令
4. 多方案对比分析（如适用）

### 7.2 设计演进

| 版本 | 方案 | LLM调用次数 | 特点 |
|------|------|------------|------|
| v4.x | 分离调用 | 2 | 一次评分，一次生成自然语言方案 |
| v5.0+ | 合并单次调用 | 1 | 评估+方案+指令在一个提示中完成 |
| v8.0 | 综合单次调用 | 1 | 增加多方案对比分析 |

### 7.3 评估上下文构建

L4为LLM构建丰富的上下文：
```python
evaluation_context = {
    "total_delay_minutes": ...,
    "max_delay_minutes": ...,
    "avg_delay_minutes": ...,
    "affected_trains_count": ...,
    "solver_name": ...,
    "scenario_type": ...,
    "train_adjustments": {  // 前5列受影响列车
        "G1563": [
            {"station_code": "BDD", "delay_minutes": 30, ...},
            ...
        ]
    },
    "has_comparison": True/False,
    "comparison_results": [...]  // 若运行了多求解器
}
```

### 7.4 LLM提示词结构

**系统提示词**（`_EVALUATION_SYSTEM_PROMPT`）：
- 专业铁路调度评估框架
- 京广高铁专用标准与KPI
- 结构化输出指令（evaluation_summary、natural_language_plan、adjustment_instructions、comparison_analysis）

**用户提示词**：
- 指标与调整详情
- 要求使用面向调度员的专业语言

### 7.5 规则计算部分

除LLM评估外，L4通过 `_calculate_high_speed_metrics()` 计算：
- **准点率**：列车最大延误 < 5分钟（300秒）的比例
- **严格准点率**：列车最大延误 < 3分钟（180秒）的比例
- **延误分级**：micro[0,300)s / small[300,1800)s / medium[1800,6000)s / large[6000,∞)s
- **综合评级**：A(≥90分) / B(≥75分) / C(≥60分) / D(<60分)

### 7.6 策略引擎集成

LLM评估后，`PolicyEngine`应用基于规则的阈值：
- `max_delay > 60分钟`：标记人工复核
- `affected_trains > 20`：建议区段隔离
- `on_time_rate < 0.5`：建议替代策略

策略决策：`ACCEPT` / `FALLBACK` / `RERUN`
- `FALLBACK` / `RERUN` 触发ARDO反射循环。
- **PolicyEngine为纯规则实现，LLM无权覆盖其决策。**

---

## 8. 自适应反射调度编排器（ARDO）

### 8.1 目的

当L4评估指示结果次优时，迭代改进调度方案。

### 8.2 算法

```
算法：ARDO ExecuteFullWorkflow
输入：user_input, canonical_request, enable_rag
输出：WorkflowResult

1: L1_result = L1.extract(user_input)
2: if not L1_result.accident_card.is_complete：
3:     return incomplete_result(missing_fields)
4:
5: snapshot = SnapshotBuilder.build(L1_result.accident_card)
6:
7: iteration_results = []
8: previous_feedback = None
9: for iteration in range(1, 4):  // 最多3次尝试
10:    L2_result = L2.plan(
11:       accident_card=L1_result.accident_card,
12:       network_snapshot=snapshot,
13:       previous_feedback=previous_feedback
14:    )
15:    L3_result = L3.execute(L2_result.planner_decision, ...)
16:    L4_result = L4.evaluate(L3_result, ...)
17:
18:    // Fix 5: 合并 PolicyEngine 反馈到 previous_feedback
19:    policy_decision = L4_result.policy_decision
20:    if policy_decision.suggested_fixes：
21:        fixes = fixes + policy_fixes
22:    if policy_decision.reason：
23:        reason = reason + " | PolicyEngine: " + policy_reason
24:
25:    rollback = L4_result.rollback_feedback
26:    if rollback.needs_rerun and iteration < 3：
27:        previous_feedback = {
28:            "rollback_reason": reason,
29:            "suggested_fixes": fixes,
30:            "iteration": iteration,
31:            "policy_override": True
32:        }
33:        continue
34:    else：
35:        iteration_results.append((iteration, L2_result, L3_result, L4_result))
36:        break
37:
38: // 选择最佳迭代
39: best = select_best_iteration(iteration_results, optimization_objective)
40: return build_success_result(best)
```

### 8.3 最佳迭代选择

基于优化目标的动态加权：

| 优化目标 | 主权重 | 次权重 | 第三权重 |
|---------|--------|--------|---------|
| `min_total_delay` | total_delay=0.35 | on_time_rate=0.20 | max_delay=0.15 |
| `min_max_delay` | max_delay=0.35 | on_time_rate=0.20 | total_delay=0.15 |
| `min_avg_delay` | avg_delay=0.30 | total_delay=0.20 | on_time_rate=0.20 |

每轮迭代得分：
$$\text{IterScore} = w_1 \cdot \text{norm}(\text{metric}_1) + w_2 \cdot \text{norm}(\text{metric}_2) + w_3 \cdot \text{norm}(\text{metric}_3)$$

IterScore最低者获胜。

---

## 9. 会话管理与多轮对话

### 9.1 会话类型

| 类型 | API端点 | 使用场景 | 统一入口 |
|------|---------|---------|----------|
| 流式聊天 | `/api/agent_chat_stream` | 主调度接口 | agent.handle() |
| 有状态工作流 | `/api/workflow/start` + `/api/workflow/next` | 多轮信息收集 | agent.handle() |
| 通用聊天 | `/api/general_chat` | 无工具调用的自由问答 | agent.handle() |

### 9.2 会话状态管理

系统使用双层会话管理：

```
Agent.session_state (内存层)
    ├── history: List[Dict]        # 对话历史（user/assistant）
    ├── last_accident_card: Any     # 最近一次事故卡片
    ├── last_dispatch_result: Dict  # 最近一次调度结果快照
    ├── last_mode: str              # 最近一次模式（light/heavy）
    └── turn_count: int             # 轮次计数

SessionManager (持久化层)
    ├── create_session()            # 创建workflow会话
    ├── update_layer_result()       # 更新L1-L4结果
    ├── complete_session()          # 标记完成
    └── update_messages()           # 同步对话历史
```

当提供 `session_id` 时，`handle()` 自动从 `SessionManager` 加载历史，处理完成后同步回去。

### 9.3 意图路由器

将输入消息路由到三个分支：
- **`query`**：信息检索（列车状态、时刻表查询）
- **`chat`**：自由对话
- **`dispatch`**：完整L1-L4工作流执行

路由使用基于LLM的分类（`classify()`），回退到关键词匹配（`classify_with_fallback()`）。

### 9.4 意图理解两层架构图

```
+------------------------------------------------------------------+
|                     意图理解：两层架构设计                         |
+------------------------------------------------------------------+
|                                                                  |
|  第一层：IntentRouter（轻量级预分类）                              |
|  【实现类型】LLM 主 + 规则兜底                                     |
|  +----------------------------------------------------------+   |
|  |  输入: 调度员自然语言消息                                   |   |
|  |  输出: dispatch / query / chat / unknown                   |   |
|  |                                                          |   |
|  |  主路径: classify() -> LLM单次调用                        |   |
|  |    "G1563在石家庄站故障延误30分钟" -> "dispatch"           |   |
|  |    "现在有哪些列车晚点？"       -> "query"                |   |
|  |    "你好"                      -> "chat"                 |   |
|  |                                                          |   |
|  |  兜底路径: _classify_with_rules()                         |   |
|  |    关键词匹配（调度/故障/延误 -> dispatch）               |   |
|  |    疑问词检测（哪些/什么/怎么 -> query）                  |   |
|  +----------------------------------------------------------+   |
|                              |                                   |
|                              | dispatch                          |
|                              v                                   |
|  第二层：L1 数据建模层（深度结构化提取）                           |
|  【实现类型】LLM 主 + 规则兜底                                     |
|  +----------------------------------------------------------+   |
|  |  输入: 自然语言 + 场景上下文                                |   |
|  |  输出: AccidentCard (结构化JSON)                           |   |
|  |                                                          |   |
|  |  提取字段:                                                |   |
|  |    - fault_type      : "设备故障"                         |   |
|  |    - scene_category  : "突发故障"                         |   |
|  |    - location_code   : "SJP"                              |   |
|  |    - affected_trains : ["G1563"]                          |   |
|  |    - expected_duration: 30 (分钟)                         |   |
|  |    - is_complete     : true/false                         |   |
|  |                                                          |   |
|  |  主路径: LLM结构化提取（l1_data_modeling模板）            |   |
|  |  兜底路径: _fallback_extraction() 正则+关键词补全         |   |
|  +----------------------------------------------------------+   |
|                              |                                   |
|                              v                                   |
|                     进入 L2 策略规划层                            |
|                                                                  |
+------------------------------------------------------------------+

**设计理由**：
- **IntentRouter** 只做轻量级分类（3-4类），LLM调用开销低（~100 tokens）
- **L1** 做深度提取（10+字段），需要更多上下文和结构化输出
- 两层分离使 L1 可以复用（如反射循环中只执行一次）
- 规则兜底确保 LLM 服务不可用时系统仍能降级运行
```

---

## 10. 提示词工程总结

### 10.1 提示词模板

| 模板 | 层级 | 用途 | 温度 |
|------|------|------|------|
| `l1_data_modeling` | L1 | 从自然语言提取AccidentCard | 0.2 |
| `intent_classification` | L0 | 意图分类（dispatch/query/chat） | 0.1 |
| `l2_system_prompt` | L2 | Agent行为 + 工具描述 + 求解器选择指南 | 0.2 |
| `l2_scenario_text` | L2 | 动态上下文（紧急程度、时段、安全约束） | 0.2 |
| `l4_evaluation_system` | L4 | 评估框架 + 输出格式规范 | 0.3 |

### 10.2 提示词策略

1. **温度控制**：L1/L2使用0.2（确定性提取）；L4使用0.3（自然语言生成略需创造性）
2. **上下文截断**：工具结果截断至至<200KB；时刻表摘要限前3列受影响列车
3. **结构化输出**：所有产生系统消费数据的LLM调用均使用JSON模式约束
4. **少样本示例**：L1提示词包含3-5个调度员语言示例及对应AccidentCard输出

---

## 11. RAG集成

### 11.1 知识库结构

```
data/knowledge/
├── operations/       # 调度员操作流程（JSON）
├── reference/        # 参考文档
└── rules/            # 调度规则与规章
```

### 11.2 检索机制

当前实现：**基于关键词的检索器**（`rag_retriever.py`）
- 从AccidentCard提取关键词（fault_type、scene_category、location）
- 与知识库条目匹配
- 返回top-k相关操作指南

计划升级：基于向量嵌入的检索，使用领域专用嵌入模型。

### 11.3 工作流中的RAG

若`enable_rag=True`，RAG结果追加到L2上下文：
```
[检索到的知识]
- 保定东站设备故障处理流程：...
- 京广高铁暴雨限速预案：...
```

---

## 12. SFT数据收集

系统自动收集监督微调（SFT）样本：

**输出**：`data/sft_train.jsonl`

```json
{
  "messages": [
    {"role": "system", "content": "从调度员描述中提取事故信息"},
    {"role": "user", "content": "暴雨导致石家庄站限速80km/h"},
    {"role": "assistant", "content": "{\"scene_category\": \"临时限速\", ...}"}
  ],
  "metadata": {"layer": "L1", "template_id": "l1_data_modeling"}
}
```

**计划用途**：微调领域专用模型，用于L1提取和L2求解器选择，降低API延迟与成本。
