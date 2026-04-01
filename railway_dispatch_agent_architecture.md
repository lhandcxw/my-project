# 铁路调度Agent系统架构设计文档

## 文档概述

基于Qwen大模型和整数规划的智能铁路调度Agent系统。

**设计约束**：
- 部署规模：小规模（13站，<50车 - MIP求解器限制）
- 建模方法：整数规划（MIP）+ 先到先服务（FCFS）
- Web框架：Flask
- 大模型：支持Qwen (ModelScope) 或 Ollama本地模型（可选）
- 数据模式：统一使用真实数据（data/目录下的trains.json和stations.json）

**设计原则（低侵入式改造）**：
- 只新增，不替换：优先新增文件和包装层，尽量不改动旧逻辑
- 可调试、可验证、可回退：所有新增能力支持 dry-run 模式
- 最小改动：每阶段完成后立即验证，失败则停止

**Agent模式**：
| 模式 | 加载方式 | 说明 |
|------|---------|------|
| qwen_agent | ModelScope | 自动下载模型（需要配置MODEL_PATH） |
| ollama_agent | Ollama API | 使用本地模型（需启动ollama服务） |
| 规则引擎 | 默认 | 无需大模型，直接使用Skills执行 |

---

## 1. 系统整体架构

### 1.1 架构分层设计（v2.0 - 新增工作流骨架）

```
┌─────────────────────────────────────────────────────────┐
│                   Web层 (web/)                          │
│  - 旧接口: /api/dispatch, /api/agent_chat              │
│  - 新增: /api/workflow_debug (调试接口)                 │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│            工作流层 (railway_agent/workflow_engine.py)  │
│  - run_workflow(): 串联整个工作流                        │
│  - dry_run=True: 快速验证，不调用真实solver             │
│  - dry_run=False: 调用solver_registry选择并执行solver   │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│            工作流组件层 (railway_agent/)                 │
│  - context_builder.py: 场景规格与调度上下文构建         │
│  - task_planner.py: 任务规划（基于模板，不依赖LLM）     │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│            求解器适配层 (solver/)                        │
│  - solver_registry.py: 求解器注册与选择                 │
│  - fcfs_adapter.py: FCFS调度器适配器                    │
│  - mip_adapter.py: MIP调度器适配器                      │
│  - base_solver.py: 统一求解器接口                       │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│            旧求解器层 (solver/)                          │
│  - fcfs_scheduler.py: FCFS调度器（未修改）             │
│  - mip_scheduler.py: MIP调度器（未修改）                │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│            Agent层 (railway_agent/)                     │
│  - qwen_agent.py: Qwen模型版本                         │
│  - ollama_agent.py: Ollama本地模型版本                 │
│  - rule_agent.py: 规则引擎（可接入新工作流）           │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│            Skills层 (dispatch_skills.py)                │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│            数据模型层 (models/)                          │
│  - data_models.py: 旧数据模型                          │
│  - workflow_models.py: 工作流数据模型（新增）          │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│            数据层 (data/)                                │
└─────────────────────────────────────────────────────────┘
```

### 1.2 工作流设计（v2.0）

```
用户输入（JSON）
       ↓
run_workflow(raw_input, dry_run=True/False)
       ↓
┌─────────────────────────────────────┐
│ Step 1: build_scene_spec           │
│   输入: raw_input                   │
│   输出: SceneSpec                  │
└─────────────────────────────────────┘
       ↓
┌─────────────────────────────────────┐
│ Step 2: build_dispatch_context      │
│   输入: SceneSpec, trains, stations │
│   输出: DispatchContext            │
└─────────────────────────────────────┘
       ↓
┌─────────────────────────────────────┐
│ Step 3: identify_affected_trains   │
│   输入: SceneSpec, DispatchContext │
│   输出: affected_trains            │
└─────────────────────────────────────┘
       ↓
┌─────────────────────────────────────┐
│ Step 4: plan_task                  │
│   输入: SceneSpec, DispatchContext │
│   输出: TaskPlan (subtasks列表)    │
└─────────────────────────────────────┘
       ↓
if dry_run=True:
   返回占位结果（WorkflowResult）
else:
┌─────────────────────────────────────┐
│ Step 5: select_solver              │
│   输入: scene_type                 │
│   输出: BaseSolver (fcfs/mip)      │
│   规则:                            │
│     - temporary_speed_limit → mip │
│     - sudden_failure → fcfs        │
│     - section_interrupt → mip     │
└─────────────────────────────────────┘
       ↓
┌─────────────────────────────────────┐
│ Step 6: solver.solve(request)      │
│   输入: SolverRequest              │
│   输出: SolverResponse             │
│   失败处理: 返回status=solver_failed│
└─────────────────────────────────────┘
       ↓
返回 WorkflowResult
```

---

## 2. 核心模块说明

### 2.1 工作流数据模型 (models/workflow_models.py)

```python
# 新增的工作流统一中间模型
SceneSpec: 场景规格（scene_type, scene_id, location, time_info）
DispatchContext: 调度上下文（trains, stations, affected_trains）
TaskPlan: 任务计划（task_id, subtasks列表）
SubTask: 子任务（task_id, task_type, input_data, output_data）
SolverRequest: 求解器请求
SolverResponse: 求解器响应
WorkflowResult: 工作流最终结果（包含debug_trace）
```

### 2.2 工作流引擎 (railway_agent/workflow_engine.py)

```python
from railway_agent.workflow_engine import run_workflow

# Dry-run模式（测试用）
result = run_workflow(raw_input, dry_run=True)
# 返回占位结果，不调用真实solver

# 真实求解模式
result = run_workflow(raw_input, dry_run=False, trains=trains, stations=stations)
# 自动选择并调用solver，失败时返回status="solver_failed"
```

### 2.3 求解器注册器 (solver/solver_registry.py)

```python
from solver.solver_registry import get_default_registry, SolverRegistry

# 获取注册器
registry = get_default_registry()

# 根据场景选择求解器
solver = registry.select_solver("temporary_speed_limit")
# 规则选择：
#   - temporary_speed_limit → mip
#   - sudden_failure → fcfs
#   - section_interrupt → mip
```

### 2.4 求解器适配器

```python
from solver.fcfs_adapter import FCFSSolverAdapter
from solver.mip_adapter import MIPSolverAdapter
from solver.base_solver import SolverRequest, SolverResponse

# 统一接口
adapter = FCFSSolverAdapter()
response = adapter.solve(request)
# response.status: "success" | "solver_failed" | "error"
```

---

## 3. 数据模型

### 3.1 核心类型（旧）

- `Train`: 列车时刻表
- `Station`: 车站信息
- `DelayInjection`: 延误注入数据

### 3.2 工作流类型（新增）

- `SceneSpec`: 场景规格
- `DispatchContext`: 调度上下文
- `TaskPlan`: 任务计划

### 3.3 场景类型

- `temporary_speed_limit`: 临时限速
- `sudden_failure`: 突发故障
- `section_interrupt`: 区间中断

### 3.4 数据模式

系统支持两种数据模式，通过`use_real_data(True/False)`切换：

| 模式 | 数据源 | 说明 |
|------|--------|------|
| 预设模式 | data/trains.json, data/stations.json | 小规模测试用 |
| 真实模式 | real_data/ | 大规模真实时刻表 |

**注意**：MIP求解器对列车数量有限制，Web应用默认使用前50列列车以保证求解可行。

---

## 4. 约束规则

### 4.1 延误等级分类

| 等级 | 标识 | 延误时间范围 |
|------|------|-------------|
| 微小 | MICRO | [0, 5) 分钟 |
| 小 | SMALL | [5, 30) 分钟 |
| 中 | MEDIUM | [30, 100) 分钟 |
| 大 | LARGE | [100, +∞) 分钟 |

### 4.2 约束常量

| 约束类型 | 默认值 | 说明 |
|---------|--------|------|
| 追踪间隔(headway_time) | 180秒 | 3分钟，高铁标准 |
| 最小停站时间(min_stop_time) | 60秒 | 1分钟 |
| 最小区间运行时间 | 从时刻表计算 | 基于计划运行时间 |
| 区间运行时间缓冲 | 1.2倍 | 允许略超计划时间 |

---

## 5. API接口

### 5.1 工作流调试接口（新增）

```
POST /api/workflow_debug
Body: { "scene_type": "temporary_speed_limit", "scene_id": "...", ... }
Response: {
    "success": true/false,
    "scene_spec": {...},
    "task_plan": {...},
    "debug_trace": {...},
    "message": "..."
}
```

### 5.2 智能调度（旧）

```
POST /api/agent_chat
Body: { "prompt": "G1001延误10分钟..." }
Response: { "success": true, "reasoning": "...", "delay_statistics": {...} }
```

### 5.3 表单调度（旧）

```
POST /api/dispatch
Body: { "scenario_type": "temporary_speed_limit", ... }
```

---

## 6. 文件结构

```
railway_dispatch/
├── data/                       # 真实数据文件
│   ├── trains.json            # 列车时刻表（147列列车）
│   ├── stations.json           # 车站数据（13个车站）
│   └── scenarios/              # 场景数据
│       ├── temporary_speed_limit.json
│       └── sudden_failure.json
├── models/                     # 数据模型
│   ├── data_models.py         # Pydantic模型定义（旧）
│   ├── data_loader.py         # 统一数据加载器
│   └── workflow_models.py     # 工作流数据模型（新增）
├── railway_agent/             # Agent模块
│   ├── qwen_agent.py          # Qwen Agent核心（可选）
│   ├── ollama_agent.py        # Ollama Agent变体
│   ├── rule_agent.py          # 规则引擎
│   ├── tool_registry.py       # Tools注册表
│   ├── dispatch_skills.py     # 调度Skills
│   ├── context_builder.py     # 上下文构建器（新增）
│   ├── task_planner.py        # 任务规划器（新增）
│   └── workflow_engine.py     # 工作流引擎（新增）
├── solver/                     # 求解器
│   ├── fcfs_scheduler.py      # FCFS调度器（未修改）
│   ├── mip_scheduler.py       # MIP整数规划求解器（未修改）
│   ├── base_solver.py         # 基础求解器接口（新增）
│   ├── solver_registry.py     # 求解器注册器（新增）
│   ├── fcfs_adapter.py        # FCFS适配器（新增）
│   └── mip_adapter.py         # MIP适配器（新增）
├── evaluation/                 # 评估
│   └── evaluator.py
├── visualization/              # 可视化
│   └── simple_diagram.py
├── web/                        # Web应用
│   └── app.py                 # 新增 /api/workflow_debug
├── scripts/                    # 脚本
│   └── validate_phase_a.py   # Phase A验证脚本（新增）
├── test_workflow_dry_run.py   # 工作流测试（新增）
└── test_solver_registry_smoke.py # 求解器注册器测试（新增）
```

---

## 7. 验证命令

### Phase A 验证（工作流骨架）

```bash
cd railway_dispatch
python scripts/validate_phase_a.py
# 测试场景：temporary_speed_limit, sudden_failure, section_interrupt
# 输出：各场景的 scene_type, task_id, subtasks数量, debug_trace keys
```

### Phase B 验证（求解器适配）

```bash
cd railway_dispatch
python test_solver_registry_smoke.py
# 测试：导入、注册器创建、求解器选择、适配器创建、适配器调用

python -c "from railway_dispatch.solver.solver_registry import SolverRegistry"
# 验证：直接导入
```

---

## 8. 求解器约束（MIP）

### 8.1 决策变量
- `arrival[train_id, station]`: 到达时间（秒）
- `departure[train_id, station]`: 发车时间（秒）
- `delay[train_id, station]`: 延误时间（秒）
- `max_delay`: 最大延误（目标函数）

### 8.2 约束类型

1. **区间运行时间约束**
   ```
   min_time <= 运行时间 <= scheduled_time * 1.2
   ```

2. **追踪间隔约束**
   ```
   后车发车时间 >= 前车发车时间 + headway_time
   ```

3. **停站时间约束**
   ```
   实际停站时间 = 计划停站时间（可压缩）
   ```

4. **发车时间约束**
   ```
   发车时间 >= 计划发车时间（不可提前）
   ```

5. **到达时间约束**
   ```
   到达时间 >= 计划到达时间（不可提前）
   ```

### 8.3 已知问题

- 当列车数量超过约60列且涉及多起点列车时，MIP模型可能不可行
- 解决方案：Web应用限制参与调度的列车数量（默认50列）

---

## 9. 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2024 | 初始架构：Agent + Skills + MIP |
| v2.0 | 2026-04 | 新增工作流骨架：工作流模型、求解器适配层、dry-run支持 |