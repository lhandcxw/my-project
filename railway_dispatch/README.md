# LLM-TTRA: 大模型辅助列车时刻表重排系统

基于阿里云Qwen大模型和整数规划的智能铁路调度优化系统（v5.0）。

## 系统概述

- **核心任务**: LLM-TTRA (Large Language Model assisted Train Timetable Rescheduling)
- **部署规模**: 13站，147列列车（京广高铁北京西→安阳东）
- **技术路线**: 阿里云Qwen API + Prompt + RAG + 整数规划
- **大模型**: 阿里云DashScope (qwen-max/qwen3.5-27b)
- **求解器**: MIP（整数规划）、FCFS（先到先服务）、MaxDelayFirst（最大延误优先）
- **Web框架**: Flask + Pydantic
- **数据**: 真实高铁时刻表

## v5.0 重构说明

**架构清理**:
- 删除所有冗余代码和重复workflow
- 删除preprocessing模块（功能已合并到L1）
- 删除comat.py（Python 3.8兼容补丁）
- 统一入口：web/app.py

**LLM主路径强化**:
- 强制使用阿里云DashScope API
- 移除硬编码API Key，强制环境变量配置
- 添加FORCE_LLM_MODE配置（正式实验时禁用规则回退）
- 启用enable_thinking深度思考模式

**Agent层重构**:
- 删除关键词场景识别，改为调用L1层LLM提取
- 删除独立实体提取逻辑，统一使用工作流
- Agent.analyze()现在调用完整L1-L4工作流

## 系统架构

```
railway_dispatch/
├── data/                     # 数据层
│   ├── station_alias.json    # 车站数据
│   ├── plan_timetable.csv    # 时刻表
│   ├── min_running_time_matrix.csv
│   ├── train_id_mapping.csv
│   ├── scenarios/            # 场景数据
│   └── knowledge/            # RAG知识库
├── models/                   # 数据模型层
│   ├── common_enums.py       # 统一枚举
│   ├── data_loader.py        # 数据加载器
│   ├── data_models.py        # Pydantic模型
│   └── workflow_models.py    # 工作流模型
├── railway_agent/            # Agent模块
│   ├── workflow/             # 工作流分层（核心）
│   │   ├── layer1_data_modeling.py  # L1: LLM提取事故信息
│   │   ├── layer2_planner.py        # L2: LLM决策planning_intent
│   │   ├── layer3_solver.py         # L3: 求解器选择与执行
│   │   └── layer4_evaluation.py     # L4: 评估与决策
│   ├── adapters/             # 适配器层
│   │   ├── llm_adapter.py           # LLM调用适配器
│   │   ├── llm_prompt_adapter.py    # Prompt适配器
│   │   └── skills.py                # 技能实现
│   ├── prompts/              # Prompt管理
│   ├── snapshot_builder.py   # 网络快照构建器
│   ├── llm_workflow_engine_v2.py  # 工作流引擎
│   ├── session_manager.py    # 会话管理
│   ├── agents.py             # Agent实现
│   └── policy_engine.py      # 策略引擎
├── solver/                   # 求解器层
│   ├── mip_scheduler.py      # MIP求解器
│   ├── fcfs_scheduler.py     # FCFS调度器
│   ├── max_delay_first_scheduler.py
│   └── solver_registry.py    # 求解器注册
├── evaluation/               # 评估层
│   └── evaluator.py
├── web/                      # Web层
│   └── app.py                # Flask应用（唯一入口）
└── config.py                 # 统一配置
```

## 快速开始

### 1. 配置API Key

编辑 `config.py` 文件，填写你的阿里云DashScope API Key：

```python
# config.py 第17行
DASHSCOPE_API_KEY = "your-actual-api-key-here"  # 替换为你的API Key
```

其他配置项（可选修改）：
```python
DASHSCOPE_MODEL = "qwen-max"           # 模型名称：qwen-max, qwen3.5-27b, qwen3.6-plus等
DASHSCOPE_ENABLE_THINKING = True       # 是否启用深度思考（部分模型不支持）
FORCE_LLM_MODE = True                  # 强制LLM模式，禁用规则回退
```

**注意**：项目完成后会改为环境变量配置方式，当前为开发调试方便使用直接变量配置。

### 2. 安装依赖

```bash
cd railway_dispatch
pip install -r requirements.txt
```

### 3. 启动Web服务

```bash
python web/app.py
```

访问 http://localhost:8081

### 4. 使用智能调度

输入自然语言描述，例如：
- "暴雨导致石家庄站限速80km/h"
- "G1563列车在保定东遭遇大风预计延误10分钟"
- "设备故障导致XSD-BDD区间临时封锁"

## 核心工作流

系统采用5层LLM决策架构：

| 层级 | 模块 | 功能 | 说明 |
|------|------|------|------|
| L1 | layer1_data_modeling.py | 数据建模 | LLM提取事故信息，构建AccidentCard |
| SB | snapshot_builder.py | 快照构建 | 确定性构建NetworkSnapshot |
| L2 | layer2_planner.py | Planner | LLM决策planning_intent |
| L3 | layer3_solver.py | Solver | 规则选择求解器并执行 |
| L4 | layer4_evaluation.py | Evaluation | LLM生成解释，PolicyEngine决策 |

## 配置说明

### 环境变量

| 变量名 | 必需 | 默认值 | 说明 |
|--------|------|--------|------|
| DASHSCOPE_API_KEY | 是 | - | 阿里云API Key |
| DASHSCOPE_MODEL | 否 | qwen-max | 模型名称 |
| DASHSCOPE_ENABLE_THINKING | 否 | true | 深度思考模式 |
| FORCE_LLM_MODE | 否 | true | 强制LLM模式 |
| LLM_PROVIDER | 否 | dashscope | LLM提供商 |

### 实验模式

**正式实验模式**（推荐）：
```bash
export FORCE_LLM_MODE="true"
export DASHSCOPE_ENABLE_THINKING="true"
```
- LLM失败时直接报错，不回退到规则
- 确保收集的微调数据质量

**调试模式**：
```bash
export FORCE_LLM_MODE="false"
```
- LLM失败时回退到规则提取
- 用于调试和开发

## API接口

### 智能调度
```http
POST /api/agent_chat
Content-Type: application/json

{
    "message": "暴雨导致石家庄站限速80km/h"
}
```

### 工作流启动
```http
POST /api/workflow/start
Content-Type: application/json

{
    "user_input": "G1563在保定东延误10分钟"
}
```

### 工作流继续
```http
POST /api/workflow/next
Content-Type: application/json

{
    "session_id": "xxx",
    "user_input": "补充信息"
}
```

## 求解器说明

| 求解器 | 适用场景 | 选择规则 |
|--------|----------|----------|
| MIP | 临时限速、列车≤3 | L3根据场景类型选择 |
| FCFS | 突发故障、列车>10 | L3根据场景类型选择 |
| NoOp | 区间封锁 | L3根据场景类型选择 |

## 微调数据收集

系统自动收集微调样本到 `data/sft_train.jsonl`：

```json
{
    "messages": [
        {"role": "system", "content": "从调度员描述中提取事故信息"},
        {"role": "user", "content": "暴雨导致石家庄站限速80km/h"},
        {"role": "assistant", "content": "{\"scene_category\": \"临时限速\", ...}"}
    ],
    "metadata": {
        "layer": "L1",
        "template_id": "l1_data_modeling"
    }
}
```

## 技术栈

- **大模型**: 阿里云DashScope (qwen-max/qwen3.5-27b)
- **求解器**: PuLP + CBC (整数规划)
- **Web**: Flask + Pydantic
- **Prompt管理**: 自定义PromptManager
- **RAG检索**: 关键词匹配

## 版本历史

- **v5.0** (2026-04-09):
  - 重构：删除所有冗余代码和重复workflow
  - 重构：删除preprocessing模块
  - 安全：移除硬编码API Key
  - 功能：添加FORCE_LLM_MODE配置
  - 功能：启用enable_thinking深度思考
  - 重构：Agent层调用L1-L4工作流
  - 文档：更新README和架构文档

- **v4.2** (2026-04-08):
  - 阿里云qwen3.5-27b替换本地Ollama
  - 支持DashScope API调用
  - 修复工作流流程

## 许可证

MIT License
