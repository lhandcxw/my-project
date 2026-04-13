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

# 导入统一LLM驱动 Agent
from railway_agent import LLMAgent, create_llm_agent, ToolRegistry

# 导入比较模块蓝图并注册
from scheduler_comparison.comparison_api import register_comparison_routes

# Agent实例
def get_llm_agent():
    """
    获取LLM驱动 Agent实例

    统一LLM驱动架构：
    - 移除RuleAgent
    - 支持阿里云API和本地微调模型
    - 使用完整L1-L4工作流
    """
    from railway_agent import create_llm_agent
    return create_llm_agent(trains=trains, stations=stations)

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
from config import AppConfig, LLMConfig, DispatchEnvConfig, get_config_summary, validate_config

# 验证配置（失败时明确报错并终止）
validate_config()

# 设置环境变量（统一配置中心已定义，这里确保生效）
os.environ['DASHSCOPE_API_KEY'] = LLMConfig.DASHSCOPE_API_KEY
os.environ['DASHSCOPE_MODEL'] = LLMConfig.DASHSCOPE_MODEL
os.environ['LLM_PROVIDER'] = LLMConfig.PROVIDER

# 导出常用配置
AGENT_MODE = AppConfig.AGENT_MODE

# 打印配置摘要
logger.info(get_config_summary())

# Agent实例
llm_agent = None

def get_agent():
    """
    获取或创建LLM驱动 Agent实例

    统一LLM驱动架构说明：
    - 完全基于LLM驱动，不依赖规则
    - 支持阿里云API和本地微调模型
    - 使用完整L1-L4工作流
    - 多轮对话和单轮对话都能正常工作
    """
    global llm_agent

    if llm_agent is not None:
        return llm_agent

    # 创建LLM驱动Agent
    logger.info(f"初始化LLM驱动Agent，模式: {AGENT_MODE}")
    llm_agent = create_llm_agent(trains=trains, stations=stations)
    logger.info("新架构Agent初始化完成（支持单轮对话和多轮对话）")
    return llm_agent


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

        # 使用LLM驱动Agent的分析功能
        agent = get_agent()
        if agent:
            # 使用LLM驱动 Agent
            result = agent.analyze(delay_injection.model_dump())
            logger.info(f"Agent分析结果: success={result.success}, dispatch_result={result.dispatch_result is not None}")

            if result.success and result.dispatch_result:
                skill_result = result.dispatch_result
                logger.info(f"Skill结果: message={skill_result.message}, optimized_schedule keys={list(skill_result.optimized_schedule.keys()) if skill_result.optimized_schedule else 'None'}")

                # 映射场景类型到 skill
                skill_mapping = {
                    "temporary_speed_limit": "temporary_speed_limit_skill",
                    "sudden_failure": "sudden_failure_skill",
                    "section_interrupt": "section_interrupt_skill"
                }
                selected_skill = skill_mapping.get(result.recognized_scenario, "unknown")

                return jsonify({
                    "success": True,
                    "planner": {
                        "recognized_scenario": result.recognized_scenario,
                        "selected_skill": selected_skill,  # 新增：返回正式 skill 字段
                        "selected_solver": result.selected_solver,  # 新增：返回求解器字段
                        "delay_level": "0",
                        "confidence": 0.9
                    },
                    "skill_result": {
                        "message": skill_result.message,
                        "optimized_schedule": skill_result.optimized_schedule,
                        "delay_statistics": skill_result.delay_statistics,
                        "computation_time": skill_result.computation_time,
                        "selected_skill": selected_skill  # 新增：skill_result 也包含
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

    支持统一LLM驱动架构：
    - 完全基于LLM驱动，不依赖规则
    - 支持阿里云API和本地微调模型
    - 使用完整L1-L4工作流
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

        # 获取LLM驱动Agent
        agent = get_agent()
        if agent is None:
            logger.error("Agent未初始化")
            return jsonify({
                "success": False,
                "message": "Agent未初始化，请检查配置"
            })

        # 解析用户输入，构建DelayInjection
        delay_injection = parse_user_prompt(prompt)
        logger.info(f"解析后的延误注入: {delay_injection.get('scenario_type')}, 列车: {delay_injection.get('affected_trains')}")

        # 检查关键信息是否缺失
        missing_info = []
        if not delay_injection.get("affected_trains"):
            missing_info.append("列车号（如：G1563）")
        if not delay_injection.get("injected_delays"):
            missing_info.append("延误时间（如：延误10分钟）")
        if missing_info:
            logger.warning(f"用户输入缺少必要信息: {missing_info}")
            return jsonify({
                "success": False,
                "message": f"请提供以下信息：{', '.join(missing_info)}"
            })

        # 调用Agent分析（LLM驱动，传入原始prompt）
        result = agent.analyze(delay_injection, user_prompt=prompt)

        if result.success and result.dispatch_result:
            dispatch = result.dispatch_result

            # 获取原始时刻表
            original_schedule = get_original_schedule()

            logger.info("Agent分析成功，返回结果")
            return jsonify({
                "success": True,
                "recognized_scenario": result.recognized_scenario,
                "selected_skill": result.selected_skill,
                "selected_solver": result.selected_solver,
                "reasoning": result.reasoning,
                "llm_summary": result.llm_summary or "",
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

        # 获取LLM驱动Agent
        agent = get_agent()
        if agent is None:
            return jsonify({
                "success": False,
                "message": "LLM Agent未初始化"
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

    # 如果没有提取到关键信息，不再使用默认值，而是设置为空
    # 这样后续流程可以检测到缺失并提示用户
    if not train_ids:
        train_ids = []  # 不再使用['G1001']作为默认值
    if not delays:
        delays = []  # 不再使用['600']作为默认值

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

    # 创建反向映射（代码到名称）
    code_to_station = {code: name for name, code in station_name_to_code.items()}

    # 尝试提取区间（使用正则匹配区间格式）
    # 匹配格式：A到B、A至B、A-B、A和B、A与B等，以及站码格式
    detected_section_id = None
    detected_station_code = None
    location_type = "station"  # 默认为车站

    section_patterns = [
        # 站码区间：XSD-BDD、XSD-BDD区间、XSD到BDD
        r'([A-Z]{3})[－\-至到]\s*([A-Z]{3})(?:区间|段)?',
        # 中文车站名：石家庄到保定东
        r'([^与和－\-]{2,})[－\-到至]\s*([^与和－\-]{2,})(?:站|线路所|区间|段)?',
        # 中文车站名（之间）：涿州东与高碑店东之间
        r'([^与和－\-]{2,})[与和]\s*([^与和－\-]{2,})(?:站|线路所)?(?:之间)?',
    ]

    for pattern in section_patterns:
        section_match = re.search(pattern, prompt)
        if section_match:
            station1 = section_match.group(1).strip()
            station2 = section_match.group(2).strip()

            # 查找两个站点的代码和名称
            code1, code2 = None, None
            name1, name2 = None, None

            # 优先匹配站码
            if station1 in code_to_station:
                code1 = station1
                name1 = code_to_station[station1]
            else:
                # 匹配中文名称（精确匹配）
                for station_name, code in station_name_to_code.items():
                    if station1 == station_name or station_name in station1:
                        code1 = code
                        name1 = station_name
                        break

            if station2 in code_to_station:
                code2 = station2
                name2 = code_to_station[station2]
            else:
                # 匹配中文名称（精确匹配）
                for station_name, code in station_name_to_code.items():
                    if station2 == station_name or station_name in station2:
                        code2 = code
                        name2 = station_name
                        break

            # 如果找到两个站点，构建区间ID
            if code1 and code2:
                detected_section_id = f"{code1}-{code2}"
                detected_station_code = code1  # 使用第一个站作为车站代码
                location_type = "section"
                logger.info(f"[parse_user_prompt] 提取到区间: {detected_section_id} ({name1}-{name2})")
                break

    # 如果没有提取到区间，尝试提取单个车站
    if not detected_section_id:
        for name, code in station_name_to_code.items():
            if name in prompt:
                detected_station_code = code
                location_type = "station"
                logger.info(f"[parse_user_prompt] 提取到车站: {code} ({name})")
                break

    # 如果没有检测到车站，设置为None，让后续流程处理
    if detected_station_code is None:
        detected_station_code = None  # 不再使用"BJX"作为默认值

    # 构建DelayInjection
    injected_delays = []
    for i, train_id in enumerate(train_ids):
        # 如果没有延误信息，设置为None，让后续流程处理
        delay_seconds = int(delays[i]) * 60 if i < len(delays) and delays[i] else None

        # 验证列车是否停靠在选定的车站
        # 如果不停靠，使用列车的第一个停靠站
        train = None
        for t in trains:
            if t.train_id == train_id:
                train = t
                break

        actual_station_code = detected_station_code

        # 如果没有检测到车站代码，尝试从列车的时刻表中获取
        if not actual_station_code:
            if train and train.schedule and train.schedule.stops and len(train.schedule.stops) > 0:
                # 使用列车的第一个停靠站
                first_stop = train.schedule.stops[0]
                if hasattr(first_stop, 'station_code'):
                    actual_station_code = first_stop.station_code
                    logger.info(f"未检测到车站代码，使用列车 {train_id} 的第一个停靠站: {actual_station_code}")

        # 如果有检测到的车站代码，检查列车是否停靠
        if actual_station_code and train and train.schedule:
            # 检查列车是否停靠在选定车站（增强安全性检查）
            train_stations = []
            if train.schedule.stops and isinstance(train.schedule.stops, (list, tuple)):
                train_stations = [stop.station_code for stop in train.schedule.stops if hasattr(stop, 'station_code')]
            if detected_station_code and detected_station_code not in train_stations and train_stations:
                # 使用列车的第一个停靠站
                first_stop = train.schedule.stops[0]
                if hasattr(first_stop, 'station_code'):
                    actual_station_code = first_stop.station_code
                    logger.warning(f"列车 {train_id} 不停靠在 {detected_station_code}，使用 {actual_station_code} 作为延误车站")
        elif not train:
            logger.warning(f"列车 {train_id} 不在列车列表中，使用检测的车站 {actual_station_code}")
        elif not train.schedule:
            logger.warning(f"列车 {train_id} 没有时刻表信息，使用检测的车站 {actual_station_code}")

        # 只有当delay_seconds不为None时才添加延误
        if delay_seconds is not None and actual_station_code is not None:
            # 根据位置类型构建location对象
            if location_type == "section" and detected_section_id:
                location_obj = {"location_type": "section", "section_id": detected_section_id}
            else:
                location_obj = {"location_type": "station", "station_code": actual_station_code}
            injected_delays.append({
                "train_id": train_id,
                "location": location_obj,
                "initial_delay_seconds": delay_seconds,
                "timestamp": "2024-01-15T10:00:00Z"
            })

    # 构建完整的delay_injection
    if scenario_type == 'temporary_speed_limit':
        # 如果没有车站代码，提供默认值但不影响关键功能
        station_code_for_section = detected_station_code if detected_station_code else "UNKNOWN"

        scenario_params = {}
        # 只有当检测到车站时，才设置scenario_params
        if detected_station_code:
            scenario_params = {
                "limit_speed_kmh": DispatchEnvConfig.scenario_temporary_speed_limit_default_speed(),
                "duration_minutes": DispatchEnvConfig.scenario_temporary_speed_limit_default_duration(),
                "affected_section": f"{station_code_for_section} -> {station_code_for_section}"
            }
        else:
            # 没有车站信息时，只设置必要的参数
            scenario_params = {
                "limit_speed_kmh": DispatchEnvConfig.scenario_temporary_speed_limit_default_speed(),
                "duration_minutes": DispatchEnvConfig.scenario_temporary_speed_limit_default_duration()
            }

        return {
            "scenario_type": scenario_type,
            "scenario_id": "AGENT_CHAT_001",
            "injected_delays": injected_delays,
            "affected_trains": train_ids,
            "scenario_params": scenario_params
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

        # 获取LLM驱动Agent
        agent = get_agent()
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
        result = agent.analyze_with_comparison(delay_injection, user_prompt=prompt, comparison_criteria=comparison_criteria)

        if result.success and result.dispatch_result:
            dispatch = result.dispatch_result
            original_schedule = get_original_schedule()

            logger.info("Agent比较分析成功，返回结果")
            return jsonify({
                "success": True,
                "recognized_scenario": result.recognized_scenario,
                "selected_skill": result.selected_skill,
                "selected_solver": result.selected_solver,
                "reasoning": result.reasoning,
                "delay_statistics": dispatch.delay_statistics,
                "message": dispatch.message,
                "computation_time": result.computation_time,
                "optimized_schedule": dispatch.optimized_schedule,
                "original_schedule": original_schedule,
                "ranking": dispatch.delay_statistics.get("ranking", []),
                "comparison_details": dispatch.delay_statistics.get("ranking", [])
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
        from config import DispatchEnvConfig
        delay_seconds = data.get('delay_seconds', DispatchEnvConfig.default_delay_seconds())
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
        agent = get_agent()
        if agent is None:
            return jsonify({
                "success": False,
                "message": "Agent未初始化"
            })

        result = agent.analyze_with_comparison(
            delay_injection.model_dump(),
            user_prompt=f"{train_id}在{station_code}延误{delay_seconds // 60}分钟",
            comparison_criteria=criteria
        )
        
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

        # 使用与智能调度相同的L1数据建模层（仅执行第一层）
        from railway_agent.llm_workflow_engine_v2 import create_workflow_engine

        workflow_engine = create_workflow_engine()
        l1_result = workflow_engine.execute_layer1(
            user_input=user_input,
            canonical_request=None,
            enable_rag=True
        )

        accident_card = l1_result.get("accident_card", {})
        if hasattr(accident_card, 'model_dump'):
            accident_card_data = accident_card.model_dump()
        else:
            accident_card_data = accident_card

        # 转换为字典格式（用于JSON序列化）
        result_dict = {
            "accident_card": accident_card_data,
            "can_solve": accident_card_data.get("is_complete", True),
            "missing_info": accident_card_data.get("missing_fields", []),
            "llm_response_type": "llm_real"
        }

        # 检查信息是否完整
        is_complete = accident_card_data.get("is_complete", True)
        missing_fields = accident_card_data.get("missing_fields", [])

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
            from models.workflow_models import AccidentCard
            from railway_agent.llm_workflow_engine_v2 import create_workflow_engine

            # 从第1层结果构建对象
            l1_result = status["layer1_result"]
            accident_card = AccidentCard(**l1_result.get("accident_card", {}))

            # 使用新架构工作流引擎
            workflow_engine = create_workflow_engine()
            result = workflow_engine.execute_layer2(
                accident_card=accident_card
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
            from models.workflow_models import AccidentCard
            from railway_agent.llm_workflow_engine_v2 import create_workflow_engine

            # 从第1层结果构建对象
            l1_result = status["layer1_result"]
            accident_card = AccidentCard(**l1_result.get("accident_card", {}))

            # 获取数据（使用完整时刻表）
            trains = get_trains_pydantic()[:50]
            stations = get_stations_pydantic()

            # 使用新架构工作流引擎
            workflow_engine = create_workflow_engine()
            result = workflow_engine.execute_layer3(
                planning_intent="recalculate_corridor_schedule",
                accident_card=accident_card,
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

            # 获取所有层的结果用于前端显示
            all_results = {
                "layer1_result": status.get("layer1_result", {}),
                "layer2_result": status.get("layer2_result", {}),
                "layer3_result": status.get("layer3_result", {}),
                "layer4_result": result_dict
            }

            return jsonify({
                "success": True,
                "session_id": session_id,
                "current_layer": 4,
                "progress": status["progress"],
                "messages": status["messages"],
                "layer4_result": result_dict,
                "is_complete": status["is_complete"],
                "all_layer_results": all_results  # 返回所有层结果供前端使用
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
        
        # 检查会话状态是否有效
        if status is None:
            return jsonify({
                "success": False,
                "message": "会话不存在或已过期"
            })

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
        elif AGENT_MODE == "dashscope":
            from config import LLMConfig
            logger.info(f"使用阿里云DashScope API，模型: {LLMConfig.DASHSCOPE_MODEL}")
        elif AGENT_MODE == "local":
            from config import LLMConfig
            logger.info(f"使用本地微调模型，路径: {LLMConfig.TRANSFORMERS_MODEL_PATH or LLMConfig.OLLAMA_MODEL}")
        else:
            logger.info("自动模式：优先本地模型，失败则用API")
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
