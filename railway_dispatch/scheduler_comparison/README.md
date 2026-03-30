# 铁路调度系统 - 调度方法比较模块使用指南

## 概述

本模块提供了多种调度方法（FCFS、MIP、强化学习等）的比较和优选功能，支持根据不同指标偏好选择最优调度方案，并可生成适合大模型理解的输出格式。

## 模块结构

```
scheduler_comparison/
├── __init__.py           # 模块入口
├── metrics.py            # 评估指标定义
├── scheduler_interface.py # 调度器统一接口
├── comparator.py         # 比较器实现
├── llm_adapter.py        # 大模型输出适配器
├── comparison_api.py     # Flask API接口
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
            location=DelayLocation(location_type="station", station_code="BDD"),
            initial_delay_seconds=1200,  # 20分钟
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

### 3. 大模型输出格式

```python
from scheduler_comparison import LLMOutputAdapter, LLMOutputFormat

adapter = LLMOutputAdapter()

# Markdown格式（适合展示）
markdown = adapter.adapt(result, LLMOutputFormat.MARKDOWN)

# 摘要格式（简洁）
summary = adapter.adapt(result, LLMOutputFormat.SUMMARY)

# JSON格式（程序处理）
json_output = adapter.adapt(result, LLMOutputFormat.JSON)

# 详细格式
detailed = adapter.adapt(result, LLMOutputFormat.DETAILED)

# 结构化输出（API返回）
structured = adapter.generate_structured_output(result)

# 生成LLM Prompt
prompt = adapter.generate_llm_prompt(
    result,
    "G1563在保定东延误了20分钟，帮我选择最优调度方案"
)
```

### 4. 添加自定义调度器

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

### 5. 强化学习调度器

```python
# 创建强化学习调度器（预留接口）
rl_scheduler = comparator.register_scheduler_by_name(
    "rl",
    model_path="/path/to/model"
)

# 检查是否可用
if rl_scheduler and rl_scheduler.is_available:
    result = comparator.compare_all(delay_injection)
```

## API接口

### 启动API服务

```python
from flask import Flask
from scheduler_comparison.comparison_api import register_comparison_routes

app = Flask(__name__)
register_comparison_routes(app)
app.run(port=8080)
```

### API端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/comparison/schedulers` | GET | 列出可用调度器 |
| `/api/comparison/criteria` | GET | 列出比较准则 |
| `/api/comparison/compare` | POST | 比较调度器 |
| `/api/comparison/recommend` | POST | 获取推荐方案 |
| `/api/comparison/llm_prompt` | POST | 生成LLM Prompt |

### API使用示例

```bash
# 列出可用调度器
curl http://localhost:8080/api/comparison/schedulers

# 比较调度器
curl -X POST http://localhost:8080/api/comparison/compare \
  -H "Content-Type: application/json" \
  -d '{
    "train_id": "G1563",
    "station_code": "BDD",
    "delay_seconds": 1200,
    "criteria": "balanced"
  }'

# 获取推荐
curl -X POST http://localhost:8080/api/comparison/recommend \
  -H "Content-Type: application/json" \
  -d '{
    "train_id": "G1563",
    "station_code": "BDD",
    "delay_seconds": 1200,
    "user_preference": "最小最大延误"
  }'
```

## 评估指标

### 基础指标

| 指标 | 说明 |
|------|------|
| `max_delay_seconds` | 最大延误时间（秒） |
| `avg_delay_seconds` | 平均延误时间（秒） |
| `total_delay_seconds` | 总延误时间（秒） |
| `affected_trains_count` | 受影响列车数 |

### 扩展指标

| 指标 | 说明 |
|------|------|
| `median_delay_seconds` | 中位数延误 |
| `delay_std_dev` | 延误标准差 |
| `on_time_rate` | 准点率（延误<5分钟的比例） |
| `recovery_rate` | 延误恢复率 |

### 延误分布

| 等级 | 范围 |
|------|------|
| 微小延误 | < 5分钟 |
| 小延误 | 5-30分钟 |
| 中延误 | 30-100分钟 |
| 大延误 | > 100分钟 |

## 权重配置

可根据用户偏好调整各指标的权重：

```python
from scheduler_comparison import MetricsWeight

# 自定义权重
weights = MetricsWeight(
    max_delay_weight=2.0,      # 最大延误权重
    avg_delay_weight=1.0,      # 平均延误权重
    computation_time_weight=0.5,  # 计算时间权重
    # ...
)

# 或使用预设配置
weights = MetricsWeight.for_min_max_delay()  # 优先最小化最大延误
weights = MetricsWeight.for_min_avg_delay()  # 优先最小化平均延误
weights = MetricsWeight.for_balance()        # 均衡考虑
weights = MetricsWeight.for_real_time()      # 实时调度优先
```

## 与大模型集成

### 生成LLM可理解的Prompt

```python
from scheduler_comparison import LLMOutputAdapter

adapter = LLMOutputAdapter()

# 生成Prompt
prompt = adapter.generate_llm_prompt(
    comparison_result=result,
    user_query="G1563在保定东延误20分钟，选择最优方案"
)

# 将prompt传递给大模型
# response = llm.chat(prompt)
```

### 输出格式示例

**摘要格式：**
```
推荐使用 MIP调度器 方案。最大延误 20 分钟，平均延误 9.2 分钟，影响 1 列列车，准点率 93.0%。
```

**Markdown格式：**
```markdown
# 铁路调度方案比较报告

## 📊 比较概览
| 指标 | 值 |
|------|------|
| 比较准则 | balanced |
| 参与方案数 | 2 |

## 🏆 方案排名
| 排名 | 调度器 | 最大延误 | 平均延误 |
|------|--------|----------|----------|
| 1 | MIP调度器 ⭐ | 20分钟 | 9.2分钟 |
| 2 | FCFS调度器 | 20分钟 | 15.0分钟 |

## ✅ 推荐方案
**MIP调度器**
...
```

## 后续扩展

### 1. 添加强化学习调度器

```python
# 实现 RL 调度器的 load_model 方法
class ReinforcementLearningSchedulerAdapter(BaseScheduler):
    def load_model(self, model_path: str) -> bool:
        # 使用 stable-baselines3 或其他 RL 框架加载模型
        from stable_baselines3 import PPO
        self._model = PPO.load(model_path)
        self._is_available = True
        return True
```

### 2. 添加更多调度算法

- 遗传算法（GA）
- 模拟退火（SA）
- 蚁群算法（ACO）
- 深度强化学习（DRL）

### 3. 集成到大模型Agent

```python
# 将比较结果作为Tool输出传递给Agent
def dispatch_with_comparison(user_query: str):
    # 解析用户需求
    delay_injection = parse_query(user_query)
    
    # 执行比较
    result = comparator.compare_all(delay_injection)
    
    # 生成LLM Prompt
    adapter = LLMOutputAdapter()
    prompt = adapter.generate_llm_prompt(result, user_query)
    
    # 调用大模型
    response = agent.chat(prompt)
    
    return response
```

## 测试

运行测试：

```bash
cd railway_dispatch
python scheduler_comparison/test_comparison.py
```
