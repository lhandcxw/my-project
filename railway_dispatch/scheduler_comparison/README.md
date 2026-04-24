# 铁路调度系统 - 调度方法比较模块使用指南

## 概述

本模块提供了多种调度方法（FCFS、MIP、MaxDelayFirst、EAF、NoOp）的统一接口、比较和优选功能，支持根据不同指标偏好选择最优调度方案。

## 模块结构

```
scheduler_comparison/
├── __init__.py           # 模块入口
├── metrics.py            # 评估指标定义
├── scheduler_interface.py # 调度器统一接口
├── comparator.py         # 比较器实现
└── test_comparison.py    # 测试代码
```

## 快速开始

### 1. 基本使用

```python
from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
from models.data_models import DelayInjection, InjectedDelay, DelayLocation, ScenarioType
from scheduler_comparison import create_comparator, ComparisonCriteria

# 加载数据
use_real_data(True)
trains = get_trains_pydantic()[:30]
stations = get_stations_pydantic()

# 创建比较器
comparator = create_comparator(trains, stations)

# 创建延误场景
delay_injection = DelayInjection(
    scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
    scenario_id="DEMO",
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

# 执行比较
result = comparator.compare_all(delay_injection)

# 查看排名
print(result.get_ranking_table())

# 获取最优方案
if result.winner:
    print(f"推荐方案: {result.winner.scheduler_name}")
```

### 2. 使用不同比较准则

```python
# 最小化最大延误
result = comparator.compare_all(
    delay_injection,
    criteria=ComparisonCriteria.MIN_MAX_DELAY
)

# 最小化平均延误
result = comparator.compare_all(
    delay_injection,
    criteria=ComparisonCriteria.MIN_AVG_DELAY
)

# 实时调度优先（重视计算速度）
result = comparator.compare_all(
    delay_injection,
    criteria=ComparisonCriteria.REAL_TIME
)

# 均衡考虑
result = comparator.compare_all(
    delay_injection,
    criteria=ComparisonCriteria.BALANCED
)
```

## 比较准则

| 准则 | 说明 | 权重设置 |
|------|------|---------|
| MIN_MAX_DELAY | 最小化最大延误 | 最大延误权重2.0，平均延误权重1.0，计算时间权重0.5 |
| MIN_AVG_DELAY | 最小化平均延误 | 平均延误权重2.0，最大延误权重1.0，计算时间权重0.5 |
| REAL_TIME | 实时调度优先 | 计算时间权重2.0，最大延误权重1.0，平均延误权重1.0 |
| BALANCED | 均衡考虑 | 所有权重为1.0 |

## 评估指标

### 基础指标

| 指标 | 说明 |
|------|------|
| `max_delay_seconds` | 最大延误时间（秒）——所有受影响列车中的最大延误 |
| `avg_delay_seconds` | 平均延误时间（秒）——各受影响列车**最大延误**的平均值 |
| `total_delay_seconds` | 总延误时间（秒）——所有受影响站点的延误总和 |
| `affected_trains_count` | 受影响列车数 |

### 扩展指标

| 指标 | 说明 |
|------|------|
| `median_delay_seconds` | 中位数延误 |
| `delay_std_dev` | 延误标准差 |
| `on_time_rate` | 准点率（延误<5分钟的比例） |

### 延误分布

| 等级 | 范围 |
|------|------|
| 微小延误 | < 5分钟 |
| 小延误 | 5-30分钟 |
| 中延误 | 30-100分钟 |
| 大延误 | > 100分钟 |

## 调度器类型

系统支持以下调度器：

| 调度器 | 特点 | 计算速度 | 优化效果 |
|--------|------|---------|---------|
| FCFS | 先到先服务 | 快 | 一般 |
| MIP | 混合整数规划 | 慢 | 优秀 |
| MaxDelayFirst | 最大延误优先 | 中 | 良好 |
| EAF | 最早到达优先 | 快 | 一般 |
| NoOp | 无调整（基线） | 最快 | 无 |

## 权重配置

可根据用户偏好调整各指标的权重：

```python
from scheduler_comparison import MetricsWeight

# 自定义权重
weights = MetricsWeight(
    max_delay_weight=2.0,      # 最大延误权重
    avg_delay_weight=1.0,      # 平均延误权重
    computation_time_weight=0.5, # 计算时间权重
    # ...
)

# 或使用预设配置
weights = MetricsWeight.for_min_max_delay()  # 优先最小化最大延误
weights = MetricsWeight.for_min_avg_delay()  # 优先最小化平均延误
weights = MetricsWeight.for_balance()        # 均衡考虑
weights = MetricsWeight.for_real_time()      # 实时调度优先
```

## 测试

运行测试：

```bash
cd railway_dispatch
python scheduler_comparison/test_comparison.py
```

## 扩展开发

### 添加新的调度器

```python
from scheduler_comparison import BaseScheduler, SchedulerResult, SchedulerType

class MyScheduler(BaseScheduler):
    @property
    def scheduler_type(self) -> SchedulerType:
        return SchedulerType.CUSTOM
    
    def solve(self, delay_injection, objective="min_max_delay"):
        # 实现自定义调度逻辑
        schedule = self.get_original_schedule()
        # ... 调度算法 ...
        
        return SchedulerResult(
            success=True,
            scheduler_name=self.name,
            scheduler_type=self.scheduler_type,
            optimized_schedule=schedule,
            metrics=MetricsDefinition.calculate_metrics(schedule)
        )

# 注册到比较器
my_scheduler = MyScheduler(trains, stations, name="我的调度器")
comparator.register_scheduler(my_scheduler)
```

## 常见问题

**Q: 如何选择FCFS还是MIP？**
A: 如果需要快速响应，选择FCFS；如果追求最优解且有足够计算时间，选择MIP。

**Q: 如何自定义比较准则？**
A: 可以通过自定义 MetricsWeight 来创建新的比较准则。

**Q: 调度器是否支持多股道车站？**
A: 是的，所有调度器都支持多股道车站。

---

*文档版本：v1.1*
*更新时间：2026-04-24*
