# LLM-TTRA 项目架构优化总结

## 修改概述

作为列车调度和大模型交叉领域的专家，我对LLM-TTRA项目进行了全面的架构审查和优化。主要目标是：
1. 为微调准备提供完善的Prompt管理系统
2. 应用适配器模式，提高代码可维护性
3. 拆分大型文件，遵循单一职责原则
4. 增强真实高铁调度场景的领域知识
5. 删除冗余代码和调试信息

## 核心问题识别

### 1. Prompt管理缺失
**问题**：Prompt硬编码在工作流引擎中，无法统一管理和优化
**影响**：难以进行prompt工程，无法为微调准备数据

### 2. 工作流引擎过大
**问题**：llm_workflow_engine.py有1509行，包含过多职责
**影响**：难以维护和测试，违反单一职责原则

### 3. Adapter模式应用不充分
**问题**：LLMAdapter过于简单，未真正发挥适配器优势
**影响**：难以切换模型，无法统一接口

### 4. 数据模型重复
**问题**：枚举定义分散，缺少Prompt相关模型
**影响**：数据不一致，类型不安全

### 5. 真实场景适配不足
**问题**：场景类型简化，约束不完整，RAG知识不足
**影响**：无法应对真实高铁调度的复杂场景

## 主要修改内容

### 一、新增文件

#### 1. Prompt管理系统
- **`models/prompts.py`** - Prompt数据模型
  - `PromptTemplate`: Prompt模板模型
  - `PromptContext`: Prompt上下文模型
  - `PromptRequest/Response`: Prompt请求/响应模型
  - `FineTuningSample`: 微调样本模型

- **`railway_agent/prompts/prompt_manager.py`** - Prompt管理器
  - 统一管理所有Prompt模板
  - 提供模板注册、检索、填充功能
  - 支持微调样本收集和导出

- **`railway_agent/adapters/llm_prompt_adapter.py`** - LLM Prompt适配器
  - 连接Prompt管理器和LLM调用器
  - 自动处理Prompt填充、LLM调用、结果解析
  - 支持RAG增强和回退机制

#### 2. 工作流分层模块
- **`railway_agent/workflow/`** - 工作流分层模块目录
  - **`layer1_data_modeling.py`** - 第一层：数据建模层
  - **`layer2_planner.py`** - 第二层：Planner层
  - **`layer3_solver.py`** - 第三层：求解技能层
  - **`layer4_evaluation.py`** - 第四层：评估层

#### 3. 新版工作流引擎
- **`railway_agent/llm_workflow_engine_v2.py`** - 新版工作流引擎
  - 应用分层模块和适配器模式
  - 代码量大幅减少，职责清晰
  - 保持向后兼容

### 二、修改文件

#### 1. RAG检索器增强
**文件**: `railway_agent/rag_retriever.py`

**修改内容**:
- 添加真实高铁调度场景的领域知识
- 新增"京广高铁网络"、"调度约束"、"延误处理"等知识模块
- 增强关键词匹配算法
- 提高知识检索的相关性

**新增知识**:
```python
- 京广高铁网络信息（13站、147列）
- 关键区间（XSD-BDD、BDD-DZD等）
- 车站作业时间标准
- 延误处理策略
- 调度约束（时间、空间、容量）
```

#### 2. RAG适配器增强
**文件**: `railway_agent/adapters/rag_adapter.py`

**修改内容**:
- 修改返回类型为`List[Dict]`（包含元数据）
- 添加知识长度限制，避免prompt过长
- 改进知识注入策略

### 三、删除和简化

#### 1. 冗余调试信息
- 删除工作流中的`print`调试语句
- 保留必要的`logger.info`日志

#### 2. 重复代码
- 统一使用Prompt管理器，消除重复的Prompt定义
- 统一使用适配器，消除重复的LLM调用代码

## 架构改进

### 旧架构
```
llm_workflow_engine.py (1509行)
├── LLMCaller (LLM调用)
├── Layer1 (数据建模) - 包含Prompt硬编码
├── Layer2 (Planner) - 包含Prompt硬编码
├── Layer3 (Solver) - 包含求解器选择逻辑
├── Layer4 (Evaluation) - 包含Prompt硬编码
└── PolicyEngine (策略引擎)
```

### 新架构
```
models/
├── prompts.py (Prompt数据模型)

railway_agent/
├── prompts/
│   └── prompt_manager.py (Prompt管理)
├── adapters/
│   ├── llm_adapter.py (LLM调用适配器)
│   ├── llm_prompt_adapter.py (新增：LLM Prompt适配器)
│   └── rag_adapter.py (RAG适配器，已增强)
├── workflow/ (新增：工作流分层)
│   ├── layer1_data_modeling.py
│   ├── layer2_planner.py
│   ├── layer3_solver.py
│   └── layer4_evaluation.py
├── llm_workflow_engine.py (保留：旧版本，向后兼容)
└── llm_workflow_engine_v2.py (新增：精简版，应用新模式)
```

## 使用方式

### 1. 使用新版工作流引擎

```python
from railway_agent.llm_workflow_engine_v2 import create_workflow_engine

# 创建工作流引擎
engine = create_workflow_engine()

# 执行完整工作流
result = engine.execute_full_workflow(
    user_input="暴雨导致石家庄站限速80km/h",
    enable_rag=True
)

# 查看结果
print(result.success)
print(result.message)
print(result.debug_trace)
```

### 2. 使用Prompt管理器

```python
from railway_agent.prompts import get_prompt_manager
from models.prompts import PromptContext

# 获取Prompt管理器
prompt_manager = get_prompt_manager()

# 构建上下文
context = PromptContext(
    request_id="test_001",
    user_input="暴雨导致石家庄站限速80km/h",
    scene_type="临时限速"
)

# 填充Prompt
filled_prompt = prompt_manager.fill_template(
    template_id="l1_data_modeling",
    context=context,
    enable_rag=True
)

# 收集微调样本
sample = prompt_manager.collect_fine_tuning_sample(
    template_id="l1_data_modeling",
    context=context,
    expected_output={"scene_category": "临时限速度", ...}
)

# 导出微调样本
prompt_manager.export_fine_tuning_samples("fine_tuning_data.jsonl")
```

### 3. 使用LLM Prompt适配器

```python
from railway_agent.adapters.llm_prompt_adapter import get_llm_prompt_adapter
from models.prompts import PromptContext

# 获取适配器
adapter = get_llm_prompt_adapter()

# 执行Prompt调用
response = adapter.execute_prompt(
    template_id="l2_planner",
    context=PromptContext(
        request_id="test_001",
        accident_card={...},
        enable_rag=True
    )
)

# 查看结果
print(response.is_valid)
print(response.parsed_output)
print(response.model_used)
```

## 为微调准备

### Prompt模板管理
所有Prompt现在都通过PromptManager统一管理，包含：
- 系统提示词（system_prompt）
- 用户提示词模板（user_prompt_template）
- 输出格式说明（output_format）
- 必需字段验证（required_output_fields）
- 输出Schema（output_schema）
- 少样本示例（examples）

### 微调数据收集
使用`collect_fine_tuning_sample`方法自动收集微调样本：
```python
# 收集样本
sample = prompt_manager.collect_fine_tuning_sample(
    template_id="l1_data_modeling",
    context=context,
    expected_output=correct_output  # 专家标注的正确输出
)

# 标注样本
sample.is_correct = True
sample.annotator = "expert_001"
sample.annotation_status = "completed"

# 导出为JSONL格式（用于微调）
prompt_manager.export_fine_tuning_samples("data/fine_tuning.jsonl")
```

### 导出格式
微调数据导出为JSONL格式，每行一个样本：
```json
{
  "sample_id": "uuid",
  "template_id": "l1_data_modeling",
  "input_context": {...},
  "user_input": "暴雨导致石家庄站限速80km/h",
  "expected_output": {"scene_category": "临时限速", ...},
  "model_output": {...},
  "is_correct": true,
  "scenario_type": "临时限速",
  "difficulty": "medium",
  "annotation_status": "completed"
}
```

## 真实场景适配

### 新增领域知识

#### 1. 京广高铁网络信息
- 13个车站的完整信息
- 147列列车运行特点
- 关键区间（XSD-BDD、BDD-DZD等）
- 典型故障位置

#### 2. 调度约束
- 时间约束（到发时间、停站时间）
- 空间约束（安全间隔、追踪间隔）
- 容量约束（车站股道、站台占用）
- 列车运行时间（最小运行时间、图定运行时间）

#### 3. 延误处理策略
- 延误分类（轻微、中等、严重）
- 延误恢复策略（顺延、压缩、越行、避让）
- 延误传播控制
- 风险提示

#### 4. 车站作业时间标准
- 高速列车停站时间：2-3分钟
- 动车组停站时间：3-5分钟
- 最小停站时间：2分钟
- 不同作业类型的时间要求

### Prompt改进

所有Prompt模板都已优化，包含：
- 明确的角色定义
- 详细的输出格式说明
- 真实的场景示例
- 领域知识增强（通过RAG）
- 错误处理和回退机制

## 向后兼容性

- 保留`llm_workflow_engine.py`（旧版本）
- 新版引擎`llm_workflow_engine_v2.py`可以与旧版本共存
- 导出旧的`LLMCaller`和`get_llm_caller`函数
- Web层可以继续使用旧接口，无需修改

## 后续建议

### 短期（1-2周）
1. **测试新版工作流**：在实际数据上测试`llm_workflow_engine_v2`
2. **收集微调样本**：使用Prompt管理器收集高质量样本
3. **完善领域知识**：根据实际运行情况补充知识库

### 中期（1-2月）
1. **微调模型**：使用收集的样本微调Qwen模型
2. **评估微调效果**：对比微调前后模型的性能
3. **优化Prompt**：根据微调结果调整Prompt模板

### 长期（3-6月）
1. **向量检索RAG**：将RAG升级为基于向量的检索
2. **多模型支持**：支持切换不同的基础模型
3. **A/B测试**：进行A/B测试，验证架构改进效果
4. **性能优化**：优化响应时间和资源使用

## 总结

本次架构优化主要实现了：
1. ✅ 创建统一的Prompt管理系统（为微调准备）
2. ✅ 应用适配器模式，提高代码可维护性
3. ✅ 拆分大型文件，遵循单一职责原则
4. ✅ 增强真实高铁调度场景的领域知识
5. ✅ 删除冗余代码和调试信息

新的架构更加清晰、可维护，并且为微调提供了完善的支持。建议在实际应用中逐步迁移到新版架构，同时保持向后兼容。

---

**修改时间**: 2026年4月7日
**修改人**: AI Agent
**版本**: v2.0
