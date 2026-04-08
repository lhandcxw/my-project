# 铁路调度Agent系统架构设计文档

## 文档概述

基于大模型和整数规划的智能铁路调度Agent系统（v2.0）。

**设计约束**：
- 部署规模：13站，147列列车（京广高铁北京西→安阳东）
- 建模方法：整数规划（MIP）+ 先到先服务（FCFS）+ 最大延误优先
- Web框架：Flask
- 大模型：ModelScope (Qwen/Qwen2.5-1.8B) - 本地模型，支持微调
- 数据模式：统一使用 `data/` 目录下的真实数据
- schema_version: dispatch_v2_0
- **v2.0新特性**：架构精简、模块化设计、完全兼容、代码量减少73%

**v2.0迁移完成**：
- ✅ 完全替代旧架构（2026-04-08完成）
- ✅ 代码量减少73%（净减少3345行）
- ✅ 所有功能测试通过
- ✅ 接口100%兼容
- ✅ 详见：MIGRATION_COMPLETE.md

---

## 1. 系统整体架构

### 1.1 架构分层设计（v2.0 - 精简架构 + 模块化 + 完全兼容）

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Web层 (web/app.py)                              │
│  - 智能调度: /api/agent_chat, /api/dispatch                            │
│  - 多轮对话: /api/workflow/start, /api/workflow/next                    │
│  - 调度比较: /api/scheduler_comparison                                 │
│  - 预处理调试: /api/preprocess_debug                                    │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│               Agent层 (railway_agent/agents.py)                       │
│  - NewArchAgent: 新架构Agent（兼容RuleAgent接口）                     │
│  - 场景识别、实体提取、推理构建                                         │
│  - 技能执行调度                                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│             技能层 (railway_agent/adapters/skills.py)                  │
│  - BaseDispatchSkill: 技能基类                                          │
│  - TemporarySpeedLimitSkill: 临时限速技能                               │
│  - SuddenFailureSkill: 突发故障技能                                    │
│  - SectionInterruptSkill: 区间中断技能                                  │
│  - GetTrainStatusSkill: 列车状态查询                                    │
│  - QueryTimetableSkill: 时刻表查询                                      │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│        技能注册表 (railway_agent/adapters/skill_registry.py)          │
│  - SkillRegistry: 管理和执行技能                                        │
│  - ToolRegistry: 兼容旧接口                                             │
│  - 提供JSON Schema和工具执行接口                                       │
└─────────────────────────────────────────────────────────────────────────┘
│  - 模板注册、检索、填充、验证                                          │
│  - 微调样本收集和导出                                                  │
│  * 支持模板版本管理和少样本示例                                        │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│      工作流分层模块 (railway_agent/workflow/)                        │
│  - layer1_data_modeling.py: 数据建模层（修正版）                  │
│    * LLM提取事故信息 + 回退推断逻辑                                   │
│    * 只构建 AccidentCard，不构建 NetworkSnapshot                          │
│  - layer2_planner.py: Planner层                                      │
│    * LLM决策planning_intent                                          │
│    * 基于规则构建skill_dispatch                                     │
│  - layer3_solver.py: 求解技能层                                      │
│    * SolverPolicyAdapter选择求解器                                     │
│    * 执行求解并返回结果                                              │
│  - layer4_evaluation.py: 评估层                                      │
│    * LLM生成解释和风险提示                                            │
│    * PolicyEngine做最终决策                                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│        工作流引擎 v2.1 (llm_workflow_engine_v2.py)           │
│  - 正确的流程：L0 → SnapshotBuilder → L1 → L2 → L3 → L4              │
│  - 应用分层模块和适配器模式                                            │
│  - LLM Prompt适配器统一LLM调用                                        │
│  - RAG检索增强（真实高铁调度知识）                                    │
│  - 支持微调数据收集                                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                    适配器层 (railway_agent/adapters/)                  │
│  - llm_adapter.py: LLM调用适配器（基础）                            │
│  - llm_prompt_adapter.py: LLM Prompt适配器（新增）                    │
│  - rag_adapter.py: RAG检索适配器（已增强）                            │
│  - skill_adapter.py: Skill调用适配器                                   │
│  - solver_adapter.py: 求解器适配器                                   │
│  - validator_adapter.py: 约束验证适配器                              │
│  - evaluator_adapter.py: 方案评估适配器                               │
│  - response_adapter.py: 响应格式化适配器                              │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                         求解器层 (solver/)                              │
│  - fcfs_scheduler.py + fcfs_adapter.py: FCFS调度器（快速响应）         │
│  - mip_scheduler.py + mip_adapter.py: MIP求解器（优化策略）            │
│  - max_delay_first_scheduler.py + max_delay_first_adapter.py: 最大延误优先│
│  - noop_scheduler.py + noop_adapter.py: 空操作调度器                   │
│  - solver_registry.py: 求解器注册与自动选择                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                      验证与评估层 (rules/, evaluation/)                 │
│  - rules/validator.py: 约束验证器（失败时LLM无权覆盖）                  │
│  - evaluation/evaluator.py: 方案评估（只做评估，最终决策由PolicyEngine） │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                         数据模型层 (models/)                           │
│  - common_enums.py: 统一英文枚举（SceneTypeCode, FaultTypeCode等）     │
│  - preprocess_models.py: 预处理数据模型                                 │
│  - prompts.py: Prompt数据模型（新增）                                  │
│  - workflow_models.py: 工作流数据模型                                   │
│  - data_loader.py: 统一数据入口                                         │
│  - data_models.py: Pydantic数据模型                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心模块

### 2.0 Prompt管理系统 (`railway_agent/prompts/`)

**v4.0新增**：统一的Prompt管理系统，为微调提供支持

**核心组件**：

1. **PromptManager** - Prompt管理器
   - 模板注册：`register_template()`
   - 模板检索：`get_template()`, `list_templates()`
   - 模板填充：`fill_template()`
   - 输出验证：`validate_output()`
   - 样本收集：`collect_fine_tuning_sample()`
   - 数据导出：`export_fine_tuning_samples()`

2. **PromptTemplate** - Prompt模板模型
   ```python
   PromptTemplate(
       template_id: str,
       template_type: PromptTemplateType,
       system_prompt: Optional[str],
       user_prompt_template: str,
       output_format: Optional[str],
       required_output_fields: List[str],
       output_schema: Optional[Dict],
       examples: List[Dict]
   )
   ```

3. **PromptContext** - Prompt上下文模型
   ```python
   PromptContext(
       request_id: str,
       user_input: Optional[str],
       scene_type: Optional[str],
       canonical_request: Optional[Dict],
       accident_card: Optional[Dict],
       network_snapshot: Optional[Dict],
       solver_result: Optional[Dict]
   )
   ```

**内置模板**：
- `l0_preprocess_extractor`: L0预处理提取器
- `l1_data_modeling`: L1数据建模
- `l2_planner`: L2规划器
- `l4_evaluation`: L4评估

**使用示例**：
```python
from railway_agent.prompts import get_prompt_manager
from models.prompts import PromptContext

prompt_manager = get_prompt_manager()

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

# 收集微调样本
sample = prompt_manager.collect_fine_tuning_sample(
    template_id="l1_data_modeling",
    context=context,
    expected_output={"scene_category": "临时限速", ...}
)

# 导出微调数据
prompt_manager.export_fine_tuning_samples("fine_tuning_data.jsonl")
```

### 2.1 L0 预处理层 (`railway_agent/preprocessing/`)

**处理流程**：
1. `request_adapter.py` - 将不同输入源统一转换为 RawUserRequest
2. `rule_extractor.py` - 优先使用正则/规则提取 train_id/station/delay/time/speed_limit
3. `alias_normalizer.py` - 使用 station_alias 和 train_id_mapping 做归一化
4. `llm_extractor.py` - 仅补全规则未确定字段，必须输出严格 JSON
5. `incident_builder.py` - 组装 CanonicalDispatchRequest
6. `completeness_gate.py` - 判断是否可进入 solver

**关键约束**：
- 所有下游模块只接收 CanonicalDispatchRequest，不再接收自然语言

### 2.2 L1 数据建模层 (`railway_agent/workflow/layer1_data_modeling.py`)

**v4.0改进**：独立为单独模块，使用Prompt适配器

**核心功能**：
1. 通过LLM Prompt适配器提取字段（scene_category, fault_type, location_code, affected_train_ids 等）
2. **回退推断逻辑**：当 LLM 输出缺失关键字段时，从用户输入回退
   - 列车号：正则提取 `G\d+` 格式
   - 故障类型：关键字匹配（风→大风，雨→暴雨）
   - 场景类别：关键字匹配（限速→临时限速，封锁→区间封锁）
   - 车站信息：映射表匹配，转换为站码
3. **完整性判定**：列车号 + 位置 + 事件类型三者都有才可进入后续层
4. 确定性逻辑构建 NetworkSnapshot（不受 LLM 输出影响）

**输出字段**：
- `AccidentCard`: scene_category, fault_type, location_code, affected_train_ids, is_complete, missing_fields
- `NetworkSnapshot`: observation_corridor, train_count
- `DispatchContextMetadata`: can_solve, missing_info

### 2.3 L2 Planner层 (`railway_agent/workflow/layer2_planner.py`)

**v4.0改进**：独立为单独模块，使用Prompt适配器

**核心功能**：
1. 通过LLM Prompt适配器决策 planning_intent
2. **不直接选择求解器**，仅决策意图
3. **基于规则构建skill_dispatch**（不依赖LLM）
   - 临时限速 → mip
   - 突发故障 → fcfs
   - 区间封锁 → noop

**输出字段**：
- `planning_intent`: 问题类型与处理意图
- `skill_dispatch`: 技能分发信息（主技能、调用顺序等）
- `问题描述`: 场景描述
- `建议窗口`: 推荐的观察窗口

### 2.4 L3 Solver执行层 (`railway_agent/workflow/layer3_solver.py`)

**v4.0改进**：独立为单独模块

**核心功能**：
1. SolverPolicyAdapter 根据 planning_intent、场景类型、列车数量选择求解器
2. 执行求解器并返回结果
3. 计算延误指标（总延误、最大延误）

**求解器选择规则**：
- 区间封锁 → noop
- 信息不完整 → fcfs
- 列车数量少（≤3）且完整 → mip
- 列车数量多（>10）→ fcfs
- 默认 → mip

### 2.5 L4 评估层 (`railway_agent/workflow/layer4_evaluation.py`)

**v4.0改进**：独立为单独模块，使用Prompt适配器

**核心功能**：
1. 通过LLM Prompt适配器生成解释和风险提示
2. PolicyEngine 根据评估结果做最终决策
3. 构建回退反馈

**决策规则（优先级从高到低）**：
1. 求解失败 → RERUN
2. 验证失败 → FALLBACK
3. 评估不可行 → FALLBACK
4. 最大延误过大（阈值可配置）→ FALLBACK
5. 有严重风险警告 → FALLBACK
6. LLM 建议回退 → 仅供参考，不能覆盖决策
7. 默认 → ACCEPT

### 2.6 LLM Prompt适配器 (`railway_agent/adapters/llm_prompt_adapter.py`)

**v4.0新增**：统一LLM Prompt调用

**核心功能**：
1. 连接Prompt管理器和LLM调用器
2. 自动处理Prompt填充、LLM调用、结果解析
3. 支持RAG增强和回退机制
4. 自动收集微调样本

**使用示例**：
```python
from railway_agent.adapters.llm_prompt_adapter import get_llm_prompt_adapter
from models.prompts import PromptContext

adapter = get_llm_prompt_adapter()

response = adapter.execute_prompt(
    template_id="l2_planner",
    context=PromptContext(
        request_id="test_001",
        accident_card={...},
        enable_rag=True
    )
)

print(response.is_valid)
print(response.parsed_output)
print(response.model_used)
```

### 2.7 增强的RAG系统 (`railway_agent/rag_retriever.py`)

**v4.0改进**：添加真实高铁调度知识

**新增知识模块**：
1. **京广高铁网络信息**
   - 13个车站的完整信息
   - 147列列车运行特点
   - 关键区间（XSD-BDD、BDD-DZD等）
   - 典型故障位置

2. **调度约束**
   - 时间约束（到发时间、停站时间）
   - 空间约束（安全间隔、追踪间隔）
   - 容量约束（车站股道、站台占用）
   - 列车运行时间（最小运行时间、图定运行时间）

3. **延误处理策略**
   - 延误分类（轻微、中等、严重）
   - 延误恢复策略（顺延、压缩、越行、避让）
   - 延误传播控制
   - 风险提示

4. **车站作业时间标准**
   - 高速列车停站时间：2-3分钟
   - 动车组停站时间：3-5分钟
   - 最小停站时间：2分钟

**检索算法**：
- 基于关键词匹配
- 场景类型匹配（临时限速、突发故障、区间封锁）
- 求解器匹配（mip、fcfs）
- 车站代码匹配

### 2.8 工作流引擎v2 (`railway_agent/llm_workflow_engine_v2.py`)

**v4.0新增**：应用分层模块和适配器模式的精简版引擎

**核心特性**：
- 使用分层模块（Layer1-Layer4）
- 应用LLM Prompt适配器
- 支持微调数据收集
- 代码量大幅减少（对比v3.2的1509行）
- 职责清晰，易于维护

**使用示例**：
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
print(result.success)
print(result.message)
print(result.debug_trace)
```

---

## 3. 数据模型

### 3.1 新增Prompt数据模型 (`models/prompts.py`)

**v4.0新增**：Prompt相关数据模型

```python
class PromptTemplate(BaseModel):
    """Prompt模板模型"""
    template_id: str
    template_type: PromptTemplateType
    template_name: str
    description: str
    system_prompt: Optional[str]
    user_prompt_template: str
    output_format: Optional[str]
    model_name: Optional[str]
    temperature: float
    max_tokens: int
    required_output_fields: List[str]
    output_schema: Optional[Dict[str, Any]]
    examples: List[Dict[str, Any]]
    tags: List[str]
    version: str
    metadata: Dict[str, Any]

class PromptContext(BaseModel):
    """Prompt上下文模型"""
    request_id: str
    scene_type: Optional[str]
    scene_category: Optional[str]
    user_input: Optional[str]
    source_type: Optional[str]
    canonical_request: Optional[Dict[str, Any]]
    accident_card: Optional[Dict[str, Any]]
    network_snapshot: Optional[Dict[str, Any]]
    solver_result: Optional[Dict[str, Any]]
    rag_knowledge: Optional[List[str]]
    variables: Dict[str, Any]
    metadata: Dict[str, Any]

class FineTuningSample(BaseModel):
    """微调样本模型"""
    sample_id: str
    template_id: str
    input_context: PromptContext
    user_input: str
    expected_output: Dict[str, Any]
    model_output: Optional[Dict[str, Any]]
    is_correct: Optional[bool]
    scenario_type: Optional[str]
    difficulty: Optional[Literal["easy", "medium", "hard"]]
    annotation_status: Literal["pending", "in_progress", "completed", "rejected"]
    annotator: Optional[str]
    tags: List[str]
    notes: Optional[str]
    metadata: Dict[str, Any]
```

### 3.2 统一枚举 (`models/common_enums.py`)

```python
class SceneTypeCode(str, Enum):
    TEMP_SPEED_LIMIT = "TEMP_SPEED_LIMIT"    # 临时限速
    SUDDEN_FAILURE = "SUDDEN_FAILURE"          # 突发故障
    SECTION_INTERRUPT = "SECTION_INTERRUPT"   # 区间封锁

class FaultTypeCode(str, Enum):
    RAIN = "RAIN"                    # 暴雨
    WIND = "WIND"                    # 大风
    SNOW = "SNOW"                    # 降雪
    EQUIPMENT_FAILURE = "EQUIPMENT_FAILURE"  # 设备故障
    SIGNAL_FAILURE = "SIGNAL_FAILURE"        # 信号故障
    CATENARY_FAILURE = "CATENARY_FAILURE"    # 接触网故障

class SolverTypeCode(str, Enum):
    MIP = "mip_scheduler"
    FCFS = "fcfs_scheduler"
    MAX_DELAY_FIRST = "max_delay_first_scheduler"
    NOOP = "noop_scheduler"

class PolicyDecisionType(str, Enum):
    ACCEPT = "accept"           # 采用主解
    FALLBACK = "fallback"       # 回退基线
    RERUN = "rerun"             # 重新求解
```

---

## 4. 微调支持

### 4.1 微调数据收集流程

**步骤1**：执行工作流并收集样本
```python
from railway_agent.prompts import get_prompt_manager
from models.prompts import PromptContext

prompt_manager = get_prompt_manager()

# 构建上下文
context = PromptContext(
    request_id="sample_001",
    user_input="暴雨导致石家庄站限速80km/h",
    scene_type="临时限速"
)

# 收集样本（专家标注正确答案）
sample = prompt_manager.collect_fine_tuning_sample(
    template_id="l1_data_modeling",
    context=context,
    expected_output={
        "scene_category": "临时限速",
        "fault_type": "暴雨",
        "location_code": "SJP",
        "affected_train_ids": ["G1563"],
        "is_complete": True
    }
)
```

**步骤2**：标注样本
```python
sample.is_correct = True
sample.annotator = "expert_001"
sample.annotation_status = "completed"
sample.difficulty = "medium"
sample.tags = ["临时限速", "暴雨", "石家庄"]
```

**步骤3**：批量收集后导出
```python
# 导出为JSONL格式（标准微调格式）
prompt_manager.export_fine_tuning_samples("data/fine_tuning.jsonl")
```

### 4.2 微调数据格式

导出的JSONL格式：
```json
{
  "sample_id": "uuid",
  "template_id": "l1_data_modeling",
  "input_context": {
    "request_id": "sample_001",
    "user_input": "暴雨导致石家庄站限速80km/h",
    "scene_type": "临时限速"
  },
  "user_input": "暴雨导致石家庄站限速80km/h",
  "expected_output": {
    "scene_category": "临时限速",
    "fault_type": "暴雨",
    "location_code": "SJP",
    "affected_train_ids": ["G1563"]
  },
  "model_output": {...},
  "is_correct": true,
  "scenario_type": "临时限速",
  "difficulty": "medium",
  "annotation_status": "completed",
  "annotator": "expert_001"
}
```

### 4.3 微调流程

1. **数据收集阶段**
   - 使用PromptManager收集高质量样本
   - 专家标注和验证
   - 按场景类型和难度分类

2. **数据导出阶段**
   - 导出为JSONL格式
   - 数据清洗和去重
   - 训练集/验证集划分

3. **模型微调阶段**
   ```bash
   # 使用微调数据训练Qwen模型
   python finetune_qwen.py \
       --model_name Qwen/Qwen2.5-1.8B \
       --train_data data/fine_tuning.jsonl \
       --output_dir models/qwen_dispatch_finetuned
   ```

4. **模型评估阶段**
   - 在测试集上评估微调效果
   - 对比微调前后模型性能
   - 调整超参数

5. **模型部署阶段**
   - 替换工作流引擎中的模型
   - 持续监控和优化

---

## 5. 数据说明

### 5.1 数据目录结构

```
data/
├── station_alias.json           # 车站数据（优先使用）
├── plan_timetable.csv            # 原始时刻表（优先使用）
├── min_running_time_matrix.csv   # 区间最小运行时间
├── train_id_mapping.csv          # 列车ID映射
├── scenarios/                    # 场景数据
│   ├── sudden_failure.json
│   └── temporary_speed_limit.json
├── knowledge/                   # RAG知识库
└── fine_tuning/                 # 微调数据（v4.0新增）
    ├── train.jsonl
    ├── validation.jsonl
    └── test.jsonl
```

### 5.2 数据规模

- **13个车站**: 北京西(BJX) → 杜家坎线路所(DJK) → 涿州东(ZBD) → 高碑店东(GBD) → 徐水东(XSD) → 保定东(BDD) → 定州东(DZD) → 正定机场(ZDJ) → 石家庄(SJP) → 高邑西(GYX) → 邢台东(XTD) → 邯郸东(HDD) → 安阳东(AYD)
- **147列列车**: 真实高铁时刻表数据

---

## 6. 接口说明

### 6.1 Web接口

| 接口 | 方法 | 功能 |
|------|------|------|
| `/api/agent_chat` | POST | 智能调度（Agent聊天） |
| `/api/dispatch` | POST | 表单方式调度 |
| `/api/workflow/start` | POST | 启动LLM多轮工作流 |
| `/api/workflow/next` | POST | 继续执行下一层 |
| `/api/workflow/reset` | POST | 重置会话 |
| `/api/workflow/status` | GET | 获取当前状态 |
| `/api/scheduler_comparison` | POST | 调度器对比 |
| `/api/preprocess_debug` | POST | 预处理调试 |

---

## 7. 技术栈

- **大模型**: ModelScope (Qwen/Qwen2.5-1.8B) - 支持微调
- **求解器**: PuLP + CBC (整数规划)
- **Web**: Flask + Pydantic
- **可视化**: Matplotlib
- **Prompt管理**: 自定义PromptManager
- **RAG检索**: 关键词匹配（可升级为向量检索）

---

## 8. 版本历史

- **v4.0** (2026-04-07):
  - 新增Prompt管理系统（PromptManager + PromptTemplate）
  - 新增工作流分层模块（Layer1-Layer4独立模块）
  - 新增LLM Prompt适配器（llm_prompt_adapter.py）
  - 增强RAG系统，添加真实高铁调度知识
  - 新增微调支持（数据收集、标注、导出）
  - 创建新版工作流引擎v2（精简版）
  - 优化架构，应用适配器模式

- **v3.2**:
  - 新增SnapshotBuilder模块
  - 扩展CanonicalDispatchRequest字段
  - 参数化PolicyEngine阈值
  - ModelScope模型替换Ollama

- **v3.1**:
  - 新增L0预处理层
  - 重构L1-L4层（分离skill意图和solver算法，LLM不做最终决策）
  - 新增适配器层和策略引擎

- **v3.0**:
  - 重构为统一的4层LLM工作流
  - 删除旧工作流代码
  - 统一数据入口

- **v2.7**:
  - 新增多轮对话Web界面
  - RAG检索增强
  - Ollama本地模型集成

- **v2.6**:
  - 新增LLM驱动的4层工作流引擎

---

## 9. 快速开始

### 9.1 使用新版工作流引擎

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

### 9.2 使用Prompt管理器

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

### 9.3 收集微调数据

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

---

## 10. 向后兼容性

- 保留旧版工作流引擎 `llm_workflow_engine.py`
- 新版引擎 `llm_workflow_engine_v2.py` 可以与旧版共存
- Web层无需修改，可以继续使用旧接口
- 逐步迁移到新版架构

---

## 10. 架构修正说明（v4.1）

### 10.1 问题识别

v4.0架构存在一个关键问题：

**问题描述**：
> L0预处理层之后是SnapshotBuilder构建网络快照，到工作流分层模块之后数据建模层还是确定性构建网络快照

**根本原因**：
- 架构文档描述：L0 → SnapshotBuilder → L1 → L2 → L3 → L4
- 实际实现（v4.0）：L0 → L1（内部调用_build_network_snapshot）→ L2 → L3 → L4
- **问题**：NetworkSnapshot 被重复构建，职责不清晰

### 10.2 修正方案

**修正后的流程**（v4.1）：

```
L0 预处理层
    输出: CanonicalDispatchRequest
    ↓
SnapshotBuilder (独立模块)  ← 唯一构建 NetworkSnapshot 的入口
    输入: CanonicalDispatchRequest
    输出: NetworkSnapshot
    ↓
L1 数据建模层 (v2)  ← 只构建 AccidentCard
    输入: user_input, canonical_request
    输出: AccidentCard
    ↓
L2 Planner层
    输入: AccidentCard, NetworkSnapshot
    输出: planning_intent, skill_dispatch
    ↓
L3 Solver执行层
    输入: planning_intent, AccidentCard, NetworkSnapshot
    输出: SolverResult
    ↓
L4 评估层
    输入: skill_execution_result
    输出: EvaluationReport, PolicyDecision
```

### 10.3 职责划分

| 模块 | 职责 | 输入 | 输出 |
|------|------|------|------|
| L0 预处理层 | 输入标准化 | user_input | CanonicalDispatchRequest |
| SnapshotBuilder | 构建 NetworkSnapshot | CanonicalDispatchRequest | NetworkSnapshot |
| L1 数据建模层 v2 | 数据建模 | user_input | AccidentCard |
| L2 Planner层 | Planner决策 | AccidentCard, NetworkSnapshot | planning_intent, skill_dispatch |
| L3 Solver执行层 | 求解器执行 | planning_intent, AccidentCard, NetworkSnapshot | SolverResult |
| L4 评估层 | 评估与决策 | skill_execution_result | EvaluationReport, PolicyDecision |

### 10.4 修正后的文件

**新增文件**：
- `railway_agent/snapshot_builder_v2.py` - 独立的 SnapshotBuilder
- `railway_agent/workflow/layer1_data_modeling_v2.py` - 修正的 L1 层
- `railway_agent/llm_workflow_engine_v2_fixed.py` - 修正的工作流引擎

**关键改进**：
1. ✅ 消除了 NetworkSnapshot 的重复构建
2. ✅ 明确了 SnapshotBuilder 为唯一入口
3. ✅ 清晰了 L1 层的职责（只负责 AccidentCard）
4. ✅ 统一了架构文档与实际实现

### 10.5 使用修正后的工作流

```python
from railway_agent.llm_workflow_engine_v2_fixed import create_workflow_engine_v2_fixed

# 创建修正版工作流引擎
engine = create_workflow_engine_v2_fixed()

# 执行完整工作流
result = engine.execute_full_workflow(
    user_input="暴雨导致石家庄站限速80km/h",
    canonical_request=canonical_request,  # L0 预处理结果
    enable_rag=True
)
```

### 10.6 详细说明

完整的架构修正说明请参考：[ARCHITECTURE_FIX.md](../ARCHITECTURE_FIX.md)

---

## 11. 向后兼容性

- 保留旧版工作流引擎 `llm_workflow_engine.py`
- 保留v4.0版本 `llm_workflow_engine_v2.py`
- 新的v4.1修正版本使用 `_v2_fixed` 后缀
- 所有版本可以共存，无需修改现有代码
- 逐步迁移到修正后的架构

---

## 12. 后续规划

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
