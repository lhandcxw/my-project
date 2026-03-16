# 铁路调度Agent系统

基于整数规划的铁路调度优化系统，支持临时限速、突发故障等场景的智能调度。

## 系统架构

```
railway_dispatch/
├── agent/               # Planner Agent
│   └── planner_agent.py
├── data/                # 预设数据（统一管理）
│   ├── trains.json
│   ├── stations.json
│   └── scenarios/       # 场景数据
│       ├── temporary_speed_limit.json
│       └── sudden_failure.json
├── evaluation/         # 评估系统
│   └── evaluator.py
├── models/              # 数据模型
│   ├── data_models.py  # Pydantic模型定义
│   └── data_loader.py  # 统一数据加载器
├── rules/              # 约束规则
│   ├── README.md      # 规则文档
│   └── validator.py   # 规则验证器
├── skills/             # 调度Skills
│   └── dispatch_skills.py
├── solver/             # 整数规划求解器
│   └── mip_scheduler.py
├── visualization/      # 运行图绘制
│   ├── simple_diagram.py      # 简单运行图（横轴时间，纵轴车站）
│   └── train_diagram_skill.py # Skill版本运行图
└── web/               # Web前端
    └── app.py
```

## 快速开始

### 1. 安装依赖

```bash
cd railway_dispatch
pip install -r requirements.txt
```

### 2. 启动Web服务

```bash
python3 web/app.py
```

### 3. 访问系统

打开浏览器访问: http://localhost:8080

## 数据管理

### 预设数据 (`data/`)

所有预设数据统一存放在 `data/` 目录下：

| 文件 | 说明 |
|------|------|
| `trains.json` | 列车时刻表数据（5列车，5车站） |
| `stations.json` | 车站数据（北京西-上海虹桥） |
| `scenarios/` | 场景数据目录 |
| `scenarios/temporary_speed_limit.json` | 临时限速场景（3个） |
| `scenarios/sudden_failure.json` | 突发故障场景（3个） |

### 数据加载

使用统一的数据加载器：

```python
from models.data_loader import (
    load_trains,           # 加载列车数据
    load_stations,         # 加载车站数据
    load_scenarios,        # 加载场景数据
    get_trains_pydantic,  # 获取Pydantic模型格式
    get_stations_pydantic
)
```

## 约束规则 (`rules/`)

### 规则文档 (`rules/README.md`)

包含以下约束定义：
- 延误等级分类（MICRO/SMALL/MEDIUM/LARGE）
- 追踪间隔约束（600秒）
- 区间运行时间约束
- 站台占用约束（300秒）
- 冗余时间约束
- 场景类型定义
- 优化目标定义

### 规则验证器 (`rules/validator.py`)

```python
from rules.validator import (
    validate_schedule,          # 验证调度方案
    validate_headway,           # 追踪间隔验证
    validate_section_times,     # 区间运行时间验证
    calculate_delay_statistics, # 延误统计
    calculate_delay_level       # 延误等级计算
)
```

## 功能说明

### 延误注入
- 支持三种场景：临时限速、突发故障、区间中断（预留）
- 可配置延误列车、延误时间、影响范围
- 支持两种优化目标：最小化最大延误、最小化平均延误

### 调度结果
- Planner智能分析场景类型和延误等级
- 整数规划求解器生成优化调度方案
- 基线对比，评估优化效果

### 运行图展示
- 调度前后运行图对比
- Matplotlib静态图片（PNG格式）
- HTML交互版本

## 核心模块

### 1. 数据模型 (`models/`)
- `data_models.py`: Pydantic模型定义（Train, Station, DelayInjection等）
- `data_loader.py`: 统一数据加载器

### 2. 求解器 (`solver/mip_scheduler.py`)
- 混合整数规划模型
- 追踪间隔约束
- 站台占用约束

### 3. Planner Agent (`agent/planner_agent.py`)
- 场景识别
- 延误等级分类
- 策略规划

### 4. Skills (`skills/dispatch_skills.py`)
- `TemporarySpeedLimitSkill`: 临时限速场景
- `SuddenFailureSkill`: 突发故障场景

### 5. 运行图 (`visualization/`)
- `simple_diagram.py`: 简单运行图生成（横轴时间，纵轴车站）
- `train_diagram_skill.py`: Skill版本运行图

### 6. 评估系统 (`evaluation/evaluator.py`)
- 方案评估
- 基线对比

## 扩展开发

### 添加新场景
1. 在 `models/data_models.py` 的 `ScenarioType` 添加新类型
2. 在 `skills/dispatch_skills.py` 创建新的Skill类
3. 在 `data/scenarios/` 添加对应的场景数据
4. 在 `web/app.py` 添加对应的配置界面

### 添加新的优化目标
1. 修改 `solver/mip_scheduler.py` 中的目标函数
2. 更新 `skills/dispatch_skills.py` 的 `optimization_objective` 参数

### 使用规则验证器
```python
from rules.validator import validate_schedule

# 验证调度方案
result = validate_schedule(schedule, station_codes)
if result.is_valid:
    print("调度方案有效")
else:
    print("验证失败:", result.errors)
```

## 技术栈

- **求解器**: PuLP
- **可视化**: Matplotlib, HTML/CSS/JavaScript
- **Web框架**: Flask
- **数据验证**: Pydantic

## 端口配置

- 默认端口: 8080
- 可通过命令行修改: `--server.port <端口号>`

## 版本

- v1.1: 新增统一数据加载器、约束规则验证器
- v1.0: 初版，支持临时限速和突发故障场景

## 许可证

MIT License
