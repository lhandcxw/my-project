# Solver 系统统一迁移方案

## 问题

当前存在两套平行的求解器/调度器系统：
- **Solver系统**：solver/solver_registry.py（用于L3 Agent）
- **Scheduler系统**：scheduler_comparison/scheduler_interface.py（用于L2 Agent、Agents模块）

每个调度器在两套系统中都有重复的适配器类。

---

## 决策

**统一使用 Scheduler 系统**，废弃 Solver 系统的适配器层。

理由：
1. Scheduler系统使用更广泛（L2 Agent、Agents模块、Web API）
2. Scheduler系统功能更完善（支持多调度器比较）
3. 只废弃适配器层，保留核心调度器实现

---

## 实施步骤

### 第1步：标记 Solver 系统为废弃

修改 `solver/solver_registry.py`：

```python
# -*- coding: utf-8 -*-
"""
求解器注册器模块
【已废弃】请使用 scheduler_comparison.scheduler_interface.SchedulerRegistry

废弃原因：
1. 架构重复：与 Scheduler 系统功能重叠
2. 维护困难：需要同时维护两套适配器
3. 接口不一致：导致使用困惑

替代方案：
使用 scheduler_comparison.scheduler_interface.SchedulerRegistry

迁移日期：2026-04-21
计划移除日期：2026-06-01
"""

import warnings
from typing import Dict, Optional, Type
import logging

from solver.base_solver import BaseSolver, SolverRequest, SolverResponse

logger = logging.getLogger(__name__)

# 废弃警告
warnings.warn(
    "SolverRegistry已废弃，请使用SchedulerRegistry。"
    "参考：scheduler_comparison.scheduler_interface.SchedulerRegistry",
    DeprecationWarning,
    stacklevel=2
)


class SolverRegistry:
    """
    求解器注册器【已废弃】

    请使用 scheduler_comparison.scheduler_interface.SchedulerRegistry 代替
    """

    _solvers: Dict[str, BaseSolver] = {}
    _solver_classes: Dict[str, Type[BaseSolver]] = {}

    @classmethod
    def register(cls, name: str, solver: BaseSolver):
        """注册求解器实例【已废弃】"""
        warnings.warn(
            f"SolverRegistry.register()已废弃，请勿使用：{name}",
            DeprecationWarning,
            stacklevel=2
        )
        cls._solvers[name] = solver
        logger.debug(f"Registered solver: {name}")

    @classmethod
    def register_class(cls, name: str, solver_class: Type[BaseSolver]):
        """注册求解器类【已废弃】"""
        warnings.warn(
            f"SolverRegistry.register_class()已废弃，请勿使用：{name}",
            DeprecationWarning,
            stacklevel=2
        )
        cls._solver_classes[name] = solver_class
        logger.debug(f"Registered solver class: {name}")

    # ... 其他方法保持不变，添加废弃警告


def get_default_registry() -> SolverRegistry:
    """
    获取默认求解器注册器【已废弃】

    替代方案：
    from scheduler_comparison.comparator import create_comparator
    comparator = create_comparator(trains, stations)
    """
    warnings.warn(
        "get_default_registry()已废弃，请使用create_comparator()",
        DeprecationWarning,
        stacklevel=2
    )
    # 保持现有逻辑
    if not SolverRegistry.list_solvers():
        from solver.fcfs_adapter import FCFSSolverAdapter
        from solver.mip_adapter import MIPSolverAdapter
        from solver.max_delay_first_adapter import MaxDelayFirstSolverAdapter
        from solver.noop_adapter import NoOpSolverAdapter

        SolverRegistry.register_class("fcfs", FCFSSolverAdapter)
        SolverRegistry.register_class("mip", MIPSolverAdapter)
        SolverRegistry.register_class("max_delay_first", MaxDelayFirstSolverAdapter)
        SolverRegistry.register_class("noop", NoOpSolverAdapter)

    return SolverRegistry
```

### 第2步：更新 L3 Agent（layer3_solver.py）

```python
# 旧代码（已废弃）
from solver.solver_registry import SolverRegistry, get_default_registry

registry = get_default_registry()
solver = registry.get_solver(solver_name)
result = solver.solve(request)

# 新代码
from scheduler_comparison.scheduler_interface import SchedulerRegistry

scheduler = SchedulerRegistry.create(solver_name, trains, stations)

# 转换 SolverRequest → DelayInjection
from models.data_models import DelayInjection, InjectedDelay, DelayLocation

delay_injection = DelayInjection(
    scenario_type=request.metadata.get("scenario_type", "temporary_speed_limit"),
    scenario_id=request.scene_id,
    injected_delays=[
        InjectedDelay(
            train_id=d.get("train_id"),
            location=DelayLocation(
                location_type=d.get("location_type", "station"),
                station_code=d.get("station_code", "")
            ),
            initial_delay_seconds=d.get("initial_delay_seconds", 0),
            timestamp=d.get("timestamp", "")
        ) for d in request.injected_delays
    ],
    affected_trains=[d.get("train_id", "") for d in request.injected_delays]
)

# 执行调度
result = scheduler.solve(delay_injection, objective="min_max_delay")

# 转换结果
response = SolverResponse(
    success=result.success,
    status="success" if result.success else "solver_failed",
    schedule=result.optimized_schedule,
    metrics=result.metrics.to_dict(),
    solving_time_seconds=result.metrics.computation_time,
    solver_type=result.scheduler_type.value,
    message=result.message
)
```

### 第3步：保留核心调度器实现

**保留以下文件**（核心算法实现）：
- ✅ `solver/fcfs_scheduler.py`
- ✅ `solver/mip_scheduler.py`
- ✅ `solver/max_delay_first_scheduler.py`
- ✅ `solver/noop_scheduler.py`
- ✅ `solver/spt_scheduler.py`（可选）
- ✅ `solver/srpt_scheduler.py`（可选）

**删除以下文件**（重复的适配器层）：
- ❌ `solver/fcfs_adapter.py`
- ❌ `solver/mip_adapter.py`
- ❌ `solver/max_delay_first_adapter.py`
- ❌ `solver/noop_adapter.py`
- ❌ `solver/spt_adapter.py`
- ❌ `solver/srpt_adapter.py`

### 第4步：更新文档

在 `solver/README.md` 中添加说明：

```markdown
# Solver 模块说明

## 目录结构

```
solver/
├── base_solver.py           # 基础求解器接口（已废弃）
├── solver_registry.py       # 求解器注册表（已废弃）
├── fcfs_scheduler.py        # FCFS调度器核心实现 ✅
├── mip_scheduler.py         # MIP调度器核心实现 ✅
├── max_delay_first_scheduler.py  # MaxDelayFirst调度器核心实现 ✅
├── noop_scheduler.py       # NoOp调度器核心实现 ✅
├── spt_scheduler.py        # SPT调度器核心实现 ✅（已废弃，不符合高铁场景）
└── srpt_scheduler.py       # SRPT调度器核心实现 ✅（已废弃，不符合高铁场景）
```

## 架构说明

本模块提供调度器的**核心算法实现**，不包含适配器层。

### 核心调度器

各调度器实现都继承自 `BaseScheduler` 接口（定义在 scheduler_comparison/scheduler_interface.py）：

- **FCFSScheduler**：先到先服务调度器
- **MIPScheduler**：混合整数规划调度器
- **MaxDelayFirstScheduler**：最大延误优先调度器
- **NoOpScheduler**：基线调度器（不做调整）

### 注册和使用

请使用 `scheduler_comparison.scheduler_interface.SchedulerRegistry` 注册和获取调度器：

```python
from scheduler_comparison.scheduler_interface import SchedulerRegistry

# 创建调度器
scheduler = SchedulerRegistry.create("mip", trains, stations)

# 执行调度
result = scheduler.solve(delay_injection, objective="min_max_delay")
```

### 废弃组件

以下组件已废弃，请勿使用：
- ❌ `base_solver.py` 中的 `BaseSolver` 接口
- ❌ `solver_registry.py` 中的 `SolverRegistry`
- ❌ 所有 `*_adapter.py` 文件

## 历史原因

早期架构设计时，L2 和 L3 使用了不同的接口，导致了两套系统的存在。

**迁移日期**：2026-04-21
**计划移除废弃组件日期**：2026-06-01
```

---

## 验证清单

- [ ] Scheduler 系统可以正常注册所有调度器
- [ ] L3 Agent 使用 Scheduler 系统后行为一致
- [ ] 多调度器比较功能正常
- [ ] 测试用例全部通过
- [ ] 性能无下降
- [ ] 文档已更新

---

**文档创建日期**：2026-04-21
