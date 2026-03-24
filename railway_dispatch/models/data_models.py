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
    platform: str = Field(description="站台编号")


class TrainSchedule(BaseModel):
    """列车时刻表"""
    stops: List[TrainStop] = Field(description="停靠站列表")


class SlackTime(BaseModel):
    """冗余时间配置"""
    max_station_slack: int = Field(default=300, description="最大车站冗余(秒)")
    max_section_slack: int = Field(default=180, description="最大区间冗余(秒)")
    total_slack: int = Field(default=480, description="总冗余时间(秒)")


class Train(BaseModel):
    """列车数据模型"""
    train_id: str = Field(description="列车唯一标识(车次号)")
    train_type: str = Field(default="高速动车组", description="列车类型")
    speed_level: int = Field(default=350, description="速度等级(km/h)")
    schedule: TrainSchedule = Field(description="时刻表")
    slack_time: SlackTime = Field(default_factory=SlackTime, description="冗余时间")

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


class Platform(BaseModel):
    """站台信息"""
    platform_id: str = Field(description="站台ID")
    track_id: str = Field(description="股道ID")
    capacity: int = Field(default=1, description="容量")


class ThroatZone(BaseModel):
    """咽喉区信息"""
    zone_id: str = Field(description="咽喉区ID")
    name: str = Field(description="咽喉区名称")
    conflicts: List[str] = Field(default_factory=list, description="冲突区域")


class ConnectionSection(BaseModel):
    """连接的区间"""
    section_id: str = Field(description="区间ID")
    to_station: str = Field(description="到达车站")
    distance_km: float = Field(description="距离(公里)")


class Station(BaseModel):
    """车站数据模型"""
    station_code: str = Field(description="车站编码")
    station_name: str = Field(description="车站名称")
    track_count: int = Field(default=1, description="股道总数")
    platforms: List[Platform] = Field(default_factory=list, description="站台列表")
    throat_zones: List[ThroatZone] = Field(default_factory=list, description="咽喉区")
    connection_sections: List[ConnectionSection] = Field(default_factory=list, description="连接区间")

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
                    position="platform"
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
                        position="platform"
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
# 示例数据生成
# ============================================

def create_sample_trains() -> List[Train]:
    """
    创建示例列车数据
    优先使用真实数据，如果没有则使用示例数据
    """
    # 尝试从data_loader加载真实数据
    try:
        from models.data_loader import get_trains_pydantic, is_using_real_data
        if is_using_real_data():
            return get_trains_pydantic()
    except Exception:
        pass

    # 如果没有启用真实数据或加载失败，使用示例数据
    # 设计原则：
    # - 5列车5车站
    # - 紧密追踪间隔（10-15分钟），前车延误会影响后续车
    # - 最小冗余，让延误传播明显
    # - 时刻表紧密衔接，需通过调度优化调整
    # 基础时间：08:00:00
    # 追踪间隔：10-15分钟
    return [
        Train(
            train_id="G1001",
            speed_level=350,
            schedule=TrainSchedule(stops=[
                TrainStop(station_code="BJP", station_name="北京西", arrival_time="08:00:00", departure_time="08:10:00", platform="1"),
                TrainStop(station_code="TJG", station_name="天津西", arrival_time="08:25:00", departure_time="08:30:00", platform="1"),
                TrainStop(station_code="JNZ", station_name="济南西", arrival_time="09:10:00", departure_time="09:15:00", platform="1"),
                TrainStop(station_code="NJH", station_name="南京南", arrival_time="10:25:00", departure_time="10:30:00", platform="1"),
                TrainStop(station_code="SHH", station_name="上海虹桥", arrival_time="11:30:00", departure_time="11:45:00", platform="1"),
            ]),
            # 减少冗余，增加延误传播
            slack_time=SlackTime(max_station_slack=120, max_section_slack=60, total_slack=180)
        ),
        Train(
            train_id="G1002",
            speed_level=350,
            schedule=TrainSchedule(stops=[
                TrainStop(station_code="BJP", station_name="北京西", arrival_time="08:15:00", departure_time="08:25:00", platform="2"),
                TrainStop(station_code="TJG", station_name="天津西", arrival_time="08:40:00", departure_time="08:45:00", platform="2"),
                TrainStop(station_code="JNZ", station_name="济南西", arrival_time="09:25:00", departure_time="09:30:00", platform="2"),
                TrainStop(station_code="NJH", station_name="南京南", arrival_time="10:40:00", departure_time="10:45:00", platform="2"),
                TrainStop(station_code="SHH", station_name="上海虹桥", arrival_time="11:45:00", departure_time="12:00:00", platform="2"),
            ]),
            slack_time=SlackTime(max_station_slack=120, max_section_slack=60, total_slack=180)
        ),
        Train(
            train_id="G1003",
            speed_level=350,
            schedule=TrainSchedule(stops=[
                TrainStop(station_code="BJP", station_name="北京西", arrival_time="08:30:00", departure_time="08:40:00", platform="3"),
                TrainStop(station_code="TJG", station_name="天津西", arrival_time="08:55:00", departure_time="09:00:00", platform="3"),
                TrainStop(station_code="JNZ", station_name="济南西", arrival_time="09:40:00", departure_time="09:45:00", platform="3"),
                TrainStop(station_code="NJH", station_name="南京南", arrival_time="10:55:00", departure_time="11:00:00", platform="3"),
                TrainStop(station_code="SHH", station_name="上海虹桥", arrival_time="12:00:00", departure_time="12:15:00", platform="3"),
            ]),
            slack_time=SlackTime(max_station_slack=120, max_section_slack=60, total_slack=180)
        ),
        Train(
            train_id="G1004",
            speed_level=350,
            schedule=TrainSchedule(stops=[
                TrainStop(station_code="BJP", station_name="北京西", arrival_time="08:45:00", departure_time="08:55:00", platform="4"),
                TrainStop(station_code="TJG", station_name="天津西", arrival_time="09:10:00", departure_time="09:15:00", platform="4"),
                TrainStop(station_code="JNZ", station_name="济南西", arrival_time="09:55:00", departure_time="10:00:00", platform="4"),
                TrainStop(station_code="NJH", station_name="南京南", arrival_time="11:10:00", departure_time="11:15:00", platform="4"),
                TrainStop(station_code="SHH", station_name="上海虹桥", arrival_time="12:15:00", departure_time="12:30:00", platform="4"),
            ]),
            slack_time=SlackTime(max_station_slack=120, max_section_slack=60, total_slack=180)
        ),
        Train(
            train_id="G1005",
            speed_level=350,
            schedule=TrainSchedule(stops=[
                TrainStop(station_code="BJP", station_name="北京西", arrival_time="09:00:00", departure_time="09:10:00", platform="5"),
                TrainStop(station_code="TJG", station_name="天津西", arrival_time="09:25:00", departure_time="09:30:00", platform="5"),
                TrainStop(station_code="JNZ", station_name="济南西", arrival_time="10:10:00", departure_time="10:15:00", platform="5"),
                TrainStop(station_code="NJH", station_name="南京南", arrival_time="11:25:00", departure_time="11:30:00", platform="5"),
                TrainStop(station_code="SHH", station_name="上海虹桥", arrival_time="12:30:00", departure_time="12:45:00", platform="5"),
            ]),
            slack_time=SlackTime(max_station_slack=120, max_section_slack=60, total_slack=180)
        ),
    ]


def create_sample_stations() -> List[Station]:
    """
    创建示例车站数据
    优先使用真实数据，如果没有则使用示例数据
    """
    # 尝试从data_loader加载真实数据
    try:
        from models.data_loader import get_stations_pydantic, is_using_real_data
        if is_using_real_data():
            return get_stations_pydantic()
    except Exception:
        pass

    # 如果没有启用真实数据或加载失败，使用示例数据
    return [
        Station(
            station_code="BJP",
            station_name="北京西",
            track_count=15,
            platforms=[Platform(platform_id=str(i), track_id=chr(64+i)) for i in range(1, 16)],
            connection_sections=[ConnectionSection(section_id="S1", to_station="TJG", distance_km=115)]
        ),
        Station(
            station_code="TJG",
            station_name="天津西",
            track_count=13,
            platforms=[Platform(platform_id=str(i), track_id=chr(64+i)) for i in range(1, 14)],
            connection_sections=[
                ConnectionSection(section_id="S1", to_station="BJP", distance_km=115),
                ConnectionSection(section_id="S2", to_station="JNZ", distance_km=360)
            ]
        ),
        Station(
            station_code="JNZ",
            station_name="济南西",
            track_count=11,
            platforms=[Platform(platform_id=str(i), track_id=chr(64+i)) for i in range(1, 12)],
            connection_sections=[
                ConnectionSection(section_id="S2", to_station="TJG", distance_km=360),
                ConnectionSection(section_id="S3", to_station="NJH", distance_km=650)
            ]
        ),
        Station(
            station_code="NJH",
            station_name="南京南",
            track_count=15,
            platforms=[Platform(platform_id=str(i), track_id=chr(64+i)) for i in range(1, 16)],
            connection_sections=[
                ConnectionSection(section_id="S3", to_station="JNZ", distance_km=650),
                ConnectionSection(section_id="S4", to_station="SHH", distance_km=295)
            ]
        ),
        Station(
            station_code="SHH",
            station_name="上海虹桥",
            track_count=15,
            platforms=[Platform(platform_id=str(i), track_id=chr(64+i)) for i in range(1, 16)],
            connection_sections=[ConnectionSection(section_id="S4", to_station="NJH", distance_km=295)]
        ),
    ]


def save_sample_data():
    """保存示例数据到JSON文件"""
    trains = create_sample_trains()
    stations = create_sample_stations()

    # 转换为字典并保存
    train_data = [train.model_dump() for train in trains]
    station_data = [station.model_dump() for station in stations]

    with open('/Users/chenshuai18/test_agents/railway_dispatch/data/trains.json', 'w', encoding='utf-8') as f:
        json.dump(train_data, f, ensure_ascii=False, indent=2)

    with open('/Users/chenshuai18/test_agents/railway_dispatch/data/stations.json', 'w', encoding='utf-8') as f:
        json.dump(station_data, f, ensure_ascii=False, indent=2)

    print("示例数据已保存到 data/ 目录")


if __name__ == "__main__":
    save_sample_data()
