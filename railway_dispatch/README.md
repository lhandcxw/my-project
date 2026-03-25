# 铁路调度Agent系统

基于Qwen大模型和整数规划的智能铁路调度优化系统。

## 系统架构

```
railway_dispatch/
├── data/                     # 数据层
│   ├── trains.json          # 列车时刻表（真实数据）
│   ├── stations.json        # 车站数据（真实数据）
│   └── scenarios/           # 场景数据
├── evaluation/              # 评估层
│   └── evaluator.py        # 方案评估
├── models/                  # 数据模型层
│   ├── data_models.py      # Pydantic模型
│   └── data_loader.py      # 数据加载器（统一入口）
├── railway_agent/                # Agent模块
│   ├── qwen_agent.py       # Qwen Agent核心
│   ├── ollama_agent.py     # Ollama Agent变体
│   ├── tool_registry.py    # MCP Tools注册表
│   └── dispatch_skills.py  # 调度Skills
├── rules/                   # 约束规则层
│   └── validator.py        # 规则验证器
├── solver/                  # 求解器层
│   └── mip_scheduler.py    # MIP整数规划求解器
├── visualization/           # 可视化层
│   └── simple_diagram.py   # 运行图生成
└── web/                     # Web层
    └── app.py              # Flask Web应用
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动Web服务

```bash
cd railway_dispatch
python web/app.py
```

### 3. 访问系统

打开浏览器访问: http://localhost:8080

## 核心功能

### 智能调度
- **自然语言输入**: 用自然语言描述调度需求，Agent自动识别场景
- **表单输入**: 传统表单方式配置延误场景
- **场景识别**: 自动识别临时限速、突发故障等场景
- **整数规划优化**: 使用MIP求解器生成最优调度方案

### Agent能力
- 基于Qwen大模型的场景理解（可选，需要配置模型路径）
- Skills模式的调度技能调用
- 规则引擎模式（无需大模型也可运行）

### 可视化
- 优化后时刻表展示
- 运行图对比

## 核心模块

### 1. Qwen Agent (`railway_agent/`)
- `qwen_agent.py`: Agent核心，场景识别和功能调用（需要配置Qwen模型）
- `ollama_agent.py`: Ollama模型变体
- `tool_registry.py`: Tools注册和执行
- `dispatch_skills.py`: 调度Skills实现

### 2. MIP求解器 (`solver/mip_scheduler.py`)
- 混合整数规划模型
- 追踪间隔约束
- 区间运行时间约束

### 3. Skills (`railway_agent/dispatch_skills.py`)
- `TemporarySpeedLimitSkill`: 临时限速场景
- `SuddenFailureSkill`: 突发故障场景

### 4. 数据模型 (`models/`)
- `data_models.py`: Pydantic模型定义
- `data_loader.py`: 统一数据加载器

## 数据说明

### 真实数据
系统统一使用 `data/` 目录下的真实数据：
- **13个车站**: 北京西(BJX) → 杜家坎线路所(DJK) → 涿州东(ZBD) → 高碑店东(GBD) → 徐水东(XSD) → 保定东(BDD) → 定州东(DZD) → 正定机场(ZDJ) → 石家庄(SJP) → 高邑西(GYX) → 邢台东(XTD) → 邯郸东(HDD) → 安阳东(AYD)
- **147列列车**: 真实高铁时刻表数据

### 使用注意
- MIP求解器在大规模数据下可能不可行，Web应用默认限制为前50列列车
- 如需调整，可在 `web/app.py` 中修改 `trains = all_trains[:50]` 行

## 技术栈

- **大模型**: Qwen (支持 ModelScope 或 Ollama 本地模型，可选)
- **求解器**: PuLP + CBC (整数规划)
- **Web框架**: Flask
- **数据验证**: Pydantic
- **可视化**: Matplotlib

## Agent模式说明

| 模式 | 文件 | 说明 |
|------|------|------|
| Qwen Agent | `railway_agent/qwen_agent.py` | 使用ModelScope加载模型，自动下载 |
| Ollama Agent | `railway_agent/ollama_agent.py` | 使用Ollama API调用本地模型 |
| 规则引擎 | 默认 | 无需大模型，直接使用Skills执行 |

**使用Ollama模式**：
1. 启动Ollama服务：`ollama serve`
2. 下载模型：`ollama pull qwen2.5:4b`
3. 修改 `web/app.py` 中的导入语句使用 `ollama_agent`

**使用Qwen模型**：
1. 在 `web/app.py` 中配置 `MODEL_PATH`（如 "Qwen/Qwen2.5-0.5B"）
2. 系统会自动下载模型

**不使用大模型**（默认）：
- 将 `USE_QWEN_AGENT = False` 或留空 `MODEL_PATH`
- 系统会直接使用规则引擎模式

## 版本

- v2.3: 统一使用真实数据，移除示例数据
- v2.2: 重构为railway_agent模块，新增Ollama支持，修复命名冲突
- v2.1: 修复MIP求解器约束问题，支持真实数据
- v2.0: 新增Qwen Agent智能调度
- v1.1: 新增统一数据加载器、约束规则验证器
- v1.0: 初版，支持临时限速和突发故障场景

## 许可证

MIT License
