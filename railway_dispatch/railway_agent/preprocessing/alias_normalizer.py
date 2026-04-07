# -*- coding: utf-8 -*-
"""
别名归一化器
使用 station_alias 和 train_id_mapping 做归一化
"""

from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class AliasNormalizer:
    """
    别名归一化器
    将文本中的站名、车次名归一化为标准代码
    """
    
    def __init__(self):
        self._station_map: Optional[Dict[str, str]] = None
        self._station_code_to_name: Optional[Dict[str, str]] = None
        self._train_no_to_id: Optional[Dict[str, str]] = None
    
    def _load_station_mapping(self) -> Dict[str, str]:
        """加载车站别名映射"""
        if self._station_map is not None:
            return self._station_map
        
        from models.data_loader import get_station_names, get_station_codes
        
        station_names = get_station_names()
        station_codes = get_station_codes()
        
        # 构建站名 -> 站码 映射
        self._station_map = {}
        for code, name in station_names.items():
            self._station_map[name] = code
            self._station_map[name + "站"] = code  # 添加"XX站"变体
            # 添加常见简称
            if len(name) >= 2:
                self._station_map[name[:2]] = code
        
        # 构建反向映射（站码 -> 站名）
        self._station_code_to_name = station_names
        
        logger.info(f"加载了 {len(self._station_map)} 个车站别名映射")
        return self._station_map
    
    def _load_train_mapping(self) -> Dict[str, str]:
        """加载列车ID映射"""
        if self._train_no_to_id is not None:
            return self._train_no_to_id
        
        try:
            from models.data_loader import load_trains
            trains = load_trains()
            self._train_no_to_id = {t["train_id"]: t["train_id"] for t in trains}
            logger.info(f"加载了 {len(self._train_no_to_id)} 个列车映射")
        except Exception as e:
            logger.warning(f"加载列车映射失败: {e}")
            self._train_no_to_id = {}
        
        return self._train_no_to_id
    
    def normalize_station(self, station_name: str) -> Optional[Dict[str, str]]:
        """
        归一化车站名称
        
        Args:
            station_name: 原始车站名称
            
        Returns:
            Dict: {station_code, station_name} 或 None
        """
        if not station_name:
            return None
        
        station_map = self._load_station_mapping()
        
        # 尝试直接匹配
        if station_name in station_map:
            code = station_map[station_name]
            return {
                "station_code": code,
                "station_name": self._station_code_to_name.get(code, station_name)
            }
        
        # 尝试去除"站"后匹配
        if station_name.endswith("站"):
            name_without_suffix = station_name[:-1]
            if name_without_suffix in station_map:
                code = station_map[name_without_suffix]
                return {
                    "station_code": code,
                    "station_name": self._station_code_to_name.get(code, name_without_suffix)
                }
        
        # 模糊匹配
        for name, code in station_map.items():
            if station_name in name or name in station_name:
                return {
                    "station_code": code,
                    "station_name": self._station_code_to_name.get(code, name)
                }
        
        logger.warning(f"未找到车站映射: {station_name}")
        return None
    
    def normalize_train(self, train_no: str) -> Optional[str]:
        """
        归一化列车车次
        
        Args:
            train_no: 原始车次号
            
        Returns:
            str: 归一化后的列车ID 或 None
        """
        if not train_no:
            return None
        
        train_map = self._load_train_mapping()
        
        # 尝试直接匹配
        if train_no in train_map:
            return train_no
        
        # 尝试模糊匹配
        for tid in train_map.keys():
            if train_no in tid or tid in train_no:
                return tid
        
        # 尝试添加/去除前缀
        for prefix in ["G", "D", "C", "T", "K", "Z"]:
            if not train_no.startswith(prefix):
                with_prefix = prefix + train_no
                if with_prefix in train_map:
                    return with_prefix
        
        logger.warning(f"未找到列车映射: {train_no}")
        return None
    
    def normalize_section(self, section_str: str) -> Optional[str]:
        """
        归一化区段字符串
        
        Args:
            section_str: 区段字符串，如 "徐水东-保定东"
            
        Returns:
            str: 归一化后的区段ID，如 "XSD-BDD" 或 None
        """
        if not section_str or "-" not in section_str:
            return None
        
        parts = section_str.replace(" ", "").split("-")
        if len(parts) != 2:
            return None
        
        from_station = self.normalize_station(parts[0])
        to_station = self.normalize_station(parts[1])
        
        if from_station and to_station:
            return f"{from_station['station_code']}-{to_station['station_code']}"
        
        return None


# 全局实例
_alias_normalizer: Optional[AliasNormalizer] = None


def get_alias_normalizer() -> AliasNormalizer:
    """获取别名归一化器实例"""
    global _alias_normalizer
    if _alias_normalizer is None:
        _alias_normalizer = AliasNormalizer()
    return _alias_normalizer