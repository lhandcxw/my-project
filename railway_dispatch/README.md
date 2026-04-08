# 铁路调度Agent系统

基于大模型和整数规划的智能铁路调度优化系统（v4.1）。

## v4.1 更新日志

**架构修复**：
- 修复工作流流程问题：明确 L0 → SnapshotBuilder → L1 → L2 → L3 → L4 的正确流程
- SnapshotBuilder 成为唯一构建 NetworkSnapshot 的入口
- L1 数据建模层只负责构建 AccidentCard，不再构建 NetworkSnapshot
- 删除冗余代码和文件（v2、v2_fixed版本文件），只保留最新版本
- 更新所有文档以反映正确的架构

**工作流职责明确**：
- SnapshotBuilder: 确定性构建 NetworkSnapshot（观察窗口、候选列车、排除列车、求解窗口）
- L1 数据建模层: 只构建 AccidentCard（事故卡片）
- L2 Planner: 决策 planning_intent
- L3 Solver: 选择并执行求解器
- L4 Evaluation: 评估和最终决策

## v4.0 更新日志

**架构升级**：
- 新增 **Prompt管理系统**（PromptManager + PromptTemplate）：统一管理所有Prompt模板
- 新增 **工作流分层模块**（Layer1-Layer4）：将工作流引擎拆分为独立模块
- 新增 **LLM Prompt适配器**（llm_prompt_adapter.py）：统一LLM Prompt调用
- 新增 **微调支持**：数据收集、标注、导出功能
- 新增 **新版工作流引擎v2**（llm_workflow_engine_v2.py）：应用分层模块和适配器模式

**RAG增强**：
- 添加真实高铁调度领域知识
- 京广高铁网络信息（13站、147列）
- 详细调度约束（时间、空间、容量）
- 延误处理策略和风险提示
- 车站作业时间标准

**Prompt管理**：
- 统一管理所有Prompt模板
- 支持模板注册、检索、填充、验证
- 支持微调样本收集和导出
- 支持模板版本管理和少样本示例

**架构改进**：
- 应用适配器模式，提高代码可维护性
- 拆分大型文件，遵循单一职责原则
- 删除冗余代码和调试信息
- 保持向后兼容

## v3.2 更新内容

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
│   ├── knowledge/            # RAG知识库
│   └── fine_tuning/          # 微调数据（v4.0新增）
├── models/                   # 数据模型层
│   ├── common_enums.py       # 统一英文枚举（SceneTypeCode, FaultTypeCode等）
│   ├── preprocess_models.py  # 预处理数据模型
│   ├── prompts.py            # Prompt数据模型（v4.0新增）
│   ├── data_models.py       # Pydantic模型
│   ├── data_loader.py       # 统一数据加载器
│   └── workflow_models.py   # 工作流数据模型
├── railway_agent/           # Agent模块
│   ├── prompts/              # Prompt管理系统（v4.0新增）
│   │   ├── __init__.py
│   │   └── prompt_manager.py # Prompt管理器
│   ├── workflow/             # 工作流分层模块（v4.0新增）
│   │   ├── __init__.py
│   │   ├── layer1_data_modeling.py  # L1层：数据建模
│   │   ├── layer2_planner.py         # L2层：Planner
│   │   ├── layer3_solver.py          # L3层：Solver
│   │   └── layer4_evaluation.py      # L4层：Evaluation
│   ├── preprocessing/        # L0 预处理层
│   │   ├── request_adapter.py      # 请求适配器
│   │   ├── rule_extractor.py       # 规则提取器
│   │   ├── alias_normalizer.py     # 别名归一化器
│   │   ├── llm_extractor.py        # LLM提取器
│   │   ├── incident_builder.py     # 事故卡片构建器
│   │   └── completeness_gate.py    # 完整性门禁
│   ├── adapters/            # 适配器层
│   │   ├── llm_adapter.py          # LLM适配器
│   │   ├── llm_prompt_adapter.py   # LLM Prompt适配器（v4.0新增）
│   │   ├── rag_adapter.py          # RAG适配器
│   │   ├── skill_adapter.py        # Skill适配器
│   │   ├── solver_adapter.py       # Solver适配器
│   │   ├── validator_adapter.py    # Validator适配器
│   │   ├── evaluator_adapter.py    # Evaluator适配器
│   │   └── response_adapter.py     # 响应适配器
│   ├── preprocess_service.py       # 预处理服务入口
│   ├── policy_engine.py            # 策略引擎
│   ├── llm_workflow_engine.py      # 5层LLM工作流引擎（v3.2）
│   ├── llm_workflow_engine_v2.py   # 新版工作流引擎（v4.0）
│   ├── rag_retriever.py            # RAG检索增强（已增强）
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

### 3. 使用新版工作流引擎（v4.0推荐）

```python
from railway_agent.llm_workflow_engine_v2 import create_workflow_engine

# 创建引擎
engine = create_workflow_engine()

# 执行完整工作流
result = engine.execute_full_workflow(
    user_input="暴雨导致石家庄站限速80km/h",
    enable_rag=True
)

# 查看结果
print(f"成功: {result.success}")
print(f"消息: {result.message}")
```

### 4. 使用Prompt管理器（v4.0新特性）

```python
from railway_agent.prompts import get_prompt_manager
from models.prompts import PromptContext

# 获取Prompt管理器
prompt_manager = get_prompt_manager()

# 列出所有模板
templates = prompt_manager.list_templates()
for t in templates:
    print(f"{t.template_id}: {t.template_name}")

# 填充Prompt
context = PromptContext(
    request_id="test_001",
    user_input="暴雨导致石家庄站限速80km/h"
)

filled_prompt = prompt_manager.fill_template(
    template_id="l1_data_modeling",
    context=context,
    enable_rag=True
)
```

### 5. 收集微调数据（v4.0新特性）

```python
# 收集样本
sample = prompt_manager.collect_fine_tuning_sample(
    template_id="l1_data_modeling",
    context=context,
    expected_output={"scene_category": "临时限速", ...}
)

# 标注样本
sample.is_correct = True
sample.annotator = "expert_001"
sample.annotation_status = "completed"

# 导出数据
prompt_manager.export_fine_tuning_samples("fine_tuning_data.jsonl")
```

### 6. LLM多轮对话

点击「LLM多轮对话」标签页，体验5层LLM决策的完整流程：
- L0: 预处理 - 统一输入转换为标准请求
- **SnapshotBuilder**: 构建网络快照 - 确定性构建 NetworkSnapshot（唯一入口）
- L1: 数据建模 - 只构建 AccidentCard（事故卡片）
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
| SnapshotBuilder | 构建网络快照 | 确定性构建 NetworkSnapshot | 唯一构建入口 |
| L1 数据建模 | 生成事故卡片 | 只构建 AccidentCard | 不构建 NetworkSnapshot |
| L2 Planner | 技能意图决策 | LLM决策 planning_intent | 不直接选择求解器 |
| L3 Solver | 求解器选择与执行 | SolverPolicyAdapter 根据 intent 选择求解器 | L2/L3 分离 |
| L4 Evaluation | 评估解释与决策 | LLM提供解释，PolicyEngine做最终决策 | LLM不做最终决策 |

### v4.1 架构修复

#### 工作流流程修正
- 修正流程顺序：L0 → SnapshotBuilder → L1 → L2 → L3 → L4
- SnapshotBuilder 成为唯一构建 NetworkSnapshot 的入口
- L1 数据建模层只负责构建 AccidentCard，不再构建 NetworkSnapshot
- 明确各层职责，避免重复构建

#### 代码精简
- 删除所有 v2、v2_fixed 版本的冗余文件
- 只保留一个最新版本的代码
- 在原文件基础上更新，不创建新文件

### v4.0新特性

#### Prompt管理系统
- 统一管理所有Prompt模板
- 支持模板注册、检索、填充、验证
- 支持微调样本收集和导出
- 支持模板版本管理和少样本示例

#### 工作流分层模块
- Layer1: 数据建模层（独立模块）
- Layer2: Planner层（独立模块）
- Layer3: Solver执行层（独立模块）
- Layer4: 评估层（独立模块）

#### 微调支持
- 自动收集微调样本
- 支持专家标注和验证
- 导出为JSONL格式
- 为微调Qwen模型准备数据

#### 增强的RAG系统
- 真实高铁调度领域知识
- 京广高铁网络信息（13站、147列）
- 详细调度约束（时间、空间、容量）
- 延误处理策略和风险提示

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

### 1. Prompt管理系统（v4.0新增） (`railway_agent/prompts/`)
- `prompt_manager.py`: Prompt模板管理器
  - 模板注册、检索、填充
  - 输出验证
  - 微调样本收集和导出

### 2. 工作流分层模块（v4.0新增） (`railway_agent/workflow/`)
- `layer1_data_modeling.py`: 数据建模层（v4.1 修复）
  - LLM提取事故信息
  - 回退推断逻辑
  - **只构建 AccidentCard，不构建 NetworkSnapshot**
- `layer2_planner.py`: Planner层
  - LLM决策planning_intent
  - 基于规则构建skill_dispatch
- `layer3_solver.py`: Solver执行层
  - SolverPolicyAdapter选择求解器
  - 执行求解并返回结果
- `layer4_evaluation.py`: 评估层
  - LLM生成解释和风险提示
  - PolicyEngine做最终决策

### 3. 新版工作流引擎（v4.0新增） (`railway_agent/llm_workflow_engine_v2.py`)
- 应用分层模块和适配器模式
- 支持微调数据收集
- 代码精简，职责清晰

### 4. LLM Prompt适配器（v4.0新增） (`railway_agent/adapters/llm_prompt_adapter.py`)
- 连接Prompt管理器和LLM调用器
- 自动处理Prompt填充、LLM调用、结果解析
- 支持RAG增强和回退机制

### 5. LLM工作流引擎v3.2 (`railway_agent/llm_workflow_engine.py`)
- 5层LLM决策架构：预处理 → 数据建模 → Planner → 求解 → 评估
- 多轮对话支持（session_manager.py）
- RAG检索增强（rag_retriever.py）
- PolicyEngine 决策（policy_engine.py）

### 6. 预处理层 (`railway_agent/preprocessing/`)
- `request_adapter.py`: 统一不同输入源（自然语言/表单/JSON）
- `rule_extractor.py`: 优先使用正则/规则提取关键字段
- `alias_normalizer.py`: 使用 station_alias 和 train_id_mapping 做归一化
- `llm_extractor.py`: 仅补全规则未确定字段
- `incident_builder.py`: 组装 CanonicalDispatchRequest
- `completeness_gate.py`: 判断是否可进入 solver

### 7. 适配器层 (`railway_agent/adapters/`)
- 统一各模块接口：LLM、RAG、Skill、Solver、Validator、Evaluator、Response

### 8. 求解器 (`solver/`)
- `fcfs_scheduler.py`: FCFS调度器（快速响应）
- `fcfs_adapter.py`: FCFS适配器
- `mip_scheduler.py`: MIP求解器（优化策略）
- `mip_adapter.py`: MIP适配器
- `max_delay_first_scheduler.py`: 最大延误优先调度器
- `max_delay_first_adapter.py`: 最大延误优先适配器
- `solver_registry.py`: 求解器注册与选择

### 9. 调度技能 (`railway_agent/dispatch_skills.py`)
- `TemporarySpeedLimitSkill`: 临时限速场景
- `SuddenFailureSkill`: 突发故障场景
- `SectionBlockSkill`: 区间封锁场景

### 10. 数据模型 (`models/`)
- `common_enums.py`: 统一英文枚举（SceneTypeCode, FaultTypeCode等）
- `preprocess_models.py`: 预处理数据模型
- `prompts.py`: Prompt数据模型（v4.0新增）
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
├── knowledge/                   # RAG知识库
└── fine_tuning/                 # 微调数据（v4.0新增）
    ├── train.jsonl
    ├── validation.jsonl
    └── test.jsonl
```

### 数据规模
- **13个车站**: 北京西(BJX) → 杜家坎线路所(DJK) → 涿州东(ZBD) → 高碑店东(GBD) → 徐水东(XSD) → 保定东(BDD) → 定州东(DZD) → 正定机场(ZDJ) → 石家庄(SJP) → 高邑西(GYX) → 邢台东(XTD) → 邯郸东(HDD) → 安阳东(AYD)
- **147列列车**: 真实高铁时刻表

## 技术栈

- **大模型**: ModelScope (Qwen/Qwen2.5-1.8B) - 支持微调
- **求解器**: PuLP + CBC (整数规划)
- **Web**: Flask + Pydantic
- **可视化**: Matplotlib
- **Prompt管理**: 自定义PromptManager（v4.0）
- **RAG检索**: 关键词匹配（可升级为向量检索）

## 版本

- **v4.1** (2026-04-08):
  - 修复工作流流程：明确 L0 → SnapshotBuilder → L1 → L2 → L3 → L4
  - SnapshotBuilder 成为唯一构建 NetworkSnapshot 的入口
  - L1 只构建 AccidentCard，不再构建 NetworkSnapshot
  - 删除冗余文件（v2、v2_fixed 版本）
  - 更新所有文档以反映正确架构
  - **代码优化**：删除 6 个未使用的冗余文件
  - **问题修复**：修复 rag_retriever.py 和 prompt_manager.py 的语法/兼容性问题
  - 详见 [项目优化总结](../PROJECT_OPTIMIZATION_SUMMARY.md)

- **v4.0** (2026-04-07):
  - 新增Prompt管理系统（PromptManager + PromptTemplate）
  - 新增工作流分层模块（Layer1-Layer4独立模块）
  - 新增LLM Prompt适配器（llm_prompt_adapter.py）
  - 增强RAG系统，添加真实高铁调度知识
  - 新增微调支持（数据收集、标注、导出）
  - 创建新版工作流引擎v2（精简版）
  - 优化架构，应用适配器模式

- **v3.2**:
  - 新增SnapshotBuilder，扩展CanonicalDispatchRequest，参数化PolicyEngine阈值

- **v3.1**:
  - 新增L0预处理层，重构L1-L4层（分离skill意图和solver算法，LLM不做最终决策）

- **v3.0**:
  - 重构为统一的4层LLM工作流，删除旧工作流代码

- **v2.7**:
  - 新增多轮对话Web界面+RAG检索增强+Ollama本地模型集成

- **v2.6**:
  - 新增LLM驱动的4层工作流引擎

## 向后兼容性

- 保留旧版工作流引擎 `llm_workflow_engine.py`
- 新版引擎 `llm_workflow_engine_v2.py` 可以与旧版共存
- Web层无需修改，可以继续使用旧接口
- 逐步迁移到新版架构

## 后续规划

### 短期（1-2周）
- 测试新版工作流引擎
- 收集高质量微调样本
- 完善领域知识库

### 中期（1-2月）
- 使用收集的样本微调Qwen模型
- 评估微调效果
- 优化Prompt模板

### 长期（3-6月）
- 升级RAG为向量检索
- 支持多模型切换
- 进行A/B测试验证效果
- 性能优化

## 文档

- [详细架构设计文档](../../railway_dispatch_agent_architecture.md)
- [架构重构总结](../../REFACTORING_SUMMARY.md)
- [求解器文档](solver/README.md)
- [规则验证文档](rules/README.md)

## 许可证

MIT License
