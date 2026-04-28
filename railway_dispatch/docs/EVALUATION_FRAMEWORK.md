# LLM-TTRA：评估框架与指标

**文档版本**：2.0  
**日期**：2026-04-26  
**用途**：评估指标、对比方法及验证程序的完整规范。旨在支持期刊投稿的严格实验设计。

---

## 1. 评估理念

铁路调度评估本质上是多目标的。单一指标无法涵盖所有利益相关方关切：
- **运营方**：总延误、计算时间
- **乘客**：最大延误、准点率
- **网络稳定性**：传播广度/深度
- **公平性**：延误分布公平
- **能耗**：速度平滑度、不必要停车

LLM-TTRA实现**层次化评估体系**：
1. **基础指标**：所有求解器输出自动计算
2. **高铁专用指标**：领域感知加权与评分
3. **专家指标**：高级公平性、鲁棒性、乘客体验、能耗
4. **LLM生成评估**：面向人类调度员的自然语言综合

---

## 2. 基础指标（所有求解器）

### 2.1 延误指标

| 指标 | 符号 | 单位 | 定义 |
|------|------|------|------|
| 最大延误 | $D_{\max}$ | 秒 | $\max_{i,j} \Delta_{ij}$ |
| 平均延误 | $D_{\text{avg}}$ | 秒 | 每列受影响列车最大延误的平均值 |
| 总延误 | $D_{\text{total}}$ | 秒 | $\sum_{i,j} \Delta_{ij}$ |
| 中位数延误 | $D_{\text{med}}$ | 秒 | 每列列车最大延误的中位数 |

**重要修正（v8.0）**：`avg_delay`从"所有车站延误之和除以受影响列车数"（产生虚高值）重新定义 为"每列受影响列车最大延误的平均值"。这符合调度员直觉：一列列车延误10分钟就是"延误10分钟"，而非其在5个站延误之和。

### 2.2 传播指标

| 指标 | 符号 | 定义 |
|------|------|------|
| 受影响列车数 | $N_{\text{aff}}$ | $\max_j \Delta_{ij} > 0$ 的列车数 |
| 传播深度 | $P_{\text{depth}}$ | 下游延误>0的最大车站数 |
| 传播广度 | $P_{\text{breadth}}$ | $N_{\text{aff}}$（同义，便于理解） |
| 传播系数 | $P_{\text{coef}}$ | $\frac{N_{\text{aff}} \times P_{\text{depth}}}{|\mathcal{T}| \times |\mathcal{S}|}$（归一化） |

### 2.3 质量指标

| 指标 | 符号 | 定义 |
|------|------|------|
| 准点率 | $\rho_{\text{on\_time}}$ | $\max_j \Delta_{ij} < 300$秒（5分钟）的列车比例 |
| 严格准点率 | $\rho_{\text{strict}}$ | $\max_j \Delta_{ij} < 180$秒（3分钟）的比例 |
| 恢复率 | $\eta$ | $\frac{D_{\text{total}}^{\text{orig}} - D_{\text{total}}^{\text{opt}}}{D_{\text{total}}^{\text{orig}}}$ |
| 计算时间 | $T_{\text{comp}}$ | 求解器实际挂钟执行时间 |

### 2.4 分布指标

| 指标 | 符号 | 定义 |
|------|------|------|
| 延误方差 | $\sigma^2_D$ | 每列列车最大延误的方差 |
| 延误标准差 | $\sigma_D$ | 每列列车最大延误的标准差 |

---

## 3. SolverSelector：多目标评分与Pareto分析

### 3.1 设计定位

`SolverSelector`（`railway_agent/solver_selector.py`）将所有求解器选择逻辑集中于一端，消除`layer2_planner`与`skills`中的重复/不一致。

- `score_result`和`find_pareto_front`：**数学计算**（非AI）
- `recommend_solver`：**基于阈值的规则推荐**，仅作为LLM失败时的兜底

### 3.2 综合评分

对单个求解结果，给定优化目标$O$：

$$\text{score}_{\max} = \min\left(\frac{\max\_delay}{30} \times 100, 100\right)$$
$$\text{score}_{\text{avg}} = \min\left(\frac{\text{avg\_delay}}{30} \times 100, 100\right)$$
$$\text{score}_{\text{total}} = \min\left(\frac{\text{total\_delay}}{120} \times 100, 100\right)$$
$$\text{score}_{\text{affected}} = \min\left(\frac{\text{affected\_trains}}{10} \times 100, 100\right)$$
$$\text{score}_{\text{comp}} = \min\left(\frac{\text{computation\_time}}{60} \times 100, 100\right)$$
$$\text{score}_{\text{on\_time}} = (1 - \text{on\_time\_rate}) \times 100$$

综合得分：
$$\text{Composite} = \sum_{k} w_k^{(O)} \cdot \text{score}_k$$

其中$w_k^{(O)}$来自`HighSpeedMetricsWeight.for_$(O)$()`，与`comparator.py`完全对齐。

### 3.3 Pareto最优解

在$(\max\_delay, \text{avg\_delay}, \text{total\_delay}, \text{affected\_trains}, \text{computation\_time})$五维空间中，解$a$支配解$b$当且仅当：
$$\forall k: a_k \leq b_k \quad \text{且} \quad \exists k: a_k < b_k$$

`find_pareto_front()`返回所有非支配解，按`composite_score`排序。

### 3.4 规则推荐（兜底）

`recommend_solver()`基于以下决策树：
1. 区间封锁或信息不完整 → FCFS（安全兜底）
2. 紧急（urgency=high/critical 或延误>60分钟） → FCFS，`min_max_delay`
3. 时间预算<30秒 → FCFS
4. 大规模（受影响>10列或延误>30分钟） → Hierarchical，`min_total_delay`
5. 高峰时段 → Hierarchical
6. 小规模、轻微延误（≤15分钟） → MIP
7. 其他 → Hierarchical

---

## 4. 高铁专用加权评分

### 4.1 权重类

`HighSpeedMetricsWeight`为不同运营优先级提供工厂方法：

```python
class HighSpeedMetricsWeight:
    max_delay_weight: float = 1.0
    avg_delay_weight: float = 0.8
    total_delay_weight: float = 0.5
    affected_trains_weight: float = 0.9
    propagation_depth_weight: float = 0.7
    propagation_breadth_weight: float = 0.6
    computation_time_weight: float = 0.3
    delay_variance_weight: float = 0.4
    recovery_rate_weight: float = 0.5
    on_time_rate_weight: float = 0.6
```

### 4.2 准则配置

| 准则 | 配置 | 使用场景 |
|------|------|---------|
| `min_total_delay` | total=3.0, avg=2.0, max=1.0 | 默认；系统效率 |
| `min_max_delay` | max=3.0, total=0.3, avg=0.5 | 关键列车保障 |
| `min_avg_delay` | avg=3.0, total=1.0, max=1.0 | 乘客服务质量 |
| `min_propagation` | affected=2.0, depth=1.5, breadth=1.2 | 高密度线路稳定性 |
| `real_time` | comp=2.0, max=1.0, total=0.5 | 应急响应 |
| `balanced` | max=1.2, avg=1.0, affected=1.0 | 通用 |

### 4.3 评分公式

对给定调度器结果，指标为 $M$，权重为 $W$：

$$\text{Score}(M, W) = \sum_{k} w_k \cdot \text{normalize}_k(m_k)$$

其中归一化将每个指标映射到[0, 100]，"越低越好"。

---

## 5. 专家指标（`evaluation/expert_metrics.py`）

### 5.1 公平性指标

| 指标 | 说明 | 公式 |
|------|------|------|
| 延误基尼系数 | 延误分布在列车间的不平等程度 | $G = \frac{2 \sum_{i=1}^{n} i \cdot d_{(i)}}{n \sum_{i=1}^{n} d_{(i)}} - \frac{n+1}{n}$ |
| 优先级加权延误 | 高优先级（G/D字头）延误惩罚更高 | $\bar{d}_p = \frac{\sum_i w_i \cdot d_i}{\sum_i w_i}$ |
| 高优先级准点率 | G/D字头列车专属准点率 | |

### 5.2 鲁棒性指标

| 指标 | 说明 |
|------|------|
| 缓冲时间比例 | 优化后保留的区间缓冲时间占比 |
| 恢复时间（分钟） | 预计恢复正常运行时刻表所需时间 |
| 关键路径冗余度 | 最受限路径上的时间松弛 |

### 5.3 效率指标

| 指标 | 说明 |
|------|------|
| 区间利用率 | 原始与优化总行程时间之比 |
| 停站时间偏差 | 原始与优化停站时间的平均绝对差 |
| 追踪间隔达标率 | 满足追踪间隔的连续发车比例 |

### 5.4 乘客体验指标

| 指标 | 说明 |
|------|------|
| 换乘衔接成功率 | 基于时刻表偏差估计的换乘成功比例 |
| 首末班车准点率 | 按时间顺序首班和末班车的准点情况 |
| 长途列车准点率 | 停靠站≥5站的列车准点率 |

### 5.5 能耗指标

| 指标 | 说明 |
|------|------|
| 速度平滑度 | $1 / (1 + CV)$，$CV$为速度比变异系数 |
| 不必要停车次数 | 原始停站≤60秒但优化停站>120秒的车站数 |
| 能耗效率指数 | $0.6 \times \text{speed\_smoothness} + 0.4 \times (1 - \frac{\text{unnecessary\_stops}}{n})$ |

### 5.6 专家综合评分

六维加权得分：

$$\text{ExpertScore} = 0.25 \cdot S_{\text{delay}} + 0.15 \cdot S_{\text{fairness}} + 0.20 \cdot S_{\text{robustness}} + 0.15 \cdot S_{\text{efficiency}} + 0.15 \cdot S_{\text{passenger}} + 0.10 \cdot S_{\text{energy}}$$

---

## 6. 多算法对比协议

### 6.1 基线定义

**NoOp调度器**作为基线：
- 对受影响列车应用初始延误
- 不调整任何发车顺序或时间
- 代表"除确认延误外不做任何调整"

所有改进均相对NoOp报告。

### 6.2 对比流程

```
对每个测试场景：
    1. 运行 NoOp 基线
    2. 对每个候选求解器：
        a. 运行求解器
        b. 验证约束
        c. 计算指标
        d. 计算加权得分（SolverSelector.score_result）
        e. 计算相对基线改进
    3. 识别Pareto最优解集（SolverSelector.find_pareto_front）
    4. 按得分升序排列
    5. 标记优胜者
    6. 生成推荐意见
```

### 6.3 输出格式

```json
{
  "success": true,
  "criteria": "min_total_delay",
  "winner": {
    "scheduler_name": "hierarchical",
    "score": 15.2,
    "metrics": {...}
  },
  "pareto_front": [...],
  "all_results": [...],
  "baseline_metrics": {...},
  "recommendations": [
    "分层求解器在延误削减与计算时间之间取得最佳平衡",
    "MIP总延误改进12%但耗时45秒"
  ]
}
```

---

## 7. 约束验证

### 7.1 验证规则

所有求解器输出在接受前自动验证：

**规则1：时间单调性**
- 每列列车：所有车站到达 <= 发车
- 每列列车：departure[i] <= arrival[i+1]

**规则2：追踪间隔合规**
- 每个股道数 $c_j >= 1$ 的车站：每股道连续发车时间差 >= $h$
- 通过站（到达==发车）免于停站检查

**规则3：区间时间下限**
- 实际区间时间 >= 0.8 × 最小区间时间

**规则4：非负性**
- 所有延误 >= 0
- 所有实际时间 >= 计划时间

### 7.2 验证结果

截至目前所有测试场景：
- 时间单调性：100%通过
- 追踪间隔合规：100%通过
- 区间时间下限：100%通过
- 非负性：100%通过

---

## 8. 论文实验设计建议

### 8.1 推荐测试场景

| 编号 | 类型 | 位置 | 列车 | 初始延误 | 预期规模 |
|------|------|------|------|---------|---------|
| S1 | 突发故障 | SJP | G1215 | 10分钟 | 中等传播 |
| S2 | 临时限速 | BDD-SJP | G573 | 15分钟 | 大范围传播 |
| S3 | 区间封锁 | DJK-ZBD | G1563 | 20分钟 | 严重延误 |
| S4 | 高峰故障 | SJP | （高峰列车） | 25分钟 | 高密度影响 |
| S5 | 多列车延误 | BDD | 3列 | 各10-15分钟 | 并发延误 |
| S6 | 轻微延误 | XSD | G1565 | 5分钟 | 微延误测试 |
| S7 | 大枢纽扰动 | SJP | 5列以上 | 30分钟以上 | 压力测试 |

### 8.2 推荐对比矩阵

每个场景评估：
- NoOp（基线）
- FCFS
- MIP（若可行）
- MaxDelayFirst
- Hierarchical
- EAF

报告：最大延误、平均延误、总延误、受影响列车数、计算时间、准点率、传播系数、Pareto最优解集。

### 8.3 统计报告

期刊投稿应报告：
1. **点估计**：场景重复的平均指标值
2. **变异性**：标准差或置信区间
3. **显著性**：算法对之间的配对t检验或Wilcoxon符号秩检验
4. **效应量**：相对基线的百分比改进及置信区间

### 8.4 计算环境

为可复现性，记录：
- CPU、内存、Python版本、PuLP版本、CBC版本、LLM API版本

---

## 9. PolicyEngine 安全层评估

### 9.1 评估维度

对PolicyEngine的评估应包含：
1. **拦截率**：异常方案被正确拦截的比例
2. **误拦截率**：正常方案被错误拦截的比例
3. **响应延迟**：PolicyEngine决策耗时（通常<1ms）
4. **规则覆盖率**：各场景类型的阈值是否覆盖实际运营需求

### 9.2 阈值标定方法

建议通过历史调度数据标定：
- 收集N个真实调度案例的专家决策
- 调整阈值使PolicyEngine决策与专家决策一致率>95%
- 使用交叉验证防止过拟合
