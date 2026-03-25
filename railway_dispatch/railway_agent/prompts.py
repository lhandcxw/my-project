# -*- coding: utf-8 -*-
"""
铁路调度系统 - Qwen Agent Prompt模板模块
定义场景识别和功能调用的Prompt模板
"""

from typing import Dict, Any, List
import json


# ============================================
# System Prompt 模板
# ============================================

SYSTEM_PROMPT = """你是一个专业的铁路调度规划助手，由阿里云Qwen模型驱动。

你的职责是：
1. 分析延误场景信息，识别场景类型
2. 选择合适的调度技能（Tool）执行优化
3. 返回调度决策结果

## 场景类型说明

| 场景类型 | 标识 | 说明 |
|---------|------|------|
| 临时限速 | temporary_speed_limit | 铁路线路临时限速导致的多列列车延误 |
| 突发故障 | sudden_failure | 列车设备故障、区间占用等单列车故障 |
| 区间中断 | section_interrupt | 线路中断导致区间无法通行（预留） |

## 决策原则

1. 临时限速场景：选择 temporary_speed_limit_skill
2. 突发故障场景：选择 sudden_failure_skill
3. 区间中断场景：选择 section_interrupt_skill（暂不支持）

请根据场景信息，选择最合适的工具执行调度优化。"""


# ============================================
# 场景分析 Prompt
# ============================================

SCENARIO_ANALYSIS_PROMPT = """## 当前场景信息

{scenario_info}

## 可用工具（Skills）

{tools_description}

## 任务

请分析以上场景信息，输出你的详细思考过程，然后选择最合适的工具执行调度优化。

请按以下格式输出JSON：

```json
{{
    "thinking": "详细的思考过程，包括：1.场景分析 2.延误情况 3.选择该工具的理由",
    "tool_name": "技能名称",
    "arguments": {{
        "train_ids": ["列车ID列表"],
        "station_codes": ["车站编码列表"],
        "delay_injection": {{...}},
        "optimization_objective": "min_max_delay"
    }}
}}
```"""


# ============================================
# 结果总结 Prompt
# ============================================

RESULT_SUMMARY_PROMPT = """## 调度执行结果

{tool_result}

## 任务

请根据以上调度结果，生成简洁的调度方案总结，包括：
1. 场景识别结果
2. 调度方案概述
3. 延误统计信息

请用中文输出总结。"""


# ============================================
# Tools 描述模板
# ============================================

def get_tools_description() -> str:
    """生成工具描述文本"""
    return """
### 1. temporary_speed_limit_skill

**描述**: 处理临时限速场景的列车调度

**适用场景**: 铁路线路临时限速导致的多列列车延误调整

**参数**:
- train_ids: 受影响列车ID列表 (必需)
- station_codes: 涉及的车站编码列表 (必需)
- delay_injection: 延误注入数据字典 (必需)
- optimization_objective: 优化目标，可选 "min_max_delay" 或 "min_avg_delay" (默认: min_max_delay)

### 2. sudden_failure_skill

**描述**: 处理突发故障场景的列车调度

**适用场景**: 列车设备故障、区间占用等单列车故障场景

**参数**:
- train_ids: 受影响列车ID列表 (必需)
- station_codes: 涉及的车站编码列表 (必需)
- delay_injection: 延误注入数据字典 (必需)
- optimization_objective: 优化目标 (默认: min_max_delay)

### 3. section_interrupt_skill

**描述**: 处理区间中断场景的列车调度（预留接口）

**适用场景**: 线路中断、严重自然灾害等导致区间无法通行

**参数**:
- train_ids: 受影响列车ID列表 (必需)
- station_codes: 涉及的车站编码列表 (必需)
- delay_injection: 延误注入数据字典 (必需)
- optimization_objective: 优化目标 (默认: min_max_delay)

**注意**: 当前版本暂不支持该场景
"""


def format_scenario_info(delay_injection: Dict[str, Any]) -> str:
    """
    格式化场景信息为Prompt文本
    
    Args:
        delay_injection: 延误注入数据字典
        
    Returns:
        str: 格式化后的场景信息
    """
    scenario_type = delay_injection.get("scenario_type", "unknown")
    scenario_id = delay_injection.get("scenario_id", "unknown")
    affected_trains = delay_injection.get("affected_trains", [])
    scenario_params = delay_injection.get("scenario_params", {})
    injected_delays = delay_injection.get("injected_delays", [])
    
    # 格式化注入的延误信息
    delays_text = ""
    for d in injected_delays:
        train_id = d.get("train_id", "unknown")
        location = d.get("location", {})
        station_code = location.get("station_code", "unknown")
        delay_seconds = d.get("initial_delay_seconds", 0)
        delays_text += f"  - 列车 {train_id}: 延误 {delay_seconds} 秒, 位置: {station_code}\n"
    
    # 格式化场景参数
    params_text = ""
    if scenario_params:
        for key, value in scenario_params.items():
            params_text += f"  - {key}: {value}\n"
    
    return f"""场景类型: {scenario_type}
场景ID: {scenario_id}
受影响列车: {', '.join(affected_trains) if affected_trains else '无'}

注入的延误:
{delays_text if delays_text else '  无'}

场景参数:
{params_text if params_text else '  无'}"""


def build_analysis_prompt(delay_injection: Dict[str, Any]) -> str:
    """
    构建完整的场景分析Prompt
    
    Args:
        delay_injection: 延误注入数据字典
        
    Returns:
        str: 完整的Prompt
    """
    scenario_info = format_scenario_info(delay_injection)
    tools_desc = get_tools_description()
    
    return SCENARIO_ANALYSIS_PROMPT.format(
        scenario_info=scenario_info,
        tools_description=tools_desc
    )


def build_messages(
    delay_injection: Dict[str, Any],
    conversation_history: List[Dict] = None
) -> List[Dict[str, str]]:
    """
    构建对话消息列表
    
    Args:
        delay_injection: 延误注入数据字典
        conversation_history: 对话历史（可选）
        
    Returns:
        List[Dict]: 消息列表
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]
    
    if conversation_history:
        messages.extend(conversation_history)
    
    user_prompt = build_analysis_prompt(delay_injection)
    messages.append({"role": "user", "content": user_prompt})
    
    return messages


# ============================================
# 测试代码
# ============================================

if __name__ == "__main__":
    # 测试Prompt生成
    test_delay_injection = {
        "scenario_type": "temporary_speed_limit",
        "scenario_id": "TEST_001",
        "injected_delays": [
            {
                "train_id": "G1001",
                "location": {"station_code": "TJG"},
                "initial_delay_seconds": 600,
                "timestamp": "2024-01-15T10:00:00Z"
            }
        ],
        "affected_trains": ["G1001", "G1003"],
        "scenario_params": {
            "limit_speed_kmh": 200,
            "duration_minutes": 120,
            "affected_section": "TJG -> JNZ"
        }
    }
    
    print("=" * 60)
    print("场景分析Prompt:")
    print("=" * 60)
    print(build_analysis_prompt(test_delay_injection))
    
    print("\n" + "=" * 60)
    print("消息列表:")
    print("=" * 60)
    messages = build_messages(test_delay_injection)
    for msg in messages:
        print(f"\n[{msg['role']}]:")
        print(msg['content'][:500] + "..." if len(msg['content']) > 500 else msg['content'])
