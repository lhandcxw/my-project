# -*- coding: utf-8 -*-
"""
铁路调度系统 - Rule Agent 模块
基于固定规则的调度Agent，无需大模型

用途：
1. 作为开发阶段的桩模块，跑通整个流程
2. 为微调数据集提供标准输出格式
3. 后续无缝替换为微调后的QwenAgent
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import json
import time
import re
import logging

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solver.mip_scheduler import MIPScheduler
from railway_agent.dispatch_skills import DispatchSkillOutput
from railway_agent.tool_registry import ToolRegistry, ToolCall

# 配置日志
logger = logging.getLogger(__name__)


# ============================================
# Agent结果数据类（与QwenAgent保持一致）
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
# 场景关键词配置
# ============================================

SCENARIO_KEYWORDS = {
    "temporary_speed_limit": {
        "keywords": ["限速", "大风", "暴雨", "降雪", "冰雪", "雨量", "风速", 
                     "天气", "自然灾害", "泥石流", "塌方", "水害", "台风"],
        "description": "临时限速场景 - 因天气或自然灾害导致的线路限速",
        "operation_flow": [
            "1. 确认限速区段和限速值",
            "2. 计算受影响列车的延误时间",
            "3. 调整后续列车时刻表",
            "4. 通知相关车站和乘务组",
            "5. 发布调度命令"
        ]
    },
    "sudden_failure": {
        "keywords": ["故障", "中断", "封锁", "设备故障", "降弓", "线路故障",
                     "设备", "停电", "信号故障", "道岔故障", "车辆故障"],
        "description": "突发故障场景 - 列车设备故障或线路异常",
        "operation_flow": [
            "1. 确认故障列车和位置",
            "2. 评估故障恢复时间",
            "3. 安排救援或调整运行计划",
            "4. 调整后续列车时刻表",
            "5. 发布调度命令",
            "6. 协调车站做好旅客服务"
        ]
    },
    "section_interrupt": {
        "keywords": ["区间中断", "线路中断", "完全中断", "无法通行"],
        "description": "区间中断场景 - 线路中断导致无法通行（预留）",
        "operation_flow": [
            "1. 确认中断区段和原因",
            "2. 评估恢复时间",
            "3. 安排列车绕行或折返",
            "4. 调整车底和乘务计划",
            "5. 发布调度命令",
            "6. 启动应急预案"
        ]
    }
}

# 操作流程模板
OPERATION_TEMPLATES = {
    "temporary_speed_limit": """
【临时限速场景调度操作流程】

一、场景确认
- 限速原因：{reason}
- 限速区段：{section}
- 限速值：{speed_limit} km/h
- 持续时间：{duration} 分钟

二、影响分析
- 受影响列车：{affected_trains}
- 预计延误时间：{delay_time}

三、调度措施
1. 发布限速调度命令
2. 调整受影响列车运行时刻
3. 通知相关车站和乘务组
4. 做好旅客解释工作
5. 恢复正常后及时取消限速

四、后续跟进
- 持续关注天气变化
- 及时调整调度方案
""",
    "sudden_failure": """
【突发故障场景调度操作流程】

一、故障确认
- 故障类型：{failure_type}
- 故障列车：{train_id}
- 故障位置：{location}
- 预计恢复时间：{repair_time} 分钟

二、应急处置
1. 立即扣停后续列车
2. 安排故障列车退行或救援
3. 调整列车运行计划
4. 做好旅客转运安排

三、调度措施
1. 发布故障处理调度命令
2. 调整受影响列车时刻表
3. 协调车站做好旅客服务
4. 安排备用车底（如需要）

四、后续跟进
- 跟踪故障处理进度
- 及时恢复列车运行
- 总结故障处理经验
"""
}


class RuleAgent:
    """
    基于固定规则的铁路调度Agent
    
    功能：
    1. 场景识别：基于关键词规则识别场景类型
    2. 操作流程生成：输出标准化的调度员操作流程
    3. 技能选择：基于场景类型选择对应的调度技能
    4. 结果返回：执行调度优化并返回结果
    5. 调度比较：比较FCFS和MIP等多种调度方法
    
    优点：
    - 无需加载大模型，启动快
    - 输出稳定，便于测试
    - 为微调数据集提供标准格式
    """
    
    def __init__(self, scheduler: MIPScheduler, trains=None, stations=None, enable_comparison: bool = True):
        """
        初始化Rule Agent
        
        Args:
            scheduler: MIP调度器实例
            trains: 列车列表（用于比较功能）
            stations: 车站列表（用于比较功能）
            enable_comparison: 是否启用调度比较功能
        """
        self.scheduler = scheduler
        self.trains = trains or scheduler.trains
        self.stations = stations or scheduler.stations
        self.enable_comparison = enable_comparison
        self.tool_registry = ToolRegistry(scheduler, self.trains, self.stations)
        logger.info("RuleAgent 初始化完成（固定规则模式）")
    
    def _detect_scenario(self, prompt: str) -> str:
        """
        基于关键词检测场景类型
        
        Args:
            prompt: 用户输入的调度需求
            
        Returns:
            str: 场景类型标识
        """
        prompt_lower = prompt.lower()
        
        # 按优先级检测场景
        for scenario_type, config in SCENARIO_KEYWORDS.items():
            for keyword in config["keywords"]:
                if keyword in prompt_lower:
                    return scenario_type
        
        # 默认返回临时限速
        return "temporary_speed_limit"
    
    def _extract_entities(self, prompt: str) -> Dict[str, Any]:
        """
        从输入中提取实体信息
        
        Args:
            prompt: 用户输入
            
        Returns:
            Dict: 提取的实体信息
        """
        entities = {
            "train_ids": [],
            "delay_minutes": [],
            "station_name": None,
            "reason": None
        }
        
        # 提取列车号
        train_pattern = r'([GDCTKZ]\d+)'
        entities["train_ids"] = re.findall(train_pattern, prompt)
        
        # 提取延误时间
        delay_pattern = r'(\d+)\s*分钟'
        delays = re.findall(delay_pattern, prompt)
        entities["delay_minutes"] = [int(d) for d in delays]
        
        # 车站名称映射
        station_name_to_code = {
            "北京西": "BJX", "杜家坎": "DJK", "涿州东": "ZBD",
            "高碑店东": "GBD", "徐水东": "XSD", "保定东": "BDD",
            "定州东": "DZD", "正定机场": "ZDJ", "石家庄": "SJP",
            "高邑西": "GYX", "邢台东": "XTD", "邯郸东": "HDD",
            "安阳东": "AYD"
        }
        
        for name, code in station_name_to_code.items():
            if name in prompt:
                entities["station_name"] = name
                entities["station_code"] = code
                break
        
        # 提取原因
        reason_keywords = ["大风", "暴雨", "降雪", "故障", "限速", "天气"]
        for kw in reason_keywords:
            if kw in prompt:
                entities["reason"] = kw
                break
        
        return entities
    
    def _generate_operation_flow(
        self,
        scenario_type: str,
        entities: Dict[str, Any],
        delay_injection: Dict[str, Any]
    ) -> str:
        """
        生成调度员操作流程
        
        Args:
            scenario_type: 场景类型
            entities: 提取的实体
            delay_injection: 延误注入数据
            
        Returns:
            str: 操作流程文本
        """
        template = OPERATION_TEMPLATES.get(scenario_type, "")
        
        if scenario_type == "temporary_speed_limit":
            params = delay_injection.get("scenario_params", {})
            return template.format(
                reason=entities.get("reason", "天气原因"),
                section=params.get("affected_section", "未知区段"),
                speed_limit=params.get("limit_speed_kmh", 200),
                duration=params.get("duration_minutes", 120),
                affected_trains=", ".join(entities.get("train_ids", ["未知"])),
                delay_time=f"{entities.get('delay_minutes', [0])[0]} 分钟" if entities.get("delay_minutes") else "待计算"
            )
        elif scenario_type == "sudden_failure":
            params = delay_injection.get("scenario_params", {})
            injected = delay_injection.get("injected_delays", [{}])
            return template.format(
                failure_type=params.get("failure_type", "车辆故障"),
                train_id=entities.get("train_ids", ["未知"])[0] if entities.get("train_ids") else "未知",
                location=entities.get("station_name", "未知位置"),
                repair_time=params.get("estimated_repair_time", 60)
            )
        
        return "暂无标准操作流程"
    
    def _build_reasoning(
        self,
        scenario_type: str,
        entities: Dict[str, Any],
        delay_injection: Dict[str, Any]
    ) -> str:
        """
        构建推理过程文本
        
        Args:
            scenario_type: 场景类型
            entities: 提取的实体
            delay_injection: 延误注入数据
            
        Returns:
            str: 推理过程文本
        """
        scenario_config = SCENARIO_KEYWORDS.get(scenario_type, {})
        
        reasoning_parts = [
            "【场景分析】",
            f"- 检测到场景类型：{scenario_type}",
            f"- 场景描述：{scenario_config.get('description', '未知场景')}",
            "",
            "【实体识别】",
            f"- 受影响列车：{', '.join(entities.get('train_ids', ['未识别']))}",
            f"- 延误时间：{entities.get('delay_minutes', ['未识别'])}",
            f"- 涉及车站：{entities.get('station_name', '未识别')}",
            f"- 原因：{entities.get('reason', '未识别')}",
            "",
            "【调度决策】",
        ]
        
        # 根据场景类型选择技能
        skill_mapping = {
            "temporary_speed_limit": "temporary_speed_limit_skill",
            "sudden_failure": "sudden_failure_skill",
            "section_interrupt": "section_interrupt_skill"
        }
        selected_skill = skill_mapping.get(scenario_type, "temporary_speed_limit_skill")
        reasoning_parts.append(f"- 选择技能：{selected_skill}")
        reasoning_parts.append(f"- 选择依据：场景类型为'{scenario_type}'，匹配对应调度技能")
        reasoning_parts.append(f"- 优化目标：最小化最大延误（min_max_delay）")
        
        return "\n".join(reasoning_parts)
    
    def _generate_json_output(
        self,
        scenario_type: str,
        entities: Dict[str, Any],
        delay_injection: Dict[str, Any]
    ) -> str:
        """
        生成标准JSON格式输出（用于微调数据集）
        
        Args:
            scenario_type: 场景类型
            entities: 提取的实体
            delay_injection: 延误注入数据
            
        Returns:
            str: JSON格式的输出
        """
        skill_mapping = {
            "temporary_speed_limit": "temporary_speed_limit_skill",
            "sudden_failure": "sudden_failure_skill",
            "section_interrupt": "section_interrupt_skill"
        }
        
        output = {
            "thinking": self._build_reasoning(scenario_type, entities, delay_injection),
            "tool_name": skill_mapping.get(scenario_type, "temporary_speed_limit_skill"),
            "arguments": {
                "train_ids": entities.get("train_ids", []),
                "station_codes": [],  # 将在执行时填充
                "delay_injection": delay_injection,
                "optimization_objective": "min_max_delay"
            }
        }
        
        return json.dumps(output, ensure_ascii=False, indent=2)
    
    def analyze(self, delay_injection: Dict[str, Any], user_prompt: str = "") -> AgentResult:
        """
        分析场景并执行调度（与QwenAgent接口一致）
        
        Args:
            delay_injection: 延误注入数据
            user_prompt: 用户输入的原始文本（可选）
            
        Returns:
            AgentResult: 执行结果
        """
        start_time = time.time()
        
        try:
            # Step 1: 场景识别
            scenario_type = delay_injection.get("scenario_type", "")
            
            # 如果delay_injection中没有场景类型，从原始输入推断
            if not scenario_type or scenario_type == "unknown":
                scenario_type = self._detect_scenario(user_prompt)
            
            # Step 2: 提取实体（用于生成推理过程）
            entities = self._extract_entities(user_prompt)
            
            # 如果实体中没有列车，从delay_injection获取
            if not entities["train_ids"]:
                entities["train_ids"] = delay_injection.get("affected_trains", [])
            
            # Step 3: 构建推理过程
            reasoning = self._build_reasoning(scenario_type, entities, delay_injection)
            
            # Step 4: 生成JSON输出（模拟大模型输出）
            json_output = self._generate_json_output(scenario_type, entities, delay_injection)
            
            # Step 5: 选择并执行技能
            skill_mapping = {
                "temporary_speed_limit": "temporary_speed_limit_skill",
                "sudden_failure": "sudden_failure_skill",
                "section_interrupt": "section_interrupt_skill"
            }
            selected_skill = skill_mapping.get(scenario_type, "temporary_speed_limit_skill")
            
            # 获取车站编码列表
            station_codes = [stop.station_code for train in self.scheduler.trains 
                           for stop in train.schedule.stops]
            station_codes = list(dict.fromkeys(station_codes))  # 去重保序
            
            # 执行工具
            dispatch_result = self.tool_registry.execute(
                selected_skill,
                {
                    "train_ids": delay_injection.get("affected_trains", entities.get("train_ids", [])),
                    "station_codes": station_codes,
                    "delay_injection": delay_injection,
                    "optimization_objective": "min_max_delay"
                }
            )
            
            computation_time = time.time() - start_time
            
            return AgentResult(
                success=True,
                recognized_scenario=scenario_type,
                selected_skill=selected_skill,
                reasoning=reasoning,
                dispatch_result=dispatch_result,
                model_response=json_output,
                computation_time=computation_time
            )
            
        except Exception as e:
            logger.exception(f"RuleAgent执行错误: {str(e)}")
            return AgentResult(
                success=False,
                recognized_scenario="error",
                selected_skill="",
                reasoning="",
                dispatch_result=None,
                model_response="",
                computation_time=time.time() - start_time,
                error_message=str(e)
            )
    
    def analyze_with_comparison(
        self, 
        delay_injection: Dict[str, Any], 
        user_prompt: str = "",
        comparison_criteria: str = "balanced"
    ) -> AgentResult:
        """
        分析场景并执行调度比较（比较FCFS和MIP等多种调度方法）
        
        Args:
            delay_injection: 延误注入数据
            user_prompt: 用户输入的原始文本
            comparison_criteria: 比较准则 (min_max_delay/min_avg_delay/balanced/real_time)
            
        Returns:
            AgentResult: 包含比较结果的执行结果
        """
        start_time = time.time()
        
        try:
            # Step 1: 场景识别
            scenario_type = delay_injection.get("scenario_type", "")
            if not scenario_type or scenario_type == "unknown":
                scenario_type = self._detect_scenario(user_prompt)
            
            # Step 2: 提取实体
            entities = self._extract_entities(user_prompt)
            if not entities["train_ids"]:
                entities["train_ids"] = delay_injection.get("affected_trains", [])
            
            # Step 3: 构建推理过程（包含比较说明）
            base_reasoning = self._build_reasoning(scenario_type, entities, delay_injection)
            comparison_reasoning = f"""
{base_reasoning}

【调度方法比较】
- 启用多调度方法比较功能
- 比较准则：{comparison_criteria}
- 参与比较的调度器：FCFS（先到先服务）、MIP（整数规划）
- 将根据综合得分选择最优方案
"""
            
            # Step 4: 获取车站编码列表
            station_codes = [stop.station_code for train in self.trains 
                           for stop in train.schedule.stops]
            station_codes = list(dict.fromkeys(station_codes))
            
            # Step 5: 执行调度比较技能
            # 在delay_injection中添加用户偏好
            delay_injection_with_preference = dict(delay_injection)
            if "scenario_params" not in delay_injection_with_preference:
                delay_injection_with_preference["scenario_params"] = {}
            delay_injection_with_preference["scenario_params"]["user_preference"] = comparison_criteria
            
            dispatch_result = self.tool_registry.execute(
                "scheduler_comparison_skill",
                {
                    "train_ids": delay_injection.get("affected_trains", entities.get("train_ids", [])),
                    "station_codes": station_codes,
                    "delay_injection": delay_injection_with_preference,
                    "optimization_objective": "min_max_delay"
                }
            )
            
            # Step 6: 构建比较结果输出
            comparison_summary = ""
            if dispatch_result.success:
                stats = dispatch_result.delay_statistics
                ranking = stats.get("ranking", [])
                winner = stats.get("winner_scheduler", "")
                
                comparison_summary = f"""
比较结果：
- 最优调度器：{winner}
- 排名：{', '.join([f'{r["rank"]}. {r["scheduler"]}({r["max_delay_minutes"]}分钟)' for r in ranking])}
- 推荐：{stats.get("recommendations", [])}
"""
            
            computation_time = time.time() - start_time
            
            return AgentResult(
                success=True,
                recognized_scenario=scenario_type,
                selected_skill="scheduler_comparison_skill",
                reasoning=comparison_reasoning,
                dispatch_result=dispatch_result,
                model_response=comparison_summary,
                computation_time=computation_time
            )
            
        except Exception as e:
            logger.exception(f"RuleAgent比较执行错误: {str(e)}")
            return AgentResult(
                success=False,
                recognized_scenario="error",
                selected_skill="",
                reasoning="",
                dispatch_result=None,
                model_response="",
                computation_time=time.time() - start_time,
                error_message=str(e)
            )
    
    def summarize_result(self, result: AgentResult) -> str:
        """
        生成结果总结（与QwenAgent一致）
        
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
推理过程: 
{result.reasoning}

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
    
    def chat_direct(self, messages: List[Dict[str, str]], max_new_tokens: int = 512) -> str:
        """
        直接对话接口（与QwenAgent接口保持一致）
        
        Args:
            messages: 对话消息列表
            max_new_tokens: 最大生成token数（固定模式下忽略）
            
        Returns:
            str: 模型响应
        """
        # 提取用户消息
        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break
        
        # 基于规则的简单回复
        scenario = self._detect_scenario(user_message)
        entities = self._extract_entities(user_message)
        
        response = f"""您好！我是铁路调度助手（规则模式）。

根据您的描述，我识别到：
- 场景类型：{scenario}
- 相关列车：{', '.join(entities.get('train_ids', ['未识别']))}
- 涉及车站：{entities.get('station_name', '未识别')}

如需执行调度，请使用智能调度功能。"""
        
        return response


# ============================================
# 工厂函数
# ============================================

def create_rule_agent(
    trains = None,
    stations = None,
    enable_comparison: bool = True
) -> RuleAgent:
    """
    创建Rule Agent实例

    Args:
        trains: 列车列表（可选，默认使用真实数据）
        stations: 车站列表（可选，默认使用真实数据）
        enable_comparison: 是否启用调度比较功能

    Returns:
        RuleAgent: Agent实例
    """
    if trains is None or stations is None:
        from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
        use_real_data(True)
        trains = get_trains_pydantic()[:50]
        stations = get_stations_pydantic()

    scheduler = MIPScheduler(trains, stations)
    return RuleAgent(scheduler, trains=trains, stations=stations, enable_comparison=enable_comparison)


# ============================================
# 测试代码
# ============================================

if __name__ == "__main__":
    print("=" * 60)
    print("Rule Agent 测试")
    print("=" * 60)
    
    agent = create_rule_agent()
    
    # 测试场景1：临时限速
    print("\n" + "=" * 60)
    print("测试场景1: 临时限速（大风导致限速）")
    print("=" * 60)
    
    delay_injection_1 = {
        "scenario_type": "temporary_speed_limit",
        "scenario_id": "TEST_001",
        "injected_delays": [
            {
                "train_id": "G1215",
                "location": {"location_type": "station", "station_code": "XSD"},
                "initial_delay_seconds": 600,
                "timestamp": "2024-01-15T10:00:00Z"
            }
        ],
        "affected_trains": ["G1215"],
        "scenario_params": {
            "limit_speed_kmh": 200,
            "duration_minutes": 120,
            "affected_section": "XSD -> BDD"
        }
    }
    
    user_prompt_1 = "G1215在徐水东因遭遇大风预计延误10分钟"
    result1 = agent.analyze(delay_injection_1, user_prompt_1)
    print(agent.summarize_result(result1))
    
    # 测试场景2：突发故障
    print("\n" + "=" * 60)
    print("测试场景2: 突发故障（设备故障）")
    print("=" * 60)
    
    delay_injection_2 = {
        "scenario_type": "sudden_failure",
        "scenario_id": "TEST_002",
        "injected_delays": [
            {
                "train_id": "G1239",
                "location": {"location_type": "station", "station_code": "BDD"},
                "initial_delay_seconds": 1800,
                "timestamp": "2024-01-15T11:00:00Z"
            }
        ],
        "affected_trains": ["G1239"],
        "scenario_params": {
            "failure_type": "vehicle_breakdown",
            "estimated_repair_time": 60
        }
    }
    
    user_prompt_2 = "G1239在保定东发生设备故障，预计延误30分钟"
    result2 = agent.analyze(delay_injection_2, user_prompt_2)
    print(agent.summarize_result(result2))
    
    print("\n" + "=" * 60)
    print("测试完成 - RuleAgent无需大模型即可运行")
    print("=" * 60)
