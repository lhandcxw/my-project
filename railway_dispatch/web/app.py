# -*- coding: utf-8 -*-
"""
铁路调度系统 - Web后端 (Flask)
降低环境配置难度
"""
import os
os.environ["RULE_AGENT_USE_WORKFLOW"] = "1"




from flask import Flask, render_template, request, jsonify, Response
from flask_cors import CORS
import json
import base64
import logging

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.data_models import Train, Station, DelayInjection, ScenarioType, InjectedDelay, DelayLocation
from models.data_loader import get_trains_pydantic, get_stations_pydantic, get_station_codes, get_station_names, get_train_ids, use_real_data, is_using_real_data
from solver.mip_scheduler import MIPScheduler
from railway_agent import create_skills, execute_skill
from railway_agent.session_manager import get_session_manager, SessionManager
from evaluation.evaluator import Evaluator

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 导入运行图生成模块（经典铁路运行图风格：横轴时间，纵轴车站）
from visualization.simple_diagram import create_train_diagram, create_comparison_diagram

# 导入新架构 Agent
from railway_agent import RuleAgent, create_rule_agent, ToolRegistry

# QwenAgent延迟导入（已迁移到新架构，使用统一Agent）
def get_qwen_agent_module():
    """获取Qwen Agent模块（新架构）"""
    # 新架构使用统一的Agent，支持多种模式
    from railway_agent import RuleAgent, create_rule_agent
    return RuleAgent, create_rule_agent

app = Flask(__name__)
CORS(app)  # 启用跨域支持

# 启用真实数据
# 使用真实数据，避免示例数据混淆
use_real_data(True)
logger.info("已启用真实数据模式")

# 全局数据 - 从 centralized data loader 加载
# 加载所有列车（真实数据共147列）
all_trains = get_trains_pydantic()
trains = all_trains  # 使用全部列车
stations = get_stations_pydantic()
station_codes = get_station_codes()
station_names = get_station_names()
train_ids = get_train_ids()

# 创建调度器
scheduler = MIPScheduler(trains, stations)
skills = create_skills(scheduler)
evaluator = Evaluator()

# 从统一配置中心导入配置
from config import AppConfig, LLMConfig, get_config_summary

# 设置环境变量（统一配置中心已定义，这里确保生效）
os.environ['DASHSCOPE_API_KEY'] = LLMConfig.DASHSCOPE_API_KEY
os.environ['DASHSCOPE_MODEL'] = LLMConfig.DASHSCOPE_MODEL
os.environ['LLM_PROVIDER'] = LLMConfig.PROVIDER

# 导出常用配置
AGENT_MODE = AppConfig.AGENT_MODE

# 打印配置摘要
logger.info(get_config_summary())

# Agent (延迟加载)
qwen_agent = None

def get_qwen_agent():
    """
    获取或创建Agent实例（新架构v2）

    新架构说明：
    - 所有Agent模式（rule/qwen/auto）都使用统一的新架构Agent
    - 新架构Agent内部包含工作流引擎，支持LLM调用
    - 多轮对话和单轮对话都能正常工作
    """
    global qwen_agent

    if qwen_agent is not None:
        return qwen_agent

    # 新架构：使用统一的Agent（RuleAgent），内部包含工作流引擎
    logger.info(f"初始化新架构Agent，模式: {AGENT_MODE}")
    qwen_agent = create_rule_agent(trains=trains, stations=stations)
    logger.info("新架构Agent初始化完成（支持单轮对话和多轮对话）")
    return qwen_agent


def get_original_schedule():
    """获取原始时刻表"""
    schedule = {}
    for train in trains:
        stops = []
        if train.schedule and train.schedule.stops and isinstance(train.schedule.stops, (list, tuple)):
            for stop in train.schedule.stops:
                stops.append({
                    "station_code": stop.station_code,
                    "station_name": stop.station_name,
                    "arrival_time": stop.arrival_time,
                    "departure_time": stop.departure_time,
                    "original_arrival": stop.arrival_time,
                    "original_departure": stop.departure_time,
                    "delay_seconds": 0
                })
        schedule[train.train_id] = stops
    return schedule




@app.route('/')
def index():
    return render_template(
        'index.html',
        train_ids=train_ids,
        station_codes=station_codes,
        station_names=station_names
    )


@app.route('/api/dispatch', methods=['POST'])
def dispatch():
    try:
        data = request.json
        logger.info(f"收到dispatch请求，scenario_type: {data.get('scenario_type')}")
        
        # 构建延误注入
        scenario_type = data.get('scenario_type', 'temporary_speed_limit')
        selected_trains = data.get('selected_trains', [])
        delay_config = data.get('delay_config', [])
        
        if scenario_type == 'temporary_speed_limit':
            # 使用有效的站点（preset数据中列车从XSD出发，真实数据从BJX出发）
            first_station = "XSD" if not is_using_real_data() else station_codes[0]
            second_station = "BDD" if not is_using_real_data() else station_codes[1]
            affected_section = f"{first_station} -> {second_station}"

            delay_injection = DelayInjection.create_temporary_speed_limit(
                scenario_id="WEB_SC_001",
                train_delays=delay_config,
                limit_speed=data.get('limit_speed', 200),
                duration=data.get('duration', 120),
                affected_section=affected_section
            )
        else:
            # 获取有效的站点编码 - 必须确保站点是所选列车实际停靠的站点
            default_station = "XSD" if not is_using_real_data() else station_codes[0]
            delay_station = delay_config[0].get('station_code') if delay_config else default_station

            # 确保站点编码有效：如果不存在于当前数据中，或者不是所选列车的停靠站，使用列车实际停靠的第一站
            if delay_station not in station_codes or (selected_trains and len(selected_trains) > 0):
                # 找到所选列车的停靠站列表
                valid_stations_for_train = []
                if selected_trains:
                    for train in trains:
                        if train.train_id == selected_trains[0]:
                            valid_stations_for_train = [s.station_code for s in train.schedule.stops] if train.schedule and train.schedule.stops else []
                            break

                # 如果选择的站点不在列车的停靠列表中，使用第一站
                if valid_stations_for_train and delay_station not in valid_stations_for_train:
                    delay_station = valid_stations_for_train[0] if valid_stations_for_train else default_station
            # 如果仍然不在station_codes中，使用默认
            if delay_station not in station_codes:
                delay_station = default_station

            delay_injection = DelayInjection.create_sudden_failure(
                scenario_id="WEB_SC_001",
                train_id=selected_trains[0] if selected_trains else "G1215",
                delay_seconds=delay_config[0].get('delay_seconds', 1800) if delay_config else 1800,
                station_code=delay_station,
                failure_type="vehicle_breakdown",
                repair_time=60
            )

        # 使用Qwen Agent或直接执行Skill（兜底）
        agent = get_qwen_agent()
        if agent:
            # 使用Qwen Agent
            result = agent.analyze(delay_injection.model_dump())
            logger.info(f"Agent分析结果: success={result.success}, dispatch_result={result.dispatch_result is not None}")

            if result.success and result.dispatch_result:
                skill_result = result.dispatch_result
                logger.info(f"Skill结果: message={skill_result.message}, optimized_schedule keys={list(skill_result.optimized_schedule.keys()) if skill_result.optimized_schedule else 'None'}")

                return jsonify({
                    "success": True,
                    "planner": {
                        "recognized_scenario": result.recognized_scenario,
                        "delay_level": "0",
                        "confidence": 0.9
                    },
                    "skill_result": {
                        "message": skill_result.message,
                        "optimized_schedule": skill_result.optimized_schedule,
                        "delay_statistics": skill_result.delay_statistics,
                        "computation_time": skill_result.computation_time
                    },
                    "original_schedule": get_original_schedule()
                })
            else:
                logger.error(f"Agent调用失败: success={result.success}, error={result.error_message}")

        # 兜底：直接执行Skill
        skill_name = "temporary_speed_limit_skill" if scenario_type == "temporary_speed_limit" else "sudden_failure_skill"
        logger.info(f"使用兜底模式，执行skill: {skill_name}")

        skill_result = execute_skill(
            skill_name=skill_name,
            skills=skills,
            train_ids=selected_trains,
            station_codes=station_codes,
            delay_injection=delay_injection.model_dump(),
            optimization_objective=data.get('objective', 'min_max_delay')
        )

        logger.info(f"Skill执行结果: success={skill_result.success}, message={skill_result.message}")
        logger.info(f"Optimized schedule keys: {list(skill_result.optimized_schedule.keys()) if skill_result.optimized_schedule else 'None'}")

        # 返回结果
        original_schedule = get_original_schedule()

        return jsonify({
            "success": True,
            "planner": {
                "recognized_scenario": scenario_type,
                "delay_level": "0",
                "confidence": 0.9
            },
            "skill_result": {
                "message": skill_result.message,
                "optimized_schedule": skill_result.optimized_schedule,
                "delay_statistics": skill_result.delay_statistics,
                "computation_time": skill_result.computation_time
            },
            "original_schedule": original_schedule
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": str(e)
        })


@app.route('/api/diagram', methods=['POST'])
def generate_diagram():
    """
    生成铁路运行图API
    使用 railway_diagram.py 的绘图方式：横轴时间，纵轴车站
    """
    try:
        data = request.json

        original_schedule = data.get('original_schedule', {})
        optimized_schedule = data.get('optimized_schedule', {})

        # 转换为 railway_diagram.py 需要的格式
        def convert_schedule(schedule_dict):
            """将时刻表转换为列车列表格式"""
            trains_list = []
            for train_id, stops in schedule_dict.items():
                trains_list.append({
                    "train_id": train_id,
                    "schedule": {
                        "stops": [
                            {
                                "station_code": stop["station_code"],
                                "station_name": stop.get("station_name", stop["station_code"]),
                                "arrival_time": stop["arrival_time"],
                                "departure_time": stop["departure_time"]
                            }
                            for stop in stops
                        ]
                    }
                })
            return trains_list

        original_trains = convert_schedule(original_schedule)
        optimized_trains = convert_schedule(optimized_schedule)

        # 生成对比图（横轴时间，纵轴车站）
        img_base64 = create_comparison_diagram(
            original_trains,
            optimized_trains,
            "Railway Train Diagram"
        )

        return jsonify({
            "success": True,
            "diagram_image": img_base64
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": str(e)
        })


@app.route('/api/agent_chat', methods=['POST'])
def agent_chat():
    """
    Agent 对话API
    接收自然语言输入，Agent自动识别场景并执行调度
    
    支持两种Agent模式：
    - RuleAgent: 固定规则，无需大模型
    - QwenAgent: 大模型驱动
    """
    try:
        data = request.json
        prompt = data.get('prompt', '')

        if not prompt:
            logger.warning("收到空prompt请求")
            return jsonify({
                "success": False,
                "message": "请输入调度需求"
            })

        logger.info(f"收到agent_chat请求，prompt: {prompt[:50]}...")

        # 获取Agent
        agent = get_qwen_agent()
        if agent is None:
            logger.error("Agent未初始化")
            return jsonify({
                "success": False,
                "message": "Agent未初始化，请检查配置"
            })

        # 解析用户输入，构建DelayInjection
        delay_injection = parse_user_prompt(prompt)
        logger.info(f"解析后的延误注入: {delay_injection.get('scenario_type')}, 列车: {delay_injection.get('affected_trains')}")

        # 调用Agent分析
        # RuleAgent和QwenAgent都支持analyze方法
        if isinstance(agent, RuleAgent):
            # RuleAgent需要传入原始prompt用于推理过程生成
            result = agent.analyze(delay_injection, user_prompt=prompt)
        else:
            # QwenAgent
            result = agent.analyze(delay_injection)

        if result.success and result.dispatch_result:
            dispatch = result.dispatch_result

            # 获取原始时刻表
            original_schedule = get_original_schedule()

            logger.info("Agent分析成功，返回结果")
            return jsonify({
                "success": True,
                "recognized_scenario": result.recognized_scenario,
                "selected_skill": result.selected_skill,
                "reasoning": result.reasoning,
                "delay_statistics": dispatch.delay_statistics,
                "message": dispatch.message,
                "computation_time": result.computation_time,
                "optimized_schedule": dispatch.optimized_schedule,
                "original_schedule": original_schedule
            })
        else:
            logger.error(f"Agent分析失败: {result.error_message}")
            return jsonify({
                "success": False,
                "message": result.error_message or "Agent执行失败"
            })

    except Exception as e:
        logger.exception(f"agent_chat处理异常: {str(e)}")
        return jsonify({
            "success": False,
            "message": str(e)
        })


@app.route('/api/general_chat', methods=['POST'])
def general_chat():
    """
    通用对话API - 不强制Tool调用
    用于回答关于系统的一般问题
    """
    try:
        data = request.json
        prompt = data.get('prompt', '')

        if not prompt:
            return jsonify({
                "success": False,
                "message": "请输入问题"
            })

        # 获取Qwen Agent
        agent = get_qwen_agent()
        if agent is None:
            return jsonify({
                "success": False,
                "message": "Qwen Agent未初始化"
            })

        # 构建通用对话Prompt（不包含Tools，自由的对话）
        general_prompt = f"""你是一个友好的铁路调度助手。请用通俗易懂的语言回答用户的问题。

用户问题: {prompt}

回答要求：
- 简洁明了
- 如果是技术术语，请简单解释
- 如果不知道，请如实说明"""

        # 调用模型（不使用Tool）
        messages = [
            {"role": "system", "content": "你是一个友好、专业的铁路调度助手。"},
            {"role": "user", "content": general_prompt}
        ]

        response = agent.chat_direct(messages)

        return jsonify({
            "success": True,
            "response": response
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": str(e)
        })


def parse_user_prompt(prompt: str) -> dict:
    """
    解析用户输入，构建DelayInjection

    简单规则解析，后续可接入LLM进行语义理解
    """
    import re

    prompt_lower = prompt.lower()

    # 检测场景类型
    # 临时限速场景关键字：限速、大风、暴雨、降雪、冰雪、雨量、风速等天气原因
    if '限速' in prompt or '大风' in prompt or '暴雨' in prompt or '降雪' in prompt or '冰雪' in prompt or '雨量' in prompt or '风速' in prompt or '天气' in prompt:
        scenario_type = 'temporary_speed_limit'
    # 突发故障场景关键字：故障、中断、封锁、设备故障、降弓、线路故障等
    elif '故障' in prompt or '中断' in prompt or '封锁' in prompt or '设备故障' in prompt or '降弓' in prompt or '线路' in prompt or '设备' in prompt:
        scenario_type = 'sudden_failure'
    else:
        scenario_type = 'temporary_speed_limit'  # 默认

    # 提取列车和延误信息
    # 匹配格式：G1001延误10分钟，G1003延误15分钟
    train_pattern = r'([GDCTKZ]\d+)'
    delay_pattern = r'(\d+)\s*分钟'

    train_ids = re.findall(train_pattern, prompt)
    delays = re.findall(delay_pattern, prompt)

    # 如果没有提取到，使用默认
    if not train_ids:
        train_ids = ['G1001']
    if not delays:
        delays = ['600']

    # 提取车站信息
    # 车站名称到代码的映射
    station_name_to_code = {
        "北京西": "BJX", "bjx": "BJX",
        "杜家坎线路所": "DJK", "djk": "DJK",
        "涿州东": "ZBD", "zbd": "ZBD",
        "高碑店东": "GBD", "gbd": "GBD",
        "徐水东": "XSD", "xsd": "XSD",
        "保定东": "BDD", "bdd": "BDD",
        "定州东": "DZD", "dzd": "DZD",
        "正定机场": "ZDJ", "zdj": "ZDJ",
        "石家庄": "SJP", "sjp": "SJP",
        "高邑西": "GYX", "gyx": "GYX",
        "邢台东": "XTD", "xtd": "XTD",
        "邯郸东": "HDD", "hdd": "HDD",
        "安阳东": "AYD", "ayd": "AYD"
    }

    # 尝试从输入中提取车站
    detected_station_code = None
    for name, code in station_name_to_code.items():
        if name in prompt:
            detected_station_code = code
            break

    # 如果没有检测到车站，使用默认第一个车站
    if detected_station_code is None:
        detected_station_code = "BJX"  # 使用北京西作为默认车站

    # 构建DelayInjection
    injected_delays = []
    for i, train_id in enumerate(train_ids):
        delay_seconds = int(delays[i]) * 60 if i < len(delays) else 600

        # 验证列车是否停靠在选定的车站
        # 如果不停靠，使用列车的第一个停靠站
        train = None
        for t in trains:
            if t.train_id == train_id:
                train = t
                break

        actual_station_code = detected_station_code
        if train and train.schedule:
            # 检查列车是否停靠在选定车站（增强安全性检查）
            train_stations = []
            if train.schedule.stops and isinstance(train.schedule.stops, (list, tuple)):
                train_stations = [stop.station_code for stop in train.schedule.stops if hasattr(stop, 'station_code')]
            if detected_station_code not in train_stations and train_stations:
                # 使用列车的第一个停靠站
                first_stop = train.schedule.stops[0]
                if hasattr(first_stop, 'station_code'):
                    actual_station_code = first_stop.station_code
                    logger.warning(f"列车 {train_id} 不停靠在 {detected_station_code}，使用 {actual_station_code} 作为延误车站")
        elif not train:
            logger.warning(f"列车 {train_id} 不在列车列表中，使用默认车站 {detected_station_code}")
        elif not train.schedule:
            logger.warning(f"列车 {train_id} 没有时刻表信息，使用默认车站 {detected_station_code}")

        injected_delays.append({
            "train_id": train_id,
            "location": {"location_type": "station", "station_code": actual_station_code},
            "initial_delay_seconds": delay_seconds,
            "timestamp": "2024-01-15T10:00:00Z"
        })

    # 构建完整的delay_injection
    if scenario_type == 'temporary_speed_limit':
        return {
            "scenario_type": scenario_type,
            "scenario_id": "AGENT_CHAT_001",
            "injected_delays": injected_delays,
            "affected_trains": train_ids,
            "scenario_params": {
                "limit_speed_kmh": 200,
                "duration_minutes": 120,
                "affected_section": f"{detected_station_code} -> {detected_station_code}"
            }
        }
    else:  # sudden_failure
        return {
            "scenario_type": scenario_type,
            "scenario_id": "AGENT_CHAT_001",
            "injected_delays": injected_delays,
            "affected_trains": train_ids,
            "scenario_params": {
                "failure_type": "vehicle_breakdown",
                "estimated_repair_time": 60
            }
        }


@app.route('/api/agent_chat_with_comparison', methods=['POST'])
def agent_chat_with_comparison():
    """
    Agent对话API（带调度比较）
    比较FCFS和MIP等多种调度方法，返回最优方案
    """
    try:
        data = request.json
        prompt = data.get('prompt', '')
        comparison_criteria = data.get('comparison_criteria', 'balanced')

        if not prompt:
            return jsonify({
                "success": False,
                "message": "请输入调度需求"
            })

        logger.info(f"收到带比较的agent_chat请求，prompt: {prompt[:50]}...")

        # 获取Agent
        agent = get_qwen_agent()
        if agent is None:
            return jsonify({
                "success": False,
                "message": "Agent未初始化"
            })

        # 解析用户输入
        delay_injection = parse_user_prompt(prompt)
        
        # 添加用户偏好到scenario_params
        if "scenario_params" not in delay_injection:
            delay_injection["scenario_params"] = {}
        delay_injection["scenario_params"]["user_preference"] = comparison_criteria

        logger.info(f"解析后的延误注入: {delay_injection.get('scenario_type')}, 列车: {delay_injection.get('affected_trains')}")

        # 调用Agent分析（带比较）
        if isinstance(agent, RuleAgent):
            result = agent.analyze_with_comparison(delay_injection, user_prompt=prompt, comparison_criteria=comparison_criteria)
        else:
            # QwenAgent也使用RuleAgent的比较方法（因为比较功能与模型无关）
            result = agent.analyze_with_comparison(delay_injection, comparison_criteria=comparison_criteria) if hasattr(agent, 'analyze_with_comparison') else agent.analyze(delay_injection)

        if result.success and result.dispatch_result:
            dispatch = result.dispatch_result
            original_schedule = get_original_schedule()

            logger.info("Agent比较分析成功，返回结果")
            return jsonify({
                "success": True,
                "recognized_scenario": result.recognized_scenario,
                "selected_skill": result.selected_skill,
                "reasoning": result.reasoning,
                "delay_statistics": dispatch.delay_statistics,
                "message": dispatch.message,
                "computation_time": result.computation_time,
                "optimized_schedule": dispatch.optimized_schedule,
                "original_schedule": original_schedule
            })
        else:
            return jsonify({
                "success": False,
                "message": result.error_message or "Agent执行失败"
            })

    except Exception as e:
        logger.exception(f"agent_chat_with_comparison处理异常: {str(e)}")
        return jsonify({
            "success": False,
            "message": str(e)
        })


@app.route('/api/scheduler_comparison', methods=['POST'])
def scheduler_comparison():
    """
    调度方法比较API
    比较FCFS、MIP、基线等多种调度方法
    """
    try:
        data = request.json
        
        train_id = data.get('train_id')
        station_code = data.get('station_code')
        delay_seconds = data.get('delay_seconds', 1200)
        criteria = data.get('criteria', 'balanced')
        
        if not train_id:
            return jsonify({
                "success": False,
                "message": "请提供列车ID"
            })
        
        logger.info(f"调度比较请求: train={train_id}, station={station_code}, delay={delay_seconds}s, criteria={criteria}")
        
        # 构建延误注入
        delay_injection = DelayInjection(
            scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
            scenario_id="COMPARISON_API",
            injected_delays=[
                InjectedDelay(
                    train_id=train_id,
                    location=DelayLocation(location_type="station", station_code=station_code),
                    initial_delay_seconds=delay_seconds,
                    timestamp="2024-01-15T10:00:00Z"
                )
            ],
            affected_trains=[train_id],
            scenario_params={
                "user_preference": criteria
            }
        )
        
        # 使用Agent的比较功能
        agent = get_qwen_agent()
        if agent is None:
            return jsonify({
                "success": False,
                "message": "Agent未初始化"
            })
        
        if isinstance(agent, RuleAgent):
            result = agent.analyze_with_comparison(
                delay_injection.model_dump(),
                user_prompt=f"{train_id}在{station_code}延误{delay_seconds // 60}分钟",
                comparison_criteria=criteria
            )
        else:
            result = agent.analyze_with_comparison(delay_injection.model_dump(), comparison_criteria=criteria) if hasattr(agent, 'analyze_with_comparison') else agent.analyze(delay_injection.model_dump())
        
        if result.success and result.dispatch_result:
            dispatch = result.dispatch_result
            
            # 构建结构化输出
            comparison_output = {
                "success": True,
                "comparison": {
                    "recommendation": {
                        "scheduler_name": dispatch.delay_statistics.get("winner_scheduler", "未知"),
                        "scheduler_type": "mip",
                        "key_metrics": {
                            "max_delay_minutes": dispatch.delay_statistics.get("max_delay_seconds", 0) // 60,
                            "avg_delay_minutes": round(dispatch.delay_statistics.get("avg_delay_seconds", 0) / 60, 2),
                            "affected_trains": dispatch.delay_statistics.get("affected_trains_count", 0),
                            "on_time_rate": round(dispatch.delay_statistics.get("on_time_rate", 1.0) * 100, 1)
                        }
                    },
                    "all_options": dispatch.delay_statistics.get("ranking", []),
                    "analysis": dispatch.delay_statistics.get("recommendations", [])
                },
                "message": dispatch.message
            }
            
            return jsonify(comparison_output)
        else:
            return jsonify({
                "success": False,
                "message": result.error_message or "比较执行失败"
            })
    
    except Exception as e:
        logger.exception(f"调度比较异常: {str(e)}")
        return jsonify({
            "success": False,
            "message": str(e)
        })


# RuleAgent 到 Workflow 的桥接调试接口
def rule_workflow_debug():
    """
    RuleAgent 到 Workflow 的桥接调试接口
    用于测试 RuleAgent 走新工作流路径
    """
    try:
        from railway_agent.rule_workflow_bridge import (
            run_rule_workflow_bridge,
            is_bridge_enabled
        )
        from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data

        data = request.json or {}

        # 获取数据
        use_real_data(True)
        trains = get_trains_pydantic()[:10]  # 限制数量
        stations = get_stations_pydantic()

        # 检查是否启用
        bridge_enabled = is_bridge_enabled()

        # 解析请求
        user_input = data

        # 调用桥接（默认 dry_run=True）
        dry_run = data.get("dry_run", True)
        workflow_result, fallback_triggered = run_rule_workflow_bridge(
            user_input=user_input,
            trains=trains,
            stations=stations,
            dry_run=dry_run
        )

        # 构建响应
        response = {
            "success": not fallback_triggered and (workflow_result.success if workflow_result else False),
            "mode": "rule_workflow_bridge",
            "bridge_enabled": bridge_enabled,
            "fallback_triggered": fallback_triggered
        }

        if workflow_result:
            response["scene_type"] = workflow_result.scene_spec.scene_type if workflow_result.scene_spec else None
            response["task_id"] = workflow_result.task_plan.task_id if workflow_result.task_plan else None
            response["task_plan"] = workflow_result.task_plan.model_dump() if workflow_result.task_plan else None
            response["solver_result"] = workflow_result.solver_result.model_dump() if workflow_result.solver_result else None
            response["debug_trace"] = workflow_result.debug_trace

            # 添加 fallback 标记到 debug_trace
            if fallback_triggered and response["debug_trace"]:
                response["debug_trace"]["solver"] = response["debug_trace"].get("solver", {})
                response["debug_trace"]["solver"]["fallback_used"] = True
                response["debug_trace"]["solver"]["fallback_reason"] = "MIP求解失败，使用备用求解器"

            response["message"] = workflow_result.message

        return jsonify(response)

    except Exception as e:
        logger.exception(f"rule_workflow_debug处理异常: {str(e)}")
        return jsonify({
            "success": False,
            "message": str(e)
        })


# ============== 多轮对话 API ==============

@app.route('/api/workflow/start', methods=['POST'])
def workflow_start():
    """
    启动LLM驱动的4层工作流（多轮对话第一轮）

    请求体:
    {
        "user_input": "用户自然语言描述",
        "snapshot_info": {...}
    }

    响应:
    {
        "session_id": "会话ID",
        "current_layer": 1,
        "progress": "执行中: 数据建模层",
        "messages": [
            {"role": "user", "content": "用户输入"},
            {"role": "system", "content": "[第1层] 识别场景..."}
        ]
    }
    """
    try:
        data = request.json
        user_input = data.get('user_input', '')
        snapshot_info = data.get('snapshot_info', {})

        if not user_input:
            return jsonify({
                "success": False,
                "message": "请输入调度需求"
            })

        logger.info(f"启动多轮工作流，输入: {user_input[:50]}...")

        # 导入新架构工作流引擎
        from railway_agent.llm_workflow_engine_v2 import LLMWorkflowEngineV2, create_workflow_engine
        from railway_agent.workflow.layer1_data_modeling import Layer1DataModeling

        # 步骤1：L0+L1 合并处理（数据建模层）
        # 先进行L0预处理，构建CanonicalDispatchRequest
        layer1 = Layer1DataModeling()
        canonical_request = layer1.build_canonical_request_from_input(user_input)
        logger.info(f"[L0] 预处理完成，scene_type={canonical_request.scene_type_code}, location={canonical_request.location.station_code if canonical_request.location else 'None'}")

        # 创建工作流引擎
        workflow_engine = create_workflow_engine()

        # 执行第1层（数据建模），传入L0预处理结果
        result = workflow_engine.execute_layer1(user_input=user_input, canonical_request=canonical_request)

        # 构建 NetworkSnapshot（使用 SnapshotBuilder）
        from railway_agent.snapshot_builder import get_snapshot_builder
        from models.workflow_models import NetworkSnapshot
        from datetime import datetime

        snapshot_builder = get_snapshot_builder()
        try:
            # 尝试从 accident_card 构建 canonical_request
            accident_card = result.get("accident_card")
            if accident_card and hasattr(accident_card, 'model_dump'):
                # 创建简化的 canonical_request
                from models.preprocess_models import CanonicalDispatchRequest, LocationInfo, CompletenessInfo
                from models.common_enums import RequestSourceType, SceneTypeCode

                acc_data = accident_card.model_dump()
                
                # 从 accident_card 获取场景类型
                from models.common_enums import SceneTypeCode
                scene_category = acc_data.get('scene_category', '突发故障')
                if scene_category == '临时限速':
                    scene_type_code = SceneTypeCode.TEMP_SPEED_LIMIT
                elif scene_category == '区间封锁':
                    scene_type_code = SceneTypeCode.SECTION_INTERRUPT
                else:
                    scene_type_code = SceneTypeCode.SUDDEN_FAILURE
                    
                canonical_req = CanonicalDispatchRequest(
                    source_type=RequestSourceType.NATURAL_LANGUAGE,
                    raw_text=user_input,
                    scene_type_code=scene_type_code,
                    location=LocationInfo(
                        station_code=acc_data.get('location_code', ''),
                        station_name=acc_data.get('location_name', '')
                    ),
                    affected_train_ids=acc_data.get('affected_train_ids', []),
                    event_time=datetime.now().isoformat(),
                    completeness=CompletenessInfo(
                        can_enter_solver=acc_data.get('is_complete', False),
                        missing_fields=acc_data.get('missing_fields', [])
                    )
                )
                network_snapshot = snapshot_builder.build(canonical_req)
            else:
                # 创建默认的 network_snapshot
                network_snapshot = NetworkSnapshot(
                    snapshot_time=datetime.now(),
                    solving_window={"observation_corridor": "BJX-AYD", "planning_time_window": {"start": "06:00", "end": "24:00"}},
                    candidate_train_ids=[],
                    trains=[],
                    stations=[],
                    sections=[]
                )
        except Exception as e:
            logger.warning(f"构建 NetworkSnapshot 失败: {e}，使用默认值")
            network_snapshot = NetworkSnapshot(
                snapshot_time=datetime.now(),
                solving_window={"observation_corridor": "BJX-AYD", "planning_time_window": {"start": "06:00", "end": "24:00"}},
                candidate_train_ids=[],
                trains=[],
                stations=[],
                sections=[]
            )

        # 获取 accident_card 并检查有效性
        accident_card = result.get("accident_card")
        if not accident_card:
            return jsonify({
                "success": False,
                "message": "无法从输入中提取事故信息，LLM返回结果为空"
            })

        # 转换为字典格式（用于JSON序列化）
        result_dict = {
            "accident_card": accident_card.model_dump() if hasattr(accident_card, "model_dump") else accident_card,
            "network_snapshot": network_snapshot.model_dump(),
            "can_solve": accident_card.is_complete if hasattr(accident_card, "is_complete") else True,
            "missing_info": accident_card.missing_fields if hasattr(accident_card, "missing_fields") else [],
            "llm_response_type": result.get("llm_response_type", "未知")
        }

        # 检查信息是否完整
        is_complete = accident_card.is_complete if hasattr(accident_card, "is_complete") else result_dict.get("can_solve", True)
        missing_fields = accident_card.missing_fields if hasattr(accident_card, "missing_fields") else result_dict.get("missing_info", [])

        # 创建会话并保存第1层结果
        session_mgr = get_session_manager()
        session_id = session_mgr.create_session(user_input, snapshot_info)
        session_mgr.update_layer_result(session_id, 1, result_dict)

        # 获取会话状态
        status = session_mgr.get_session_status(session_id)

        # 构建响应
        response = {
            "success": True,
            "session_id": session_id,
            "current_layer": 1,
            "progress": status["progress"],
            "messages": status["messages"],
            "layer1_result": result_dict
        }

        # 如果信息不完整，返回提示信息要求补充
        if not is_complete and missing_fields:
            response["needs_more_info"] = True
            response["missing_fields"] = missing_fields
            response["message"] = f"请补充以下信息：{', '.join(missing_fields)}"
            response["can_proceed"] = False
        else:
            response["needs_more_info"] = False
            response["can_proceed"] = True
            response["message"] = "信息完整，可继续执行后续流程"

        return jsonify(response)

    except Exception as e:
        logger.exception(f"workflow_start处理异常: {str(e)}")
        return jsonify({
            "success": False,
            "message": str(e)
        })


@app.route('/api/workflow/next', methods=['POST'])
def workflow_next():
    """
    继续执行多轮对话（从当前层继续到下一层）

    请求体:
    {
        "session_id": "会话ID",
        "continue_layer": true/false  # 是否继续执行下一层
    }

    响应:
    {
        "session_id": "会话ID",
        "current_layer": 2,
        "messages": [...],
        "layer2_result": {...}
    }
    """
    try:
        data = request.json
        session_id = data.get('session_id', '')
        continue_execution = data.get('continue_layer', True)

        if not session_id:
            return jsonify({
                "success": False,
                "message": "缺少session_id"
            })

        # 获取会话
        session_mgr = get_session_manager()
        status = session_mgr.get_session_status(session_id)

        if status is None:
            return jsonify({
                "success": False,
                "message": "会话不存在"
            })

        current_layer = status["current_layer"]
        is_complete = status.get("is_complete", False)

        # 如果工作流已完成，返回完成状态
        if is_complete or current_layer >= 4:
            return jsonify({
                "success": True,
                "session_id": session_id,
                "current_layer": current_layer,
                "progress": status["progress"],
                "is_complete": True,
                "messages": status["messages"],
                "message": "工作流已完成"
            })

        # 如果用户尝试继续但L1信息不完整，则拒绝
        if current_layer == 1 and continue_execution:
            l1_result = status.get("layer1_result", {})
            can_solve = l1_result.get("can_solve", True)
            missing_info = l1_result.get("missing_info", [])
            
            if not can_solve and missing_info:
                return jsonify({
                    "success": False,
                    "message": f"信息不完整，请先补充以下信息：{', '.join(missing_info)}",
                    "needs_more_info": True,
                    "missing_fields": missing_info
                })

        # 根据当前层执行下一层
        if current_layer == 1:
            # 执行第2层
            from models.workflow_models import AccidentCard, NetworkSnapshot, DispatchContextMetadata
            from railway_agent.snapshot_builder import get_snapshot_builder
            from railway_agent.llm_workflow_engine_v2 import create_workflow_engine
            from datetime import datetime

            # 从第1层结果构建对象
            l1_result = status["layer1_result"]
            accident_card = AccidentCard(**l1_result.get("accident_card", {}))

            # 构建或获取 network_snapshot
            network_snapshot_data = l1_result.get("network_snapshot", {})
            if not network_snapshot_data or not network_snapshot_data.get("snapshot_time"):
                # 如果没有 network_snapshot，使用 SnapshotBuilder 构建一个默认的
                snapshot_builder = get_snapshot_builder()
                network_snapshot = NetworkSnapshot(
                    snapshot_time=datetime.now(),
                    solving_window={
                        "observation_corridor": "BJX-AYD",
                        "planning_time_window": {"start": "06:00", "end": "24:00"}
                    },
                    candidate_train_ids=accident_card.affected_train_ids or [],
                    excluded_train_ids=[],
                    trains=[],
                    train_count=len(accident_card.affected_train_ids) if accident_card.affected_train_ids else 0,
                    stations=[],
                    sections=[],
                    headways={},
                    current_delays={}
                )
            else:
                network_snapshot = NetworkSnapshot(**network_snapshot_data)
            dispatch_metadata = DispatchContextMetadata(
                train_count=network_snapshot.train_count,
                station_count=13,
                time_window_start="2024-01-15T10:00:00",
                time_window_end="2024-01-15T12:00:00"
            )

            # 使用新架构工作流引擎
            workflow_engine = create_workflow_engine()
            result = workflow_engine.execute_layer2(
                accident_card=accident_card,
                network_snapshot=network_snapshot,
                dispatch_metadata=dispatch_metadata
            )
            session_mgr.update_layer_result(session_id, 2, result)

            status = session_mgr.get_session_status(session_id)

            return jsonify({
                "success": True,
                "session_id": session_id,
                "current_layer": 2,
                "progress": status["progress"],
                "messages": status["messages"],
                "layer2_result": {
                    "skill_dispatch": result.get("skill_dispatch", {}),
                    "reasoning": result.get("reasoning", ""),
                    "llm_response_type": result.get("llm_response_type", "未知")
                }
            })

        elif current_layer == 2:
            # 执行第3层
            from models.workflow_models import AccidentCard, NetworkSnapshot
            from railway_agent.llm_workflow_engine_v2 import create_workflow_engine

            # 从第1层结果构建对象
            l1_result = status["layer1_result"]
            accident_card = AccidentCard(**l1_result.get("accident_card", {}))
            network_snapshot = NetworkSnapshot(**l1_result.get("network_snapshot", {}))

            # 获取数据
            trains = get_trains_pydantic()[:50]
            stations = get_stations_pydantic()

            # 使用新架构工作流引擎
            workflow_engine = create_workflow_engine()
            result = workflow_engine.execute_layer3(
                planning_intent="recalculate_corridor_schedule",
                accident_card=accident_card,
                network_snapshot=network_snapshot,
                trains=trains,
                stations=stations
            )
            # 转换为字典 (支持Pydantic模型)
            def to_dict(obj):
                if hasattr(obj, 'model_dump'):
                    return obj.model_dump()
                elif hasattr(obj, '__dict__'):
                    return obj.__dict__
                elif isinstance(obj, dict):
                    return {k: to_dict(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [to_dict(i) for i in obj]
                else:
                    return obj
            result_dict = to_dict(result)
            session_mgr.update_layer_result(session_id, 3, result_dict)

            status = session_mgr.get_session_status(session_id)

            return jsonify({
                "success": True,
                "session_id": session_id,
                "current_layer": 3,
                "progress": status["progress"],
                "messages": status["messages"],
                "layer3_result": result_dict
            })

        elif current_layer == 3:
            # 执行第4层
            from railway_agent.llm_workflow_engine_v2 import create_workflow_engine
            l3_result = status["layer3_result"]

            # 获取 skill_execution_result 和 solver_response
            skill_execution_result = l3_result.get("skill_execution_result", {}) if isinstance(l3_result, dict) else {}
            solver_response_data = l3_result.get("solver_response", {}) if isinstance(l3_result, dict) else {}

            # 构建solver_response的简化对象
            class SimpleSolverResponse:
                def __init__(self, data):
                    self.success = data.get("success", False)
                    self.total_delay_minutes = data.get("total_delay_minutes", 0)
                    self.max_delay_minutes = data.get("max_delay_minutes", 0)
                    self.adjusted_schedule = data.get("adjusted_schedule", [])
                    self.message = data.get("message", "")

                def model_dump(self):
                    return {
                        "success": self.success,
                        "total_delay_minutes": self.total_delay_minutes,
                        "max_delay_minutes": self.max_delay_minutes,
                        "adjusted_schedule": self.adjusted_schedule,
                        "message": self.message
                    }

            solver_response = SimpleSolverResponse(solver_response_data)

            # 使用新架构工作流引擎
            workflow_engine = create_workflow_engine()
            result = workflow_engine.execute_layer4(
                skill_execution_result=skill_execution_result,
                solver_response=solver_response
            )
            # 转换为字典
            result_dict = {
                "evaluation_report": result.get("evaluation_report", {}).model_dump() if hasattr(result.get("evaluation_report", {}), "model_dump") else result.get("evaluation_report", {}),
                "ranking_result": result.get("ranking_result", {}).model_dump() if hasattr(result.get("ranking_result", {}), "model_dump") else result.get("ranking_result", {}),
                "rollback_feedback": result.get("rollback_feedback", {}).model_dump() if hasattr(result.get("rollback_feedback", {}), "model_dump") else result.get("rollback_feedback", {}),
                "llm_response_type": result.get("llm_response_type", "未知")
            }
            session_mgr.update_layer_result(session_id, 4, result_dict)

            status = session_mgr.get_session_status(session_id)

            return jsonify({
                "success": True,
                "session_id": session_id,
                "current_layer": 4,
                "progress": status["progress"],
                "messages": status["messages"],
                "layer4_result": result_dict,
                "is_complete": status["is_complete"]
            })

        else:
            return jsonify({
                "success": False,
                "message": f"当前层 {current_layer} 已完成，无法继续"
            })

    except Exception as e:
        logger.exception(f"workflow_next处理异常: {str(e)}")
        return jsonify({
            "success": False,
            "message": str(e)
        })


@app.route('/api/workflow/status', methods=['GET'])
def workflow_status():
    """获取会话状态"""
    session_id = request.args.get('session_id', '')

    if not session_id:
        return jsonify({
            "success": False,
            "message": "缺少session_id"
        })

    session_mgr = get_session_manager()
    status = session_mgr.get_session_status(session_id)

    if status is None:
        return jsonify({
            "success": False,
            "message": "会话不存在"
        })

    return jsonify({
        "success": True,
        "session_id": status["session_id"],
        "current_layer": status["current_layer"],
        "progress": status["progress"],
        "is_complete": status["is_complete"],
        "messages": status["messages"]
    })


@app.route('/api/workflow/continue', methods=['POST'])
def workflow_continue():
    """
    使用补充信息继续工作流

    请求体:
    {
        "session_id": "会话ID",
        "additional_info": "用户补充的信息"
    }

    响应:
    {
        "success": true,
        "current_layer": 1,
        "progress": "执行中: 数据建模层",
        "messages": [...],
        "needs_more_info": false,
        "can_proceed": true
    }
    """
    try:
        data = request.json
        session_id = data.get('session_id', '')
        additional_info = data.get('additional_info', '')

        if not session_id:
            return jsonify({
                "success": False,
                "message": "缺少session_id"
            })

        if not additional_info:
            return jsonify({
                "success": False,
                "message": "请提供补充信息"
            })

        logger.info(f"继续工作流，会话ID: {session_id}, 补充信息: {additional_info[:50]}...")

        # 获取会话管理器
        session_mgr = get_session_manager()
        status = session_mgr.get_session_status(session_id)

        if status is None:
            return jsonify({
                "success": False,
                "message": "会话不存在或已过期"
            })

        # 导入工作流引擎
        from railway_agent.llm_workflow_engine_v2 import create_workflow_engine
        workflow_engine = create_workflow_engine()

        # 获取之前的状态
        layer1_result = status.get("layer_results", {}).get("layer1", {})
        accident_card_data = layer1_result.get("accident_card", {})

        # 合并原始输入和补充信息
        original_input = status.get("original_input", "")
        combined_input = f"{original_input} {additional_info}"

        # 重新执行L1，使用合并后的输入
        result = workflow_engine.execute_layer1(user_input=combined_input)

        accident_card = result.get("accident_card")
        if not accident_card:
            return jsonify({
                "success": False,
                "message": "无法从补充信息中提取事故信息"
            })

        # 转换为字典格式
        result_dict = {
            "accident_card": accident_card.model_dump() if hasattr(accident_card, "model_dump") else accident_card,
            "can_solve": accident_card.is_complete if hasattr(accident_card, "is_complete") else True,
            "missing_info": accident_card.missing_fields if hasattr(accident_card, "missing_fields") else [],
            "llm_response_type": result.get("llm_response_type", "未知")
        }

        # 更新会话状态
        session_mgr.update_layer_result(session_id, 1, result_dict)

        # 获取更新后的会话状态
        status = session_mgr.get_session_status(session_id)

        # 检查信息是否完整
        is_complete = accident_card.is_complete if hasattr(accident_card, "is_complete") else True
        missing_fields = accident_card.missing_fields if hasattr(accident_card, "missing_fields") else []

        # 构建响应
        response = {
            "success": True,
            "session_id": session_id,
            "current_layer": 1,
            "progress": status["progress"],
            "messages": status["messages"],
            "layer1_result": result_dict
        }

        if not is_complete and missing_fields:
            response["needs_more_info"] = True
            response["missing_fields"] = missing_fields
            response["message"] = f"信息仍不完整，请继续补充：{', '.join(missing_fields)}"
            response["can_proceed"] = False
        else:
            response["needs_more_info"] = False
            response["can_proceed"] = True
            response["message"] = "信息已完整，可继续执行后续流程"

        return jsonify(response)

    except Exception as e:
        logger.exception(f"workflow_continue处理异常: {str(e)}")
        return jsonify({
            "success": False,
            "message": str(e)
        })


@app.route('/api/workflow/reset', methods=['POST'])
def workflow_reset():
    """重置/删除会话"""
    data = request.json
    session_id = data.get('session_id', '')

    if not session_id:
        return jsonify({
            "success": False,
            "message": "缺少session_id"
        })

    session_mgr = get_session_manager()
    deleted = session_mgr.delete_session(session_id)

    return jsonify({
        "success": deleted,
        "message": "会话已删除" if deleted else "会话不存在"
    })


@app.route('/api/preprocess_debug', methods=['POST'])
def preprocess_debug():
    """
    预处理调试API（已简化，直接调用L1层）
    返回完整的数据建模过程信息，用于调试
    """
    try:
        data = request.json
        raw_input = data.get('input', '')

        if not raw_input:
            return jsonify({
                "success": False,
                "message": "请提供输入内容"
            })

        logger.info(f"收到preprocess_debug请求: {raw_input[:50]}...")

        # 直接调用L1层进行数据建模
        from railway_agent.workflow import Layer1DataModeling
        l1 = Layer1DataModeling()
        l1_result = l1.execute(user_input=raw_input, enable_rag=True)
        
        accident_card = l1_result.get("accident_card", {})
        
        return jsonify({
            "success": True,
            "request_id": "debug_" + str(hash(raw_input)),
            "raw_user_request": {"input": raw_input},
            "accident_card": accident_card.model_dump() if hasattr(accident_card, 'model_dump') else accident_card,
            "response_source": l1_result.get("response_source", "unknown"),
            "needs_more_info": l1_result.get("needs_more_info", False),
            "missing_questions": l1_result.get("missing_questions", []),
            "message": "L1数据建模完成"
        })

    except Exception as e:
        logger.exception(f"preprocess_debug处理异常: {str(e)}")
        return jsonify({
            "success": False,
            "message": str(e)
        })


def check_api_connectivity():
    """检查API连通性"""
    try:
        from railway_agent.adapters.llm_adapter import get_llm_caller
        llm = get_llm_caller()
        # 尝试一个简单的API调用
        test_response = llm.call("测试", max_tokens=10, temperature=0.1)
        logger.info("[API连通性检查] DashScope API连接正常")
        return True
    except Exception as e:
        logger.error(f"[API连通性检查] 失败: {e}")
        return False


if __name__ == '__main__':
    # 避免Flask reloader导致的重复启动
    import os
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        logger.info("=" * 50)
        logger.info("铁路调度Agent系统 v1.0")
        logger.info("=" * 50)
        logger.info(f"Agent模式: {AGENT_MODE}")
        if AGENT_MODE == "rule":
            logger.info("使用固定规则Agent，无需加载大模型")
        elif AGENT_MODE == "qwen":
            from config import LLM_CONFIG
            logger.info(f"使用阿里云DashScope API，模型: {LLM_CONFIG['model']}")
        else:
            logger.info("自动模式：优先Qwen Agent，失败则回退到RuleAgent")
        logger.info("访问地址: http://localhost:8081")
        logger.info("按 Ctrl+C 停止服务")
        logger.info("=" * 50)
        
        # 检查API连通性
        logger.info("正在检查API连通性...")
        if not check_api_connectivity():
            logger.error("API连通性检查失败，服务可能无法正常工作")
            logger.error("请检查:")
            logger.error("1. DASHSCOPE_API_KEY 是否正确设置")
            logger.error("2. 网络连接是否正常")
            logger.error("3. 阿里云DashScope服务是否可用")
    # 关闭debug模式以避免重复启动，但保留自动重载功能
    app.run(host='0.0.0.0', port=8081, debug=False)
