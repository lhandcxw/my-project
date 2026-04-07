# 铁路调度Agent系统架构设计文档

## 文档概述

基于大模型和整数规划的智能铁路调度Agent系统（v3.2）。

**设计约束**：
- 部署规模：13站，147列列车（京广高铁北京西→安阳东）
- 建模方法：整数规划（MIP）+ 先到先服务（FCFS）+ 最大延误优先
- Web框架：Flask
- 大模型：ModelScope (Qwen/Qwen2.5-1.8B) - 本地模型，比Ollama 0.5B/0.8B更强
- 数据模式：统一使用 `data/` 目录下的真实数据
- schema_version: dispatch_v3_2

---

## 1. 系统整体架构

### 1.1 架构分层设计（v3.2 - L0预处理 + 4层工作流 + SnapshotBuilder）

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Web层 (web/app.py)                              │
│  - 智能调度: /api/agent_chat, /api/dispatch                            │
│  - 多轮对话: /api/workflow/start, /api/workflow/next                  │
│  - 调度比较: /api/scheduler_comparison                                 │
│  - 预处理调试: /api/preprocess_debug                                    │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│             L0 预处理层 (railway_agent/preprocessing/)                 │
│  - request_adapter.py: 统一不同输入源（自然语言/表单/JSON）             │
│  - rule_extractor.py: 优先使用正则/规则提取关键字段                    │
│  - alias_normalizer.py: 使用 station_alias 做归一化                   │
│  - llm_extractor.py: 仅补全规则未确定字段                               │
│  - incident_builder.py: 组装 CanonicalDispatchRequest                  │
│  - completeness_gate.py: 判断是否可进入 solver                         │
│  * 输出：CanonicalDispatchRequest (唯一可信主中间态)                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│        SnapshotBuilder (railway_agent/snapshot_builder.py)            │
│  * 确定性构建 NetworkSnapshot，不调用 LLM                              │
│  * 输入：CanonicalDispatchRequest + 结构化数据                        │
│  * 输出：candidate_train_ids, corridor_id, window_start/end          │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│      LLM驱动工作流层 (railway_agent/llm_workflow_engine.py)            │
│  - L1：数据建模层 - 基于 CanonicalDispatchRequest 构造 AccidentCard    │
│       * 不再从自然语言二次抽取字段，只做领域语义组织                    │
│  - L2：Planner层 - LLM决策 planning_intent（问题类型与处理意图）        │
│  - L3：Solver执行层 - SolverPolicyAdapter 根据 intent 选择求解器       │
│  - L4：评估层 - LLM解释/摘要/风险提示，PolicyEngine做最终决策          │
│  - policy_engine.py: 策略引擎（阈值参数化，根据配置决策）              │
│  - RAG检索增强：rag_retriever.py                                        │
│  - 会话管理：session_manager.py                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                    适配器层 (railway_agent/adapters/)                  │
│  - llm_adapter.py: LLM调用适配器                                        │
│  - rag_adapter.py: RAG检索适配器                                        │
│  - skill_adapter.py: Skill调用适配器                                     │
│  - solver_adapter.py: 求解器适配器                                       │
│  - validator_adapter.py: 约束验证适配器                                  │
│  - evaluator_adapter.py: 方案评估适配器                                 │
│  - response_adapter.py: 响应格式化适配器                                │
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
│  - data_loader.py: 统一数据入口                                         │
│  - data_models.py: Pydantic数据模型                                     │
│  - workflow_models.py: 工作流数据模型                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心模块

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

### 2.2 L1 数据建模层 (`llm_workflow_engine.py` - `layer1_data_modeling`)

**核心功能**：
1. LLM 提取字段（scene_category, fault_type, location_code, affected_train_ids 等）
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
- 只有 `CanonicalDispatchRequest.completeness.can_enter_solver=True` 时才进入求解器

### 2.2 LLM工作流引擎 (`railway_agent/llm_workflow_engine.py`)

**5层架构**：

| 层级 | 函数 | 功能 | 职责 |
|------|------|------|------|
| L0 | `PreprocessService` | 预处理 | 统一输入源转换为 CanonicalDispatchRequest |
| L1 | `layer1_data_modeling` | 数据建模 | LLM辅助判断场景类型，确定性逻辑切出NetworkSnapshot |
| L2 | `layer2_planner` | Planner | LLM决策 planning_intent（问题类型与处理意图） |
| L3 | `layer3_solver_execution` | Solver执行 | SolverPolicyAdapter 根据 intent 选择求解器并执行 |
| L4 | `layer4_evaluation` | 评估 | LLM解释/摘要/风险提示，PolicyEngine做最终决策 |

**关键类**：
- `LLMCaller`: LLM调用适配器，支持ModelScope远程模型(Qwen/Qwen2.5-1.8B)和Ollama本地模型
- `SolverPolicyAdapter`: 求解器策略选择（根据 planning_intent 选择求解器）
- `PolicyEngine`: 策略引擎（根据规则决策，LLM建议仅供参考）
- `session_manager.py`: 多轮会话管理

### 2.3 策略引擎 (`railway_agent/policy_engine.py`)

**决策规则（优先级从高到低）**：
1. 求解失败 → RERUN
2. 验证失败 → FALLBACK
3. 评估不可行 → FALLBACK
4. 最大延误过大（>30分钟）→ FALLBACK
5. 有严重风险警告 → FALLBACK
6. LLM 建议回退 → 仅供参考，不能覆盖决策
7. 默认 → ACCEPT

**关键约束**：
- validator 失败时，LLM 无权覆盖结果
- evaluator 只做评估，最终采用/回退由 policy_engine 决定

### 2.4 适配器层 (`railway_agent/adapters/`)

| 适配器 | 功能 |
|--------|------|
| llm_adapter.py | 统一 LLM 调用接口 |
| rag_adapter.py | 统一 RAG 检索接口 |
| skill_adapter.py | 统一 Skill 调用接口 |
| solver_adapter.py | 统一求解器调用接口 |
| validator_adapter.py | 统一约束验证接口 |
| evaluator_adapter.py | 统一方案评估接口 |
| response_adapter.py | 统一 API 响应格式 |

### 2.5 求解器层 (`solver/`)

| 调度器 | 文件 | 特性 |
|--------|------|------|
| FCFS | fcfs_scheduler.py + fcfs_adapter.py | 快速响应，毫秒级 |
| MIP | mip_scheduler.py + mip_adapter.py | 最优解，秒级 |
| 最大延误优先 | max_delay_first_scheduler.py + max_delay_first_adapter.py | 最小化最大延误 |
| 空操作 | noop_scheduler.py + noop_adapter.py | 区间封锁等场景 |

**适配器模式**：
- `solver_registry.py`: 求解器注册与自动选择

### 2.6 数据模型 (`models/`)

**统一枚举** (`common_enums.py`)：
```python
class SceneTypeCode(str, Enum):
    TEMP_SPEED_LIMIT = "TEMP_SPEED_LIMIT"    # 临时限速
    SUDDEN_FAILURE = "SUDDEN_FAILURE"          # 突发故障
    SECTION_INTERRUPT = "SECTION_INTERRUPT"   # 区间封锁

class FaultTypeCode(str, Enum):
    RAIN = "RAIN"                    # 暴雨
    EQUIPMENT_FAILURE = "EQUIPMENT_FAILURE"  # 设备故障
    SIGNAL_FAILURE = "SIGNAL_FAILURE"        # 信号故障
    CATENARY_FAILURE = "CATENARY_FAILURE"    # 接触网故障
```

**预处理模型** (`preprocess_models.py`)：
- `RawUserRequest`: 原始用户请求
- `CanonicalDispatchRequest`: 标准化调度请求
- `PlannerDecision`: Planner 决策
- `PolicyDecision`: Policy 决策
- `WorkflowResponse`: 工作流最终响应

---

## 3. 数据说明

### 3.1 数据目录结构

```
data/
├── station_alias.json           # 车站数据（优先使用）
├── plan_timetable.csv            # 原始时刻表（优先使用）
├── min_running_time_matrix.csv   # 区间最小运行时间
├── train_id_mapping.csv          # 列车ID映射
├── scenarios/                    # 场景数据
│   ├── sudden_failure.json
│   └── temporary_speed_limit.json
└── knowledge/                   # RAG知识库
```

### 3.2 数据规模

- **13个车站**: 北京西(BJX) → 杜家坎线路所(DJK) → 涿州东(ZBD) → 高碑店东(GBD) → 徐水东(XSD) → 保定东(BDD) → 定州东(DZD) → 正定机场(ZDJ) → 石家庄(SJP) → 高邑西(GYX) → 邢台东(XTD) → 邯郸东(HDD) → 安阳东(AYD)
- **147列列车**: 真实高铁时刻表数据

---

## 4. 接口说明

### 4.1 Web接口

| 接口 | 方法 | 功能 |
|------|------|------|
| `/api/agent_chat` | POST | 智能调度（Agent聊天） |
| `/api/dispatch` | POST | 表单方式调度 |
| `/api/workflow/start` | POST | 启动LLM多轮工作流 |
| `/api/workflow/next` | POST | 继续执行下一层 |
| `/api/workflow/reset` | POST | 重置会话 |
| `/api/workflow/status` | GET | 获取当前状态 |
| `/api/scheduler_comparison` | POST | 调度器对比 |
| `/api/preprocess_debug` | POST | 预处理调试（新增） |

### 4.2 场景类型枚举

**英文代码（内部使用）**：
```python
class SceneTypeCode(str, Enum):
    TEMP_SPEED_LIMIT = "TEMP_SPEED_LIMIT"    # 临时限速
    SUDDEN_FAILURE = "SUDDEN_FAILURE"          # 突发故障
    SECTION_INTERRUPT = "SECTION_INTERRUPT"   # 区间封锁
```

**中文标签（展示层使用）**：
- 临时限速 → TEMP_SPEED_LIMIT
- 突发故障 → SUDDEN_FAILURE
- 区间封锁 → SECTION_INTERRUPT

---

## 5. CanonicalDispatchRequest 数据结构

```python
class CanonicalDispatchRequest(BaseModel):
    schema_version: str = "dispatch_v3_2"
    request_id: str

    # 来源信息
    source_type: RequestSourceType  # natural_language / form / json
    raw_text: Optional[str]

    # 场景信息（英文 code + 中文 label）
    scene_type_code: Optional[SceneTypeCode]
    scene_type_label: Optional[str]
    fault_type: Optional[FaultTypeCode]

    # 位置信息
    location: Optional[LocationInfo]  # station_code, station_name, section_id

    # 列车信息
    affected_train_ids: List[str]
    reported_delay_seconds: Optional[int]
    speed_limit_kph: Optional[int]

    # 完整性判定
    completeness: CompletenessInfo  # can_enter_solver, missing_fields

    # 证据列表
    evidence: List[EvidenceInfo]

    # 置信度
    confidence: Optional[float]
```

---

## 6. 技术栈

- **大模型**: ModelScope (Qwen/Qwen2.5-1.8B) - 本地模型，比Ollama 0.5B/0.8B更强
- **求解器**: PuLP + CBC (整数规划)
- **Web**: Flask + Pydantic
- **可视化**: Matplotlib

---

## 7. 版本历史

- **v3.2**: 新增SnapshotBuilder模块，扩展CanonicalDispatchRequest字段，参数化PolicyEngine阈值，ModelScope模型替换Ollama
- **v3.1**: 新增L0预处理层，重构L1-L4层（分离skill意图和solver算法，LLM不做最终决策），新增适配器层和策略引擎
- **v3.0**: 重构为统一的4层LLM工作流，删除旧工作流代码，统一数据入口
- **v2.7**: 新增多轮对话Web界面+RAG检索增强+Ollama本地模型集成
- **v2.6**: 新增LLM驱动的4层工作流引擎