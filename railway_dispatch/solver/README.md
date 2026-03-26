# 铁路调度求解器模块

本模块提供列车调度的多种算法实现，包括FCFS（先到先服务）和MIP（混合整数规划）两种调度策略。

## 调度器概览

### 1. FCFS调度器（FCFSScheduler）

**先到先服务调度器** - 一种简单快速的调度策略。

**特点：**
- 计算速度快，适合实时调度
- 按照原始发车顺序处理列车
- 简单易懂，易于实现和维护

**适用场景：**
- 需要快速响应的实时调度
- 调度规则简单的场景
- 对优化要求不高的场景

### 2. MIP调度器（MIPScheduler）

**混合整数规划调度器** - 使用数学优化方法寻找最优解。

**特点：**
- 能够找到全局最优解
- 支持多种优化目标（最小化最大延误、最小化总延误等）
- 计算时间较长，但调度效果更好

**适用场景：**
- 对调度质量要求高的场景
- 可以接受较长计算时间的场景
- 需要优化特定目标的场景

## 快速开始

### 基本用法

```python
from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
from models.data_models import InjectedDelay, DelayLocation, ScenarioType, DelayInjection
from solver.fcfs_scheduler import create_fcfs_scheduler
from solver.mip_scheduler import create_scheduler

# 加载数据
use_real_data(True)
trains = get_trains_pydantic()
stations = get_stations_pydantic()

# 创建延误场景
delay_injection = DelayInjection(
    scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
    scenario_id="TEST_001",
    injected_delays=[
        InjectedDelay(
            train_id="G1563",
            location=DelayLocation(location_type="station", station_code="BDD"),
            initial_delay_seconds=1200,  # 20分钟
            timestamp="2024-01-15T10:00:00Z"
        )
    ],
    affected_trains=["G1563"]
)

# 使用FCFS调度器
fcfs_scheduler = create_fcfs_scheduler(trains, stations)
fcfs_result = fcfs_scheduler.solve(delay_injection)

print(f"FCFS调度结果:")
print(f"  最大延误: {fcfs_result.delay_statistics['max_delay_seconds']} 秒")
print(f"  计算时间: {fcfs_result.computation_time:.4f} 秒")

# 使用MIP调度器
mip_scheduler = create_scheduler(trains, stations)
mip_result = mip_scheduler.solve(delay_injection)

print(f"MIP调度结果:")
print(f"  最大延误: {mip_result.delay_statistics['max_delay_seconds']} 秒")
print(f"  计算时间: {mip_result.computation_time:.4f} 秒")
```

### 调度结果说明

调度结果返回一个`SolveResult`对象，包含以下信息：

```python
@dataclass
class SolveResult:
    success: bool  # 是否成功
    optimized_schedule: Dict[str, List[Dict]]  # 优化后的时刻表
    delay_statistics: Dict[str, Any]  # 延误统计信息
    computation_time: float  # 计算时间（秒）
    message: str  # 消息
```

**optimized_schedule** 格式：
```python
{
    "train_id_1": [
        {
            "station_code": "BJX",
            "station_name": "北京西",
            "arrival_time": "17:36:00",
            "departure_time": "17:36:00",
            "original_arrival": "17:36",
            "original_departure": "17:36",
            "delay_seconds": 0
        },
        ...
    ],
    ...
}
```

**delay_statistics** 格式：
```python
{
    "max_delay_seconds": 1200,  # 最大延误（秒）
    "avg_delay_seconds": 43.64,  # 平均延误（秒）
    "total_delay_seconds": 9600,  # 总延误（秒）
    "affected_trains_count": 1  # 受影响列车数
}
```

## 算法对比

| 特性 | FCFS | MIP |
|------|------|-----|
| 计算速度 | 快（毫秒级） | 较慢（秒级到分钟级） |
| 优化效果 | 一般 | 优秀 |
| 实现复杂度 | 低 | 高 |
| 适用场景 | 实时调度 | 离线优化 |

## 完整流程示例

完整的调度评估流程包括：
1. 加载数据
2. 创建延误场景
3. 使用调度器进行调度
4. 使用评估系统评估结果

```python
from evaluation.evaluator import Evaluator

# 1. 使用调度器
scheduler = create_fcfs_scheduler(trains, stations)
result = scheduler.solve(delay_injection)

# 2. 准备评估数据
original_schedule = {}
for train in trains:
    stops = []
    for stop in train.schedule.stops:
        stops.append({
            "station_code": stop.station_code,
            "station_name": stop.station_name,
            "arrival_time": stop.arrival_time,
            "departure_time": stop.departure_time,
            "original_arrival": stop.arrival_time,
            "original_departure": stop.departure_time,
            "delay_seconds": 0
        })
    original_schedule[train.train_id] = stops

delay_injection_dict = {
    "scenario_type": delay_injection.scenario_type,
    "scenario_id": delay_injection.scenario_id,
    "injected_delays": [
        {
            "train_id": d.train_id,
            "location": {
                "location_type": d.location.location_type,
                "station_code": d.location.station_code
            },
            "initial_delay_seconds": d.initial_delay_seconds,
            "timestamp": d.timestamp
        }
        for d in delay_injection.injected_delays
    ],
    "affected_trains": delay_injection.affected_trains
}

# 3. 评估结果
evaluator = Evaluator(baseline_strategy="no_adjustment")
evaluation_result = evaluator.evaluate(
    result.optimized_schedule,
    original_schedule,
    delay_injection_dict
)

# 4. 输出评估报告
print(evaluator.comparator.format_result(evaluation_result))
```

## 测试脚本

本模块提供以下测试脚本：

### 1. FCFS调度器测试
```bash
cd railway_dispatch/solver
python fcfs_scheduler.py
```

### 2. 调度器对比演示
```bash
cd railway_dispatch/solver
python compare_schedulers.py
```

### 3. 完整流程演示（FCFS + 评估）
```bash
cd /data/wls/test-agent
python test_fcfs_evaluation.py
```

## 参数说明

### FCFSScheduler初始化参数

```python
FCFSScheduler(
    trains: List[Train],  # 列车列表
    stations: List[Station],  # 车站列表
    headway_time: int = 180,  # 追踪间隔（秒），默认3分钟
    min_stop_time: int = 60   # 最小停站时间（秒），默认1分钟
)
```

### MIPScheduler初始化参数

```python
MIPScheduler(
    trains: List[Train],  # 列车列表
    stations: List[Station],  # 车站列表
    headway_time: int = 180,  # 追踪间隔（秒），默认3分钟
    min_stop_time: int = 60,  # 最小停站时间（秒），默认1分钟
    min_headway_time: int = 180  # 最小安全间隔（秒），默认3分钟
)
```

## 扩展开发

### 添加新的调度器

如果需要添加新的调度器，请按照以下步骤：

1. 在`solver`目录下创建新的调度器文件（如`new_scheduler.py`）
2. 实现`solve()`方法，返回`SolveResult`对象
3. 在`solver/__init__.py`中导出新的调度器
4. 在文档中添加使用说明

示例结构：
```python
from dataclasses import dataclass

@dataclass
class SolveResult:
    success: bool
    optimized_schedule: Dict[str, List[Dict]]
    delay_statistics: Dict[str, Any]
    computation_time: float
    message: str = ""

class NewScheduler:
    def __init__(self, trains, stations, **kwargs):
        self.trains = trains
        self.stations = stations
        # ... 初始化参数

    def solve(self, delay_injection, objective="min_max_delay"):
        # ... 实现调度逻辑
        return SolveResult(...)
```

## 常见问题

**Q: 如何选择FCFS还是MIP？**
A: 如果需要快速响应，选择FCFS；如果追求最优解且有足够计算时间，选择MIP。

**Q: 如何自定义追踪间隔时间？**
A: 在创建调度器时，通过`headway_time`参数指定，单位为秒。

**Q: 调度器是否支持多股道车站？**
A: 是的，两种调度器都支持多股道车站，会根据车站的股道数量进行调度。

**Q: 如何评估调度结果？**
A: 使用`evaluation.evaluator.Evaluator`类进行评估，参考"完整流程示例"部分。

## 参考资料

- 项目主文档: `/railway_dispatch/README.md`
- 数据模型: `/railway_dispatch/models/data_models.py`
- 评估系统: `/railway_dispatch/evaluation/evaluator.py`
