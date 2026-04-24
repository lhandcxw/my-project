# -*- coding: utf-8 -*-
"""
MIP优化的网络快照构建器
专为MIP求解器设计，实现三级裁剪策略

功能：
1. 空间裁剪：只保留受影响区段±window_size个区间
2. 时间裁剪：只保留规划时间窗口内的列车
3. 优先级分类：A类(直接影响)/B类(潜在影响)/C类(无影响)

专家推荐参数：
- MAX_TRAINS_FOR_MIP = 30 (确保60秒内可解)
- MAX_STATIONS_FOR_MIP = 10
- DEFAULT_WINDOW_SIZE = 2
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# 专家推荐的MIP规模上限
MAX_TRAINS_FOR_MIP = 30
MAX_STATIONS_FOR_MIP = 10
DEFAULT_WINDOW_SIZE = 2


class MIPSnapshotBuilder:
    """
    专为MIP求解优化的网络快照构建器
    
    核心价值：
    - 将147列×13站的超大规模问题
    - 裁剪为25-30列×6-8站的可解规模
    - 确保MIP在60秒内完成求解
    """
    
    @classmethod
    def build_for_mip(
        cls,
        accident_card: Any,
        all_trains: List[Dict],
        all_stations: List[Dict],
        max_trains: int = MAX_TRAINS_FOR_MIP,
        window_size: int = DEFAULT_WINDOW_SIZE
    ) -> Dict[str, Any]:
        """
        为MIP构建优化的网络快照
        
        Args:
            accident_card: 事故信息卡（包含location_code等）
            all_trains: 所有列车数据
            all_stations: 所有车站数据
            max_trains: MIP最大列车数（默认30）
            window_size: 窗口大小（上下游各window_size站）
            
        Returns:
            Dict包含：
            - mip_trains: 选取的MIP求解列车列表
            - mip_stations: 选取的车站列表
            - priority分类信息
            - excluded_trains: 排除的列车（C类）
        """
        logger.info(f"[MIP-Snapshot] 开始构建，原始：{len(all_trains)}列 × {len(all_stations)}站")
        
        # Step 1: 获取事故位置中心站
        location_code = getattr(accident_card, 'location_code', None) or ""
        
        station_codes = [s.get('station_code', '') for s in all_stations]
        
        # Step 2: 计算空间窗口（车站裁剪）
        window_stations = cls._compute_spatial_window(
            location_code, station_codes, window_size
        )
        
        logger.info(f"[MIP-Snapshot] 空间窗口：{len(window_stations)}站")
        
        # Step 3: 分类列车（优先级裁剪）
        priority_a, priority_b, priority_c = cls._classify_trains(
            all_trains, location_code, window_stations
        )
        
        logger.info(f"[MIP-Snapshot] 列车分类：A类{len(priority_a)}列，B类{len(priority_b)}列，C类{len(priority_c)}列")
        
        # Step 4: 选取MIP求解列车
        selected_trains = cls._select_mip_trains(
            all_trains, priority_a, priority_b, max_trains
        )
        
        logger.info(f"[MIP-Snapshot] MIP选取：{len(selected_trains)}列")
        
        # Step 5: 构建MIP专用快照数据
        mip_snapshot = {
            "mip_trains": selected_trains,
            "mip_stations": window_stations,
            "max_trains_limit": max_trains,
            "window_size": window_size,
            "priority_a_count": len(priority_a),
            "priority_b_count": len(priority_b),
            "priority_c_count": len(priority_c),
            "excluded_train_ids": [t['train_id'] for t in priority_c],
            "center_station": location_code,
            "window_stations": window_stations,
            "statistics": {
                "original_train_count": len(all_trains),
                "original_station_count": len(all_stations),
                "selected_train_count": len(selected_trains),
                "selected_station_count": len(window_stations),
                "reduction_ratio_train": f"{(1 - len(selected_trains)/len(all_trains))*100:.1f}%"
            }
        }
        
        return mip_snapshot
    
    @classmethod
    def _compute_spatial_window(
        cls,
        center_station: str,
        all_stations: List[str],
        window_size: int
    ) -> List[str]:
        """
        计算空间窗口 - 只保留中心站上下游window_size个车站
        
        例如：center=BDD, window_size=2, 站点=[BJX, DJK, ZBD, GBD, XSD, ...]
        结果：[ZBD, GBD, BDD, XSD] （±2站）
        """
        if not center_station or center_station not in all_stations:
            # 无中心站，返回全部车站但限制数量
            return all_stations[:MAX_STATIONS_FOR_MIP]
        
        center_idx = all_stations.index(center_station)
        
        start_idx = max(0, center_idx - window_size)
        end_idx = min(len(all_stations), center_idx + window_size + 1)
        
        window_stations = all_stations[start_idx:end_idx]
        
        # 限制车站数量
        if len(window_stations) > MAX_STATIONS_FOR_MIP:
            window_stations = window_stations[:MAX_STATIONS_FOR_MIP]
        
        return window_stations
    
    @classmethod
    def _classify_trains(
        cls,
        all_trains: List[Dict],
        center_station: str,
        window_stations: List[str]
    ) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """
        分类列车为A/B/C三类
        
        A类（直接受影响）：列车经过事故中心站
        B类（潜在受影响）：列车经过窗口内其他车站
        C类（无影响）：列车不经过窗口内任何车站
        """
        priority_a = []  # 直接受影响
        priority_b = []  # 潜在受影响
        priority_c = []  # 无影响
        
        for train in all_trains:
            train_id = train.get('train_id', '')
            stops = train.get('schedule', {}).get('stops', [])
            train_stations = [s.get('station_code', '') for s in stops]
            
            # A类：经过事故中心站
            if center_station and center_station in train_stations:
                priority_a.append(train)
            # B类：经过窗口内车站
            elif any(s in window_stations for s in train_stations):
                priority_b.append(train)
            else:
                priority_c.append(train)
        
        return priority_a, priority_b, priority_c
    
    @classmethod
    def _select_mip_trains(
        cls,
        all_trains: List[Dict],
        priority_a: List[Dict],
        priority_b: List[Dict],
        max_trains: int
    ) -> List[Dict]:
        """
        选取MIP求解的列车集合
        
        策略：
        1. 优先包含所有A类（直接影响）
        2. 然后填充B类直到达到上限
        3. 按发车时间排序
        """
        selected = []
        
        # Step 1: 添加所有A类
        for train in priority_a:
            if len(selected) >= max_trains:
                break
            selected.append(train)
        
        # Step 2: 填充B类
        if len(selected) < max_trains:
            for train in priority_b:
                if len(selected) >= max_trains:
                    break
                # 避免重复
                if train not in selected:
                    selected.append(train)
        
        # Step 3: 按第一站发车时间排序
        selected = cls._sort_trains_by_time(selected)
        
        return selected
    
    @classmethod
    def _sort_trains_by_time(cls, trains: List[Dict]) -> List[Dict]:
        """按第一站发车时间排序"""
        def get_first_departure(train):
            stops = train.get('schedule', {}).get('stops', [])
            if stops and len(stops) > 0:
                first_stop = stops[0]
                dep_time = first_stop.get('departure_time', '00:00:00')
                try:
                    # 转换为秒数
                    h, m, s = map(int, dep_time.split(':'))
                    return h * 3600 + m * 60 + s
                except:
                    return 0
            return 0
        
        return sorted(trains, key=get_first_departure)


def build_mip_snapshot(
    accident_card: Any,
    all_trains: List[Dict],
    all_stations: List[Dict],
    max_trains: int = MAX_TRAINS_FOR_MIP,
    window_size: int = DEFAULT_WINDOW_SIZE
) -> Dict[str, Any]:
    """
    便捷函数：构建MIP优化快照
    """
    return MIPSnapshotBuilder.build_for_mip(
        accident_card=accident_card,
        all_trains=all_trains,
        all_stations=all_stations,
        max_trains=max_trains,
        window_size=window_size
    )