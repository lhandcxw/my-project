# LLM-TTRA：算法规范

**文档版本**：1.0  
**日期**：2026-04-26  
**用途**：MIP、FCFS、分层求解器及多算法对比框架的正式算法描述。适用于论文Methods章节引用。

---

## 1. 问题建模

### 1.1 符号体系

| 符号 | 说明 |
|------|------|
| $\mathcal{T}$ | 列车集合，$|\mathcal{T}| = n$ |
| $\mathcal{S}$ | 车站集合，$|\mathcal{S}| = m$ |
| $t_i$ | 列车 $i$，$i \in \{1, \dots, n\}$ |
| $s_j$ | 车站 $j$，$j \in \{1, \dots, m\}$ |
| $a_{ij}$ | 列车 $t_i$ 在 $s_j$ 的计划到达时间（自午夜起秒数） |
| $d_{ij}$ | 列车 $t_i$ 从 $s_j$ 的计划发车时间 |
| $a'_{ij}$ | 实际到达时间（决策变量） |
| $d'_{ij}$ | 实际发车时间（决策变量） |
| $h$ | 最小追踪间隔，$h = 180$ 秒 |
| $\tau^{\min}_{j,j+1}$ | $s_j$ 到 $s_{j+1}$ 的最小区间运行时间 |
| $\sigma^{\min}_{j}$ | $s_j$ 的最小停站时间，$\sigma^{\min}_{j} = 120$ 秒 |
| $c_j$ | $s_j$ 的股道数 |
| $\delta_{ij}$ | 注入到列车 $t_i$ 在 $s_j$ 的初始延误（秒） |

### 1.2 决策变量（MIP）

对每列列车 $t_i$ 及其径路上的每个车站 $s_j$：

- $A_{ij} \in \mathbb{Z}_{\geq 0}$：实际到达时间
- $D_{ij} \in \mathbb{Z}_{\geq 0}$：实际发车时间
- $\Delta^{\text{arr}}_{ij} \in \mathbb{Z}_{\geq 0}$：到达延误
- $\Delta^{\text{dep}}_{ij} \in \mathbb{Z}_{\geq 0}$：发车延误
- $M \in \mathbb{Z}_{\geq 0}$：所有列车与车站中的最大延误

### 1.3 目标函数

系统支持三种优化目标：

**O1：最小化总延误**（默认，与中国铁路KPI对齐）
$$\min \sum_{i \in \mathcal{T}} \sum_{j \in \mathcal{S}_i} \left( \Delta^{\text{arr}}_{ij} + \Delta^{\text{dep}}_{ij} \right)$$

**O2：最小化最大延误**（关键列车保障）
$$\min \quad M$$

**O3：最小化平均延误**（服务质量）
与O1在最优解集上等价，但评估权重不同。

---

## 2. MIP调度器（`solver/mip_scheduler.py`）

### 2.1 完整约束集

**C1：初始延误注入**
对每个注入的延误 $\delta_{ij} > 0$：
$$A_{ij} \geq a_{ij} + \delta_{ij}$$

*原理*：列车物理上晚到；MIP可通过压缩停站和区间时间恢复部分延误。

**C2：区间运行时间下限**
对每列列车 $t_i$ 及其径路上相邻车站 $(s_j, s_{j+1})$：
$$A_{i,j+1} - D_{ij} \geq \tau^{\min}_{j,j+1} \times 0.8$$

系数0.8允许在历史最小值基础上压缩20%（为制动/加速保留安全裕量）。

**C3：追踪间隔（Tracking Interval）**
在每个股道数 $c_j \geq 1$ 的车站 $s_j$，按计划发车时间排序列车。对同股道上相邻列车 $t_i, t_k$：
$$D_{kj} - D_{ij} \geq h$$

股道分配采用轮询：$\text{track}(t_i) = \text{sort\_index}(t_i) \bmod c_j$。

**C4：最小停站时间**
对每列在车站 $s_j$ 停车（$a_{ij} < d_{ij}$）的列车 $t_i$：
$$D_{ij} - A_{ij} \geq \sigma^{\min}_{j}$$

**C5：延误定义**
对所有 $(i, j)$：
$$\Delta^{\text{arr}}_{ij} \geq A_{ij} - a_{ij}, \quad \Delta^{\text{dep}}_{ij} \geq D_{ij} - d_{ij}$$
$$M \geq \Delta^{\text{arr}}_{ij}, \quad M \geq \Delta^{\text{dep}}_{ij}$$

**C6：时间单调性**
对每列列车 $t_i$：
$$A_{ij} \leq D_{ij} \quad \text{（停站非负）}$$
$$D_{ij} \leq A_{i,j+1} \quad \text{（顺序行驶）}$$

### 2.2 求解器配置

- **引擎**：PuLP + CBC后端
- **时间限制**：300秒
- **最优性间隙**：0.01（1%）
- **问题规模**：可变；经分层裁剪后通常为25-30列×6-8站

---

## 3. FCFS调度器（`solver/fcfs_scheduler.py`）

### 3.1 算法概述

FCFS是一种确定性启发式算法，模拟真实调度员行为：列车按计划顺序行进，当追踪间隔被违反时，延误向前传播。

### 3.2 伪代码

```
算法：FCFS调度器
输入：列车集合 T，车站集合 S，延误注入 DI
输出：优化时刻表 S_opt，指标

1: 根据计划时刻表初始化调度
2: // 步骤1：应用初始延误
3: for DI 中的每个 (train_id, station_code, delay)：
4:     idx = station_code 在列车径路中的索引
5:     for 列车径路中后续每个车站 s（从idx开始）：
6:         S_opt[train_id, s].arrival += delay
7:         S_opt[train_id, s].departure += delay
8:
9: // 步骤2：按追踪间隔传播（逐站处理）
10: for S 中的每个车站 s（按径路顺序）：
11:     if s.track_count == 0: continue  // 跳过线路所
12:     trains_at_s = 所有在 s 停靠的列车
13:     按 original_departure_time 排序 trains_at_s
14:     last_departures = [0] * s.track_count
15:     for idx, train in enumerate(trains_at_s)：
16:         track = idx % s.track_count
17:         required_dep = max(
18:             S_opt[train, s].departure,
19:             last_departures[track] + headway
20:         )
21:         delay_needed = required_dep - S_opt[train, s].departure
22:         if delay_needed > 0：
23:             for 列车径路中后续每个车站 s'：
24:                 S_opt[train, s'].arrival += delay_needed
25:                 S_opt[train, s'].departure += delay_needed
26:         last_departures[track] = S_opt[train, s].departure
27:
28: // 步骤3：冗余恢复
29: for T 中的每列列车 t：
30:     for t 的径路中每个车站 s：
31:         // 停站时间压缩
32:         original_dwell = s 处的计划停站时间
33:         min_dwell = max(min_stop_time, original_dwell // 2)
34:         redundancy = original_dwell - min_dwell
35:         current_delay = 列车当前位置的最大延误
36:         if redundancy > 0 and current_delay > 0：
37:             recover = min(redundancy, current_delay)
38:             S_opt[t, s].departure -= recover
39:             将 -recover 传播到所有后续车站
40:
41:         // 区间运行时间压缩
42:         if s 不是最后一站：
43:             s_next = 径路中的下一站
44:             original_section = 计划运行时间
45:             min_section = min_running_time[s, s_next]
46:             redundancy = original_section - min_section
47:             if redundancy > 0：
48:                 current_interval = arrival[s_next] - departure[s]
49:                 recover = min(redundancy, current_interval - min_section, current_delay)
50:                 if recover > 0：
51:                     将 -recover 传播到所有后续车站
```

### 3.3 时间复杂度

- 步骤1：$O(n \cdot m_{\max})$，$m_{\max}$为每列列车最多车站数
- 步骤2：$O(m \cdot n \log n)$，因每站需排序
- 步骤3：$O(n \cdot m_{\max})$
- **总计**：$O(m \cdot n \log n)$
- **实测运行时间**：147列列车约1-3秒

---

## 4. 分层求解器（`railway_agent/hierarchical_solver.py`）

### 4.1 动机

纯MIP在全问题（147列×13站）上产生约5000个整数变量并超时。纯FCFS速度快但次优。分层求解器在最优性与可扩展性之间取得平衡。

### 4.2 算法

```
算法：HierarchicalSolver
输入：完整列车集合 T，车站集合 S，延误注入 DI，目标 O
参数：MAX_TRAINS_FOR_MIP=30，MAX_DELAY_FOR_FCFS=5分钟，MIN_MIP_IMPROVEMENT=1分钟
输出：HierarchicalResult

1: // 第一层：FCFS基线
2: fcfs_result = FCFS.solve(DI, O)
3: if fcfs_result.max_delay < MAX_DELAY_FOR_FCFS：
4:     return fcfs_result  // 微延误，FCFS已足够
5:
6: // 第二层：动态MIP窗口构建
7: affected_trains = 从 fcfs_result 中提取延误>0的列车
8: incident_station = DI.primary_location
9:
10: // 根据最大延误量级确定窗口半径
11: max_delay = fcfs_result.max_delay
12: if max_delay < 10 分钟: radius = 3站
13: elif max_delay < 30 分钟: radius = 4站
14: elif max_delay < 60 分钟: radius = 5站
15: else: radius = 6站
16:
17: center_idx = incident_station 在径路中的索引
18: window_stations = S[max(0, center_idx - radius) : min(m, center_idx + radius)]
19:
20: // 确保所有注入延误的列车均被包含
21: window_trains = affected_trains 并集 DI.affected_trains
22: if |window_trains| > MAX_TRAINS_FOR_MIP：
23:     // 按总延误贡献排序，保留前30列
24:     window_trains = sort(window_trains, key=total_delay, descending)[:30]
25:
26: // 将列车时刻表裁剪到窗口车站
27: cropped_trains = [crop(t, window_stations) for t in window_trains]
28: cropped_stations = [s for s in S if s in window_stations]
29:
30: // 第二层b：在裁剪后的问题上运行MIP
31: mip_result = MIP.solve(DI, O, trains=cropped_trains, stations=cropped_stations)
32:
33: // 第三层：质量评估
34: if not mip_result.success：
35:     return fcfs_result
36:
37: improvement = fcfs_result.total_delay - mip_result.total_delay
38: if improvement < MIN_MIP_IMPROVEMENT：
39:     return fcfs_result  // MIP改进不足
40:
41: // 将MIP结果合并回完整FCFS时刻表
42: for 窗口中的每个 (train, station)：
43:     if station 同时存在于 fcfs 和 mip 时刻表：
44:         full_schedule[train, station] = mip_schedule[train, station]
45:     else：
46:         full_schedule[train, station] = fcfs_schedule[train, station]
47:
48: recalculate_metrics(full_schedule)
49: return merged_result
```

### 4.3 安全性保证

- **回退保证**：若MIP失败或改进改进<1分钟，返回FCFS结果。
- **数据保护**：窗口外车站保留FCFS值，无数据丢失。
- **受影响列车覆盖**：所有注入延误的列车被强制包含在MIP窗口中。

---

## 5. 多算法对比框架

### 5.1 评分函数

对每个调度器 $s$，计算归一化分量得分（越低越好）：

$$\text{score}_{\max} = \min\left(\frac{\max\_delay}{30 \times 60} \times 100, 100\right)$$
$$\text{score}_{\text{avg}} = \min\left(\frac{\text{avg\_delay}}{30 \times 60} \times 100, 100\right)$$
$$\text{score}_{\text{total}} = \min\left(\frac{\text{total\_delay}}{120 \times 60} \times 100, 100\right)$$
$$\text{score}_{\text{on\_time}} = (1 - \text{on\_time\_rate}) \times 100$$
$$\text{score}_{\text{affected}} = \min\left(\frac{\text{affected\_trains}}{10} \times 100, 100\right)$$
$$\text{score}_{\text{comp}} = \min\left(\frac{\text{computation\_time}}{60} \times 100, 100\right)$$

带归一化权重 $\mathbf{w}$ 的综合得分：
$$\text{Score}(s) = \sum_{k \in K} w_k \cdot \text{score}_k$$

### 5.2 准则特定权重

| 准则 | 使用场景 | 权重配置 |
|------|---------|---------|
| `min_total_delay` | 默认 / 系统效率 | total=0.35, avg=0.20, max=0.15 |
| `min_max_delay` | 关键列车保障 | max=0.35, total=0.15, avg=0.15 |
| `min_avg_delay` | 乘客服务质量 | avg=0.30, total=0.20, max=0.15 |
| `min_propagation` | 高密度线路控制 | affected=0.25, depth=0.15, breadth=0.10 |
| `real_time` | 应急响应 | comp=0.30, max=0.20, total=0.15 |

### 5.3 优胜者选择

按得分升序排列调度器。标记优胜者，并报告每项指标相对NoOp基线的改进。

---

## 6. 快照构建器

### 6.1 目的

在L2/L3执行前，将完整网络（147列、13站）基于事故描述裁剪为相关子问题。

### 6.2 裁剪规则

1. **时间裁剪**：排除在事故发生时间之前已通过事故地点的列车。
2. **空间裁剪**：仅包含事故地点±N站范围内的车站（N可配置，默认6）。
3. **径路裁剪**：仅包含径路与受影响走廊相交的列车。
4. **密度裁剪**：若剩余列车>30列，按与事故点接近程度排序并保留前30列。

### 6.3 输出

`NetworkSnapshot`包含：
- `candidate_train_ids`：可能需要调整的列车
- `excluded_train_ids`：已通过事故点的列车
- `solving_window`：`{corridor_id, window_start_station, window_end_station}`
- `trains` / `stations`：裁剪后的数据对象

---

## 7. 约束验证

系统对所有求解器输出进行如下验证：

1. **时间单调性**：$A_{ij} \leq D_{ij}$ 且 $D_{ij} \leq A_{i,j+1}$
2. **追踪间隔合规**：同股道相邻列车满足 $D_{kj} - D_{ij} \geq h$
3. **区间时间下限**：$A_{i,j+1} - D_{ij} \geq 0.8 \times \tau^{\min}_{j,j+1}$
4. **通过站排除**：$a_{ij} = d_{ij}$（不停车）的车站免于停站约束检查

所有求解器结果在返回Agent层之前自动执行验证。
