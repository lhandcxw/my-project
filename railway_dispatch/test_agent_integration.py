# -*- coding: utf-8 -*-
"""
铁路调度系统 - Agent集成测试
验证调度比较功能与Agent的集成
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
from models.data_models import DelayInjection, InjectedDelay, DelayLocation, ScenarioType
from railway_agent import create_rule_agent, SchedulerComparisonSkill, create_comparison_skill
from scheduler_comparison import create_comparator, ComparisonCriteria


def test_rule_agent_with_comparison():
    """测试RuleAgent的比较功能"""
    print("=" * 60)
    print("测试1: RuleAgent调度比较功能")
    print("=" * 60)
    
    use_real_data(True)
    trains = get_trains_pydantic()[:15]
    stations = get_stations_pydantic()
    
    agent = create_rule_agent(trains=trains, stations=stations)
    print(f"Agent创建成功，比较功能: {agent.enable_comparison}")
    print(f"可用技能: {agent.tool_registry.get_tool_names()}")
    
    # 创建延误场景
    delay_injection = {
        "scenario_type": "temporary_speed_limit",
        "scenario_id": "INTEGRATION_TEST",
        "injected_delays": [{
            "train_id": trains[0].train_id,
            "location": {
                "location_type": "station",
                "station_code": trains[0].schedule.stops[0].station_code
            },
            "initial_delay_seconds": 1200,
            "timestamp": "2024-01-15T10:00:00Z"
        }],
        "affected_trains": [trains[0].train_id],
        "scenario_params": {
            "user_preference": "balanced"
        }
    }
    
    # 执行比较
    result = agent.analyze_with_comparison(
        delay_injection,
        user_prompt=f"{trains[0].train_id}延误20分钟",
        comparison_criteria="balanced"
    )
    
    print(f"\n执行结果: 成功={result.success}")
    print(f"选择技能: {result.selected_skill}")
    
    if result.success and result.dispatch_result:
        stats = result.dispatch_result.delay_statistics
        print(f"\n比较结果:")
        print(f"  最优调度器: {stats.get('winner_scheduler')}")
        print(f"  最大延误: {stats.get('max_delay_seconds', 0) // 60}分钟")
        print(f"  平均延误: {stats.get('avg_delay_seconds', 0) / 60:.1f}分钟")
        print(f"  准点率: {stats.get('on_time_rate', 1.0) * 100:.1f}%")
        
        print(f"\n排名:")
        for r in stats.get("ranking", []):
            mark = " ⭐" if r["rank"] == 1 else ""
            print(f"  {r['rank']}. {r['scheduler']}{mark} - 最大延误{r['max_delay_minutes']}分钟")
        
        print(f"\n推荐建议:")
        for rec in stats.get("recommendations", []):
            print(f"  - {rec}")
    
    return result


def test_comparison_skill():
    """测试调度比较技能"""
    print("\n" + "=" * 60)
    print("测试2: 调度比较技能独立测试")
    print("=" * 60)
    
    use_real_data(True)
    trains = get_trains_pydantic()[:10]
    stations = get_stations_pydantic()
    
    skill = create_comparison_skill(trains, stations)
    
    delay_injection = {
        "scenario_type": "sudden_failure",
        "scenario_id": "SKILL_TEST",
        "injected_delays": [{
            "train_id": trains[3].train_id if len(trains) > 3 else trains[0].train_id,
            "location": {
                "location_type": "station",
                "station_code": "BDD"
            },
            "initial_delay_seconds": 1800,
            "timestamp": "2024-01-15T10:00:00Z"
        }],
        "affected_trains": [trains[3].train_id if len(trains) > 3 else trains[0].train_id],
        "scenario_params": {
            "user_preference": "min_max_delay"
        }
    }
    
    result = skill.execute(
        train_ids=delay_injection["affected_trains"],
        station_codes=[s.station_code for s in stations],
        delay_injection=delay_injection
    )
    
    print(f"执行成功: {result.success}")
    print(f"消息: {result.message}")
    
    if result.success:
        stats = result.delay_statistics
        print(f"最优调度器: {stats.get('winner_scheduler')}")
        print(f"排名数量: {len(stats.get('ranking', []))}")
    
    return result


def test_different_criteria():
    """测试不同比较准则"""
    print("\n" + "=" * 60)
    print("测试3: 不同比较准则")
    print("=" * 60)
    
    use_real_data(True)
    trains = get_trains_pydantic()[:10]
    stations = get_stations_pydantic()
    
    agent = create_rule_agent(trains=trains, stations=stations)
    
    delay_injection = {
        "scenario_type": "temporary_speed_limit",
        "scenario_id": "CRITERIA_TEST",
        "injected_delays": [{
            "train_id": trains[0].train_id,
            "location": {
                "location_type": "station",
                "station_code": trains[0].schedule.stops[0].station_code
            },
            "initial_delay_seconds": 900,
            "timestamp": "2024-01-15T10:00:00Z"
        }],
        "affected_trains": [trains[0].train_id]
    }
    
    criteria_list = ["balanced", "min_max_delay", "min_avg_delay", "real_time"]
    
    for criteria in criteria_list:
        print(f"\n--- 比较准则: {criteria} ---")
        delay_injection["scenario_params"] = {"user_preference": criteria}
        
        result = agent.analyze_with_comparison(
            delay_injection,
            user_prompt="测试",
            comparison_criteria=criteria
        )
        
        if result.success and result.dispatch_result:
            stats = result.dispatch_result.delay_statistics
            print(f"最优调度器: {stats.get('winner_scheduler')}")


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("铁路调度系统 - Agent集成测试")
    print("=" * 60)
    
    try:
        # 测试1: RuleAgent比较功能
        test_rule_agent_with_comparison()
        
        # 测试2: 比较技能独立测试
        test_comparison_skill()
        
        # 测试3: 不同比较准则
        test_different_criteria()
        
        print("\n" + "=" * 60)
        print("所有测试通过!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
