# 铁路调度求解器模块

本模块提供列车调度的多种算法实现，包括FCFS（先到先服务）、MIP（混合整数规划）和MaxDelayFirst（最大延误优先）调度策略。

## 求解器概览

### 1. FCFS调度器（FCFSScheduler）

**先到先服务调度器** - 一种简单快速的调度策略。

**特点**：
- 计算速度快，适合实时调度
- 按照原始发车顺序处理列车
- 简单易懂，易于实现和维护

**适用场景**：
- 需要快速响应的实时调度
- 突发故障场景
- 信息不完整的场景

### 2. MIP调度器（MIPScheduler）

**混合整数规划调度器** - 使用数学优化方法寻找最优解。

**特点**：
- 能够找到全局最优解
- 支持多种优化目标（最小化最大延误、最小化总延误等）
- 计算时间较长，但调度效果更好

**适用场景**：
- 对调度质量要求高的场景
- 临时限速场景
- 可以接受较长计算时间的场景

### 3. MaxDelayFirst调度器（MaxDelayFirstScheduler）

**最大延误优先调度器** - 优先处理延误最大的列车。

**特点**：
- 防止延误传播
- 适合延误严重的场景
- 平衡计算速度和调度效果

**适用场景**：
- 延误传播严重的场景
- 需要控制最大延误的场景

### 4. NoOp调度器（NoOpScheduler）

**空操作调度器** - 仅记录，不做调整。

**特点**：
- 不进行任何调整
- 仅用于记录和对比
- 适用于区间封锁等无法调整的场景

**适用场景**：
- 区间封锁
- 用于性能对比的基线

## 快速开始

### 基本用法

```python
from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
from models.data_models import InjectedDelay, DelayLocation, ScenarioType, DelayInjection
from solver.fcfs_scheduler import FCFSScheduler
from solver.mip_scheduler import MIPScheduler
from solver.max_delay_first_scheduler import MaxDelayFirstScheduler

# 加载数据
use_real_data(True)
trains = get_trains_pydantic()[:30]  # MIP求解器建议不超过50列
stations = get_stations_pydantic()

# 创建延误场景
delay_injection = DelayInjection(
    scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
    scenario_id="TEST_001",
    injected_delays=[
        InjectedDelay(
            train_id="G1563",
            location=DelayLocation(location_type="station", station_code="SJP"),
            initial_delay_seconds=600,  # 10分钟
            timestamp="2024-01-15T10:00:00Z"
        )
    ],
    affected_trains=["G1563"]
)

# 使用FCFS调度器
fcfs_scheduler = FCFSScheduler(trains, stations)
fcfs_result = fcfs_scheduler.solve(delay_injection)

print(f"FCFS调度结果:")
print(f"  最大延误: {fcfs_result.delay_statistics['max_delay_seconds']} 秒")
print(f"  计算时间: {fcfs_result.computation_time:.4f} 秒")

# 使用MIP调度器
mip_scheduler = MIPScheduler(trains, stations)
mip_result = mip_scheduler.solve(delay_injection)

print(f"MIP调度结果:")
print(f"  最大延误: {mip_result.delay_statistics['max_delay_seconds']} 秒")
print(f"  计算时间: {mip_result.computation_time:.4f} 秒")

# 使用MaxDelayFirst调度器
mdf_scheduler = MaxDelayFirstScheduler(trains, stations)
mdf_result = mdf_scheduler.solve(delay_injection)

print(f"MaxDelayFirst调度结果:")
print(f"  最大延误: {mdf_result.delay_statistics['max_delay_seconds']} 秒")
print(f"  计算时间: {mdf_result.computation_time:.4f} 秒")
```

## 求解器对比

| 特性 | FCFS | MIP | MaxDelayFirst | NoOp |
|------|------|-----|---------------|------|
| 计算速度 | 快（毫秒级） | 较慢（秒级到分钟级） | 中等 | 最快 |
| 优化效果 | 一般 | 优秀 | 良好 | 无 |
| 实现复杂度 | 低 | 高 | 中 | 低 |
| 适用场景 | 实时调度、突发故障 | 离线优化、临时限速 | 延误控制 | 区间封锁、基线 |

## 求解器注册

系统使用求解器注册表统一管理所有求解器：

```python
from solver.solver_registry import get_default_registry

# 获取注册表
registry = get_default_registry()

# 列出所有求解器
for solver_name in registry.list_solvers():
    print(f"求解器: {solver_name}")

# 获取求解器实例
solver = registry.get_solver("mip_scheduler")
result = solver.solve(delay_injection)
```

## 调度结果说明

调度结果返回一个`SolverResult`对象，包含以下信息：

```python
@dataclass
class SolverResult:
    success: bool  # 是否成功
    status: str  # 状态（optimal, feasible, infeasible, error）
    schedule: List[Dict[str, Any]]  # 优化后的时刻表
    metrics: Dict[str, Any]  # 延误统计信息
    solving_time_seconds: float  # 计算时间（秒）
    message: str  # 消息
    error_message: Optional[str]  # 错误信息
```

**schedule** 格式：
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

**metrics** 格式：
```python
{
    "max_delay_seconds": 600,  # 最大延误（秒）
    "avg_delay_seconds": 43.64,  # 平均延误（秒）
    "total_delay_seconds": 9600,  # 总延误（秒）
    "affected_trains_count": 1  # 受影响列车数
}
```

## 参数说明

### 通用初始化参数

```python
# FCFS、MaxDelayFirst、NoOp 使用相同参数
Scheduler(
    trains: List[Train],  # 列车列表
    stations: List[Station],  # 车站列表
    headway_time: int = 180,  # 追踪间隔（秒），默认3分钟
    min_stop_time: int = 60   # 最小停站时间（秒），默认1分钟
)

# MIP 求解器额外参数
MIPScheduler(
    trains: List[Train],
    stations: List[Station],
    headway_time: int = 180,  # 追踪间隔（秒）
    min_stop_time: int = 60,  # 最小停站时间（秒）
    min_headway_time: int = 180,  # 最小安全间隔（秒）
    time_limit: int = 300  # 求解时间限制（秒），默认5分钟
)
```

## 求解器选择规则

系统根据以下规则自动选择求解器：

1. **区间封锁** → noop_scheduler
2. **信息不完整** → fcfs_scheduler
3. **列车数量少（≤3）且信息完整** → mip_scheduler
4. **列车数量多（>10）** → fcfs_scheduler
5. **临时限速** → mip_scheduler
6. **突发故障** → fcfs_scheduler
7. **默认** → mip_scheduler

## 测试脚本

### 1. 单个求解器测试

```bash
cd railway_dispatch/solver
python fcfs_scheduler.py
python mip_scheduler.py
python max_delay_first_scheduler.py
```

### 2. 求解器对比演示

```bash
cd railway_dispatch/solver
python compare_schedulers.py
```

### 3. 完整流程演示

```bash
cd railway_dispatch
python test_full_workflow.py
```

## 扩展开发

### 添加新的调度器

如果需要添加新的调度器，请按照以下步骤：

1. 在 `solver` 目录下创建新的调度器文件（如 `new_scheduler.py`）
2. 实现 `BaseScheduler` 接口
3. 在 `solver/__init__.py` 中导出新的调度器
4. 在 `solver_registry.py` 中注册求解器

示例结构：
```python
from dataclasses import dataclass
from solver.base_solver import BaseSolver, SolverRequest, SolverResult

class NewScheduler(BaseSolver):
    def __init__(self, trains, stations, **kwargs):
        super().__init__(trains, stations, **kwargs)
        # ... 初始化参数

    def solve(self, request: SolverRequest) -> SolverResult:
        # ... 实现调度逻辑
        return SolverResult(...)
```

## 常见问题

**Q: 如何选择FCFS还是MIP？**
A: 如果需要快速响应，选择FCFS；如果追求最优解且有足够计算时间，选择MIP。

**Q: MIP求解器为什么会失败？**
A: 可能原因包括：约束冲突、时间限制、内存不足。建议减少列车数量或调整约束条件。

**Q: 如何自定义追踪间隔时间？**
A: 在创建调度器时，通过 `headway_time` 参数指定，单位为秒。

**Q: 如何评估调度结果？**
A: 使用 `evaluation.evaluator.Evaluator` 类进行评估。

**Q: 为什么MIP求解器建议不超过50列列车？**
A: 列车数量过多会导致MIP问题规模过大，求解时间过长或无法找到可行解。

## 参考资料

- 项目主文档: `/railway_dispatch/README.md`
- 详细架构文档: `/railway_dispatch_agent_architecture.md`
- 数据模型: `/railway_dispatch/models/data_models.py`
- 评估系统: `/railway_dispatch/evaluation/evaluator.py`
