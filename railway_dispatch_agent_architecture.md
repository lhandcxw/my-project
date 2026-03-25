# 铁路调度Agent系统架构设计文档

## 文档概述

基于Qwen大模型和整数规划的智能铁路调度Agent系统。

**设计约束**：
- 部署规模：小规模（13站，<50车 - MIP求解器限制）
- 建模方法：整数规划（MIP）
- Web框架：Flask
- 大模型：支持Qwen (ModelScope) 或 Ollama本地模型（可选）
- 数据模式：统一使用真实数据（data/目录下的trains.json和stations.json）

**Agent模式**：
| 模式 | 加载方式 | 说明 |
|------|---------|------|
| qwen_agent | ModelScope | 自动下载模型（需要配置MODEL_PATH） |
| ollama_agent | Ollama API | 使用本地模型（需启动ollama服务） |
| 规则引擎 | 默认 | 无需大模型，直接使用Skills执行 |

---

## 1. 系统整体架构

### 1.1 架构分层设计

```
┌─────────────────────────────────────────────────────────┐
│                   Web层 (web/)                          │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│              Agent层 (railway_agent/)                    │
│  场景识别、延误分析、Skill选择、功能调用                 │
│  - qwen_agent.py: Qwen模型版本                          │
│  - ollama_agent.py: Ollama本地模型版本                  │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                  Skills层 (dispatch_skills.py)          │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                  求解器层 (solver/)                      │
│  MIPScheduler: 混合整数规划求解                          │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                  数据模型层 (models/)                    │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                   数据层 (data/)                        │
└─────────────────────────────────────────────────────────┘
```

### 1.2 工作流设计

```
用户输入（自然语言或表单）
       ↓
QwenAgent.analyze()
       ↓
场景识别 + Tool选择
       ↓
执行Skill (MIP求解)
       ↓
返回调度方案
       ↓
展示时刻表 + 运行图
```

---

## 2. 核心模块

### 2.1 Qwen Agent

```python
from railway_agent import create_qwen_agent

agent = create_qwen_agent()
result = agent.analyze(delay_injection)
print(result.reasoning)  # Agent推理过程
```

### 2.2 MIP求解器

```python
from solver.mip_scheduler import MIPScheduler

scheduler = MIPScheduler(trains, stations)
result = scheduler.solve(delay_injection, objective="min_max_delay")
```

### 2.3 Skills

| Skill | 场景类型 | 说明 |
|-------|---------|------|
| TemporarySpeedLimitSkill | 临时限速 | 处理限速导致的多车延误 |
| SuddenFailureSkill | 突发故障 | 处理单车故障延误 |

---

## 3. 数据模型

### 3.1 核心类型

- `Train`: 列车时刻表
- `Station`: 车站信息
- `DelayInjection`: 延误注入数据

### 3.2 场景类型

- `temporary_speed_limit`: 临时限速
- `sudden_failure`: 突发故障
- `section_interrupt`: 区间中断（预留）

### 3.3 数据模式

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
| 追踪间隔(headway_time) | 120秒 | 2分钟，最小安全间隔 |
| 站台占用(platform_occupancy_time) | 300秒 | 5分钟 |
| 最小区间运行时间 | 从时刻表计算 | 基于计划运行时间 |
| 区间运行时间缓冲 | 1.2倍 | 允许略超计划时间 |

---

## 5. API接口

### 5.1 智能调度

```
POST /api/agent_chat
Body: { "prompt": "G1001延误10分钟..." }
Response: { "success": true, "reasoning": "...", "delay_statistics": {...} }
```

### 5.2 表单调度

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
│   ├── stations.json          # 车站数据（13个车站）
│   └── scenarios/              # 场景数据
│       ├── temporary_speed_limit.json
│       └── sudden_failure.json
├── models/                     # 数据模型
│   ├── data_models.py         # Pydantic模型定义
│   └── data_loader.py         # 统一数据加载器
├── railway_agent/                   # Agent模块
│   ├── qwen_agent.py          # Qwen Agent核心（可选）
│   ├── ollama_agent.py        # Ollama Agent变体
│   ├── tool_registry.py       # Tools注册表
│   └── dispatch_skills.py     # 调度Skills
├── solver/                     # 求解器
│   └── mip_scheduler.py       # MIP整数规划求解器
├── evaluation/                 # 评估
│   └── evaluator.py
├── visualization/              # 可视化
│   └── simple_diagram.py
└── web/                        # Web应用
    └── app.py
```

## 7. MIP求解器约束

### 7.1 决策变量
- `arrival[train_id, station]`: 到达时间（秒）
- `departure[train_id, station]`: 发车时间（秒）
- `delay[train_id, station]`: 延误时间（秒）
- `max_delay`: 最大延误（目标函数）

### 7.2 约束类型

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
   实际停站时间 = 计划停站时间
   ```

4. **发车时间约束**
   ```
   发车时间 >= 计划发车时间（不可提前）
   ```

5. **到达时间约束**
   ```
   到达时间 >= 计划到达时间（不可提前）
   ```

### 7.3 已知问题

- 当列车数量超过约60列且涉及多起点列车时，MIP模型可能不可行
- 解决方案：Web应用限制参与调度的列车数量（默认50列）
