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
        logger.info(f"注册Prompt模板: {template.template_id} - {template.template_name}")

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
                   "solver_result", "execution_result", "rag_knowledge", "rag_documents"]
        for var in all_vars:
            if var not in context_dict:
                context_dict[var] = ""
        
        # 对包含JSON的变量进行转义，防止被.format()误解析
        # 将 { 替换为 {{, } 替换为 }}
        for key in ["accident_card", "network_snapshot", "canonical_request", 
                    "dispatch_context", "solver_result", "execution_result"]:
            if key in context_dict and isinstance(context_dict[key], str):
                # 只转义单个的 { 和 }，不转义已经转义的 {{ 和 }}
                value = context_dict[key]
                # 使用临时标记避免重复转义
                value = value.replace("{{", "\x00L\x00").replace("}}", "\x00R\x00")
                value = value.replace("{", "{{").replace("}", "}}")
                value = value.replace("\x00L\x00", "{{").replace("\x00R\x00", "}}")
                context_dict[key] = value
        
        # 填充用户模板
        # 注意：需要对包含JSON的变量进行转义，防止.format()将其中的{}误认为占位符
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
            missing_var = error_str.strip("'") if error_str else "unknown"
            logger.error(f"模板填充失败: {e}")
            logger.error(f"缺失变量: {missing_var}")
            logger.error(f"上下文中已有变量: {list(context_dict.keys())}")
            # 再次检查并补充缺失变量
            if missing_var in all_vars:
                context_dict[missing_var] = ""
                try:
                    filled_prompt = template.user_prompt_template.format(**context_dict)
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
        import json
        from datetime import datetime

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

已知信息：{canonical_request}

车站名称到站码的映射：
- 北京西 -> BJX, 杜家坎线路所 -> DJK, 涿州东 -> ZBD, 高碑店东 -> GBD
- 徐水东 -> XSD, 保定东 -> BDD, 定州东 -> DZD, 正定机场 -> ZDJ
- 石家庄 -> SJP, 高邑西 -> GYX, 邢台东 -> XTD, 邯郸东 -> HDD, 安阳东 -> AYD

必须严格按照以下JSON格式输出，不要添加markdown代码块标记：
{"scene_type": "TEMP_SPEED_LIMIT", "fault_type": "WIND", "station_code": "SJP", "delay_seconds": 600}

scene_type 可选: TEMP_SPEED_LIMIT, SUDDEN_FAILURE, SECTION_INTERRUPT
fault_type 可选: RAIN, WIND, SNOW, EQUIPMENT_FAILURE, SIGNAL_FAILURE, CATENARY_FAILURE, DELAY

只输出JSON对象，不要其他内容。""",
            required_output_fields=["scene_type", "station_code"],
            temperature=0.1,
            max_tokens=256,
            tags=["preprocess", "extraction"],
            version="1.0"
        )
        self.register_template(l0_template)

        # L1数据建模模板（增强版，带RAG知识指导）
        l1_template = PromptTemplate(
            template_id="l1_data_modeling",
            template_type=PromptTemplateType.L1_DATA_MODELING,
            template_name="L1数据建模",
            description="从调度员描述中生成事故卡片和网络快照",
            system_prompt="你是一个专业的铁路调度数据建模助手，负责将自然语言描述转换为结构化的调度数据。必须只输出JSON格式，不要添加任何解释文字或markdown标记。",
            user_prompt_template="""根据铁路故障/调整描述，生成事故卡片。只输出纯JSON，不要添加markdown代码块标记(```)。

故障描述：{user_input}

【领域知识参考】
{rag_knowledge}

【场景类型判断指南】
1. 临时限速场景：包含"限速"、天气原因（大风、暴雨、冰雪）、自然灾害
2. 突发故障场景：包含"故障"、设备问题、列车问题、信号问题
3. 区间封锁场景：包含"封锁"、"中断"、线路无法通行

【车站代码映射】
- 石家庄 -> SJP, 北京西 -> BJX, 保定东 -> BDD, 定州东 -> DZD
- 徐水东 -> XSD, 涿州东 -> ZBD, 高碑店东 -> GBD, 正定机场 -> ZDJ
- 高邑西 -> GYX, 邢台东 -> XTD, 邯郸东 -> HDD, 安阳东 -> AYD

【提取规则】
1. 从描述中提取列车号（如G1563、D1234）放入affected_train_ids数组
2. 从描述中提取车站名转换为站码（如"石家庄"->"SJP"）
3. 如果有"延误"或"晚点"，提取分钟数放入reported_delay_minutes
4. 从描述中提取事件类型（如大风、暴雨、设备故障等）放入fault_type
5. **重要：如果描述中没有明确提到事件类型，fault_type必须设为"未知"，不要猜测或编造**
6. **判断is_complete**：只有当有列车号+车站+事件类型（fault_type不是"未知"）时，才设为true，否则设为false
7. **如果is_complete为false，在missing_fields中列出缺失的字段**（如"列车号"、"位置"、"事件类型"）

【输出示例】（仅作为格式参考，不要照搬内容）：
- 完整信息示例：{{"accident_card": {{"scene_category": "临时限速", "fault_type": "大风", "affected_section": "SJP-SJP", "location_code": "SJP", "location_name": "石家庄", "affected_train_ids": ["G1563"], "is_complete": true, "missing_fields": []}}}}
- 不完整信息示例：{{"accident_card": {{"scene_category": "突发故障", "fault_type": "未知", "affected_section": "SJP-SJP", "location_code": "SJP", "location_name": "石家庄", "affected_train_ids": ["G1563"], "is_complete": false, "missing_fields": ["事件类型"]}}}}

必须严格按照JSON格式输出，不要添加任何额外文字或markdown标记。""",
            required_output_fields=["accident_card"],
            temperature=0.1,
            max_tokens=512,
            tags=["data_modeling", "accident_card"],
            version="1.1"
        )
        self.register_template(l1_template)

        # L2 Planner模板
        l2_template = PromptTemplate(
            template_id="l2_planner",
            template_type=PromptTemplateType.L2_PLANNER,
            template_name="L2规划器",
            description="根据事故卡片判断问题类型和处理意图",
            system_prompt="你是一个专业的铁路调度规划助手，负责分析故障场景并制定处理策略。必须只输出JSON格式，不要添加任何解释文字或markdown标记。",
            user_prompt_template="""根据事故卡片判断问题类型和处理意图。只输出纯JSON，不要添加markdown代码块标记(```)。

事故卡片：{accident_card}

{rag_knowledge}

【场景类型映射规则】
- 临时限速场景 -> planning_intent: "recalculate_corridor_schedule"
- 突发故障场景 -> planning_intent: "recover_from_disruption"
- 区间封锁场景 -> planning_intent: "handle_section_block"

【求解器选择参考】
- 临时限速：使用mip_scheduler（优化调整）
- 突发故障：使用fcfs_scheduler（快速响应）
- 区间封锁：使用noop_scheduler（不调度）

必须严格按照以下JSON格式输出，不要添加任何额外文字：
{{"planning_intent": "recover_from_disruption", "问题描述": "大风导致列车延误", "建议窗口": "SJP"}}

planning_intent可选值：
- recalculate_corridor_schedule：重新计算走廊时刻表（临时限速）
- recover_from_disruption：从干扰中恢复（突发故障）
- handle_section_block：处理区间封锁

只输出JSON对象，不要其他内容。""",
            required_output_fields=["planning_intent"],
            temperature=0.1,
            max_tokens=256,
            tags=["planner", "intent"],
            version="1.1"
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

        # L4评估模板
        l4_template = PromptTemplate(
            template_id="l4_evaluation",
            template_type=PromptTemplateType.L4_EVALUATION,
            template_name="L4评估",
            description="评估调度方案，生成解释和风险提示",
            system_prompt="你是一个专业的铁路调度方案评估助手，负责评估调度方案的可行性和风险。必须只输出JSON格式，不要添加任何解释文字或markdown标记。",
            user_prompt_template="""评估调度方案，生成解释和风险提示。只输出纯JSON，不要添加markdown代码块标记(```)。

求解结果：{execution_result}

{rag_knowledge}

评估要求：
1. 分析求解结果中的延误情况（total_delay_minutes, max_delay_minutes）
2. 识别潜在风险（延误传播、约束违反等）
3. 根据延误处理策略知识，评估方案合理性
4. 给出可行性评分（0.0-1.0）

必须严格按照以下JSON格式输出，不要添加任何额外文字：
{{"llm_summary": "方案可行，总延误10分钟", "risk_warnings": [], "feasibility_score": 0.9, "constraint_check": {{"时间约束": true, "空间约束": true}}}}

feasibility_score范围0.0-1.0，表示方案可行性评分。

只输出JSON对象，不要其他内容。""",
            required_output_fields=["llm_summary", "feasibility_score"],
            temperature=0.1,
            max_tokens=512,
            tags=["evaluation", "risk_analysis"],
            version="1.0"
        )
        self.register_template(l4_template)

        # L3求解器选择模板（备用，当L2需要更精细控制时使用）
        l3_solver_template = PromptTemplate(
            template_id="l3_solver_selector",
            template_type=PromptTemplateType.L3_SOLVER,
            template_name="L3求解器选择器",
            description="根据场景特征选择最优求解器",
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
        self.register_template(l3_solver_template)


# 全局实例
_prompt_manager: Optional[PromptManager] = None


def get_prompt_manager() -> PromptManager:
    """获取全局Prompt管理器实例"""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = PromptManager()
    return _prompt_manager
