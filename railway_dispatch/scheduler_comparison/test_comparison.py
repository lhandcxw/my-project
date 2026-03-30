# -*- coding: utf-8 -*-
"""
铁路调度系统 - 调度方法比较测试
演示如何使用调度比较框架进行多方法对比和优选
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
from models.data_models import (
    InjectedDelay, DelayLocation, ScenarioType, DelayInjection, Train, Station
)
from scheduler_comparison import (
    SchedulerComparator,
    ComparisonCriteria,
    create_comparator,
    LLMOutputAdapter,
    LLMOutputFormat
)


def test_basic_comparison():
    """基本比较功能测试"""
    print("=" * 80)
    print("测试1: 基本比较功能")
    print("=" * 80)
    
    # 加载数据
    use_real_data(True)
    trains = get_trains_pydantic()[:20]
    stations = get_stations_pydantic()
    
    print(f"加载数据: {len(trains)}列列车, {len(stations)}个车站")
    
    # 创建比较器
    comparator = create_comparator(trains, stations)
    print(f"已注册调度器: {comparator.list_schedulers()}")
    
    # 创建延误场景
    delay_injection = DelayInjection(
        scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
        scenario_id="TEST_BASIC",
        injected_delays=[
            InjectedDelay(
                train_id=trains[0].train_id,
                location=DelayLocation(
                    location_type="station",
                    station_code=trains[0].schedule.stops[0].station_code
                ),
                initial_delay_seconds=1200,  # 20分钟
                timestamp="2024-01-15T10:00:00Z"
            )
        ],
        affected_trains=[trains[0].train_id]
    )
    
    # 执行比较
    result = comparator.compare_all(delay_injection)
    
    print("\n" + result.get_ranking_table())
    
    if result.winner:
        print(f"\n最优方案: {result.winner.scheduler_name}")
        m = result.winner.result.metrics
        print(f"最大延误: {m.max_delay_seconds // 60} 分钟")
        print(f"平均延误: {m.avg_delay_seconds / 60:.1f} 分钟")
        print(f"受影响列车: {m.affected_trains_count} 列")
    
    return result


def test_criteria_comparison():
    """不同比较准则测试"""
    print("\n" + "=" * 80)
    print("测试2: 不同比较准则")
    print("=" * 80)
    
    use_real_data(True)
    trains = get_trains_pydantic()[:15]
    stations = get_stations_pydantic()
    
    comparator = create_comparator(trains, stations)
    
    delay_injection = DelayInjection(
        scenario_type=ScenarioType.SUDDEN_FAILURE,
        scenario_id="TEST_CRITERIA",
        injected_delays=[
            InjectedDelay(
                train_id=trains[5].train_id if len(trains) > 5 else trains[0].train_id,
                location=DelayLocation(
                    location_type="station",
                    station_code="BDD"  # 保定东
                ),
                initial_delay_seconds=1800,  # 30分钟
                timestamp="2024-01-15T10:00:00Z"
            )
        ],
        affected_trains=[trains[5].train_id if len(trains) > 5 else trains[0].train_id]
    )
    
    # 测试不同准则
    criteria_list = [
        ComparisonCriteria.MIN_MAX_DELAY,
        ComparisonCriteria.MIN_AVG_DELAY,
        ComparisonCriteria.BALANCED,
        ComparisonCriteria.REAL_TIME
    ]
    
    for criteria in criteria_list:
        print(f"\n--- 比较准则: {criteria.value} ---")
        result = comparator.compare_all(delay_injection, criteria=criteria)
        
        if result.winner:
            print(f"最优方案: {result.winner.scheduler_name}")
            m = result.winner.result.metrics
            print(f"最大延误: {m.max_delay_seconds // 60}分钟, "
                  f"平均延误: {m.avg_delay_seconds / 60:.1f}分钟, "
                  f"计算时间: {m.computation_time:.2f}秒")


def test_llm_output():
    """大模型输出格式测试"""
    print("\n" + "=" * 80)
    print("测试3: 大模型输出格式")
    print("=" * 80)
    
    use_real_data(True)
    trains = get_trains_pydantic()[:10]
    stations = get_stations_pydantic()
    
    comparator = create_comparator(trains, stations)
    
    delay_injection = DelayInjection(
        scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
        scenario_id="TEST_LLM",
        injected_delays=[
            InjectedDelay(
                train_id=trains[0].train_id,
                location=DelayLocation(
                    location_type="station",
                    station_code=trains[0].schedule.stops[0].station_code
                ),
                initial_delay_seconds=900,  # 15分钟
                timestamp="2024-01-15T10:00:00Z"
            )
        ],
        affected_trains=[trains[0].train_id]
    )
    
    result = comparator.compare_all(delay_injection)
    
    # 创建LLM输出适配器
    adapter = LLMOutputAdapter()
    
    # 测试不同输出格式
    print("\n--- 摘要格式 ---")
    print(adapter.adapt(result, LLMOutputFormat.SUMMARY))
    
    print("\n--- 结构化文本格式 ---")
    print(adapter.adapt(result, LLMOutputFormat.STRUCTURED_TEXT))
    
    print("\n--- Markdown格式 (部分) ---")
    md_output = adapter.adapt(result, LLMOutputFormat.MARKDOWN)
    print(md_output[:1000] + "..." if len(md_output) > 1000 else md_output)
    
    # 生成LLM Prompt
    print("\n--- LLM Prompt示例 ---")
    prompt = adapter.generate_llm_prompt(
        result,
        "G1234在保定东延误了15分钟，帮我选择一个最优的调度方案"
    )
    print(prompt[:1500] + "..." if len(prompt) > 1500 else prompt)


def test_multi_train_delay():
    """多列车延误场景测试"""
    print("\n" + "=" * 80)
    print("测试4: 多列车延误场景")
    print("=" * 80)
    
    use_real_data(True)
    trains = get_trains_pydantic()[:25]
    stations = get_stations_pydantic()
    
    comparator = create_comparator(trains, stations)
    
    # 创建多列车延误场景
    injected_delays = []
    affected_trains = []
    
    # 模拟临时限速影响多列列车
    for i, train in enumerate(trains[:3]):
        injected_delays.append(
            InjectedDelay(
                train_id=train.train_id,
                location=DelayLocation(
                    location_type="station",
                    station_code=train.schedule.stops[0].station_code
                ),
                initial_delay_seconds=600 + i * 300,  # 10分钟, 15分钟, 20分钟
                timestamp="2024-01-15T10:00:00Z"
            )
        )
        affected_trains.append(train.train_id)
    
    delay_injection = DelayInjection(
        scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
        scenario_id="TEST_MULTI",
        injected_delays=injected_delays,
        affected_trains=affected_trains
    )
    
    print(f"延误注入: {len(injected_delays)}列列车")
    for d in injected_delays:
        print(f"  - {d.train_id}: {d.initial_delay_seconds // 60}分钟")
    
    result = comparator.compare_all(delay_injection)
    
    print("\n" + result.get_ranking_table())
    
    if result.winner:
        print(f"\n推荐方案: {result.winner.scheduler_name}")
        for rec in result.recommendations:
            print(f"  - {rec}")


def test_structured_output():
    """结构化输出测试（适合API返回）"""
    print("\n" + "=" * 80)
    print("测试5: 结构化输出（API格式）")
    print("=" * 80)
    
    use_real_data(True)
    trains = get_trains_pydantic()[:10]
    stations = get_stations_pydantic()
    
    comparator = create_comparator(trains, stations)
    
    delay_injection = DelayInjection(
        scenario_type=ScenarioType.SUDDEN_FAILURE,
        scenario_id="TEST_API",
        injected_delays=[
            InjectedDelay(
                train_id=trains[0].train_id,
                location=DelayLocation(
                    location_type="station",
                    station_code=trains[0].schedule.stops[0].station_code
                ),
                initial_delay_seconds=1200,
                timestamp="2024-01-15T10:00:00Z"
            )
        ],
        affected_trains=[trains[0].train_id]
    )
    
    result = comparator.compare_all(delay_injection)
    
    # 获取结构化输出
    adapter = LLMOutputAdapter()
    structured = adapter.generate_structured_output(result)
    
    import json
    print(json.dumps(structured, ensure_ascii=False, indent=2))


def main():
    """运行所有测试"""
    print("\n" + "=" * 80)
    print("铁路调度系统 - 调度方法比较框架测试")
    print("=" * 80)
    
    try:
        # 测试1: 基本比较
        test_basic_comparison()
        
        # 测试2: 不同准则
        test_criteria_comparison()
        
        # 测试3: LLM输出
        test_llm_output()
        
        # 测试4: 多列车延误
        test_multi_train_delay()
        
        # 测试5: 结构化输出
        test_structured_output()
        
        print("\n" + "=" * 80)
        print("所有测试完成!")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n测试出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
