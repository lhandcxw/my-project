# LLM-TTRA v4.0 快速开始指南

本指南帮助您快速上手LLM-TTRA v4.0的新特性：Prompt管理系统、工作流分层模块、微调支持。

## 目录

1. [安装和配置](#安装和配置)
2. [使用新版工作流引擎](#使用新版工作流引擎)
3. [使用Prompt管理器](#使用prompt管理器)
4. [收集微调数据](#收集微调数据)
5. [常见问题](#常见问题)

---

## 安装和配置

### 1. 安装依赖

```bash
cd railway_dispatch
pip install -r requirements.txt
```

### 2. 配置大模型（可选）

系统支持两种大模型：

**选项A：使用ModelScope远程模型（推荐）**
```bash
export MODELSCOPE_API_TOKEN='your_api_token'
```

**选项B：使用Ollama本地模型**
```bash
# 安装Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 下载Qwen模型
ollama pull qwen2.5:0.5b
```

### 3. 启动Web服务

```bash
python web/app.py
```

访问 http://localhost:8081

---

## 使用新版工作流引擎

v4.0推荐使用新的工作流引擎 `llm_workflow_engine_v2.py`，它应用了分层模块和适配器模式。

### 基础使用

```python
from railway_agent.llm_workflow_engine_v2 import create_workflow_engine

# 创建工作流引擎
engine = create_workflow_engine()

# 执行完整工作流
result = engine.execute_full_workflow(
    user_input="暴雨导致石家庄站限速80km/h",
    enable_rag=True  # 启用RAG增强
)

# 查看结果
print(f"成功: {result.success}")
print(f"消息: {result.message}")

# 查看详细调试信息
import json
print(json.dumps(result.debug_trace, indent=2, ensure_ascii=False))
```

### 分层执行

您也可以单独执行某一层：

```python
# 只执行L1层
l1_result = engine.execute_layer1(
    user_input="暴雨导致石家庄站限速80km/h",
    enable_rag=True
)

# 只执行L2层
l2_result = engine.execute_layer2(
    accident_card=l1_result["accident_card"],
    network_snapshot=l1_result["network_snapshot"],
    dispatch_metadata=l1_result["dispatch_context_metadata"]
)

# 只执行L3层
l3_result = engine.execute_layer3(
    planning_intent=l2_result["planning_intent"],
    accident_card=l1_result["accident_card"],
    network_snapshot=l1_result["network_snapshot"]
)

# 只执行L4层
l4_result = engine.execute_layer4(
    skill_execution_result=l3_result["skill_execution_result"],
    solver_response=l3_result.get("solver_response")
)
```

### 完整示例

```python
from railway_agent.llm_workflow_engine_v2 import create_workflow_engine
import json

def main():
    # 创建引擎
    engine = create_workflow_engine()

    # 测试场景
    test_cases = [
        "暴雨导致石家庄站限速80km/h",
        "保定东站设备故障，G1563次列车延误15分钟",
        "XSD-BDD区间封锁，需要绕行"
    ]

    for user_input in test_cases:
        print(f"\n{'='*60}")
        print(f"输入: {user_input}")
        print(f"{'='*60}")

        # 执行工作流
        result = engine.execute_full_workflow(
            user_input=user_input,
            enable_rag=True
        )

        # 输出结果
        print(f"成功: {result.success}")
        print(f"消息: {result.message}")

        if result.success:
            # 输出调试信息
            trace = result.debug_trace
            print(f"\n场景类别: {trace.get('accident_card', {}).get('scene_category')}")
            print(f"规划意图: {trace.get('planning_intent')}")
            print(f"选择求解器: {trace.get('solver_result', {}).get('skill_name')}")

            if 'evaluation_report' in trace and trace['evaluation_report']:
                eval_report = trace['evaluation_report']
                print(f"\n评估结果:")
                print(f"  - 可行性: {eval_report.get('is_feasible')}")
                print(f"  - 总延误: {eval_report.get('total_delay_minutes')} 分钟")
                print(f"  - 最大延误: {eval_report.get('max_delay_minutes')} 分钟")

if __name__ == "__main__":
    main()
```

---

## 使用Prompt管理器

v4.0新增的Prompt管理系统可以统一管理所有Prompt模板。

### 列出所有模板

```python
from railway_agent.prompts import get_prompt_manager

# 获取Prompt管理器
prompt_manager = get_prompt_manager()

# 列出所有模板
templates = prompt_manager.list_templates()

print("可用的Prompt模板:")
for t in templates:
    print(f"  - {t.template_id}: {t.template_name}")
    print(f"    类型: {t.template_type}")
    print(f"    描述: {t.description}")
    print(f"    温度: {t.temperature}, 最大token: {t.max_tokens}")
    print(f"    必需字段: {t.required_output_fields}")
    print()
```

### 填充Prompt

```python
from models.prompts import PromptContext

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
    enable_rag=True  # 启用RAG增强
)

print("填充后的Prompt:")
print(filled_prompt)
```

### 验证输出

```python
# 模拟LLM输出
output = {
    "accident_card": {
        "scene_category": "临时限速",
        "fault_type": "暴雨",
        "location_code": "SJP",
        "is_complete": True
    }
}

# 验证输出
is_valid, errors = prompt_manager.validate_output(
    template_id="l1_data_modeling",
    output=output
)

if is_valid:
    print("输出验证通过！")
else:
    print("输出验证失败:")
    for error in errors:
        print(f"  - {error}")
```

### 查看模板详情

```python
# 获取特定模板
template = prompt_manager.get_template("l1_data_modeling")

if template:
    print(f"模板ID: {template.template_id}")
    print(f"模板名称: {template.template_name}")
    print(f"系统提示词: {template.system_prompt}")
    print(f"用户提示词模板: {template.user_prompt_template}")
    print(f"输出格式: {template.output_format}")
    print(f"示例数量: {len(template.examples)}")

    # 显示示例
    if template.examples:
        print("\n示例:")
        for i, example in enumerate(template.examples, 1):
            print(f"  示例 {i}:")
            print(f"    输入: {example.get('input', 'N/A')}")
            print(f"    输出: {example.get('output', 'N/A')}")
```

---

## 收集微调数据

v4.0支持自动收集微调数据，为模型微调做准备。

### 收集单个样本

```python
from railway_agent.prompts import get_prompt_manager
from models.prompts import PromptContext

prompt_manager = get_prompt_manager()

# 构建上下文
context = PromptContext(
    request_id="sample_001",
    user_input="暴雨导致石家庄站限速80km/h",
    scene_type="临时限速"
)

# 收集样本（专家标注正确答案）
sample = prompt_manager.collect_fine_tuning_sample(
    template_id="l1_data_modeling",
    context=context,
    expected_output={
        "accident_card": {
            "scene_category": "临时限速",
            "fault_type": "暴雨",
            "location_code": "SJP",
            "location_name": "石家庄",
            "affected_train_ids": ["G1563"],
            "is_complete": True
        }
    }
)

# 标注样本
sample.is_correct = True
sample.annotator = "expert_001"
sample.annotation_status = "completed"
sample.difficulty = "medium"
sample.tags = ["临时限速", "暴雨", "石家庄"]
sample.notes = "典型场景，数据完整"

print(f"样本ID: {sample.sample_id}")
print(f"标注状态: {sample.annotation_status}")
print(f"标注人: {sample.annotator}")
```

### 批量收集样本

```python
from datetime import datetime

# 准备测试用例
test_cases = [
    {
        "input": "暴雨导致石家庄站限速80km/h",
        "output": {
            "scene_category": "临时限速",
            "fault_type": "暴雨",
            "location_code": "SJP",
            "affected_train_ids": ["G1563"],
            "is_complete": True
        },
        "tags": ["临时限速", "暴雨", "石家庄"]
    },
    {
        "input": "保定东站设备故障，G1563次列车延误15分钟",
        "output": {
            "scene_category": "突发故障",
            "fault_type": "设备故障",
            "location_code": "BDD",
            "affected_train_ids": ["G1563"],
            "is_complete": True
        },
        "tags": ["突发故障", "设备故障", "保定东"]
    }
]

# 批量收集
for i, test_case in enumerate(test_cases, 1):
    context = PromptContext(
        request_id=f"sample_{i:03d}",
        user_input=test_case["input"]
    )

    sample = prompt_manager.collect_fine_tuning_sample(
        template_id="l1_data_modeling",
        context=context,
        expected_output={"accident_card": test_case["output"]}
    )

    # 标注样本
    sample.is_correct = True
    sample.annotator = "expert_001"
    sample.annotation_status = "completed"
    sample.tags = test_case["tags"]
    sample.difficulty = "medium"

    print(f"收集样本 {i}: {sample.sample_id}")
```

### 导出微调数据

```python
# 导出所有已完成的样本为JSONL格式
output_file = "data/fine_tuning/train.jsonl"
prompt_manager.export_fine_tuning_samples(output_file)

print(f"已导出微调数据到: {output_file}")
```

### 导出格式

导出的JSONL格式如下（每行一个样本）：

```json
{
  "sample_id": "uuid",
  "template_id": "l1_data_modeling",
  "input_context": {
    "request_id": "sample_001",
    "user_input": "暴雨导致石家庄站限速80km/h",
    "scene_type": "临时限速"
  },
  "user_input": "暴雨导致石家庄站限速80km/h",
  "expected_output": {
    "accident_card": {
      "scene_category": "临时限速",
      "fault_type": "暴雨",
      "location_code": "SJP"
    }
  },
  "model_output": null,
  "is_correct": true,
  "scenario_type": "临时限速",
  "difficulty": "medium",
  "annotation_status": "completed",
  "annotator": "expert_001",
  "tags": ["临时限速", "暴雨", "石家庄"],
  "notes": "典型场景，数据完整",
  "created_at": "2026-04-07T10:00:00"
}
```

### 微调流程

收集足够数据后，可以使用以下流程微调Qwen模型：

```bash
# 1. 导出数据
python -c "
from railway_agent.prompts import get_prompt_manager
prompt_manager = get_prompt_manager()
prompt_manager.export_fine_tuning_samples('data/fine_tuning/train.jsonl')
"

# 2. 数据预处理（划分训练集、验证集）
python scripts/prepare_finetuning_data.py

# 3. 微调模型（使用你选择的微调框架）
python scripts/finetune_qwen.py \
    --model_name Qwen/Qwen2.5-1.8B \
    --train_data data/fine_tuning/train.jsonl \
    --valid_data data/fine_tuning/valid.jsonl \
    --output_dir models/qwen_dispatch_finetuned \
    --num_epochs 3 \
    --batch_size 4 \
    --learning_rate 5e-5

# 4. 评估微调效果
python scripts/evaluate_model.py \
    --model_path models/qwen_dispatch_finetuned \
    --test_data data/fine_tuning/test.jsonl

# 5. 替换模型（在工作流引擎中）
# 修改 railway_agent/llm_workflow_engine_v2.py 中的模型路径
```

---

## 常见问题

### Q1: v3.2和v4.0有什么区别？

**A**: v4.0主要新增以下特性：
- Prompt管理系统：统一管理所有Prompt模板
- 工作流分层模块：将工作流引擎拆分为独立模块
- LLM Prompt适配器：统一LLM Prompt调用
- 微调支持：数据收集、标注、导出
- 增强的RAG系统：添加真实高铁调度知识

v3.2的代码仍然保留，可以继续使用。

### Q2: 如何从v3.2迁移到v4.0？

**A**: v4.0保持向后兼容：
1. 保留旧的`llm_workflow_engine.py`
2. 新的`llm_workflow_engine_v2.py`可以独立使用
3. Web层无需修改
4. 建议逐步迁移，先测试新功能

### Q3: 如何选择使用哪个工作流引擎？

**A**:
- **开发新功能**：推荐使用`llm_workflow_engine_v2.py`
- **维护旧功能**：继续使用`llm_workflow_engine.py`
- **收集微调数据**：必须使用`llm_workflow_engine_v2.py`

### Q4: 微调需要多少数据？

**A**: 建议：
- 最少：100个高质量样本
- 推荐：500-1000个样本
- 样本应覆盖所有场景类型（临时限速、突发故障、区间封锁）
- 样本应包含不同难度级别（easy、medium、hard）

### Q5: RAG知识如何更新？

**A**:
1. 编辑`railway_agent/rag_retriever.py`中的`_load_enhanced_knowledge()`方法
2. 在`self.knowledge_base`字典中添加新的知识
3. 重启服务即可生效

### Q6: 如何添加新的Prompt模板？

**A**:
```python
from railway_agent.prompts import get_prompt_manager
from models.prompts import PromptTemplate, PromptTemplateType

prompt_manager = get_prompt_manager()

new_template = PromptTemplate(
    template_id="my_custom_template",
    template_type=PromptTemplateType.L2_PLANNER,
    template_name="我的自定义模板",
    description="用于特定场景的Prompt模板",
    system_prompt="你是一个专业的...",
    user_prompt_template="用户输入：{user_input}\n输出：{output}",
    required_output_fields=["field1", "field2"],
    temperature=0.7,
    max_tokens=512,
    examples=[...]
)

prompt_manager.register_template(new_template)
```

### Q7: 如何调试工作流？

**A**: 启用详细日志：
```python
import logging
logging.basicConfig(level=logging.DEBUG)

# 执行工作流
result = engine.execute_full_workflow(
    user_input="...",
    enable_rag=True
)

# 查看调试信息
print(json.dumps(result.debug_trace, indent=2, ensure_ascii=False))
```

### Q8: 微调后如何替换模型？

**A**: 修改`railway_agent/llm_workflow_engine.py`中的模型路径：
```python
# 原始模型
MODELSCOPE_MODEL_ID = "Qwen/Qwen2.5-1.8B"

# 微调后的模型
MODELSCOPE_MODEL_ID = "models/qwen_dispatch_finetuned"
# 或者
MODELSCOPE_MODEL_ID = "your_huggingface_username/qwen_dispatch_finetuned"
```

---

## 下一步

- 阅读详细架构文档：[railway_dispatch_agent_architecture.md](../../railway_dispatch_agent_architecture.md)
- 查看重构总结：[REFACTORING_SUMMARY.md](../../REFACTORING_SUMMARY.md)
- 探索求解器文档：[solver/README.md](solver/README.md)

---

## 获取帮助

如有问题，请：
1. 查看常见问题部分
2. 阅读详细文档
3. 查看代码注释
4. 提交Issue
