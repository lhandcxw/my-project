# -*- coding: utf-8 -*-
"""
规则提取器
优先使用正则/规则提取 train_id/station/delay/time/speed_limit
"""

import re
from typing import Optional, Dict, Any, List
from models.common_enums import FaultTypeCode
import logging

logger = logging.getLogger(__name__)


class RuleExtractor:
    """
    规则提取器
    使用正则表达式和规则从文本中提取关键信息
    """
    
    # 列车车次正则
    TRAIN_PATTERN = re.compile(r'([GDCTZK]\d{1,4})(?=[^0-9]|$)')
    
    # 车站名称正则
    STATION_PATTERN = re.compile(r'(?:在|于|至|到|经过)?([^\s,，。,\.]+(?:站|线路所))')
    
    # 延误时间正则（分钟）
    DELAY_MINUTE_PATTERN = re.compile(r'延误(\d+)(?:分钟|分)')
    
    # 延误时间正则（秒）
    DELAY_SECOND_PATTERN = re.compile(r'延误(\d+)(?:秒)')
    
    # 限速正则
    SPEED_LIMIT_PATTERN = re.compile(r'限速(\d+)(?:km/?h|千米/时|公里/时)')
    
    # 时间正则
    TIME_PATTERN = re.compile(r'(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?\s*\d{1,2}[:：]\d{1,2}(?::\d{1,2})?)')
    
    # 故障类型关键词
    FAULT_KEYWORDS = {
        "暴雨": FaultTypeCode.RAIN,
        "大雨": FaultTypeCode.RAIN,
        "雷雨": FaultTypeCode.RAIN,
        "设备故障": FaultTypeCode.EQUIPMENT_FAILURE,
        "设备": FaultTypeCode.EQUIPMENT_FAILURE,
        "信号故障": FaultTypeCode.SIGNAL_FAILURE,
        "信号": FaultTypeCode.SIGNAL_FAILURE,
        "接触网故障": FaultTypeCode.CATENARY_FAILURE,
        "接触网": FaultTypeCode.CATENARY_FAILURE
    }
    
    # 场景类型关键词
    SCENE_KEYWORDS = {
        "限速": "TEMP_SPEED_LIMIT",
        "speed_limit": "TEMP_SPEED_LIMIT",
        "故障": "SUDDEN_FAILURE",
        "fault": "SUDDEN_FAILURE",
        "封锁": "SECTION_INTERRUPT",
        "block": "SECTION_INTERRUPT"
    }
    
    def extract(self, text: str) -> Dict[str, Any]:
        """
        从文本中提取关键信息
        
        Args:
            text: 输入文本
            
        Returns:
            Dict: 提取的信息
        """
        result = {
            "train_ids": [],
            "station_name": None,
            "delay_seconds": None,
            "speed_limit_kph": None,
            "event_time": None,
            "fault_type": None,
            "scene_type": None,
            "confidence": 0.0
        }
        
        if not text:
            return result
        
        # 提取列车车次
        train_matches = self.TRAIN_PATTERN.findall(text)
        if train_matches:
            result["train_ids"] = list(set(train_matches))
            result["confidence"] += 0.2
        
        # 提取车站名称
        station_matches = self.STATION_PATTERN.findall(text)
        if station_matches:
            result["station_name"] = station_matches[0]
            result["confidence"] += 0.2
        
        # 提取延误时间
        delay_match = self.DELAY_MINUTE_PATTERN.search(text)
        if delay_match:
            result["delay_seconds"] = int(delay_match.group(1)) * 60
            result["confidence"] += 0.2
        else:
            delay_match = self.DELAY_SECOND_PATTERN.search(text)
            if delay_match:
                result["delay_seconds"] = int(delay_match.group(1))
                result["confidence"] += 0.2
        
        # 提取限速
        speed_match = self.SPEED_LIMIT_PATTERN.search(text)
        if speed_match:
            result["speed_limit_kph"] = int(speed_match.group(1))
            result["confidence"] += 0.2
        
        # 提取时间
        time_match = self.TIME_PATTERN.search(text)
        if time_match:
            result["event_time"] = time_match.group(1)
            result["confidence"] += 0.1
        
        # 提取故障类型
        for keyword, fault_type in self.FAULT_KEYWORDS.items():
            if keyword in text:
                result["fault_type"] = fault_type
                result["confidence"] += 0.1
                break
        
        # 提取场景类型
        for keyword, scene_type in self.SCENE_KEYWORDS.items():
            if keyword in text:
                result["scene_type"] = scene_type
                break
        
        logger.info(f"RuleExtractor 结果: confidence={result['confidence']:.2f}, fields={list(result.keys())}")
        
        return result


# 全局实例
_rule_extractor: Optional[RuleExtractor] = None


def get_rule_extractor() -> RuleExtractor:
    """获取规则提取器实例"""
    global _rule_extractor
    if _rule_extractor is None:
        _rule_extractor = RuleExtractor()
    return _rule_extractor