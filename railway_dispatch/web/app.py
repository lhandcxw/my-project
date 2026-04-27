# -*- coding: utf-8 -*-
"""
铁路调度系统 - Web后端 (Flask)
降低环境配置难度
"""
import os
os.environ["RULE_AGENT_USE_WORKFLOW"] = "1"

from typing import Dict, Any

from flask import Flask, render_template, request, jsonify, Response, redirect, url_for
from flask_cors import CORS
import json
import base64
import logging
from datetime import datetime

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.data_models import Train, Station, DelayInjection, ScenarioType, InjectedDelay, DelayLocation
from models.data_loader import get_trains_pydantic, get_stations_pydantic, get_station_codes, get_station_names, get_train_ids, use_real_data, is_using_real_data
from solver.mip_scheduler import MIPScheduler
from scheduler_comparison.comparator import ComparisonCriteria
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

# 导入意图路由（新增：支持query/chat/dispatch多分支）


# 导入RAG检索器（新增：chat分支知识增强）
from railway_agent.rag_retriever import get_retriever

# 导入比较模块蓝图并注册
from scheduler_comparison.comparison_api import register_comparison_routes

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
# evaluator = Evaluator()  # 已弃用：评估功能已整合到Layer4Evaluation

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

# 短期对话记忆（多轮对话上下文）
# 格式: {session_id: {"entities": {"train_id": "G1563", "station_name": "石家庄"}, "last_intent": "query", "timestamp": ...}}
_chat_memory: Dict[str, Dict[str, Any]] = {}


def _get_chat_context(session_id: str) -> Dict[str, Any]:
    """获取指定会话的上下文记忆"""
    if not session_id or session_id not in _chat_memory:
        return {}
    return _chat_memory.get(session_id, {})


def _get_chat_messages(session_id: str, max_turns: int = 0) -> list:
    """
    获取指定会话的对话历史消息

    Args:
        session_id: 会话ID
        max_turns: 最大轮数，0表示返回全部

    Returns:
        list: OpenAI格式的消息列表 [{"role": "user", "content": "..."}, ...]
    """
    if not session_id or session_id not in _chat_memory:
        return []
    messages = _chat_memory[session_id].get("messages", [])
    if max_turns > 0:
        # 每轮 = user + assistant = 2条消息
        return messages[-max_turns * 2:]
    return messages


def _save_chat_context(session_id: str, entities: Dict[str, Any], intent: str = "", user_msg: str = "", assistant_msg: str = ""):
    """保存会话上下文记忆"""
    if not session_id:
        return

    # 读取现有记忆
    existing = _chat_memory.get(session_id, {})
    messages = existing.get("messages", [])

    # 追加当前轮对话
    if user_msg:
        messages.append({"role": "user", "content": user_msg})
    if assistant_msg:
        messages.append({"role": "assistant", "content": assistant_msg})

    # 限制历史长度：最多保留20轮（40条消息），防止token超限
    if len(messages) > 40:
        messages = messages[-40:]

    _chat_memory[session_id] = {
        "entities": entities,
        "last_intent": intent,
        "messages": messages,
        "timestamp": datetime.now().isoformat()
    }
    # 清理超过100条的旧记忆
    if len(_chat_memory) > 100:
        oldest = sorted(_chat_memory.items(), key=lambda x: x[1].get("timestamp", ""))[0][0]
        _chat_memory.pop(oldest, None)


def _append_assistant_message(session_id: str, assistant_msg: str):
    """单独追加assistant回复到对话历史（用于在yield之后保存）"""
    if not session_id or session_id not in _chat_memory:
        return
    _chat_memory[session_id].setdefault("messages", [])
    _chat_memory[session_id]["messages"].append({"role": "assistant", "content": assistant_msg})
    # 限制长度
    if len(_chat_memory[session_id]["messages"]) > 40:
        _chat_memory[session_id]["messages"] = _chat_memory[session_id]["messages"][-40:]

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


def compute_dispatcher_metrics(original_schedule, optimized_schedule):
    """
    计算调度员关心的现实场景指标
    """
    metrics = {}
    total_trains = len(optimized_schedule) if optimized_schedule else 0

    # 1. 终点站准点率：列车在终点站延误延误<5分钟的比例
    terminal_on_time = 0
    for train_id, stops in (optimized_schedule or {}).items():
        if stops:
            last_delay = stops[-1].get("delay_seconds", 0)
            if last_delay < 300:
                terminal_on_time += 1
    metrics["terminal_on_time_rate"] = round(terminal_on_time / total_trains, 3) if total_trains > 0 else 1.0

    # 2. 调整车次比例：有多少列车被调整了（存在任意延误>0）
    adjusted_trains = 0
    for train_id, stops in (optimized_schedule or {}).items():
        if any(s.get("delay_seconds", 0) > 0 for s in stops):
            adjusted_trains += 1
    metrics["adjustment_ratio"] = round(adjusted_trains / total_trains, 3) if total_trains > 0 else 0.0

    # 3. 车站最大压力：单一车站同时出现延误的列车数最大值
    station_delays = {}
    for train_id, stops in (optimized_schedule or {}).items():
        for stop in stops:
            if stop.get("delay_seconds", 0) > 0:
                sc = stop.get("station_code", "UNKNOWN")
                station_delays[sc] = station_delays.get(sc, 0) + 1
    if station_delays:
        max_pressure_station = max(station_delays, key=station_delays.get)
        metrics["station_pressure_max"] = station_delays[max_pressure_station]
        metrics["station_pressure_max_name"] = max_pressure_station
    else:
        metrics["station_pressure_max"] = 0
        metrics["station_pressure_max_name"] = "-"

    # 4. 延误恢复率：有多少受影响列车在运行过程中恢复了部分延误（终点延误 < 首次延误）
    recovery_count = 0
    affected_count = 0
    for train_id, stops in (optimized_schedule or {}).items():
        delays = [s.get("delay_seconds", 0) for s in stops]
        if any(d > 0 for d in delays):
            affected_count += 1
            first_delay = next((d for d in delays if d > 0), 0)
            last_delay = delays[-1] if delays else 0
            if last_delay < first_delay:
                recovery_count += 1
    metrics["delay_recovery_rate"] = round(recovery_count / affected_count, 3) if affected_count > 0 else 1.0

    # 5. 延误集中指数（延误标准差/平均延误）- 反映延误分布均衡性
    train_max_delays = []
    for train_id, stops in (optimized_schedule or {}).items():
        max_d = max((s.get("delay_seconds", 0) for s in stops), default=0)
        train_max_delays.append(max_d)
    if train_max_delays:
        avg_d = sum(train_max_delays) / len(train_max_delays)
        if len(train_max_delays) > 1 and avg_d > 0:
            variance = sum((d - avg_d) ** 2 for d in train_max_delays) / len(train_max_delays)
            std_dev = variance ** 0.5
            metrics["delay_concentration_index"] = round(std_dev / avg_d, 2)
        else:
            metrics["delay_concentration_index"] = 0.0
    else:
        metrics["delay_concentration_index"] = 0.0

    return metrics


@app.route('/')
def index():
    return render_template(
        'index.html',
        train_ids=train_ids,
        station_codes=station_codes,
        station_names=station_names
    )


@app.route('/v2')
def index_v2():
    """【统一】v2界面已合并到主入口，重定向到 /"""
    return redirect(url_for('index'))



@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查端点，供前端检测后端服务状态"""
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'data_loaded': is_using_real_data(),
        'trains_count': len(trains),
        'stations_count': len(stations)
    })


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

                # 所有场景统一使用通用求解技能
                selected_skill = "dispatch_solve_skill"

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

        # 兜底：直接执行通用求解Skill
        skill_name = "dispatch_solve_skill"
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
    【性能优化】只绘制有延误变化的列车，大幅提升渲染速度
    """
    import time
    try:
        data = request.json

        original_schedule = data.get('original_schedule', {})
        optimized_schedule = data.get('optimized_schedule', {})

        # 【性能优化】识别有变化的列车ID，只渲染这些列车
        highlight_train_ids = data.get('highlight_train_ids', None)
        if highlight_train_ids is None:
            # 自动计算：找出优化后有时刻变化的列车
            highlight_train_ids = []
            for train_id, opt_stops in optimized_schedule.items():
                orig_stops = original_schedule.get(train_id, [])
                has_change = False
                for i, opt in enumerate(opt_stops):
                    orig = orig_stops[i] if i < len(orig_stops) else {}
                    if (opt.get("arrival_time") != orig.get("arrival_time") or
                        opt.get("departure_time") != orig.get("departure_time") or
                        opt.get("delay_seconds", 0) > 0):
                        has_change = True
                        break
                if has_change:
                    highlight_train_ids.append(train_id)
            # 如果变化列车太多，只取前30列（保证性能）
            if len(highlight_train_ids) > 30:
                highlight_train_ids = highlight_train_ids[:30]

        # 转换为 railway_diagram.py 需要的格式
        def convert_schedule(schedule_dict, train_ids_filter=None):
            """将时刻表转换为列车列表格式，支持过滤"""
            trains_list = []
            for train_id, stops in schedule_dict.items():
                if train_ids_filter and train_id not in train_ids_filter:
                    continue
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

        original_trains = convert_schedule(original_schedule, highlight_train_ids)
        optimized_trains = convert_schedule(optimized_schedule, highlight_train_ids)

        # 生成对比图（横轴时间，纵轴车站）
        t0 = time.time()
        img_base64 = create_comparison_diagram(
            original_trains,
            optimized_trains,
            "Railway Train Diagram",
            highlight_train_ids=highlight_train_ids
        )
        render_time = time.time() - t0
        logger.info(f"[运行图生成] 耗时: {render_time:.2f}秒, 绘制列车数: {len(highlight_train_ids)}")

        return jsonify({
            "success": True,
            "diagram_image": img_base64,
            "render_time": round(render_time, 2),
            "trains_drawn": len(highlight_train_ids)
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

        # 统一LLM驱动架构：实体提取完全由L1层LLM完成，不使用规则解析
        # 直接传递原始prompt给Agent，由工作流引擎内部处理
        logger.info("使用LLM驱动工作流进行实体提取和场景识别（无规则解析）")

        # UAO-RD: 统一调用全局Agent入口
        result = agent.handle(user_input=prompt)

        if not result.get("success"):
            logger.error(f"Agent处理失败: {result.get('message')}")
            return jsonify({
                "success": False,
                "message": result.get("message", "Agent执行失败")
            })

        mode = result.get("mode", "heavy")
        if mode == "light":
            return jsonify({
                "success": True,
                "mode": "light",
                "content": result.get("content", ""),
                "message": result.get("content", ""),
                "computation_time": result.get("computation_time", 0)
            })

        # Heavy Mode（调度求解）
        accident_card = result.get("accident_card", {})
        evaluation_report = result.get("evaluation_report", {})
        if hasattr(evaluation_report, 'model_dump'):
            evaluation_report = evaluation_report.model_dump()
        elif hasattr(evaluation_report, '__dict__'):
            evaluation_report = evaluation_report.__dict__

        response_data = {
            "success": True,
            "mode": "heavy",
            "recognized_scenario": accident_card.get("scene_category", "unknown") if isinstance(accident_card, dict) else getattr(accident_card, "scene_category", "unknown"),
            "selected_skill": "dispatch_solve_skill",
            "reasoning": result.get("reasoning", ""),
            "llm_summary": result.get("natural_language_plan", ""),
            "delay_statistics": result.get("dispatch_metrics", {}),
            "message": result.get("message", ""),
            "computation_time": result.get("computation_time", 0),
            "optimized_schedule": {},
            "original_schedule": get_original_schedule(),
            "evaluation_report": evaluation_report
        }

        operations_guide = result.get("operations_guide")
        if operations_guide:
            response_data["operations_guide"] = operations_guide

        return jsonify(response_data)

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
        general_prompt = f"""你是京广高铁智能调度系统的专业顾问。请基于铁路运输专业知识，准确、清晰地回答用户问题。

用户问题: {prompt}

回答要求：
- 专业严谨，体现技术权威性
- 涉及技术术语时，给出准确的专业解释
- 对无法确认的内容，明确说明"该信息暂无法核实"，禁止编造"""

        # UAO-RD: 统一调用全局Agent入口
        result = agent.handle(user_input=prompt)

        if not result.get("success"):
            return jsonify({
                "success": False,
                "message": result.get("message", "处理失败")
            })

        mode = result.get("mode", "light")
        if mode == "light":
            return jsonify({
                "success": True,
                "mode": "light",
                "response": result.get("content", "")
            })

        # Heavy Mode 不应在 general_chat 中出现，但做兜底
        return jsonify({
            "success": True,
            "mode": mode,
            "response": result.get("message", result.get("content", ""))
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": str(e)
        })


@app.route('/api/agent_chat_stream', methods=['POST'])
def agent_chat_stream():
    """
    流式Agent对话API - 支持实时反馈

    返回Server-Sent Events (SSE)格式的流式响应
    事件类型：
    - start: 开始处理
    - thinking: Agent思考过程
    - progress: 工作流进度（L1-L4）
    - result: 最终结果
    - error: 错误信息
    """
    import time
    from datetime import datetime

    try:
        data = request.json
        prompt = data.get('prompt', '')
        session_id = data.get('session_id', '')

        if not prompt:
            def error_gen():
                yield f"data: {json.dumps({'type': 'error', 'message': '请输入您的问题'}, ensure_ascii=False)}\n\n"
            return Response(error_gen(), mimetype='text/event-stream')

        logger.info(f"[流式API] 收到请求，session_id={session_id}, prompt: {prompt[:50]}...")

        def generate():
            try:
                yield f"data: {json.dumps({'type': 'start', 'timestamp': datetime.now().isoformat()}, ensure_ascii=False)}\n\n"

                agent = get_agent()
                if agent is None:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Agent未初始化，请检查配置'}, ensure_ascii=False)}\n\n"
                    return

                total_start = time.time()
                stage_times = {}

                context = _get_chat_context(session_id)
                if context:
                    logger.info(f"[对话记忆] 恢复上下文: {context.get('entities', {})}")

                # UAO-RD: 统一调用全局Agent入口
                yield f"data: {json.dumps({'type': 'thinking', 'content': '正在分析用户意图...'}, ensure_ascii=False)}\n\n"

                result = agent.handle(user_input=prompt, session_history=context.get("messages") if context else None)

                if not result.get("success"):
                    yield f"data: {json.dumps({'type': 'error', 'message': result.get('message', '处理失败')}, ensure_ascii=False)}\n\n"
                    return

                mode = result.get("mode", "heavy")

                if mode == "light":
                    yield f"data: {json.dumps({'type': 'thinking', 'content': '正在查询相关信息...'}, ensure_ascii=False)}\n\n"
                    tool_calls = result.get("tool_calls", [])
                    if tool_calls:
                        tool_call_str = ", ".join(tool_calls)
                        yield f"data: {json.dumps({'type': 'thinking', 'content': f'已调用工具: {tool_call_str}'}, ensure_ascii=False)}\n\n"
                    content = result.get("content", "")
                    if content:
                        yield f"data: {json.dumps({'type': 'thinking', 'content': content}, ensure_ascii=False)}\n\n"
                    light_result = {
                        "success": True,
                        "mode": "light",
                        "ui_action": result.get("ui_action", "render_chat"),
                        "chat_message": content,
                        "response": content,
                        "computation_time": result.get("computation_time", 0)
                    }
                    yield f"data: {json.dumps({'type': 'result', 'data': light_result}, ensure_ascii=False)}\n\n"
                    _save_chat_context(session_id, {}, "query", user_msg=prompt, assistant_msg=content)
                    return

                # Heavy Mode: 基于Agent返回结果重构L1-L4进度事件
                accident_card = result.get("accident_card", {})

                # L1
                t0 = time.time()
                yield f"data: {json.dumps({'type': 'thinking', 'content': '正在分析调度场景...'}, ensure_ascii=False)}\n\n"
                if accident_card:
                    scene_cat = accident_card.get("scene_category", "未知") if isinstance(accident_card, dict) else getattr(accident_card, "scene_category", "未知")
                    fault_type = accident_card.get("fault_type", "未知") if isinstance(accident_card, dict) else getattr(accident_card, "fault_type", "未知")
                    loc_name = accident_card.get("location_name", "未知") if isinstance(accident_card, dict) else getattr(accident_card, "location_name", "未知")
                    loc_code = accident_card.get("location_code", "未知") if isinstance(accident_card, dict) else getattr(accident_card, "location_code", "未知")
                    affected = accident_card.get("affected_train_ids", []) if isinstance(accident_card, dict) else getattr(accident_card, "affected_train_ids", [])
                    yield f"data: {json.dumps({'type': 'thinking', 'content': f'场景识别：{scene_cat} - {fault_type}'}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'thinking', 'content': f'位置：{loc_name} ({loc_code})'}, ensure_ascii=False)}\n\n"
                    train_count = len(affected)
                    yield f"data: {json.dumps({'type': 'thinking', 'content': f'受影响列车：{train_count}列'}, ensure_ascii=False)}\n\n"
                    if affected:
                        affected_str = ", ".join(affected[:10])
                        yield f"data: {json.dumps({'type': 'thinking', 'content': f'   车次：{affected_str}'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'progress', 'layer': 1, 'message': '数据建模完成'}, ensure_ascii=False)}\n\n"
                stage_times['L1数据建模'] = time.time() - t0

                # L2
                t0 = time.time()
                yield f"data: {json.dumps({'type': 'thinking', 'content': '正在制定调度策略...'}, ensure_ascii=False)}\n\n"
                selected_solver = result.get("selected_solver", "fcfs")
                yield f"data: {json.dumps({'type': 'thinking', 'content': f'选择求解器：{selected_solver}'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'progress', 'layer': 2, 'message': '策略制定完成'}, ensure_ascii=False)}\n\n"
                stage_times['L2策略规划'] = time.time() - t0

                # L3
                t0 = time.time()
                yield f"data: {json.dumps({'type': 'thinking', 'content': '正在执行调度算法...'}, ensure_ascii=False)}\n\n"
                dispatch_metrics = result.get("dispatch_metrics", {})
                total_delay = dispatch_metrics.get("total_delay_minutes", 0)
                max_delay = dispatch_metrics.get("max_delay_minutes", 0)
                solving_time = dispatch_metrics.get("computation_time", 0)
                yield f"data: {json.dumps({'type': 'thinking', 'content': f'求解完成：{selected_solver}'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'thinking', 'content': f'   总延误：{total_delay}分钟'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'thinking', 'content': f'   最大延误：{max_delay}分钟'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'thinking', 'content': f'   求解器耗时：{solving_time:.2f}秒'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'progress', 'layer': 3, 'message': '求解完成'}, ensure_ascii=False)}\n\n"
                stage_times['L3求解执行'] = time.time() - t0

                # L4
                t0 = time.time()
                yield f"data: {json.dumps({'type': 'thinking', 'content': '正在生成调度方案...'}, ensure_ascii=False)}\n\n"
                eval_grade = result.get("eval_grade", "N")
                yield f"data: {json.dumps({'type': 'thinking', 'content': f'评估完成：综合评级 {eval_grade}'}, ensure_ascii=False)}\n\n"
                llm_summary = result.get("natural_language_plan", "")
                if llm_summary:
                    yield f"data: {json.dumps({'type': 'thinking', 'content': f'评估摘要：{llm_summary}'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'progress', 'layer': 4, 'message': '评估与方案生成完成'}, ensure_ascii=False)}\n\n"
                stage_times['L4评估方案'] = time.time() - t0

                # 性能摘要
                stage_times['总耗时'] = time.time() - total_start
                timing_summary = " | ".join([f"{k}: {v:.1f}s" for k, v in stage_times.items()])
                logger.info(f"[流式API性能] {timing_summary}")
                yield f"data: {json.dumps({'type': 'thinking', 'content': f'各环节耗时：{timing_summary}'}, ensure_ascii=False)}\n\n"

                # 构建 evaluation_report 字典
                eval_report = result.get("evaluation_report", {})
                eval_report_dict = None
                if eval_report:
                    try:
                        eval_report_dict = eval_report.model_dump() if hasattr(eval_report, 'model_dump') else eval_report.dict()
                    except:
                        eval_report_dict = {
                            'evaluation_grade': getattr(eval_report, 'evaluation_grade', 'N'),
                            'on_time_rate': getattr(eval_report, 'on_time_rate', 0.0),
                            'punctuality_strict': getattr(eval_report, 'punctuality_strict', 0.0),
                            'delay_std_dev': getattr(eval_report, 'delay_std_dev', 0.0),
                            'delay_propagation_depth': getattr(eval_report, 'delay_propagation_depth', 0),
                            'delay_propagation_breadth': getattr(eval_report, 'delay_propagation_breadth', 0),
                            'risk_warnings': getattr(eval_report, 'risk_warnings', []),
                            'constraint_check': getattr(eval_report, 'constraint_check', {})
                        }

                opt_schedule = result.get("optimized_schedule", {})
                original_schedule = get_original_schedule()
                dispatcher_metrics = compute_dispatcher_metrics(original_schedule, opt_schedule)
                comparison_results = result.get("comparison_results")

                final_result = {
                    'success': True,
                    'ui_action': 'render_dispatch',
                    'chat_message': '调度方案已生成，请查看右侧信息面板和详细方案。',
                    'recognized_scenario': accident_card.get("scene_category", "unknown") if isinstance(accident_card, dict) else getattr(accident_card, "scene_category", "unknown"),
                    'selected_skill': result.get("selected_skill", "dispatch_solve_skill"),
                    'selected_solver': selected_solver,
                    'reasoning': result.get("reasoning", ""),
                    'llm_summary': llm_summary,
                    'natural_language_plan': llm_summary,
                    'delay_statistics': {**dispatch_metrics, **dispatcher_metrics},
                    'message': result.get("message", ""),
                    'computation_time': result.get("computation_time", 0),
                    'optimized_schedule': opt_schedule,
                    'original_schedule': original_schedule,
                    'evaluation_report': eval_report_dict or eval_report,
                    'operations_guide': result.get("operations_guide"),
                    'comparison_results': comparison_results,
                    'dispatcher_metrics': dispatcher_metrics
                }

                yield f"data: {json.dumps({'type': 'result', 'data': final_result}, ensure_ascii=False)}\n\n"

                # 保存调度分支的对话上下文
                dispatch_entities = {}
                if accident_card:
                    affected = accident_card.get("affected_train_ids", []) if isinstance(accident_card, dict) else getattr(accident_card, "affected_train_ids", [])
                    if affected:
                        dispatch_entities["train_id"] = affected[0]
                    loc_name = accident_card.get("location_name", "") if isinstance(accident_card, dict) else getattr(accident_card, "location_name", "")
                    if loc_name:
                        dispatch_entities["station_name"] = loc_name
                dispatch_summary = f"调度完成：{accident_card.get('scene_category', '未知场景') if isinstance(accident_card, dict) else getattr(accident_card, 'scene_category', '未知场景')}，"
                dispatch_summary += f"使用{selected_solver}求解器，"
                dispatch_summary += f"评估评级{eval_grade}"
                _save_chat_context(session_id, dispatch_entities, "dispatch", user_msg=prompt, assistant_msg=dispatch_summary)
            except Exception as e:
                logger.exception(f"[流式API] 处理异常: {str(e)}")
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

        return Response(generate(), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache',
                                 'Connection': 'keep-alive',
                                 'X-Accel-Buffering': 'no'})

    except Exception as e:
        logger.exception(f"agent_chat_stream设置异常: {str(e)}")
        return jsonify({
            "success": False,
            "message": str(e)
        })


def parse_user_prompt(prompt: str) -> dict:
    """
    解析用户输入，构建DelayInjection

    统一LLM驱动架构：
    - 不再使用关键词硬判场景类型
    - 仅提取基本的列车、车站、延误时间信息
    - 场景识别交由L1层的LLM完成
    """
    import re

    # 提取列车和延误信息（纯文本提取，不涉及语义理解）
    # 匹配格式：G1001延误10分钟，G1003延误15分钟
    train_pattern = r'([GDCTKZ]\d+)'
    delay_pattern = r'(\d+)\s*分钟'

    train_ids = re.findall(train_pattern, prompt)
    delays = re.findall(delay_pattern, prompt)

    # 如果没有提取到关键信息，不再使用默认值，而是设置为空
    if not train_ids:
        train_ids = []
    if not delays:
        delays = []

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

        # 直接调用Agent分析（Agent内部使用LLM进行语义理解，不再预解析）
        logger.info(f"调用Agent分析（带比较），prompt: {prompt[:50]}...")
        result = agent.analyze_with_comparison(
            delay_injection={"user_prompt": prompt},  # 只传递原始prompt，Agent内部用LLM解析
            user_prompt=prompt,
            comparison_criteria=comparison_criteria
        )

        if result.success and result.dispatch_result:
            dispatch = result.dispatch_result
            original_schedule = get_original_schedule()

            logger.info("Agent比较分析成功，返回结果")

            # 构建返回数据
            response_data = {
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
            }

            # 添加评估报告相关字段
            if hasattr(result, 'evaluation_report') and result.evaluation_report:
                # 如果是pydantic模型，使用model_dump
                if hasattr(result.evaluation_report, 'model_dump'):
                    response_data["evaluation_report"] = result.evaluation_report.model_dump()
                else:
                    response_data["evaluation_report"] = result.evaluation_report

            # 添加LLM摘要（从evaluation_report中获取）
            if hasattr(result, 'evaluation_report') and result.evaluation_report:
                eval_report = result.evaluation_report
                if isinstance(eval_report, dict):
                    llm_summary = eval_report.get('llm_summary', '')
                elif hasattr(eval_report, 'llm_summary'):
                    llm_summary = eval_report.llm_summary
                else:
                    llm_summary = ''

                if llm_summary:
                    response_data["llm_summary"] = llm_summary

            # 添加调度员操作指南
            if hasattr(result, 'operations_guide') and result.operations_guide:
                response_data["operations_guide"] = result.operations_guide
                logger.info(f"添加调度员操作指南，场景: {result.operations_guide.get('scene_name', '未知')}")
            else:
                logger.warning("调度员操作指南为空或不存在")

            # 添加自然语言调度方案（优先从result.natural_language_plan获取）
            if hasattr(result, 'natural_language_plan') and result.natural_language_plan:
                response_data["natural_language_plan"] = result.natural_language_plan
                logger.info(f"添加自然语言调度方案，长度: {len(result.natural_language_plan)}")
            else:
                logger.warning("自然语言调度方案为空或不存在")
                # 提供默认的自然语言方案
                winner_scheduler = response_data.get("delay_statistics", {}).get("winner_scheduler", "未知调度器")
                if result.dispatch_result and result.dispatch_result.delay_statistics:
                    max_delay = result.dispatch_result.delay_statistics.get("max_delay_seconds", 0) / 60
                    default_plan = f"使用{winner_scheduler}进行调度，最大延误{max_delay:.1f}分钟。"
                    response_data["natural_language_plan"] = default_plan
                    logger.info(f"使用默认自然语言方案: {default_plan}")

            return jsonify(response_data)
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


# 全局比较器实例（延迟初始化）
_scheduler_comparator = None

def get_scheduler_comparator():
    """获取或创建调度器比较器实例（直接比较，不走Agent工作流）"""
    global _scheduler_comparator
    if _scheduler_comparator is None:
        from scheduler_comparison.comparator import create_comparator
        from models.data_loader import get_trains_pydantic, get_stations_pydantic
        trains = get_trains_pydantic()
        stations = get_stations_pydantic()
        _scheduler_comparator = create_comparator(trains, stations)
        logger.info(f"调度器比较器初始化完成，已注册: {_scheduler_comparator.list_schedulers()}")
    return _scheduler_comparator


@app.route('/api/scheduler_comparison', methods=['POST'])
def scheduler_comparison():
    """
    调度方法比较API
    直接对比所有调度算法（FCFS、MIP、MaxDelayFirst、Hierarchical、NoOp等），不走Agent工作流
    """
    try:
        data = request.json

        train_id = data.get('train_id')
        station_code = data.get('station')
        if not station_code:
            station_code = data.get('station_code')
        delay_minutes = data.get('delay_minutes', 20)
        criteria = data.get('criteria', 'balanced')

        if not train_id:
            return jsonify({"success": False, "message": "请提供列车ID"})
        if not station_code:
            return jsonify({"success": False, "message": "请提供车站"})

        delay_seconds = delay_minutes * 60
        logger.info(f"调度比较请求: train={train_id}, station={station_code}, delay={delay_minutes}min, criteria={criteria}")

        # 解析比较准则
        criteria_map = {
            'min_max_delay': ComparisonCriteria.MIN_MAX_DELAY,
            'min_avg_delay': ComparisonCriteria.MIN_AVG_DELAY,
            'min_total_delay': ComparisonCriteria.MIN_TOTAL_DELAY,
            'max_on_time_rate': ComparisonCriteria.MAX_ON_TIME_RATE,
            'min_affected_trains': ComparisonCriteria.MIN_AFFECTED_TRAINS,
            'balanced': ComparisonCriteria.BALANCED,
            'real_time': ComparisonCriteria.REAL_TIME
        }
        comparison_criteria = criteria_map.get(criteria, ComparisonCriteria.BALANCED)

        # 解析优化目标
        objective = "min_total_delay"
        if criteria == 'min_max_delay':
            objective = "min_max_delay"
        elif criteria == 'min_avg_delay':
            objective = "min_avg_delay"

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
            affected_trains=[train_id]
        )

        # 【关键修复】直接调用比较器，不走Agent工作流，无需LLM实体提取
        comparator = get_scheduler_comparator()
        result = comparator.compare_all(
            delay_injection,
            criteria=comparison_criteria,
            objective=objective
        )

        if not result.success:
            return jsonify({
                "success": False,
                "message": "调度比较执行失败",
                "comparison_result": {"all_results": [], "recommendations": result.recommendations}
            })

        # 构建前端兼容的输出格式
        all_results = []
        for r in result.results:
            m = r.result.metrics if r.result else None
            all_results.append({
                "rank": r.rank,
                "scheduler_name": r.scheduler_name,
                "scheduler_type": r.scheduler_type.value if r.scheduler_type else "unknown",
                "score": r.score,
                "is_winner": r.is_winner,
                "metrics": m.to_dict() if m else {},
                "improvement_over_baseline": r.improvement_over_baseline
            })

        winner = result.winner
        comparison_result = {
            "success": True,
            "criteria": result.criteria.value,
            "all_results": all_results,
            "recommendations": result.recommendations,
            "computation_time": result.computation_time,
            "winner": {
                "scheduler_name": winner.scheduler_name,
                "score": winner.score,
                "metrics": winner.result.metrics.to_dict() if winner.result and winner.result.metrics else {}
            } if winner else None
        }

        return jsonify({
            "success": True,
            "comparison_result": comparison_result,
            "message": f"已对比 {len(all_results)} 个调度器，最优方案: {winner.scheduler_name if winner else '无'}"
        })

    except Exception as e:
        logger.exception(f"调度比较异常: {str(e)}")
        return jsonify({
            "success": False,
            "message": str(e),
            "comparison_result": {"all_results": [], "recommendations": [f"异常: {str(e)}"]}
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
    
    统一架构：直接使用LLMAgent执行完整L1-L4工作流，不再手动编排

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

        # 统一架构：使用LLMAgent执行完整工作流（与agent_chat保持一致）
        agent = get_agent()
        if agent is None:
            return jsonify({
                "success": False,
                "message": "Agent未初始化"
            })

        # 统一架构：使用LLMAgent执行完整L1-L4工作流（与agent_chat保持一致）
        # 注意：这里使用agent.analyze()，它会内部调用WorkflowEngine执行完整工作流
        delay_injection = {"raw_input": user_input}
        agent_result = agent.analyze(delay_injection, user_input)

        # 从工作流结果中提取accident_card
        workflow_result = getattr(agent_result, '_workflow_result', None)
        accident_card_data = getattr(agent_result, '_accident_card', {}) if not workflow_result else None
        if not accident_card_data and workflow_result:
            accident_card_data = workflow_result.debug_trace.get("accident_card", {}) if hasattr(workflow_result, 'debug_trace') else {}

        # 转换为字典格式（用于JSON序列化）
        result_dict = {
            "accident_card": accident_card_data,
            "can_solve": accident_card_data.get("is_complete", True) if accident_card_data else True,
            "missing_info": accident_card_data.get("missing_fields", []) if accident_card_data else [],
            "llm_response_type": "llm_real"
        }

        # 检查信息是否完整
        is_complete = accident_card_data.get("is_complete", True) if accident_card_data else True
        missing_fields = accident_card_data.get("missing_fields", []) if accident_card_data else []

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
            "layer1_result": result_dict,
            "agent_result": {
                "success": agent_result.success,
                "recognized_scenario": agent_result.recognized_scenario,
                "selected_skill": agent_result.selected_skill,
                "selected_solver": agent_result.selected_solver,
                "reasoning": agent_result.reasoning,
                "llm_summary": agent_result.llm_summary
            }
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
    继续执行多轮对话（统一架构：直接使用LLMAgent执行完整工作流）

    请求体:
    {
        "session_id": "会话ID",
        "continue_layer": true/false  # 是否继续执行下一层
    }

    响应:
    {
        "session_id": "会话ID",
        "current_layer": 4,
        "messages": [...],
        "workflow_result": {...}  # 完整工作流结果
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

        # 统一架构：直接使用LLMAgent执行完整L1-L4工作流
        # 不再手动逐层执行，而是复用统一的analyze接口
        agent = get_agent()
        if agent is None:
            return jsonify({
                "success": False,
                "message": "Agent未初始化"
            })

        # 获取用户原始输入
        user_input = status.get("user_input", "")
        if not user_input:
            return jsonify({
                "success": False,
                "message": "会话中未找到用户输入"
            })

        logger.info(f"多轮对话继续，执行完整工作流，输入: {user_input[:50]}...")

        # 使用Agent执行完整L1-L4工作流（与agent_chat/workflow_start统一入口）
        result = agent.analyze(
            delay_injection={"raw_input": user_input},
            user_prompt=user_input
        )

        if not result.success:
            return jsonify({
                "success": False,
                "message": f"工作流执行失败: {result.error_message}"
            })

        # 构建统一格式的响应
        workflow_result = {
            "success": True,
            "recognized_scenario": result.recognized_scenario,
            "selected_skill": result.selected_skill,
            "selected_solver": result.selected_solver,
            "reasoning": result.reasoning,
            "llm_summary": result.llm_summary,
            "dispatch_result": {
                "optimized_schedule": result.dispatch_result.optimized_schedule if result.dispatch_result else [],
                "delay_statistics": result.dispatch_result.delay_statistics if result.dispatch_result else {},
                "computation_time": result.dispatch_result.computation_time if result.dispatch_result else 0,
                "success": result.dispatch_result.success if result.dispatch_result else False,
                "message": result.dispatch_result.message if result.dispatch_result else "",
                "skill_name": result.dispatch_result.skill_name if result.dispatch_result else ""
            },
            "computation_time": result.computation_time,
            "model_used": result.model_used
        }

        # 更新会话状态为完成
        session_mgr.update_layer_result(session_id, 4, workflow_result)
        session_mgr.complete_session(session_id)

        # 获取更新后的状态
        status = session_mgr.get_session_status(session_id)

        return jsonify({
            "success": True,
            "session_id": session_id,
            "current_layer": 4,
            "progress": status["progress"],
            "messages": status["messages"],
            "workflow_result": workflow_result,
            "is_complete": True,
            "message": "工作流执行完成"
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

        # 统一架构：使用LLMAgent执行完整工作流（与agent_chat/workflow_start保持一致）
        agent = get_agent()
        if agent is None:
            return jsonify({
                "success": False,
                "message": "Agent未初始化"
            })

        # 合并原始输入和补充信息
        original_input = status.get("original_input", "")
        combined_input = f"{original_input} {additional_info}"

        logger.info(f"使用补充信息重新执行完整工作流: {combined_input[:50]}...")

        # 使用Agent执行完整L1-L4工作流（统一入口）
        agent_result = agent.analyze(
            delay_injection={"raw_input": combined_input},
            user_prompt=combined_input
        )

        # 从工作流结果中提取accident_card
        workflow_result = getattr(agent_result, '_workflow_result', None)
        accident_card_data = getattr(agent_result, '_accident_card', {}) if not workflow_result else None
        if not accident_card_data and workflow_result:
            accident_card_data = workflow_result.debug_trace.get("accident_card", {}) if hasattr(workflow_result, 'debug_trace') else {}

        # 转换为字典格式（用于JSON序列化）
        result_dict = {
            "accident_card": accident_card_data,
            "can_solve": accident_card_data.get("is_complete", True) if accident_card_data else True,
            "missing_info": accident_card_data.get("missing_fields", []) if accident_card_data else [],
            "llm_response_type": "llm_real"
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
        is_complete = accident_card_data.get("is_complete", True) if accident_card_data else True
        missing_fields = accident_card_data.get("missing_fields", []) if accident_card_data else []

        # 构建响应
        response = {
            "success": True,
            "session_id": session_id,
            "current_layer": 1,
            "progress": status["progress"],
            "messages": status["messages"],
            "layer1_result": result_dict,
            "agent_result": {
                "success": agent_result.success,
                "recognized_scenario": agent_result.recognized_scenario,
                "selected_skill": agent_result.selected_skill,
                "selected_solver": agent_result.selected_solver,
                "reasoning": agent_result.reasoning,
                "llm_summary": agent_result.llm_summary
            }
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

        # 改进：检查端口是否被占用
        import socket
        port = 8081
        host = '127.0.0.1'  # 修改为本地回环地址，提高连接稳定性

        # 检查端口是否被占用
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()

        if result == 0:
            logger.warning(f"警告: 端口 {port} 已被占用！")
            logger.warning("请先关闭占用该端口的其他服务")
            logger.warning("或者修改config.py中的WEB_PORT配置")
            # 尝试下一个端口
            port = 8082
            logger.info(f"尝试使用端口 {port}...")

        # 无论端口是否被占用，都显示访问地址
        logger.info(f"访问地址: http://localhost:{port}")
        logger.info(f"或者访问: http://127.0.0.1:{port}")
        logger.info(f"")
        logger.info(f"= 界面访问说明 =")
        logger.info(f"  统一入口：http://localhost:{port}/")
        logger.info(f"  （智能调度 + 调度器对比 + LLM工作流三合一界面）")
        logger.info(f"=")
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

        # 改进Flask运行配置
        try:
            # 使用更稳定的配置
            app.run(
                host=host,  # 使用127.0.0.1而不是0.0.0.0
                port=port,
                debug=False,
                threaded=True,  # 启用多线程
                use_reloader=False  # 禁用自动重载避免端口冲突
            )
        except OSError as e:
            if "Address already in use" in str(e):
                logger.error(f"错误: 端口 {port} 被占用")
                logger.error("请关闭占用该端口的进程或更换端口")
            else:
                logger.error(f"服务启动失败: {e}")
            sys.exit(1)
