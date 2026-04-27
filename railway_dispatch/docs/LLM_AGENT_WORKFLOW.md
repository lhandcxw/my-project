# LLM-TTRA：LLM-Agent工作流设计

**文档版本**：1.0  
**日期**：2026-04-26  
**用途**：四层LLM-Agent工作流（L1-L4）、自适应反射调度编排器（ARDO）及Function Calling L2规划器的详细设计文档。目标读者：AI+OR混合系统审稿人。

---

## 1. 设计理念

核心设计问题是：*LLM应该做什么？经典算法应该做什么？*

我们的答案，源自系统迭代版本（v1.0至v8.0）的演进：

| 任务 | 分配对象 | 理由 |
|------|---------|------|
| 自然语言理解 | LLM（L1） | LLM擅长实体提取、消歧及处理口语化调度员语言 |
| 策略性求解器选择 | LLM（L2） | 需要结合场景类型、紧急程度、时段、规模进行上下文推理 |
| 数值优化 | OR求解器（L3） | MIP/FCFS保证约束满足；LLM不擅长精确数值优化 |
| 解释与报告 | LLM（L4） | 需要将指标综合为自然语言叙述和可执行指令 |
| 安全关键决策 | 规则引擎（PolicyEngine） | 硬约束（区间封锁→FCFS）不能依赖LLM可靠性 |

这一划分遵循 **"策略交给认知，计算交给数字"** 原则。

---

## 2. 工作流概览

```
用户输入（自然语言）
    |
    v
+-------------------+
|   L0: 预处理       |  意图分类（聊天 / 查询 / 调度）
+-------------------+
    |
    v
+-------------------+
|   L1: 数据建模     |  从自然语言提取 AccidentCard
+-------------------+
    |
    v
+-------------------+
|   SB: 快照构建     |  构建 NetworkSnapshot（确定性裁剪）
+-------------------+
    |
    v
+-------------------+
|   L2: 规划器       |  LLM Agent 决定策略
+-------------------+
    |
    v
+-------------------+
|   L3: 求解器       |  执行选定的调度器
+-------------------+
    |
    v
+-------------------+
|   L4: 评估层       |  计算指标 + 生成自然语言方案
+-------------------+
    |
    v
+-------------------+
|   策略引擎         |  接受 / 拒绝 / 重规划
+-------------------+
    |
    +--[拒绝]--> 反馈至 L2（最多3轮迭代）
    |
    +--[接受]--> 返回 WorkflowResult
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

- **主路径**：LLM调用，使用结构化输出提示（`l1_data_modeling`模板）
- **回退路径**：当`FORCE_LLM_MODE=false`且LLM失败时，基于关键词的规则提取
- **校验**：Pydantic模型校验确保所有必填字段存在

### 3.4 关键技术细节：LLM提取 vs. 规则提取

v5.1+ 中，L0场景识别从硬编码规则迁移到LLM调用：
- LLM同时提取`scene_type`、`fault_type`、`station_code`
- 若LLM失败且`FORCE_LLM_MODE=false`，回退到关键词匹配
- 此设计收集SFT训练数据（`data/sft_train.jsonl`），用于未来模型微调

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

| 工具 | 类型 | 说明 |
|------|------|------|
| `assess_impact` | 感知 | 基于时刻表密度分析量化影响 |
| `get_train_status` | 感知 | 返回特定列车的当前延误状态 |
| `query_timetable` | 感知 | 检索时刻表片段供检查 |
| `quick_line_overview` | 感知 | 走廊密度与容量摘要 |
| `check_impact_cascade` | 感知 | 使用经验公式估计传播 |
| `generate_dispatch_notice` | 辅助 | 格式化调度通知文本 |
| `run_solver` | 动作 | 执行指定名称的求解器 |
| `compare_strategies` | 动作 | 运行多个求解器，用综合指标评分 |

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

### 5.6 反射支持

若L4向工作流引擎发送`RollbackFeedback`，L2在其消息中追加`previous_feedback`：
```
上次尝试失败原因：{rollback_reason}
建议修复：{suggested_fixes}
请结合以上考虑重新规划。
```

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
        "max_delay_first": MaxDelayFirstScheduler,
        "hierarchical": HierarchicalSolver,
        "eaf": EAFScheduler,
        "noop": NoOpScheduler,
    }

    @classmethod
    def create(cls, name, trains, stations, **kwargs)：
        scheduler_class = cls._schedulers.get(name)
        return scheduler_class(trains, stations, **kwargs)
```

### 6.3 安全兜底

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

### 7.5 策略引擎集成

LLM评估后，`PolicyEngine`应用基于规则的阈值：
- `max_delay > 60分钟`：标记人工复核
- `affected_trains > 20`：建议区段隔离
- `on_time_rate < 0.5`：建议替代策略

策略决策：`ACCEPT` / `REJECT` / `REPLAN`
- `REJECT` / `REPLAN` 触发ARDO反射循环。

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
3:     return dialogue_workflow(L1_result.missing_fields)
4:
5: snapshot = SnapshotBuilder.build(L1_result.accident_card)
6:
7: best_iterations = []
8: for iteration in range(1, 4):  // 最多3次尝试
9:     L2_result = L2.plan(
10:        accident_card=L1_result.accident_card,
11:        network_snapshot=snapshot,
12:        previous_feedback=rollback_feedback if iteration > 1 else None
13:    )
14:    L3_result = L3.execute(L2_result.planner_decision, ...)
15:    L4_result = L4.evaluate(L3_result, ...)
16:
17:    if L4_result.rollback_feedback.needs_rerun and iteration < 3：
18:        rollback_feedback = L4_result.rollback_feedback
19:        continue
20:    else：
21:        best_iterations.append((iteration, L4_result))
22:
23: // 选择最佳迭代
24: best = select_best_iteration(best_iterations, optimization_objective)
25: return build_success_result(best)
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

| 类型 | API端点 | 使用场景 |
|------|---------|---------|
| 流式聊天 | `/api/agent_chat_stream` | 主调度接口 |
| 有状态工作流 | `/api/workflow/start` + `/api/workflow/next` | 多轮信息收集 |
| 通用聊天 | `/api/general_chat` | 无工具调用的自由问答 |

### 9.2 内存中会话存储

```python
_chat_memory: Dict[str, Dict] = {
    session_id: {
        "entities": {},           // 提取的AccidentCard字段
        "last_intent": "",        // IntentRouter输出
        "messages": [],           // OpenAI格式消息历史
        "timestamp": float,       // 最后活动时间
    }
}
```

限制：每会话20轮（40条消息），全局最多100个会话。

### 9.3 意图路由器

将输入消息路由到三个分支：
- **`query`**：信息检索（列车状态、时刻表查询）
- **`chat`**：自由对话
- **`dispatch`**：完整L1-L4工作流执行

路由使用基于LLM的分类，回退到关键词匹配。

---

## 10. 提示词工程总结

### 10.1 提示词模板

| 模板 | 层级 | 用途 |
|------|------|------|
| `l1_data_modeling` | L1 | 从自然语言提取AccidentCard |
| `l0_preprocess_extractor` | L0 | 场景类型分类 |
| `l2_system_prompt` | L2 | Agent行为 + 工具描述 + 求解器选择指南 |
| `l2_scenario_text` | L2 | 动态上下文（紧急程度、时段、安全约束） |
| `l4_evaluation_system` | L4 | 评估框架 + 输出格式规范 |

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
