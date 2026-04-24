# -*- coding: utf-8 -*-
"""
第一层：数据建模层（整合L0预处理功能）
从调度员描述中生成事故卡片

职责说明（v5.0 整合版）：
- 整合L0预处理：构建CanonicalDispatchRequest
- LLM提取事故信息（scene_category, fault_type, location_code等）
- 回退推断逻辑（当LLM失败时）
- 构建AccidentCard
- **不**构建 NetworkSnapshot（由 SnapshotBuilder 负责）

v5.0 整合：
- 将L0预处理功能合并到L1
- 统一入口：从用户输入直接构建完整数据结构
- 保持向后兼容：仍支持传入canonical_request
"""

import logging
import os
import json
from typing import Dict, Any, Optional
from datetime import datetime
import re

from models.workflow_models import AccidentCard
from models.common_enums import fault_code_to_label, RequestSourceType, SceneTypeCode, FaultTypeCode
from models.preprocess_models import CanonicalDispatchRequest, LocationInfo, CompletenessInfo
from models.prompts import PromptContext
from railway_agent.adapters.llm_prompt_adapter import get_llm_prompt_adapter
from models.data_loader import validate_train_at_station
from config import LLMConfig, L1Config

logger = logging.getLogger(__name__)

# L1 微调模型系统提示词
L1_FINETUNED_SYSTEM_PROMPT = """你是铁路调度数据建模助手。负责从调度员描述中提取关键信息并生成事故卡片。只输出JSON，不要解释。

规则：
1. 场景：限速/天气→临时限速，故障→突发故障，封锁→区间封锁
2. 车站：石家庄→SJP,北京西→BJX,保定东→BDD,徐水东→XSD,高邑西→GYX,邢台东→XTD,邯郸东→HDD,安阳东→AYD,高碑店东→GBD,定州东→DZD,正定机场→ZDJ,杜家坎线路所→DJK,涿州东→ZBD
3. 区间格式：location_type=section, location_code="DJK-ZBD", affected_section="DJK-ZBD"
4. 车站格式：location_type=station, location_code="SJP", affected_section="SJP-SJP"
5. 提取列车号(如G1563)、延误分钟数、事件类型
6. fault_type未知则设"未知"，is_complete：列车号+位置+事件类型+延误时间齐全才为true

输出字段：scene_category, fault_type, expected_duration, affected_section, location_type, location_code, location_name, affected_train_ids, is_complete, missing_fields"""

# 调度员操作指南检索器（关键词匹配版）
class DispatcherOperationGuideRetriever:
    """
    调度员操作指南检索器
    基于关键词匹配，从知识库(operations/目录)中检索调度员操作指南
    """

    def __init__(self):
        self.knowledge_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'data', 'knowledge'
        )
        self.knowledge_base = self._load_operation_knowledge()

    def _load_operation_knowledge(self) -> Dict[str, Any]:
        """
        加载调度员操作知识库
        优先从operations/目录的JSON文件加载，fallback到内置知识
        """
        knowledge = {}

        # 1. 尝试从operations/目录加载JSON知识库
        operations_dir = os.path.join(self.knowledge_dir, "operations")
        if os.path.exists(operations_dir):
            try:
                knowledge = self._load_from_operations_dir(operations_dir)
                if knowledge:
                    logger.info(f"[L1] 从operations目录加载了 {len(knowledge)} 个操作知识库")
                    return knowledge
            except Exception as e:
                logger.warning(f"[L1] 加载operations目录失败: {e}")

        # 2. Fallback: 使用内置知识
        knowledge = self._get_fallback_knowledge()
        return knowledge

    def _load_from_operations_dir(self, operations_dir: str) -> Dict[str, Any]:
        """从operations/目录递归加载JSON格式知识库（支持子文件夹层级）"""
        knowledge = {}
        loaded_files = 0
        try:
            for root, _, files in os.walk(operations_dir):
                for filename in files:
                    if not filename.endswith('.json'):
                        continue
                    filepath = os.path.join(root, filename)
                    rel_path = os.path.relpath(filepath, operations_dir)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            data = json.load(f)

                        # 解析JSON知识库（支持两种结构：有scenes数组 或 直接是单场景）
                        scenes = data.get('scenes', [])
                        if not scenes and 'scene_id' in data:
                            scenes = [data]

                        for scene in scenes:
                            scene_id = scene.get('scene_id', '')
                            scene_name = scene.get('scene_name', '')
                            category = scene.get('category', '')

                            # 提取关键词（primary权重高，secondary权重低）
                            keywords_data = scene.get('keywords', {})
                            keywords_primary = keywords_data.get('primary', [])
                            keywords_secondary = keywords_data.get('secondary', [])
                            keywords_all = list(keywords_primary)
                            synonyms = keywords_data.get('synonyms', {})
                            for syn_list in synonyms.values():
                                keywords_all.extend(syn_list)

                            # 提取操作步骤（保留step结构，不再flatten）
                            steps = []
                            for op in scene.get('operations', []):
                                steps.append({
                                    "step_id": op.get('step_id', 0),
                                    "phase": op.get('phase', ''),
                                    "priority": op.get('priority', 'medium'),
                                    "time_limit": op.get('time_limit', ''),
                                    "actions": op.get('actions', [])
                                })

                            # 提取关键要点（key_notes）用于辅助匹配
                            key_notes = scene.get('key_notes', [])

                            knowledge[scene_name] = {
                                "scene_id": scene_id,
                                "category": category,
                                "keywords_primary": keywords_primary,
                                "keywords_secondary": keywords_secondary,
                                "keywords_all": keywords_all,
                                "steps": steps,
                                "key_notes": key_notes,
                                "source": f"operations/{rel_path}"
                            }
                        loaded_files += 1
                    except Exception as e:
                        logger.warning(f"[L1] 加载 {rel_path} 失败: {e}")
            logger.info(f"[L1] 从operations目录递归加载了 {loaded_files} 个知识库文件，共 {len(knowledge)} 个场景")
        except Exception as e:
            logger.warning(f"[L1] 读取operations目录失败: {e}")

        return knowledge

    def _get_fallback_knowledge(self) -> Dict[str, Any]:
        """Fallback: 内置的默认知识库（当无法加载JSON时使用，结构与JSON加载保持一致）"""
        knowledge = {
            "大风天气行车组织": {
                "scene_id": "FALLBACK_WIND",
                "category": "自然灾害",
                "keywords_primary": ["大风", "风速", "侧风", "台风", "强风"],
                "keywords_secondary": ["限速", "报警"],
                "keywords_all": ["大风", "风速", "侧风", "台风", "强风", "限速", "报警"],
                "steps": [
                    {
                        "step_id": 1,
                        "phase": "立即处置",
                        "priority": "critical",
                        "time_limit": "立即",
                        "actions": [
                            "立即确认大风报警地点（区段、里程）",
                            "确认风速监测子系统显示的风速值",
                            "根据风速设置列控限速：15-20m/s限速200km/h，20-25m/s限速120km/h，>25m/s禁止运行",
                            "立即呼叫已进入区间的列车司机，通知限速要求",
                            "若显示禁止运行报警，立即命令列车停车",
                            "持续监控风速变化，每5分钟确认一次"
                        ]
                    }
                ],
                "key_notes": ["大风天气需持续监控风速变化"],
                "source": "内置知识库"
            },
            "雨天天气行车组织": {
                "scene_id": "FALLBACK_RAIN",
                "category": "自然灾害",
                "keywords_primary": ["大雨", "暴雨", "降雨", "洪水", "积水"],
                "keywords_secondary": ["限速", "扣停"],
                "keywords_all": ["大雨", "暴雨", "降雨", "洪水", "积水", "限速", "扣停"],
                "steps": [
                    {
                        "step_id": 1,
                        "phase": "立即处置",
                        "priority": "critical",
                        "time_limit": "立即",
                        "actions": [
                            "立即确认降雨报警地点和等级",
                            "确认降雨量监测数据（小时雨量/连续雨量）",
                            "根据警戒等级执行限速或扣停",
                            "暴雨持续期间每10分钟确认一次雨量数据",
                            "解除警戒后逐步恢复常速运行"
                        ]
                    }
                ],
                "key_notes": ["暴雨期间需密切监控雨量数据"],
                "source": "内置知识库"
            },
            "冰雪天气行车组织": {
                "scene_id": "FALLBACK_ICE",
                "category": "自然灾害",
                "keywords_primary": ["冰雪", "结冰", "冻雨", "降雪", "道岔冻结", "覆冰"],
                "keywords_secondary": ["限速", "融雪"],
                "keywords_all": ["冰雪", "结冰", "冻雨", "降雪", "道岔冻结", "覆冰", "限速", "融雪"],
                "steps": [
                    {
                        "step_id": 1,
                        "phase": "立即处置",
                        "priority": "critical",
                        "time_limit": "立即",
                        "actions": [
                            "立即确认冰雪天气报警地点和类型",
                            "根据冰雪情况设置列控限速",
                            "冰雪天气持续期间每15分钟确认一次设备状态",
                            "降雪结束后组织添乘检查线路状况"
                        ]
                    }
                ],
                "key_notes": ["冰雪天气注意道岔融雪装置状态"],
                "source": "内置知识库"
            },
            "设备故障行车": {
                "scene_id": "FALLBACK_FAULT",
                "category": "设备故障行车",
                "keywords_primary": ["设备故障", "信号故障", "接触网故障", "线路故障", "道岔故障"],
                "keywords_secondary": ["扣停", "抢修"],
                "keywords_all": ["设备故障", "信号故障", "接触网故障", "线路故障", "道岔故障", "扣停", "抢修"],
                "steps": [
                    {
                        "step_id": 1,
                        "phase": "立即处置",
                        "priority": "critical",
                        "time_limit": "立即",
                        "actions": [
                            "立即扣停后续列车",
                            "确认故障类型和影响范围",
                            "通知相关设备管理部门",
                            "评估故障恢复时间",
                            "调整后续列车时刻表"
                        ]
                    }
                ],
                "key_notes": ["设备故障需第一时间扣停列车"],
                "source": "内置知识库"
            },
            "临时限速调度": {
                "scene_id": "FALLBACK_LIMIT",
                "category": "自然灾害",
                "keywords_primary": ["临时限速", "限速运行", "限速命令"],
                "keywords_secondary": ["列控", "调度命令"],
                "keywords_all": ["临时限速", "限速运行", "限速命令", "列控", "调度命令"],
                "steps": [
                    {
                        "step_id": 1,
                        "phase": "立即处置",
                        "priority": "critical",
                        "time_limit": "立即",
                        "actions": [
                            "确认限速区段和限速值",
                            "计算受影响列车数量和延误时间",
                            "设置列控限速",
                            "调整列车发车时间",
                            "发布限速调度命令"
                        ]
                    }
                ],
                "key_notes": ["临时限速需发布正式调度命令"],
                "source": "内置知识库"
            },
            "区间封锁处置": {
                "scene_id": "FALLBACK_BLOCK",
                "category": "非正常行车",
                "keywords_primary": ["区间封锁", "线路封锁", "封锁区间"],
                "keywords_secondary": ["停运", "绕行"],
                "keywords_all": ["区间封锁", "线路封锁", "封锁区间", "停运", "绕行"],
                "steps": [
                    {
                        "step_id": 1,
                        "phase": "立即处置",
                        "priority": "critical",
                        "time_limit": "立即",
                        "actions": [
                            "确认封锁区段和原因",
                            "停止新列车发车",
                            "区间内列车就近停靠",
                            "启动应急预案"
                        ]
                    }
                ],
                "key_notes": ["区间封锁期间禁止进入封锁区段"],
                "source": "内置知识库"
            }
        }
        return knowledge

    def retrieve_operations(self, scene_category: str, fault_type: str, user_input: str) -> Optional[Dict[str, Any]]:
        """
        检索调度员操作指南（两层检索策略）
        Step 1: 用 category 预过滤，将36个场景缩小到候选集
        Step 2: 在候选集内用 keywords 加权匹配，返回最佳单场景

        Args:
            scene_category: 场景大类（自然灾害/设备故障行车/非正常行车/救援组织）
            fault_type: 故障类型（大风/暴雨/接触网跳闸等）
            user_input: 用户原始输入

        Returns:
            最佳匹配的操作指南字典
        """
        if not self.knowledge_base:
            return None

        query = f"{fault_type} {user_input}".lower()

        # Step 1: Category 预过滤
        # 支持模糊匹配：如 scene_category="突发故障" 可匹配 category="设备故障"
        candidates = {}
        for scene_name, knowledge in self.knowledge_base.items():
            cat = knowledge.get("category", "")
            if not scene_category:
                candidates[scene_name] = knowledge
                continue
            # 精确匹配或互相包含
            if scene_category == cat or scene_category in cat or cat in scene_category:
                candidates[scene_name] = knowledge

        # 如果 category 过滤后为空，回退到全库检索（兜底）
        if not candidates:
            candidates = self.knowledge_base

        # Step 2: 在候选集内用 keywords 加权匹配
        best_match = None
        best_score = 0

        for scene_name, knowledge in candidates.items():
            score = 0

            # primary 关键词匹配（权重3）
            for kw in knowledge.get("keywords_primary", []):
                if kw.lower() in query:
                    score += 3

            # secondary 关键词匹配（权重1）
            for kw in knowledge.get("keywords_secondary", []):
                if kw.lower() in query:
                    score += 1

            # scene_name 名称匹配（权重5）
            if fault_type and fault_type.lower() in scene_name.lower():
                score += 5
            # 用 user_input 中的关键词也匹配 scene_name
            for term in user_input.lower().split():
                if len(term) >= 2 and term in scene_name.lower():
                    score += 2

            # key_notes 辅助匹配（权重1，用于处理边缘情况）
            for note in knowledge.get("key_notes", []):
                for term in query.split():
                    if len(term) >= 2 and term in note.lower():
                        score += 0.5

            if score > best_score:
                best_score = score
                # 只保留 priority=critical/high 的steps（当前阶段操作），减少冗余
                filtered_steps = [
                    s for s in knowledge.get("steps", [])
                    if s.get("priority") in ("critical", "high")
                ]
                # 如果没有critical/high，取前2个step兜底
                if not filtered_steps and knowledge.get("steps"):
                    filtered_steps = knowledge["steps"][:2]
                best_match = {
                    "scene_name": scene_name,
                    "scene_id": knowledge.get("scene_id", ""),
                    "category": knowledge.get("category", ""),
                    "steps": filtered_steps,
                    "key_notes": knowledge.get("key_notes", []),
                    "source": knowledge["source"],
                    "match_score": score
                }

        # Fallback: 如果 category 预过滤后无匹配，回退到全库检索（处理LLM分类偏差）
        if best_score == 0:
            for scene_name, knowledge in self.knowledge_base.items():
                if scene_name in candidates:
                    continue  # 已检索过，跳过
                score = 0
                for kw in knowledge.get("keywords_primary", []):
                    if kw.lower() in query:
                        score += 3
                for kw in knowledge.get("keywords_secondary", []):
                    if kw.lower() in query:
                        score += 1
                if fault_type and fault_type.lower() in scene_name.lower():
                    score += 5
                for term in user_input.lower().split():
                    if len(term) >= 2 and term in scene_name.lower():
                        score += 2
                if score > best_score:
                    best_score = score
                    filtered_steps = [
                        s for s in knowledge.get("steps", [])
                        if s.get("priority") in ("critical", "high")
                    ]
                    if not filtered_steps and knowledge.get("steps"):
                        filtered_steps = knowledge["steps"][:2]
                    best_match = {
                        "scene_name": scene_name,
                        "scene_id": knowledge.get("scene_id", ""),
                        "category": knowledge.get("category", ""),
                        "steps": filtered_steps,
                        "key_notes": knowledge.get("key_notes", []),
                        "source": knowledge["source"],
                        "match_score": score
                    }

        # 设置阈值：score <= 0 视为无有效匹配
        return best_match if best_score > 0 else None

    def format_operations_for_display(self, operations_data: Dict[str, Any]) -> str:
        """格式化操作指南为显示文本（按step分组）"""
        if not operations_data:
            return ""

        lines = [
            f"\n【调度员操作指南 - {operations_data['scene_name']}】",
            f"来源：{operations_data['source']}\n"
        ]

        steps = operations_data.get("steps", [])
        if not steps:
            # 兼容旧结构（operations扁平列表）
            for i, op in enumerate(operations_data.get("operations", []), 1):
                lines.append(f"{i}. {op}")
            return "\n".join(lines)

        for step in steps:
            phase = step.get("phase", "")
            priority = step.get("priority", "")
            time_limit = step.get("time_limit", "")
            actions = step.get("actions", [])
            if not actions:
                continue
            # step标题
            header = f"\n【{phase}】"
            if priority:
                header += f" 优先级:{priority}"
            if time_limit:
                header += f" 时限:{time_limit}"
            lines.append(header)
            # actions
            for action in actions:
                lines.append(f"  {action}")

        return "\n".join(lines)


class Layer1DataModeling:
    """
    第一层：数据建模层
    使用LLM提取事故信息，构建 AccidentCard
    不构建 NetworkSnapshot（由 SnapshotBuilder 负责）
    """

    # 统一完整性判定规则：train + location + event（根据需求）
    # 理由：完整调度需要知道车次、位置、场景类型即可，延误时间可选
    COMPLETENESS_REQUIRES_TRAIN = True
    COMPLETENESS_REQUIRES_LOCATION = True
    COMPLETENESS_REQUIRES_EVENT = True
    # 注意：延误时间不再是必填项

    def __init__(self):
        """初始化第一层"""
        self.prompt_adapter = get_llm_prompt_adapter()
        self.operations_retriever = DispatcherOperationGuideRetriever()
        self.finetuned_model = None

        # 如果启用了微调模型，初始化模型
        if L1Config.USE_FINETUNED_MODEL:
            self._init_finetuned_model()

    def _init_finetuned_model(self):
        """初始化微调模型"""
        try:
            if L1Config.FINETUNED_MODEL_PROVIDER == "ollama":
                import openai
                self.finetuned_model = openai.OpenAI(
                    base_url=L1Config.FINETUNED_MODEL_BASE_URL,
                    api_key="ollama"  # Ollama 不需要真实 API key
                )
                logger.info(f"[L1] 微调模型初始化成功 (Ollama): {L1Config.FINETUNED_MODEL_NAME}")
            elif L1Config.FINETUNED_MODEL_PROVIDER == "vllm":
                import openai
                self.finetuned_model = openai.OpenAI(
                    base_url=L1Config.FINETUNED_MODEL_BASE_URL,
                    api_key="vllm"  # vLLM 不需要真实 API key
                )
                logger.info(f"[L1] 微调模型初始化成功 (vLLM): {L1Config.FINETUNED_MODEL_NAME}")
            elif L1Config.FINETUNED_MODEL_PROVIDER == "transformers":
                # 使用 Transformers 原生加载
                from transformers import AutoModelForCausalLM, AutoTokenizer
                self.finetuned_tokenizer = AutoTokenizer.from_pretrained(
                    L1Config.FINETUNED_MODEL_PATH
                )
                self.finetuned_model = AutoModelForCausalLM.from_pretrained(
                    L1Config.FINETUNED_MODEL_PATH,
                    device_map=L1Config.FINETUNED_MODEL_DEVICE
                )
                logger.info(f"[L1] 微调模型初始化成功 (Transformers): {L1Config.FINETUNED_MODEL_PATH}")
            else:
                logger.warning(f"[L1] 未知的微调模型提供商: {L1Config.FINETUNED_MODEL_PROVIDER}")
        except Exception as e:
            logger.error(f"[L1] 微调模型初始化失败: {e}")
            if L1Config.FALLBACK_TO_PROMPT_ON_ERROR:
                logger.warning("[L1] 将回退到 prompt 模式")
            else:
                raise

    @classmethod
    def _check_completeness(cls, accident_card_data: Dict[str, Any]) -> tuple:
        """
        统一的完整性检查逻辑（方案：列车号 + 位置 + 事件类型）

        Args:
            accident_card_data: 事故卡片数据字典

        Returns:
            tuple: (is_complete, missing_fields)
        """
        has_train = bool(accident_card_data.get("affected_train_ids"))
        has_location = bool(
            accident_card_data.get("location_code") or
            accident_card_data.get("location_name")
        )
        has_event = bool(accident_card_data.get("scene_category"))

        # 统一规则：train + location + event（延误时间不再是必填项）
        is_complete = has_train and has_location and has_event

        missing_fields = []
        if not has_train:
            missing_fields.append("列车号")
        if not has_location:
            missing_fields.append("位置")
        if not has_event:
            missing_fields.append("事件类型")

        return is_complete, missing_fields

    def execute(
        self,
        user_input: str,
        canonical_request: Optional[Any] = None,
        enable_rag: bool = True,
        previous_accident_card: Optional[AccidentCard] = None
    ) -> Dict[str, Any]:
        """
        执行第一层数据建模（支持信息补全对话）

        Args:
            user_input: 用户输入
            canonical_request: L0预处理结果（可选）
            enable_rag: 是否启用RAG
            previous_accident_card: 之前提取的部分事故卡片（用于信息补全）

        Returns:
            Dict: 包含 accident_card 的字典，如果信息不完整会返回询问问题
        """
        logger.debug("[L1] 数据建模层")

        # 如果有L0预处理结果，直接使用
        if canonical_request and canonical_request.scene_type_code:
            return self._use_preprocessed_result(canonical_request)

        # 智能分流：简单输入走规则提取，复杂输入走 LLM
        if self._is_simple_input(user_input):
            logger.info("[L1] 简单输入，使用规则提取（跳过LLM）")
            accident_card = self._fallback_extraction(user_input)
            # 包装为与 LLM 路径一致的格式
            from models.prompts import PromptResponse
            import uuid
            request_id = str(uuid.uuid4())
            template_id = "l1_data_modeling_rule"
            response = PromptResponse(
                request_id=request_id,
                template_id=template_id,
                is_valid=True,
                parsed_output={"accident_card": accident_card.model_dump(), "_response_source": "rule_fast_path"},
                raw_response="规则快速提取",
                model_used="rule_fast_path"
            )
        elif L1Config.USE_FINETUNED_MODEL and self.finetuned_model is not None:
            logger.info("[L1] 使用微调模型进行实体提取")
            accident_card, response = self._extract_with_finetuned_model(user_input)
        else:
            logger.info("[L1] 使用Prompt进行实体提取")
            accident_card, response = self._extract_with_prompt(user_input, enable_rag)

        # 如果有之前的事故卡片，合并信息
        if previous_accident_card:
            accident_card = self._merge_accident_cards(previous_accident_card, accident_card)
            logger.debug(f"[L1] 合并之前的事故卡片信息")

        # 处理提取失败的情况
        if accident_card is None:
            logger.error("[L1] 实体提取失败")
            return {
                "accident_card": None,
                "llm_response": response.raw_response if response else "",
                "llm_response_type": response.model_used if response else "",
                "response_source": "llm",
                "needs_more_info": True,
                "missing_questions": ["请重新提供事故信息"],
                "error": "实体提取失败"
            }

        # 检查信息完整性
        if not accident_card.is_complete:
            missing_questions = self._generate_missing_questions(accident_card.missing_fields)
            logger.debug(f"[L1] 信息不完整，需要补充: {accident_card.missing_fields}")
        # 构建返回结果
        result = {
            "accident_card": accident_card,
            "llm_response": response.raw_response,
            "llm_response_type": response.model_used,
            "response_source": "llm" if response.is_valid and not response.parsed_output.get("_response_source", "").startswith("rule_based") else "fallback_rule"
        }

        # 如果信息不完整，生成询问问题
        if not accident_card.is_complete:
            result["needs_more_info"] = True
            result["missing_questions"] = self._generate_missing_questions(accident_card.missing_fields)
        else:
            result["needs_more_info"] = False
            result["missing_questions"] = []

        # 检索调度员操作指南（Prompt+RAG）
        operations_guide = self.operations_retriever.retrieve_operations(
            scene_category=accident_card.scene_category or "",
            fault_type=accident_card.fault_type or "",
            user_input=user_input
        )
        if operations_guide:
            result["dispatcher_operations"] = operations_guide
            # 记录日志（精简为单行）
            logger.info(f"[L1] 调度指南: {operations_guide['scene_name']} (匹配度{operations_guide['match_score']})")

        # 精简事故卡片日志
        logger.info(f"[L1] 事故卡片: {accident_card.scene_category}/{accident_card.fault_type} @ {accident_card.location_name}, "
                   f"延误{accident_card.expected_duration}分钟, 影响{len(accident_card.affected_train_ids or [])}列车")

        return result

    def _use_preprocessed_result(
        self,
        canonical_request: Any
    ) -> Dict[str, Any]:
        """使用L0预处理结果"""
        scene_label = canonical_request.scene_type_label or "临时限速"

        # 根据位置信息构建 AccidentCard
        location = canonical_request.location
        if location:
            # 判断是区间还是车站
            if location.section_id:
                # 区间场景
                location_type = "section"
                location_code = location.section_id
                location_name = location.station_name  # 对于区间，station_name 保存的是区间名称，如"徐水东-保定东"
                affected_section = location.section_id
                logger.info(f"[L0] 构建区间事故卡片: section_id={location.section_id}, name={location.station_name}")
            else:
                # 车站场景
                location_type = "station"
                location_code = location.station_code or ""
                location_name = location.station_name or ""
                affected_section = f"{location_code}-{location_code}" if location_code else ""
                logger.debug(f"[L0] 构建车站事故卡片: station_code={location_code}, name={location_name}")
        else:
            # 没有位置信息
            location_type = "station"
            location_code = ""
            location_name = ""
            affected_section = ""

        accident_card = AccidentCard(
            fault_type=fault_code_to_label(canonical_request.fault_type) if canonical_request.fault_type else "未知",
            scene_category=scene_label,
            start_time=datetime.fromisoformat(canonical_request.event_time) if canonical_request.event_time else None,
            expected_duration=(canonical_request.reported_delay_seconds or 0) / 60 if canonical_request.reported_delay_seconds else None,
            affected_section=affected_section,
            location_type=location_type,
            location_code=location_code,
            location_name=location_name,
            affected_train_ids=canonical_request.affected_train_ids or [],
            is_complete=canonical_request.completeness.can_enter_solver if canonical_request.completeness else False,
            missing_fields=canonical_request.completeness.missing_fields if canonical_request.completeness else []
        )

        logger.debug(f"第一层完成(L0): scene_category={accident_card.scene_category}, location_type={accident_card.location_type}, location_code={accident_card.location_code}")

        # 检索调度员操作指南
        operations_guide = self.operations_retriever.retrieve_operations(
            scene_category=accident_card.scene_category,
            fault_type=accident_card.fault_type,
            user_input=canonical_request.raw_text if hasattr(canonical_request, 'raw_text') else ""
        )

        result = {
            "accident_card": accident_card,
            "llm_response": "使用L0预处理结果",
            "dispatcher_operations": operations_guide
        }

        # 记录操作指南
        if operations_guide:
            logger.debug("=" * 50)
            logger.debug("【调度员操作指南】")
            logger.debug(f"  场景: {operations_guide['scene_name']}")
            logger.debug(f"  匹配度: {operations_guide['match_score']}")
            logger.debug(f"  来源: {operations_guide['source']}")
            logger.debug("=" * 50)

        return result

    def _build_accident_card(
        self,
        acc_card_data: Dict[str, Any],
        user_input: str
    ) -> AccidentCard:
        """
        构建事故卡片
        包含回退推断逻辑
        """
        # 回退推断：从用户输入中提取缺失字段
        user_input_lower = user_input.lower()

        # 提取列车号
        if not acc_card_data.get("affected_train_ids"):
            train_match = re.search(r'([GCDZ]\d+)', user_input)
            if train_match:
                acc_card_data["affected_train_ids"] = [train_match.group(1)]

        # 推断故障类型
        if not acc_card_data.get("fault_type") or acc_card_data.get("fault_type") == "未知":
            if "风" in user_input or "大风" in user_input:
                acc_card_data["fault_type"] = "大风"
            elif "雨" in user_input or "暴雨" in user_input:
                acc_card_data["fault_type"] = "暴雨"
            elif "设备" in user_input or "故障" in user_input:
                acc_card_data["fault_type"] = "设备故障"
            elif "延误" in user_input or "晚点" in user_input:
                acc_card_data["fault_type"] = "预计晚点"

        # 推断场景类别（修改优先级：先判断天气相关）
        if not acc_card_data.get("scene_category"):
            # 场景类型只有三种：临时限速、突发故障、区间中断
            # 具体天气/故障作为fault_type
            if "封锁" in user_input:
                acc_card_data["scene_category"] = "区间中断"
            elif "风" in user_input or "大风" in user_input or "雨" in user_input or "暴雨" in user_input or "雪" in user_input or "冰雪" in user_input or "限速" in user_input:
                # 所有天气相关和限速都归为临时限速场景
                acc_card_data["scene_category"] = "临时限速"
            else:
                acc_card_data["scene_category"] = "突发故障"

        # 提取位置信息（车站或区间）
        # 车站代码映射表
        station_to_code = {
            "石家庄": "SJP", "北京西": "BJX", "保定东": "BDD", "定州东": "DZD",
            "徐水东": "XSD", "涿州东": "ZBD", "高碑店东": "GBD", "正定机场": "ZDJ",
            "高邑西": "GYX", "邢台东": "XTD", "邯郸东": "HDD", "安阳东": "AYD",
            "杜家坎": "DJK", "杜家坎线路所": "DJK"
        }

        # 创建反向映射（代码到名称）
        code_to_station = {code: name for name, code in station_to_code.items()}

        # 只有当LLM没有提取到位置信息时，才尝试规则提取
        if not acc_card_data.get("location_code") and not acc_card_data.get("location_name"):
            # 先尝试提取区间（使用正则匹配区间格式）
            # 匹配格式：A到B、A至B、A-B、A和B、A与B等，以及站码格式
            section_patterns = [
                # 站码区间：XSD-BDD、XSD-BDD区间、XSD到BDD
                r'([A-Z]{3})[－\-至到]\s*([A-Z]{3})(?:区间|段)?',
                # 中文车站名：石家庄到保定东
                r'([^与和－\-]{2,})[－\-到至]\s*([^与和－\-]{2,})(?:站|线路所|区间|段)?',
                # 中文车站名（之间）：涿州东与高碑店东之间
                r'([^与和－\-]{2,})[与和]\s*([^与和－\-]{2,})(?:站|线路所)?(?:之间)?',
            ]

            for pattern in section_patterns:
                section_match = re.search(pattern, user_input)
                if section_match:
                    station1 = section_match.group(1)
                    station2 = section_match.group(2)

                    # 查找两个站点的代码和名称
                    code1, code2 = None, None
                    name1, name2 = None, None

                    # 优先匹配站码
                    if station1 in code_to_station:
                        code1 = station1
                        name1 = code_to_station[station1]
                    else:
                        # 匹配中文名称（精确匹配）
                        for station_name, code in station_to_code.items():
                            if station1.strip() == station_name or (station_name in station1 and len(station1) >= len(station_name)):
                                code1 = code
                                name1 = station_name
                                break

                    if station2 in code_to_station:
                        code2 = station2
                        name2 = code_to_station[station2]
                    else:
                        # 匹配中文名称（精确匹配）
                        for station_name, code in station_to_code.items():
                            if station2.strip() == station_name or station_name in station2 and len(station2) >= len(station_name):
                                code2 = code
                                name2 = station_name
                                break

                    # 如果两个站点都找到且不同，则构建区间
                    if code1 and code2 and code1 != code2:
                        acc_card_data["location_type"] = "section"
                        acc_card_data["location_code"] = f"{code1}-{code2}"
                        acc_card_data["location_name"] = f"{name1}-{name2}"
                        acc_card_data["affected_section"] = f"{code1}-{code2}"
                        logger.debug(f"[_build_accident_card] 提取到区间: {name1}-{name2} ({code1}-{code2})")
                        break

            # 如果没有提取到区间，则提取单个车站
            if not acc_card_data.get("location_code"):
                for station_name, code in station_to_code.items():
                    if station_name in user_input:
                        acc_card_data["location_type"] = "station"
                        acc_card_data["location_name"] = station_name
                        acc_card_data["location_code"] = code
                        acc_card_data["affected_section"] = f"{code}-{code}"
                        logger.debug(f"[_build_accident_card] 提取到车站: {station_name} ({code})")
                        break

        # 提取延误时间（如果LLM没有提取到）
        if not acc_card_data.get("expected_duration"):
            delay_match = re.search(r'(\d+)\s*分钟', user_input)
            if delay_match:
                acc_card_data["expected_duration"] = int(delay_match.group(1))
                logger.debug(f"[_build_accident_card] 从用户输入提取延误时间: {acc_card_data['expected_duration']}分钟")

        # 使用统一的完整性判定逻辑
        if not acc_card_data.get("is_complete") or not acc_card_data.get("missing_fields"):
            is_complete, missing_fields = self._check_completeness(acc_card_data)
            acc_card_data["is_complete"] = is_complete
            acc_card_data["missing_fields"] = missing_fields

        return AccidentCard(
            fault_type=acc_card_data.get("fault_type", "未知"),
            scene_category=acc_card_data.get("scene_category", "临时限速"),
            affected_section=acc_card_data.get("affected_section", ""),
            location_type=acc_card_data.get("location_type", "station"),
            location_code=acc_card_data.get("location_code", ""),
            location_name=acc_card_data.get("location_name", ""),
            affected_train_ids=acc_card_data.get("affected_train_ids", []),
            is_complete=acc_card_data.get("is_complete", False),
            missing_fields=acc_card_data.get("missing_fields", []),
            expected_duration=acc_card_data.get("expected_duration"),
            start_time=datetime.fromisoformat(acc_card_data.get("start_time", "2024-01-15T10:00:00")) if acc_card_data.get("start_time") else datetime.now()
        )

    def _is_simple_input(self, user_input: str) -> bool:
        """
        判断输入是否足够简单，可以用规则直接提取。

        简单输入标准（基于京广高铁真实调度场景）：
        1. 有明确的场景类型（临时限速/突发故障/区间封锁）
        2. 有明确的位置信息（车站或区间）
        3. 有明确的延误/事件信息

        复杂输入需要 LLM 推理的场景：
        - 模糊的描述，需要理解语义关系
        - 多列车/大范围影响的复杂场景
        - 需要从上下文推断信息
        """
        import re

        # 检查是否有明确的场景类型关键词
        scene_keywords = [
            '临时限速', '限速', '突发故障', '故障', '区间封锁', '封锁', '区间中断',
            '大风', '暴雨', '大雪', '冰雪', '设备故障', '信号故障', '接触网故障'
        ]
        has_scene = any(kw in user_input for kw in scene_keywords)

        # 检查是否有明确的位置信息（京广高铁13站）
        location_keywords = [
            '站', '区间', '北京西', 'BJX', '杜家坎', 'DJK', '涿州东', 'ZBD',
            '高碑店东', 'GBD', '保定东', 'BDD', '定州东', 'DZD', '正定机场', 'ZDJ',
            '石家庄', 'SJP', '高邑西', 'GYX', '邢台东', 'XTD', '邯郸东', 'HDD', '安阳东', 'AYD',
            '徐水东', 'XSD'
        ]
        has_location = any(kw in user_input for kw in location_keywords)

        # 检查是否有明确的延误/事件信息
        event_keywords = [
            '延误', '晚点', '限速', '故障', '封锁', '分钟', '小时',
            '恢复', '解除', '预计', '持续'
        ]
        has_event = any(kw in user_input for kw in event_keywords)

        # 场景+位置+事件都明确 → 规则可处理（约60%的真实调度场景）
        if has_scene and has_location and has_event:
            return True

        # 场景+位置明确，有列车号 → 规则可处理
        has_train = bool(re.search(r'[GCDZ]\d+', user_input))
        if has_scene and has_location and has_train:
            return True

        # 位置+事件明确（可能是标准化的限速场景）→ 规则可处理
        if has_location and has_event:
            return True

        return False

    def _fallback_extraction(self, user_input: str) -> AccidentCard:
        """
        回退提取：基于规则的提取（京广高铁调度专家版）

        基于真实调度场景优化：
        1. 覆盖京广高铁常见的故障类型和场景
        2. 精确匹配13个车站和区间
        3. 支持多种延误时间表达方式
        """
        user_input_lower = user_input.lower()

        # 默认值
        scene_category = "突发故障"
        fault_type = "未知"
        location_code = ""
        location_name = ""
        affected_section = ""
        affected_train_ids = []
        expected_duration = None

        # 推断场景类型（只有三种：临时限速、突发故障、区间中断）
        # 天气/具体故障类型作为fault_type
        if any(kw in user_input for kw in ["封锁", "区间中断", "线路中断", "完全中断", "无法通行", "停运"]):
            scene_category = "区间中断"
        elif any(kw in user_input for kw in ["限速", "大风", "暴雨", "降雪", "冰雪", "雨量", "风速",
                                           "天气", "自然灾害", "泥石流", "塌方", "水害", "台风", "风", "雨", "雪",
                                           "冰雹", "雾霾", "沙尘"]):
            scene_category = "临时限速"
        else:
            scene_category = "突发故障"

        # 推断故障类型（基于京广高铁真实故障统计）
        if any(kw in user_input for kw in ["大风", "强风", "侧风", "台风"]):
            fault_type = "大风"
        elif any(kw in user_input for kw in ["暴雨", "大雨", "降雨", "洪水", "积水"]):
            fault_type = "暴雨"
        elif any(kw in user_input for kw in ["雪", "降雪", "大雪", "暴雪", "冰雪", "结冰", "冻雨"]):
            fault_type = "冰雪"
        elif any(kw in user_input for kw in ["设备故障", "设备"]):
            fault_type = "设备故障"
        elif any(kw in user_input for kw in ["信号故障", "信号"]):
            fault_type = "信号故障"
        elif any(kw in user_input for kw in ["接触网故障", "接触网", "供电", "停电"]):
            fault_type = "接触网故障"
        elif any(kw in user_input for kw in ["道岔故障", "道岔"]):
            fault_type = "道岔故障"
        elif any(kw in user_input for kw in ["线路故障", "线路", "轨道", "钢轨"]):
            fault_type = "线路故障"
        elif any(kw in user_input for kw in ["故障"]):
            fault_type = "设备故障"

        # 提取列车号（支持多个列车）
        train_matches = re.findall(r'([GCDZ]\d+)', user_input)
        if train_matches:
            affected_train_ids = train_matches

        # 提取延误时间（支持多种表达方式）
        # 支持格式："15分钟"、"15分"、"15 min"、"(15分钟)"、"延误15"
        delay_patterns = [
            r'(\d+)\s*分钟',
            r'(\d+)\s*分',
            r'(\d+)\s*min',
            r'延误[：:]\s*(\d+)',
            r'晚点[：:]\s*(\d+)',
            r'延误\s*(\d+)',
            r'晚点\s*(\d+)'
        ]
        for pattern in delay_patterns:
            delay_match = re.search(pattern, user_input)
            if delay_match:
                expected_duration = int(delay_match.group(1))
                logger.debug(f"[_fallback_extraction] 从用户输入提取延误时间: {expected_duration}分钟")
                break

        # 提取位置信息（车站或区间，京广高铁13站完整版）
        station_to_code = {
            "北京西": "BJX", "BJX": "BJX",
            "杜家坎": "DJK", "杜家坎线路所": "DJK", "DJK": "DJK",
            "涿州东": "ZBD", "ZBD": "ZBD",
            "高碑店东": "GBD", "GBD": "GBD",
            "保定东": "BDD", "BDD": "BDD",
            "定州东": "DZD", "DZD": "DZD",
            "正定机场": "ZDJ", "ZDJ": "ZDJ",
            "石家庄": "SJP", "SJP": "SJP",
            "高邑西": "GYX", "GYX": "GYX",
            "邢台东": "XTD", "XTD": "XTD",
            "邯郸东": "HDD", "HDD": "HDD",
            "安阳东": "AYD", "AYD": "AYD",
            "徐水东": "XSD", "XSD": "XSD"
        }
        location_type = "station"  # 默认为车站

        # 创建反向映射（代码到名称）
        code_to_station = {code: name for name, code in station_to_code.items()}

        # 先尝试提取区间（使用正则匹配区间格式）
        # 匹配格式：A到B、A至B、A-B、A和B、A与B等，以及站码格式
        section_patterns = [
            # 站码区间：XSD-BDD、XSD-BDD区间、XSD到BDD
            r'([A-Z]{3})[－\-至到]\s*([A-Z]{3})(?:区间|段)?',
            # 中文车站名：石家庄到保定东
            r'([^与和－\-]{2,})[－\-到至]\s*([^与和－\-]{2,})(?:站|线路所|区间|段)?',
            # 中文车站名（之间）：涿州东与高碑店东之间
            r'([^与和－\-]{2,})[与和]\s*([^与和－\-]{2,})(?:站|线路所)?(?:之间)?',
        ]

        for pattern in section_patterns:
            section_match = re.search(pattern, user_input)
            if section_match:
                station1 = section_match.group(1)
                station2 = section_match.group(2)

                # 查找两个站点的代码和名称
                code1, code2 = None, None
                name1, name2 = None, None

                # 优先匹配站码
                if station1 in code_to_station:
                    code1 = station1
                    name1 = code_to_station[station1]
                else:
                    # 匹配中文名称（精确匹配）
                    for station_name, code in station_to_code.items():
                        if station1.strip() == station_name or station_name in station1 and len(station1) >= len(station_name):
                            code1 = code
                            name1 = station_name
                            break

                if station2 in code_to_station:
                    code2 = station2
                    name2 = code_to_station[station2]
                else:
                    # 匹配中文名称（精确匹配）
                    for station_name, code in station_to_code.items():
                        if station2.strip() == station_name or station_name in station2 and len(station2) >= len(station_name):
                            code2 = code
                            name2 = station_name
                            break

                # 如果两个站点都找到且不同，则构建区间
                if code1 and code2 and code1 != code2:
                    location_type = "section"
                    location_code = f"{code1}-{code2}"
                    location_name = f"{name1}-{name2}"
                    affected_section = f"{code1}-{code2}"
                    logger.debug(f"[_fallback_extraction] 提取到区间: {name1}-{name2} ({code1}-{code2})")
                    break

        # 如果没有提取到区间，则提取单个车站
        if not location_code:
            # 尝试匹配车站
            for station_name, code in station_to_code.items():
                if station_name in user_input:
                    location_name = station_name
                    location_code = code
                    affected_section = f"{code}-{code}"
                    break

            # 如果没有找到车站，尝试匹配"在XX站"模式
            if not location_code:
                station_pattern = r'在(\w+?)(?:站|线路所)'
                station_match = re.search(station_pattern, user_input)
                if station_match:
                    matched_name = station_match.group(1)
                    # 尝试在映射中查找（精确匹配）
                    for station_name, code in station_to_code.items():
                        if matched_name.strip() == station_name or station_name in matched_name and len(matched_name) >= len(station_name):
                            location_name = station_name
                            location_code = code
                            affected_section = f"{code}-{code}"
                            break

        # 使用统一的完整性判定逻辑
        # 确保所有字符串字段都不是 None（使用空字符串作为默认值）
        accident_card_data = {
            "affected_train_ids": affected_train_ids or [],
            "location_code": location_code or "",
            "location_name": location_name or "",
            "scene_category": scene_category or "突发故障",
            "fault_type": fault_type or "未知",
            "expected_duration": expected_duration
        }
        is_complete, missing_fields = self._check_completeness(accident_card_data)

        # 确保字段类型正确（防止 None 值）
        return AccidentCard(
            fault_type=accident_card_data["fault_type"],
            scene_category=accident_card_data["scene_category"],
            affected_section=affected_section or "",
            location_type=location_type or "station",
            location_code=accident_card_data["location_code"],
            location_name=accident_card_data["location_name"],
            affected_train_ids=accident_card_data["affected_train_ids"],
            is_complete=is_complete,
            missing_fields=missing_fields,
            expected_duration=accident_card_data["expected_duration"],
            start_time=datetime.now()
        )

    def _extract_with_finetuned_model(self, user_input: str):
        """
        使用微调模型提取事故信息

        Args:
            user_input: 用户输入

        Returns:
            tuple: (AccidentCard, response) 提取的事故卡片和响应对象，如果失败返回(None, None)
        """
        try:
            logger.debug("[L1] 使用微调模型提取事故信息")

            output_text = None

            if L1Config.FINETUNED_MODEL_PROVIDER in ["ollama", "vllm"]:
                # 使用 OpenAI 兼容接口调用 Ollama/vLLM
                response = self.finetuned_model.chat.completions.create(
                    model=L1Config.FINETUNED_MODEL_NAME,
                    messages=[
                        {"role": "system", "content": L1_FINETUNED_SYSTEM_PROMPT},
                        {"role": "user", "content": user_input}
                    ],
                    temperature=L1Config.FINETUNED_MODEL_TEMPERATURE,
                    max_tokens=L1Config.FINETUNED_MODEL_MAX_TOKENS
                )
                output_text = response.choices[0].message.content
                # 创建简化的响应对象
                llm_response = type('obj', (object,), {
                    'raw_response': output_text,
                    'model_used': L1Config.FINETUNED_MODEL_NAME,
                    'is_valid': True,
                    'parsed_output': {}
                })()
            elif L1Config.FINETUNED_MODEL_PROVIDER == "transformers":
                # 使用 Transformers 原生加载
                messages = [
                    {"role": "system", "content": L1_FINETUNED_SYSTEM_PROMPT},
                    {"role": "user", "content": user_input}
                ]
                input_text = self.finetuned_tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True
                )
                inputs = self.finetuned_tokenizer(input_text, return_tensors="pt")
                if L1Config.FINETUNED_MODEL_DEVICE == "cuda":
                    inputs = {k: v.cuda() for k, v in inputs.items()}

                outputs = self.finetuned_model.generate(
                    **inputs,
                    max_new_tokens=L1Config.FINETUNED_MODEL_MAX_TOKENS,
                    temperature=L1Config.FINETUNED_MODEL_TEMPERATURE
                )
                output_text = self.finetuned_tokenizer.decode(
                    outputs[0][inputs['input_ids'].shape[1]:],
                    skip_special_tokens=True
                )
                # 创建简化的响应对象
                llm_response = type('obj', (object,), {
                    'raw_response': output_text,
                    'model_used': L1Config.FINETUNED_MODEL_NAME,
                    'is_valid': True,
                    'parsed_output': {}
                })()
            else:
                logger.error(f"[L1] 未知的微调模型提供商: {L1Config.FINETUNED_MODEL_PROVIDER}")
                return None, None

            # 解析输出
            accident_card = self._parse_finetuned_output(output_text, user_input)
            return accident_card, llm_response

        except Exception as e:
            logger.error(f"[L1] 微调模型提取异常: {str(e)}")
            return None, None

    def _parse_finetuned_output(self, output_text: str, user_input: str) -> Optional[AccidentCard]:
        """
        解析微调模型的输出

        Args:
            output_text: 模型输出的文本
            user_input: 原始用户输入

        Returns:
            AccidentCard: 解析后的事故卡片，如果失败返回None
        """
        try:
            # 清理输出文本，提取 JSON 部分
            output_text = output_text.strip()
            if output_text.startswith("```json"):
                output_text = output_text[7:]
            if output_text.startswith("```"):
                output_text = output_text[3:]
            if output_text.endswith("```"):
                output_text = output_text[:-3]
            output_text = output_text.strip()

            # 解析 JSON
            import json
            data = json.loads(output_text)

            # 构建 AccidentCard
            return self._build_accident_card(data, user_input)

        except json.JSONDecodeError as e:
            logger.error(f"[L1] 微调模型输出 JSON 解析失败: {e}, 输出: {output_text[:200]}")
            return None
        except Exception as e:
            logger.error(f"[L1] 解析微调模型输出异常: {str(e)}")
            return None

    def _extract_with_prompt(self, user_input: str, enable_rag: bool = True):
        """
        使用 Prompt 方式提取事故信息（原有实现）

        Args:
            user_input: 用户输入
            enable_rag: 是否启用RAG

        Returns:
            tuple: (AccidentCard, response) 提取的事故卡片和响应对象，如果失败返回(None, None)
        """
        try:
            logger.debug("[L1] 使用 Prompt 方式提取事故信息")

            # 构建Prompt上下文
            context = PromptContext(
                request_id="",
                user_input=user_input,
                source_type="natural_language"
            )

            # 调用LLM提取事故卡片
            response = self.prompt_adapter.execute_prompt(
                template_id="l1_data_modeling",
                context=context,
                enable_rag=enable_rag
            )

            # 处理响应
            if response.is_valid and response.parsed_output:
                acc_card_data = response.parsed_output.get("accident_card", {})
                return self._build_accident_card(acc_card_data, user_input), response
            else:
                logger.warning(f"[L1] Prompt 提取失败: {response.error}")
                return None, response

        except Exception as e:
            logger.error(f"[L1] Prompt 提取异常: {str(e)}")
            return None, None

    def _generate_missing_questions(self, missing_fields: list) -> list:
        """
        根据缺失字段生成询问用户的问题

        Args:
            missing_fields: 缺失字段列表，如 ["列车号", "位置", "事件类型"]

        Returns:
            list: 生成的询问问题列表
        """
        questions = []

        # 定义字段到问题的映射
        field_to_question = {
            "列车号": "请提供受影响列车号（例如：G1563）",
            "位置": "请提供事故发生位置（如：石家庄站）",
            "事件类型": "请说明事件类型（如：设备故障、临时限速等）",
            "时间": "请提供事故发生时间",
            "持续时间": "请提供预计持续时间（分钟）"
        }

        for field in missing_fields:
            if field in field_to_question:
                questions.append({
                    "field": field,
                    "question": field_to_question[field]
                })
            else:
                questions.append({
                    "field": field,
                    "question": f"请提供{field}"
                })

        return questions

    def _llm_extraction(self, user_input: str) -> Optional[AccidentCard]:
        """
        使用LLM提取事故信息（L0功能）

        Args:
            user_input: 用户输入

        Returns:
            AccidentCard: 提取的事故卡片，如果失败返回None
        """
        try:
            logger.debug("[L0] 使用LLM提取事故信息")

            # 构建Prompt上下文
            context = PromptContext(
                request_id="",
                user_input=user_input,
                source_type="natural_language"
            )

            # 调用LLM提取
            response = self.prompt_adapter.execute_prompt(
                template_id="l0_preprocess_extractor",
                context=context,
                enable_rag=False  # L0阶段不启用RAG，提高速度
            )

            if response.is_valid and response.parsed_output:
                llm_result = response.parsed_output
                logger.debug(f"[L0] LLM提取成功: {llm_result}")

                # 解析LLM结果
                scene_type_mapping = {
                    "TEMP_SPEED_LIMIT": "临时限速",
                    "SUDDEN_FAILURE": "突发故障",
                    "SECTION_INTERRUPT": "区间封锁"
                }
                fault_type_mapping = {
                    "WIND": "大风",
                    "RAIN": "暴雨",
                    "SNOW": "大雪",
                    "EQUIPMENT_FAILURE": "设备故障",
                    "SIGNAL_FAILURE": "信号故障",
                    "CATENARY_FAILURE": "接触网故障",
                    "DELAY": "预计晚点"
                }

                scene_category = scene_type_mapping.get(llm_result.get("scene_type", ""), "突发故障")
                fault_type = fault_type_mapping.get(llm_result.get("fault_type", ""), "未知")
                location_code = llm_result.get("station_code", "")
                delay_seconds = llm_result.get("delay_seconds", 0)

                # 提取列车号（从用户输入中提取，因为LLM可能不返回）
                train_matches = re.findall(r'([GCDZ]\d+)', user_input)
                affected_train_ids = train_matches if train_matches else []

                # 查找车站名称
                station_name = self._get_station_name(location_code)

                # 使用统一的完整性判定逻辑
                accident_card_data = {
                    "affected_train_ids": affected_train_ids,
                    "location_code": location_code,
                    "location_name": station_name,
                    "scene_category": scene_category,
                    "fault_type": fault_type
                }
                is_complete, missing_fields = self._check_completeness(accident_card_data)

                # 检索调度员操作指南
                dispatcher_guide = self._retrieve_dispatcher_guide(scene_category, fault_type)
                if dispatcher_guide:
                    logger.debug(f"[L1] 检索到调度员操作指南: {len(dispatcher_guide)} 条")

                return AccidentCard(
                    fault_type=fault_type,
                    scene_category=scene_category,
                    affected_section=f"{location_code}-{location_code}" if location_code else "",
                    location_code=location_code,
                    location_name=station_name,
                    affected_train_ids=affected_train_ids,
                    is_complete=is_complete,
                    missing_fields=missing_fields,
                    expected_duration=delay_seconds // 60 if delay_seconds else None,
                    start_time=datetime.now()
                )
            else:
                logger.warning(f"[L0] LLM提取失败: {response.error}")
                return None

        except Exception as e:
            logger.error(f"[L0] LLM提取异常: {str(e)}")
            return None

    def _get_station_name(self, station_code: str) -> str:
        """根据车站代码获取车站名称"""
        code_to_name = {
            "BJX": "北京西", "DJK": "杜家坎线路所", "ZBD": "涿州东", "GBD": "高碑店东",
            "XSD": "徐水东", "BDD": "保定东", "DZD": "定州东", "ZDJ": "正定机场",
            "SJP": "石家庄", "GYX": "高邑西", "XTD": "邢台东", "HDD": "邯郸东", "AYD": "安阳东"
        }
        return code_to_name.get(station_code, "")

    def build_canonical_request_from_input(
        self,
        user_input: str
    ) -> Any:
        """
        从用户输入构建 CanonicalDispatchRequest（L0功能）
        
        流程：
        1. 使用规则提取事故信息（AccidentCard）
        2. 从事故卡片构建 CanonicalDispatchRequest
        
        Args:
            user_input: 原始用户输入
            
        Returns:
            CanonicalDispatchRequest: 标准化调度请求
        """
        from models.preprocess_models import CanonicalDispatchRequest, LocationInfo, CompletenessInfo
        from models.common_enums import RequestSourceType, SceneTypeCode, FaultTypeCode
        from config import LLMConfig
        
        # 步骤1：优先使用LLM提取事故卡片（L0功能）
        if LLMConfig.FORCE_LLM_MODE:
            # 强制LLM模式：直接调用LLM
            accident_card = self._llm_extraction(user_input)
            if accident_card and accident_card.is_complete:
                logger.debug("[L0] LLM提取成功，使用LLM结果")
            elif accident_card:
                logger.warning("[L0] LLM提取结果不完整，尝试规则补充")
                # LLM结果不完整，用规则补充
                fallback_card = self._fallback_extraction(user_input)
                # 合并结果，优先使用LLM结果
                accident_card = self._merge_accident_cards(accident_card, fallback_card)
            else:
                logger.warning("[L0] LLM提取失败，使用规则回退")
                accident_card = self._fallback_extraction(user_input)
        else:
            # 非强制模式：优先尝试LLM，失败则回退到规则
            accident_card = self._llm_extraction(user_input)
            if not accident_card or not accident_card.is_complete:
                logger.debug("[L0] LLM提取失败或结果不完整，使用规则回退")
                fallback_card = self._fallback_extraction(user_input)
                if accident_card:
                    # 合并结果
                    accident_card = self._merge_accident_cards(accident_card, fallback_card)
                else:
                    accident_card = fallback_card
        
        # 映射场景类型
        scene_type_mapping = {
            "临时限速": SceneTypeCode.TEMP_SPEED_LIMIT,
            "突发故障": SceneTypeCode.SUDDEN_FAILURE,
            "区间封锁": SceneTypeCode.SECTION_INTERRUPT
        }
        
        # 映射故障类型
        fault_type_mapping = {
            "大风": FaultTypeCode.WIND,
            "暴雨": FaultTypeCode.RAIN,
            "大雪": FaultTypeCode.SNOW,
            "设备故障": FaultTypeCode.EQUIPMENT_FAILURE,
            "信号故障": FaultTypeCode.SIGNAL_FAILURE,
            "接触网故障": FaultTypeCode.CATENARY_FAILURE,
            "预计晚点": FaultTypeCode.DELAY
        }
        
        scene_type_code = scene_type_mapping.get(accident_card.scene_category, SceneTypeCode.SUDDEN_FAILURE)
        fault_type_code = fault_type_mapping.get(accident_card.fault_type, FaultTypeCode.EQUIPMENT_FAILURE)
        
        # 计算延误秒数
        reported_delay_seconds = None
        if accident_card.expected_duration:
            reported_delay_seconds = int(accident_card.expected_duration * 60)
        
        # 根据location_type构建LocationInfo
        location_info = LocationInfo(
            station_code=None,
            station_name=None,
            section_id=None
        )

        if accident_card.location_type == "section":
            # 区间场景
            location_info.section_id = accident_card.location_code
            location_info.station_name = accident_card.location_name
            logger.debug(f"[L0+L1] 构建区间位置: section_id={accident_card.location_code}, name={accident_card.location_name}")
        else:
            # 车站场景（默认）
            location_info.station_code = accident_card.location_code
            location_info.station_name = accident_card.location_name
            logger.debug(f"[L0+L1] 构建车站位置: station_code={accident_card.location_code}, name={accident_card.location_name}")

        canonical_request = CanonicalDispatchRequest(
            source_type=RequestSourceType.NATURAL_LANGUAGE,
            raw_text=user_input,
            scene_type_code=scene_type_code,
            scene_type_label=accident_card.scene_category,
            fault_type=fault_type_code,
            location=location_info,
            affected_train_ids=accident_card.affected_train_ids or [],
            event_time=datetime.now().isoformat(),
            reported_delay_seconds=reported_delay_seconds,
            completeness=CompletenessInfo(
                can_enter_solver=accident_card.is_complete,
                missing_fields=accident_card.missing_fields or []
            )
        )
        
        logger.debug(f"[L0+L1] 构建 CanonicalDispatchRequest: scene={accident_card.scene_category}, "
                   f"trains={accident_card.affected_train_ids}, complete={accident_card.is_complete}")
        
        return canonical_request
