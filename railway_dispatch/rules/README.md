# 铁路调度系统约束规则文档

本文档整理了系统中所有约束规则，用于验证和约束检查。

---

## 1. 延误等级分类

根据延误时间长短，将延误分为四个等级：

| 等级 | 标识 | 延误时间范围 | 处理策略 |
|------|------|-------------|---------|
| 微小 | MICRO | [0, 5) 分钟 | 忽略或轻微调整 |
| 小 | SMALL | [5, 30) 分钟 | 站内冗余吸收 |
| 中 | MEDIUM | [30, 100) 分钟 | 区间冗余+顺序调整 |
| 大 | LARGE | [100, +∞) 分钟 | 混合策略+深度优化 |

**计算方法**：
```
delay_minutes = delay_seconds / 60
if delay_minutes < 5: MICRO
elif delay_minutes < 30: SMALL
elif delay_minutes < 100: MEDIUM
else: LARGE
```

---

## 2. 追踪间隔约束

后续列车必须晚于前车一定时间发车。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| headway_time | 180 秒 (3分钟) | 追踪间隔时间 |
| min_headway_time | 180 秒 (3分钟) | 最小安全间隔 |

**约束条件**：
```
departure[t2, s] - departure[t1, s] >= headway_time
其中 t1 在 t2 之前经过车站 s
```

---

## 3. 区间运行时间约束

列车在相邻车站之间的运行时间必须满足最低要求。

### 3.1 区间运行时间计算

系统从列车时刻表动态计算区间运行时间：

```
min_time = 计划运行时间（从时刻表提取）
max_time = 计划运行时间 × 1.2（允许20%缓冲）
```

**约束条件**：
```
min_time <= (到达时间 - 发车时间) <= max_time
```

### 3.2 真实数据区间运行时间

| 区间 | 计划运行时间 |
|------|-------------|
| 徐水东 -> 保定东 | 10 分钟 (600秒) |
| 保定东 -> 定州东 | 15 分钟 (900秒) |
| 定州东 -> 正定机场 | 9 分钟 (540秒) |

---

## 4. 站台占用约束

列车停靠站台时，需要满足站台占用时间要求。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| platform_occupancy_time | 300 秒 (5分钟) | 站台占用时间 |

**约束条件**：
```
departure[t, s] - arrival[t, s] >= platform_occupancy_time - min_stop_time
```
其中 min_stop_time 是原始计划停站时间。

---

## 5. 冗余时间约束

列车在时刻表中预留的冗余时间用于吸收延误。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| max_station_slack | 300 秒 | 最大车站冗余时间 |
| max_section_slack | 180 秒 | 最大区间冗余时间 |
| total_slack | 480 秒 | 总冗余时间 |

**约束条件**：
```
每站调整时间 <= max_station_slack
每区间调整时间 <= max_section_slack
总调整时间 <= total_slack
```

---

## 6. 场景类型约束

### 6.1 临时限速场景 (temporary_speed_limit)

**特征**：
- 限速区段明确
- 影响多列列车
- 持续时间可预估

**参数**：
| 参数 | 说明 |
|------|------|
| limit_speed_kmh | 限速值 |
| duration_minutes | 持续时间（分钟） |
| affected_section | 影响区间 |

**处理策略**：
- 调整到达时间
- 调整发车时间
- 必要时调整列车顺序

### 6.2 突发故障场景 (sudden_failure)

**特征**：
- 单列车受影响
- 故障位置明确
- 恢复时间不确定

**参数**：
| 参数 | 说明 |
|------|------|
| failure_type | 故障类型 |
| estimated_repair_time | 预计修复时间（分钟） |
| failure_location | 故障位置 |

**处理策略**：
- 延误传播分析
- 待避决策
- 优化求解

### 6.3 区间中断场景 (section_interrupt)

**特征**：
- 区间完全无法通行
- 影响所有经过列车
- 需要绕行或等待

**参数**：
| 参数 | 说明 |
|------|------|
| interrupt_location | 中断位置 |
| interrupt_reason | 中断原因 |

**处理策略**：
- 使用 noop_scheduler（仅记录）
- 不进行调整

---

## 7. 优化目标约束

系统支持以下优化目标：

| 目标 | 说明 | 公式 |
|------|------|------|
| min_max_delay | 最小化最大延误 | min max(delay_i) |
| min_avg_delay | 最小化平均延误 | min sum(delay_i) / n |

---

## 8. 系统规模约束

| 参数 | 最大值 | 说明 |
|------|--------|------|
| n_stations | 13 | 车站数量 |
| n_trains | 147 | 列车总数（MIP建议≤50） |

**说明**：
- 真实数据模式：147列列车
- Web应用默认使用前50列列车以保证MIP求解可行
- 超过50列可能产生不可行解

---

## 9. 验证规则

### 9.1 调度方案验证

每个调度方案必须满足以下条件：

1. **时间单调性**：
   - 同一列车的到达时间 <= 发车时间
   - 后续车站的到达时间 >= 前一车站的发车时间

2. **约束满足**：
   - 追踪间隔约束
   - 区间运行时间约束
   - 站台占用约束

3. **边界条件**：
   - 时间非负
   - 车站编码有效
   - 列车ID有效

### 9.2 延误计算

```
actual_delay = actual_departure - scheduled_departure
如果 actual_delay < 0: delay = 0 (表示提前)
```

---

## 10. 使用示例

### 10.1 验证调度方案

```python
from rules.validator import Validator

# 创建验证器
validator = Validator(trains, stations)

# 验证调度方案
validation_report = validator.validate(optimized_schedule)

# 检查结果
if validation_report.is_valid:
    print("方案通过验证")
else:
    print("方案存在问题：")
    for issue in validation_report.issues:
        print(f"  - {issue.description}")
```

### 10.2 调整约束参数

```python
from rules.validator import Validator

# 自定义参数
validator = Validator(
    trains=trains,
    stations=stations,
    headway_time=120,      # 2分钟追踪间隔
    max_station_slack=300,  # 5分钟车站冗余
    max_section_slack=180   # 3分钟区间冗余
)

validation_report = validator.validate(schedule)
```

---

## 11. 常见问题

**Q: 为什么要有冗余时间约束？**
A: 冗余时间是时刻表预留的弹性空间，用于吸收延误，避免小延误导致连锁反应。

**Q: 追踪间隔如何确定？**
A: 根据实际铁路运营安全要求，一般高铁为3分钟，可根据实际情况调整。

**Q: 不同场景的约束有何不同？**
A: 临时限速需要调整运行时间，突发故障需要延误传播分析，区间中断使用noop策略。

---

## 12. 参考文献

- 架构文档: `railway_dispatch_agent_architecture.md`
- 求解器模块: `solver/mip_scheduler.py`
- 数据模型: `models/data_models.py`

---

*文档版本：v1.1*
*更新时间：2026-04-08*
