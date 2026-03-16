# 铁路调度Agent系统架构设计文档

## 文档概述

本文档描述基于整数规划的铁路调度Agent系统完整架构设计。该系统旨在通过Agent工作流和自定义Skills实现列车延误场景下的智能调度功能。

**设计约束**：
- 部署规模：小规模（<10站，<20车）
- 建模方法：整数规划（MIP）
- Web框架：Flask
- 运行图：经典铁路运行图风格（时间-空间网格）

---

## 1. 系统整体架构

### 1.1 架构分层设计

```
┌─────────────────────────────────────────────────────────┐
│                     数据层 (data/)                       │
│  trains.json | stations.json | scenarios/               │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                  数据模型层 (models/)                    │
│  data_models.py | data_loader.py                         │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                   Agent层 (agent/)                       │
│  Planner Agent: 场景识别、延误分类、策略规划              │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                  Skills层 (skills/)                      │
│  TemporarySpeedLimitSkill | SuddenFailureSkill          │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                  求解器层 (solver/)                      │
│  MIPScheduler: 混合整数规划求解                          │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                  评估层 (evaluation/)                    │
│  Evaluator: 方案评估、基线对比                          │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│               可视化层 (visualization/)                  │
│  运行图生成: PNG / HTML                                  │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                   Web层 (web/)                          │
│  Flask Web应用                                           │
└─────────────────────────────────────────────────────────┘
```

### 1.2 工作流设计

```
用户输入延误信息
       ↓
  Planner Agent 场景识别
       ↓
  延误等级分类 (MICRO/SMALL/MEDIUM/LARGE)
       ↓
  选择对应Skill
       ↓
  MIP求解器优化
       ↓
  方案评估 + 基线对比
       ↓
  输出调度方案 + 运行图
```

### 1.3 核心组件说明

| 组件名称 | 职责 | 技术选型 |
|---------|------|---------|
| Planner Agent | 场景识别、延误分类、策略规划 | LangChain LLM + Prompt Engineering |
| MIP求解器 | 混合整数规划求解 | PuLP |
| Skills | 调度函数执行 | LangChain Tools |
| 数据加载器 | 统一数据管理 | Python JSON + Pydantic |
| 规则验证器 | 约束验证 | Python |

---

## 2. 数据集格式设计

### 2.1 数据文件结构

```
data/
├── trains.json          # 列车数据
├── stations.json         # 车站数据
└── scenarios/           # 场景数据
    ├── temporary_speed_limit.json
    └── sudden_failure.json
```

### 2.2 核心数据模型

#### 2.2.1 列车数据 (Train)

```json
{
  "train_id": "G1001",
  "train_type": "高速动车组",
  "speed_level": 350,
  "schedule": {
    "stops": [
      {
        "station_code": "BJP",
        "station_name": "北京西",
        "arrival_time": "08:00:00",
        "departure_time": "08:10:00",
        "platform": "1"
      }
    ]
  },
  "slack_time": {
    "max_station_slack": 120,
    "max_section_slack": 60,
    "total_slack": 180
  }
}
```

#### 2.2.2 车站数据 (Station)

```json
{
  "station_code": "BJP",
  "station_name": "北京西",
  "track_count": 15,
  "platforms": [...],
  "connection_sections": [...]
}
```

#### 2.2.3 延误注入数据 (DelayInjection)

```json
{
  "scenario_type": "temporary_speed_limit",
  "scenario_id": "SC_TSL_001",
  "injected_delays": [
    {
      "train_id": "G1001",
      "location": {"station_code": "TJG", "position": "platform"},
      "initial_delay_seconds": 600
    }
  ],
  "affected_trains": ["G1001", "G1002"],
  "scenario_params": {
    "limit_speed_kmh": 200,
    "duration_minutes": 120
  }
}
```

### 2.3 数据加载接口

```python
from models.data_loader import (
    load_trains,           # 加载JSON格式
    load_stations,
    load_scenarios,
    get_trains_pydantic,   # 加载Pydantic模型
    get_stations_pydantic
)
```

---

## 3. 约束规则设计

### 3.1 延误等级分类

| 等级 | 标识 | 延误时间范围 |
|------|------|-------------|
| 微小 | MICRO | [0, 5) 分钟 |
| 小 | SMALL | [5, 30) 分钟 |
| 中 | MEDIUM | [30, 100) 分钟 |
| 大 | LARGE | [100, +∞) 分钟 |

### 3.2 约束常量

| 约束类型 | 默认值 | 说明 |
|---------|--------|------|
| 追踪间隔 | 600秒 | 后续列车发车必须晚于前车 |
| 站台占用 | 300秒 | 站台占用时间 |
| 最大车站冗余 | 120秒 | 单站调整上限 |
| 最大区间冗余 | 60秒 | 单区间调整上限 |

### 3.3 标准区间运行时间

| 区间 | 标准时间 | 最小时间(90%) |
|------|---------|--------------|
| 北京西→天津西 | 15分钟 | 13.5分钟 |
| 天津西→济南西 | 40分钟 | 36分钟 |
| 济南西→南京南 | 70分钟 | 63分钟 |
| 南京南→上海虹桥 | 60分钟 | 54分钟 |

### 3.4 规则验证器

```python
from rules.validator import (
    validate_schedule,
    validate_headway,
    validate_section_times,
    calculate_delay_statistics
)

# 验证调度方案
result = validate_schedule(schedule, station_codes)
if result.is_valid:
    print("方案有效")
else:
    print("错误:", result.errors)
```

---

## 4. Planner Agent设计

### 4.1 Agent架构

```
输入: 延误注入数据
  ↓
场景识别: 临时限速 / 突发故障 / 区间中断
  ↓
延误分类: MICRO / SMALL / MEDIUM / LARGE
  ↓
策略规划: 选择建模方案和Skills
  ↓
输出: 策略计划
```

### 4.2 延误等级处理策略

| 等级 | 处理策略 |
|------|---------|
| MICRO | 忽略或轻微调整 |
| SMALL | 站内冗余吸收 |
| MEDIUM | 区间冗余+顺序调整 |
| LARGE | 混合策略+深度优化 |

---

## 5. 求解器设计

### 5.1 MIP模型

使用PuLP库实现混合整数规划求解。

#### 决策变量
- `arrival[t,s]`: 列车t在车站s的到达时间
- `departure[t,s]`: 列车t在车站s的发车时间

#### 目标函数
- `min_max_delay`: 最小化最大延误
- `min_avg_delay`: 最小化平均延误

#### 约束条件
1. 车站间隔约束
2. 追踪间隔约束
3. 站台占用约束
4. 时刻表约束

### 5.2 求解器接口

```python
from solver.mip_scheduler import MIPScheduler
from models.data_loader import get_trains_pydantic, get_stations_pydantic

trains = get_trains_pydantic()
stations = get_stations_pydantic()
scheduler = MIPScheduler(trains, stations)

result = scheduler.solve(delay_injection, objective="min_max_delay")
print(result.optimized_schedule)
```

---

## 6. Skills设计

### 6.1 Skill接口

```python
class BaseDispatchSkill(BaseTool):
    name: str
    description: str

    def _run(self, train_ids, station_codes, delay_injection, optimization_objective):
        # 返回调度结果
        return DispatchSkillOutput(...)
```

### 6.2 已实现Skills

| Skill | 场景类型 | 说明 |
|-------|---------|------|
| TemporarySpeedLimitSkill | 临时限速 | 处理限速导致的多车延误 |
| SuddenFailureSkill | 突发故障 | 处理单车故障延误 |

### 6.3 Skill注册

```python
from skills.dispatch_skills import create_skills

skills = create_skills(scheduler)
# skills[0]: TemporarySpeedLimitSkill
# skills[1]: SuddenFailureSkill
```

---

## 7. 评估系统

### 7.1 评估指标

| 指标 | 说明 |
|------|------|
| max_delay | 最大延误时间 |
| avg_delay | 平均延误时间 |
| total_delay | 总延误时间 |

### 7.2 基线对比

```python
from evaluation.evaluator import Evaluator

evaluator = Evaluator()
result = evaluator.compare(proposed_schedule, baseline_name)
```

---

## 8. 运行图可视化

### 8.1 经典铁路运行图

- 横轴：车站（从左到右：北京西→上海虹桥）
- 纵轴：时间（从早到晚）
- 红色斜线：列车运行线
- 蓝色矩形：停站时间

### 8.2 可视化模块

```python
# Matplotlib版本
from visualization.simple_diagram import create_train_diagram
create_train_diagram(trains, output_path="diagram.png")

# Skill版本
from visualization.train_diagram_skill import TrainDiagramSkill
skill = TrainDiagramSkill()
result = skill.execute(original, optimized, stations, names)
```

---

## 9. Web接口

### 9.1 启动服务

```bash
python3 web/app.py
# 访问: http://localhost:8080
```

### 9.2 API端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 主页面 |
| `/api/optimize` | POST | 执行优化 |
| `/api/scenarios` | GET | 获取场景列表 |

---

## 10. 文件结构汇总

```
railway_dispatch/
├── agent/                    # Agent层
│   └── planner_agent.py
├── data/                     # 数据层
│   ├── trains.json
│   ├── stations.json
│   └── scenarios/
│       ├── temporary_speed_limit.json
│       └── sudden_failure.json
├── evaluation/               # 评估层
│   └── evaluator.py
├── models/                   # 数据模型层
│   ├── data_models.py
│   └── data_loader.py
├── rules/                    # 规则层
│   ├── README.md
│   └── validator.py
├── skills/                   # Skills层
│   └── dispatch_skills.py
├── solver/                   # 求解器层
│   └── mip_scheduler.py
├── visualization/            # 可视化层
│   ├── simple_diagram.py
│   └── train_diagram_skill.py
└── web/                      # Web层
    └── app.py
```

---

## 11. 扩展开发指南

### 11.1 添加新场景

1. 在 `data/scenarios/` 添加场景JSON
2. 在 `models/data_models.py` 添加场景类型
3. 在 `skills/` 创建对应Skill
4. 在 `web/app.py` 添加配置界面

### 11.2 添加新约束

1. 在 `rules/README.md` 文档化约束
2. 在 `rules/validator.py` 实现验证函数
3. 在 `solver/mip_scheduler.py` 添加约束条件

### 11.3 添加新评估指标

1. 在 `evaluation/evaluator.py` 添加指标计算
2. 在 `rules/validator.py` 添加统计函数

---

*文档版本：v1.2*
*更新时间：2026-03-16*
