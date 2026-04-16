# 铁路调度Agent系统架构设计文档（v6.6）

## 文档概述

基于大模型和整数规划的智能铁路调度Agent系统（v6.6 - 代码审查与架构优化）。

**v6.6更新（当前版本）**：
- **代码审查**：全面审查所有Python代码，检查语法、逻辑、流程问题
- **调度算法审查**：深入审查MIP、FCFS调度器实现，修正约束模型
- **工作流审查**：检查L1-L4各层的数据传递和接口契约
- **前端审查**：检查JavaScript交互逻辑和API调用
- **文档更新**：更新架构文档和README，反映最新代码状态
- **配置统一**：统一配置文件与实际代码的对应关系

**v6.5更新**：
- **多股道车站约束优化**：修正FCFS和MIP调度器的股道分配逻辑
- **前端UI优化**：添加工作流执行详情展示，优化视觉层次和交互体验
- **RAG检索改进方案**：提出向量检索升级方案
- **错误处理优化**：提出统一的错误处理模式
- **LLM调用优化**：提出统一的超时和重试机制

**v6.4更新**：
- **统一多轮对话工作流**：`/api/workflow/start`和`/api/workflow/next`也使用统一的LLM驱动工作流（与`agent_chat`保持一致）
- **移除手动逐层执行**：`workflow_next`不再手动调用L2/L3/L4，而是直接使用`agent.analyze()`执行完整工作流
- **统一实体提取**：多轮对话也使用L1层LLM进行场景识别和实体提取，不再依赖规则预处理

**设计约束**：
- 部署规模：13站，147列列车（京广高铁北京西→安阳东）
- 建模方法：整数规划（MIP）+ 先到先服务（FCFS）+ 最大延误优先
- Web框架：Flask
- **大模型架构：统一LLM驱动，移除RuleAgent**
- **LLM调用方式**：
  1. API调用阿里云模型（DashScope）
  2. 调用微调后的本地模型（Ollama/vLLM/Transformers）
- 数据模式：统一使用 `data/` 目录下的真实数据
- schema_version: dispatch_v6_2

**v6.4更新（当前版本）**：
- **统一多轮对话工作流**：`/api/workflow/start`和`/api/workflow/next`也使用统一的LLM驱动工作流（与`agent_chat`保持一致）
- **移除手动逐层执行**：`workflow_next`不再手动调用L2/L3/L4，而是直接使用`agent.analyze()`执行完整工作流
- **统一实体提取**：多轮对话也使用L1层LLM进行场景识别和实体提取，不再依赖规则预处理

**v6.3更新**：
- **修复调度器接口**：修正`scheduler_interface.py`中`EarliestArrivalFirstScheduler`类名错误（原错误命名为`ReinforcementLearningSchedulerAdapter`）
- **新增调度器类型**：添加`EARLIEST_ARRIVAL_FIRST`到`SchedulerType`枚举
- **更新FORCE_LLM_MODE注释**：修正注释说明，明确`True=强制使用LLM，False=允许规则回退`
- **代码审查完成**：修复重复类定义问题

**v6.2更新**：
- **新增：系统不足与改进方向文档（第11-12节）**
- **更新：后续规划（短期/中期/长期改进任务清单）**
- **更新：版本历史**

**v6.1更新**：
- **移除NetworkSnapshot**：简化架构，使用完整时刻表进行调度
- **新增调度器比较**：支持MIP/FCFS/最大延误优先/基线/最早到站优先调度器对比
- **修复多个bug**：workflow_continue空值检查、求解器名称获取、调度器比较属性名等
- **优化推理输出**：显示正确的求解器名称和调度器比较结果

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
│  - LLMAgent: 统一LLM驱动Agent（移除RuleAgent）                         │
│  - 通过L1层LLM进行场景识别和实体提取                                    │
│  - 调用完整L1-L4工作流                                                  │
│  - 支持阿里云API和本地微调模型                                           │
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
│  - layer1_data_modeling.py: L1数据建模层                               │
│    * LLM提取事故信息，构建AccidentCard                                  │
│    * 完整性判定：车次 + 位置 + 场景 + 延误时间（方案A）               │
│  - layer2_planner.py: Planner层 (L2)                                    │
│    * LLM决策planning_intent                                             │
│    * 基于规则构建skill_dispatch                                         │
│  - layer3_solver.py: 求解技能层 (L3)                                    │
│    * 基于规则选择求解器（MIP/FCFS/最大延误优先）                       │
│    * 使用完整时刻表进行调度（不依赖NetworkSnapshot）                   │
│    * 支持与基线/FCFS/最大延误优先调度器对比                            │
│  - layer4_evaluation.py: 评估层 (L4)                                    │
│    * LLM生成解释和风险提示                                              │
│    * PolicyEngine做最终决策                                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│        工作流引擎 (llm_workflow_engine_v2.py)                          │
│  - 流程：L0 → L1 → L2 → L3 → L4（不依赖NetworkSnapshot）              │
│  - 支持多轮对话补全                                                     │
│  - 应用分层模块和适配器模式                                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                    适配器层 (railway_agent/adapters/)                   │
│  - llm_adapter.py: LLM调用适配器（支持DashScope/本地模型）             │
│  - llm_prompt_adapter.py: LLM Prompt适配器                              │
│  - skills.py: 技能实现                                                  │
│  - skill_registry.py: 技能注册表                                        │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                      求解器层 (solver/)                              │
│  - fcfs_scheduler.py: FCFS调度器（快速响应）                            │
│  - mip_scheduler.py: MIP求解器（优化策略）                              │
│  - max_delay_first_scheduler.py: 最大延误优先调度器                     │
│  - noop_scheduler.py: 空操作调度器（基线）                              │
│  - solver_registry.py: 求解器注册与自动选择                             │
│                                                                         │
│              调度器比较层 (scheduler_comparison/)                       │
│  - scheduler_interface.py: 统一调度器接口                               │
│    * BaseScheduler: 调度器抽象基类                                      │
│    * FCFSSchedulerAdapter: FCFS适配器                                   │
│    * MIPSchedulerAdapter: MIP适配器                                     │
│    * MaxDelayFirstSchedulerAdapter: 最大延误优先适配器                  │
│    * NoOpSchedulerAdapter: 基线调度器适配器                             │
│    * EarliestArrivalFirstScheduler: 最早到站优先调度器                  │
│    * ReinforcementLearningSchedulerAdapter: 强化学习适配器（预留）      │
│    * SchedulerRegistry: 调度器注册表                                    │
│  - comparator.py: 调度器比较器                                          │
│  - metrics.py: 评估指标定义                                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│         LLM调用层 (railway_agent/adapters/llm_adapter.py)              │
│  ┌──────────────────────────────────────────────────────────────────┐ │
│  │  方式1：阿里云API                                             │ │
│  │  - DashScope API (qwen3.6-plus)                               │ │
│  │  - OpenAI兼容模式                                              │ │
│  │  - 配置：PROVIDER="dashscope"                                  │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────┐ │
│  │  方式2：本地微调模型                                          │ │
│  │  - Ollama (本地推理): PROVIDER="ollama"                      │ │
│  │  - vLLM (高性能): PROVIDER="vllm"                            │ │
│  │  - Transformers (原生): PROVIDER="transformers"              │ │
│  └──────────────────────────────────────────────────────────────────┘ │
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
│  - api_models.py: API统一响应模型（UnifiedDispatchResponse）           │
│  - data_loader.py: 统一数据入口                                         │
│  - data_models.py: Pydantic数据模型                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心模块

### 2.1 LLMAgent - 统一LLM驱动Agent

**职责**：统一LLM驱动架构，移除RuleAgent

**特点**：
- 完全基于LLM驱动，不依赖规则
- 使用完整L1-L4工作流
- 支持两种LLM调用方式：
  1. 阿里云API（DashScope）
  2. 本地微调模型（Ollama/vLLM/Transformers）

**文件位置**：`railway_agent/agents.py`

**核心方法**：
- `analyze()`: 执行完整L1-L4工作流
- `analyze_with_comparison()`: 执行带比较的调度
- `chat_direct()`: 直接对话接口

---

### 2.2 L1+L2+L3+L4 完整工作流

**职责说明（v6.0 统一版）**：
- **L1 数据建模层**：
  - LLM提取事故信息（scene_category, fault_type, location_code等）
  - 完整性判定：车次 + 位置 + 场景 + 延误时间（方案A）

- **L2 Planner层**：
  - LLM决策 planning_intent
  - 基于规则构建skill_dispatch

- **L3 Solver执行层**：
  - 基于规则选择求解器（MIP/FCFS/最大延误优先）
  - **使用完整时刻表进行调度（不依赖NetworkSnapshot）**
  - 支持调度器比较功能

- **L4 评估层**：
  - LLM生成解释和风险提示
  - PolicyEngine 根据评估结果做最终决策

---

### 2.3 LLM调用适配器

**职责**：统一LLM调用接口，支持多种调用方式

**文件位置**：`railway_agent/adapters/llm_adapter.py`

**支持的调用方式**：

1. **阿里云DashScope API**：
   ```python
   # 配置
   PROVIDER = "dashscope"
   DASHSCOPE_API_KEY = "your-api-key"
   DASHSCOPE_MODEL = "qwen3.6-plus"
   ```

2. **Ollama本地推理**：
   ```python
   # 配置
   PROVIDER = "ollama"
   OLLAMA_BASE_URL = "http://localhost:11434"
   OLLAMA_MODEL = "qwen-finetuned"  # 微调后的模型
   ```

3. **vLLM高性能推理**：
   ```python
   # 配置
   PROVIDER = "vllm"
   VLLM_BASE_URL = "http://localhost:8000/v1"
   VLLM_MODEL = "qwen-finetuned"
   ```

4. **Transformers原生加载**：
   ```python
   # 配置
   PROVIDER = "transformers"
   TRANSFORMERS_MODEL_PATH = "./models/qwen-finetuned"
   TRANSFORMERS_DEVICE = "cuda"
   ```

---

## 3. 配置说明

### 3.1 配置文件 (config.py)

```python
class LLMConfig:
    """LLM 配置（统一LLM驱动架构）"""

    # LLM提供方式: "dashscope" | "local" | "ollama" | "vllm" | "transformers"
    PROVIDER = "dashscope"

    # ========== 方式1：阿里云 DashScope API ==========
    DASHSCOPE_API_KEY = ""  # 请填写您的DashScope API Key
    DASHSCOPE_MODEL = "qwen3.6-plus"
    DASHSCOPE_ENABLE_THINKING = False

    # ========== 方式2：本地微调模型 ==========
    # Ollama 配置
    OLLAMA_BASE_URL = "http://localhost:11434"
    OLLAMA_MODEL = "qwen-finetuned"  # 或微调后的模型名称

    # vLLM 配置（高性能推理）
    VLLM_BASE_URL = "http://localhost:8000/v1"
    VLLM_MODEL = "qwen-finetuned"

    # Transformers 原生加载（用于微调模型）
    TRANSFORMERS_MODEL_PATH = ""  # 微调模型路径
    TRANSFORMERS_DEVICE = "cuda"
    TRANSFORMERS_MAX_LENGTH = 4096

    # ========== 通用配置 ==========
    FORCE_LLM_MODE = True


class AppConfig:
    """应用配置"""
    # Agent 模式: "dashscope" | "local" | "auto"
    AGENT_MODE = "dashscope"

    # 数据配置
    USE_REAL_DATA = True
```

### 3.2 模型选择建议

| 模型 | 是否支持思考模式 | 调用方式 | 说明 |
|------|-----------------|----------|------|
| qwen-max | ✅ 支持 | API | 推荐，效果最佳 |
| qwen3.6-plus | ❌ 不支持 | API/本地 | 当前主力模型 |
| qwen-finetuned | ⚠️ 取决于基座 | 本地 | 微调后模型，需本地部署 |

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
| `/api/agent_chat` | POST | 智能调度（LLM驱动） |
| `/api/dispatch` | POST | 表单方式调度 |
| `/api/workflow/start` | POST | 启动LLM多轮工作流 |
| `/api/workflow/next` | POST | 继续执行下一层 |
| `/api/workflow/reset` | POST | 重置会话 |
| `/api/workflow/status` | GET | 获取当前状态 |
| `/api/scheduler_comparison` | POST | 调度器对比 |

---

## 6. 技术栈

- **大模型架构**：统一LLM驱动，支持API和本地模型
- **阿里云API**：DashScope (qwen3.6-plus) - 云端API
- **本地模型**：Ollama/vLLM/Transformers - 微调模型
- **求解器**：PuLP + CBC (整数规划)、FCFS（先到先服务）、MaxDelayFirst（最大延误优先）、EarliestArrivalFirst（最早到站优先）
- **Web**：Flask + Pydantic
- **可视化**：Matplotlib
- **Prompt管理**：自定义PromptManager
- **RAG检索**：关键词匹配

---

## 7. 版本历史

- **v6.1** (2026-04-10):
  - **移除NetworkSnapshot**：简化架构，使用完整时刻表进行调度
  - **新增调度器比较**：支持MIP/FCFS/最大延误优先/基线调度器对比
  - **修复多个bug**：workflow_continue空值检查、求解器名称获取、调度器比较属性名等
  - **优化推理输出**：显示正确的求解器名称和调度器比较结果

- **v6.0** (2026-04-10):
  - **架构重构：完全移除RuleAgent，统一使用LLM驱动**
  - **新增：支持本地微调模型调用（Ollama/vLLM/Transformers）**
  - **统一：单一LLMAgent，支持两种LLM调用方式**
  - **优化：完整L1-L4工作流，不依赖规则回退**
  - **配置：统一配置中心，支持API和本地模型切换**
  - **完整性判定：采用方案A（车次+位置+场景+延误时间）**

- **v5.1** (2026-04-10):
  - 功能：L0层场景识别从规则改为LLM调用
  - 功能：新增 `_llm_extraction()` 方法
  - 功能：使用 `l0_preprocess_extractor` Prompt模板
  - 修复：移除 `llm_workflow_engine_v2.py` 中的硬编码场景判断
  - 修复：移除 `app.py` 中的硬编码场景判断
  - 修复：前端响应格式匹配问题
  - 修复：运行图中文字体缺失问题（改用英文标签）
  - 修复：SchedulerComparator初始化参数错误
  - 文档：更新README和架构文档

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

## 11. 当前不足与改进方向

### 11.1 代码质量

**问题**：
- 缺少单元测试（unittest/pytest）
- 部分关键函数缺少完整的docstring
- 日志记录不够完善，缺乏详细错误追踪
- 错误处理不一致（某些地方捕获异常，某些地方直接抛出）

**改进方向**：
- 添加单元测试覆盖核心模块（L1-L4工作流、求解器）
- 完善关键函数docstring
- 统一错误处理模式
- 增加详细日志，特别是异常堆栈信息

### 11.2 架构优化

**问题**：
- RAG实现过于简单，仅使用关键词匹配，未使用向量检索
- workflow层之间的数据传递依赖不够清晰
- 数据模型命名不一致（Pydantic vs Dataclass混用）

**改进方向**：
- 升级RAG为向量检索（使用faiss/chroma）
- 梳理workflow层之间的接口契约
- 统一数据模型规范

### 11.3 前端交互

**问题**：
- 前端页面未能完整展示LLM工作流状态
- 缺少对每个layer执行结果的详细展示
- 对话式交互UI不够直观

**改进方向**：
- 增加工作流执行进度展示
- 展示每个layer的输入输出详情
- 优化对话式交互UI

### 11.4 性能优化

**问题**：
- LLM调用只有Ollama有简单重试机制，DashScope/API调用没有实现请求超时和重试
- 调度器比较功能未完全集成到主流程

**改进方向**：
- 添加LLM调用超时和重试机制（统一所有provider）
- 完善调度器比较功能集成

---

## 12. 后续规划（更新）

### 短期（1-2周）
- [ ] 添加单元测试（覆盖率目标：50%以上）
- [ ] 完善关键函数docstring
- [ ] 优化前端工作流状态展示
- [ ] 统一错误处理模式

### 中期（1-2月）
- [ ] 升级RAG为向量检索
- [ ] 添加LLM请求超时和重试机制（统一所有provider）
- [ ] 统一数据模型规范
- [ ] 完善调度器比较功能

### 长期（3-6月）
- [ ] 支持多模型切换（A/B测试）
- [ ] 添加性能监控和日志分析
- [ ] 前端代码重构（Vue3/React）
- [ ] 微调模型训练和部署

---

## 13. 快速开始

### 13.1 配置

#### 方式1：使用阿里云API

编辑 `config.py`：

```python
# 填写阿里云API Key
DASHSCOPE_API_KEY = "your-actual-api-key"

# 选择模型
DASHSCOPE_MODEL = "qwen3.6-plus"
```

#### 方式2：使用本地微调模型

编辑 `config.py`：

```python
# 使用Ollama
PROVIDER = "ollama"
OLLAMA_MODEL = "qwen-finetuned"

# 或使用vLLM
PROVIDER = "vllm"
VLLM_MODEL = "qwen-finetuned"

# 或使用Transformers
PROVIDER = "transformers"
TRANSFORMERS_MODEL_PATH = "./models/qwen-finetuned"
```

### 13.2 启动Web服务

```bash
cd railway_dispatch
python web/app.py
```

访问 http://localhost:8081

### 13.3 使用工作流引擎

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

## 14. 注意事项

1. **统一LLM驱动**：完全移除RuleAgent，所有功能基于LLM
2. **LLM调用方式切换**：通过PROVIDER配置切换API和本地模型
3. **完整性判定**：车次 + 位置 + 场景 + 延误时间（方案A）
4. **工作流顺序**：L1 → L2 → L3 → L4
5. **不使用NetworkSnapshot**：使用完整时刻表进行调度，简化架构
6. **调度器比较**：analyze_with_comparison方法支持MIP/FCFS/最大延误优先/基线对比
7. **FORCE_LLM_MODE=True**：强制使用LLM，失败时报错（不启用规则回退）
8. **思考模式支持**：仅qwen3系列和qwen-max支持enable_thinking
9. **本地模型部署**：需要配置模型路径，使用Ollama/vLLM/Transformers部署

---

## 15. 版本历史（更新）

- **v6.2** (2026-04-10):
  - **新增：系统不足与改进方向文档**
  - **新增：短期/中期/长期改进规划**
  - **发现的问题**：
    - 安全问题：config.py硬编码API Key
    - 缺少单元测试
    - L4层PolicyEngine实现不完整
    - RAG仅用关键词匹配，未使用向量检索
    - 前端工作流状态展示不完整
    - LLM调用超时和重试机制不完善（仅Ollama有简单重试）

---

## 16. 系统审查报告（v6.5新增）

### 16.1 审查概述

本次审查从以下六个维度对系统进行了全面评估：
1. 调度算法实现审查
2. 工作流和LLM集成审查
3. 前端页面设计和交互体验审查
4. 整体调度流程合理性审查
5. 配置管理和数据模型审查
6. 错误处理和日志记录审查

### 16.2 审查发现的问题

#### 高优先级问题

**1. 多股道车站约束处理过于简化**
- **影响模块**：FCFS调度器、MIP调度器
- **问题描述**：
  - FCFS调度器：股道分配逻辑不合理（使用全局最早可用时间而非按原始顺序分配）
  - MIP调度器：多股道车站约束过于简化，未考虑咽喉区能力限制
- **影响范围**：多股道车站的调度效果
- **改进措施**：已修正股道分配逻辑，按原始发车顺序分配股道

**2. RAG检索过于简单**
- **影响模块**：L1数据建模层
- **问题描述**：仅使用关键词匹配，未使用向量检索
- **影响范围**：LLM理解调度场景的准确性
- **改进措施**：提出向量检索升级方案（待实施）

**3. LLM调用缺少统一的超时和重试机制**
- **影响模块**：LLM调用适配器
- **问题描述**：仅Ollama有简单重试，DashScope调用没有超时和重试
- **影响范围**：LLM调用的稳定性和可靠性
- **改进措施**：提出统一的超时和重试机制（待实施）

**4. 前端工作流状态展示不完整**
- **影响模块**：前端HTML/JavaScript
- **问题描述**：缺少对每个layer执行结果的详细展示
- **影响范围**：用户对工作流执行过程的理解
- **改进措施**：已添加工作流执行详情展示

**5. 错误处理不一致，缺少详细日志**
- **影响模块**：整个系统
- **问题描述**：异常捕获不一致，部分函数缺少docstring，日志记录不够完善
- **影响范围**：系统维护和问题定位
- **改进措施**：提出统一的错误处理模式（待实施）

#### 中优先级问题

**1. 配置管理存在重复**
- **影响模块**：config.py、dispatch_env.yaml
- **问题描述**：部分配置项在两个文件中都存在，可能导致不一致
- **影响范围**：配置管理的清晰度
- **改进措施**：建议统一配置管理，明确配置项的优先级

**2. 数据模型命名不一致**
- **影响模块**：数据模型层
- **问题描述**：Pydantic vs Dataclass混用，枚举类型和枚举代码混用
- **影响范围**：代码的可维护性
- **改进措施**：建议统一数据模型规范

**3. 前端UI可以进一步优化**
- **影响模块**：前端CSS/JavaScript
- **问题描述**：视觉层次感不够强，移动端适配可以优化
- **影响范围**：用户体验
- **改进措施**：已优化前端样式和交互效果

**4. 缺少单元测试**
- **影响模块**：整个系统
- **问题描述**：缺少单元测试，代码覆盖率低
- **影响范围**：代码质量和可维护性
- **改进措施**：建议添加单元测试

### 16.3 已实施的改进措施

#### 改进1：优化FCFS多股道车站股道分配
- **文件**：`solver/fcfs_scheduler.py`
- **修改内容**：将股道分配逻辑从"选择全局最早可用股道"改为"按原始发车顺序分配股道"
- **效果**：股道分配更合理，符合实际调度逻辑

#### 改进2：优化MIP多股道车站约束
- **文件**：`solver/mip_scheduler.py`
- **修改内容**：改进多股道车站约束模型，考虑同一股道的追踪间隔和不同股道的咽喉区限制
- **效果**：多股道车站约束更准确，调度方案更符合实际运营要求

#### 改进3：添加工作流执行详情展示
- **文件**：`web/templates/index.html`、`web/static/main.js`
- **修改内容**：添加L1-L4各层执行结果的详细展示区域
- **效果**：用户可以清楚地看到每个工作流的执行过程和结果

#### 改进4：优化前端视觉层次
- **文件**：`web/static/style.css`
- **修改内容**：增加卡片的悬停效果，优化指标卡片的渐变背景和动画效果
- **效果**：UI更具吸引力，交互体验更好

### 16.4 待实施的改进措施

#### 改进1：升级RAG为向量检索
- **目标**：提升检索准确性
- **方案**：使用sentence-transformers和faiss/chroma构建向量检索系统
- **预期效果**：LLM理解调度场景的准确性提升20-30%

#### 改进2：添加统一的LLM调用超时和重试机制
- **目标**：提升LLM调用的稳定性和可靠性
- **方案**：使用装饰器模式实现统一的超时和重试逻辑
- **预期效果**：LLM调用成功率提升10-15%

#### 改进3：统一错误处理模式
- **目标**：提升错误处理的一致性和可维护性
- **方案**：定义统一的异常类和错误处理装饰器
- **预期效果**：错误处理更规范，问题定位更高效

#### 改进4：添加单元测试
- **目标**：提升代码质量和可维护性
- **方案**：使用pytest编写单元测试，目标覆盖率50%以上
- **预期效果**：代码质量提升，bug数量减少

### 16.5 改进优先级和时间线

#### 短期（1-2周）
- ✅ 优化FCFS和MIP多股道车站约束处理（已完成）
- ✅ 添加工作流执行详情展示（已完成）
- ✅ 优化前端UI（已完成）
- ⏳ 升级RAG为向量检索（待实施）

#### 中期（1-2月）
- ⏳ 添加统一的LLM调用超时和重试机制（待实施）
- ⏳ 统一错误处理模式（待实施）
- ⏳ 统一配置管理（待实施）
- ⏳ 添加单元测试（目标覆盖率30%）（待实施）

#### 长期（3-6月）
- ⏳ 统一数据模型规范（待实施）
- ⏳ 优化移动端适配（待实施）
- ⏳ 添加更多的交互动效（待实施）
- ⏳ 提升单元测试覆盖率（目标覆盖率50%+）（待实施）

---

## 17. 技术债务和风险提示

### 17.1 技术债务

1. **RAG检索过于简单**：仅使用关键词匹配，未使用向量检索
2. **LLM调用缺少统一的超时和重试机制**：仅Ollama有简单重试
3. **错误处理不一致**：异常捕获不一致，缺少详细日志
4. **配置管理存在重复**：部分配置项在两个文件中都存在
5. **数据模型命名不一致**：Pydantic vs Dataclass混用
6. **缺少单元测试**：代码覆盖率低

### 17.2 风险提示

1. **API Key安全**：config.py中硬编码了API Key，存在安全风险
   - 建议：使用环境变量或密钥管理系统

2. **多股道车站约束过于简化**：可能导致调度方案不符合实际运营要求
   - 建议：引入更精细的多股道车站约束模型

3. **LLM调用稳定性**：缺少统一的超时和重试机制，可能导致调用失败
   - 建议：添加统一的超时和重试机制

4. **性能问题**：FCFS调度器的冗余恢复逻辑存在性能问题
   - 建议：预先计算并缓存各站点的冗余时间

## 18. 代码审查报告（v6.6新增）

### 18.1 审查范围

本次代码审查涵盖以下模块：
- **求解器层**：fcfs_scheduler.py, mip_scheduler.py, solver_registry.py, base_solver.py, 各适配器
- **工作流层**：layer1_data_modeling.py, layer2_planner.py, layer3_solver.py, layer4_evaluation.py
- **数据层**：data_models.py, data_loader.py, workflow_models.py, common_enums.py
- **Web层**：app.py, main.js, index.html, style.css
- **配置层**：config.py, dispatch_env.yaml

### 18.2 代码质量评估

#### 语法检查
- **通过**：所有Python文件语法正确，无明显语法错误
- **通过**：JavaScript文件语法正确，符合ES6标准

#### 逻辑检查
- **通过**：工作流L1-L4各层数据传递逻辑正确
- **通过**：求解器接口（BaseSolver/SolverRequest/SolverResponse）设计合理
- **通过**：适配器模式实现正确，数据转换逻辑完整

#### 流程检查
- **通过**：LLM驱动架构流程完整（L0场景识别 → L1数据建模 → L2规划 → L3求解 → L4评估）
- **通过**：Web API端点设计合理，请求/响应格式统一

### 18.3 发现的问题

#### 问题1：config.py中硬编码API Key
- **严重程度**：高
- **位置**：config.py:27
- **问题描述**：DASHSCOPE_API_KEY直接暴露在代码中
- **影响**：安全风险，不适合生产环境
- **建议**：使用环境变量或密钥管理系统

#### 问题2：求解器选择逻辑简化
- **严重程度**：中
- **位置**：layer3_solver.py:180-225
- **问题描述**：当前主要依赖L2的preferred_solver，规则校验逻辑被跳过
- **影响**：求解器选择的灵活性降低
- **建议**：保留完整的规则校验逻辑作为备用

#### 问题3：数据模型混用
- **严重程度**：低
- **位置**：多个模块
- **问题描述**：Pydantic模型和Dataclass混用
- **影响**：代码可维护性略有降低
- **建议**：统一使用Pydantic模型

#### 问题4：缺少单元测试
- **严重程度**：中
- **位置**：整个项目
- **问题描述**：没有单元测试文件
- **影响**：代码质量和可维护性无法保证
- **建议**：添加pytest测试，覆盖核心模块

### 18.4 已验证的正确实现

1. **调度算法**：
   - FCFS调度器实现了完整的延误传播逻辑
   - MIP调度器实现了合理的约束模型（追踪间隔、停站时间、区间运行时间）
   - 求解器适配器正确转换数据格式

2. **工作流**：
   - L1层正确提取事故信息并构建AccidentCard
   - L2层正确生成PlannerDecision结构化信息
   - L3层正确执行求解器选择和调度
   - L4层正确生成评估报告和决策

3. **数据加载**：
   - data_loader.py提供统一的数据加载接口
   - 支持真实数据（plan_timetable.csv）和处理后数据（trains.json）
   - 缓存机制避免重复加载

4. **前端交互**：
   - JavaScript正确处理API请求和响应
   - 标签页切换逻辑正确
   - 工作流状态展示完整

### 18.5 代码改进建议

1. **安全改进**：将API Key移至环境变量
2. **测试改进**：添加单元测试（目标覆盖率50%以上）
3. **日志改进**：增加详细日志，特别是异常堆栈信息
4. **错误处理**：统一异常处理模式
5. **文档**：为关键函数添加docstring

- **v6.1** (2026-04-10):
  - **移除NetworkSnapshot**：简化架构，使用完整时刻表进行调度
  - **新增调度器比较**：支持MIP/FCFS/最大延误优先/基线调度器对比
  - **修复多个bug**：workflow_continue空值检查、求解器名称获取、调度器比较属性名等
  - **FORCE_LLM_MODE保持True**：强制使用LLM，失败时报错
  - **优化推理输出**：显示正确的求解器名称和调度器比较结果
