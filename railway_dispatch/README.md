# 铁路调度Agent系统

基于大模型和整数规划的智能铁路调度优化系统（v3.2）。

## v3.2 更新日志

**架构调整**：
- 新增 `snapshot_builder.py`：确定性构建 NetworkSnapshot，不调用 LLM
- L1 不再从自然语言二次抽取字段，只消费 CanonicalDispatchRequest
- 统一 schema_version 为 dispatch_v3_2
- 新增 WorkflowResponse 统一响应结构

**字段扩展**：
- CanonicalDispatchRequest 新增：snapshot_time, expected_duration_minutes, fault_severity, corridor_hint, current_state_ref, normalization_trace
- FaultTypeCode 扩展：新增 WIND, SNOW, TRACK_CONDITION, MANUAL_RESTRICTION, DELAY

**参数化**：
- PolicyEngine 阈值从配置读取（支持环境变量覆盖）
- 场景类型不同阈值不同（TEMP_SPEED_LIMIT: 30min, SUDDEN_FAILURE: 45min, ...）

**原则**：
- L0 是唯一输入标准化入口
- 所有下游模块只消费 CanonicalDispatchRequest
- LLM 不做最终决策，PolicyEngine 做最终决策
- validator 失败时，任何 LLM 输出都不能覆盖失败结论
- rule_agent 保留为 fallback/兼容模式

## 系统架构

```
railway_dispatch/
├── data/                     # 数据层（统一入口）
│   ├── station_alias.json    # 车站数据（优先使用）
│   ├── plan_timetable.csv    # 时刻表（优先使用）
│   ├── min_running_time_matrix.csv  # 区间最小运行时间
│   ├── train_id_mapping.csv  # 列车ID映射
│   ├── scenarios/            # 场景数据
│   └── knowledge/            # RAG知识库
├── models/                   # 数据模型层
│   ├── common_enums.py       # 统一英文枚举（SceneTypeCode, FaultTypeCode等）
│   ├── preprocess_models.py  # 预处理数据模型
│   ├── data_models.py       # Pydantic模型
│   ├── data_loader.py       # 统一数据加载器
│   └── workflow_models.py   # 工作流数据模型
├── railway_agent/           # Agent模块
│   ├── preprocessing/        # L0 预处理层（新增）
│   │   ├── request_adapter.py      # 请求适配器
│   │   ├── rule_extractor.py       # 规则提取器
│   │   ├── alias_normalizer.py     # 别名归一化器
│   │   ├── llm_extractor.py        # LLM提取器
│   │   ├── incident_builder.py     # 事故卡片构建器
│   │   └── completeness_gate.py    # 完整性门禁
│   ├── adapters/            # 适配器层（新增）
│   │   ├── llm_adapter.py          # LLM适配器
│   │   ├── rag_adapter.py          # RAG适配器
│   │   ├── skill_adapter.py        # Skill适配器
│   │   ├── solver_adapter.py       # Solver适配器
│   │   ├── validator_adapter.py    # Validator适配器
│   │   ├── evaluator_adapter.py    # Evaluator适配器
│   │   └── response_adapter.py     # 响应适配器
│   ├── preprocess_service.py       # 预处理服务入口
│   ├── policy_engine.py            # 策略引擎（新增）
│   ├── llm_workflow_engine.py      # 5层LLM工作流引擎
│   ├── rag_retriever.py            # RAG检索增强
│   ├── session_manager.py          # 多轮会话管理
│   ├── dispatch_skills.py          # 调度Skills
│   ├── rule_agent.py               # 规则引擎（Fallback模式，非主流程）
│   └── rule_workflow_bridge.py     # 规则到工作流桥接
├── solver/                   # 求解器层
│   ├── fcfs_scheduler.py          # FCFS调度器
│   ├── fcfs_adapter.py            # FCFS适配器
│   ├── mip_scheduler.py            # MIP求解器
│   ├── mip_adapter.py              # MIP适配器
│   ├── max_delay_first_scheduler.py    # 最大延误优先调度器
│   ├── max_delay_first_adapter.py      # 最大延误优先适配器
│   ├── noop_scheduler.py          # 空操作调度器
│   ├── noop_adapter.py            # 空操作适配器
│   └── solver_registry.py         # 求解器注册
├── rules/                    # 约束规则层
│   └── validator.py               # 规则验证器
├── evaluation/              # 评估层
│   └── evaluator.py              # 方案评估
├── visualization/           # 可视化层
│   └── simple_diagram.py        # 运行图生成
└── web/                    # Web层
    └── app.py                  # Flask应用
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动Web服务

```bash
cd railway_dispatch
python web/app.py
```

访问 http://localhost:8081

### 3. LLM多轮对话（推荐）

点击「LLM多轮对话」标签页，体验5层LLM决策的完整流程：
- L0: 预处理 - 统一输入转换为标准请求
- L1: 数据建模 - LLM辅助判断场景类型，NetworkSnapshot由确定性逻辑切出
- L2: Planner - LLM决策 planning_intent（问题类型与处理意图）
- L3: Solver - SolverPolicyAdapter 根据 intent 选择求解器并执行
- L4: Evaluation - LLM提供解释/摘要/风险提示，PolicyEngine做最终决策

> 注：需要先启动Ollama服务（可选），否则使用模拟响应

## 核心功能

### 主流程：LLM驱动的5层工作流（推荐）

系统采用5层LLM决策架构，作为唯一主流程：

| 层级 | 功能 | 职责 | 说明 |
|------|------|------|------|
| L0 预处理 | 输入标准化 | 将不同输入转换为 CanonicalDispatchRequest | 新增层 |
| L1 数据建模 | 生成事故卡片 | LLM辅助判断场景类型 | NetworkSnapshot由确定性逻辑切出 |
| L2 Planner | 技能意图决策 | LLM决策 planning_intent | 不直接选择求解器 |
| L3 Solver | 求解器选择与执行 | SolverPolicyAdapter 根据 intent 选择求解器 | L2/L3 分离 |
| L4 Evaluation | 评估解释与决策 | LLM提供解释，PolicyEngine做最终决策 | LLM不做最终决策 |

### 智能调度
- **自然语言输入**: 用自然语言描述调度需求，Agent自动识别场景
- **表单输入**: 传统表单方式配置延误场景
- **场景识别**: 自动识别临时限速、突发故障等场景
- **FCFS调度**: 先到先服务策略，快速响应
- **整数规划优化**: 使用MIP求解器生成最优调度方案

### Agent能力
- 基于Qwen大模型的场景理解（可选，需要配置模型路径）
- Skills模式的调度技能调用
- 规则引擎模式（无需大模型也可运行）

### 可视化
- 优化后时刻表展示
- 运行图对比

## 核心模块

### 1. LLM工作流引擎 (`railway_agent/llm_workflow_engine.py`)
- 5层LLM决策架构：预处理 → 数据建模 → Planner → 求解 → 评估
- 多轮对话支持（session_manager.py）
- RAG检索增强（rag_retriever.py）
- PolicyEngine 决策（policy_engine.py）

### 2. 预处理层 (`railway_agent/preprocessing/`)
- `request_adapter.py`: 统一不同输入源（自然语言/表单/JSON）
- `rule_extractor.py`: 优先使用正则/规则提取关键字段
- `alias_normalizer.py`: 使用 station_alias 和 train_id_mapping 做归一化
- `llm_extractor.py`: 仅补全规则未确定字段
- `incident_builder.py`: 组装 CanonicalDispatchRequest
- `completeness_gate.py`: 判断是否可进入 solver

### 3. 适配器层 (`railway_agent/adapters/`)
- 统一各模块接口：LLM、RAG、Skill、Solver、Validator、Evaluator、Response

### 4. 求解器 (`solver/`)
- `fcfs_scheduler.py`: FCFS调度器（快速响应）
- `fcfs_adapter.py`: FCFS适配器
- `mip_scheduler.py`: MIP求解器（优化策略）
- `mip_adapter.py`: MIP适配器
- `max_delay_first_scheduler.py`: 最大延误优先调度器
- `max_delay_first_adapter.py`: 最大延误优先适配器
- `solver_registry.py`: 求解器注册与选择

### 5. 调度技能 (`railway_agent/dispatch_skills.py`)
- `TemporarySpeedLimitSkill`: 临时限速场景
- `SuddenFailureSkill`: 突发故障场景
- `SectionBlockSkill`: 区间封锁场景

### 6. 数据模型 (`models/`)
- `common_enums.py`: 统一英文枚举（SceneTypeCode, FaultTypeCode等）
- `preprocess_models.py`: 预处理数据模型
- `data_loader.py`: 统一数据入口
- `data_models.py`: Pydantic模型定义
- `workflow_models.py`: 工作流数据模型

## 数据说明

### 统一数据入口
系统使用统一的 `data_loader.py` 作为唯一数据入口：

```
data/
├── station_alias.json           # 车站数据（优先）
├── plan_timetable.csv           # 时刻表（优先）
├── train_id_mapping.csv          # 列车ID映射
├── min_running_time_matrix.csv   # 区间最小运行时间
├── scenarios/                    # 场景数据
└── knowledge/                   # RAG知识库
```

### 数据规模
- **13个车站**: 北京西 → 安阳东
- **147列列车**: 真实高铁时刻表

## 技术栈

- **大模型**: ModelScope (Qwen/Qwen2.5-1.8B) - 比Ollama 0.5B/0.8B更强的本地模型
- **求解器**: PuLP + CBC (整数规划)
- **Web**: Flask + Pydantic
- **可视化**: Matplotlib

## 版本

- v3.2: 新增SnapshotBuilder，扩展CanonicalDispatchRequest，参数化PolicyEngine阈值
- v3.1: 新增L0预处理层，重构L1-L4层（分离skill意图和solver算法，LLM不做最终决策）
- v3.0: 重构为统一的4层LLM工作流，删除旧工作流代码
- v2.7: 新增多轮对话Web界面+RAG检索增强+Ollama本地模型集成
- v2.6: 新增LLM驱动的4层工作流引擎

## 许可证

MIT License
