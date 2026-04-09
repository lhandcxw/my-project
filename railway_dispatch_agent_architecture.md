# 铁路调度Agent系统架构设计文档

## 文档概述

基于大模型和整数规划的智能铁路调度Agent系统（v5.0）。

**设计约束**：
- 部署规模：13站，147列列车（京广高铁北京西→安阳东）
- 建模方法：整数规划（MIP）+ 先到先服务（FCFS）+ 最大延误优先
- Web框架：Flask
- **大模型：阿里云 DashScope (qwen-max/qwen3.5-27b)** - 云端API
- 数据模式：统一使用 `data/` 目录下的真实数据
- schema_version: dispatch_v5_0

**v5.0更新**：
- 重构：删除所有冗余代码和重复workflow
- 重构：删除preprocessing模块（功能已合并到L1）
- 重构：删除comat.py（Python 3.8兼容补丁）
- 重构：删除6个冗余适配器文件
- 安全：移除硬编码API Key，改为config.py变量配置
- 功能：添加FORCE_LLM_MODE配置（正式实验时禁用规则回退）
- 功能：支持enable_thinking深度思考模式（仅qwen3/qwen-max系列）
- 重构：Agent层调用L1-L4完整工作流
- 重构：统一入口为web/app.py

---

## 1. 系统整体架构

### 1.1 架构分层设计

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Web层 (web/app.py)                              │
│  - 智能调度: /api/agent_chat, /api/dispatch                             │
│  - 多轮对话: /api/workflow/start, /api/workflow/next                    │
│  - 调度比较: /api/scheduler_comparison                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│               Agent层 (railway_agent/agents.py)                         │
│  - NewArchAgent: 新架构Agent（兼容RuleAgent接口）                       │
│  - 通过L1层LLM进行场景识别和实体提取                                    │
│  - 调用完整L1-L4工作流                                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│             技能层 (railway_agent/adapters/skills.py)                   │
│  - BaseDispatchSkill: 技能基类                                          │
│  - TemporarySpeedLimitSkill: 临时限速技能                               │
│  - SuddenFailureSkill: 突发故障技能                                     │
│  - SectionInterruptSkill: 区间中断技能                                  │
│  - GetTrainStatusSkill: 列车状态查询                                    │
│  - QueryTimetableSkill: 时刻表查询                                      │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│        技能注册表 (railway_agent/adapters/skill_registry.py)            │
│  - SkillRegistry: 管理和执行技能                                        │
│  - ToolRegistry: 兼容旧接口                                             │
│  - 提供JSON Schema和工具执行接口                                        │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│        工作流分层模块 (railway_agent/workflow/)                         │
│  - layer1_data_modeling.py: 数据建模层 (L1)                             │
│    * LLM提取事故信息，构建AccidentCard                                  │
│    * FORCE_LLM_MODE控制是否禁用规则回退                                 │
│  - layer2_planner.py: Planner层 (L2)                                    │
│    * LLM决策planning_intent                                             │
│    * 基于规则构建skill_dispatch                                         │
│  - layer3_solver.py: 求解技能层 (L3)                                    │
│    * SolverPolicyAdapter选择求解器                                      │
│    * 执行求解并返回结果                                                 │
│  - layer4_evaluation.py: 评估层 (L4)                                    │
│    * LLM生成解释和风险提示                                              │
│    * PolicyEngine做最终决策                                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│        工作流引擎 v2.2 (llm_workflow_engine_v2.py)                      │
│  - 正确的流程：SnapshotBuilder → L1 → L2 → L3 → L4                      │
│  - 支持多轮对话补全                                                     │
│  - 应用分层模块和适配器模式                                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                    适配器层 (railway_agent/adapters/)                   │
│  - llm_adapter.py: LLM调用适配器（支持DashScope/Ollama/OpenAI）        │
│  - llm_prompt_adapter.py: LLM Prompt适配器                              │
│  - skills.py: 技能实现                                                  │
│  - skill_registry.py: 技能注册表                                        │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                         求解器层 (solver/)                              │
│  - fcfs_scheduler.py: FCFS调度器（快速响应）                            │
│  - mip_scheduler.py: MIP求解器（优化策略）                              │
│  - max_delay_first_scheduler.py: 最大延误优先                           │
│  - noop_scheduler.py: 空操作调度器                                      │
│  - solver_registry.py: 求解器注册与自动选择                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                      验证与评估层 (rules/, evaluation/)                 │
│  - rules/validator.py: 约束验证器                                       │
│  - evaluation/evaluator.py: 方案评估                                    │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                         数据模型层 (models/)                            │
│  - common_enums.py: 统一英文枚举（SceneTypeCode, FaultTypeCode等）      │
│  - preprocess_models.py: 预处理数据模型                                 │
│  - prompts.py: Prompt数据模型                                           │
│  - workflow_models.py: 工作流数据模型                                   │
│  - data_loader.py: 统一数据入口                                         │
│  - data_models.py: Pydantic数据模型                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心模块

### 2.1 SnapshotBuilder - 网络快照构建器

**职责**：唯一构建 NetworkSnapshot 的入口，使用确定性逻辑

**输入**：
- CanonicalDispatchRequest：标准化调度请求
- time_window：可选的时间窗口
- window_size：观察窗口大小（默认2）

**输出**：
- NetworkSnapshot：确定性构建的网络快照

**文件位置**：`railway_agent/snapshot_builder.py`

### 2.2 L1 数据建模层

**职责**：从调度员描述中生成事故卡片，只构建 AccidentCard

**核心功能**：
1. 通过LLM Prompt适配器提取字段（scene_category, fault_type, location_code等）
2. **FORCE_LLM_MODE控制**：
   - `True`：LLM失败时直接报错，不回退到规则
   - `False`：LLM失败时回退到规则提取
3. **完整性判定**：列车号 + 位置 + 事件类型三者都有才可进入后续层

**输出字段**：
- `AccidentCard`: scene_category, fault_type, location_code, affected_train_ids, is_complete, missing_fields

**文件位置**：`railway_agent/workflow/layer1_data_modeling.py`

### 2.3 L2 Planner层

**职责**：决策规划意图和技能分发

**核心功能**：
1. 通过LLM Prompt适配器决策 planning_intent
2. **不直接选择求解器**，仅决策意图
3. **基于规则构建skill_dispatch**（不依赖LLM）
   - 临时限速 → mip
   - 突发故障 → fcfs
   - 区间封锁 → noop

**文件位置**：`railway_agent/workflow/layer2_planner.py`

### 2.4 L3 Solver执行层

**职责**：选择并执行求解器

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

**文件位置**：`railway_agent/workflow/layer3_solver.py`

### 2.5 L4 评估层

**职责**：评估调度方案并生成最终决策

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
6. 默认 → ACCEPT

**文件位置**：`railway_agent/workflow/layer4_evaluation.py`

### 2.6 LLM Prompt适配器

**职责**：统一LLM Prompt调用

**核心功能**：
1. 连接Prompt管理器和LLM调用器
2. 自动处理Prompt填充、LLM调用、结果解析
3. 支持RAG增强
4. 自动收集微调样本

**文件位置**：`railway_agent/adapters/llm_prompt_adapter.py`

### 2.7 RAG系统

**职责**：提供真实高铁调度知识检索

**知识模块**：
1. **京广高铁网络信息**：13个车站，147列列车
2. **调度约束**：时间、空间、容量约束
3. **延误处理策略**：延误分类、恢复策略
4. **车站作业时间标准**

**检索算法**：基于关键词匹配

**文件位置**：`railway_agent/rag_retriever.py`

---

## 3. 配置说明

### 3.1 配置文件 (config.py)

所有配置集中在 `config.py` 中，使用变量直接定义（非环境变量）：

```python
class LLMConfig:
    """LLM 配置"""
    PROVIDER = "dashscope"  # 可选: dashscope, ollama, openai
    DASHSCOPE_API_KEY = ""  # 阿里云API Key（直接填写）
    DASHSCOPE_MODEL = "qwen-max"  # 或 "qwen3.5-27b"
    DASHSCOPE_ENABLE_THINKING = True  # 是否启用深度思考（仅qwen3/qwen-max支持）
    FORCE_LLM_MODE = True  # 强制LLM模式，禁用规则回退

class AppConfig:
    """应用配置"""
    WEB_HOST = "0.0.0.0"
    WEB_PORT = 8081
    AGENT_MODE = "qwen"
    USE_REAL_DATA = True
```

### 3.2 模型选择建议

| 模型 | 是否支持思考模式 | 说明 |
|------|-----------------|------|
| qwen-max | ✅ 支持 | 推荐，效果最佳 |
| qwen3-72b | ✅ 支持 | 支持思考模式 |
| qwen3.5-27b | ❌ 不支持 | 性价比高但不支持思考 |
| qwen3.6-plus | ❓ 需测试 | 可能支持 |

---

## 4. 数据模型

### 4.1 统一枚举

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
```

### 4.2 数据目录结构

```
data/
├── station_alias.json           # 车站数据
├── plan_timetable.csv            # 原始时刻表
├── min_running_time_matrix.csv   # 区间最小运行时间
├── train_id_mapping.csv          # 列车ID映射
├── scenarios/                    # 场景数据
└── knowledge/                   # RAG知识库
```

### 4.3 数据规模

- **13个车站**: 北京西(BJX) → 杜家坎线路所(DJK) → 涿州东(ZBD) → 高碑店东(GBD) → 徐水东(XSD) → 保定东(BDD) → 定州东(DZD) → 正定机场(ZDJ) → 石家庄(SJP) → 高邑西(GYX) → 邢台东(XTD) → 邯郸东(HDD) → 安阳东(AYD)
- **147列列车**: 真实高铁时刻表数据

---

## 5. 接口说明

### 5.1 Web接口

| 接口 | 方法 | 功能 |
|------|------|------|
| `/api/agent_chat` | POST | 智能调度（Agent聊天） |
| `/api/dispatch` | POST | 表单方式调度 |
| `/api/workflow/start` | POST | 启动LLM多轮工作流 |
| `/api/workflow/next` | POST | 继续执行下一层 |
| `/api/workflow/reset` | POST | 重置会话 |
| `/api/workflow/status` | GET | 获取当前状态 |
| `/api/scheduler_comparison` | POST | 调度器对比 |

---

## 6. 技术栈

- **大模型**: 阿里云 DashScope (qwen-max/qwen3.5-27b)
- **备选大模型**: Ollama (qwen2.5:1.5b) - 本地模型
- **求解器**: PuLP + CBC (整数规划)
- **Web**: Flask + Pydantic
- **可视化**: Matplotlib
- **Prompt管理**: 自定义PromptManager
- **RAG检索**: 关键词匹配

---

## 7. 版本历史

- **v5.0** (2026-04-09):
  - 重构：删除所有冗余代码和重复workflow
  - 重构：删除preprocessing模块（7个文件）
  - 重构：删除comat.py
  - 重构：删除6个冗余适配器文件
  - 安全：移除硬编码API Key，改为config.py变量配置
  - 功能：添加FORCE_LLM_MODE配置
  - 功能：支持enable_thinking深度思考模式
  - 重构：Agent层调用完整L1-L4工作流
  - 重构：统一入口为web/app.py
  - 文档：更新README和架构文档

- **v4.2** (2026-04-08):
  - 阿里云 qwen3.5-27B 替换本地 Ollama
  - 支持 DashScope API 调用
  - 修复工作流流程

---

## 8. 快速开始

### 8.1 配置

编辑 `config.py`：

```python
# 填写阿里云API Key
DASHSCOPE_API_KEY = "your-actual-api-key"

# 选择模型
DASHSCOPE_MODEL = "qwen-max"  # 或 "qwen3.5-27b"

# 是否启用思考模式（仅qwen3/qwen-max支持）
DASHSCOPE_ENABLE_THINKING = True

# 是否强制LLM模式（禁用规则回退）
FORCE_LLM_MODE = True
```

### 8.2 启动Web服务

```bash
cd railway_dispatch
python web/app.py
```

访问 http://localhost:8081

### 8.3 使用工作流引擎

```python
from railway_agent.llm_workflow_engine_v2 import create_workflow_engine

# 创建引擎
engine = create_workflow_engine()

# 执行完整工作流
result = engine.execute_full_workflow(
    user_input="暴雨导致石家庄站限速80km/h",
    enable_rag=True
)

print(f"成功: {result.success}")
print(f"消息: {result.message}")
```

---

## 9. 注意事项

1. **LLM不做最终决策**：PolicyEngine 做最终决策，LLM 只提供解释和建议
2. **SnapshotBuilder 唯一入口**：所有 NetworkSnapshot 必须通过 SnapshotBuilder 构建
3. **L1 只构建 AccidentCard**：不再构建 NetworkSnapshot，避免职责重叠
4. **确定性逻辑优先**：Schedule、SnapshotBuilder、Solver选择使用确定性规则
5. **FORCE_LLM_MODE**：正式实验时设为True，确保数据质量
6. **思考模式支持**：仅qwen3系列和qwen-max支持enable_thinking

---

## 10. 后续规划

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
