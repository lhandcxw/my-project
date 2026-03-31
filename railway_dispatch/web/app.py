# -*- coding: utf-8 -*-
"""
铁路调度系统 - Web后端 (Flask)
降低环境配置难度
"""

from flask import Flask, render_template_string, request, jsonify, Response
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
from railway_agent.dispatch_skills import create_skills, execute_skill
from evaluation.evaluator import Evaluator

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 导入运行图生成模块
import sys
import os

# 添加项目根目录到 Python 路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# 导入运行图生成模块（经典铁路运行图风格：横轴时间，纵轴车站）
from visualization.simple_diagram import create_train_diagram, create_comparison_diagram

# 导入Agent
from railway_agent.rule_agent import RuleAgent, create_rule_agent
from railway_agent.tool_registry import ToolRegistry

# QwenAgent延迟导入（需要时再加载）
def get_qwen_agent_module():
    """延迟导入QwenAgent（避免在RuleAgent模式下加载modelscope）"""
    from railway_agent.qwen_agent import QwenAgent, create_qwen_agent
    return QwenAgent, create_qwen_agent

app = Flask(__name__)
CORS(app)  # 启用跨域支持

# 启用真实数据
# 使用真实数据，避免示例数据混淆
use_real_data(True)
logger.info("已启用真实数据模式")

# 全局数据 - 从 centralized data loader 加载
# 注意：使用真实数据时，MIP求解器对列车数量有限制（约60列内可行）
# 这里只加载前50列列车以保证求解器可以正常工作
all_trains = get_trains_pydantic()
trains = all_trains[:50]  # 限制列车数量
stations = get_stations_pydantic()
station_codes = get_station_codes()
station_names = get_station_names()
train_ids = get_train_ids()

# 创建调度器
scheduler = MIPScheduler(trains, stations)
skills = create_skills(scheduler)
evaluator = Evaluator()

# Qwen Agent (延迟加载)
qwen_agent = None

# ============================================
# Agent模式配置
# ============================================
# 可选值：
#   "rule"    - 使用固定规则Agent，无需大模型（推荐用于开发和测试）
#   "qwen"    - 使用Qwen大模型Agent（需要配置MODEL_PATH）
#   "auto"    - 自动选择：优先使用Qwen，如果失败则回退到RuleAgent
AGENT_MODE = "rule"  # 默认使用规则模式，跑通流程后再切换为"qwen"

# 模型配置: 设置为 ModelScope 模型 ID 或本地路径
# 例如: "Qwen3.5-4B"
# 留空则不使用大模型
MODEL_PATH = "/data/wls/test-agent/Qwen3.5-4B"  # 使用本地 Qwen3.5-4B 模型

# ModelScope API Key 配置
import os
os.environ['MODELSCOPE_API_TOKEN'] = 'ms-4e02888f-95d6-4fd1-b07c-4897386cf13c'

def get_qwen_agent():
    """
    获取或创建Agent实例
    
    根据AGENT_MODE配置选择Agent类型：
    - "rule": 使用RuleAgent（固定规则，无需大模型）
    - "qwen": 使用QwenAgent（需要大模型）
    - "auto": 优先Qwen，失败则回退到RuleAgent
    """
    global qwen_agent
    
    if qwen_agent is not None:
        return qwen_agent
    
    # Rule模式：使用固定规则Agent
    if AGENT_MODE == "rule":
        logger.info("使用RuleAgent模式（固定规则，无需大模型）")
        qwen_agent = create_rule_agent(trains=trains, stations=stations)
        logger.info("RuleAgent 初始化完成")
        return qwen_agent
    
    # Qwen模式：尝试加载大模型
    if AGENT_MODE in ["qwen", "auto"]:
        try:
            logger.info(f"正在初始化QwenAgent，模型路径: {MODEL_PATH}")
            # 延迟导入QwenAgent（避免在RuleAgent模式下加载modelscope）
            _, create_qwen_agent_func = get_qwen_agent_module()
            qwen_agent = create_qwen_agent_func(model_path=MODEL_PATH, trains=trains, stations=stations)
            if qwen_agent is None:
                logger.warning("QwenAgent创建失败（可能未配置模型路径）")
                if AGENT_MODE == "auto":
                    logger.info("回退到RuleAgent模式")
                    qwen_agent = create_rule_agent(trains=trains, stations=stations)
                    logger.info("RuleAgent 初始化完成")
            else:
                logger.info("QwenAgent 初始化完成")
            return qwen_agent
        except Exception as e:
            logger.error(f"QwenAgent 初始化失败: {e}")
            if AGENT_MODE == "auto":
                logger.info("回退到RuleAgent模式")
                qwen_agent = create_rule_agent(trains=trains, stations=stations)
                logger.info("RuleAgent 初始化完成")
                return qwen_agent
            return None
    
    return None


def get_original_schedule():
    """获取原始时刻表"""
    schedule = {}
    for train in trains:
        stops = []
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


# HTML模板
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>铁路调度Agent系统</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: "Microsoft YaHei", Arial, sans-serif; background: #f5f7fa; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        
        /* 头部 */
        header { background: linear-gradient(135deg, #1E88E5, #1565C0); color: white; padding: 30px 0; text-align: center; }
        header h1 { font-size: 2rem; margin-bottom: 10px; }
        header p { opacity: 0.9; }
        
        /* 标签页 */
        .tabs { display: flex; margin: 20px 0; border-bottom: 2px solid #ddd; }
        .tab { padding: 15px 30px; cursor: pointer; border: none; background: none; font-size: 1rem; color: #666; }
        .tab.active { color: #1E88E5; border-bottom: 3px solid #1E88E5; font-weight: bold; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        
        /* 卡片 */
        .card { background: white; border-radius: 10px; padding: 25px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .card h2 { color: #333; margin-bottom: 20px; font-size: 1.3rem; border-left: 4px solid #1E88E5; padding-left: 15px; }
        
        /* 表单 */
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; margin-bottom: 5px; color: #555; font-weight: 500; }
        .form-group select, .form-group input { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 5px; font-size: 1rem; }
        
        /* 按钮 */
        .btn { padding: 12px 30px; border: none; border-radius: 5px; cursor: pointer; font-size: 1rem; transition: all 0.3s; }
        .btn-primary { background: #1E88E5; color: white; }
        .btn-primary:hover { background: #1565C0; }
        .btn-success { background: #4CAF50; color: white; }
        
        /* 网格 */
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        
        /* 指标卡片 */
        .metric { background: #f8f9fa; padding: 20px; border-radius: 8px; text-align: center; }
        .metric-value { font-size: 1.8rem; font-weight: bold; color: #1E88E5; }
        .metric-label { color: #666; margin-top: 5px; }
        
        /* 时刻表 */
        .schedule-table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        .schedule-table th, .schedule-table td { padding: 12px; text-align: left; border-bottom: 1px solid #eee; }
        .schedule-table th { background: #f8f9fa; color: #333; }
        .schedule-table tr:hover { background: #f8f9fa; }
        .delay-tag { padding: 3px 8px; border-radius: 3px; font-size: 0.85rem; }
        .delay-red { background: #ffebee; color: #c62828; }
        .delay-green { background: #e8f5e9; color: #2e7d32; }
        
        /* 运行图 */
        .diagram-container { display: flex; gap: 20px; overflow-x: auto; padding: 20px 0; }
        .diagram { border: 1px solid #ddd; background: #fafafa; min-width: 700px; padding: 15px; border-radius: 8px; }
        .diagram h3 { text-align: center; color: #333; margin-bottom: 15px; }
        
        /* 加载 */
        .loading { text-align: center; padding: 40px; display: none; }
        .spinner { border: 3px solid #f3f3f3; border-top: 3px solid #1E88E5; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto 15px; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        
        /* 结果区域 */
        .result-section { display: none; }
        
        /* 车站轴 */
        .station-axis { display: flex; flex-direction: column; border-right: 2px solid #333; padding-right: 10px; margin-right: 10px; min-width: 80px; }
        .station-item { height: 60px; display: flex; align-items: center; font-weight: bold; border-bottom: 1px dotted #ccc; }
        
        /* 时间线 */
        .timeline { position: relative; flex-grow: 1; height: 300px; }
        .train-line { position: absolute; height: 3px; }
        .train-dot { position: absolute; width: 10px; height: 10px; border-radius: 50%; transform: translate(-50%, -50%); }
        .delay-label { color: red; font-size: 10px; position: absolute; white-space: nowrap; }
        
        /* 颜色 */
        .color-0 { background: #E91E63; }
        .color-1 { background: #9C27B0; }
        .color-2 { background: #3F51B5; }
        .color-3 { background: #00BCD4; }
        .color-4 { background: #4CAF50; }
        .color-5 { background: #FF9800; }
        
        /* 经典铁路运行图样式 */
        .classic-diagram {
            position: relative;
            width: 700px;
            height: 450px;
            background: #fafafa;
            border: 2px solid #333;
            overflow: visible;
            padding: 10px;
        }
        .time-axis {
            position: absolute;
            left: 10px;
            right: 10px;
            top: 0;
            height: 30px;
            background: #f0f0f0;
            border-bottom: 1px solid #333;
        }
        .time-tick {
            position: absolute;
            transform: translateX(-50%);
            font-size: 10px;
            color: #666;
        }
        .station-axis-bottom {
            position: absolute;
            left: 10px;
            right: 10px;
            bottom: 0;
            height: 30px;
            background: #f0f0f0;
            border-top: 1px solid #333;
            display: flex;
            justify-content: space-around;
            padding: 0 20px;
        }
        .station-label {
            position: absolute;
            transform: translateX(-50%);
            font-size: 11px;
            font-weight: bold;
            color: #333;
            bottom: 5px;
        }
        .grid-v-lines, .grid-h-lines {
            position: absolute;
            left: 10px;
            right: 10px;
            top: 30px;
            bottom: 30px;
        }
        .grid-v {
            position: absolute;
            width: 1px;
            background: #e0e0e0;
        }
        .grid-h {
            position: absolute;
            height: 1px;
            background: #e0e0e0;
        }
        .train-lines {
            position: absolute;
            left: 0;
            right: 0;
            top: 25px;
            bottom: 25px;
        }
        .train-dot {
            position: absolute;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            transform: translate(-50%, -50%);
            z-index: 10;
        }
        .train-slope {
            position: absolute;
            height: 3px;
            transform-origin: left center;
            z-index: 5;
        }
        .train-name {
            position: absolute;
            font-size: 9px;
            font-weight: bold;
            white-space: nowrap;
            z-index: 20;
        }
        .delay-tag {
            position: absolute;
            font-size: 9px;
            color: red;
            font-weight: bold;
            white-space: nowrap;
            z-index: 20;
        }
        
        /* 对比区域 */
        .comparison { display: flex; gap: 30px; margin: 20px 0; }
        .comparison-item { flex: 1; }
        .comparison-item h4 { margin-bottom: 10px; color: #333; }
        
        /* 建议 */
        .recommendation { background: #e3f2fd; padding: 15px; border-radius: 5px; margin-top: 15px; }
        .recommendation h4 { color: #1565C0; margin-bottom: 10px; }
        
        footer { text-align: center; padding: 20px; color: #888; margin-top: 40px; }
    </style>
</head>
<body>
    <header>
        <div class="container">
            <h1>🚄 铁路调度Agent系统</h1>
            <p>基于整数规划的智能铁路调度优化系统</p>
        </div>
    </header>
    
    <div class="container">
        <!-- 标签页 -->
        <div class="tabs">
            <button class="tab active" onclick="showTab('dispatch')">🤖 智能调度</button>
            <button class="tab" onclick="showTab('comparison')">📊 调度比较</button>
        </div>

        <!-- 智能调度 - 统一入口 -->
        <div id="dispatch" class="tab-content active">
            <!-- 输入区域 -->
            <div class="card">
                <h2>📝 输入调度需求</h2>

                <!-- 智能对话输入 -->
                <div style="margin-bottom: 20px;">
                    <h3 style="color: #1565C0; margin-bottom: 10px;">💬 智能对话输入</h3>
                    <p style="color: #666; font-size: 0.9em; margin-bottom: 10px;">用自然语言描述您的需求，如"G1001在天津西延误10分钟"</p>
                    <div class="grid" style="margin-bottom: 10px;">
                        <button class="btn" style="background: #e3f2fd; color: #1565C0;" onclick="fillPrompt('限速')">🚄 临时限速</button>
                        <button class="btn" style="background: #ffebee; color: #c62828;" onclick="fillPrompt('故障')">🚨 突发故障</button>
                        <button class="btn" style="background: #f3e5f5; color: #7b1fa2;" onclick="fillPrompt('延误')">📋 延误调整</button>
                    </div>
                    <textarea id="dispatchPrompt" rows="3" placeholder="描述您的调度需求..."></textarea>
                    <div class="grid" style="margin-top: 10px;">
                        <button class="btn btn-primary" onclick="sendDispatch()">🚀 开始智能调度</button>
                        <button class="btn btn-success" onclick="sendDispatchWithComparison()">📊 调度方法比较</button>
                    </div>
                </div>

                <div style="border-top: 1px dashed #ddd; padding-top: 20px;">
                    <h3 style="color: #666; margin-bottom: 10px; cursor: pointer;" onclick="toggleFormInput()">
                        📋 表单输入 <span id="formToggleIcon" style="font-size: 0.8em;">▼ 点击展开</span>
                    </h3>
                    <div id="formInputSection" style="display: none;">
                    <div class="grid-2">
                        <div class="form-group">
                            <label>场景类型</label>
                            <select id="scenarioType">
                                <option value="temporary_speed_limit">临时限速</option>
                                <option value="sudden_failure">突发故障</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>优化目标</label>
                            <select id="objective">
                                <option value="min_max_delay">最小化最大延误</option>
                                <option value="min_avg_delay">最小化平均延误</option>
                            </select>
                        </div>
                    </div>

                    <div class="form-group">
                        <label>选择列车</label>
                        <select id="selectedTrains" multiple style="height: 80px;">
                            {% for train_id in train_ids %}
                            <option value="{{ train_id }}">{{ train_id }}</option>
                            {% endfor %}
                        </select>
                    </div>

                    <div class="grid-2">
                        <div class="form-group">
                            <label>延误车站</label>
                            <select id="delayStation">
                                {% for code, name in station_names.items() %}
                                <option value="{{ code }}">{{ name }}</option>
                                {% endfor %}
                            </select>
                        </div>
                        <div class="form-group">
                            <label>延误时间(秒)</label>
                            <input type="number" id="delaySeconds" value="600" min="60" max="7200">
                        </div>
                    </div>

                    <button class="btn btn-success" onclick="runFormDispatch()" style="width: 100%;">🚀 执行调度</button>
                    </div>
                </div>

            <!-- 加载状态 -->
            <div class="loading" id="dispatchLoading">
                <div class="spinner"></div>
                <p>Agent正在分析场景、执行调度...</p>
            </div>

            <!-- 结果展示 -->
            <div id="dispatchResult" style="display: none;">
                <!-- 分析结果 -->
                <div class="card">
                    <h2>📊 分析结果</h2>
                    <div class="grid">
                        <div class="metric">
                            <div class="metric-value" id="resultScenario">-</div>
                            <div class="metric-label">场景类型</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value" id="resultSkill">-</div>
                            <div class="metric-label">使用技能</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value" id="resultTime">-</div>
                            <div class="metric-label">计算时间</div>
                        </div>
                    </div>

                    <h4 style="margin: 15px 0 10px;">🤔 Agent推理过程</h4>
                    <div id="resultReasoning" style="background: #f5f5f5; padding: 15px; border-radius: 5px; max-height: 150px; overflow-y: auto; white-space: pre-wrap;"></div>

                    <h4 style="margin: 15px 0 10px;">📈 延误统计</h4>
                    <div class="grid">
                        <div class="metric">
                            <div class="metric-value" id="resultMaxDelay">-</div>
                            <div class="metric-label">最大延误</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value" id="resultAvgDelay">-</div>
                            <div class="metric-label">平均延误</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value" id="resultTotalDelay">-</div>
                            <div class="metric-label">总延误</div>
                        </div>
                    </div>

                    <div id="resultMessage" style="background: #e8f5e9; padding: 12px; border-radius: 5px; margin-top: 15px;"></div>
                </div>

                <!-- 调度比较结果（新增） -->
                <div id="comparisonResultSection" style="display: none;">
                    <div class="card">
                        <h2>🏆 调度方法比较结果</h2>
                        <div id="comparisonRanking"></div>
                        <div id="comparisonRecommendations" style="margin-top: 15px;"></div>
                    </div>
                </div>

                <!-- 时刻表 -->
                <div class="card">
                    <h2>📅 优化后时刻表</h2>
                    <div id="scheduleTable" style="overflow-x: auto;"></div>
                </div>

                <!-- 运行图 -->
                <div class="card">
                    <h2>📈 运行图对比</h2>
                    <div id="diagramContainer" style="text-align: center;"></div>
                </div>
            </div>
        </div>
        
        <!-- 调度比较标签页 -->
        <div id="comparison" class="tab-content">
            <div class="card">
                <h2>📊 调度方法比较</h2>
                <p style="color: #666; margin-bottom: 15px;">比较FCFS（先到先服务）和MIP（整数规划）等多种调度方法，根据您的偏好选择最优方案</p>
                
                <div class="form-group">
                    <label>比较准则</label>
                    <select id="comparisonCriteria">
                        <option value="balanced">均衡考虑</option>
                        <option value="min_max_delay">最小最大延误</option>
                        <option value="min_avg_delay">最小平均延误</option>
                        <option value="real_time">实时优先（计算速度）</option>
                    </select>
                </div>
                
                <div class="form-group">
                    <label>列车ID</label>
                    <select id="comparisonTrainId">
                        {% for train_id in train_ids %}
                        <option value="{{ train_id }}">{{ train_id }}</option>
                        {% endfor %}
                    </select>
                </div>
                
                <div class="grid-2">
                    <div class="form-group">
                        <label>延误车站</label>
                        <select id="comparisonStation">
                            {% for code, name in station_names.items() %}
                            <option value="{{ code }}">{{ name }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="form-group">
                        <label>延误时间(分钟)</label>
                        <input type="number" id="comparisonDelayMinutes" value="20" min="1" max="120">
                    </div>
                </div>
                
                <button class="btn btn-primary" onclick="runComparison()" style="width: 100%;">🔍 开始比较</button>
            </div>
            
            <!-- 比较结果加载 -->
            <div class="loading" id="comparisonLoading">
                <div class="spinner"></div>
                <p>正在比较多种调度方法...</p>
            </div>
            
            <!-- 比较结果展示 -->
            <div id="comparisonResultDisplay" style="display: none;">
                <div class="card">
                    <h2>📋 比较报告</h2>
                    <div id="comparisonReport"></div>
                </div>
            </div>
        </div>
    </div>

    <footer>
        <p>铁路调度Agent系统 v1.0 | 基于整数规划优化</p>
    </footer>

    <script>
        // 切换表单输入显示
        function toggleFormInput() {
            const section = document.getElementById('formInputSection');
            const icon = document.getElementById('formToggleIcon');
            if (section.style.display === 'none') {
                section.style.display = 'block';
                icon.textContent = '▲ 点击收起';
            } else {
                section.style.display = 'none';
                icon.textContent = '▼ 点击展开';
            }
        }

        // 标签页切换
        function showTab(tabId, event) {
            if (event) {
                event.preventDefault();
            }
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            if (event && event.target) {
                event.target.classList.add('active');
            }
            document.getElementById(tabId).classList.add('active');
        }

        // 填充快速输入
        function fillPrompt(type) {
            const prompts = {
                '限速': 'G1001和G1003列车在天津西站因临时限速延误10分钟和15分钟',
                '故障': 'G1005列车在天津西站发生设备故障，延误40分钟',
                '延误': 'G1001列车在北京西站发车延误5分钟，需要调整'
            };
            document.getElementById('dispatchPrompt').value = prompts[type] || '';
        }

        // 格式化时间
        function formatTime(seconds) {
            if (seconds === undefined || seconds === null) return '-';
            const mins = Math.floor(seconds / 60);
            const secs = Math.round(seconds % 60);
            return mins + '分' + secs + '秒';
        }

        // 发送智能调度（对话模式）
        function sendDispatch() {
            const prompt = document.getElementById('dispatchPrompt').value.trim();
            if (!prompt) {
                alert('请输入调度需求');
                return;
            }

            document.getElementById('dispatchLoading').style.display = 'block';
            document.getElementById('dispatchResult').style.display = 'none';

            fetch('/api/agent_chat', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({prompt: prompt})
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP错误! 状态码: ${response.status}`);
                }
                return response.json();
            })
            .then(result => {
                document.getElementById('dispatchLoading').style.display = 'none';

                if (result.success) {
                    showDispatchResult(result);
                } else {
                    alert('执行失败: ' + result.message);
                }
            })
            .catch(error => {
                document.getElementById('dispatchLoading').style.display = 'none';
                console.error('请求失败:', error);
                alert('请求失败: ' + error.message + '\n\n请检查：\n1. 后端服务是否正常运行\n2. 浏览器控制台是否有更多错误信息');
            });
        }

        // 发送表单调度
            function runFormDispatch() {
                const selectedTrains = Array.from(document.getElementById('selectedTrains').selectedOptions).map(o => o.value);
                if (selectedTrains.length === 0) {
                    alert('请至少选择一列列车');
                    return;
                }

                const scenarioType = document.getElementById('scenarioType').value;
                const objective = document.getElementById('objective').value;
                const delayStation = document.getElementById('delayStation').value;
                const delaySeconds = parseInt(document.getElementById('delaySeconds').value);

                const data = {
                    scenario_type: scenarioType,
                    objective: objective,
                    selected_trains: selectedTrains,
                    delay_config: [{
                        train_id: selectedTrains[0],
                        delay_seconds: delaySeconds,
                        station_code: delayStation
                    }]
                };

                document.getElementById('dispatchLoading').style.display = 'block';
                document.getElementById('dispatchResult').style.display = 'none';

                fetch('/api/dispatch', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                })
                .then(response => {
                    if (!response.ok) {
                        throw new Error(`HTTP错误! 状态码: ${response.status}`);
                    }
                    return response.json();
                })
                .then(result => {
                    document.getElementById('dispatchLoading').style.display = 'none';

                    if (result.success) {
                        // 转换为统一格式，添加空值检查
                        const skillMessage = result.skill_result && result.skill_result.message ? result.skill_result.message : '';
                        const unified = {
                            success: true,
                            recognized_scenario: result.planner ? result.planner.recognized_scenario : '',
                            selected_skill: skillMessage.includes('限速') ? 'temporary_speed_limit_skill' : 'sudden_failure_skill',
                            reasoning: '基于表单输入执行调度优化',
                            delay_statistics: result.skill_result ? result.skill_result.delay_statistics : {},
                            message: skillMessage,
                            computation_time: result.skill_result ? result.skill_result.computation_time : 0,
                            optimized_schedule: result.skill_result ? result.skill_result.optimized_schedule : {},
                            original_schedule: result.original_schedule
                        };
                        showDispatchResult(unified);
                    } else {
                        alert('执行失败: ' + result.message);
                    }
                })
                .catch(error => {
                    document.getElementById('dispatchLoading').style.display = 'none';
                    console.error('请求失败:', error);
                    alert('请求失败: ' + error.message + '\n\n请检查：\n1. 后端服务是否正常运行\n2. 浏览器控制台是否有更多错误信息');
                });
            }

        // 显示调度结果
        function showDispatchResult(result) {
            document.getElementById('dispatchResult').style.display = 'block';

            // 基本信息
            document.getElementById('resultScenario').textContent = result.recognized_scenario || '-';
            document.getElementById('resultSkill').textContent = result.selected_skill || '-';
            document.getElementById('resultTime').textContent = (result.computation_time || 0).toFixed(2) + 's';

            // 推理过程
            document.getElementById('resultReasoning').textContent = result.reasoning || '-';

            // 延误统计
            const stats = result.delay_statistics || {};
            document.getElementById('resultMaxDelay').textContent = formatTime(stats.max_delay_seconds);
            document.getElementById('resultAvgDelay').textContent = formatTime(stats.avg_delay_seconds);
            document.getElementById('resultTotalDelay').textContent = formatTime(stats.total_delay_seconds);

            // 消息
            document.getElementById('resultMessage').textContent = result.message || '-';

            // 时刻表
            let tableHtml = '<table class="schedule-table"><thead><tr><th>车次</th><th>车站</th><th>到达</th><th>发车</th><th>延误</th></tr></thead><tbody>';
            for (let [trainId, stops] of Object.entries(result.optimized_schedule || {})) {
                for (let stop of stops) {
                    const delay = stop.delay_seconds || 0;
                    const delayClass = delay > 0 ? 'delay-red' : 'delay-green';
                    const delayText = delay > 0 ? '+' + delay + '秒' : '准点';
                    tableHtml += '<tr><td>' + trainId + '</td><td>' + (stop.station_name || stop.station_code) + '</td><td>' + stop.arrival_time + '</td><td>' + stop.departure_time + '</td><td><span class="delay-tag ' + delayClass + '">' + delayText + '</span></td></tr>';
                }
            }
            tableHtml += '</tbody></table>';
            document.getElementById('scheduleTable').innerHTML = tableHtml;

            // 运行图
            if (result.optimized_schedule && result.original_schedule) {
                fetch('/api/diagram', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        original_schedule: result.original_schedule,
                        optimized_schedule: result.optimized_schedule
                    })
                })
                .then(resp => resp.json())
                .then(data => {
                    if (data.success) {
                        const html = '<img src="data:image/png;base64,' + data.diagram_image + '" style="max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 8px;">';
                        document.getElementById('diagramContainer').innerHTML = html;
                    }
                });
            }
            
            // 显示比较结果（如果有）
            const stats = result.delay_statistics || {};
            if (stats.ranking && stats.ranking.length > 0) {
                showComparisonResult(stats);
            }
        }
        
        // 发送智能调度（带比较）
        function sendDispatchWithComparison() {
            const prompt = document.getElementById('dispatchPrompt').value.trim();
            if (!prompt) {
                alert('请输入调度需求');
                return;
            }

            document.getElementById('dispatchLoading').style.display = 'block';
            document.getElementById('dispatchResult').style.display = 'none';
            document.getElementById('comparisonResultSection').style.display = 'none';

            fetch('/api/agent_chat_with_comparison', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({prompt: prompt, comparison_criteria: 'balanced'})
            })
            .then(response => response.json())
            .then(result => {
                document.getElementById('dispatchLoading').style.display = 'none';

                if (result.success) {
                    showDispatchResult(result);
                } else {
                    alert('执行失败: ' + result.message);
                }
            })
            .catch(error => {
                document.getElementById('dispatchLoading').style.display = 'none';
                console.error('请求失败:', error);
                alert('请求失败: ' + error.message);
            });
        }
        
        // 显示比较结果
        function showComparisonResult(stats) {
            const section = document.getElementById('comparisonResultSection');
            const rankingDiv = document.getElementById('comparisonRanking');
            const recDiv = document.getElementById('comparisonRecommendations');
            
            // 生成排名表格
            let rankingHtml = '<table class="schedule-table"><thead><tr><th>排名</th><th>调度器</th><th>最大延误</th><th>平均延误</th><th>得分</th></tr></thead><tbody>';
            for (let r of stats.ranking || []) {
                const winner = r.rank === 1 ? ' ⭐' : '';
                rankingHtml += '<tr><td>' + r.rank + winner + '</td><td>' + r.scheduler + '</td><td>' + r.max_delay_minutes + '分钟</td><td>' + r.avg_delay_minutes + '分钟</td><td>' + r.score.toFixed(1) + '</td></tr>';
            }
            rankingHtml += '</tbody></table>';
            rankingDiv.innerHTML = rankingHtml;
            
            // 显示推荐
            let recHtml = '<div class="recommendation"><h4>💡 推荐建议</h4><ul>';
            for (let rec of stats.recommendations || []) {
                recHtml += '<li>' + rec + '</li>';
            }
            recHtml += '</ul></div>';
            recDiv.innerHTML = recHtml;
            
            section.style.display = 'block';
        }
        
        // 运行调度比较
        function runComparison() {
            const trainId = document.getElementById('comparisonTrainId').value;
            const station = document.getElementById('comparisonStation').value;
            const delayMinutes = parseInt(document.getElementById('comparisonDelayMinutes').value);
            const criteria = document.getElementById('comparisonCriteria').value;

            document.getElementById('comparisonLoading').style.display = 'block';
            document.getElementById('comparisonResultDisplay').style.display = 'none';

            fetch('/api/scheduler_comparison', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    train_id: trainId,
                    station_code: station,
                    delay_seconds: delayMinutes * 60,
                    criteria: criteria
                })
            })
            .then(response => response.json())
            .then(result => {
                document.getElementById('comparisonLoading').style.display = 'none';
                
                if (result.success) {
                    displayComparisonReport(result);
                } else {
                    alert('比较失败: ' + result.message);
                }
            })
            .catch(error => {
                document.getElementById('comparisonLoading').style.display = 'none';
                console.error('请求失败:', error);
                alert('请求失败: ' + error.message);
            });
        }
        
        // 显示比较报告
        function displayComparisonReport(result) {
            const display = document.getElementById('comparisonResultDisplay');
            const reportDiv = document.getElementById('comparisonReport');
            
            let html = '';
            
            // 推荐方案
            if (result.comparison && result.comparison.recommendation) {
                const rec = result.comparison.recommendation;
                html += '<div class="recommendation" style="margin-bottom: 20px;">';
                html += '<h4>🏆 推荐方案: ' + rec.scheduler_name + '</h4>';
                html += '<div class="grid">';
                html += '<div class="metric"><div class="metric-value">' + rec.key_metrics.max_delay_minutes + '分钟</div><div class="metric-label">最大延误</div></div>';
                html += '<div class="metric"><div class="metric-value">' + rec.key_metrics.avg_delay_minutes + '分钟</div><div class="metric-label">平均延误</div></div>';
                html += '<div class="metric"><div class="metric-value">' + rec.key_metrics.on_time_rate + '%</div><div class="metric-label">准点率</div></div>';
                html += '</div></div>';
            }
            
            // 所有方案
            if (result.comparison && result.comparison.all_options) {
                html += '<h4 style="margin: 15px 0 10px;">📊 所有方案对比</h4>';
                html += '<table class="schedule-table"><thead><tr><th>排名</th><th>调度器</th><th>最大延误</th><th>平均延误</th><th>计算时间</th></tr></thead><tbody>';
                for (let opt of result.comparison.all_options) {
                    const winner = opt.rank === 1 ? ' ⭐' : '';
                    html += '<tr><td>' + opt.rank + winner + '</td><td>' + opt.name + '</td><td>' + opt.max_delay_minutes + '分钟</td><td>' + opt.avg_delay_minutes + '分钟</td><td>' + opt.computation_time.toFixed(2) + '秒</td></tr>';
                }
                html += '</tbody></table>';
            }
            
            // 分析建议
            if (result.comparison && result.comparison.analysis) {
                html += '<h4 style="margin: 15px 0 10px;">💡 分析建议</h4><ul>';
                for (let a of result.comparison.analysis) {
                    html += '<li>' + a + '</li>';
                }
                html += '</ul>';
            }
            
            reportDiv.innerHTML = html;
            display.style.display = 'block';
        }
    </script>
</body>
</html>
'''


@app.route('/')
def index():
    return render_template_string(
        HTML_TEMPLATE,
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
                            valid_stations_for_train = [s.station_code for s in train.schedule.stops]
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
        if train:
            # 检查列车是否停靠在选定车站
            train_stations = [stop.station_code for stop in train.schedule.stops]
            if detected_station_code not in train_stations:
                # 使用列车的第一个停靠站
                actual_station_code = train.schedule.stops[0].station_code
                logger.warning(f"列车 {train_id} 不停靠在 {detected_station_code}，使用 {actual_station_code} 作为延误车站")

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
    比较FCFS、MIP等多种调度方法
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


if __name__ == '__main__':
    logger.info("=" * 50)
    logger.info("铁路调度Agent系统 v1.0")
    logger.info("=" * 50)
    logger.info(f"Agent模式: {AGENT_MODE}")
    if AGENT_MODE == "rule":
        logger.info("使用固定规则Agent，无需加载大模型")
    elif AGENT_MODE == "qwen":
        logger.info(f"使用Qwen Agent，模型路径: {MODEL_PATH}")
    else:
        logger.info("自动模式：优先Qwen Agent，失败则回退到RuleAgent")
    logger.info("访问地址: http://localhost:8080")
    logger.info("按 Ctrl+C 停止服务")
    logger.info("=" * 50)
    app.run(host='0.0.0.0', port=8080, debug=True)
