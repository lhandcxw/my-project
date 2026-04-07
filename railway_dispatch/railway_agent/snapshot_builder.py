# -*- coding: utf-8 -*-
"""
SnapshotBuilder - 确定性网络快照构建器
从 CanonicalDispatchRequest 切出观察窗口，构建 NetworkSnapshot
不调用 LLM，只使用结构化数据和确定性逻辑
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from models.preprocess_models import CanonicalDispatchRequest
from models.workflow_models import NetworkSnapshot
from models.data_loader import get_station_codes, load_trains, load_stations
import logging

logger = logging.getLogger(__name__)


class SnapshotBuilder:
    """
    确定性网络快照构建器
    输入：CanonicalDispatchRequest + 结构化数据
    输出：NetworkSnapshot
    """
    
    # 车站代码表（固定顺序）
    STATION_CODES = None  # 延迟加载
    
    @classmethod
    def _get_station_codes(cls) -> List[str]:
        """获取车站代码列表（延迟加载）"""
        if cls.STATION_CODES is None:
            cls.STATION_CODES = get_station_codes()
        return cls.STATION_CODES
    
    @classmethod
    def build(cls, canonical_request: CanonicalDispatchRequest) -> NetworkSnapshot:
        """
        构建网络快照
        
        Args:
            canonical_request: 标准化调度请求
            
        Returns:
            NetworkSnapshot: 确定性构建的网络快照
        """
        logger.info(f"=== SnapshotBuilder 构建快照: request_id={canonical_request.request_id}")
        
        # 1. 确定观察走廊 (corridor)
        corridor_id, selection_reason = cls._determine_corridor(canonical_request)
        
        # 2. 确定时间窗口 (window_start, window_end)
        window_start, window_end = cls._determine_time_window(canonical_request)
        
        # 3. 筛选候选列车 (candidate_train_ids)
        candidate_train_ids = cls._select_candidate_trains(corridor_id, window_start, window_end)
        
        # 4. 确定排除列车 (excluded_train_ids) - 如已通过受影响区段的列车
        excluded_train_ids = cls._determine_excluded_trains(
            canonical_request, candidate_train_ids
        )
        
        # 5. 构建 NetworkSnapshot
        snapshot_time = datetime.now()
        
        # 如果 canonical_request 有 snapshot_time，使用它
        if canonical_request.snapshot_time:
            try:
                snapshot_time = datetime.fromisoformat(canonical_request.snapshot_time)
            except:
                pass
        
        network_snapshot = NetworkSnapshot(
            snapshot_time=snapshot_time,
            solving_window={
                "corridor_id": corridor_id,
                "window_start": window_start,
                "window_end": window_end,
                "selection_reason": selection_reason
            },
            train_count=len(candidate_train_ids),
            candidate_train_ids=candidate_train_ids,
            excluded_train_ids=excluded_train_ids
        )
        
        logger.info(f"=== SnapshotBuilder 完成: corridor={corridor_id}, train_count={len(candidate_train_ids)}")
        
        return network_snapshot
    
    @classmethod
    def _determine_corridor(cls, canonical_request: CanonicalDispatchRequest) -> tuple:
        """
        确定观察走廊
        
        Returns:
            (corridor_id: str, selection_reason: str)
        """
        # 优先使用 corridor_hint
        if canonical_request.corridor_hint:
            return canonical_request.corridor_hint, "使用 corridor_hint"
        
        # 从 location 提取
        location = canonical_request.location
        station_codes = cls._get_station_codes()
        
        if location and location.station_code:
            station_code = location.station_code
            if station_code in station_codes:
                loc_idx = station_codes.index(station_code)
                # 前后各扩展2个区间
                start_idx = max(0, loc_idx - 2)
                end_idx = min(len(station_codes) - 1, loc_idx + 2)
                corridor = f"{station_codes[start_idx]}-{station_codes[end_idx]}"
                return corridor, f"基于站点 {station_code} 扩展"
        
        if location and location.section_id:
            return location.section_id, "使用 section_id"
        
        # 默认：全走廊
        station_codes = cls._get_station_codes()
        if station_codes:
            return f"{station_codes[0]}-{station_codes[-1]}", "使用全走廊"
        
        return "UNKNOWN", "无法确定走廊"
    
    @classmethod
    def _determine_time_window(cls, canonical_request: CanonicalDispatchRequest) -> tuple:
        """
        确定时间窗口
        
        Returns:
            (window_start: str, window_end: str)
        """
        from datetime import timedelta
        
        # 使用 event_time 作为窗口开始
        window_start = canonical_request.event_time or datetime.now().isoformat()
        
        # 计算窗口结束：event_time + expected_duration + buffer
        duration = canonical_request.expected_duration_minutes or 60
        try:
            start_dt = datetime.fromisoformat(window_start)
            # 使用 timedelta 正确计算时间，避免分钟溢出
            end_dt = start_dt + timedelta(minutes=duration + 30)  # 加30分钟缓冲
            window_end = end_dt.isoformat()
        except Exception as e:
            logger.warning(f"时间窗口计算失败: {e}，使用默认窗口")
            # 回退：使用当前时间+90分钟
            end_dt = datetime.now() + timedelta(minutes=90)
            window_end = end_dt.isoformat()
        
        return window_start, window_end
    
    @classmethod
    def _select_candidate_trains(cls, corridor_id: str, window_start: str, window_end: str) -> List[str]:
        """
        筛选候选列车
        
        在观察走廊内、时间窗口内的列车
        """
        if not corridor_id or corridor_id == "UNKNOWN":
            return []
        
        # 解析 corridor
        if "-" in corridor_id:
            parts = corridor_id.split("-")
            if len(parts) >= 2:
                start_station = parts[0]
                end_station = parts[-1]
            else:
                start_station = end_station = parts[0]
        else:
            start_station = end_station = corridor_id
        
        station_codes = cls._get_station_codes()
        
        # 确定走廊内的站码列表
        try:
            start_idx = station_codes.index(start_station)
            end_idx = station_codes.index(end_station)
            if start_idx > end_idx:
                start_idx, end_idx = end_idx, start_idx
            corridor_stations = station_codes[start_idx:end_idx+1]
        except ValueError:
            corridor_stations = [start_station]
        
        # 加载列车数据，筛选经过走廊的列车
        trains = load_trains()
        candidate_ids = []
        
        for train in trains:
            train_id = train.get("train_id")
            schedule = train.get("schedule", {})
            stops = schedule.get("stops", [])
            
            # 检查是否经过走廊内的任一车站
            for stop in stops:
                stop_code = stop.get("station_code")
                if stop_code in corridor_stations:
                    candidate_ids.append(train_id)
                    break
        
        return candidate_ids
    
    @classmethod
    def _determine_excluded_trains(cls, canonical_request: CanonicalDispatchRequest, candidate_ids: List[str]) -> List[str]:
        """
        确定排除的列车
        
        例如：已经通过受影响区段的列车不需要调整
        """
        # 当前实现：暂不排除任何列车
        # 后续可扩展：根据列车当前位置排除
        return []


def build_network_snapshot(canonical_request: CanonicalDispatchRequest) -> NetworkSnapshot:
    """
    便捷函数：构建网络快照
    
    Args:
        canonical_request: 标准化调度请求
        
    Returns:
        NetworkSnapshot
    """
    return SnapshotBuilder.build(canonical_request)