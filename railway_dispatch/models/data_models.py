# -*- coding: utf-8 -*-
"""
铁路调度系统 - 数据模型模块
对应架构文档第2节：数据集格式设计
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime, time as dt_time
import json


class ScenarioType(str, Enum):
    """场景类型枚举"""
    TEMPORARY_SPEED_LIMIT = "temporary_speed_limit"  # 临时限速
    SUDDEN_FAILURE = "sudden_failure"  # 突发故障
    SECTION_INTERRUPT = "section_interrupt"  # 区间中断


class DelayLevel(str, Enum):
    """延误等级枚举"""
    MICRO = "0"    # 微小延误 [0, 5)分钟
    SMALL = "5"     # 小延误 [5, 30)分钟
    MEDIUM = "30"  # 中延误 [30, 100)分钟
    LARGE = "100"  # 大延误 [100, +∞)分钟


class TrainStop(BaseModel):
    """列车停靠站信息"""
    station_code: str = Field(description="车站编码")
    station_name: str = Field(description="车站名称")
    arrival_time: str = Field(description="到达时间 HH:MM:SS")
    departure_time: str = Field(description="发车时间 HH:MM:SS")


class TrainSchedule(BaseModel):
    """列车时刻表"""
    stops: List[TrainStop] = Field(description="停靠站列表")


class Train(BaseModel):
    """列车数据模型"""
    train_id: str = Field(description="列车唯一标识(车次号)")
    train_type: str = Field(default="高速动车组", description="列车类型")
    schedule: TrainSchedule = Field(description="时刻表")

    def time_to_seconds(self, time_str: str) -> int:
        """将时间字符串转换为秒数"""
        h, m, s = map(int, time_str.split(':'))
        return h * 3600 + m * 60 + s

    def seconds_to_time(self, seconds: int) -> str:
        """将秒数转换为时间字符串"""
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def get_all_times(self) -> Dict[str, int]:
        """获取列车所有经停站的到发时间(秒)"""
        times = {}
        for stop in self.schedule.stops:
            times[f"{stop.station_code}_arrival"] = self.time_to_seconds(stop.arrival_time)
            times[f"{stop.station_code}_departure"] = self.time_to_seconds(stop.departure_time)
        return times


class Station(BaseModel):
    """车站数据模型"""
    station_code: str = Field(description="车站编码")
    station_name: str = Field(description="车站名称")
    track_count: int = Field(default=1, description="股道总数")

    def get_station_index(self, station_code: str, all_stations: List['Station']) -> int:
        """获取车站在线路中的索引位置"""
        for i, s in enumerate(all_stations):
            if s.station_code == station_code:
                return i
        return -1


class DelayLocation(BaseModel):
    """延误位置"""
    location_type: str = Field(description="位置类型: station/section")
    station_code: Optional[str] = Field(default=None, description="车站编码")
    section_id: Optional[str] = Field(default=None, description="区间ID")
    position: Optional[str] = Field(default=None, description="位置描述")


class InjectedDelay(BaseModel):
    """注入的延误信息"""
    train_id: str = Field(description="列车ID")
    location: DelayLocation = Field(description="延误位置")
    initial_delay_seconds: int = Field(description="初始延误时间(秒)")
    timestamp: str = Field(description="发生时间戳")


class DelayInjection(BaseModel):
    """延误注入数据模型"""
    scenario_type: ScenarioType = Field(description="场景类型")
    scenario_id: str = Field(description="场景ID")
    injected_delays: List[InjectedDelay] = Field(description="注入的延误列表")
    affected_trains: List[str] = Field(description="受影响列车列表")
    scenario_params: Dict[str, Any] = Field(default_factory=dict, description="场景参数")

    @classmethod
    def create_temporary_speed_limit(
        cls,
        scenario_id: str,
        train_delays: List[Dict],
        limit_speed: int,
        duration: int,
        affected_section: str
    ):
        """创建临时限速场景"""
        injected = []
        affected = []
        for td in train_delays:
            delay = InjectedDelay(
                train_id=td['train_id'],
                location=DelayLocation(
                    location_type="station",
                    station_code=td.get('station_code', 'TJG'),
                    position="station"
                ),
                initial_delay_seconds=td['delay_seconds'],
                timestamp=datetime.now().isoformat()
            )
            injected.append(delay)
            affected.append(td['train_id'])

        return cls(
            scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
            scenario_id=scenario_id,
            injected_delays=injected,
            affected_trains=affected,
            scenario_params={
                "limit_speed_kmh": limit_speed,
                "duration_minutes": duration,
                "affected_section": affected_section
            }
        )

    @classmethod
    def create_sudden_failure(
        cls,
        scenario_id: str,
        train_id: str,
        delay_seconds: int,
        station_code: str,
        failure_type: str = "vehicle_breakdown",
        repair_time: int = 60
    ):
        """创建突发故障场景"""
        return cls(
            scenario_type=ScenarioType.SUDDEN_FAILURE,
            scenario_id=scenario_id,
            injected_delays=[
                InjectedDelay(
                    train_id=train_id,
                    location=DelayLocation(
                        location_type="station",
                        station_code=station_code,
                        position="station"
                    ),
                    initial_delay_seconds=delay_seconds,
                    timestamp=datetime.now().isoformat()
                )
            ],
            affected_trains=[train_id],
            scenario_params={
                "failure_type": failure_type,
                "estimated_repair_time": repair_time
            }
        )


class DelayPrediction(BaseModel):
    """延误预测"""
    station_code: str = Field(description="车站编码")
    predicted_delay_seconds: int = Field(description="预测延误(秒)")
    confidence: float = Field(default=0.9, description="置信度")


class TrainDelayPrediction(BaseModel):
    """列车延误预测"""
    train_id: str = Field(description="列车ID")
    current_station: str = Field(description="当前车站")
    future_predictions: List[DelayPrediction] = Field(default_factory=list, description="未来预测")


class DelayPredictionTable(BaseModel):
    """延误预测时间表"""
    prediction_table: List[TrainDelayPrediction] = Field(default_factory=list)


# ============================================
# 示例数据生成 (已弃用，请使用 data_loader 中的真实数据)
# ============================================

def create_sample_trains() -> List[Train]:
    """
    创建示例列车数据
    注意：此函数已弃用，请使用 data_loader.get_trains_pydantic() 获取真实数据
    """
    # 直接从data_loader加载真实数据
    from models.data_loader import get_trains_pydantic
    return get_trains_pydantic()


def create_sample_stations() -> List[Station]:
    """
    创建示例车站数据
    注意：此函数已弃用，请使用 data_loader.get_stations_pydantic() 获取真实数据
    """
    # 直接从data_loader加载真实数据
    from models.data_loader import get_stations_pydantic
    return get_stations_pydantic()


def save_sample_data():
    """保存示例数据到JSON文件（已弃用）"""
    print("警告：save_sample_data() 已弃用，请直接使用 data/trains.json 和 data/stations.json 中的真实数据")


if __name__ == "__main__":
    save_sample_data()
