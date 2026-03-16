# -*- coding: utf-8 -*-
"""
铁路调度系统 - Web后端 (Flask)
降低环境配置难度
"""

from flask import Flask, render_template_string, request, jsonify, Response
import json
import base64

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.data_models import Train, Station, DelayInjection, ScenarioType
from models.data_loader import get_trains_pydantic, get_stations_pydantic, get_station_codes, get_station_names, get_train_ids
from solver.mip_scheduler import MIPScheduler
from agent.planner_agent import PlannerAgent
from skills.dispatch_skills import create_skills, execute_skill
from evaluation.evaluator import Evaluator

# 导入运行图生成模块
import sys
import os

# 添加项目根目录到 Python 路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# 导入运行图生成模块（经典铁路运行图风格：横轴时间，纵轴车站）
from visualization.simple_diagram import create_train_diagram, create_comparison_diagram

app = Flask(__name__)

# 全局数据 - 从 centralized data loader 加载
trains = get_trains_pydantic()
stations = get_stations_pydantic()
station_codes = get_station_codes()
station_names = get_station_names()
train_ids = get_train_ids()

# 创建调度器
scheduler = MIPScheduler(trains, stations)
skills = create_skills(scheduler)
planner = PlannerAgent()
evaluator = Evaluator()


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
            <button class="tab active" onclick="showTab('input')">📝 延误注入</button>
            <button class="tab" onclick="showTab('result')">📊 调度结果</button>
            <button class="tab" onclick="showTab('diagram')">📈 运行图</button>
        </div>
        
        <!-- 延误注入 -->
        <div id="input" class="tab-content active">
            <div class="card">
                <h2>场景配置</h2>
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
                
                <div id="speedLimitParams" class="grid-2">
                    <div class="form-group">
                        <label>限速值 (km/h)</label>
                        <input type="number" id="limitSpeed" value="200" min="50" max="350">
                    </div>
                    <div class="form-group">
                        <label>持续时间 (分钟)</label>
                        <input type="number" id="duration" value="120" min="10" max="480">
                    </div>
                </div>
            </div>
            
            <div class="card">
                <h2>选择受影响列车</h2>
                <div class="form-group">
                    <label>列车选择（可多选）</label>
                    <select id="selectedTrains" multiple style="height: 120px;">
                        {% for train_id in train_ids %}
                        <option value="{{ train_id }}">{{ train_id }}</option>
                        {% endfor %}
                    </select>
                </div>
            </div>
            
            <div class="card">
                <h2>延误配置</h2>
                <div id="delayConfig">
                    <div class="delay-row grid-2">
                        <div class="form-group">
                            <label>延误车站</label>
                            <select class="delay-station">
                                {% for code, name in station_names.items() %}
                                <option value="{{ code }}">{{ name }}</option>
                                {% endfor %}
                            </select>
                        </div>
                        <div class="form-group">
                            <label>延误时间 (秒)</label>
                            <input type="number" class="delay-seconds" value="600" min="60" max="7200" step="60">
                        </div>
                    </div>
                </div>
                <button class="btn btn-primary" onclick="addDelayRow()" style="margin-top: 10px;">+ 添加延误</button>
            </div>
            
            <button class="btn btn-success" onclick="runDispatch()" style="width: 100%;">🚀 开始调度</button>
            
            <div class="loading" id="loading">
                <div class="spinner"></div>
                <p>调度优化中，请稍候...</p>
            </div>
        </div>
        
        <!-- 调度结果 -->
        <div id="result" class="tab-content">
            <div id="resultContent">
                <div class="card">
                    <h2>📋 Planner分析结果</h2>
                    <div class="grid" id="plannerResult">
                        <div class="metric">
                            <div class="metric-value" id="scenarioTypeResult">-</div>
                            <div class="metric-label">场景类型</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value" id="delayLevelResult">-</div>
                            <div class="metric-label">延误等级</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value" id="confidenceResult">-</div>
                            <div class="metric-label">置信度</div>
                        </div>
                    </div>
                </div>
                
                <div class="card">
                    <h2>📊 调度结果统计</h2>
                    <div class="grid">
                        <div class="metric">
                            <div class="metric-value" id="maxDelayResult">-</div>
                            <div class="metric-label">最大延误</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value" id="avgDelayResult">-</div>
                            <div class="metric-label">平均延误</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value" id="totalDelayResult">-</div>
                            <div class="metric-label">总延误</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value" id="computeTimeResult">-</div>
                            <div class="metric-label">计算时间</div>
                        </div>
                    </div>
                </div>
                
                <div class="card">
                    <h2>📈 评估对比</h2>
                    <div class="comparison">
                        <div class="comparison-item">
                            <h4>优化方案</h4>
                            <div class="metric">
                                <div class="metric-value" id="optMaxDelay">-</div>
                                <div class="metric-label">最大延误</div>
                            </div>
                        </div>
                        <div class="comparison-item">
                            <h4>基线方案（不调整）</h4>
                            <div class="metric">
                                <div class="metric-value" id="baseMaxDelay">-</div>
                                <div class="metric-label">最大延误</div>
                            </div>
                        </div>
                    </div>
                    <div class="comparison">
                        <div class="comparison-item">
                            <div class="metric">
                                <div class="metric-value" id="optAvgDelay">-</div>
                                <div class="metric-label">平均延误</div>
                            </div>
                        </div>
                        <div class="comparison-item">
                            <div class="metric">
                                <div class="metric-value" id="baseAvgDelay">-</div>
                                <div class="metric-label">平均延误</div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="card">
                    <h2>📅 优化后时刻表</h2>
                    <div id="scheduleTable"></div>
                </div>
            </div>
            
            <div id="noResult" style="text-align: center; padding: 60px; color: #888;">
                <p>请先在【延误注入】标签页配置场景并执行调度</p>
            </div>
        </div>
        
        <!-- 运行图 -->
        <div id="diagram" class="tab-content">
            <div id="diagramContent">
                <div class="card">
                    <h2>📈 列车运行图对比</h2>
                    <div id="diagramContainer"></div>
                </div>
            </div>
            <div id="noDiagram" style="text-align: center; padding: 60px; color: #888;">
                <p>请先执行调度生成运行图</p>
            </div>
        </div>
    </div>
    
    <footer>
        <p>铁路调度Agent系统 v1.0 | 基于整数规划优化</p>
    </footer>
    
    <script>
        // 全局数据
        let dispatchResult = null;
        
        // 标签页切换
        function showTab(tabId) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            // 找到对应tabId的按钮并添加active类
            const tabs = document.querySelectorAll('.tab');
            if (tabId === 'input') tabs[0].classList.add('active');
            else if (tabId === 'result') tabs[1].classList.add('active');
            else if (tabId === 'diagram') tabs[2].classList.add('active');
            document.getElementById(tabId).classList.add('active');
        }
        
        // 添加延误行
        function addDelayRow() {
            const config = document.getElementById('delayConfig');
            const stations = {{ station_names|tojson }};
            let html = '<div class="delay-row grid-2">';
            html += '<div class="form-group"><label>延误车站</label><select class="delay-station">';
            for (let [code, name] of Object.entries(stations)) {
                html += '<option value="' + code + '">' + name + '</option>';
            }
            html += '</select></div>';
            html += '<div class="form-group"><label>延误时间 (秒)</label><input type="number" class="delay-seconds" value="600" min="60" max="7200" step="60"></div>';
            html += '</div>';
            config.insertAdjacentHTML('beforeend', html);
        }
        
        // 运行调度
        function runDispatch() {
            // 获取选择
            const selectedTrains = Array.from(document.getElementById('selectedTrains').selectedOptions).map(o => o.value);
            if (selectedTrains.length === 0) {
                alert('请至少选择一列列车');
                return;
            }
            
            // 获取延误配置
            const delayRows = document.querySelectorAll('.delay-row');
            const delayConfig = [];
            delayRows.forEach((row, index) => {
                if (index < selectedTrains.length) {
                    delayConfig.push({
                        train_id: selectedTrains[index],
                        delay_seconds: parseInt(row.querySelector('.delay-seconds').value),
                        station_code: row.querySelector('.delay-station').value
                    });
                }
            });
            
            // 构建请求数据
            const scenarioType = document.getElementById('scenarioType').value;
            const objective = document.getElementById('objective').value;
            
            const data = {
                scenario_type: scenarioType,
                objective: objective,
                selected_trains: selectedTrains,
                delay_config: delayConfig,
                limit_speed: parseInt(document.getElementById('limitSpeed').value),
                duration: parseInt(document.getElementById('duration').value)
            };
            
            // 显示加载
            document.getElementById('loading').style.display = 'block';
            
            // 发送请求
            fetch('/api/dispatch', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            })
            .then(response => response.json())
            .then(result => {
                document.getElementById('loading').style.display = 'none';
                
                if (result.success) {
                    dispatchResult = result;
                    showResult(result);
                    showDiagram(result);
                    showTab('result');
                } else {
                    alert('调度失败: ' + result.message);
                }
            })
            .catch(error => {
                document.getElementById('loading').style.display = 'none';
                alert('请求失败: ' + error);
            });
        }
        
        // 显示结果
        function showResult(result) {
            document.getElementById('noResult').style.display = 'none';
            document.getElementById('resultContent').style.display = 'block';
            
            // Planner结果
            document.getElementById('scenarioTypeResult').textContent = result.planner.recognized_scenario;
            document.getElementById('delayLevelResult').textContent = result.planner.delay_level;
            document.getElementById('confidenceResult').textContent = (result.planner.confidence * 100).toFixed(0) + '%';
            
            // 调度统计
            const stats = result.skill_result.delay_statistics;
            document.getElementById('maxDelayResult').textContent = formatTime(stats.max_delay_seconds);
            document.getElementById('avgDelayResult').textContent = formatTime(stats.avg_delay_seconds);
            document.getElementById('totalDelayResult').textContent = formatTime(stats.total_delay_seconds);
            document.getElementById('computeTimeResult').textContent = result.skill_result.computation_time.toFixed(2) + 's';
            
            // 评估对比
            const evalResult = result.eval_result;
            document.getElementById('optMaxDelay').textContent = formatTime(evalResult.proposed_metrics.max_delay_seconds);
            document.getElementById('baseMaxDelay').textContent = formatTime(evalResult.baseline_metrics.max_delay_seconds);
            document.getElementById('optAvgDelay').textContent = formatTime(evalResult.proposed_metrics.avg_delay_seconds);
            document.getElementById('baseAvgDelay').textContent = formatTime(evalResult.baseline_metrics.avg_delay_seconds);
            
            // 时刻表
            let tableHtml = '<table class="schedule-table"><thead><tr><th>车次</th><th>车站</th><th>到达</th><th>发车</th><th>延误</th></tr></thead><tbody>';
            for (let [trainId, stops] of Object.entries(result.skill_result.optimized_schedule)) {
                for (let stop of stops) {
                    const delay = stop.delay_seconds;
                    const delayClass = delay > 0 ? 'delay-red' : 'delay-green';
                    const delayText = delay > 0 ? '+' + delay + '秒' : '准点';
                    tableHtml += '<tr><td>' + trainId + '</td><td>' + stop.station_name + '</td><td>' + stop.arrival_time + '</td><td>' + stop.departure_time + '</td><td><span class="delay-tag ' + delayClass + '">' + delayText + '</span></td></tr>';
                }
            }
            tableHtml += '</tbody></table>';
            document.getElementById('scheduleTable').innerHTML = tableHtml;
        }

        // 显示运行图（调用后端API生成）
        function showDiagram(result) {
            document.getElementById('noDiagram').style.display = 'none';
            document.getElementById('diagramContent').style.display = 'block';

            const original = result.original_schedule;
            const optimized = result.skill_result.optimized_schedule;

            // 调用后端API生成运行图
            fetch('/api/diagram', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    original_schedule: original,
                    optimized_schedule: optimized
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    // 显示后端生成的图片
                    const html = `
                        <div style="text-align: center;">
                            <img src="data:image/png;base64,${data.diagram_image}"
                                 style="max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 8px;">
                        </div>
                    `;
                    document.getElementById('diagramContainer').innerHTML = html;
                } else {
                    document.getElementById('diagramContainer').innerHTML =
                        '<p style="color: red; text-align: center;">运行图生成失败: ' + data.message + '</p>';
                }
            })
            .catch(error => {
                console.error('Error:', error);
                document.getElementById('diagramContainer').innerHTML =
                    '<p style="color: red; text-align: center;">请求失败: ' + error + '</p>';
            });
        }

        // 保留原来的JS生成函数作为备用（现在不再使用）
        function generateClassicDiagram(schedule, stationCodes, stationNames, timeMin, timeMax) {
            // 拼音映射
            const pinyinMap = {
                "北京西": "BJP", "天津西": "TJG", "济南西": "JNZ",
                "南京南": "NJH", "上海虹桥": "SHH"
            };
            
            // 计算时间范围（对齐到10分钟）
            const tMin = Math.floor(timeMin / 10) * 10 - 30;
            const tMax = Math.floor(timeMax / 10) * 10 + 30;
            
            function timeToY(minutes) {
                return (minutes - tMin) / (tMax - tMin) * 100;
            }
            
            function stationToX(idx) {
                return idx / (stationCodes.length - 1) * 100;
            }
            
            // 生成时间刻度
            let timeTicks = '';
            for (let t = Math.ceil(tMin/10)*10; t <= tMax; t += 10) {
                const y = 100 - timeToY(t);
                const h = Math.floor(t / 60);
                const m = t % 60;
                timeTicks += `<div class="time-tick" style="top:${y}%;">${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}</div>`;
            }
            
            // 生成车站标签
            let stationLabels = '';
            stationCodes.forEach((code, idx) => {
                const x = stationToX(idx);
                const name = stationNames[code] || code;
                stationLabels += `<div class="station-label" style="left:${x}%;">${pinyinMap[name] || code}</div>`;
            });
            
            // 生成垂直网格线（时间刻度线）
            let vLines = '';
            for (let t = Math.ceil(tMin/10)*10; t <= tMax; t += 10) {
                const y = 100 - timeToY(t);
                vLines += `<div class="grid-v" style="top:30px;bottom:30px;left:${y}%;"></div>`;
            }

            // 生成水平网格线（车站横线）
            let hLines = '';
            stationCodes.forEach((_, idx) => {
                const x = stationToX(idx);
                hLines += `<div class="grid-h" style="left:10px;right:10px;top:${x}%;"></div>`;
            });

            // 生成运行线
            let trainLines = '';
            const colors = ['#E91E63', '#9C27B0', '#3F51B5', '#00BCD4', '#4CAF50', '#FF9800'];
            let trainIdx = 0;
            
            for (let [trainId, stops] of Object.entries(schedule)) {
                const color = colors[trainIdx % colors.length];
                trainIdx++;
                
                // 收集该列车的所有点
                let points = [];
                for (let stop of stops) {
                    if (!stationCodes.includes(stop.station_code)) continue;
                    const sIdx = stationCodes.indexOf(stop.station_code);
                    const arrMin = timeToMinutes(stop.arrival_time);
                    const depMin = timeToMinutes(stop.departure_time);
                    points.push({idx: sIdx, arr: arrMin, dep: depMin, delay: stop.delay_seconds || 0});
                }
                
                // 绘制运行线
                for (let i = 0; i < points.length; i++) {
                    const p = points[i];
                    const x = stationToX(p.idx);
                    const y = 100 - timeToY(p.dep);
                    
                    // 绘制车站点
                    trainLines += `<div class="train-dot" style="left:${x}%;top:${y}%;background:${color};"></div>`;
                    
                    // 绘制到下一站的斜线
                    if (i < points.length - 1) {
                        const nextP = points[i + 1];
                        const x1 = stationToX(p.idx);
                        const y1 = 100 - timeToY(p.dep);
                        const x2 = stationToX(nextP.idx);
                        const y2 = 100 - timeToY(nextP.arr);

                        // 计算斜线长度和角度
                        const dx = x2 - x1;
                        const dy = y1 - y2;

                        // 基础角度（负数，表示向右下倾斜）
                        const baseAngle = Math.atan2(dy, dx) * 180 / Math.PI;

                        // 根据延误调整斜率：延误越多，斜率越接近0（越平缓），最大为0（停车）
                        // 延误导致额外的时间，所以角度应该变大（更接近0）
                        const delayMinutes = p.delay / 60; // 当前站的延误（分钟）
                        const delayFactor = Math.min(delayMinutes / 60, 1); // 延误因子，最大1

                        // 计算调整后的角度：基础角度向0方向调整
                        // baseAngle是负数，向0调整意味着减去一个正数（变得更小/更接近0）
                        // 但我们希望斜率"变大"（更接近0），所以需要反向
                        // 实际上：dy越大（延误越多），角度应该越小（绝对值越大）
                        // 用户说"斜率变大"，如果指的是数值变大：-1 -> -0.5

                        // 重新理解：延误时，斜线应该更平缓（更接近水平）
                        // 即角度从负值向0变化，如-45度变成-30度
                        // 这需要将角度乘以一个小于1的正数
                        const adjustedAngle = baseAngle * (1 - delayFactor * 0.5);
                        // 限制角度范围：最大0（水平/停车），最小为基础角度
                        const finalAngle = Math.max(adjustedAngle, baseAngle);

                        // 计算调整后的长度（保持终点位置不变，调整角度）
                        // 新的dy' = dx * tan(finalAngle)
                        const newDy = dx * Math.tan(finalAngle * Math.PI / 180);
                        const length = Math.sqrt(dx*dx + newDy*newDy);

                        // 斜线从当前站发车点(x1, y1)开始
                        trainLines += `<div class="train-slope" style="
                            left:${x1}%;top:${y1}%;
                            width:${length}%;transform:rotate(${finalAngle}deg);
                            background:${color};
                        "></div>`;
                    }
                    
                    // 标注延误
                    if (p.delay > 0) {
                        const delayMin = Math.round(p.delay / 60);
                        const x = stationToX(p.idx);
                        const y = 100 - timeToY(p.dep);
                        trainLines += `<div class="delay-tag" style="left:${x+2}%;top:${y-15}px;">+${delayMin}min</div>`;
                    }
                }
                
                // 标注车次
                if (points.length > 0) {
                    const x = stationToX(points[0].idx) - 3;
                    const y = 100 - timeToY(points[0].dep) + 5;
                    trainLines += `<div class="train-name" style="left:${x}%;top:${y}%;color:${color};">${trainId}</div>`;
                }
            }
            
            return `
                <div class="classic-diagram">
                    <div class="time-axis">${timeTicks}</div>
                    <div class="station-axis-bottom">${stationLabels}</div>
                    <div class="grid-v-lines">${vLines}</div>
                    <div class="grid-h-lines">${hLines}</div>
                    <div class="train-lines">${trainLines}</div>
                </div>
            `;
        }
        
        // 辅助函数：时间转分钟
        function timeToMinutes(timeStr) {
            if (!timeStr) return 0;
            const [h, m, s] = timeStr.split(':').map(Number);
            return h * 60 + m + (s || 0);
        }
        
        // 格式化时间（秒转分钟秒）
        function formatTime(seconds) {
            if (seconds === undefined || seconds === null) return '-';
            const mins = Math.floor(seconds / 60);
            const secs = Math.round(seconds % 60);
            return mins + '分' + secs + '秒';
        }
        
        // 初始化场景参数显示
        document.getElementById('scenarioType').addEventListener('change', function() {
            const params = document.getElementById('speedLimitParams');
            if (this.value === 'temporary_speed_limit') {
                params.style.display = 'grid';
            } else {
                params.style.display = 'none';
            }
        });
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
        
        # 构建延误注入
        scenario_type = data.get('scenario_type', 'temporary_speed_limit')
        selected_trains = data.get('selected_trains', [])
        delay_config = data.get('delay_config', [])
        
        if scenario_type == 'temporary_speed_limit':
            delay_injection = DelayInjection.create_temporary_speed_limit(
                scenario_id="WEB_SC_001",
                train_delays=delay_config,
                limit_speed=data.get('limit_speed', 200),
                duration=data.get('duration', 120),
                affected_section="TJG -> JNZ"
            )
        else:
            delay_injection = DelayInjection.create_sudden_failure(
                scenario_id="WEB_SC_001",
                train_id=selected_trains[0] if selected_trains else "G1001",
                delay_seconds=delay_config[0]['delay_seconds'] if delay_config else 600,
                station_code=delay_config[0]['station_code'] if delay_config else "TJG",
                failure_type="vehicle_breakdown",
                repair_time=60
            )
        
        # Planner分析
        planner_output = planner.process(delay_injection)
        
        # 执行Skill
        skill_name = "temporary_speed_limit_skill" if scenario_type == "temporary_speed_limit" else "sudden_failure_skill"
        skill_result = execute_skill(
            skill_name=skill_name,
            skills=skills,
            train_ids=selected_trains,
            station_codes=station_codes,
            delay_injection=delay_injection.model_dump(),
            optimization_objective=data.get('objective', 'min_max_delay')
        )
        
        # 评估
        original_schedule = get_original_schedule()
        eval_result = evaluator.evaluate(
            proposed_schedule=skill_result.optimized_schedule,
            original_schedule=original_schedule,
            delay_injection=delay_injection.model_dump()
        )
        
        return jsonify({
            "success": True,
            "planner": {
                "recognized_scenario": planner_output.recognized_scenario.value,
                "delay_level": planner_output.delay_level.value,
                "confidence": planner_output.confidence_score
            },
            "skill_result": {
                "optimized_schedule": skill_result.optimized_schedule,
                "delay_statistics": skill_result.delay_statistics,
                "computation_time": skill_result.computation_time
            },
            "eval_result": {
                "proposed_metrics": {
                    "max_delay_seconds": eval_result.proposed_metrics.max_delay_seconds,
                    "avg_delay_seconds": eval_result.proposed_metrics.avg_delay_seconds
                },
                "baseline_metrics": {
                    "max_delay_seconds": eval_result.baseline_metrics.max_delay_seconds,
                    "avg_delay_seconds": eval_result.baseline_metrics.avg_delay_seconds
                }
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


if __name__ == '__main__':
    print("=" * 50)
    print("铁路调度Agent系统 v1.0")
    print("=" * 50)
    print("访问地址: http://localhost:8080")
    print("按 Ctrl+C 停止服务")
    print("=" * 50)
    app.run(host='0.0.0.0', port=8080, debug=True)
