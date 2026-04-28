# -*- coding: utf-8 -*-
import re

with open('e:/LLM-TTRA/test-agent/railway_dispatch/railway_agent/agents.py', 'r', encoding='utf-8') as f:
    content = f.read()

insert_marker = '    def analyze(self, delay_injection: Dict[str, Any], user_prompt: str = "",'
insert_idx = content.find(insert_marker)

if insert_idx == -1:
    print('ERROR: Could not find insert marker')
    exit(1)

new_code = '''    def _build_prompt_from_delay_injection(self, delay_injection: Dict[str, Any]) -> str:
        """
        将结构化 DelayInjection 转换为自然语言描述，供 handle() 统一入口使用
        【设计原则】所有入口最终都通过自然语言进入 handle()，确保 L1 提取的一致性
        """
        scenario_type = delay_injection.get("scenario_type", "")
        injected_delays = delay_injection.get("injected_delays", [])
        scenario_params = delay_injection.get("scenario_params", {})

        parts = []
        if scenario_type == "temporary_speed_limit":
            limit_speed = scenario_params.get("limit_speed_kmh", 200)
            duration = scenario_params.get("duration_minutes", 120)
            section = scenario_params.get("affected_section", "")
            parts.append(f"因天气原因导致{section}区间临时限速{limit_speed}km/h，预计持续{duration}分钟")
        elif scenario_type == "sudden_failure":
            if injected_delays:
                d = injected_delays[0]
                train_id = d.get("train_id", "")
                station = d.get("location", {}).get("station_code", "")
                delay_sec = d.get("initial_delay_seconds", 0)
                delay_min = delay_sec // 60
                parts.append(f"{train_id}在{station}站发生突发故障，预计延误{delay_min}分钟")
        elif scenario_type == "section_interrupt":
            if injected_delays:
                d = injected_delays[0]
                train_id = d.get("train_id", "")
                station = d.get("location", {}).get("station_code", "")
                delay_min = d.get("initial_delay_seconds", 0) // 60
                parts.append(f"{train_id}在{station}站附近区间因施工封锁，预计延误{delay_min}分钟")
        else:
            if injected_delays:
                d = injected_delays[0]
                train_id = d.get("train_id", "")
                station = d.get("location", {}).get("station_code", "")
                delay_min = d.get("initial_delay_seconds", 0) // 60
                parts.append(f"{train_id}在{station}站发生异常，预计延误{delay_min}分钟")

        return " ".join(parts) if parts else "请分析当前调度场景并生成调整方案"

    def _convert_handle_result_to_agent_result(self, handle_result: Dict[str, Any], model_used: str, computation_time: float) -> AgentResult:
        """
        将 handle() 返回的字典转换为 AgentResult，保持向后兼容
        """
        if not handle_result.get("success", False):
            return AgentResult(
                success=False,
                recognized_scenario=handle_result.get("recognized_scenario", "error"),
                selected_skill="",
                selected_solver="",
                reasoning="",
                llm_summary="",
                dispatch_result=None,
                model_response=handle_result.get("message", ""),
                computation_time=computation_time,
                model_used=model_used,
                error_message=handle_result.get("message", ""),
            )

        accident_card = handle_result.get("accident_card", {})
        dispatch_metrics = handle_result.get("dispatch_metrics", {})
        optimized_schedule = handle_result.get("optimized_schedule", {})
        selected_solver = handle_result.get("selected_solver", "unknown")

        dispatch_result = DispatchSkillOutput(
            optimized_schedule=optimized_schedule,
            delay_statistics=dispatch_metrics,
            computation_time=dispatch_metrics.get("computation_time", 0),
            success=True,
            message=handle_result.get("message", "调度完成"),
            skill_name="dispatch_solve_skill",
        )

        reasoning = handle_result.get("reasoning", "")
        return AgentResult(
            success=True,
            recognized_scenario=handle_result.get("recognized_scenario", "unknown"),
            selected_skill="dispatch_solve_skill",
            selected_solver=selected_solver,
            reasoning=reasoning,
            llm_summary=handle_result.get("message", ""),
            dispatch_result=dispatch_result,
            model_response=reasoning,
            computation_time=computation_time,
            model_used=model_used,
            evaluation_report=handle_result.get("evaluation_report", {}),
            natural_language_plan=handle_result.get("natural_language_plan", ""),
            operations_guide=handle_result.get("operations_guide", {}),
        )

    def analyze(self, delay_injection: Dict[str, Any], user_prompt: str = "",
                time_budget_seconds: float = 120.0) -> AgentResult:
        """
        【已弃用 / 内部委托】analyze() 现已委托给统一入口 handle()

        为保持向后兼容，本方法将结构化 delay_injection 转为自然语言 prompt，
        然后调用 handle() 执行完整 L1-L4 工作流。

        新代码请直接使用 agent.handle(user_input=...) 作为统一入口。
        """
        start_time = time.time()

        prompt = user_prompt or self._build_prompt_from_delay_injection(delay_injection)

        handle_result = self.handle(
            user_input=prompt,
            time_budget_seconds=time_budget_seconds,
        )

        computation_time = time.time() - start_time

        return self._convert_handle_result_to_agent_result(
            handle_result, self.model_name, computation_time
        )

'''

old_analyze_start = content.find(insert_marker)
analyze_with_comparison_marker = '\n    def analyze_with_comparison('
old_analyze_end = content.find(analyze_with_comparison_marker)

if old_analyze_start == -1 or old_analyze_end == -1:
    print('ERROR: Could not find boundaries')
    exit(1)

new_content = content[:old_analyze_start] + new_code + content[old_analyze_end:]

with open('e:/LLM-TTRA/test-agent/railway_dispatch/railway_agent/agents.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print('Successfully replaced analyze() method')
