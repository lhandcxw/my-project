"""
铁路运行图生成器
横轴：时间（分钟）
纵轴：车站序列（从下到上）
"""

# 正确的车站顺序（从下到上）
STATION_ORDER = [
    'BJX',  # 北京西
    'DJK',  # 杜家坎线路所
    'ZBD',  # 涿州东
    'GBD',  # 高碑店东
    'XSD',  # 徐水东
    'BDD',  # 保定东
    'DZD',  # 定州东
    'ZDJ',  # 正定机场
    'SJP',  # 石家庄
    'GYX',  # 高邑西
    'XTD',  # 邢台东
    'HDD',  # 邯郸东
    'AYD'   # 安阳东
]

import matplotlib
matplotlib.use('Agg')  # 非交互式后端
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from typing import List, Dict, Tuple
import base64
import io
import logging

# 配置日志
logger = logging.getLogger(__name__)


def time_to_minutes(time_str: str) -> int:
    """
    将时间字符串转换为从0点开始的分钟数
    例如: '6:10' -> 370, '6:20' -> 380
    """
    parts = time_str.split(':')
    hours = int(parts[0])
    minutes = int(parts[1])
    return hours * 60 + minutes


def create_train_diagram(trains: List[Dict], output_path: str = None, return_base64: bool = False):
    """
    生成铁路运行图

    参数:
        trains: 列车数据列表
        output_path: 输出路径（可选）
        return_base64: 是否返回base64编码的图片
    返回:
        如果return_base64=True，返回base64编码的图片字符串
    """

    # ========== 1. 使用固定车站顺序（从BJX开始，从下到上） ==========
    # 车站顺序：BJX在最下面，AYD在最上面
    station_codes = ["BJX", "DJK", "ZBD", "GBD", "XSD", "BDD", "DZD", "ZDJ", "SJP", "GYX", "XTD", "HDD", "AYD"]

<<<<<<< HEAD
    # 按预定义顺序排列（从下到上是BJX, DJK, ZBD, GBD, XSD, BDD, DZD, ZDJ, SJP, GYX, XTD, HDD, AYD）
    # 只保留实际出现的车站，但按正确顺序排列
    station_codes = [code for code in STATION_ORDER if code in station_codes]
    print(f"车站列表（已排序）: {station_codes}")
=======
    logger.debug(f"车站列表: {station_codes}")
>>>>>>> 860e9e3d031984e9febd5d412e3f4e6a48ca0914

    # ========== 2. 确定时间范围 ==========
    all_times = []
    for train in trains:
        for stop in train['schedule']['stops']:
            all_times.append(time_to_minutes(stop['arrival_time']))
            all_times.append(time_to_minutes(stop['departure_time']))

    time_min = min(all_times) - 10  # 留出边距
    time_max = max(all_times) + 10

    # 时间刻度（每10分钟）
    time_ticks = list(range((time_min // 10) * 10, (time_max // 10 + 1) * 10, 10))

    # ========== 3. 创建图形 ==========
    fig, ax = plt.subplots(figsize=(16, 10))

    # ========== 4. 绘制网格 ==========
    # 车站网格线（水平线）
    for i in range(len(station_codes)):
        ax.axhline(y=i, color='#D3D3D3', linestyle='--', linewidth=0.8, zorder=1)

    # 时间网格线（垂直线）
    for t in time_ticks:
        ax.axvline(x=t, color='#D3D3D3', linestyle='--', linewidth=0.8, zorder=1)

    # ========== 5. 绘制运行线和停站 ==========
    # 颜色映射
    train_colors = plt.cm.Set1(np.linspace(0, 1, len(trains)))

    for idx, train in enumerate(trains):
        train_id = train['train_id']
        stops = train['schedule']['stops']

        # 收集运行线的点
        x_points = []
        y_points = []

        for stop in stops:
            station = stop['station_code']
            # 如果车站不在固定列表中，跳过
            if station not in station_codes:
                continue
            station_idx = station_codes.index(station)

            arrival_time = time_to_minutes(stop['arrival_time'])
            departure_time = time_to_minutes(stop['departure_time'])

            # 添加停站矩形（蓝色半透明）
            rect = mpatches.Rectangle(
                (arrival_time, station_idx - 0.3),  # 左下角
                departure_time - arrival_time,      # 宽度
                0.6,                                 # 高度
                linewidth=1,
                edgecolor='#4169E1',
                facecolor='#4169E1',
                alpha=0.4,  # 半透明
                zorder=2
            )
            ax.add_patch(rect)

            # 运行线点
            x_points.append(arrival_time)
            y_points.append(station_idx)
            x_points.append(departure_time)
            y_points.append(station_idx)

        # 绘制运行线（红色，2px宽）
        if x_points and y_points:
            ax.plot(x_points, y_points, color='red', linewidth=2, marker='o',
                    markersize=6, markerfacecolor='red', markeredgecolor='white',
                    markeredgewidth=1.5, zorder=3, label=train_id)

            # 添加车次标签（在第一条线旁边）
            if len(x_points) >= 2:
                # 找到中间位置
                mid_idx = len(x_points) // 2
                label_x = (x_points[mid_idx] + x_points[mid_idx + 1]) / 2 if mid_idx + 1 < len(x_points) else x_points[0]
                label_y = (y_points[mid_idx] + y_points[mid_idx + 1]) / 2 if mid_idx + 1 < len(y_points) else y_points[0]

                ax.annotate(train_id, xy=(label_x, label_y),
                           xytext=(label_x + 3, label_y + 0.15),
                           fontsize=10, fontweight='bold', color='darkred',
                           zorder=4)

    # ========== 6. 添加标注（箭头） ==========
    # 运行时间标注
    if len(trains) > 0 and len(trains[0]['schedule']['stops']) >= 2:
        # 找到第一个区间的运行时间进行标注
        first_train = trains[0]
        stop1 = first_train['schedule']['stops'][0]
        stop2 = first_train['schedule']['stops'][1]

        if stop1['station_code'] in station_codes and stop2['station_code'] in station_codes:
            station1_idx = station_codes.index(stop1['station_code'])
            station2_idx = station_codes.index(stop2['station_code'])
            time1 = time_to_minutes(stop1['departure_time'])
            time2 = time_to_minutes(stop2['arrival_time'])

            # 运行时间箭头（在对角线方向）
            mid_x = (time1 + time2) / 2
            mid_y = (station1_idx + station2_idx) / 2

            # 绘制运行时间标注
            ax.annotate('Running Time',
                       xy=(mid_x, mid_y),
                       xytext=(mid_x + 15, mid_y + 0.8),
                       fontsize=9,
                       color='black',
                       arrowprops=dict(arrowstyle='->', color='black', lw=1.5),
                       zorder=5)

    # 停站时间标注
    if len(trains) > 0:
        first_train = trains[0]
        first_stop = first_train['schedule']['stops'][0]

        if first_stop['station_code'] in station_codes:
            station_idx = station_codes.index(first_stop['station_code'])
            arrival = time_to_minutes(first_stop['arrival_time'])
            departure = time_to_minutes(first_stop['departure_time'])

            mid_x = (arrival + departure) / 2

            # 绘制停站时间标注
            ax.annotate('Stop Time',
                       xy=(mid_x, station_idx),
                       xytext=(mid_x, station_idx - 0.8),
                       fontsize=9,
                       color='black',
                       arrowprops=dict(arrowstyle='->', color='black', lw=1.5),
                       ha='center',
                       zorder=5)

    # ========== 7. 设置坐标轴 ==========
    # X轴 - 时间
    ax.set_xlim(time_min, time_max)
    ax.set_xticks(time_ticks)

    # 时间标签格式化 (英文)
    time_labels = [f"{t // 60}:{t % 60:02d}" for t in time_ticks]
    ax.set_xticklabels(time_labels, rotation=45, ha='right', fontsize=10)
    ax.set_xlabel('Time', fontsize=12, fontweight='bold')

    # Y轴 - 车站（从下到上：BJX在最下面，AYD在最上面）
    ax.set_ylim(-0.5, len(station_codes) - 0.5)
    ax.set_yticks(range(len(station_codes)))
    ax.set_yticklabels(station_codes, fontsize=12, fontweight='bold')
    ax.set_ylabel('Station', fontsize=12, fontweight='bold')

    # ========== 8. 添加图例 ==========
    legend_patches = [mpatches.Patch(color='red', label='Train Route'),
                     mpatches.Patch(color='#4169E1', alpha=0.4, label='Stop Time')]
    ax.legend(handles=legend_patches, loc='upper right', fontsize=10)

    # ========== 9. 标题 ==========
    ax.set_title('Railway Train Diagram', fontsize=16, fontweight='bold', pad=20)

    # ========== 10. 调整布局 ==========
    plt.tight_layout()

    # ========== 11. 保存或返回 ==========
    if return_base64:
        # 返回base64编码的图片
        buffer = io.BytesIO()
        plt.savefig(buffer, format='png', dpi=120, bbox_inches='tight', facecolor='white')
        buffer.seek(0)
        img_base64 = base64.b64encode(buffer.read()).decode('utf-8')
        plt.close()
        return img_base64
    elif output_path:
        plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
        logger.info(f"运行图已保存至: {output_path}")
        plt.close()
    else:
        plt.show()
        plt.close()


def create_comparison_diagram(original_trains: List[Dict], optimized_trains: List[Dict],
                               title: str = "Railway Train Diagram") -> str:
    """
    生成对比运行图（原始 vs 优化后）
    返回base64编码的图片
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 10))

    # 绘制原始运行图
    _draw_single_diagram(ax1, original_trains, f"{title} - Original Schedule")

    # 绘制优化后运行图
    _draw_single_diagram(ax2, optimized_trains, f"{title} - Optimized Schedule")

    plt.tight_layout()

    # 返回base64编码
    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', dpi=120, bbox_inches='tight', facecolor='white')
    buffer.seek(0)
    img_base64 = base64.b64encode(buffer.read()).decode('utf-8')
    plt.close()

    return img_base64


def _draw_single_diagram(ax, trains: List[Dict], title: str):
    """绘制单幅运行图（内部函数）"""
<<<<<<< HEAD
    # ========== 1. 提取车站列表 ==========
    station_codes = []
    for train in trains:
        for stop in train['schedule']['stops']:
            if stop['station_code'] not in station_codes:
                station_codes.append(stop['station_code'])
                
    # 按预定义顺序排列（从下到上是BJX, DJK, ZBD, GBD, XSD, BDD, DZD, ZDJ, SJP, GYX, XTD, HDD, AYD）
    # 只保留实际出现的车站，但按正确顺序排列
    station_codes = [code for code in STATION_ORDER if code in station_codes]
=======
    # ========== 1. 使用固定车站顺序（从BJX开始，从下到上） ==========
    # 车站顺序：BJX在最下面，AYD在最上面
    station_codes = ["BJX", "DJK", "ZBD", "GBD", "XSD", "BDD", "DZD", "ZDJ", "SJP", "GYX", "XTD", "HDD", "AYD"]
>>>>>>> 860e9e3d031984e9febd5d412e3f4e6a48ca0914

    # ========== 2. 确定时间范围 ==========
    all_times = []
    for train in trains:
        for stop in train['schedule']['stops']:
            all_times.append(time_to_minutes(stop['arrival_time']))
            all_times.append(time_to_minutes(stop['departure_time']))

    if not all_times:
        all_times = [6 * 60, 12 * 60]

    time_min = min(all_times) - 10
    time_max = max(all_times) + 10
    time_ticks = list(range((time_min // 10) * 10, (time_max // 10 + 1) * 10, 10))

    # ========== 3. 绘制网格 ==========
    for i in range(len(station_codes)):
        ax.axhline(y=i, color='#D3D3D3', linestyle='--', linewidth=0.8, zorder=1)

    for t in time_ticks:
        ax.axvline(x=t, color='#D3D3D3', linestyle='--', linewidth=0.8, zorder=1)

    # ========== 4. 绘制运行线和停站 ==========
    colors = ['#E91E63', '#9C27B0', '#3F51B5', '#00BCD4', '#4CAF50', '#FF9800']

    for idx, train in enumerate(trains):
        train_id = train['train_id']
        stops = train['schedule']['stops']
        color = colors[idx % len(colors)]

        x_points = []
        y_points = []

        for stop in stops:
            station = stop['station_code']
            # 如果车站不在固定列表中，跳过
            if station not in station_codes:
                continue
            station_idx = station_codes.index(station)

            arrival_time = time_to_minutes(stop['arrival_time'])
            departure_time = time_to_minutes(stop['departure_time'])

            # 停站矩形（蓝色半透明）
            rect = mpatches.Rectangle(
                (arrival_time, station_idx - 0.3),
                departure_time - arrival_time,
                0.6,
                linewidth=1,
                edgecolor='#4169E1',
                facecolor='#4169E1',
                alpha=0.4,
                zorder=2
            )
            ax.add_patch(rect)

            # 运行线点
            x_points.append(arrival_time)
            y_points.append(station_idx)
            x_points.append(departure_time)
            y_points.append(station_idx)

        # 绘制运行线（红色，2px宽）
        if x_points and y_points:
            ax.plot(x_points, y_points, color=color, linewidth=2, marker='o',
                    markersize=6, markerfacecolor=color, markeredgecolor='white',
                    markeredgewidth=1.5, zorder=3, label=train_id)

            # 添加车次标签
            if len(x_points) >= 2:
                mid_idx = len(x_points) // 2
                label_x = (x_points[mid_idx] + x_points[mid_idx + 1]) / 2 if mid_idx + 1 < len(x_points) else x_points[0]
                label_y = (y_points[mid_idx] + y_points[mid_idx + 1]) / 2 if mid_idx + 1 < len(y_points) else y_points[0]

                ax.annotate(train_id, xy=(label_x, label_y),
                           xytext=(label_x + 3, label_y + 0.15),
                           fontsize=10, fontweight='bold', color='darkred',
                           zorder=4)

    # ========== 5. 设置坐标轴 ==========
    ax.set_xlim(time_min, time_max)
    ax.set_xticks(time_ticks)
    time_labels = [f"{t // 60}:{t % 60:02d}" for t in time_ticks]
    ax.set_xticklabels(time_labels, rotation=45, ha='right', fontsize=10)
    ax.set_xlabel('Time', fontsize=12, fontweight='bold')

    # 纵轴：从下到上，BJX在最下面（index=0），AYD在最上面（index=12）
    ax.set_ylim(-0.5, len(station_codes) - 0.5)
    ax.set_yticks(range(len(station_codes)))
    ax.set_yticklabels(station_codes, fontsize=12, fontweight='bold')
    ax.set_ylabel('Station', fontsize=12, fontweight='bold')

    ax.set_title(title, fontsize=14, fontweight='bold', pad=10)
    ax.legend(loc='upper right', fontsize=10)
    ax.set_facecolor('#fafafa')


# ========== 示例数据 ==========
if __name__ == "__main__":
    # 示例列车数据
    sample_trains = [
        {
            "train_id": "G1001",
            "schedule": {
                "stops": [
                    {
                        "station_code": "A",
                        "station_name": "北京西",
                        "arrival_time": "6:10",
                        "departure_time": "6:15"
                    },
                    {
                        "station_code": "B",
                        "station_name": "天津南",
                        "arrival_time": "6:35",
                        "departure_time": "6:38"
                    },
                    {
                        "station_code": "C",
                        "station_name": "济南西",
                        "arrival_time": "7:15",
                        "departure_time": "7:18"
                    },
                    {
                        "station_code": "D",
                        "station_name": "南京南",
                        "arrival_time": "8:05",
                        "departure_time": "8:10"
                    }
                ]
            }
        },
        {
            "train_id": "G1002",
            "schedule": {
                "stops": [
                    {
                        "station_code": "A",
                        "station_name": "北京西",
                        "arrival_time": "6:20",
                        "departure_time": "6:25"
                    },
                    {
                        "station_code": "B",
                        "station_name": "天津南",
                        "arrival_time": "6:45",
                        "departure_time": "6:48"
                    },
                    {
                        "station_code": "C",
                        "station_name": "济南西",
                        "arrival_time": "7:25",
                        "departure_time": "7:28"
                    },
                    {
                        "station_code": "D",
                        "station_name": "南京南",
                        "arrival_time": "8:15",
                        "departure_time": "8:20"
                    }
                ]
            }
        },
        {
            "train_id": "G1003",
            "schedule": {
                "stops": [
                    {
                        "station_code": "B",
                        "station_name": "天津南",
                        "arrival_time": "6:50",
                        "departure_time": "6:55"
                    },
                    {
                        "station_code": "C",
                        "station_name": "济南西",
                        "arrival_time": "7:35",
                        "departure_time": "7:38"
                    },
                    {
                        "station_code": "D",
                        "station_name": "南京南",
                        "arrival_time": "8:25",
                        "departure_time": "8:30"
                    }
                ]
            }
        }
    ]

    # 生成运行图
    create_train_diagram(sample_trains, 'railway_diagram.png')
