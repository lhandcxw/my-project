# LLM-TTRA：系统架构与技术总览

**文档版本**：1.0  
**日期**：2026-04-26  
**目标期刊**：IEEE Transactions on Intelligent Transportation Systems、Transportation Research Part C、CCF-A/B类中文期刊

---

## 1. 摘要

LLM-TTRA（大语言模型辅助列车时刻表重排助手，Large Language Model-assisted Train Timetable Rescheduling Assistant）是一款面向高铁调度运营的智能决策支持系统。系统部署于京广高铁走廊（北京西至安阳东，13站、147列列车），融合大语言模型的认知能力与运筹优化技术，实现基于自然语言驱动的时刻表重排。架构采用四层LLM-Agent工作流（L1数据建模 — L2策略规划 — L3求解执行 — L4方案评估），配备FCFS快速筛选与MIP精确优化相结合的分层混合求解器，以及面向高铁客运专线的多算法对比评估框架。

---

## 2. 系统概览

### 2.1 问题域

高铁调度面临根本性矛盾：突发扰动（天气、设备故障、区间封锁）要求快速响应，但最优重排涉及数百列列车与数十个车站上的NP-hard组合优化。传统方法分为两类：

- **基于规则的系统**：速度快但次优，无法适应新场景。
- **数学优化**：小规模实例最优，但在运营规模（147列×13站）下纯MIP超过300秒超时限制。

LLM-TTRA弥合了这一鸿沟：利用LLM进行高层态势理解与求解器选择，将数值优化委托给专门算法。

### 2.2 设计原则

1. **真实世界 grounded**：全部数据来源于京广高铁实际时刻表；约束依据CTCS-3标准标定。
2. **混合智能**：LLM处理歧义、自然语言与策略推理；OR求解器处理数值优化。
3. **可扩展性**：分层求解器通过动态窗口裁剪将MIP问题从147列缩减至不超过30列。
4. **可解释性**：每个决策均可通过L1-L4工作流追溯；为调度员生成自然语言方案。
5. **安全性**：多层安全兜底（基于规则的L3回退、策略引擎拒绝阈值、区间封锁强制FCFS）。

### 2.3 高层架构

```
+------------------------------------------------------------------+
|                        用户交互层                                 |
|  自然语言输入  -->  意图路由器  -->  调度/查询                     |
+------------------------------------------------------------------+
                              |
        +---------------------+---------------------+
        |                                           |
        v                                           v
+------------------------+             +---------------------------+
|   L1 数据建模层         |             |   Web前端（SPA）           |
|   (AccidentCard提取)    |             |   Flask + 原生JS           |
+------------------------+             +---------------------------+
        |
        v
+------------------------+     +---------------------------+
|   快照构建器            | --> |   网络快照                 |
|   (确定性裁剪)          |     |   (候选列车、车站窗口)      |
+------------------------+     +---------------------------+
        |
        v
+------------------------+
|   L2 策略规划层         |
|   (LLM Agent + 工具)    |
+------------------------+
        |
        v
+------------------------+
|   L3 求解执行层         |
|   (调度器注册表)         |
+------------------------+
        |
        v
+------------------------+
|   L4 方案评估层         |
|   (指标 + LLM方案)       |
+------------------------+
        |
        v
+------------------------+
|   策略引擎              |
|   (接受/拒绝/重规划)     |
+------------------------+
```

---

## 3. 核心模块

### 3.1 数据层（`models/`、`data/`）

**职责**：全部铁路数据的单一可信来源。

| 组件 | 文件 | 说明 |
|------|------|------|
| 数据加载器 | `models/data_loader.py` | 从`plan_timetable.csv`加载147列列车，从`station_alias.json`加载13个车站，从`min_running_time_matrix.csv`加载最小区间运行时间 |
| Pydantic模型 | `models/data_models.py` | `Train`、`Station`、`DelayInjection`、`TrainStop` 及校验 |
| 工作流模型 | `models/workflow_models.py` | `AccidentCard`、`NetworkSnapshot`、`EvaluationReport` |
| 知识库 | `data/knowledge/` | 支持RAG检索的调度员操作指南 |

### 3.2 求解层（`solver/`、`scheduler_comparison/`）

**职责**：核心调度算法，统一接口。

| 算法 | 类型 | 复杂度 | 最佳适用场景 |
|------|------|--------|-------------|
| MIP | 精确（PuLP+CBC） | NP-hard | 小规模（≤3列）、临时限速 |
| FCFS | 启发式 | O(n log n) | 大规模、实时响应 |
| MaxDelayFirst | 启发式 | O(n²) | 高峰时段、严重延误 |
| EAF | 启发式 | O(n log n) | 早班车恢复正点 |
| NoOp | 基线 | O(n) | 区间封锁基线 |
| Hierarchical | 混合 | O(n log n) + MIP | 通用，可扩展至全网络 |

`scheduler_comparison/`模块提供：
- `scheduler_interface.py`：抽象基类`BaseScheduler`，定义`solve()`契约
- `comparator.py`：`SchedulerComparator`，多求解器加权评分对比
- `metrics.py`：`EvaluationMetrics` + `HighSpeedMetricsWeight`，面向领域的评分

### 3.3 Agent层（`railway_agent/`）

**职责**：LLM驱动的工作流编排。

| 组件 | 文件 | 角色 |
|------|------|------|
| 工作流引擎 | `llm_workflow_engine_v2.py` | ARDO（自适应反射调度编排器），最多3轮迭代L2-L4循环 |
| L1提取器 | `workflow/layer1_data_modeling.py` | 自然语言 → 结构化`AccidentCard` |
| L2规划器 | `workflow/layer2_planner.py` | ReAct风格LLM Agent，8个工具（影响评估、求解器对比） |
| L3求解器 | `workflow/layer3_solver.py` | 兜底执行引擎，将AccidentCard转为DelayInjection |
| L4评估器 | `workflow/layer4_evaluation.py` | 指标计算 + 单次LLM调用生成自然语言方案与调整指令 |
| 策略引擎 | `policy_engine.py` | 基于规则的接受/拒绝/重规划阈值 |
| 分层求解器 | `hierarchical_solver.py` | FCFS+MIP混合，动态窗口裁剪 |
| 快照构建器 | `snapshot_builder.py` | 基于事故位置和时间将147列裁剪为候选集 |
| LLM适配器 | `adapters/llm_adapter.py` | 统一调用接口，支持DashScope / Ollama / vLLM / OpenAI |

### 3.4 Web层（`web/`）

**职责**：面向调度员的交互界面。

| 组件 | 文件 | 特性 |
|------|------|------|
| 后端 | `app.py` | Flask、SSE流式传输、REST API端点 |
| 前端 | `templates/index.html` | SPA，集成聊天、指标面板、求解器对比、工作流追踪 |
| JavaScript | `static/main_unified.js` | SSE解析、运行图渲染、对比图表 |

---

## 4. 关键技术创新

### 4.1 分层混合求解器

**问题**：纯MIP在147列×13站上超时（>300秒）。
**解决方案**：三层层次结构：
1. **FCFS筛选**：对所有列车运行FCFS，毫秒级识别真正受影响的列车（含传播）。
2. **动态MIP窗口**：根据延误严重程度裁剪至≤30列×≤8站：
   - 小延误（（<10分钟）：±3站
   - 中等延误（10-30分钟）：±4站
   - 大延误（30-60分钟）：±5站
   - 严重延误（>60分钟）：±6站
3. **质量门控**：MIP相对FCFS改进≥1分钟才接受；否则使用FCFS。

**效果**：MIP在裁剪窗口上30-60秒求解；整体延误比纯FCFS减少30-60%。

### 4.2 基于Function Calling的LLM-Agent（L2规划器）

与早期版本让LLM直接输出JSON求解器名称不同，当前L2规划器采用OpenAI兼容的Function Calling接口，配备8个工具：

1. `assess_impact`：基于时刻表分析量化影响（而非仅用户输入）
2. `get_train_status`：查询特定列车状态
3. `query_timetable`：检索时刻表片段
4. `quick_line_overview`：走廊密度分析
5. `check_impact_cascade`：传播估计
6. `generate_dispatch_notice`：格式化调度通知
7. `run_solver`：执行单个求解器
8. `compare_strategies`：智能多求解器对比，动态选择

Agent运行ReAct风格循环（最多8步，temperature=0.2），可自主完成从态势感知到方案对比的完整工作流。

### 4.3 自适应反射编排（ARDO）

工作流引擎支持最多3轮L2→L3→L4迭代，带反射反馈：
- L4评估可触发`RollbackFeedback`，含`rollback_reason`与`suggested_fixes`
- 最佳迭代通过加权评分选择，与优化目标对齐：
  - `min_total_delay`：total_delay=0.35、max_delay=0.15、on_time=0.20
  - `min_max_delay`：max_delay=0.35、total_delay=0.15、on_time=0.20
  - `min_avg_delay`：avg_delay=0.30、total_delay=0.20、on_time=0.20

### 4.4 面向高铁客运专线的评估指标

除标准延误指标外，系统还计算：
- **传播指标**：深度（站数）、广度（车数）、传播系数
- **恢复率**：(原始总延误 - 优化后总延误) / 原始总延误
- **专家指标**（`evaluation/expert_metrics.py`）：延误公平性基尼系数、优先级加权延误（G/D字头列车惩罚更高）、缓冲时间比例、区间利用率、能耗效率指数

---

## 5. API端点

| 方法 | 端点 | 用途 |
|------|------|------|
| POST | `/api/agent_chat_stream` | 主调度API（SSE流式） |
| POST | `/api/agent_chat` | 非流式调度API |
| POST | `/api/compare` | 求解器对比 |
| POST | `/api/diagram` | 生成列车运行图（PNG base64） |
| POST | `/api/workflow/start` | 启动多轮工作流 |
| POST | `/api/workflow/next` | 继续多轮工作流 |
| GET | `/api/health` | 系统状态 |

---

## 6. 配置管理

环境参数由`config/dispatch_env.yaml`管理：
- `headway_time`：180秒（CTCS-3标准）
- `stop_time_redundancy_ratio`：0.8
- `running_time_redundancy_ratio`：0.85
- `min_departure_interval`：120秒（咽喉区约束）
- 场景类型默认求解器

Python配置由`config.py`管理LLM提供商、API密钥、模型名称及特性开关（`FORCE_LLM_MODE`、`DASHSCOPE_ENABLE_THINKING`）。

---

## 7. 技术栈

| 层次 | 技术 |
|------|------|
| 大模型 | 阿里云DashScope（glm-5.1）、OpenAI兼容API |
| MIP求解器 | PuLP + CBC |
| Web后端 | Flask、Pydantic |
| Web前端 | 原生JS、HTML5、Canvas 2D |
| 数据处理 | Pandas、NumPy |
| 配置管理 | YAML + Python数据类 |

---

## 8. 部署方式

- **后端**：Python 3.10+，Flask，端口8081
- **前端**：纯HTML/CSS/JS，无需构建步骤
- **大模型**：云端API（DashScope）或本地部署（Ollama/vLLM）
- **数据**：启动时加载静态CSV/JSON文件（约147列列车，总计约1MB）

---

## 9. 版本历史

- **v8.0**（2026-04-24）：avg_delay计算修正、YAML配置、统一SPA、glm-5.1、EAF调度器
- **v7.0**（2026-04-17）：SFT规划文档、代码审查完成
- **v5.1**（2026-04-10）：L0场景识别改为LLM调用、前端修复
- **v5.0**（2026-04-09）：架构清理、FORCE_LLM_MODE、统一工作流

---

## 10. 未来工作

1. **算法层面**：强化学习调度器、滚动时域优化、列车优先级差异化
2. **建模层面**：扩展至京广高铁全段（北京西—广州南）、列车动力学模型、能耗优化
3. **系统层面**：多场景并发处理、接入CTC实时数据、移动端调度员APP
4. **大模型层面**：基于已收集调度决策数据（`data/sft_train.jsonl`）进行SFT/RL微调，实现自主求解器选择
