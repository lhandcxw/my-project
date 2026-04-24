# -*- coding: utf-8 -*-
"""
SnapshotBuilder - 确定性网络快照构建器
从 CanonicalDispatchRequest 切出观察窗口，构建 NetworkSnapshot
不调用 LLM，只使用结构化数据和确定性逻辑

v4.1 更新：
- 成为唯一构建 NetworkSnapshot 的入口
- 支持自定义时间窗口参数
- 完善候选列车筛选逻辑
- 增强确定性的走廊选择
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
    这是唯一构建 NetworkSnapshot 的入口

    输入：CanonicalDispatchRequest + 可选的时间窗口
    输出：NetworkSnapshot
    """

    # 车站代码表（延迟加载）
    _station_codes = None
    _trains_cache = None

    @classmethod
    def _get_station_codes(cls) -> List[str]:
        """获取车站代码列表（延迟加载）"""
        if cls._station_codes is None:
            cls._station_codes = get_station_codes()
        return cls._station_codes

    @classmethod
    def _get_trains(cls) -> List[Dict[str, Any]]:
        """获取列车数据（带缓存）"""
        if cls._trains_cache is None:
            cls._trains_cache = load_trains()
        return cls._trains_cache

    @classmethod
    def build(
        cls,
        canonical_request: CanonicalDispatchRequest,
        time_window: Optional[Dict[str, str]] = None,
        window_size: int = 2
    ) -> NetworkSnapshot:
        """
        构建网络快照

        Args:
            canonical_request: 标准化调度请求
            time_window: 可选的时间窗口，格式：{"start": "06:00", "end": "24:00"}
            window_size: 观察窗口大小（前后扩展的区间数），默认2

        Returns:
            NetworkSnapshot: 确定性构建的网络快照
        """
        logger.info(f"=== SnapshotBuilder 构建快照: request_id={canonical_request.request_id}")

        # 1. 确定观察走廊
        observation_corridor = cls._determine_observation_corridor(canonical_request, window_size)

        # 2. 筛选候选列车
        candidate_train_ids = cls._filter_candidate_trains(canonical_request, observation_corridor, window_size)

        # 3. 确定排除列车
        excluded_train_ids = cls._determine_excluded_trains(canonical_request, candidate_train_ids)

        # 4. 确定时间窗口
        if time_window:
            planning_time_window = time_window
        else:
            planning_time_window = cls._determine_default_time_window(canonical_request)

        # 5. 构建求解窗口
        solving_window = {
            "corridor_id": observation_corridor,
            "observation_corridor": observation_corridor,
            "window_start": planning_time_window.get("start", "06:00"),
            "window_end": planning_time_window.get("end", "24:00"),
            "selection_reason": cls._generate_selection_reason(canonical_request)
        }

        # 6. 构建窗口内列车集合
        trains_in_window = cls._build_trains_in_window(
            candidate_train_ids,
            observation_corridor,
            window_size
        )

        # 7. 构建车站信息
        stations_info = cls._build_stations_info(observation_corridor, window_size)

        # 8. 构建区间信息
        sections_info = cls._build_sections_info(observation_corridor, window_size)

        # 9. 构建追踪间隔
        headways = cls._build_headways(observation_corridor)

        # 10. 构建当前延误信息
        current_delays = cls._build_current_delays(canonical_request, candidate_train_ids)

        # 确定快照时间
        if canonical_request.snapshot_time:
            try:
                snapshot_time = datetime.fromisoformat(canonical_request.snapshot_time)
            except:
                snapshot_time = datetime.now()
        else:
            snapshot_time = datetime.now()

        network_snapshot = NetworkSnapshot(
            snapshot_time=snapshot_time,
            solving_window={
                "observation_corridor": observation_corridor,
                "planning_time_window": planning_time_window
            },
            candidate_train_ids=candidate_train_ids,
            excluded_train_ids=excluded_train_ids,
            trains=trains_in_window,
            train_count=len(candidate_train_ids),
            stations=stations_info,
            sections=sections_info,
            headways=headways,
            current_delays=current_delays
        )

        logger.info(f"=== SnapshotBuilder 完成: corridor={observation_corridor}, train_count={len(candidate_train_ids)}")

        return network_snapshot

    @classmethod
    def _determine_observation_corridor(
        cls,
        canonical_request: CanonicalDispatchRequest,
        window_size: int
    ) -> str:
        """
        确定观察走廊

        规则：
        1. 如果有明确的 corridor_hint，优先使用
        2. 如果有 location.station_code，以其为中心，前后扩展 window_size 个区间
        3. 如果有 location.section_id，扩展该区间
        4. 默认使用全走廊
        """
        # 规则1：优先使用 corridor_hint
        if canonical_request.corridor_hint:
            logger.info(f"使用 corridor_hint: {canonical_request.corridor_hint}")
            return canonical_request.corridor_hint

        # 规则2：基于车站确定
        location = canonical_request.location
        station_codes = cls._get_station_codes()

        if location and location.station_code:
            station_code = location.station_code
            if station_code in station_codes:
                loc_idx = station_codes.index(station_code)
                start_idx = max(0, loc_idx - window_size)
                end_idx = min(len(station_codes) - 1, loc_idx + window_size)
                corridor = f"{station_codes[start_idx]}-{station_codes[end_idx]}"
                logger.info(f"基于车站 {station_code} 确定走廊: {corridor}")
                return corridor

        # 规则3：基于区间确定
        if location and location.section_id:
            section_id = location.section_id
            # 扩展该区间
            if "-" in section_id:
                logger.info(f"基于区间 {section_id} 确定走廊")
                return section_id  # 简化处理，实际可以扩展

        # 规则4：默认使用全走廊
        station_codes = cls._get_station_codes()
        if station_codes:
            default_corridor = f"{station_codes[0]}-{station_codes[-1]}"
            logger.info(f"使用默认走廊: {default_corridor}")
            return default_corridor

        logger.warning("无法确定走廊，使用默认值")
        return "UNKNOWN"

    @classmethod
    def _filter_candidate_trains(
        cls,
        canonical_request: CanonicalDispatchRequest,
        observation_corridor: str,
        window_size: int
    ) -> List[str]:
        """
        筛选候选列车

        规则：
        1. 优先使用 affected_train_ids（如果有）
        2. 否则筛选观察走廊内的列车
        """
        # 规则1：优先使用明确指定的受影响列车
        if canonical_request.affected_train_ids:
            logger.info(f"使用指定的受影响列车: {canonical_request.affected_train_ids}")
            return canonical_request.affected_train_ids

        # 规则2：筛选观察走廊内的列车
        observation_window_codes = cls._extract_station_codes_from_corridor(
            observation_corridor,
            window_size
        )

        all_trains = cls._get_trains()
        candidate_ids = []

        for train in all_trains:
            train_id = train.get("train_id", "")
            stops = train.get("schedule", {}).get("stops", [])

            # 检查列车是否经过观察窗口内的车站
            for stop in stops:
                stop_code = stop.get("station_code")
                if stop_code and stop_code in observation_window_codes:
                    candidate_ids.append(train_id)
                    break

        logger.info(f"筛选出候选列车: {len(candidate_ids)} 列")
        return candidate_ids

    @classmethod
    def _determine_excluded_trains(
        cls,
        canonical_request: CanonicalDispatchRequest,
        candidate_train_ids: List[str]
    ) -> List[str]:
        """
        确定排除的列车

        规则：
        1. 已通过事故位置的列车
        2. 在时间窗口前已经通过整个走廊的列车
        """
        excluded_ids = []

        # 简化处理：目前暂不实现复杂的排除逻辑
        # 后续可以根据实际情况完善

        return excluded_ids

    @classmethod
    def _extract_station_codes_from_corridor(
        cls,
        corridor: str,
        window_size: int
    ) -> List[str]:
        """
        从走廊字符串中提取站码列表
        """
        if "-" not in corridor:
            return [corridor]

        start_code, end_code = corridor.split("-", 1)
        station_codes = cls._get_station_codes()

        try:
            start_idx = station_codes.index(start_code)
            end_idx = station_codes.index(end_code)
            # 扩展窗口
            start_idx = max(0, start_idx - window_size)
            end_idx = min(len(station_codes), end_idx + window_size + 1)
            return station_codes[start_idx:end_idx]
        except ValueError:
            # 如果站码不存在，返回全列表
            return station_codes

    @classmethod
    def _generate_selection_reason(cls, canonical_request: CanonicalDispatchRequest) -> str:
        """生成选择原因"""
        reasons = []

        if canonical_request.affected_train_ids:
            reasons.append(f"指定受影响列车: {', '.join(canonical_request.affected_train_ids)}")

        location = canonical_request.location
        if location and location.station_code:
            reasons.append(f"事故位置: {location.station_code}")

        if canonical_request.corridor_hint:
            reasons.append(f"走廊提示: {canonical_request.corridor_hint}")

        return "; ".join(reasons) if reasons else "默认选择"

    @classmethod
    def _determine_default_time_window(cls, canonical_request: CanonicalDispatchRequest) -> Dict[str, str]:
        """确定默认时间窗口"""
        from datetime import timedelta

        # 使用 event_time 作为窗口开始
        window_start = canonical_request.event_time or datetime.now().isoformat()

        # 计算窗口结束：event_time + expected_duration + buffer
        duration = canonical_request.expected_duration_minutes or 60
        try:
            start_dt = datetime.fromisoformat(window_start)
            end_dt = start_dt + timedelta(minutes=duration + 30)  # 加30分钟缓冲
            window_end = end_dt.isoformat()
        except Exception as e:
            logger.warning(f"时间窗口计算失败: {e}，使用默认窗口")
            # 回退：使用当前时间+90分钟
            end_dt = datetime.now() + timedelta(minutes=90)
            window_end = end_dt.isoformat()

        return {"start": window_start, "end": window_end}

    @classmethod
    def _build_trains_in_window(
        cls,
        candidate_train_ids: List[str],
        observation_corridor: str,
        window_size: int
    ) -> List[Dict[str, Any]]:
        """构建窗口内列车集合"""
        all_trains = cls._get_trains()
        trains_in_window = []

        for train_id in candidate_train_ids:
            for train in all_trains:
                if train.get("train_id") == train_id:
                    trains_in_window.append({
                        "train_id": train.get("train_id"),
                        "train_type": train.get("train_type", "G"),
                        "stops_count": len(train.get("schedule", {}).get("stops", [])),
                        "corridor": observation_corridor
                    })
                    break

        return trains_in_window

    @classmethod
    def _build_stations_info(
        cls,
        observation_corridor: str,
        window_size: int
    ) -> List[Dict[str, Any]]:
        """构建车站信息"""
        station_codes_in_window = cls._extract_station_codes_from_corridor(
            observation_corridor,
            window_size
        )

        # 【修复】使用真实车站容量数据，不再硬编码
        from models.data_loader import load_stations
        all_stations_data = {s["station_code"]: s for s in load_stations()}

        stations_info = []
        for station_code in station_codes_in_window:
            station_data = all_stations_data.get(station_code, {})
            stations_info.append({
                "station_code": station_code,
                "track_count": station_data.get("track_count", 4),  # 使用真实数据
                "current_occupancy": 0  # 简化处理
            })

        return stations_info

    @classmethod
    def _build_sections_info(
        cls,
        observation_corridor: str,
        window_size: int
    ) -> List[Dict[str, Any]]:
        """构建区间信息"""
        station_codes_in_window = cls._extract_station_codes_from_corridor(
            observation_corridor,
            window_size
        )

        sections_info = []
        for i in range(len(station_codes_in_window) - 1):
            section_id = f"{station_codes_in_window[i]}-{station_codes_in_window[i+1]}"
            sections_info.append({
                "section_id": section_id,
                "from_station": station_codes_in_window[i],
                "to_station": station_codes_in_window[i+1],
                "status": "normal",  # 简化处理
                "remaining_capacity": 1  # 简化处理
            })

        return sections_info

    @classmethod
    def _build_headways(cls, observation_corridor: str) -> Dict[str, str]:
        """构建追踪间隔（headway约束）"""
        station_codes_in_window = cls._extract_station_codes_from_corridor(
            observation_corridor,
            window_size=0
        )

        headways = {}
        # 简化处理：所有区间使用相同追踪间隔
        for i in range(len(station_codes_in_window) - 1):
            section_key = f"{station_codes_in_window[i]}-{station_codes_in_window[i+1]}"
            headways[section_key] = "180"  # 3分钟 = 180秒

        return headways

    @classmethod
    def _build_current_delays(
        cls,
        canonical_request: CanonicalDispatchRequest,
        candidate_train_ids: List[str]
    ) -> Dict[str, float]:
        """构建当前延误信息"""
        current_delays = {}

        # 如果 CanonicalDispatchRequest 中有延误信息
        if canonical_request.reported_delay_seconds:
            # 简化处理：所有候选列车应用相同延误
            for train_id in candidate_train_ids:
                current_delays[train_id] = float(canonical_request.reported_delay_seconds)

        return current_delays


# 全局实例
_snapshot_builder: Optional[SnapshotBuilder] = None


def get_snapshot_builder() -> SnapshotBuilder:
    """获取 SnapshotBuilder 实例"""
    global _snapshot_builder
    if _snapshot_builder is None:
        _snapshot_builder = SnapshotBuilder()
    return _snapshot_builder


# 向后兼容的便捷函数
def build_network_snapshot(canonical_request: CanonicalDispatchRequest) -> NetworkSnapshot:
    """
    便捷函数：构建网络快照（向后兼容）

    Args:
        canonical_request: 标准化调度请求

    Returns:
        NetworkSnapshot
    """
    return get_snapshot_builder().build(canonical_request)
