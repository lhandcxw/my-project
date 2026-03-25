# -*- coding: utf-8 -*-
"""
基于Ollama的铁路调度Agent
使用Ollama API调用本地模型
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import json
import time
import requests

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
# Ollama配置
# ============================================

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:0.5b"


class OllamaClient:
    """Ollama API客户端"""

    def __init__(self, base_url: str = DEFAULT_OLLAMA_BASE_URL, model: str = DEFAULT_MODEL):
        self.base_url = base_url
        self.model = model

    def chat(self, messages: List[Dict], temperature: float = 0.7, max_tokens: int = 1024) -> str:
        """
        调用Ollama chat接口

        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数

        Returns:
            模型响应文本
        """
        url = f"{self.base_url}/api/chat"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False
        }

        try:
            response = requests.post(url, json=payload, timeout=120)
            response.raise_for_status()
            result = response.json()
            return result.get("message", {}).get("content", "")
        except requests.exceptions.ConnectionError:
            raise ConnectionError(f"Cannot connect to Ollama at {self.base_url}. Please ensure Ollama is running.")
        except Exception as e:
            raise RuntimeError(f"Ollama API error: {str(e)}")

    def check_health(self) -> bool:
        """检查Ollama服务是否可用"""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except:
            return False


# ============================================
# Ollama Agent类
# ============================================

class OllamaAgent:
    """
    基于Ollama的铁路调度Agent

    功能：
    1. 场景识别：分析延误注入数据，识别场景类型
    2. 功能调用：基于MCP/Skills模式选择并调用对应的调度技能
    3. 结果返回：执行调度优化并返回结果
    """

    def __init__(
        self,
        scheduler: MIPScheduler,
        ollama_url: str = DEFAULT_OLLAMA_BASE_URL,
        model: str = DEFAULT_MODEL
    ):
        """
        初始化Ollama Agent

        Args:
            scheduler: MIP调度器实例
            ollama_url: Ollama服务地址
            model: 模型名称
        """
        self.scheduler = scheduler
        self.client = OllamaClient(ollama_url, model)

        # 检查服务是否可用
        if not self.client.check_health():
            print(f"WARNING: Ollama service not available at {ollama_url}")
            print("Please start Ollama: ollama serve")
        else:
            print(f"Connected to Ollama: {model}")

        # 初始化Tool注册表
        self.tool_registry = ToolRegistry(scheduler)

    def _build_prompt(self, delay_injection: Dict[str, Any]) -> str:
        """
        构建Prompt

        Args:
            delay_injection: 延误注入数据

        Returns:
            构建好的prompt字符串
        """
        # 获取场景信息
        scenario_info = format_scenario_info(delay_injection)

        # 获取可用工具描述
        tools_description = self.tool_registry.get_tools_description()

        # 构建完整的prompt
        prompt = f"""你是铁路调度智能助手，负责分析列车延误场景并生成调度方案。

{scenario_info}

可用工具:
{tools_description}

请分析以上场景，选择合适的工具来解决问题。
你必须按照以下JSON格式返回结果:
{{
    "thought": "你的思考过程",
    "tool_call": {{
        "tool_name": "工具名称",
        "arguments": {{}}
    }}
}}
"""
        return prompt

    def _call_model(self, prompt: str) -> str:
        """
        调用Ollama模型

        Args:
            prompt: 提示词

        Returns:
            模型响应
        """
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        return self.client.chat(messages)

    def _execute_tool(self, tool_call: ToolCall) -> Dict[str, Any]:
        """
        执行工具调用

        Args:
            tool_call: 工具调用对象

        Returns:
            执行结果
        """
        return self.tool_registry.execute_tool(tool_call)

    def run(self, task_description: str) -> AgentResult:
        """
        执行任务

        Args:
            task_description: 任务描述

        Returns:
            AgentResult: 执行结果
        """
        start_time = time.time()

        try:
            # 1. 解析任务
            delay_injection = self._parse_task(task_description)

            # 2. 构建prompt
            prompt = self._build_prompt(delay_injection)

            # 3. 调用模型
            model_response = self._call_model(prompt)

            # 4. 解析工具调用
            tool_call = parse_tool_call(model_response)

            if tool_call is None:
                return AgentResult(
                    success=False,
                    recognized_scenario="unknown",
                    selected_skill="none",
                    reasoning="无法解析模型响应",
                    dispatch_result=None,
                    model_response=model_response,
                    computation_time=time.time() - start_time,
                    error_message="Failed to parse tool call"
                )

            # 5. 验证工具调用
            if not validate_tool_call(tool_call, self.tool_registry.get_tool_names()):
                return AgentResult(
                    success=False,
                    recognized_scenario="unknown",
                    selected_skill=tool_call.tool_name if tool_call else "none",
                    reasoning="无效的工具调用",
                    dispatch_result=None,
                    model_response=model_response,
                    computation_time=time.time() - start_time,
                    error_message="Invalid tool call"
                )

            # 6. 执行工具
            execution_result = self._execute_tool(tool_call)

            # 7. 返回结果
            return AgentResult(
                success=True,
                recognized_scenario=delay_injection.get("scenario_type", "unknown"),
                selected_skill=tool_call.tool_name,
                reasoning=model_response,
                dispatch_result=execution_result.get("dispatch_result"),
                model_response=model_response,
                computation_time=time.time() - start_time
            )

        except Exception as e:
            return AgentResult(
                success=False,
                recognized_scenario="error",
                selected_skill="none",
                reasoning=str(e),
                dispatch_result=None,
                model_response="",
                computation_time=time.time() - start_time,
                error_message=str(e)
            )

    def _parse_task(self, task_description: str) -> Dict[str, Any]:
        """
        解析任务描述为延误注入数据

        Args:
            task_description: 任务描述

        Returns:
            延误注入数据字典
        """
        # 简单的规则解析
        # 在实际应用中可以让LLM来解析
        delay_injection = {
            "scenario_type": "sudden_failure",
            "affected_trains": [],
            "injected_delays": []
        }

        # 尝试解析列车号
        import re
        train_match = re.search(r'G\d+', task_description)
        if train_match:
            delay_injection["affected_trains"].append(train_match.group())

        # 尝试解析延误时间
        delay_match = re.search(r'(\d+)\s*分钟', task_description)
        if delay_match:
            delay_minutes = int(delay_match.group(1))
            delay_injection["injected_delays"].append({
                "train_id": delay_injection["affected_trains"][0] if delay_injection["affected_trains"] else "G1001",
                "delay_seconds": delay_minutes * 60
            })

        # 尝试解析场景类型
        if "故障" in task_description:
            delay_injection["scenario_type"] = "sudden_failure"
        elif "限速" in task_description:
            delay_injection["scenario_type"] = "temporary_speed_limit"

        return delay_injection


# ============================================
# 工厂函数
# ============================================

def create_ollama_agent(
    ollama_url: str = None,
    model: str = None
) -> OllamaAgent:
    """
    创建Ollama Agent

    Args:
        ollama_url: Ollama服务地址
        model: 模型名称

    Returns:
        OllamaAgent实例
    """
    from railway_dispatch.solver.mip_scheduler import create_scheduler
    from railway_agent.dispatch_skills import create_skills

    # 创建调度器
    scheduler = create_scheduler()

    # 使用默认值
    if ollama_url is None:
        ollama_url = DEFAULT_OLLAMA_BASE_URL
    if model is None:
        model = DEFAULT_MODEL

    return OllamaAgent(scheduler, ollama_url, model)


# ============================================
# 测试
# ============================================

if __name__ == "__main__":
    print("Testing Ollama Agent...")
    agent = create_ollama_agent()

    if agent.client.check_health():
        print("Ollama is running!")
        result = agent.run("G1001在天津西延误了40分钟")
        print(f"Success: {result.success}")
        print(f"Selected skill: {result.selected_skill}")
        print(f"Computation time: {result.computation_time:.2f}s")
    else:
        print("Ollama is not running. Please start it with: ollama serve")
