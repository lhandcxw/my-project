# -*- coding: utf-8 -*-
"""
第一层：数据建模层
从调度员描述中生成事故卡片

职责说明（修正后）：
- LLM提取事故信息（scene_category, fault_type, location_code等）
- 回退推断逻辑（当LLM失败时）
- 构建AccidentCard
- **不**构建 NetworkSnapshot（由 SnapshotBuilder 负责）

v4.1 修正：
- 移除 _build_network_snapshot 方法
- L1 只负责数据建模（AccidentCard）
- NetworkSnapshot 由 SnapshotBuilder 单一入口构建
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime
import re

from models.workflow_models import AccidentCard
from models.common_enums import fault_code_to_label
from models.prompts import PromptContext
from railway_agent.adapters.llm_prompt_adapter import get_llm_prompt_adapter

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
        enable_rag: bool = True
    ) -> Dict[str, Any]:
        """
        执行第一层数据建模

        Args:
            user_input: 用户输入
            canonical_request: L0预处理结果（可选）
            enable_rag: 是否启用RAG

        Returns:
            Dict: 包含 accident_card 的字典
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
        else:
            # LLM失败，使用回退逻辑
            logger.warning("LLM提取失败，使用回退逻辑")
            accident_card = self._fallback_extraction(user_input)

        logger.info(f"第一层完成: scene_category={accident_card.scene_category}, is_complete={accident_card.is_complete}")

        return {
            "accident_card": accident_card,
            "llm_response": response.raw_response,
            "llm_response_type": response.model_used
        }

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
            if "风" in user_input:
                acc_card_data["fault_type"] = "大风"
            elif "雨" in user_input:
                acc_card_data["fault_type"] = "暴雨"
            elif "设备" in user_input or "故障" in user_input:
                acc_card_data["fault_type"] = "设备故障"

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
        """回退提取：基于规则的提取"""
        user_input_lower = user_input.lower()

        # 默认值
        scene_category = "突发故障"
        fault_type = "未知"
        location_code = ""
        location_name = ""
        affected_section = ""
        affected_train_ids = []

        # 推断场景类型
        if "限速" in user_input:
            scene_category = "临时限速"
        elif "封锁" in user_input:
            scene_category = "区间封锁"

        # 推断故障类型
        if "风" in user_input:
            fault_type = "大风"
        elif "雨" in user_input:
            fault_type = "暴雨"
        elif "设备" in user_input or "故障" in user_input:
            fault_type = "设备故障"

        # 提取列车号
        train_match = re.search(r'([GCDZ]\d+)', user_input)
        if train_match:
            affected_train_ids = [train_match.group(1)]

        # 提取车站
        station_to_code = {
            "石家庄": "SJP", "北京西": "BJX", "保定东": "BDD", "定州东": "DZD",
            "徐水东": "XSD", "涿州东": "ZBD", "高碑店东": "GBD", "正定机场": "ZDJ",
            "高邑西": "GYX", "邢台东": "XTD", "邯郸东": "HDD", "安阳东": "AYD",
            "杜家坎": "DJK"
        }
        for station_name, code in station_to_code.items():
            if station_name in user_input:
                location_name = station_name
                location_code = code
                affected_section = f"{code}-{code}"
                break

        # 判断完整性
        is_complete = bool(affected_train_ids and location_code and fault_type != "未知")
        missing_fields = []
        if not affected_train_ids:
            missing_fields.append("列车号")
        if not location_code:
            missing_fields.append("位置")
        if fault_type == "未知":
            missing_fields.append("事件类型")

        return AccidentCard(
            fault_type=fault_type,
            scene_category=scene_category,
            affected_section=affected_section,
            location_code=location_code,
            location_name=location_name,
            affected_train_ids=affected_train_ids,
            is_complete=is_complete,
            missing_fields=missing_fields,
            start_time=datetime.now()
        )
