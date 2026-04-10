# 铁路调度Agent系统架构设计文档（v6.0）

## 文档概述

基于大模型和整数规划的智能铁路调度Agent系统（v6.0 - 统一LLM驱动架构）。

**设计约束**：
- 部署规模：13站，147列列车（京广高铁北京西→安阳东）
- 建模方法：整数规划（MIP）+ 先到先服务（FCFS）+ 最大延误优先
- Web框架：Flask
- **大模型架构：统一LLM驱动，移除RuleAgent**
- **LLM调用方式**：
  1. API调用阿里云模型（DashScope）
  2. 调用微调后的本地模型（Ollama/vLLM/Transformers）
- 数据模式：统一使用 `data/` 目录下的真实数据
- schema_version: dispatch_v6_0

**v6.0更新**：
- **架构重构：完全移除RuleAgent，统一使用LLM驱动**
- **新增：支持本地微调模型调用（Ollama/vLLM/Transformers）**
- **统一：单一LLMAgent，支持两种LLM调用方式**
- **优化：完整L1-L4工作流，不依赖规则回退**
- **配置：统一配置中心，支持API和本地模型切换**
- **完整性判定：采用方案A（车次+位置+场景+延误时间）**
- **移除NetworkSnapshot：简化架构，使用完整时刻表进行调度**
- **新增调度器比较：支持MIP/FCFS/最大延误优先/基线调度器对比**
- **FORCE_LLM_MODE改为False：允许LLM失败时使用规则回退**

**v5.1更新**：
- 功能：L0层场景识别从规则改为LLM调用
- 功能：新增 `_llm_extraction()` 方法
- 功能：使用 `l0_preprocess_extractor` Prompt模板

**v5.0更新**：
- 重构：删除所有冗余代码和重复workflow
- 重构：删除preprocessing模块（功能已合并到L1）
- 重构：删除comat.py（Python 3.8兼容补丁）
- 重构：删除6个冗余适配器文件
- 安全：移除硬编码API Key，改为config.py变量配置
- 功能：添加FORCE_LLM_MODE配置（正式实验时禁用规则回退）
- 功能：支持enable_thinking深度思考模式（仅qwen3/qwen-max系列）
- 重构：Agent层调用完整L1-L4工作流
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
│  - max_delay_first_scheduler.py: 最大延误优先                           │
│  - noop_scheduler.py: 空操作调度器                                      │
│  - solver_registry.py: 求解器注册与自动选择                             │
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
- **求解器**：PuLP + CBC (整数规划)
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
  - **FORCE_LLM_MODE改为False**：允许LLM失败时使用规则回退
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

## 8. 快速开始

### 8.1 配置

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

1. **统一LLM驱动**：完全移除RuleAgent，所有功能基于LLM
2. **LLM调用方式切换**：通过PROVIDER配置切换API和本地模型
3. **完整性判定**：车次 + 位置 + 场景 + 延误时间（方案A）
4. **工作流顺序**：L1 → L2 → L3 → L4
5. **不使用NetworkSnapshot**：使用完整时刻表进行调度，简化架构
6. **调度器比较**：analyze_with_comparison方法支持MIP/FCFS/最大延误优先/基线对比
7. **FORCE_LLM_MODE=False**：LLM失败时可使用规则回退
8. **思考模式支持**：仅qwen3系列和qwen-max支持enable_thinking
9. **本地模型部署**：需要配置模型路径，使用Ollama/vLLM/Transformers部署

---

## 10. 后续规划

### 短期（1-2周）
- 测试新版工作流引擎
- 收集高质量微调样本
- 完成模型微调
- 部署本地微调模型

### 中期（1-2月）
- 评估微调效果
- 优化Prompt模板
- 对比API和本地模型效果
- 性能优化

### 长期（3-6月）
- 升级RAG为向量检索
- 支持多模型切换
- 进行A/B测试验证效果
- 前端代码重构
