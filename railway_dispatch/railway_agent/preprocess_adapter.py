# -*- coding: utf-8 -*-
"""
L0 数据预处理层模块
将不同输入源统一转换为固定 schema 的 CanonicalDispatchRequest
"""

from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from datetime import datetime
import logging
import json

logger = logging.getLogger(__name__)


# ============== 标准化的调度请求 Schema ==============

class CanonicalDispatchRequest(BaseModel):
    """
    标准的调度请求格式
    统一所有输入源的接口
    """
    # 请求来源
    source: str = Field(description="请求来源: natural_language / form_submit / script_api")
    
    # 场景信息
    scene_category: str = Field(description="场景类型: 临时限速 / 突发故障 / 区间封锁")
    fault_type: str = Field(description="故障类型: 暴雨 / 设备故障 / 信号故障 / 接触网故障")
    
    # 位置信息
    location_code: str = Field(description="位置代码，如 XSD")
    location_name: Optional[str] = Field(default=None, description="位置名称")
    affected_section: str = Field(description="受影响区段，如 XSD-BDD")
    
    # 时间信息
    start_time: Optional[datetime] = Field(default=None, description="开始时间")
    expected_duration: Optional[int] = Field(default=None, description="预计持续时间（分钟）")
    
    # 列车信息
    affected_trains: List[str] = Field(default_factory=list, description="受影响列车列表")
    initial_delays: Dict[str, int] = Field(default_factory=dict, description="初始延误（秒），如 {train_id: seconds}")
    
    # 原始输入（保留用于调试）
    raw_input: str = Field(description="原始输入文本")
    
    # 额外参数
    extra_params: Dict[str, Any] = Field(default_factory=dict, description="额外参数")


class PreprocessAdapter:
    """
    预处理适配器
    将不同格式的输入转换为 CanonicalDispatchRequest
    """
    
    # 场景类型映射
    SCENE_CATEGORY_MAP = {
        "限速": "临时限速",
        "speed_limit": "临时限速",
        "临时限速": "临时限速",
        "故障": "突发故障",
        "fault": "突发故障",
        "突发故障": "突发故障",
        "封锁": "区间封锁",
        "block": "区间封锁",
        "区间封锁": "区间封锁"
    }
    
    # 故障类型映射
    FAULT_TYPE_MAP = {
        "暴雨": "暴雨",
        "rain": "暴雨",
        "大雨": "暴雨",
        "设备": "设备故障",
        "equipment": "设备故障",
        "设备故障": "设备故障",
        "信号": "信号故障",
        "signal": "信号故障",
        "信号故障": "信号故障",
        "接触网": "接触网故障",
        "catenary": "接触网故障",
        "接触网故障": "接触网故障"
    }
    
    def __init__(self):
        pass
    
    def process_natural_language(self, user_input: str) -> CanonicalDispatchRequest:
        """
        处理自然语言输入（来自 /api/agent_chat）
        这里的解析由 LLM 完成，这里只做基础清洗
        
        Args:
            user_input: 用户自然语言描述
            
        Returns:
            CanonicalDispatchRequest: 标准化的调度请求
        """
        logger.info(f"L0预处理自然语言输入: {user_input[:50]}...")
        
        # 基础清洗：去除多余空白
        cleaned_input = " ".join(user_input.split())
        
        # 返回基础请求，详细的场景解析由 L1 层完成
        return CanonicalDispatchRequest(
            source="natural_language",
            scene_category="",  # 由 L1 层确定
            fault_type="",      # 由 L1 层确定
            location_code="",  # 由 L1 层确定
            affected_section="", # 由 L1 层确定
            raw_input=cleaned_input
        )
    
    def process_form_submit(self, form_data: Dict[str, Any]) -> CanonicalDispatchRequest:
        """
        处理表单提交（来自 /api/dispatch）
        
        Args:
            form_data: 表单数据
            
        Returns:
            CanonicalDispatchRequest: 标准化的调度请求
        """
        logger.info(f"L0预处理表单提交: {form_data}")
        
        # 直接映射字段
        scene = form_data.get("scene_type", "")
        fault = form_data.get("fault_type", "")
        location = form_data.get("location_code", "")
        section = form_data.get("affected_section", "")
        
        # 解析时间
        start_time_str = form_data.get("start_time")
        start_time = None
        if start_time_str:
            try:
                start_time = datetime.fromisoformat(start_time_str)
            except:
                pass
        
        return CanonicalDispatchRequest(
            source="form_submit",
            scene_category=self.SCENE_CATEGORY_MAP.get(scene, scene),
            fault_type=self.FAULT_TYPE_MAP.get(fault, fault),
            location_code=location,
            location_name=form_data.get("location_name", ""),
            affected_section=section,
            start_time=start_time,
            expected_duration=form_data.get("expected_duration"),
            affected_trains=form_data.get("affected_trains", []),
            initial_delays=form_data.get("initial_delays", {}),
            raw_input=json.dumps(form_data, ensure_ascii=False)
        )
    
    def process_script_api(self, api_data: Dict[str, Any]) -> CanonicalDispatchRequest:
        """
        处理脚本 API 调用（未来扩展）
        
        Args:
            api_data: API 数据
            
        Returns:
            CanonicalDispatchRequest: 标准化的调度请求
        """
        logger.info(f"L0预处理脚本API: {api_data}")
        
        return CanonicalDispatchRequest(
            source="script_api",
            scene_category=api_data.get("scene_category", ""),
            fault_type=api_data.get("fault_type", ""),
            location_code=api_data.get("location_code", ""),
            location_name=api_data.get("location_name", ""),
            affected_section=api_data.get("affected_section", ""),
            start_time=api_data.get("start_time"),
            expected_duration=api_data.get("expected_duration"),
            affected_trains=api_data.get("affected_trains", []),
            initial_delays=api_data.get("initial_delays", {}),
            raw_input=json.dumps(api_data, ensure_ascii=False),
            extra_params=api_data.get("extra_params", {})
        )
    
    def normalize(self, raw_input: Any) -> CanonicalDispatchRequest:
        """
        统一入口：根据输入类型自动选择处理方式
        
        Args:
            raw_input: 任意格式的输入
            
        Returns:
            CanonicalDispatchRequest: 标准化的调度请求
        """
        if isinstance(raw_input, str):
            # 字符串输入：假设是自然语言
            return self.process_natural_language(raw_input)
        elif isinstance(raw_input, dict):
            # 字典输入：检查 source 字段
            source = raw_input.get("source", "")
            if source == "form_submit":
                return self.process_form_submit(raw_input)
            elif source == "script_api":
                return self.process_script_api(raw_input)
            else:
                # 默认为表单提交
                return self.process_form_submit(raw_input)
        else:
            raise ValueError(f"不支持的输入类型: {type(raw_input)}")


# ============== 全局实例 ==============

_preprocess_adapter: Optional[PreprocessAdapter] = None


def get_preprocess_adapter() -> PreprocessAdapter:
    """获取全局预处理适配器实例"""
    global _preprocess_adapter
    if _preprocess_adapter is None:
        _preprocess_adapter = PreprocessAdapter()
    return _preprocess_adapter


# ============== 测试代码 ==============

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    adapter = get_preprocess_adapter()
    
    # 测试自然语言输入
    nl_request = adapter.process_natural_language("G1215列车在徐水东站因暴雨限速60km/h")
    print(f"自然语言请求: {nl_request.model_dump()}")
    
    # 测试表单输入
    form_request = adapter.process_form_submit({
        "scene_type": "限速",
        "fault_type": "暴雨",
        "location_code": "XSD",
        "affected_section": "XSD-BDD"
    })
    print(f"表单请求: {form_request.model_dump()}")
    
    # 测试统一入口
    normalized = adapter.normalize("测试输入")
    print(f"统一入口: {normalized.model_dump()}")