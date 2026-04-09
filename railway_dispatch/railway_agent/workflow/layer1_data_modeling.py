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
from config import LLMConfig

logger = logging.getLogger(__name__)


class Layer1DataModeling:
    """
    第一层：数据建模层
    使用LLM提取事故信息，构建 AccidentCard
    不构建 NetworkSnapshot（由 SnapshotBuilder 负责）
    """

    def __init__(self):
        """初始化第一层"""
        self.prompt_adapter = get_llm_prompt_adapter()

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
        logger.info("[L1] 数据建模层")

        # 如果有L0预处理结果，直接使用
        if canonical_request and canonical_request.scene_type_code:
            return self._use_preprocessed_result(canonical_request)

        # 构建Prompt上下文
        context = PromptContext(
            request_id="",  # 由外部提供
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
            accident_card = self._build_accident_card(acc_card_data, user_input)
            # 检查响应来源并记录日志
            response_source = response.parsed_output.get("_response_source", "")
            response_note = response.parsed_output.get("_response_note", "")
            if response_source.startswith("rule_based"):
                logger.info(f"[L1] 使用模拟响应: {response_note}")
            elif response_source.startswith("llm_"):
                logger.info(f"[L1] 使用LLM真实响应: {response_note}")
            else:
                logger.info(f"[L1] 响应来源: {response_source}, {response_note}")
        else:
            # LLM失败，根据配置决定是否使用回退逻辑
            from config import LLMConfig
            if LLMConfig.FORCE_LLM_MODE:
                # 强制LLM模式：直接报错，不使用规则回退
                error_msg = f"[L1] LLM提取失败，FORCE_LLM_MODE=true，中止处理。错误: {response.error}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            else:
                # 调试模式：使用规则回退
                logger.warning("[L1] LLM提取失败，使用回退逻辑（规则提取）- 调试模式")
                accident_card = self._fallback_extraction(user_input)

        # 如果有之前的事故卡片，合并信息
        if previous_accident_card:
            accident_card = self._merge_accident_cards(previous_accident_card, accident_card)
            logger.info(f"[L1] 合并之前的事故卡片信息")

        # 检查信息完整性
        if not accident_card.is_complete:
            missing_questions = self._generate_missing_questions(accident_card.missing_fields)
            logger.info(f"[L1] 信息不完整，需要补充: {accident_card.missing_fields}")
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

        logger.info(f"[L1] 第一层完成: scene_category={accident_card.scene_category}, is_complete={accident_card.is_complete}")

        return result

    def _use_preprocessed_result(
        self,
        canonical_request: Any
    ) -> Dict[str, Any]:
        """使用L0预处理结果"""
        scene_label = canonical_request.scene_type_label or "临时限速"

        accident_card = AccidentCard(
            fault_type=fault_code_to_label(canonical_request.fault_type) if canonical_request.fault_type else "未知",
            scene_category=scene_label,
            start_time=datetime.fromisoformat(canonical_request.event_time) if canonical_request.event_time else None,
            expected_duration=(canonical_request.reported_delay_seconds or 0) / 60 if canonical_request.reported_delay_seconds else None,
            affected_section=f"{canonical_request.location.station_code}-{canonical_request.location.station_code}" if canonical_request.location and canonical_request.location.station_code else "",
            location_code=canonical_request.location.station_code if canonical_request.location else "",
            location_name=canonical_request.location.station_name if canonical_request.location else "",
            affected_train_ids=canonical_request.affected_train_ids or [],
            is_complete=canonical_request.completeness.can_enter_solver if canonical_request.completeness else False,
            missing_fields=canonical_request.completeness.missing_fields if canonical_request.completeness else []
        )

        logger.info(f"第一层完成(L0): scene_category={accident_card.scene_category}")

        return {
            "accident_card": accident_card,
            "llm_response": "使用L0预处理结果"
        }

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

        # 推断场景类别
        if not acc_card_data.get("scene_category"):
            if "限速" in user_input:
                acc_card_data["scene_category"] = "临时限速"
            elif "封锁" in user_input:
                acc_card_data["scene_category"] = "区间封锁"
            else:
                acc_card_data["scene_category"] = "突发故障"

        # 提取车站信息
        if not acc_card_data.get("location_code") and not acc_card_data.get("location_name"):
            station_to_code = {
                "石家庄": "SJP", "北京西": "BJX", "保定东": "BDD", "定州东": "DZD",
                "徐水东": "XSD", "涿州东": "ZBD", "高碑店东": "GBD", "正定机场": "ZDJ",
                "高邑西": "GYX", "邢台东": "XTD", "邯郸东": "HDD", "安阳东": "AYD",
                "杜家坎": "DJK"
            }
            for station_name, code in station_to_code.items():
                if station_name in user_input:
                    acc_card_data["location_name"] = station_name
                    acc_card_data["location_code"] = code
                    acc_card_data["affected_section"] = f"{code}-{code}"
                    break

        # 判断信息完整性
        has_train = bool(acc_card_data.get("affected_train_ids"))
        has_location = bool(acc_card_data.get("location_code") or acc_card_data.get("affected_section"))
        has_event = bool(acc_card_data.get("fault_type") and acc_card_data.get("fault_type") != "未知")

        if not acc_card_data.get("is_complete"):
            acc_card_data["is_complete"] = has_train and has_location and has_event

        if not acc_card_data.get("missing_fields") and not acc_card_data["is_complete"]:
            missing = []
            if not has_train:
                missing.append("列车号")
            if not has_location:
                missing.append("位置")
            if not has_event:
                missing.append("事件类型")
            acc_card_data["missing_fields"] = missing

        return AccidentCard(
            fault_type=acc_card_data.get("fault_type", "未知"),
            scene_category=acc_card_data.get("scene_category", "临时限速"),
            affected_section=acc_card_data.get("affected_section", ""),
            location_code=acc_card_data.get("location_code", ""),
            location_name=acc_card_data.get("location_name", ""),
            affected_train_ids=acc_card_data.get("affected_train_ids", []),
            is_complete=acc_card_data.get("is_complete", False),
            missing_fields=acc_card_data.get("missing_fields", []),
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

        # 推断场景类型（增强：更多关键词匹配）
        # 优先级：区间封锁 > 临时限速 > 突发故障
        if any(kw in user_input for kw in ["封锁", "区间中断", "线路中断", "完全中断", "无法通行"]):
            scene_category = "区间封锁"
        elif any(kw in user_input for kw in ["限速", "大风", "暴雨", "降雪", "冰雪", "雨量", "风速",
                                           "天气", "自然灾害", "泥石流", "塌方", "水害", "台风"]):
            scene_category = "临时限速"
        elif any(kw in user_input for kw in ["故障", "中断", "设备故障", "降弓", "线路故障",
                                              "设备", "停电", "信号故障", "道岔故障", "车辆故障"]):
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

        # 提取车站（增强：支持更多车站和更灵活的匹配）
        station_to_code = {
            "石家庄": "SJP", "北京西": "BJX", "保定东": "BDD", "定州东": "DZD",
            "徐水东": "XSD", "涿州东": "ZBD", "高碑店东": "GBD", "正定机场": "ZDJ",
            "高邑西": "GYX", "邢台东": "XTD", "邯郸东": "HDD", "安阳东": "AYD",
            "杜家坎": "DJK", "杜家坎线路所": "DJK"
        }

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
                # 尝试在映射中查找
                for station_name, code in station_to_code.items():
                    if matched_name in station_name or station_name in matched_name:
                        location_name = station_name
                        location_code = code
                        affected_section = f"{code}-{code}"
                        break

        # 判断完整性（放宽条件：只要有列车号和位置即可）
        has_train = bool(affected_train_ids)
        has_location = bool(location_code)
        # 故障类型可以推断为"未知"，不影响完整性
        is_complete = has_train and has_location

        missing_fields = []
        if not has_train:
            missing_fields.append("列车号")
        if not has_location:
            missing_fields.append("位置")

        return AccidentCard(
            fault_type=fault_type,
            scene_category=scene_category,
            affected_section=affected_section,
            location_code=location_code,
            location_name=location_name,
            affected_train_ids=affected_train_ids,
            is_complete=is_complete,
            missing_fields=missing_fields,
            expected_duration=expected_duration,
            start_time=datetime.now()
        )

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
        
        # 步骤1：使用规则提取事故卡片（L0功能）
        accident_card = self._fallback_extraction(user_input)
        
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
        
        canonical_request = CanonicalDispatchRequest(
            source_type=RequestSourceType.NATURAL_LANGUAGE,
            raw_text=user_input,
            scene_type_code=scene_type_code,
            scene_type_label=accident_card.scene_category,
            fault_type=fault_type_code,
            location=LocationInfo(
                station_code=accident_card.location_code,
                station_name=accident_card.location_name
            ),
            affected_train_ids=accident_card.affected_train_ids or [],
            event_time=datetime.now().isoformat(),
            reported_delay_seconds=reported_delay_seconds,
            completeness=CompletenessInfo(
                can_enter_solver=accident_card.is_complete,
                missing_fields=accident_card.missing_fields or []
            )
        )
        
        logger.info(f"[L0+L1] 构建 CanonicalDispatchRequest: scene={accident_card.scene_category}, "
                   f"trains={accident_card.affected_train_ids}, complete={accident_card.is_complete}")
        
        return canonical_request
