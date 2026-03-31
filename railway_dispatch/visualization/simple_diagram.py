"""
铁路运行图生成器
横轴：时间（分钟）
纵轴：车站序列（从下到上）
支持100+列车的美观显示
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


# 定义丰富的颜色调色板（支持更多列车）
TRAIN_COLORS = [
    '#E91E63', '#9C27B0', '#3F51B5', '#00BCD4', '#4CAF50',
    '#FF9800', '#F44336', '#673AB7', '#009688', '#795548',
    '#607D8B', '#FF5722', '#8BC34A', '#03A9F4', '#FFC107',
    '#8E24AA', '#1E88E5', '#00ACC1', '#43A047', '#FB8C00',
    '#D81B60', '#5E35B1', '#3949AB', '#039BE5', '#00897B',
    '#7CB342', '#FDD835', '#FFB300', '#F4511E', '#6D4C41'
]


def create_train_diagram(trains: List[Dict], output_path: str = None, return_base64: bool = False):
    """
    生成铁路运行图（优化版，支持100+列车）

    参数:
        trains: 列车数据列表
        output_path: 输出路径（可选）
        return_base64: 是否返回base64编码的图片
    返回:
        如果return_base64=True，返回base64编码的图片字符串
    """

    # ========== 1. 使用固定车站顺序（从BJX开始，从下到上） ==========
    station_codes = ["BJX", "DJK", "ZBD", "GBD", "XSD", "BDD", "DZD", "ZDJ", "SJP", "GYX", "XTD", "HDD", "AYD"]

    logger.debug(f"车站列表: {station_codes}")

    # ========== 2. 确定时间范围 ==========
    all_times = []
    for train in trains:
        for stop in train['schedule']['stops']:
            all_times.append(time_to_minutes(stop['arrival_time']))
            all_times.append(time_to_minutes(stop['departure_time']))

    time_min = min(all_times) - 10
    time_max = max(all_times) + 10

    # 时间刻度（每10分钟）
    time_ticks = list(range((time_min // 10) * 10, (time_max // 10 + 1) * 10, 10))

    # ========== 3. 根据列车数量动态调整图形尺寸 ==========
    num_trains = len(trains)
    # 动态调整图形大小：每增加50列列车，高度增加3
    fig_height = max(10, 10 + (num_trains // 50) * 3)
    fig_width = max(16, 16 + (num_trains // 100) * 4)

    fig, ax = plt.subplots(figsize=(fig_width, fig_height))

    # ========== 4. 绘制网格 ==========
    # 车站网格线（水平线）
    for i in range(len(station_codes)):
        ax.axhline(y=i, color='#D3D3D3', linestyle='--', linewidth=0.8, zorder=1)

    # 时间网格线（垂直线）
    for t in time_ticks:
        ax.axvline(x=t, color='#D3D3D3', linestyle='--', linewidth=0.8, zorder=1)

    # ========== 5. 绘制运行线和停站 ==========
    # 使用颜色映射
    for idx, train in enumerate(trains):
        train_id = train['train_id']
        stops = train['schedule']['stops']

        # 循环使用颜色
        color = TRAIN_COLORS[idx % len(TRAIN_COLORS)]

        x_points = []
        y_points = []

        for stop in stops:
            station = stop['station_code']
            if station not in station_codes:
                continue
            station_idx = station_codes.index(station)

            arrival_time = time_to_minutes(stop['arrival_time'])
            departure_time = time_to_minutes(stop['departure_time'])

            # 添加停站矩形（使用列车颜色半透明）
            if departure_time > arrival_time:  # 只绘制有停站的
                rect = mpatches.Rectangle(
                    (arrival_time, station_idx - 0.3),
                    departure_time - arrival_time,
                    0.6,
                    linewidth=1,
                    edgecolor=color,
                    facecolor=color,
                    alpha=0.3,
                    zorder=2
                )
                ax.add_patch(rect)

            # 运行线点
            x_points.append(arrival_time)
            y_points.append(station_idx)
            x_points.append(departure_time)
            y_points.append(station_idx)

        # 绘制运行线
        if x_points and y_points:
            ax.plot(x_points, y_points, color=color, linewidth=1.5, alpha=0.8,
                    zorder=3)

            # 对于前20列列车，添加标签
            if idx < 20 and len(x_points) >= 2:
                mid_idx = len(x_points) // 2
                label_x = x_points[mid_idx] if mid_idx < len(x_points) else x_points[0]
                label_y = y_points[mid_idx] if mid_idx < len(y_points) else y_points[0]

                ax.annotate(train_id, xy=(label_x, label_y),
                           xytext=(label_x + 2, label_y + 0.12),
                           fontsize=7, fontweight='bold', color=color,
                           alpha=0.9, zorder=4)

    # ========== 6. 设置坐标轴 ==========
    ax.set_xlim(time_min, time_max)
    ax.set_xticks(time_ticks)

    time_labels = [f"{t // 60}:{t % 60:02d}" for t in time_ticks]
    ax.set_xticklabels(time_labels, rotation=45, ha='right', fontsize=9)
    ax.set_xlabel('Time (minutes from 0:00)', fontsize=11, fontweight='bold')

    ax.set_ylim(-0.5, len(station_codes) - 0.5)
    ax.set_yticks(range(len(station_codes)))
    ax.set_yticklabels(station_codes, fontsize=10, fontweight='bold')
    ax.set_ylabel('Station', fontsize=11, fontweight='bold')

    # ========== 7. 添加标题 ==========
    ax.set_title(f'Railway Train Diagram ({num_trains} trains)', fontsize=14, fontweight='bold', pad=15)

    # ========== 8. 添加网格背景 ==========
    ax.set_facecolor('#fafafa')
    ax.grid(True, alpha=0.3)

    # ========== 9. 调整布局 ==========
    plt.tight_layout()

    # ========== 10. 保存或返回 ==========
    if return_base64:
        buffer = io.BytesIO()
        # 调整DPI以适应更多列车
        dpi = min(120, 120 + num_trains // 10)
        plt.savefig(buffer, format='png', dpi=dpi, bbox_inches='tight', facecolor='white')
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
    生成对比运行图（原始 vs 优化后，优化的版本支持100+列车）
    改为上下两行布局，更加美观
    返回base64编码的图片
    """
    num_trains = max(len(original_trains), len(optimized_trains))

    # 动态调整图形尺寸（两行布局，宽度可以更宽）
    fig_height = max(12, 12 + (num_trains // 30) * 3)
    fig_width = max(14, 14 + (num_trains // 50) * 2)

    # 改为2行1列的布局
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(fig_width, fig_height * 2))

    # 绘制原始运行图
    _draw_single_diagram(ax1, original_trains, f"原始时刻表 (Original)")

    # 绘制优化后运行图
    _draw_single_diagram(ax2, optimized_trains, f"优化后时刻表 (Optimized)")

    plt.tight_layout()

    # 返回base64编码
    buffer = io.BytesIO()
    dpi = min(120, 120 + num_trains // 10)
    plt.savefig(buffer, format='png', dpi=dpi, bbox_inches='tight', facecolor='white')
    buffer.seek(0)
    img_base64 = base64.b64encode(buffer.read()).decode('utf-8')
    plt.close()

    return img_base64


def _draw_single_diagram(ax, trains: List[Dict], title: str):
    """绘制单幅运行图（内部函数，优化的版本）"""
    # ========== 1. 使用固定车站顺序 ==========
    station_codes = ["BJX", "DJK", "ZBD", "GBD", "XSD", "BDD", "DZD", "ZDJ", "SJP", "GYX", "XTD", "HDD", "AYD"]

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
    # 使用丰富的颜色调色板
    num_trains = len(trains)

    for idx, train in enumerate(trains):
        train_id = train['train_id']
        stops = train['schedule']['stops']
        color = TRAIN_COLORS[idx % len(TRAIN_COLORS)]

        x_points = []
        y_points = []

        for stop in stops:
            station = stop['station_code']
            if station not in station_codes:
                continue
            station_idx = station_codes.index(station)

            arrival_time = time_to_minutes(stop['arrival_time'])
            departure_time = time_to_minutes(stop['departure_time'])

            # 停站矩形（使用列车颜色）
            if departure_time > arrival_time:
                rect = mpatches.Rectangle(
                    (arrival_time, station_idx - 0.3),
                    departure_time - arrival_time,
                    0.6,
                    linewidth=1,
                    edgecolor=color,
                    facecolor=color,
                    alpha=0.3,
                    zorder=2
                )
                ax.add_patch(rect)

            # 运行线点
            x_points.append(arrival_time)
            y_points.append(station_idx)
            x_points.append(departure_time)
            y_points.append(station_idx)

        # 绘制运行线
        if x_points and y_points:
            ax.plot(x_points, y_points, color=color, linewidth=1.5, alpha=0.8,
                    zorder=3)

            # 显示所有列车标签
            label_threshold = num_trains  # 显示所有标签
            if idx < label_threshold and len(x_points) >= 2:
                # 选择合适的位置添加标签（避免重叠）
                mid_idx = len(x_points) // 2
                label_x = x_points[mid_idx] if mid_idx < len(x_points) else x_points[0]
                label_y = y_points[mid_idx] if mid_idx < len(y_points) else y_points[0]

                # 根据列车索引调整标签位置，减少重叠
                offset_x = 3 + (idx % 5) * 2
                offset_y = 0.15 if idx % 2 == 0 else -0.15

                ax.annotate(train_id, xy=(label_x, label_y),
                           xytext=(label_x + offset_x, label_y + offset_y),
                           fontsize=7, fontweight='bold', color=color,
                           alpha=0.9, zorder=4,
                           bbox=dict(boxstyle='round,pad=0.2', facecolor='white', alpha=0.7, edgecolor='none'))

    # ========== 5. 设置坐标轴 ==========
    ax.set_xlim(time_min, time_max)
    ax.set_xticks(time_ticks)
    time_labels = [f"{t // 60}:{t % 60:02d}" for t in time_ticks]
    ax.set_xticklabels(time_labels, rotation=45, ha='right', fontsize=9)
    ax.set_xlabel('Time (minutes)', fontsize=11, fontweight='bold')

    ax.set_ylim(-0.5, len(station_codes) - 0.5)
    ax.set_yticks(range(len(station_codes)))
    ax.set_yticklabels(station_codes, fontsize=10, fontweight='bold')
    ax.set_ylabel('Station', fontsize=11, fontweight='bold')

    ax.set_title(f"{title} ({num_trains} trains)", fontsize=12, fontweight='bold', pad=10)
    ax.set_facecolor('#fafafa')
    ax.grid(True, alpha=0.3)


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
