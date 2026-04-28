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
        # L0预处理模板（v2.0 - 与L1对齐，提取更完整）
        l0_template = PromptTemplate(
            template_id="l0_preprocess_extractor",
            template_type=PromptTemplateType.L0_PREPROCESS,
            template_name="L0预处理提取器",
            description="从用户输入中提取基础调度信息（快速预处理）",
            system_prompt="你是京广高铁调度预处理助手。从调度员描述中快速提取关键信息。必须只输出JSON格式，不要添加任何解释文字或markdown标记。",
            user_prompt_template="""从以下铁路调度描述中提取信息，只输出JSON。

描述：{user_input}

【车站映射】（必须准确）
北京西→BJX, 杜家坎线路所→DJK, 涿州东→ZBD, 高碑店东→GBD, 徐水东→XSD,
保定东→BDD, 定州东→DZD, 正定机场→ZDJ, 石家庄→SJP, 高邑西→GYX,
邢台东→XTD, 邯郸东→HDD, 安阳东→AYD

【提取规则】
1. scene_type（场景类型）：
   - TEMP_SPEED_LIMIT：大风、暴雨、降雪、冰雹、天气原因、限速
   - SUDDEN_FAILURE：设备故障、信号故障、接触网故障、列车故障
   - SECTION_INTERRUPT：封锁、线路中断、施工
2. fault_type（故障类型）：提取具体原因英文大写，如WIND、RAIN、EQUIPMENT_FAILURE
3. location_type：提到"站"→station，提到"区间"/"段"→section
4. location_code：
   - station类型→单站编码（如"SJP"）
   - section类型→两端站编码用"-"连接（如"DJK-ZBD"）
5. train_ids：提取所有车次号（如G1563），放入数组
6. delay_seconds：延误秒数（如15分钟→900）

【输出格式】
{{"scene_type":"TEMP_SPEED_LIMIT","fault_type":"WIND","location_type":"station","location_code":"SJP","train_ids":["G1563"],"delay_seconds":900}}

只输出JSON对象，不要其他内容。""",
            required_output_fields=["scene_type", "location_code"],
            temperature=0.1,
            max_tokens=512,
            tags=["preprocess", "extraction"],
            version="2.0"
        )
        self.register_template(l0_template)

        # L1数据建模模板（优化版，减少token使用）
        l1_template = PromptTemplate(
            template_id="l1_data_modeling",
            template_type=PromptTemplateType.L1_DATA_MODELING,
            template_name="L1数据建模",
            description="从调度员描述中生成事故卡片和网络快照",
            system_prompt="你是京广高铁（北京西→安阳东，13站）调度数据建模助手。负责从调度员自然语言描述中提取结构化事故信息。只输出JSON，不要解释。",
            user_prompt_template="""描述：{user_input}

规则：
1. 场景类型(scene_category)仅三种，按严重性优先：区间封锁>突发故障>临时限速
   - 临时限速：大风、暴雨、降雪、冰雹、天气原因、能见度低、限速
   - 突发故障：设备故障、信号故障、接触网故障、列车故障、道岔故障
   - 区间封锁：封锁、线路中断、施工、禁止通行
2. 故障类型(fault_type)：提取具体原因（如"大风"、"设备故障"、"施工"），未知则填"未知"
3. 车站映射：北京西→BJX, 杜家坎线路所→DJK, 涿州东→ZBD, 高碑店东→GBD, 徐水东→XSD, 保定东→BDD, 定州东→DZD, 正定机场→ZDJ, 石家庄→SJP, 高邑西→GYX, 邢台东→XTD, 邯郸东→HDD, 安阳东→AYD
4. 位置判断：
   - "XX站"/"在XX" → location_type="station", location_code="XX", affected_section="XX-XX"
   - "XX到YY区间"/"XX-YY段" → location_type="section", location_code="XX-YY", affected_section="XX-YY"
   - 单地名未明确时默认station
5. 列车号：格式G/D/C+数字，如G1563，提取到affected_train_ids数组
6. 延误时间：提取分钟数，设给expected_duration（数值）
7. is_complete=true条件：affected_train_ids非空 + location_code非空 + scene_category非空 + expected_duration≥0
8. missing_fields：is_complete=false时列出缺失字段名，否则[]

输出JSON：{{"accident_card":{{"scene_category":"临时限速","fault_type":"大风","expected_duration":15,"affected_section":"SJP-SJP","location_type":"station","location_code":"SJP","location_name":"石家庄","affected_train_ids":["G1563"],"is_complete":true,"missing_fields":[]}}}}""",
            required_output_fields=["accident_card"],
            temperature=0.0,
            max_tokens=512,
            tags=["data_modeling", "accident_card"],
            version="1.3"
        )
        self.register_template(l1_template)

        # L2 Planner模板（v6.0 - 完善字段与hierarchical支持）
        l2_template = PromptTemplate(
            template_id="l2_planner",
            template_type=PromptTemplateType.L2_PLANNER,
            template_name="L2规划器",
            description="根据事故场景特征，LLM自主决策最优求解策略、参数配置和目标权重",
            system_prompt="你是一名经验丰富的中国高铁调度专家，负责京广高铁（北京西→安阳东，13站，147列列车）的应急调度决策。你需要根据事故场景特征选择最优求解策略。\n\n决策原则：\n1. 安全第一：优先确保列车运行安全和追踪间隔≥3分钟\n2. 效率优先：在安全前提下最小化总延误和最大延误\n3. 快速响应：紧急场景（大面积延误、高峰期）优先使用秒级求解器\n4. 精准优化：非紧急小场景使用MIP求全局最优\n5. 分层决策：大规模复杂场景推荐使用hierarchical分层求解（先FCFS筛选，后MIP精优）\n\n输出要求：\n- 只输出JSON格式，不要任何解释文字或markdown标记\n- 所有字段必须有值，不能为null或空字符串\n- solver_candidates必须包含至少2个候选求解器\n- optimization_weights四项之和必须等于1.0",
            user_prompt_template="""【事故场景特征】
{scenario_features}

【可选求解策略】
1. mip（混合整数规划）：全局最优求解，可精确最小化总延误或最大延误。适合列车规模小（≤10列）、时间充裕（非高峰期）、需要高质量方案的场景。求解时间30-300秒。
2. fcfs（先到先服务）：按实际到达顺序发车，支持延误传播计算和冗余恢复，允许快车超越慢车。适合大规模列车（>10列）、紧急响应（秒级出解）、突发故障场景。
3. fsfs（先计划先服务）：严格按原始运行图顺序调度，保持原计划的相对优先级和越行关系不变，仅做整体时间平移。适合需要保持原计划结构、变动最小的场景。
4. srpt（最短剩余时间优先）：优先调度剩余运行时间短的列车，快速释放线路容量。适合实时延误调整和局部中断恢复场景。
5. spt（最短处理时间优先）：优先调度停站少、运行距离短的列车。适合局部延误场景中快速消化短途列车。
6. max-delay-first（最大延误优先）：优先压缩延误最大列车的恢复时间，均衡各列车延误。适合多列车不同程度大面积延误的场景。
7. hierarchical（分层求解）：三层架构——先FCFS快速初筛，再MIP对关键窗口精优，最后质量评估。适合中等规模（10-30列）、需要兼顾速度和质量的场景。这是系统推荐的综合策略。
8. noop（不调度）：适用于区间完全封锁且无可行替代路径的场景。

【求解器选择决策树】
- 区间封锁(scene_category=区间封锁) → 优先noop或fcfs
- 列车数≤10且非紧急 → 优先mip或hierarchical
- 列车数11-30且需要较好质量 → 优先hierarchical或fcfs
- 列车数>30或紧急响应 → 优先fcfs
- 需要保持原计划顺序不变 → fsfs
- 多列车不同程度大面积延误 → max-delay-first

【求解参数配置（solver_config）】
- time_limit：MIP求解时间上限（秒），范围30-600
  * 紧急场景（延误>30分钟、运营高峰14-18点、突发故障）：60-120秒
  * 非紧急场景：120-300秒
  * 大规模场景（列车>20列）：300-600秒
- optimality_gap：MIP最优性间隙，范围0.01-0.1
  * 高精度：0.01-0.03（非紧急小场景）
  * 平衡模式：0.05（推荐）
  * 快速模式：0.08-0.1（紧急场景）
- optimization_objective：优化目标
  * min_max_delay：最小化最大延误（防止个别列车严重延误，推荐）
  * min_total_delay：最小化总延误（提升整体效率）
  * min_avg_delay：最小化平均延误

【优化目标权重（optimization_weights）】
四项权重之和必须严格等于1.0：
- max_delay_weight：最大单列延误权重（0.3-0.5，防止个别列车严重晚点最重要）
- avg_delay_weight：平均延误权重（0.2-0.3，体现整体服务质量）
- affected_trains_weight：受影响列车数权重（0.1-0.2，关注波及范围）
- runtime_weight：求解时间权重（0.0-0.2，紧急场景下响应速度重要）

【决策任务】
请输出以下字段的JSON：

1. planning_intent：规划意图
   - recalculate_corridor_schedule（重新计算走廊时刻表）：限速、需重新排班
   - recover_from_disruption（从中断恢复）：故障、需快速恢复
   - handle_section_block（处理区间封锁）：封锁、需绕行或等待

2. scenario_description：场景简要描述（20-40字）

3. solver_suggestion：最优求解器（单选：mip/fcfs/fsfs/srpt/spt/max-delay-first/hierarchical/noop）

4. solver_candidates：候选求解器列表（至少2个，如["mip","fcfs","hierarchical"]）

5. solver_config：求解参数
   {{"time_limit":120,"optimality_gap":0.05,"optimization_objective":"min_max_delay"}}

6. optimization_weights：优化权重
   {{"max_delay_weight":0.4,"avg_delay_weight":0.3,"affected_trains_weight":0.2,"runtime_weight":0.1}}

7. reasoning：决策理由（30-60字）

8. rejected_solvers_reasoning：不选其他求解器的理由
   字典格式，键为未选求解器名，值为理由。例如：{{"fcfs":"本场景规模小，FCFS无法全局优化","spt":"非短途列车优先场景"}}

【输出格式示例】
{{"planning_intent":"recalculate_corridor_schedule","scenario_description":"石家庄站大风限速，5列列车受影响，非高峰时段","solver_suggestion":"mip","solver_candidates":["mip","hierarchical","fcfs"],"solver_config":{{"time_limit":180,"optimality_gap":0.05,"optimization_objective":"min_max_delay"}},"optimization_weights":{{"max_delay_weight":0.4,"avg_delay_weight":0.3,"affected_trains_weight":0.2,"runtime_weight":0.1}},"reasoning":"列车规模小（5列）、时间充裕，MIP可求全局最优，hierarchical作为候选兼顾鲁棒性","rejected_solvers_reasoning":{{"fcfs":"规模小不需要快速启发式","fsfs":"限速需重新排班，不宜保持原顺序"}}}}""",
            required_output_fields=["planning_intent", "solver_suggestion", "solver_config", "reasoning", "solver_candidates", "optimization_weights"],
            temperature=0.2,
            max_tokens=1024,
            tags=["planner", "intent", "solver_selection", "llm_decision", "finetuning"],
            version="6.0"
        )
        self.register_template(l2_template)

        # L3求解器选择模板（v2.0 - 与hierarchical_solver对齐）
        l3_template = PromptTemplate(
            template_id="l3_solver_selector",
            template_type=PromptTemplateType.L3_SOLVER,
            template_name="L3求解器选择器",
            description="根据L2规划意图和场景特征，精确选择最终执行求解器",
            system_prompt="你是铁路调度求解器执行助手。根据L2已确定的规划意图和场景特征，选择最终执行的具体求解器。必须只输出JSON格式，不要添加任何解释文字或markdown标记。",
            user_prompt_template="""根据以下信息选择最优求解器。只输出纯JSON，不要添加markdown代码块标记(```)。

事故卡片：{accident_card}
网络快照：{network_snapshot}
L2规划建议：{dispatch_context}

{rag_knowledge}

【求解器选择规则】（按优先级从高到低执行）：

1. **区间封锁且无替代路径** → solver="noop"（不调度，等待封锁解除）

2. **突发故障+紧急响应**（延误>30分钟 或 高峰时段14-18点 或 列车数>30列） → solver="fcfs"（秒级快速响应）

3. **小规模+高质量要求**（列车数≤10列 且 非紧急 且 临时限速/小延误） → solver="mip"（全局最优）

4. **中等规模+兼顾速度质量**（列车数11-30列 且 最大延误≥5分钟） → solver="hierarchical"（分层求解：先FCFS初筛，后MIP精优）

5. **保持原计划结构**（用户明确要求不变更运行图顺序） → solver="fsfs"（先计划先服务）

6. **大面积多程度延误**（>5列列车且最大延误差异>15分钟） → solver="max-delay-first"（优先压缩最大延误）

7. **实时局部调整**（单点延误、、<5列列车） → solver="srpt"（最短剩余时间优先）

【输出格式】
{{"solver": "hierarchical", "reasoning": "中等规模18列列车，最大延误12分钟，适合分层求解兼顾速度和质量", "solver_config": {{"time_limit":120,"optimality_gap":0.05,"optimization_objective":"min_max_delay"}}}}

solver可选: mip, fcfs, fsfs, srpt, spt, max-delay-first, hierarchical, noop

只输出JSON对象，不要其他内容。""",
            required_output_fields=["solver", "reasoning", "solver_config"],
            temperature=0.1,
            max_tokens=256,
            tags=["solver", "selection", "execution"],
            version="2.0"
        )
        self.register_template(l3_template)

        # L4评估模板（v2.0 - 与layer4_evaluation对齐）
        l4_template = PromptTemplate(
            template_id="l4_evaluation",
            template_type=PromptTemplateType.L4_EVALUATION,
            template_name="L4评估",
            description="评估调度方案，生成专业评估报告和调度方案文档",
            system_prompt="""你是京广高铁（北京西→安阳东，13站，147列列车）高级调度评估专家。

## 你的职责
根据调度求解结果，生成专业的评估报告和调度方案文档。你的输出将直接用于调度决策汇报。

## 评估标准（基于京广高铁运营实际）
1. 正点率影响 — 优秀：延误延误<5分钟占比>90%；需关注：延误>15分钟超过3列
2. 最大延误控制 — 优秀：<10分钟；可接受：<20分钟；需干预：>30分钟
3. 延误均衡性 — 标准差越大越不均衡，理想状态是所有受影响列车延误相近
4. 执行可行性 — 追踪间隔是否满足最小安全间隔（3分钟）

## 输出要求
输出JSON，包含：llm_summary, feasibility_score, risk_warnings, natural_language_plan, comparison_analysis
- feasibility_score: 0-1评分，基于整体方案质量
- risk_warnings: 具体风险项（如"G1563在石家庄站延误12分钟，可能影响后续D1234发车"）
- natural_language_plan: 完整的自然语言调度方案（调整概述+具体调整+注意事项）
- comparison_analysis: 多方案对比分析（如有数据），否则留空字符串""",
            user_prompt_template="""【调度场景信息】
场景类型：{scene_type}
求解结果：{execution_result}

请评估延误和风险，给出0-1评分，并生成调度方案。

输出JSON：{{"llm_summary":"2-3句话评价","risk_warnings":[],"feasibility_score":0.85,"natural_language_plan":"【调整概述】...\\n【具体调整】...\\n【注意事项】...","comparison_analysis":""}}""",
            required_output_fields=["llm_summary", "feasibility_score", "risk_warnings", "natural_language_plan"],
            temperature=0.2,
            max_tokens=1024,
            tags=["evaluation", "risk_analysis", "plan_generation"],
            version="2.0"
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
4. 延误>30分钟 -> solver: "max-delay-first" (优先处理延误)

必须严格按照以下JSON格式输出，不要添加任何额外文字：
{"solver": "fcfs", "reasoning": "突发故障场景，需要快速响应", "solver_config": {}}

solver可选: mip, fcfs, max-delay-first, noop

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

        # L4自然语言调度方案生成模板（v2.0 - 专业调度指令格式）
        l4_natural_language_template = PromptTemplate(
            template_id="l4_natural_language_plan",
            template_type=PromptTemplateType.L4_EVALUATION,
            template_name="L4自然语言调度方案",
            description="生成人类可读的自然语言调度方案",
            system_prompt="""你是京广高铁调度指令撰写专家。负责将数值化调度结果转换为标准调度指令，供调度员执行和向上级汇报。

撰写规范：
1. 使用铁路调度标准术语（到发、通过、待避、追踪间隔、运行图调整）
2. 时间格式统一为HH:MM（24小时制）
3. 列车号使用标准格式（如G1563次）
4. 车站名使用标准全称
5. 安全注意事项必须醒目，涉及追踪间隔、限速值、封锁范围等关键数字要精确""",
            user_prompt_template="""根据调度结果生成标准调度指令。

场景：{scene_type}
位置：{delay_location}
列车：{affected_trains}
评估：{evaluation_summary}

调度结果：{execution_result}

生成包含以下内容的方案：
1. 【调整概述】事故原因+影响范围+总体策略（30-50字）
2. 【具体调整】按列车分条列出：各站点到发时间变化、通过/停靠变更、待避安排
3. 【注意事项】安全要求+关键节点+执行优先级

要求：
- 只输出JSON格式
- natural_language_plan 中使用\\n换行，分节清晰
- 时间变化必须包含"原计划X→调整后Y"格式

输出JSON：{{"natural_language_plan":"【调整概述】因石家庄站大风限速，G1563次等3列列车受影响，采取顺延发车策略。\\n\\n【具体调整】\\nG1563次列车：\\n- 石家庄站：原计划19:05发车→调整为19:35发车\\n- 高邑西站：原计划19:17通过→调整为19:47通过\\n\\n【注意事项】\\n1. 确保石家庄站追踪间隔不小于3分钟\\n2. 后续站点同步更新到发时间\\n3. 密切关注大风预警，必要时再次限速"}}""",
            required_output_fields=["natural_language_plan"],
            temperature=0.2,
            max_tokens=1024,
            tags=["natural_language", "dispatch_plan"],
            version="2.0"
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
