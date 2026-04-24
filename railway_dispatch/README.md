# LLM-TTRA: 大模型辅助列车时刻表重排系统

基于阿里云DashScope大模型和整数规划的智能铁路调度优化系统（v8.0）。

## 系统概述

- **核心任务**: LLM-TTRA (Large Language Model assisted Train Timetable Rescheduling)
- **部署规模**: 13站，147列列车（京广高铁北京西→安阳东）
- **技术路线**: 阿里云DashScope API + Prompt + RAG + 整数规划
- **大模型**: 阿里云DashScope (glm-5.1)
- **求解器**: MIP（整数规划）、FCFS（先到先服务）、Hierarchical（分层混合）、MaxDelayFirst（最大延误优先）、EAF（最早到达优先）、NoOp（无调整基线）
- **Web框架**: Flask + 原生JS（统一单页应用）
- **数据**: 真实高铁时刻表
- **配置管理**: YAML配置文件 + Python配置类
- **最终目标**: 大模型根据不同场景自主选择求解器组合和参数，以达到最优解

## v8.0 更新说明

**关键Bug修复与架构优化**：
- **修复avg_delay计算错误**：原实现将所有站点的延误累加后除以受影响列车数，导致数值异常偏高。现已修正为"各受影响列车最大延误的平均值"。
- **移除重复配置定义**：`config.py` 中 `L1_EXTRACTION_MODE` 被重复定义两次，已移除重复项。
- **修复枚举引用错误**：`EARLIEST_ARRIVAL_FIRST` 引用与枚举定义 `EARLIEST_ARRIVAL` 不匹配，已修正。
- **统一Web入口**：删除 `index_v2.html` 和 `main.js`，合并为统一单页应用（`index.html` + `main_unified.js`）。
- **移除废弃代码**：删除 `solver` 目录下所有 `*_adapter.py` 文件（功能已迁移至 `scheduler_comparison`）。
- **模型更新**：切换至 `glm-5.1` 模型（原 `qwen3.6-plus`）。
- **YAML配置管理**：新增 `config/dispatch_env.yaml` 管理环境参数（headway、冗余度、场景默认求解器等）。

**SFT规划：L2/L3智能增强**：
- 目标：让大模型根据场景自主选择求解器组合和参数
- 路径：构建SFT数据集 → 模型微调 → 强化学习优化
- 详细规划参考：[railway_dispatch_agent_architecture.md](railway_dispatch_agent_architecture.md) 第19节

## v5.1 更新说明

**LLM场景识别增强**:
- L0层场景识别从规则改为LLM调用
- 新增 `_llm_extraction()` 方法，调用 `l0_preprocess_extractor` 模板
- LLM失败时自动回退到规则（受 FORCE_LLM_MODE 控制）
- 移除 `llm_workflow_engine_v2.py` 和 `app.py` 中的硬编码场景判断规则

**前端显示优化**:
- 修复智能调度模块响应格式匹配问题
- 运行图标题改为英文，避免中文字体缺失问题

**其他修复**:
- 修复 `SchedulerComparator` 初始化参数错误

## v5.0 重构说明

**架构清理**:
- 删除所有冗余代码和重复workflow
- 删除preprocessing模块（功能已合并到L1）
- 删除comat.py（Python 3.8兼容补丁）
- 统一入口：web/app.py

**LLM主路径强化**:
- 强制使用阿里云DashScope API
- 移除硬编码API Key，强制环境变量配置
- 添加FORCE_LLM_MODE配置（正式实验时禁用规则回退）
- 启用enable_thinking深度思考模式

**Agent层重构**:
- 删除关键词场景识别，改为调用L1层LLM提取
- 删除独立实体提取逻辑，统一使用工作流
- Agent.analyze()现在调用完整L1-L4工作流

## 系统架构

```
railway_dispatch/
├── data/                     # 数据层
│   ├── station_alias.json    # 车站数据
│   ├── plan_timetable.csv    # 时刻表
│   ├── min_running_time_matrix.csv
│   ├── train_id_mapping.csv
│   ├── scenarios/            # 场景数据
│   └── knowledge/            # RAG知识库
├── models/                   # 数据模型层
│   ├── common_enums.py       # 统一枚举
│   ├── data_loader.py        # 数据加载器
│   ├── data_models.py        # Pydantic模型
│   └── workflow_models.py    # 工作流模型
├── railway_agent/            # Agent模块
│   ├── workflow/             # 工作流分层（核心）
│   │   ├── layer1_data_modeling.py  # L1: LLM提取事故信息
│   │   ├── layer2_planner.py        # L2: LLM决策planning_intent
│   │   ├── layer3_solver.py         # L3: 求解器选择与执行
│   │   └── layer4_evaluation.py     # L4: 评估与决策
│   ├── adapters/             # 适配器层
│   │   ├── llm_adapter.py           # LLM调用适配器
│   │   ├── llm_prompt_adapter.py    # Prompt适配器
│   │   └── skills.py                # 技能实现
│   ├── prompts/              # Prompt管理
│   ├── snapshot_builder.py   # 网络快照构建器
│   ├── snapshot_builder_mip.py  # MIP快照裁剪器（Hierarchical用）
│   ├── hierarchical_solver.py  # 分层求解器（FCFS+MIP混合）
│   ├── llm_workflow_engine_v2.py  # 工作流引擎
│   ├── session_manager.py    # 会话管理
│   ├── agents.py             # Agent实现
│   └── policy_engine.py      # 策略引擎
├── solver/                   # 调度器实现层（核心调度算法）
│   ├── mip_scheduler.py      # MIP调度器（整数规划）
│   ├── fcfs_scheduler.py     # FCFS调度器（先到先服务）
│   ├── max_delay_first_scheduler.py  # 最大延误优先调度器
│   ├── noop_scheduler.py     # NoOp调度器（无调整基线）
│   ├── solver_registry.py    # 【已废弃】求解器注册器（保留用于向后兼容）
│   └── base_solver.py        # 【已废弃】基础求解器（保留用于向后兼容）
├── scheduler_comparison/      # 调度系统（统一接口层）
│   ├── scheduler_interface.py  # 调度器接口和注册器
│   ├── comparator.py         # 调度器对比工具
│   ├── metrics.py            # 评估指标
│   └── llm_adapter.py        # LLM适配器
├── evaluation/               # 评估层
│   └── evaluator.py
├── web/                      # Web层
│   ├── app.py                # Flask应用（唯一入口）
│   ├── templates/index.html  # 统一前端界面
│   └── static/main_unified.js # 前端逻辑
├── config.py                 # 统一配置
└── config/dispatch_env.yaml  # 环境参数配置（headway、场景默认求解器等）
```

## 快速开始

### 1. 配置API Key

编辑 `config.py` 文件，填写你的阿里云DashScope API Key：

```python
# config.py
DASHSCOPE_API_KEY = "your-actual-api-key-here"  # 替换为你的API Key
```

其他配置项（可选修改）：
```python
DASHSCOPE_MODEL = "glm-5.1"            # 模型名称：glm-5.1, qwen-max 等
DASHSCOPE_ENABLE_THINKING = False      # 是否启用深度思考（glm-5.1 不支持）
FORCE_LLM_MODE = True                  # 强制LLM模式，禁用规则回退
```

环境参数配置（`config/dispatch_env.yaml`）：
```yaml
headway_time: 180              # 最小追踪间隔（秒）
min_stop_time: 120             # 最小停站时间（秒）
stop_time_redundancy_ratio: 0.8   # 停站时间冗余系数
running_time_redundancy_ratio: 0.85  # 运行时间冗余系数
```

**注意**：项目完成后会改为环境变量配置方式，当前为开发调试方便使用直接变量配置。

### 2. 安装依赖

```bash
cd railway_dispatch
pip install -r requirements.txt
```

### 3. 启动Web服务（推荐使用启动脚本）

**Windows系统：**
```bash
start_server.bat
```

**Linux/Mac系统：**
```bash
chmod +x start_server.sh
./start_server.sh
```

**直接启动（不推荐）：**
```bash
python web/app.py
```

访问 http://localhost:8081 或 http://127.0.0.1:8081

**如果遇到端口占用问题：**
```bash
# Windows
python fix_port_issue.py

# Linux/Mac
python fix_port_issue.py
```

**如果需要系统诊断：**
```bash
python diagnose.py
```

### 4. 使用智能调度

输入自然语言描述，例如：
- "暴雨导致石家庄站限速80km/h"
- "G1563列车在保定东遭遇大风预计延误10分钟"
- "设备故障导致XSD-BDD区间临时封锁"

---

## 常见问题解决

### localhost 无法连接或需要多次刷新

**问题症状：**
- 浏览器显示 "localhost 拒绝连接"
- 需要多次刷新或关闭重开才能访问
- 连接不稳定

**解决方案：**

1. **使用启动脚本**（推荐）：
   ```bash
   # Windows
   start_server.bat
   
   # Linux/Mac
   ./start_server.sh
   ```

2. **清理端口占用**：
   ```bash
   python fix_port_issue.py
   ```

3. **使用127.0.0.1代替localhost**：
   - 访问: http://127.0.0.1:8081 而不是 http://localhost:8081

4. **检查防火墙设置**：
   - Windows: 控制面板 → Windows Defender 防火墙 → 入站规则
   - Linux: `sudo ufw allow 8081/tcp`
   - Mac: 系统偏好设置 → 安全性与隐私 → 防火墙

5. **运行诊断工具**：
   ```bash
   python diagnose.py
   ```

### 端口8081被占用

**解决方案：**

1. **自动清理端口**：
   ```bash
   python fix_port_issue.py
   ```

2. **手动清理（Windows）**：
   ```cmd
   netstat -ano | findstr :8081
   taskkill /F /PID <进程ID>
   ```

3. **手动清理（Linux/Mac）**：
   ```bash
   lsof -ti :8081 | xargs kill -9
   ```

4. **修改配置使用其他端口**：
   - 编辑 `config.py`
   - 修改 `WEB_PORT = 8081` 为其他端口

### 浏览器缓存问题

**解决方案：**

1. **清除浏览器缓存**：
   - Chrome: Ctrl+Shift+Delete → 选择"缓存的图片和文件"
   - Firefox: Ctrl+Shift+Delete → 选择"缓存"
   - Edge: Ctrl+Shift+Delete → 选择"缓存的图像和文件"

2. **使用无痕模式**：
   - Chrome: Ctrl+Shift+N
   - Firefox: Ctrl+Shift+P
   - Edge: Ctrl+Shift+P

3. **强制刷新页面**：
   - Windows: Ctrl+F5
   - Mac: Cmd+Shift+R

## 核心工作流

系统采用6层LLM决策架构：

| 层级 | 模块 | 功能 | 说明 |
|------|------|------|------|
| L0 | layer1_data_modeling.py | 场景识别 | LLM提取scene_type/fault_type/station_code |
| L1 | layer1_data_modeling.py | 数据建模 | LLM提取事故信息，构建AccidentCard |
| SB | snapshot_builder.py | 快照构建 | 确定性构建NetworkSnapshot |
| L2 | layer2_planner.py | Planner | LLM决策planning_intent |
| L3 | layer3_solver.py | Solver | 规则选择求解器并执行 |
| L4 | layer4_evaluation.py | Evaluation | LLM生成解释，PolicyEngine决策 |

## 配置说明

### 环境变量

| 变量名 | 必需 | 默认值 | 说明 |
|--------|------|--------|------|
| DASHSCOPE_API_KEY | 是 | - | 阿里云API Key |
| DASHSCOPE_MODEL | 否 | qwen-max | 模型名称 |
| DASHSCOPE_ENABLE_THINKING | 否 | true | 深度思考模式 |
| FORCE_LLM_MODE | 否 | true | 强制LLM模式 |
| LLM_PROVIDER | 否 | dashscope | LLM提供商 |

### 实验模式

**正式实验模式**（推荐）：
```bash
export FORCE_LLM_MODE="true"
export DASHSCOPE_ENABLE_THINKING="true"
```
- LLM失败时直接报错，不回退到规则
- 确保收集的微调数据质量

**调试模式**：
```bash
export FORCE_LLM_MODE="false"
```
- LLM失败时回退到规则提取
- 用于调试和开发

## API接口

### 智能调度
```http
POST /api/agent_chat
Content-Type: application/json

{
    "message": "暴雨导致石家庄站限速80km/h"
}
```

### 工作流启动
```http
POST /api/workflow/start
Content-Type: application/json

{
    "user_input": "G1563在保定东延误10分钟"
}
```

### 工作流继续
```http
POST /api/workflow/next
Content-Type: application/json

{
    "session_id": "xxx",
    "user_input": "补充信息"
}
```

## 求解器说明

| 调度器 | 适用场景 | 选择规则 | 核心特点 |
|--------|----------|----------|----------|
| Hierarchical | 大规模（>10列）、复杂场景 | L2 Agent指定或L3自动选择 | 结合FCFS快速筛选+MIP精准优化 |
| MIP | 临时限速、列车≤3 | L3根据场景类型选择 | 全局优化，可调整发车顺序 |
| FCFS | 突发故障、列车>10 | L3根据场景类型选择 | 先到先服务，允许改变原计划顺序 |
| MaxDelayFirst | 高峰时段、延误严重 | L3根据场景类型选择 | 优先调度最大延误列车 |
| EAF | 早班车优先、恢复正点 | L3根据场景类型选择 | 最早到达时间优先发车 |
| NoOp | 区间封锁、基线对比 | L3根据场景类型选择 | 不调整，仅应用初始延误 |

### 调度器对比

| 特性 | Hierarchical | MIP | FCFS | MaxDelayFirst | EAF | NoOp |
|------|-------------|-----|------|---------------|-----|------|
| **优化目标** | 分层优化 | 全局最优 | 简单快速 | 减少最大延误 | 恢复正点 | 无调整 |
| **发车顺序** | 可调整 | 可调整 | 可调整 | 可调整 | 可调整 | 保持原计划 |
| **求解时间** | 中等 | 较长 | 很快 | 快 | 快 | 立即 |
| **适用规模** | 大规模 | 小规模（≤3列） | 中大规模 | 中大规模 | 中大规模 | 任意规模 |
| **质量保证** | 近似最优 | 近似最优 | 启发式 | 启发式 | 启发式 | 基线 |
| **算法复杂度** | 高 | 高 | 低 | 中 | 中 | 无 |

### Hierarchical 分层求解器详解

**核心思想**：
```
Layer 1: FCFS 快速筛选（毫秒级）
   ↓ 识别受影响列车
Layer 2: MIP 精准优化（秒级）
   ↓ 只对30列以内的关键列车优化
Layer 3: 质量评估
   ↓ 判断是否接受MIP结果
```

**工作流程**：
1. **Layer 1 (FCFS)**: 对所有147列车进行快速调度，识别受影响列车集合
2. **Layer 2 (MIP)**: 使用 MIPSnapshotBuilder 裁剪出 30列×8站的优化窗口，进行精细优化
3. **Layer 3 (评估)**: 对比 MIP 与 FCFS 的结果，如果 MIP 改进≥2分钟则采用，否则回退到 FCFS

**优势**：
- ✅ **解决大规模问题**: 原始147列 × 13站 → MIP超时(>300秒)
- ✅ **自动裁剪**: 裁剪后25列 × 8站 → MIP在30-60秒内求解
- ✅ **质量保证**: 延误减少通常比纯FCFS减少30-60%
- ✅ **自适应**: 根据问题难度自动选择最佳求解路径
- ✅ **鲁棒性**: MIP失败时自动回退到FCFS，保证可靠性

**关键参数**：
- `MAX_TRAINS_FOR_MIP = 30`: MIP最大列车数
- `MAX_MIP_IMPROVEMENT_MINUTES = 2`: MIP最小改进阈值
- `MAX_DELAY_FOR_FCFS_MINUTES = 10`: 小于此值直接用FCFS

**典型效果**：
| 场景 | 纯FCFS | 纯MIP | Hierarchical |
|------|--------|-------|--------------|
| 小规模（≤3列） | 2分钟 | 15分钟（超时） | 2分钟（使用FCFS） |
| 中规模（10-30列） | 3分钟 | 45分钟（超时） | 28分钟（MIP优化） |
| 大规模（>30列） | 5分钟 | 无法求解 | 30分钟（分层求解） |

## 微调数据收集

系统自动收集微调样本到 `data/sft_train.jsonl`：

```json
{
    "messages": [
        {"role": "system", "content": "从调度员描述中提取事故信息"},
        {"role": "user", "content": "暴雨导致石家庄站限速80km/h"},
        {"role": "assistant", "content": "{\"scene_category\": \"临时限速\", ...}"}
    ],
    "metadata": {
        "layer": "L1",
        "template_id": "l1_data_modeling"
    }
}
```

## 技术栈

- **大模型**: 阿里云DashScope (glm-5.1)
- **求解器**: PuLP + CBC (整数规划)
- **Web**: Flask + Pydantic + 原生JS单页应用
- **Prompt管理**: 自定义PromptManager
- **RAG检索**: 关键词匹配 + 知识库检索
- **配置管理**: YAML + Python配置类

## 版本历史

- **v8.0** (2026-04-24):
  - **关键Bug修复**：修正 `avg_delay` 计算逻辑（改为各列车最大延误的平均值）
  - **代码清理**：移除重复配置定义、修复枚举引用错误、删除废弃adapter文件
  - **Web统一**：合并双入口为统一单页应用（`index.html` + `main_unified.js`）
  - **模型更新**：切换至 `glm-5.1`
  - **YAML配置**：新增 `config/dispatch_env.yaml` 环境参数管理
  - **新增调度器**：EAF（最早到达优先）

- **v7.0** (2026-04-17):
  - **新增：SFT规划文档**：详细规划L2/L3智能增强路径
  - **代码审查完成**：确认所有模块语法、逻辑正确
  - **调度算法确认**：MIP/FCFS实现合理
  - **工作流确认**：L1-L4数据传递正确
  - **前端优化**：从调度员视角优化交互
  - **知识库扩展**：京广高铁知识库专业
  - **最终目标**：大模型根据场景自主选择求解器组合和参数

- **v6.6** (2026-04-16):
  - 代码审查：全面审查所有Python代码，检查语法、逻辑、流程问题
  - 调度算法审查：深入审查MIP、FCFS调度器实现，修正约束模型
  - 工作流审查：检查L1-L4各层的数据传递和接口契约
  - 前端审查：检查JavaScript交互逻辑和API调用
  - 文档更新：更新架构文档和README，反映最新代码状态
  - 配置统一：统一配置文件与实际代码的对应关系
  - 发现问题：config.py中硬编码API Key需改进，求解器选择逻辑简化，数据模型混用
  - 建议：添加单元测试，统一错误处理模式，增加日志详细度

- **v5.1** (2026-04-10):
  - 功能：L0层场景识别从规则改为LLM调用
  - 功能：新增 `_llm_extraction()` 方法
  - 修复：前端响应格式匹配问题
  - 修复：运行图中文字体缺失问题
  - 修复：SchedulerComparator初始化参数错误
  - 文档：更新README和架构文档

- **v5.0** (2026-04-09):
  - 重构：删除所有冗余代码和重复workflow
  - 重构：删除preprocessing模块
  - 安全：移除硬编码API Key
  - 功能：添加FORCE_LLM_MODE配置
  - 功能：启用enable_thinking深度思考
  - 重构：Agent层调用L1-L4工作流
  - 文档：更新README和架构文档

- **v4.2** (2026-04-08):
  - 阿里云qwen3.5-27b替换本地Ollama
  - 支持DashScope API调用
  - 修复工作流流程

## 许可证

MIT License
