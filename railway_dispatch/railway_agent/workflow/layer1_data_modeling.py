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
    基于关键词匹配，从知识库中检索调度员操作指南
    """

    def __init__(self):
        self.knowledge_base = self._load_operation_knowledge()

    def _load_operation_knowledge(self) -> Dict[str, Any]:
        """加载调度员操作知识库"""
        knowledge = {
            "大风天气": {
                "keywords": ["大风", "风速", "侧风", "台风", "强风"],
                "operations": [
                    "立即确认大风报警地点（区段、里程）",
                    "确认风速监测子系统显示的风速值",
                    "根据风速设置列控限速：15-20m/s限速200km/h，20-25m/s限速120km/h，>25m/s禁止运行",
                    "立即呼叫已进入区间的列车司机，通知限速要求",
                    "若显示禁止运行报警，立即命令列车停车",
                    "台风登录前72小时发布预警，48小时启动应急预案",
                    "持续监控风速变化，每5分钟确认一次"
                ],
                "source": "高铁非正常情况调度员操作知识库"
            },
            "雨天天气": {
                "keywords": ["大雨", "暴雨", "降雨", "洪水", "积水"],
                "operations": [
                    "立即确认降雨报警地点和等级",
                    "确认降雨量监测数据（小时雨量/连续雨量）",
                    "通知工务部门开展区间巡视",
                    "根据警戒等级执行限速或扣停：限速警戒限速120km/h或160km/h，封锁警戒禁止进入",
                    "暴雨红色预警时立即封锁相关区间线路",
                    "暴雨持续期间每10分钟确认一次雨量数据",
                    "解除警戒后逐步恢复常速运行"
                ],
                "source": "高铁非正常情况调度员操作知识库"
            },
            "冰雪天气": {
                "keywords": ["冰雪", "结冰", "冻雨", "降雪", "道岔冻结", "覆冰"],
                "operations": [
                    "立即确认冰雪天气报警地点和类型",
                    "通知车务部门启动道岔融雪装置",
                    "通知工务部门组织扫雪除冰",
                    "确认接触网覆冰情况，必要时组织热滑除冰",
                    "根据冰雪情况设置列控限速：小雪限速200km/h，中雪限速120km/h，大雪限速80km/h或停车",
                    "冰雪天气持续期间每15分钟确认一次设备状态",
                    "降雪结束后组织添乘检查线路状况"
                ],
                "source": "高铁非正常情况调度员操作知识库"
            },
            "设备故障": {
                "keywords": ["设备故障", "信号故障", "接触网故障", "线路故障", "道岔故障"],
                "operations": [
                    "立即扣停后续列车",
                    "确认故障类型和影响范围",
                    "通知相关设备管理部门（电务、供电、工务）",
                    "评估故障恢复时间",
                    "安排故障列车处理（救援或拖行）",
                    "调整后续列车时刻表",
                    "做好旅客转运安排"
                ],
                "source": "铁路调度操作规则知识库"
            },
            "临时限速": {
                "keywords": ["临时限速", "限速运行", "限速命令"],
                "operations": [
                    "确认限速区段和限速值",
                    "计算受影响列车数量和延误时间",
                    "设置列控限速（明确起止里程、限速值、原因）",
                    "调整列车发车时间（顺延）",
                    "压缩停站时间（在安全范围内）",
                    "发布限速调度命令",
                    "持续监控，及时调整"
                ],
                "source": "铁路调度操作规则知识库"
            },
            "区间封锁": {
                "keywords": ["区间封锁", "线路封锁", "封锁区间"],
                "operations": [
                    "确认封锁区段和原因",
                    "评估封锁持续时间",
                    "停止新列车发车（进入封锁区段）",
                    "区间内列车就近停靠",
                    "安排绕行（如可行）",
                    "启动应急预案",
                    "确认设备正常后方可解除封锁"
                ],
                "source": "铁路调度操作规则知识库"
            }
        }
        return knowledge

    def retrieve_operations(self, scene_category: str, fault_type: str, user_input: str) -> Optional[Dict[str, Any]]:
        """
        检索调度员操作指南
        优先按故障类型(fault_type)检索，其次按场景类型

        Args:
            scene_category: 场景类型（临时限速/突发故障/区间中断）
            fault_type: 故障类型（大风/暴雨/设备故障等）
            user_input: 用户原始输入

        Returns:
            操作指南字典，包含operations列表
        """
        # 优先使用fault_type检索
        query = f"{fault_type} {user_input}".lower()

        best_match = None
        best_score = 0

        for scene_name, knowledge in self.knowledge_base.items():
            score = 0
            # 关键词匹配
            for keyword in knowledge["keywords"]:
                if keyword in query:
                    score += 3  # fault_type匹配得3分

            # 故障类型名称匹配
            if fault_type and fault_type.lower() in scene_name.lower():
                score += 5  # 精确匹配得5分

            if score > best_score:
                best_score = score
                best_match = {
                    "scene_name": scene_name,
                    "operations": knowledge["operations"],
                    "source": knowledge["source"],
                    "match_score": score
                }

        # 如果没有匹配到，尝试使用scene_category
        if best_score == 0 and scene_category:
            query = f"{scene_category} {user_input}".lower()
            for scene_name, knowledge in self.knowledge_base.items():
                score = 0
                for keyword in knowledge["keywords"]:
                    if keyword in query:
                        score += 2
                if scene_category.lower() in scene_name.lower():
                    score += 3

                if score > best_score:
                    best_score = score
                    best_match = {
                        "scene_name": scene_name,
                        "operations": knowledge["operations"],
                        "source": knowledge["source"],
                        "match_score": score
                    }

        return best_match if best_score > 0 else None

    def format_operations_for_display(self, operations_data: Dict[str, Any]) -> str:
        """格式化操作指南为显示文本"""
        if not operations_data:
            return ""

        lines = [
            f"\n【调度员操作指南 - {operations_data['scene_name']}】",
            f"来源：{operations_data['source']}\n"
        ]

        for i, op in enumerate(operations_data["operations"], 1):
            lines.append(f"{i}. {op}")

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

        # 根据配置选择提取方式：微调模型 或 Prompt
        if L1Config.USE_FINETUNED_MODEL and self.finetuned_model is not None:
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

    def _fallback_extraction(self, user_input: str) -> AccidentCard:
        """回退提取：基于规则的提取（增强版，与RuleAgent一致）"""
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
        if any(kw in user_input for kw in ["封锁", "区间中断", "线路中断", "完全中断", "无法通行"]):
            scene_category = "区间中断"
        elif any(kw in user_input for kw in ["限速", "大风", "暴雨", "降雪", "冰雪", "雨量", "风速",
                                           "天气", "自然灾害", "泥石流", "塌方", "水害", "台风", "风", "雨", "雪"]):
            scene_category = "临时限速"
        else:
            scene_category = "突发故障"

        # 推断故障类型（增强：更多关键词）
        if "风" in user_input or "大风" in user_input:
            fault_type = "大风"
        elif "雨" in user_input or "暴雨" in user_input:
            fault_type = "暴雨"
        elif "雪" in user_input or "降雪" in user_input:
            fault_type = "大雪"
        elif "设备" in user_input:
            fault_type = "设备故障"
        elif "信号" in user_input:
            fault_type = "信号故障"
        elif "接触网" in user_input or "停电" in user_input:
            fault_type = "接触网故障"
        elif "故障" in user_input:
            fault_type = "设备故障"

        # 提取列车号（支持多个列车）
        train_matches = re.findall(r'([GCDZ]\d+)', user_input)
        if train_matches:
            affected_train_ids = train_matches

        # 提取延误时间（分钟）
        delay_match = re.search(r'(\d+)\s*分钟', user_input)
        if delay_match:
            expected_duration = int(delay_match.group(1))

        # 提取位置信息（车站或区间，增强版）
        station_to_code = {
            "石家庄": "SJP", "北京西": "BJX", "保定东": "BDD", "定州东": "DZD",
            "徐水东": "XSD", "涿州东": "ZBD", "高碑店东": "GBD", "正定机场": "ZDJ",
            "高邑西": "GYX", "邢台东": "XTD", "邯郸东": "HDD", "安阳东": "AYD",
            "杜家坎": "DJK", "杜家坎线路所": "DJK"
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
        accident_card_data = {
            "affected_train_ids": affected_train_ids,
            "location_code": location_code,
            "location_name": location_name,
            "scene_category": scene_category,
            "fault_type": fault_type,
            "expected_duration": expected_duration
        }
        is_complete, missing_fields = self._check_completeness(accident_card_data)

        return AccidentCard(
            fault_type=fault_type,
            scene_category=scene_category,
            affected_section=affected_section,
            location_type=location_type,
            location_code=location_code,
            location_name=location_name,
            affected_train_ids=affected_train_ids,
            is_complete=is_complete,
            missing_fields=missing_fields,
            expected_duration=expected_duration,
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
