# 铁路调度Agent系统架构（v2.0 - 完全迁移版）

## 系统概述

基于大模型和整数规划的智能铁路调度Agent系统，部署规模为13站147列列车（京广高铁北京西→安阳东）。

## 技术栈

- **大模型**: ModelScope (Qwen/Qwen2.5-1.8B)
- **求解器**: PuLP + CBC (整数规划)
- **Web框架**: Flask
- **数据**: 真实高铁时刻表（data/目录）

## 核心架构

### 分层工作流（4层）

```
L0: 预处理层
    └─ 输入标准化（自然语言 → CanonicalDispatchRequest）
    ↓
SnapshotBuilder
    └─ 构建 NetworkSnapshot（确定性逻辑）
    ↓
L1: 数据建模层
    └─ LLM提取事故信息，构建 AccidentCard
    ↓
L2: Planner层
    └─ 决策 planning_intent，构建 skill_dispatch
    ↓
L3: Solver执行层
    └─ 选择并执行求解器（MIP/FCFS/NOOP）
    ↓
L4: 评估层
    └─ PolicyEngine 最终决策（ACCEPT/FALLBACK/RERUN）
```

### 核心组件

| 组件 | 路径 | 功能 |
|------|------|------|
| 工作流引擎 | `llm_workflow_engine_v2.py` | 执行4层工作流 |
| Agent模块 | `agents.py` | 新架构 Agent（兼容旧接口） |
| 技能模块 | `adapters/skills.py` | 所有调度和查询技能 |
| 技能注册表 | `adapters/skill_registry.py` | 管理和执行技能 |
| Prompt管理 | `prompts/prompt_manager.py` | 统一管理Prompt模板 |
| RAG检索 | `rag_retriever.py` | 提供领域知识检索 |
| 预处理服务 | `preprocess_service.py` | L0层预处理 |
| 技能适配器 | `adapters/skill_adapter.py` | 技能调用统一接口 |

### 求解器

| 求解器 | 适用场景 | 优先级 |
|--------|----------|--------|
| mip_scheduler | 临时限速、列车少且信息完整 | 高 |
| fcfs_scheduler | 突发故障、需要快速响应 | 中 |
| max_delay_first_scheduler | 延误传播严重 | 低 |
| noop_scheduler | 区间封锁 | 仅记录 |

## 数据说明

### 数据文件（data/目录）

- `stations.json` - 13个车站信息
- `trains.json` - 147列列车时刻表
- `plan_timetable.csv` - 时刻表CSV格式
- `station_alias.json` - 车站别名映射
- `train_id_mapping.csv` - 列车ID映射
- `min_running_time_matrix.csv` - 最小运行时间矩阵

### 知识库（data/knowledge/目录）

- `station_knowledge.txt` - 车站详细信息
- `timetable_knowledge.txt` - 时刻表知识
- `operational_rules.txt` - 操作规则

## 接口说明

| 接口 | 方法 | 功能 |
|------|------|------|
| `/api/agent_chat` | POST | 智能调度（Agent聊天） |
| `/api/dispatch` | POST | 表单方式调度 |
| `/api/workflow/start` | POST | 启动LLM多轮工作流 |
| `/api/scheduler_comparison` | POST | 调度器对比 |

## 快速开始

### 使用新架构 Agent

```python
from railway_agent import RuleAgent, create_rule_agent

# 创建 Agent
agent = create_rule_agent()

# 执行调度
delay_injection = {
    "scenario_type": "temporary_speed_limit",
    "scenario_id": "TEST_001",
    "injected_delays": [...]
}

result = agent.analyze(delay_injection, "暴雨导致石家庄站限速80km/h")

# 查看结果
print(f"场景类型: {result.recognized_scenario}")
print(f"执行状态: {result.success}")
print(f"调度消息: {result.dispatch_result.message}")
```

### 使用工作流引擎

```python
from railway_agent.llm_workflow_engine_v2 import create_workflow_engine

engine = create_workflow_engine()
result = engine.execute_full_workflow(
    user_input="暴雨导致石家庄站限速80km/h",
    enable_rag=True
)
```

### 使用RAG检索

```python
from railway_agent.rag_retriever import get_retriever

retriever = get_retriever()
results = retriever.retrieve('临时限速', top_k=3)
```

## 场景类型

1. **临时限速** (TEMPORARY_SPEED_LIMIT)
   - 原因：暴雨、大风、冰雪
   - 推荐：mip_scheduler

2. **突发故障** (SUDDEN_FAILURE)
   - 原因：接触网故障、信号故障
   - 推荐：fcfs_scheduler

3. **区间封锁** (SECTION_INTERRUPT)
   - 原因：严重事故、线路中断
   - 推荐：noop_scheduler

## 技能列表

### 调度技能
- `temporary_speed_limit_skill` - 临时限速调度
- `sudden_failure_skill` - 突发故障调度
- `section_interrupt_skill` - 区间中断调度

### 查询技能
- `get_train_status` - 列车状态查询
- `query_timetable` - 时刻表查询

## 架构改进

### v2.0 迁移完成（2026-04-08）

**删除的旧架构文件**：
- `dispatch_skills.py` → 迁移到 `adapters/skills.py`
- `tool_registry.py` → 迁移到 `adapters/skill_registry.py`
- `rule_agent.py` → 迁移到 `agents.py`
- `qwen_agent.py` → 已删除（功能已集成）
- `llm_workflow_engine.py` → 替代为 `llm_workflow_engine_v2.py`

**新增的文件**：
- `agents.py` - 新架构 Agent（兼容旧接口）
- `adapters/skills.py` - 完整技能实现
- `adapters/skill_registry.py` - 新架构技能注册表
- `adapters/skill_adapter.py` - 更新的技能适配器
- `adapters/llm_adapter.py` - 更新的LLM适配器

**接口兼容性**：
- ✅ `RuleAgent` - 新架构 Agent，接口兼容
- ✅ `create_rule_agent` - 工厂函数，接口兼容
- ✅ `create_skills` - 技能创建，接口兼容
- ✅ `execute_skill` - 技能执行，接口兼容
- ✅ `ToolRegistry` - 指向 SkillRegistry，接口兼容

## 测试结果

所有核心功能测试通过：

1. ✅ Agent 接口（RuleAgent）
2. ✅ 技能注册表（SkillRegistry/ToolRegistry）
3. ✅ 技能创建（create_skills）
4. ✅ 技能执行（execute_skill）
5. ✅ 场景识别和分类
6. ✅ 实体提取
7. ✅ 临时限速调度
8. ✅ 突发故障调度
9. ✅ 列车状态查询
10. ✅ 时刻表查询
11. ✅ 接口兼容性
12. ✅ RAG 检索

## 版本信息

- 当前版本: v2.0（2026-04-08，完全迁移版）
- 架构模式: 4层工作流 + 统一技能系统 + RAG检索
- 数据规模: 13站，147列列车
- 迁移状态: 已完成，旧架构文件已删除
