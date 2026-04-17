# -*- coding: utf-8 -*-
"""
Prompt管理器模块
统一管理所有LLM Prompt模板，为微调和prompt工程提供支持
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
import json

from models.prompts import (
    PromptTemplate,
    PromptTemplateType,
    PromptContext,
    PromptRequest,
    PromptResponse,
    FineTuningSample
)

logger = logging.getLogger(__name__)


class PromptManager:
    """
    Prompt管理器
    集中管理所有Prompt模板，提供模板注册、检索、填充等功能
    """

    def __init__(self):
        """初始化Prompt管理器"""
        self._templates: Dict[str, PromptTemplate] = {}
        self._fine_tuning_samples: Dict[str, FineTuningSample] = {}
        self._initialize_builtin_templates()

    def register_template(self, template: PromptTemplate):
        """
        注册Prompt模板

        Args:
            template: Prompt模板对象
        """
        self._templates[template.template_id] = template
        logger.debug(f"注册Prompt模板: {template.template_id} - {template.template_name}")

    def get_template(self, template_id: str) -> Optional[PromptTemplate]:
        """
        获取Prompt模板

        Args:
            template_id: 模板ID

        Returns:
            PromptTemplate: 模板对象，不存在返回None
        """
        return self._templates.get(template_id)

    def list_templates(self, template_type: Optional[PromptTemplateType] = None) -> List[PromptTemplate]:
        """
        列出所有模板

        Args:
            template_type: 可选，按类型过滤

        Returns:
            List[PromptTemplate]: 模板列表
        """
        templates = list(self._templates.values())
        if template_type:
            templates = [t for t in templates if t.template_type == template_type]
        return templates

    def fill_template(
        self,
        template_id: str,
        context: PromptContext,
        enable_rag: bool = False,
        rag_knowledge: Optional[List[str]] = None
    ) -> str:
        """
        填充Prompt模板

        Args:
            template_id: 模板ID
            context: Prompt上下文
            enable_rag: 是否启用RAG
            rag_knowledge: RAG知识库内容

        Returns:
            str: 填充后的完整Prompt
        """
        template = self.get_template(template_id)
        if template is None:
            raise ValueError(f"模板不存在: {template_id}")

        # 构建基础上下文字典
        context_dict = self._build_context_dict(context)

        # 添加RAG知识
        if enable_rag and rag_knowledge:
            context_dict["rag_knowledge"] = "\n".join(rag_knowledge)
            if context.rag_documents:
                context_dict["rag_documents"] = self._format_rag_documents(context.rag_documents)
        else:
            context_dict["rag_knowledge"] = ""
            context_dict["rag_documents"] = ""

        # 确保所有模板变量都有默认值
        all_vars = ["user_input", "request_id", "scene_type", "scene_category", "source_type",
                   "canonical_request", "accident_card", "network_snapshot", "dispatch_context",
                   "solver_result", "execution_result", "rag_knowledge", "rag_documents",
                   "scenario_features"]
        for var in all_vars:
            if var not in context_dict:
                context_dict[var] = ""

        # 对所有字符串值进行转义，防止模板中的花括号与 .format() 冲突
        # 创建一份转义后的字典
        escaped_dict = {}
        for key, value in context_dict.items():
            if isinstance(value, str):
                # 转义花括号：{ -> {{, } -> }}
                escaped_dict[key] = value.replace('{', '{{').replace('}', '}}')
            else:
                escaped_dict[key] = value

        try:
            filled_prompt = template.user_prompt_template.format(**escaped_dict)
        except (KeyError, ValueError) as e:
            # 详细记录错误信息以便调试
            import re
            error_str = str(e)
            # 提取缺失的变量名，处理各种错误格式
            missing_var_match = re.search(r'["\']([^"\']+)["\']', error_str)
            missing_var = missing_var_match.group(1) if missing_var_match else "unknown"
            logger.error(f"模板填充失败: {e}")
            logger.error(f"缺失变量: {missing_var}")
            logger.error(f"上下文中已有变量: {list(context_dict.keys())}")
            # 再次检查并补充缺失变量
            if missing_var in context_dict:
                context_dict[missing_var] = ""
                # 重建转义字典
                escaped_retry = {}
                for key, value in context_dict.items():
                    if isinstance(value, str):
                        escaped_retry[key] = value.replace('{', '{{').replace('}', '}}')
                    else:
                        escaped_retry[key] = value
                try:
                    filled_prompt = template.user_prompt_template.format(**escaped_retry)
                    logger.info(f"补充缺失变量后填充成功: {missing_var}")
                except Exception as e2:
                    logger.error(f"补充变量后仍失败: {e2}")
                    raise
            else:
                raise

        # 组合系统提示和用户提示
        if template.system_prompt:
            full_prompt = f"{template.system_prompt}\n\n{filled_prompt}"
        else:
            full_prompt = filled_prompt

        return full_prompt

    def validate_output(
        self,
        template_id: str,
        output: Dict[str, Any]
    ) -> Tuple[bool, List[str]]:
        """
        验证输出是否符合模板要求

        Args:
            template_id: 模板ID
            output: 输出字典

        Returns:
            Tuple[bool, List[str]]: (是否有效, 错误列表)
        """
        template = self.get_template(template_id)
        if template is None:
            return False, [f"模板不存在: {template_id}"]

        errors = []

        # 检查必需字段
        for field in template.required_output_fields:
            if field not in output or output[field] is None:
                errors.append(f"缺少必需字段: {field}")

        # 检查Schema（如果定义）
        if template.output_schema:
            schema_errors = self._validate_schema(output, template.output_schema)
            errors.extend(schema_errors)

        return len(errors) == 0, errors

    def collect_fine_tuning_sample(
        self,
        template_id: str,
        context: PromptContext,
        expected_output: Dict[str, Any],
        model_output: Optional[Dict[str, Any]] = None
    ) -> FineTuningSample:
        """
        收集微调样本

        Args:
            template_id: 模板ID
            context: 输入上下文
            expected_output: 期望输出
            model_output: 模型输出（可选）

        Returns:
            FineTuningSample: 微调样本对象
        """
        import uuid

        sample = FineTuningSample(
            sample_id=str(uuid.uuid4()),
            template_id=template_id,
            input_context=context,
            user_input=context.user_input or "",
            expected_output=expected_output,
            model_output=model_output,
            annotation_status="pending",
            created_at=datetime.now().isoformat()
        )

        self._fine_tuning_samples[sample.sample_id] = sample
        logger.info(f"收集微调样本: {sample.sample_id}")
        return sample

    def export_fine_tuning_samples(self, filepath: str):
        """
        导出微调样本为JSONL格式（用于微调）

        Args:
            filepath: 导出文件路径
        """
        samples = [s.model_dump() for s in self._fine_tuning_samples.values()
                  if s.annotation_status in ["completed", "in_progress"]]

        with open(filepath, 'w', encoding='utf-8') as f:
            for sample in samples:
                f.write(json.dumps(sample, ensure_ascii=False) + '\n')

        logger.info(f"导出 {len(samples)} 个微调样本到 {filepath}")

    def _build_context_dict(self, context: PromptContext) -> Dict[str, Any]:
        """
        从PromptContext构建字典

        Args:
            context: Prompt上下文

        Returns:
            Dict: 上下文字典
        """
        def json_serial(obj):
            """JSON序列化辅助函数，处理datetime等特殊类型"""
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Type {type(obj)} not serializable")

        context_dict = {
            "user_input": context.user_input or "",
            "request_id": context.request_id or "",
            "scene_type": context.scene_type or "",
            "scene_category": context.scene_category or "",
            "source_type": context.source_type or "",
        }

        # 添加复杂对象 - 使用安全的JSON序列化
        def safe_json_dump(obj):
            """安全地将对象转为JSON字符串"""
            if obj is None:
                return "{}"
            try:
                return json.dumps(obj, ensure_ascii=False, indent=2, default=json_serial)
            except Exception:
                return str(obj) if obj else "{}"

        context_dict["canonical_request"] = safe_json_dump(context.canonical_request)
        context_dict["accident_card"] = safe_json_dump(context.accident_card)
        context_dict["network_snapshot"] = safe_json_dump(context.network_snapshot)
        context_dict["dispatch_context"] = safe_json_dump(context.dispatch_context)
        context_dict["solver_result"] = safe_json_dump(context.solver_result)
        context_dict["execution_result"] = safe_json_dump(context.execution_result)

        # 添加额外变量
        if context.variables:
            context_dict.update(context.variables)

        return context_dict

    def _format_rag_documents(self, documents: List[Dict[str, Any]]) -> str:
        """
        格式化RAG文档

        Args:
            documents: RAG文档列表

        Returns:
            str: 格式化后的文本
        """
        if not documents:
            return ""

        formatted = ["相关领域知识："]
        for i, doc in enumerate(documents, 1):
            formatted.append(f"\n【文档{i}】")
            if "content" in doc:
                formatted.append(doc["content"])
            if "metadata" in doc:
                formatted.append(f"(来源: {doc['metadata'].get('source', '未知')})")

        return "\n".join(formatted)

    def _validate_schema(self, output: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
        """
        简单的Schema验证

        Args:
            output: 输出字典
            schema: Schema定义

        Returns:
            List[str]: 错误列表
        """
        errors = []

        # 检查必需字段
        for field, field_schema in schema.items():
            if field_schema.get("required", False) and field not in output:
                errors.append(f"Schema要求字段: {field}")

        # 检查字段类型
        for field, field_schema in schema.items():
            if field in output:
                expected_type = field_schema.get("type")
                if expected_type == "array" and not isinstance(output[field], list):
                    errors.append(f"字段 {field} 应为数组")
                elif expected_type == "object" and not isinstance(output[field], dict):
                    errors.append(f"字段 {field} 应为对象")

        return errors

    def _initialize_builtin_templates(self):
        """初始化内置Prompt模板"""
        # L0预处理模板
        l0_template = PromptTemplate(
            template_id="l0_preprocess_extractor",
            template_type=PromptTemplateType.L0_PREPROCESS,
            template_name="L0预处理提取器",
            description="从用户输入中提取调度信息",
            system_prompt="你是一个专业的铁路调度助手，负责从调度员的描述中提取关键信息。必须只输出JSON格式，不要添加任何解释文字。",
            user_prompt_template="""从铁路调度描述中提取信息，只输出JSON，不要添加任何解释或markdown标记。

描述：{user_input}

车站名称到站码的映射：
- 北京西 -> BJX, 杜家坎线路所 -> DJK, 涿州东 -> ZBD, 高碑店东 -> GBD
- 徐水东 -> XSD, 保定东 -> BDD, 定州东 -> DZD, 正定机场 -> ZDJ
- 石家庄 -> SJP, 高邑西 -> GYX, 邢台东 -> XTD, 邯郸东 -> HDD, 安阳东 -> AYD

场景类型：TEMP_SPEED_LIMIT(临时限速), SUDDEN_FAILURE(突发故障), SECTION_INTERRUPT(区间封锁)
故障类型：RAIN(暴雨), WIND(大风), SNOW(大雪), EQUIPMENT_FAILURE(设备故障), SIGNAL_FAILURE(信号故障), CATENARY_FAILURE(接触网故障), DELAY(预计晚点)

输出格式：{{"scene_type": "TEMP_SPEED_LIMIT", "fault_type": "WIND", "station_code": "SJP", "delay_seconds": 600}}

只输出JSON对象，不要其他内容。""",
            required_output_fields=["scene_type", "station_code"],
            temperature=0.1,
            max_tokens=256,
            tags=["preprocess", "extraction"],
            version="1.0"
        )
        self.register_template(l0_template)

        # L1数据建模模板（优化版，减少token使用）
        l1_template = PromptTemplate(
            template_id="l1_data_modeling",
            template_type=PromptTemplateType.L1_DATA_MODELING,
            template_name="L1数据建模",
            description="从调度员描述中生成事故卡片和网络快照",
            system_prompt="你是铁路调度数据建模助手。只输出JSON，不要解释。",
            user_prompt_template="""描述：{user_input}

规则：
1. 场景类型只有三种：临时限速、突发故障、区间中断（具体天气/故障作为fault_type）
   - 大风/暴雨/降雪/限速/天气 → 临时限速
   - 设备故障/信号故障/接触网故障 → 突发故障
   - 封锁/线路中断 → 区间中断
2. 车站：石家庄→SJP,北京西→BJX,保定东→BDD,徐水东→XSD,高邑西→GYX,邢台东→XTD,邯郸东→HDD,安阳东→AYD
3. 区间格式："XSD-BDD"，车站格式："SJP-SJP"
4. 提取列车号(如G1563)、延误分钟数、事件类型(fault_type)
5. fault_type未知则设"未知"，is_complete：列车号+位置+事件类型齐全才为true

输出JSON：{{"accident_card":{{"scene_category":"临时限速","fault_type":"大风","expected_duration":30,"affected_section":"SJP-SJP","location_type":"station","location_code":"SJP","location_name":"石家庄","affected_train_ids":["G1563"],"is_complete":true,"missing_fields":[]}}}}""",
            required_output_fields=["accident_card"],
            temperature=0.0,
            max_tokens=256,
            tags=["data_modeling", "accident_card"],
            version="1.2"
        )
        self.register_template(l1_template)

        # L2 Planner模板（v5.0 - 基于真实高铁场景）
        l2_template = PromptTemplate(
            template_id="l2_planner",
            template_type=PromptTemplateType.L2_PLANNER,
            template_name="L2规划器",
            description="根据事故场景特征，LLM自主决策最优求解策略、参数配置和目标权重",
            system_prompt="你是一名经验丰富的中国高铁调度专家。你需要仔细分析事故场景的各项特征（场景类型、受影响列车数、延误时长、位置、运营时段等），基于铁路运营实际做出最优决策。\n\n决策原则：\n1. 安全第一：优先确保列车运行安全\n2. 效率优先：在安全前提下最小化延误\n3. 快速响应：紧急场景优先使用秒级求解器\n4. 精准优化：非紧急场景使用MIP求全局最优\n\n输出要求：\n- 只输出JSON格式，不要任何解释文字\n- 所有字段必须有值，不能为null或空字符串\n- solver_candidates必须包含至少2个候选求解器\n- objective_weights的四项权重之和必须为1.0\n- 需要预测求解效果（延误范围、求解时间、决策置信度）",
            user_prompt_template="""【事故场景特征】
{scenario_features}

【可选求解策略】
1. mip（整数规划）：全局优化求解，可最小化总延误或最大延误，适合列车规模小（≤10列）、时间充裕的场景。求解时间较长（30-300秒）。
2. fcfs（先到先服务）：模拟真实调度员按实际到达顺序发车，支持延误传播和冗余恢复，允许快车超越慢车，适合紧急响应（秒级）或大规模列车场景。
3. fsfs（先计划先服务）：严格按原始运行图的计划发车顺序调度，保持原计划的相对优先级和越行关系不变，仅做整体时间平移，适合需要保持原计划顺序的场景。
4. srpt（最短剩余时间优先）：优先调度剩余运行时间短的列车，快速释放系统容量，适合实时延误调整和局部中断场景。
5. spt（最短处理时间优先）：优先调度停站少、运行距离短的列车，可以快速完成一些短途列车，适合局部延误场景。
6. max_delay_first（最大延误优先）：优先压缩延误最大列车的恢复时间，适合多列车不同程度延误的场景。
7. 多策略组合：可同时推荐多个求解器，由下游评估层选择最优方案。

【求解参数说明】
- time_limit：MIP求解时间上限（秒），范围30-600
  * 紧急场景（延误≤30分、下午运营14-18点）：60-120秒
  * 非紧急场景：120-300秒
  * 大规模场景（列车>15列）：可适当延长至300-600秒
- optimality_gap：MIP最优性间隙，范围0.01-0.1
  * 要求高精度：0.01-0.03（但求解时间长）
  * 平衡模式：0.05（推荐）
  * 要求快速：0.08-0.1

【优化目标权重说明】（四项权重之和应为1.0）
- max_delay_weight：最大单列延误的权重，铁路运营中防止个别列车严重延误最重要
- avg_delay_weight：平均延误的权重，体现整体服务质量
- affected_trains_weight：受影响列车数的权重，关注波及范围
- runtime_weight：求解时间的权重，紧急场景下响应速度重要

【决策任务】
你是一位高铁调度专家，需要根据上述场景特征，自主决策以下内容：

1. **规划意图（planning_intent）**：从以下选项中选择
   - recalculate_corridor_schedule（重新计算走廊时刻表）：适用于限速、需要重新排班
   - recover_from_disruption（从中断恢复）：适用于故障、需要快速恢复
   - handle_section_block（处理区间封锁）：适用于区间封锁、需要绕行

2. **最优求解器（solver_suggestion）**：从以下选项中选择
   - mip：适合小规模（≤10列）、时间充裕、需要全局优化的场景
   - fcfs：适合紧急响应、大规模列车（>10列）、需要快速处理的场景
   - fsfs：适合需要严格保持原计划顺序和越行关系的场景
   - srpt：适合实时延误调整和局部中断场景，快速释放系统容量
   - spt：适合局部延误场景，优先短途列车
   - max_delay_first：适合多列车不同程度延误的场景

3. **候选求解器列表（solver_candidates）**：推荐至少2个求解器供下游评估
   - 例如：["mip", "fcfs"] 或 ["srpt", "spt"]

4. **求解参数配置（solver_config）**：
   - time_limit：MIP求解时间上限（秒），范围30-600
     * 紧急场景（延误≤30分、下午运营14-18点）：60-120秒
     * 非紧急场景：120-300秒
     * 大规模场景（列车>15列）：可适当延长至300-600秒
   - optimality_gap：MIP最优性间隙，范围0.01-0.1
     * 要求高精度：0.01-0.03（但求解时间长）
     * 平衡模式：0.05（推荐）
     * 要求快速：0.08-0.1
   - optimization_objective：优化目标，从以下选择
     * min_max_delay：最小化最大延误（防止个别列车严重延误）
     * min_total_delay：最小化总延误（提升整体效率）
     * min_avg_delay：最小化平均延误

5. **目标权重（objective_weights）**：四项权重之和必须为1.0
   - max_delay_weight：最大单列延误权重（铁路运营中最重要）
   - avg_delay_weight：平均延误权重
   - affected_trains_weight：受影响列车数权重
   - runtime_weight：求解时间权重（紧急场景下重要）
   
   权重建议：
   * 轻微延误（≤10分）：max=0.5, avg=0.3, affected=0.1, runtime=0.1
   * 一般延误（10-30分）：max=0.4, avg=0.3, affected=0.2, runtime=0.1
   * 较大延误（30-60分）：max=0.3, avg=0.4, affected=0.2, runtime=0.1
   * 严重延误（>60分）：max=0.2, avg=0.3, affected=0.3, runtime=0.2

6. **预期求解效果（predicted_outcomes）**：预测求解后的效果
   - expected_total_delay：预期总延误范围（分钟），格式："80-120"
   - expected_max_delay：预期最大延误范围（分钟），格式："20-30"
   - expected_solve_time：预期求解时间（秒），整数
   - decision_confidence：决策置信度（0-1），0.85表示85%的信心

7. **决策理由（reasoning）**：简要说明选择理由（30-60字）

8. **不选其他solver的理由（rejected_solvers_reasoning）**：简要说明不选其他候选求解器的理由
   - 字典格式，例如：{{"fcfs":"FCFS虽然快但无法优化延误","spt":"本场景不是短途列车优先场景"}}

【输出格式】（必须严格遵守）
{{"planning_intent":"recalculate_corridor_schedule","问题描述":"场景的简要描述","solver_suggestion":"mip","solver_candidates":["mip","fcfs"],"solver_config":{{"time_limit":120,"optimality_gap":0.05,"optimization_objective":"min_max_delay"}},"objective_weights":{{"max_delay_weight":0.5,"avg_delay_weight":0.3,"affected_trains_weight":0.1,"runtime_weight":0.1}},"predicted_outcomes":{{"expected_total_delay":"80-120","expected_max_delay":"20-30","expected_solve_time":90,"decision_confidence":0.85}},"reasoning":"选择mip求解器，因为列车规模小（5列）、时间充裕，可以求全局最优解","rejected_solvers_reasoning":{{"fcfs":"FCFS虽然快但无法优化延误"}}}}""",
            required_output_fields=["planning_intent", "solver_suggestion", "solver_config", "objective_weights", "predicted_outcomes"],
            temperature=0.2,
            max_tokens=1024,
            tags=["planner", "intent", "solver_selection", "llm_decision", "finetuning"],
            version="5.0"
        )
        self.register_template(l2_template)

        # L3求解器选择模板
        l3_template = PromptTemplate(
            template_id="l3_solver_selector",
            template_type=PromptTemplateType.L3_SOLVER,
            template_name="L3求解器选择器",
            description="根据场景类型和列车数量选择最优求解器",
            system_prompt="你是一个专业的铁路调度求解器选择助手，负责根据场景特征选择最合适的求解算法。必须只输出JSON格式，不要添加任何解释文字或markdown标记。",
            user_prompt_template="""根据事故卡片和网络快照信息，选择最优求解器。只输出纯JSON，不要添加markdown代码块标记(```)。

事故卡片：{accident_card}
网络快照：{network_snapshot}

{rag_knowledge}

求解器选择规则：
1. 区间封锁场景 -> solver: "noop" (不调度)
2. 临时限速场景 -> solver: "mip" (优化求解)
3. 突发故障场景 -> solver: "fcfs" (快速响应)
4. 列车数量>20且临时限速 -> solver: "fcfs" (规模过大)
5. 延误>30分钟且突发故障 -> solver: "max_delay_first" (优先处理延误)

必须严格按照以下JSON格式输出，不要添加任何额外文字：
{{"solver": "fcfs", "reasoning": "突发故障场景，需要快速响应", "solver_config": {{"optimization_objective": "min_max_delay"}}}}

solver可选: mip, fcfs, max_delay_first, noop

只输出JSON对象，不要其他内容。""",
            required_output_fields=["solver"],
            temperature=0.1,
            max_tokens=256,
            tags=["solver", "selection"],
            version="1.0"
        )
        self.register_template(l3_template)

        # L4评估模板（优化版）
        l4_template = PromptTemplate(
            template_id="l4_evaluation",
            template_type=PromptTemplateType.L4_EVALUATION,
            template_name="L4评估",
            description="评估调度方案，生成解释和风险提示",
            system_prompt="你是铁路调度方案评估助手。只输出JSON，不要解释。",
            user_prompt_template="""结果：{execution_result}

评估延误和风险，给出0-1评分。

输出JSON：{{"llm_summary":"方案可行，总延误10分钟","risk_warnings":[],"feasibility_score":0.9,"constraint_check":{{"时间约束":true,"空间约束":true}}}}""",
            required_output_fields=["llm_summary", "feasibility_score"],
            temperature=0.0,
            max_tokens=256,
            tags=["evaluation", "risk_analysis"],
            version="1.1"
        )
        self.register_template(l4_template)

        # L3求解器选择模板（备用，当L2需要更精细控制时使用）
        # 注意：此模板为备用版本，默认不注册以避免重复注册问题
        # 如需使用，请通过 register_template 手动注册
        self._l3_solver_v2_template = PromptTemplate(
            template_id="l3_solver_selector_v2",
            template_type=PromptTemplateType.L3_SOLVER,
            template_name="L3求解器选择器V2",
            description="根据场景特征选择最优求解器（备用版本，未注册）",
            system_prompt="你是一个专业的铁路调度求解器选择助手，负责根据场景特征选择最合适的求解算法。必须只输出JSON格式，不要添加任何解释文字或markdown标记。",
            user_prompt_template="""根据事故卡片信息，选择最优求解器。只输出纯JSON，不要添加markdown代码块标记(```)。

事故卡片：{accident_card}

{rag_knowledge}

求解器选择规则：
1. 区间封锁场景 -> solver: "noop" (不调度)
2. 临时限速场景 -> solver: "mip" (优化求解)
3. 突发故障场景 -> solver: "fcfs" (快速响应)
4. 延误>30分钟 -> solver: "max_delay_first" (优先处理延误)

必须严格按照以下JSON格式输出，不要添加任何额外文字：
{"solver": "fcfs", "reasoning": "突发故障场景，需要快速响应", "solver_config": {}}

solver可选: mip, fcfs, max_delay_first, noop

只输出JSON对象，不要其他内容。""",
            required_output_fields=["solver"],
            temperature=0.1,
            max_tokens=256,
            tags=["solver", "selection"],
            version="1.0"
        )
        # 不注册V2版本，避免重复注册问题
        # 如需使用，请通过 register_template 手动注册
        # self.register_template(l3_solver_template)

        # L4自然语言调度方案生成模板（折中版）
        l4_natural_language_template = PromptTemplate(
            template_id="l4_natural_language_plan",
            template_type=PromptTemplateType.L4_EVALUATION,
            template_name="L4自然语言调度方案",
            description="生成人类可读的自然语言调度方案",
            system_prompt="你是铁路调度方案解释助手，负责将调度结果转换为清晰的调度指令。",
            user_prompt_template="""根据调度结果生成方案。

场景：{scene_type}
位置：{delay_location}  
列车：{affected_trains}
评估：{evaluation_summary}

调度结果：{execution_result}

生成包含以下内容的方案：
1. 调整概述（原因+影响范围）
2. 具体调整（列车+站点+时间变化）
3. 注意事项（关键节点+安全要求）

输出JSON：{{"natural_language_plan":"【调整概述】因石家庄站大风，G1563延误30分钟。\n\n【具体调整】\nG1563次列车：\n- 石家庄站：原计划19:05发车，调整为19:35发车\n- 高邑西站：原计划19:17通过，调整为19:47通过\n\n【注意事项】\n- 确保石家庄站追踪间隔不小于3分钟\n- 后续站点同步更新到发时间"}}""",
            required_output_fields=["natural_language_plan"],
            temperature=0.2,
            max_tokens=512,
            tags=["natural_language", "dispatch_plan"],
            version="1.2"
        )
        self.register_template(l4_natural_language_template)


# 全局实例
_prompt_manager: Optional[PromptManager] = None


def get_prompt_manager() -> PromptManager:
    """获取全局Prompt管理器实例"""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = PromptManager()
    return _prompt_manager
