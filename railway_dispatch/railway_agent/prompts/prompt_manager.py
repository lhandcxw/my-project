# -*- coding: utf-8 -*-
"""
Prompt管理器模块
统一管理所有LLM Prompt模板，为微调和prompt工程提供支持
"""

import logging
from typing import Dict, List, Optional, Any
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

        # 填充用户模板
        try:
            filled_prompt = template.user_prompt_template.format(**context_dict)
        except KeyError as e:
            logger.warning(f"模板填充缺少变量: {e}，使用默认值")
            # 提供默认值
            context_dict = {k: v if v is not None else "" for k, v in context_dict.items()}
            filled_prompt = template.user_prompt_template.format(**context_dict)

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
    ) -> tuple[bool, List[str]]:
        """
        验证输出是否符合模板要求

        Args:
            template_id: 模板ID
            output: 输出字典

        Returns:
            tuple[bool, List[str]]: (是否有效, 错误列表)
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

        context_dict = {
            "user_input": context.user_input or "",
            "request_id": context.request_id,
            "scene_type": context.scene_type or "",
            "scene_category": context.scene_category or "",
            "source_type": context.source_type or "",
        }

        # 添加复杂对象
        if context.canonical_request:
            context_dict["canonical_request"] = json.dumps(
                context.canonical_request, ensure_ascii=False, indent=2
            )
        else:
            context_dict["canonical_request"] = "{}"

        if context.accident_card:
            context_dict["accident_card"] = json.dumps(
                context.accident_card, ensure_ascii=False, indent=2
            )
        else:
            context_dict["accident_card"] = "{}"

        if context.network_snapshot:
            context_dict["network_snapshot"] = json.dumps(
                context.network_snapshot, ensure_ascii=False, indent=2
            )
        else:
            context_dict["network_snapshot"] = "{}"

        if context.dispatch_context:
            context_dict["dispatch_context"] = json.dumps(
                context.dispatch_context, ensure_ascii=False, indent=2
            )
        else:
            context_dict["dispatch_context"] = "{}"

        if context.solver_result:
            context_dict["solver_result"] = json.dumps(
                context.solver_result, ensure_ascii=False, indent=2
            )
        else:
            context_dict["solver_result"] = "{}"

        if context.execution_result:
            context_dict["execution_result"] = json.dumps(
                context.execution_result, ensure_ascii=False, indent=2
            )
        else:
            context_dict["execution_result"] = "{}"

        # 添加额外变量
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
            system_prompt="你是一个专业的铁路调度助手，负责从调度员的描述中提取关键信息。",
            user_prompt_template="""从铁路调度描述中提取信息，只输出JSON。

描述：{user_input}

已知信息：{canonical_request}

车站名称到站码的映射：
- 北京西 -> BJX, 杜家坎线路所 -> DJK, 涿州东 -> ZBD, 高碑店东 -> GBD
- 徐水东 -> XSD, 保定东 -> BDD, 定州东 -> DZD, 正定机场 -> ZDJ
- 石家庄 -> SJP, 高邑西 -> GYX, 邢台东 -> XTD, 邯郸东 -> HDD, 安阳东 -> AYD

输出格式（严格JSON）：
{{
  "scene_type": "TEMP_SPEED_LIMIT",
  "fault_type": "RAIN",
  "station_code": "XSD",
  "delay_seconds": 600
}}

scene_type 可选: TEMP_SPEED_LIMIT, SUDDEN_FAILURE, SECTION_INTERRUPT
fault_type 可选: RAIN, WIND, SNOW, EQUIPMENT_FAILURE, SIGNAL_FAILURE, CATENARY_FAILURE, DELAY
""",
            required_output_fields=["scene_type", "station_code"],
            temperature=0.3,
            max_tokens=256,
            tags=["preprocess", "extraction"],
            version="1.0"
        )
        self.register_template(l0_template)

        # L1数据建模模板
        l1_template = PromptTemplate(
            template_id="l1_data_modeling",
            template_type=PromptTemplateType.L1_DATA_MODELING,
            template_name="L1数据建模",
            description="从调度员描述中生成事故卡片和网络快照",
            system_prompt="你是一个专业的铁路调度数据建模助手，负责将自然语言描述转换为结构化的调度数据。",
            user_prompt_template="""根据铁路故障/调整描述，生成事故卡片。只输出JSON。

故障描述：{user_input}

{rag_knowledge}

事故卡片格式：
{{
  "accident_card": {{
    "scene_category": "临时限速",
    "fault_type": "暴雨",
    "affected_section": "XSD-BDD",
    "location_code": "SJP",
    "location_name": "石家庄",
    "affected_train_ids": ["G1265"],
    "reported_delay_minutes": 10,
    "start_time": "2024-01-15T10:00:00",
    "is_complete": true
  }}
}}

可选scene_category: 临时限速, 突发故障, 区间封锁
可选fault_type: 暴雨, 大风, 设备故障, 信号故障, 接触网故障, 预计晚点, 晚点

注意：
- 从描述中提取列车号（如G1265）放入affected_train_ids
- 从描述中提取车站名（如石家庄）转换为站码（如SJP）
- 判断is_complete：如果有列车号+车站/区段+事件类型，则为true
""",
            required_output_fields=["accident_card"],
            temperature=0.5,
            max_tokens=512,
            tags=["data_modeling", "accident_card"],
            version="1.0"
        )
        self.register_template(l1_template)

        # L2 Planner模板
        l2_template = PromptTemplate(
            template_id="l2_planner",
            template_type=PromptTemplateType.L2_PLANNER,
            template_name="L2规划器",
            description="根据事故卡片判断问题类型和处理意图",
            system_prompt="你是一个专业的铁路调度规划助手，负责分析故障场景并制定处理策略。",
            user_prompt_template="""根据事故卡片判断问题类型和处理意图。只输出JSON。

事故卡片：{accident_card}

{rag_knowledge}

问题类型和意图：
- 临时限速场景 -> recalculate_corridor_schedule（重新计算区间时刻表）
- 突发故障恢复 -> recover_from_disruption（恢复故障后运行）
- 区间封锁处理 -> handle_section_block（处理区间中断）

输出格式：
{{
  "planning_intent": "recalculate_corridor_schedule",
  "问题描述": "暴雨导致临时限速",
  "建议窗口": "XSD-BDD"
}}
""",
            required_output_fields=["planning_intent"],
            temperature=0.3,
            max_tokens=256,
            tags=["planner", "intent"],
            version="1.0"
        )
        self.register_template(l2_template)

        # L4评估模板
        l4_template = PromptTemplate(
            template_id="l4_evaluation",
            template_type=PromptTemplateType.L4_EVALUATION,
            template_name="L4评估",
            description="评估调度方案，生成解释和风险提示",
            system_prompt="你是一个专业的铁路调度方案评估助手，负责评估调度方案的可行性和风险。",
            user_prompt_template="""评估调度方案，生成解释和风险提示。只输出JSON。

求解结果：{execution_result}

{rag_knowledge}

输出格式：
{{
  "llm_summary": "方案可行，总延误10分钟",
  "risk_warnings": ["建议监控G1215列车"],
  "feasibility_score": 0.9,
  "constraint_check": {{
    "时间约束": true,
    "空间约束": true
  }}
}}
""",
            required_output_fields=["llm_summary", "feasibility_score"],
            temperature=0.5,
            max_tokens=512,
            tags=["evaluation", "risk_analysis"],
            version="1.0"
        )
        self.register_template(l4_template)


# 全局实例
_prompt_manager: Optional[PromptManager] = None


def get_prompt_manager() -> PromptManager:
    """获取全局Prompt管理器实例"""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = PromptManager()
    return _prompt_manager
