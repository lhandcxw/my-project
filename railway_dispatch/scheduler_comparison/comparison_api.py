# -*- coding: utf-8 -*-
"""
铁路调度系统 - 调度比较API模块
提供Web API接口，用于调度方法的比较和优选
"""

from typing import Dict, List, Any, Optional
from flask import Blueprint, request, jsonify
import logging

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.data_models import DelayInjection, ScenarioType, InjectedDelay, DelayLocation
from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
from scheduler_comparison import (
    SchedulerComparator,
    ComparisonCriteria,
    create_comparator,
    LLMOutputAdapter,
    LLMOutputFormat
)

logger = logging.getLogger(__name__)

# 创建蓝图
comparison_bp = Blueprint('comparison', __name__, url_prefix='/api/comparison')

# 全局比较器实例（延迟初始化）
_comparator = None
_llm_adapter = None


def get_comparator():
    """获取或创建比较器实例"""
    global _comparator
    
    if _comparator is None:
        use_real_data(True)
        trains = get_trains_pydantic()[:50]  # 限制列车数量
        stations = get_stations_pydantic()
        _comparator = create_comparator(trains, stations)
        logger.info(f"比较器初始化完成，已注册调度器: {_comparator.list_schedulers()}")
    
    return _comparator


def get_llm_adapter():
    """获取LLM适配器实例"""
    global _llm_adapter
    
    if _llm_adapter is None:
        _llm_adapter = LLMOutputAdapter(output_format=LLMOutputFormat.MARKDOWN)
    
    return _llm_adapter


@comparison_bp.route('/schedulers', methods=['GET'])
def list_schedulers():
    """
    列出所有可用的调度器
    
    Returns:
        JSON: 可用调度器列表
    """
    comparator = get_comparator()
    schedulers = comparator.list_schedulers()
    
    return jsonify({
        "success": True,
        "schedulers": schedulers,
        "count": len(schedulers)
    })


@comparison_bp.route('/criteria', methods=['GET'])
def list_criteria():
    """
    列出所有可用的比较准则
    
    Returns:
        JSON: 比较准则列表
    """
    criteria = [
        {
            "id": c.value,
            "name": c.value,
            "description": _get_criteria_description(c)
        }
        for c in ComparisonCriteria
    ]
    
    return jsonify({
        "success": True,
        "criteria": criteria
    })


def _get_criteria_description(criteria: ComparisonCriteria) -> str:
    """获取比较准则描述"""
    descriptions = {
        ComparisonCriteria.MIN_MAX_DELAY: "优先最小化最大延误时间",
        ComparisonCriteria.MIN_AVG_DELAY: "优先最小化平均延误时间",
        ComparisonCriteria.MIN_TOTAL_DELAY: "优先最小化总延误时间",
        ComparisonCriteria.MAX_ON_TIME_RATE: "优先最大化准点率",
        ComparisonCriteria.MIN_AFFECTED_TRAINS: "优先最小化受影响列车数",
        ComparisonCriteria.BALANCED: "均衡考虑各项指标",
        ComparisonCriteria.REAL_TIME: "优先考虑计算速度，适合实时调度"
    }
    return descriptions.get(criteria, "")


@comparison_bp.route('/compare', methods=['POST'])
def compare_schedulers():
    """
    比较不同调度器
    
    Request JSON:
        {
            "train_id": "G1563",
            "station_code": "BDD",
            "delay_seconds": 1200,
            "criteria": "balanced",  // 可选
            "scenario_type": "temporary_speed_limit",  // 可选
            "schedulers": ["fcfs", "mip"]  // 可选，默认全部
        }
    
    Returns:
        JSON: 比较结果
    """
    try:
        data = request.json
        
        # 解析参数
        train_id = data.get('train_id')
        station_code = data.get('station_code')
        delay_seconds = data.get('delay_seconds', 1200)
        criteria_str = data.get('criteria', 'balanced')
        scenario_type_str = data.get('scenario_type', 'temporary_speed_limit')
        scheduler_names = data.get('schedulers')
        
        # 获取比较器
        comparator = get_comparator()
        
        # 构建延误注入
        scenario_type = ScenarioType.TEMPORARY_SPEED_LIMIT
        if scenario_type_str == 'sudden_failure':
            scenario_type = ScenarioType.SUDDEN_FAILURE
        
        delay_injection = DelayInjection(
            scenario_type=scenario_type,
            scenario_id="API_COMPARE",
            injected_delays=[
                InjectedDelay(
                    train_id=train_id,
                    location=DelayLocation(
                        location_type="station",
                        station_code=station_code
                    ),
                    initial_delay_seconds=delay_seconds,
                    timestamp="2024-01-15T10:00:00Z"
                )
            ],
            affected_trains=[train_id]
        )
        
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
        criteria = criteria_map.get(criteria_str, ComparisonCriteria.BALANCED)
        
        # 执行比较
        result = comparator.compare_all(
            delay_injection,
            criteria=criteria,
            scheduler_names=scheduler_names
        )
        
        # 获取LLM适配器并生成输出
        adapter = get_llm_adapter()
        
        return jsonify({
            "success": result.success,
            "comparison": adapter.generate_structured_output(result),
            "markdown_report": adapter.adapt(result, LLMOutputFormat.MARKDOWN),
            "summary": adapter.adapt(result, LLMOutputFormat.SUMMARY)
        })
        
    except Exception as e:
        logger.exception(f"比较调度器失败: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@comparison_bp.route('/recommend', methods=['POST'])
def get_recommendation():
    """
    获取推荐方案
    
    Request JSON:
        {
            "train_id": "G1563",
            "station_code": "BDD",
            "delay_seconds": 1200,
            "user_preference": "我希望最小化最大延误",
            "scenario_type": "temporary_speed_limit"
        }
    
    Returns:
        JSON: 推荐结果
    """
    try:
        data = request.json
        
        # 解析参数
        train_id = data.get('train_id')
        station_code = data.get('station_code')
        delay_seconds = data.get('delay_seconds', 1200)
        user_preference = data.get('user_preference', 'balanced')
        scenario_type_str = data.get('scenario_type', 'temporary_speed_limit')
        
        # 根据用户偏好确定比较准则
        preference_to_criteria = {
            '最小最大延误': ComparisonCriteria.MIN_MAX_DELAY,
            'min_max_delay': ComparisonCriteria.MIN_MAX_DELAY,
            '最小平均延误': ComparisonCriteria.MIN_AVG_DELAY,
            'min_avg_delay': ComparisonCriteria.MIN_AVG_DELAY,
            '准点率': ComparisonCriteria.MAX_ON_TIME_RATE,
            '实时': ComparisonCriteria.REAL_TIME,
            'real_time': ComparisonCriteria.REAL_TIME,
            '均衡': ComparisonCriteria.BALANCED,
            'balanced': ComparisonCriteria.BALANCED
        }
        
        criteria = preference_to_criteria.get(user_preference, ComparisonCriteria.BALANCED)
        
        # 获取比较器
        comparator = get_comparator()
        
        # 构建延误注入
        scenario_type = ScenarioType.TEMPORARY_SPEED_LIMIT
        if scenario_type_str == 'sudden_failure':
            scenario_type = ScenarioType.SUDDEN_FAILURE
        
        delay_injection = DelayInjection(
            scenario_type=scenario_type,
            scenario_id="API_RECOMMEND",
            injected_delays=[
                InjectedDelay(
                    train_id=train_id,
                    location=DelayLocation(
                        location_type="station",
                        station_code=station_code
                    ),
                    initial_delay_seconds=delay_seconds,
                    timestamp="2024-01-15T10:00:00Z"
                )
            ],
            affected_trains=[train_id]
        )
        
        # 执行比较
        result = comparator.compare_all(delay_injection, criteria=criteria)
        
        # 生成LLM提示词
        adapter = get_llm_adapter()
        llm_prompt = adapter.generate_llm_prompt(
            result,
            f"{train_id}在{station_code}延误{delay_seconds // 60}分钟，{user_preference}"
        )
        
        return jsonify({
            "success": result.success,
            "recommendation": adapter.generate_structured_output(result),
            "llm_prompt": llm_prompt,
            "llm_context": {
                "winner": result.winner.scheduler_name if result.winner else None,
                "criteria": criteria.value,
                "recommendations": result.recommendations
            }
        })
        
    except Exception as e:
        logger.exception(f"获取推荐失败: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@comparison_bp.route('/llm_prompt', methods=['POST'])
def generate_llm_prompt():
    """
    生成供大模型使用的Prompt
    
    Request JSON:
        {
            "comparison_result": {...},  // 比较结果（可选）
            "user_query": "用户原始问题",
            "additional_context": "额外上下文"  // 可选
        }
    
    Returns:
        JSON: LLM Prompt
    """
    try:
        data = request.json
        
        # 如果提供了比较结果，直接使用
        # 否则重新执行比较
        user_query = data.get('user_query', '')
        additional_context = data.get('additional_context')
        
        # 解析用户查询中的参数（简单解析）
        import re
        train_pattern = r'([GDCTKZ]\d+)'
        delay_pattern = r'(\d+)\s*分钟'
        
        train_ids = re.findall(train_pattern, user_query)
        delays = re.findall(delay_pattern, user_query)
        
        if not train_ids:
            return jsonify({
                "success": False,
                "message": "无法从查询中识别列车号"
            })
        
        train_id = train_ids[0]
        delay_seconds = int(delays[0]) * 60 if delays else 1200
        
        # 执行比较
        comparator = get_comparator()
        
        delay_injection = DelayInjection(
            scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
            scenario_id="LLM_PROMPT",
            injected_delays=[
                InjectedDelay(
                    train_id=train_id,
                    location=DelayLocation(
                        location_type="station",
                        station_code="BDD"  # 默认保定东
                    ),
                    initial_delay_seconds=delay_seconds,
                    timestamp="2024-01-15T10:00:00Z"
                )
            ],
            affected_trains=[train_id]
        )
        
        result = comparator.compare_all(delay_injection)
        
        # 生成LLM Prompt
        adapter = get_llm_adapter()
        prompt = adapter.generate_llm_prompt(result, user_query, additional_context)
        
        return jsonify({
            "success": True,
            "prompt": prompt,
            "comparison_summary": adapter.adapt(result, LLMOutputFormat.SUMMARY)
        })
        
    except Exception as e:
        logger.exception(f"生成LLM Prompt失败: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


def register_comparison_routes(app):
    """
    将比较API路由注册到Flask应用
    
    Args:
        app: Flask应用实例
    """
    app.register_blueprint(comparison_bp)
    logger.info("调度比较API路由已注册")


# 测试代码
if __name__ == "__main__":
    from flask import Flask
    
    app = Flask(__name__)
    register_comparison_routes(app)
    
    print("调度比较API测试")
    print("可用路由:")
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith('comparison.'):
            print(f"  {rule.methods} {rule.rule}")
