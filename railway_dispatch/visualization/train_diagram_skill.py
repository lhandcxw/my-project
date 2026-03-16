# -*- coding: utf-8 -*-
"""
铁路调度系统 - 运行图绘制Skills模块
经典铁路运行图风格：时间-空间网格 + 红色运行线 + 蓝色标注
"""

from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import base64
import io

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch, ArrowStyle
import numpy as np

# 设置字体（英文/拼音为主，避免中文显示问题）
plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['axes.unicode_minus'] = False

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class VisualizationInput:
    """可视化输入参数"""
    original_schedule: Dict[str, List[Dict]]
    optimized_schedule: Dict[str, List[Dict]]
    station_codes: List[str]
    station_names: Dict[str, str]
    title: str = "列车运行图"


@dataclass
class VisualizationOutput:
    """可视化输出结果"""
    success: bool
    image_base64: str = ""
    message: str = ""
    comparison_stats: Dict[str, Any] = None


class TrainDiagramSkill:
    """
    经典铁路运行图绘制
    - 垂直时间轴（时间从早到晚）
    - 水平空间轴（车站从左到右）
    - 红色斜线表示列车运行线
    - 蓝色箭头标注时间含义
    """

    def __init__(self):
        self.figure_size = (18, 12)
        # 列车颜色
        self.colors = ['#E91E63', '#9C27B0', '#3F51B5', '#00BCD4', '#4CAF50', '#FF9800', '#F44336']

    def _time_to_minutes(self, time_str: str) -> float:
        """时间字符串转分钟数"""
        h, m, s = map(int, time_str.split(':'))
        return h * 60 + m + s / 60

    def _minutes_to_time(self, minutes: float) -> str:
        """分钟数转时间字符串"""
        h = int(minutes // 60)
        m = int(minutes % 60)
        return f"{h:02d}:{m:02d}"

    def execute(
        self,
        original_schedule: Dict[str, List[Dict]],
        optimized_schedule: Dict[str, List[Dict]],
        station_codes: List[str],
        station_names: Dict[str, str],
        title: str = "铁路列车运行图"
    ) -> VisualizationOutput:
        """执行运行图绘制"""
        try:
            # 创建两个子图：原始 vs 优化后
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=self.figure_size)

            # 绘制原始运行图
            self._draw_train_diagram(
                ax1, original_schedule, station_codes, station_names,
                f"{title} - 原始时刻表"
            )

            # 绘制优化后运行图
            self._draw_train_diagram(
                ax2, optimized_schedule, station_codes, station_names,
                f"{title} - 优化后时刻表"
            )

            plt.tight_layout()

            # 转换为Base64
            buffer = io.BytesIO()
            plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight', facecolor='white')
            buffer.seek(0)
            image_base64 = base64.b64encode(buffer.read()).decode('utf-8')
            plt.close(fig)

            # 计算统计
            stats = self._create_comparison_stats(original_schedule, optimized_schedule)

            return VisualizationOutput(
                success=True,
                image_base64=image_base64,
                message="运行图绘制成功",
                comparison_stats=stats
            )

        except Exception as e:
            return VisualizationOutput(
                success=False,
                message=f"运行图绘制失败: {str(e)}"
            )

    def _draw_train_diagram(
        self,
        ax,
        schedule: Dict[str, List[Dict]],
        station_codes: List[str],
        station_names: Dict[str, str],
        title: str
    ):
        """绘制单幅运行图"""
        # 计算时间范围
        all_times = []
        for train_id, stops in schedule.items():
            for stop in stops:
                all_times.append(self._time_to_minutes(stop["arrival_time"]))
                all_times.append(self._time_to_minutes(stop["departure_time"]))

        if not all_times:
            all_times = [8*60, 13*60]  # 默认时间范围

        time_min = int(min(all_times) // 10) * 10 - 20  # 提前20分钟
        time_max = int(max(all_times) // 10) * 10 + 20  # 延后20分钟

        # 设置坐标轴
        # X轴：车站（0, 1, 2, 3, 4...）
        # Y轴：时间（分钟，从晚到早，所以反转Y轴）
        ax.set_xlim(-0.5, len(station_codes) - 0.5)
        ax.set_ylim(time_max, time_min)

        # 设置X轴标签（车站 - 使用拼音避免中文显示问题）
        station_labels = []
        for code in station_codes:
            name = station_names.get(code, code)
            # 中文转拼音映射
            pinyin_map = {
                "北京西": "BJP", "天津西": "TJG", "济南西": "JNZ",
                "南京南": "NJH", "上海虹桥": "SHH"
            }
            station_labels.append(pinyin_map.get(name, code))
        ax.set_xticks(range(len(station_codes)))
        ax.set_xticklabels(station_labels, fontsize=11)

        # 设置Y轴标签（时间）
        y_ticks = range(int(time_min // 10) * 10, int(time_max // 10) * 10 + 1, 10)
        ax.set_yticks(list(y_ticks))
        ax.set_yticklabels([self._minutes_to_time(t) for t in y_ticks], fontsize=10)

        # 绘制网格（方格）
        for t in range(int(time_min // 10) * 10, int(time_max // 10) * 10 + 1, 10):
            ax.axhline(y=t, color='#e0e0e0', linestyle='-', linewidth=0.5, alpha=0.7)
        for i in range(len(station_codes) + 1):
            ax.axvline(x=i - 0.5, color='#e0e0e0', linestyle='-', linewidth=0.5, alpha=0.7)

        # 绘制车站横线（加粗）
        for i in range(len(station_codes)):
            ax.axhline(y=time_min, xmin=i/len(station_codes), xmax=(i+1)/len(station_codes),
                      color='#333333', linestyle='-', linewidth=2)

        # 标题
        ax.set_title(title, fontsize=14, fontweight='bold', pad=10)
        ax.set_xlabel('车站', fontsize=12)
        ax.set_ylabel('时间', fontsize=12)

        # 绘制列车运行线
        train_idx = 0
        for train_id, stops in schedule.items():
            color = self.colors[train_idx % len(self.colors)]
            train_idx += 1

            # 获取该列车经过的车站及其时间
            points = []
            for stop in stops:
                station_idx = station_codes.index(stop["station_code"])
                arr_time = self._time_to_minutes(stop["arrival_time"])
                dep_time = self._time_to_minutes(stop["departure_time"])

                # 到达点
                points.append((station_idx, arr_time))
                # 发车点
                points.append((station_idx, dep_time))

            # 绘制运行线（红色斜线）
            if len(points) >= 2:
                for i in range(len(points) - 1):
                    x1, y1 = points[i]
                    x2, y2 = points[i + 1]

                    if x1 == x2:  # 同一车站（停站）
                        # 水平线段表示停站
                        ax.plot([x1 - 0.03, x2 + 0.03], [y1, y2],
                               color=color, linewidth=2.5, alpha=0.8)
                    else:  # 不同车站（运行）
                        # 斜线表示运行
                        ax.plot([x1, x2], [y1, y2],
                               color=color, linewidth=2.5, alpha=0.8,
                               marker='o', markersize=6, markerfacecolor=color)

            # 标注列车车次
            if points:
                x, y = points[0]
                ax.annotate(train_id, xy=(x, y), xytext=(x - 0.3, y + 5),
                           fontsize=9, color=color, fontweight='bold',
                           alpha=0.9)

            # 标注延误（红色文字）
            for stop in stops:
                if stop.get("delay_seconds", 0) > 0:
                    station_idx = station_codes.index(stop["station_code"])
                    dep_time = self._time_to_minutes(stop["departure_time"])
                    delay_min = stop["delay_seconds"] / 60
                    ax.annotate(f"+{int(delay_min)}min",
                               xy=(station_idx, dep_time),
                               xytext=(station_idx + 0.15, dep_time - 3),
                               fontsize=8, color='red', alpha=0.8)

        # 添加图例
        legend_elements = [
            plt.Line2D([0], [0], color=self.colors[i % len(self.colors)], linewidth=2, label=f'G{1001+i}')
            for i in range(min(len(schedule), 5))
        ]
        ax.legend(handles=legend_elements, loc='upper right', fontsize=8)

        # 设置背景色
        ax.set_facecolor('#fafafa')

    def _create_comparison_stats(
        self,
        original_schedule: Dict[str, List[Dict]],
        optimized_schedule: Dict[str, List[Dict]]
    ) -> Dict[str, Any]:
        """创建对比统计"""
        def calc_stats(schedule):
            delays = []
            for train_id, stops in schedule.items():
                for stop in stops:
                    d = stop.get("delay_seconds", 0)
                    if d > 0:
                        delays.append(d)
            if not delays:
                return {"max": 0, "avg": 0, "total": 0}
            return {
                "max": max(delays),
                "avg": sum(delays) / len(delays),
                "total": sum(delays)
            }

        return {
            "original": calc_stats(original_schedule),
            "optimized": calc_stats(optimized_schedule)
        }


# HTML版本运行图（同样风格）
class HTMLTrainDiagramSkill:
    """HTML版本的经典铁路运行图"""

    def __init__(self):
        pass

    def _generate_html(
        self,
        original_schedule: Dict[str, List[Dict]],
        optimized_schedule: Dict[str, List[Dict]],
        station_codes: List[str],
        station_names: Dict[str, str]
    ) -> str:
        """生成HTML运行图"""

        # 计算时间范围
        all_times = []
        for schedule in [original_schedule, optimized_schedule]:
            for train_id, stops in schedule.items():
                for stop in stops:
                    all_times.append(self._time_to_minutes(stop["arrival_time"]))
                    all_times.append(self._time_to_minutes(stop["departure_time"]))

        time_min = int(min(all_times) // 10) * 10 - 20
        time_max = int(max(all_times) // 10) * 10 + 20

        def time_to_pct(time_str):
            minutes = self._time_to_minutes(time_str)
            return (minutes - time_min) / (time_max - time_min) * 100

        def station_to_pct(idx):
            return idx / (len(station_codes) - 1) * 100

        colors = ['#E91E63', '#9C27B0', '#3F51B5', '#00BCD4', '#4CAF50']

        html = f'''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>铁路运行图</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h2 {{ text-align: center; color: #333; }}
        .diagrams {{ display: flex; gap: 20px; justify-content: center; flex-wrap: wrap; }}
        .diagram-box {{ background: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        .diagram-title {{ text-align: center; font-weight: bold; margin-bottom: 10px; color: #555; }}
        
        /* 运行图样式 */
        .train-diagram {{
            position: relative;
            width: 700px;
            height: 500px;
            border: 2px solid #333;
            background: #fafafa;
            overflow: hidden;
        }}
        
        /* 车站轴（水平） */
        .station-axis {{
            position: absolute;
            left: 0;
            right: 0;
            bottom: 30px;
            height: 20px;
            display: flex;
            justify-content: space-between;
            padding: 0 10px;
        }}
        .station-label {{
            font-weight: bold;
            font-size: 12px;
            color: #333;
            text-align: center;
            width: 80px;
        }}
        
        /* 时间轴（垂直） */
        .time-axis {{
            position: absolute;
            left: 30px;
            top: 10px;
            bottom: 60px;
            width: 30px;
        }}
        .time-label {{
            position: absolute;
            font-size: 10px;
            color: #666;
            transform: translateY(-50%);
        }}
        
        /* 网格 */
        .grid-v-lines {{
            position: absolute;
            left: 40px;
            right: 10px;
            top: 10px;
            bottom: 60px;
        }}
        .grid-v-line {{
            position: absolute;
            width: 1px;
            background: #e0e0e0;
            top: 0;
            bottom: 0;
        }}
        .grid-h-lines {{
            position: absolute;
            left: 40px;
            right: 10px;
            top: 10px;
            bottom: 60px;
        }}
        .grid-h-line {{
            position: absolute;
            height: 1px;
            background: #e0e0e0;
            left: 0;
            right: 0;
        }}
        
        /* 车站横线 */
        .station-lines {{
            position: absolute;
            left: 40px;
            right: 10px;
            bottom: 60px;
        }}
        .station-line {{
            position: absolute;
            height: 2px;
            background: #333;
            left: 0;
            right: 0;
        }}
        
        /* 绘图区域 */
        .plot-area {{
            position: absolute;
            left: 40px;
            right: 10px;
            top: 10px;
            bottom: 60px;
        }}
        
        /* 运行线 */
        .train-line {{
            position: absolute;
            height: 3px;
            transform-origin: left center;
        }}
        
        /* 列车标签 */
        .train-label {{
            position: absolute;
            font-size: 10px;
            font-weight: bold;
            white-space: nowrap;
        }}
        
        /* 延误标签 */
        .delay-label {{
            position: absolute;
            font-size: 9px;
            color: red;
            white-space: nowrap;
        }}
        
        /* 图例 */
        .legend {{
            display: flex;
            justify-content: center;
            gap: 15px;
            margin-top: 15px;
            flex-wrap: wrap;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 5px;
            font-size: 12px;
        }}
        .legend-color {{
            width: 20px;
            height: 3px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h2>铁路列车运行图</h2>
        
        <div class="diagrams">
            <!-- 原始运行图 -->
            <div class="diagram-box">
                <div class="diagram-title">原始时刻表</div>
                {self._generate_diagram_html(original_schedule, station_codes, station_names, time_min, time_max, time_to_pct, station_to_pct, colors)}
            </div>
            
            <!-- 优化后运行图 -->
            <div class="diagram-box">
                <div class="diagram-title">优化后时刻表</div>
                {self._generate_diagram_html(optimized_schedule, station_codes, station_names, time_min, time_max, time_to_pct, station_to_pct, colors)}
            </div>
        </div>
        
        <div class="legend">
            <div class="legend-item"><div class="legend-color" style="background:#E91E63"></div>G1001</div>
            <div class="legend-item"><div class="legend-color" style="background:#9C27B0"></div>G1002</div>
            <div class="legend-item"><div class="legend-color" style="background:#3F51B5"></div>G1003</div>
            <div class="legend-item"><div class="legend-color" style="background:#00BCD4"></div>G1004</div>
            <div class="legend-item"><div class="legend-color" style="background:#4CAF50"></div>G1005</div>
        </div>
    </div>
</body>
</html>
'''
        return html

    def _generate_diagram_html(
        self,
        schedule: Dict[str, List[Dict]],
        station_codes: List[str],
        station_names: Dict[str, str],
        time_min: float,
        time_max: float,
        time_to_pct_func,
        station_to_pct_func,
        colors: List[str]
    ) -> str:
        """生成单个运行图HTML"""

        # 时间刻度
        time_ticks = list(range(int(time_min // 10) * 10, int(time_max // 10) * 10 + 1, 10))

        # 垂直刻度线HTML
        v_lines = ""
        for i, t in enumerate(time_ticks):
            pct = (t - time_min) / (time_max - time_min) * 100
            v_lines += f'<div class="grid-v-line" style="left:{pct}%;"></div>'

        # 水平刻度线HTML
        h_lines = ""
        for i in range(len(station_codes)):
            pct = i / (len(station_codes) - 1) * 100
            h_lines += f'<div class="grid-h-line" style="top:{pct}%;"></div>'

        # 车站横线HTML
        station_lines = ""
        for i in range(len(station_codes)):
            pct = i / (len(station_codes) - 1) * 100
            station_lines += f'<div class="station-line" style="top:{pct}%;"></div>'

        # 车站标签HTML
        station_labels = ""
        for i, code in enumerate(station_codes):
            pct = i / (len(station_codes) - 1) * 100
            station_labels += f'<div class="station-label" style="left:{pct}%;">{station_names.get(code, code)}</div>'

        # 时间标签HTML
        time_labels = ""
        for t in time_ticks:
            pct = (t - time_min) / (time_max - time_min) * 100
            time_labels += f'<div class="time-label" style="top:{100-pct}%;">{self._minutes_to_time(t)}</div>'

        # 绘制运行线
        train_lines = ""
        train_idx = 0
        for train_id, stops in schedule.items():
            color = colors[train_idx % len(colors)]
            train_idx += 1

            # 收集时间点
            points = []
            for stop in stops:
                if stop["station_code"] in station_codes:
                    station_idx = station_codes.index(stop["station_code"])
                    arr_pct = time_to_pct_func(stop["arrival_time"])
                    dep_pct = time_to_pct_func(stop["departure_time"])
                    points.append((station_idx, arr_pct, dep_pct))

            # 绘制运行线
            for i, (station_idx, arr_pct, dep_pct) in enumerate(points):
                # 绘制车站点
                train_lines += f'<div class="train-line" style="left:{station_to_pct_func(station_idx)}%; top:{100-arr_pct}%; width:6px; height:6px; border-radius:50%; background:{color};"></div>'

                # 如果有下一个点，绘制连接线
                if i < len(points) - 1:
                    next_station_idx, next_arr_pct, _ = points[i + 1]

                    # 计算斜线的角度和长度
                    dx = (next_station_idx - station_idx) / (len(station_codes) - 1) * 100
                    dy = arr_pct - next_arr_pct
                    angle = np.degrees(np.arctan2(dy, dx)) if dx > 0 else 0
                    length = np.sqrt(dx**2 + dy**2) * 7  # 缩放因子

                    train_lines += f'''<div class="train-line" style="
                        left:{station_to_pct_func(station_idx)}%;
                        top:{100-dep_pct}%;
                        width:{length}%;
                        background:{color};
                        transform:rotate({angle}deg);
                        transform-origin: left center;
                    "></div>'''

                # 标注延误
                if stops[i].get("delay_seconds", 0) > 0:
                    delay = int(stops[i]["delay_seconds"] / 60)
                    train_lines += f'<div class="delay-label" style="left:{station_to_pct_func(station_idx) + 3}%; top:{100-dep_pct}%;">+{delay}min</div>'

            # 标注车次
            if points:
                x = station_to_pct_func(points[0][0])
                y = 100 - points[0][2] + 2
                train_lines += f'<div class="train-label" style="left:{x}%; top:{y}%; color:{color};">{train_id}</div>'

        return f'''
        <div class="train-diagram">
            <div class="grid-v-lines">{v_lines}</div>
            <div class="grid-h-lines">{h_lines}</div>
            <div class="station-lines">{station_lines}</div>
            <div class="time-axis">{time_labels}</div>
            <div class="station-axis">{station_labels}</div>
            <div class="plot-area">{train_lines}</div>
        </div>
        '''

    def _time_to_minutes(self, time_str: str) -> float:
        h, m, s = map(int, time_str.split(':'))
        return h * 60 + m + s / 60

    def _minutes_to_time(self, minutes: float) -> str:
        h = int(minutes // 60)
        m = int(minutes % 60)
        return f"{h:02d}:{m:02d}"

    def execute(
        self,
        original_schedule: Dict[str, List[Dict]],
        optimized_schedule: Dict[str, List[Dict]],
        station_codes: List[str],
        station_names: Dict[str, str],
        title: str = "铁路运行图"
    ) -> str:
        return self._generate_html(original_schedule, optimized_schedule, station_codes, station_names)


# 测试
if __name__ == "__main__":
    original = {
        "G1001": [
            {"station_code": "BJP", "arrival_time": "08:00:00", "departure_time": "08:10:00", "delay_seconds": 0},
            {"station_code": "TJG", "arrival_time": "08:25:00", "departure_time": "08:30:00", "delay_seconds": 600},
            {"station_code": "JNZ", "arrival_time": "09:10:00", "departure_time": "09:15:00", "delay_seconds": 600},
            {"station_code": "NJH", "arrival_time": "10:25:00", "departure_time": "10:30:00", "delay_seconds": 300},
            {"station_code": "SHH", "arrival_time": "11:30:00", "departure_time": "11:45:00", "delay_seconds": 0},
        ]
    }

    stations = ["BJP", "TJG", "JNZ", "NJH", "SHH"]
    names = {"BJP": "北京西", "TJG": "天津西", "JNZ": "济南西", "NJH": "南京南", "SHH": "上海虹桥"}

    skill = TrainDiagramSkill()
    result = skill.execute(original, original, stations, names)

    print(f"成功: {result.success}")
    print(f"消息: {result.message}")
