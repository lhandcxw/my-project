# -*- coding: utf-8 -*-
"""
铁路调度系统 - Qwen Agent核心模块
基于Qwen模型实现场景识别和功能调用的Agent
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import json
import time

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modelscope import AutoModelForCausalLM, AutoTokenizer
import torch

from solver.mip_scheduler import MIPScheduler
from railway_agent.dispatch_skills import DispatchSkillOutput
from railway_agent.tool_registry import (
    ToolRegistry, ToolCall, parse_tool_call, validate_tool_call
)
from railway_agent.prompts import (
    build_messages, format_scenario_info,
    RESULT_SUMMARY_PROMPT, SYSTEM_PROMPT
)


# ============================================
# Agent结果数据类
# ============================================

@dataclass
class AgentResult:
    """Agent执行结果"""
    success: bool
    recognized_scenario: str
    selected_skill: str
    reasoning: str
    dispatch_result: Optional[DispatchSkillOutput]
    model_response: str
    computation_time: float
    error_message: str = ""


# ============================================
# Qwen Agent类
# ============================================

class QwenAgent:
    """
    基于Qwen模型的铁路调度Agent
    
    功能：
    1. 场景识别：分析延误注入数据，识别场景类型
    2. 功能调用：基于MCP/Skills模式选择并调用对应的调度技能
    3. 结果返回：执行调度优化并返回结果
    """
    
    def __init__(
        self,
        model_path: str,
        scheduler: MIPScheduler,
        device: str = "auto"
    ):
        """
        初始化Qwen Agent
        
        Args:
            model_path: 模型路径
            scheduler: MIP调度器实例
            device: 设备类型 ("auto", "cuda", "cpu", "mps")
        """
        self.model_path = model_path
        self.scheduler = scheduler
        self.device = device
        
        # 加载模型
        print(f"正在加载模型: {model_path}")
        self.model = AutoModelForCausalLM.from_pretrained(
            model_path,
            torch_dtype="auto",
            device_map=device
        )
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        print("模型加载完成")
        
        # 初始化Tool注册表
        self.tool_registry = ToolRegistry(scheduler)
    
    def _build_chat_messages(
        self,
        delay_injection: Dict[str, Any],
        conversation_history: List[Dict] = None
    ) -> List[Dict[str, str]]:
        """
        构建对话消息
        
        Args:
            delay_injection: 延误注入数据
            conversation_history: 对话历史
            
        Returns:
            List[Dict]: 消息列表
        """
        return build_messages(delay_injection, conversation_history)
    
    def _call_model(
        self,
        messages: List[Dict[str, str]],
        max_new_tokens: int = 1024,
        temperature: float = 0.7
    ) -> str:
        """
        调用Qwen模型进行推理
        
        Args:
            messages: 消息列表
            max_new_tokens: 最大生成token数
            temperature: 温度参数
            
        Returns:
            str: 模型响应
        """
        # 应用chat template
        text = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True
        )
        
        # 编码输入
        model_inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)
        
        # 生成响应
        generated_ids = self.model.generate(
            **model_inputs,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=0.9,
            top_k=50,
            do_sample=True
        )
        
        # 解码输出（只取新生成的部分）
        generated_ids = [
            output_ids[len(input_ids):] 
            for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
        ]
        
        response = self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
        return response

    def chat_direct(self, messages: List[Dict[str, str]], max_new_tokens: int = 512) -> str:
        """
        直接对话，不使用Tool调用

        Args:
            messages: 对话消息列表
            max_new_tokens: 最大生成token数

        Returns:
            str: 模型响应
        """
        # 应用chat template
        text = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True
        )

        # 编码输入
        model_inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)

        # 生成响应
        generated_ids = self.model.generate(
            **model_inputs,
            max_new_tokens=max_new_tokens,
            temperature=0.7,
            top_p=0.9,
            do_sample=True
        )

        # 解码输出
        generated_ids = [
            output_ids[len(input_ids):]
            for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
        ]

        response = self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
        return response

    def analyze(
        self,
        delay_injection: Dict[str, Any],
        max_new_tokens: int = 1024
    ) -> AgentResult:
        """
        分析场景并执行调度
        
        Args:
            delay_injection: 延误注入数据
            max_new_tokens: 最大生成token数
            
        Returns:
            AgentResult: 执行结果
        """
        start_time = time.time()
        
        try:
            # Step 1: 构建Prompt并调用模型
            messages = self._build_chat_messages(delay_injection)
            model_response = self._call_model(messages, max_new_tokens)
            
            # Step 2: 解析工具调用
            tool_call = parse_tool_call(model_response)
            
            if tool_call is None:
                # 尝试基于场景类型直接选择工具（回退策略）
                tool_call = self._fallback_tool_selection(delay_injection)
                if tool_call is None:
                    return AgentResult(
                        success=False,
                        recognized_scenario=delay_injection.get("scenario_type", "unknown"),
                        selected_skill="",
                        reasoning="无法解析模型响应",
                        dispatch_result=None,
                        model_response=model_response,
                        computation_time=time.time() - start_time,
                        error_message="无法从模型响应中解析工具调用"
                    )
            
            # Step 3: 验证工具调用
            is_valid, error_msg = validate_tool_call(tool_call, self.tool_registry)
            if not is_valid:
                return AgentResult(
                    success=False,
                    recognized_scenario=delay_injection.get("scenario_type", "unknown"),
                    selected_skill=tool_call.tool_name,
                    reasoning=tool_call.reasoning,
                    dispatch_result=None,
                    model_response=model_response,
                    computation_time=time.time() - start_time,
                    error_message=f"工具调用验证失败: {error_msg}"
                )
            
            # Step 4: 执行工具（调用MIP求解器）
            # 确保使用完整的delay_injection数据（模型可能简化了参数）
            arguments = tool_call.arguments.copy()
            arguments["delay_injection"] = delay_injection  # 使用原始完整数据
            
            dispatch_result = self.tool_registry.execute(
                tool_call.tool_name,
                arguments
            )
            
            computation_time = time.time() - start_time
            
            return AgentResult(
                success=True,
                recognized_scenario=delay_injection.get("scenario_type", "unknown"),
                selected_skill=tool_call.tool_name,
                reasoning=tool_call.reasoning,
                dispatch_result=dispatch_result,
                model_response=model_response,
                computation_time=computation_time
            )
            
        except Exception as e:
            return AgentResult(
                success=False,
                recognized_scenario=delay_injection.get("scenario_type", "unknown"),
                selected_skill="",
                reasoning="",
                dispatch_result=None,
                model_response="",
                computation_time=time.time() - start_time,
                error_message=f"执行错误: {str(e)}"
            )
    
    def _fallback_tool_selection(self, delay_injection: Dict[str, Any]) -> Optional[ToolCall]:
        """
        回退策略：基于场景类型直接选择工具
        
        Args:
            delay_injection: 延误注入数据
            
        Returns:
            Optional[ToolCall]: 工具调用
        """
        scenario_type = delay_injection.get("scenario_type", "")
        affected_trains = delay_injection.get("affected_trains", [])
        
        # 获取所有车站编码（简化处理）
        station_codes = ["BJP", "TJG", "JNZ", "NJH", "SHH"]
        
        # 根据场景类型映射到工具
        scenario_to_skill = {
            "temporary_speed_limit": "temporary_speed_limit_skill",
            "sudden_failure": "sudden_failure_skill",
            "section_interrupt": "section_interrupt_skill"
        }
        
        tool_name = scenario_to_skill.get(scenario_type)
        if tool_name and self.tool_registry.has_tool(tool_name):
            return ToolCall(
                tool_name=tool_name,
                arguments={
                    "train_ids": affected_trains,
                    "station_codes": station_codes,
                    "delay_injection": delay_injection,
                    "optimization_objective": "min_max_delay"
                },
                reasoning=f"基于场景类型 '{scenario_type}' 直接选择工具"
            )
        
        return None
    
    def summarize_result(self, result: AgentResult) -> str:
        """
        生成结果总结
        
        Args:
            result: Agent执行结果
            
        Returns:
            str: 总结文本
        """
        if not result.success:
            return f"调度执行失败: {result.error_message}"
        
        dispatch = result.dispatch_result
        
        summary = f"""
========================================
        铁路调度 Agent 分析报告
========================================

场景识别: {result.recognized_scenario}
选择工具: {result.selected_skill}
推理过程: {result.reasoning}

调度结果:
  - 执行状态: {'成功' if dispatch.success else '失败'}
  - 消息: {dispatch.message}
  - 计算时间: {dispatch.computation_time:.2f}秒

延误统计:
  - 最大延误: {dispatch.delay_statistics.get('max_delay_seconds', 0)}秒
  - 平均延误: {dispatch.delay_statistics.get('avg_delay_seconds', 0):.2f}秒
  - 总延误: {dispatch.delay_statistics.get('total_delay_seconds', 0)}秒

Agent总耗时: {result.computation_time:.2f}秒
========================================
"""
        return summary


# ============================================
# 模型配置
# ============================================

# 默认模型配置
# 使用 ModelScope Hub 的模型 ID 或本地模型路径
# 示例：
#   - "Qwen/Qwen2.5-0.5B" (从 ModelScope 下载)
#   - "Qwen/Qwen2.5-1.8B" (从 ModelScope 下载)
#   - "models/qwen3.5-4b/base" (本地路径)
DEFAULT_MODEL_PATH = ""  # 留空则不使用大模型（使用规则引擎）


def create_qwen_agent(
    model_path: str = None,
    trains = None,
    stations = None
) -> QwenAgent:
    """
    创建Qwen Agent实例

    Args:
        model_path: 模型路径或ModelScope模型ID
                   例如: "Qwen/Qwen2.5-0.5B" 或 "models/qwen3.5-4b/base"
                   如果为None，则使用 DEFAULT_MODEL_PATH
        trains: 列车列表（可选，默认使用示例数据）
        stations: 车站列表（可选，默认使用示例数据）

    Returns:
        QwenAgent: Agent实例
    """
    # 使用默认配置
    if model_path is None:
        model_path = DEFAULT_MODEL_PATH

    # 如果模型路径为空，返回None（不使用大模型）
    if not model_path:
        print("未配置模型路径，Agent将使用规则引擎模式")
        return None

    if trains is None or stations is None:
        from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
        use_real_data(True)
        trains = get_trains_pydantic()[:20]
        stations = get_stations_pydantic()

    scheduler = MIPScheduler(trains, stations)
    return QwenAgent(model_path, scheduler)


# ============================================
# 测试代码
# ============================================

if __name__ == "__main__":
    from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data

    print("=" * 60)
    print("Qwen Agent 测试")
    print("=" * 60)

    # 使用真实数据
    use_real_data(True)
    trains = get_trains_pydantic()[:20]
    stations = get_stations_pydantic()

    # 创建Agent（注意：如果没有配置模型路径，会返回None）
    agent = create_qwen_agent(trains=trains, stations=stations)
    if agent is None:
        print("警告：Agent创建失败，可能是未配置模型路径")

    # 测试场景1：临时限速
    print("\n" + "=" * 60)
    print("测试场景1: 临时限速")
    print("=" * 60)

    if trains:
        first_train = trains[0].train_id
        first_station = trains[0].schedule.stops[0].station_code
    else:
        first_train = "G1215"
        first_station = "XSD"

    delay_injection_1 = {
        "scenario_type": "temporary_speed_limit",
        "scenario_id": "TEST_001",
        "injected_delays": [
            {
                "train_id": first_train,
                "location": {"location_type": "station", "station_code": first_station},
                "initial_delay_seconds": 600,
                "timestamp": "2024-01-15T10:00:00Z"
            }
        ],
        "affected_trains": [first_train],
        "scenario_params": {
            "limit_speed_kmh": 200,
            "duration_minutes": 120,
            "affected_section": f"{first_station} -> BDD"
        }
    }

    if agent:
        result1 = agent.analyze(delay_injection_1)
        print(agent.summarize_result(result1))

    # 测试场景2：突发故障
    print("\n" + "=" * 60)
    print("测试场景2: 突发故障")
    print("=" * 60)

    if len(trains) > 1:
        second_train = trains[1].train_id
        second_station = trains[1].schedule.stops[0].station_code
    else:
        second_train = "G1239"
        second_station = "XSD"

    delay_injection_2 = {
        "scenario_type": "sudden_failure",
        "scenario_id": "TEST_002",
        "injected_delays": [
            {
                "train_id": second_train,
                "location": {"location_type": "station", "station_code": second_station},
                "initial_delay_seconds": 1800,
                "timestamp": "2024-01-15T11:00:00Z"
            }
        ],
        "affected_trains": [second_train],
        "scenario_params": {
            "failure_type": "vehicle_breakdown",
            "estimated_repair_time": 60
        }
    }

    if agent:
        result2 = agent.analyze(delay_injection_2)
        print(agent.summarize_result(result2))
