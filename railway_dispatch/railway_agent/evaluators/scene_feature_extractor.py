# -*- coding: utf-8 -*-
"""
场景特征提取器
从AccidentCard中提取精细化的场景特征标签
为微调数据集提供高质量的场景描述
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass
from enum import Enum
import hashlib
import json

from models.workflow_models import AccidentCard

logger = logging.getLogger(__name__)


class SceneCategory(Enum):
    """场景类别（精细版）"""
    # 临时限速子类
    TEMP_SPEED_LIMIT_MINOR = "temp_speed_limit_minor"  # 轻微限速
    TEMP_SPEED_LIMIT_MODERATE = "temp_speed_limit_moderate"  # 中等限速
    TEMP_SPEED_LIMIT_SEVERE = "temp_speed_limit_severe"  # 严重限速

    # 突发故障子类
    SUDDEN_FAILURE_MINOR = "sudden_failure_minor"  # 轻微故障
    SUDDEN_FAILURE_MODERATE = "sudden_failure_moderate"  # 中等故障
    SUDDEN_FAILURE_SEVERE = "sudden_failure_severe"  # 严重故障

    # 区间封锁子类
    SECTION_BLOCK_SHORT = "section_block_short"  # 短时封锁
    SECTION_BLOCK_LONG = "section_block_long"  # 长时封锁


class DelayLevel(Enum):
    """延误等级"""
    TRIVIAL = "trivial"  # 微小（≤5分）
    MINOR = "minor"  # 轻微（5-10分）
    MODERATE = "moderate"  # 一般（10-30分）
    SIGNIFICANT = "significant"  # 较大（30-60分）
    SEVERE = "severe"  # 严重（>60分）


class TrainDensity(Enum):
    """列车密度"""
    SPARSE = "sparse"  # 稀疏（≤3列）
    MODERATE = "moderate"  # 中等（4-10列）
    DENSE = "dense"  # 密集（>10列）


class OperationPeriod(Enum):
    """运营时段（高铁24小时运营，不区分高峰/平谷）"""
    WINDOW = "window"  # 天窗期（0:00-6:00）：线路检修时段，几乎无列车运行
    EARLY_OPERATION = "early_operation"  # 运营初期（6:00-9:00）：列车逐步发出
    DAY_OPERATION = "day_operation"  # 日间运营（9:00-14:00）：多方向列车交叉运行
    AFTERNOON_OPERATION = "afternoon_operation"  # 下午运营（14:00-18:00）：全天列车密度最高
    EVENING_OPERATION = "evening_operation"  # 晚间运营（18:00-22:00）：列车陆续终到
    LATE_NIGHT_OPERATION = "late_night_operation"  # 深夜运营（22:00-24:00）：准备进入天窗期


class UrgencyLevel(Enum):
    """紧急程度"""
    LOW = "low"  # 低（轻微延误+平峰期+小规模）
    MEDIUM = "medium"  # 中
    HIGH = "high"  # 高（较大延误+高峰期+中规模）
    CRITICAL = "critical"  # 严重（严重延误+高峰期+大规模）


@dataclass
class SceneFeatures:
    """场景特征（精细化）"""
    # 基础信息
    scene_category: str  # 原始场景类别
    scene_fingerprint: str  # 场景指纹（hash）

    # 精细化标签
    fine_grained_category: SceneCategory  # 细粒度场景类别
    delay_level: DelayLevel  # 延误等级
    train_density: TrainDensity  # 列车密度
    operation_period: OperationPeriod  # 运营时段
    urgency_level: UrgencyLevel  # 紧急程度

    # 详细特征
    features_dict: Dict[str, Any]  # 所有特征的字典表示

    # 嵌入表示（可选，用于相似场景聚类）
    embedding: Optional[List[float]] = None


class SceneFeatureExtractor:
    """
    场景特征提取器

    职责：
    1. 从AccidentCard中提取精细化场景特征
    2. 生成场景指纹（用于相似场景聚类）
    3. 计算紧急程度、列车密度等高级特征
    4. 为微调数据集提供高质量的场景描述
    """

    def extract(self, accident_card: AccidentCard) -> SceneFeatures:
        """
        提取场景特征

        Args:
            accident_card: 事故卡片

        Returns:
            SceneFeatures: 场景特征
        """
        # 1. 提取基础信息
        scene_category = accident_card.scene_category or "unknown"
        fault_type = accident_card.fault_type or "unknown"

        # 2. 提取延误等级
        delay_level = self._extract_delay_level(accident_card)

        # 3. 提取列车密度
        train_density = self._extract_train_density(accident_card)

        # 4. 提取运营时段
        operation_period = self._extract_operation_period()

        # 5. 计算紧急程度
        urgency_level = self._calculate_urgency_level(delay_level, train_density, operation_period)

        # 6. 推断细粒度场景类别
        fine_grained_category = self._infer_fine_grained_category(
            scene_category, delay_level, fault_type
        )

        # 7. 生成场景指纹
        scene_fingerprint = self._generate_fingerprint(
            fine_grained_category, delay_level, train_density, operation_period
        )

        # 8. 构建特征字典
        features_dict = {
            # 基础信息
            "场景类型": scene_category,
            "故障类型": fault_type,
            "位置": accident_card.location_name or accident_card.location_code or "未知",
            "位置类型": accident_card.location_type or "station",

            # 延误信息
            "预计延误时长_分钟": accident_card.expected_duration or 0,
            "延误等级": delay_level.value,

            # 列车信息
            "受影响列车数": len(accident_card.affected_train_ids) if accident_card.affected_train_ids else 0,
            "列车密度": train_density.value,

            # 时段信息
            "运营时段": operation_period.value,

            # 综合特征
            "紧急程度": urgency_level.value,

            # 精细化标签
            "细粒度场景类别": fine_grained_category.value,

            # 其他信息
            "信息完整性": "完整" if accident_card.is_complete else "不完整",
            "缺失字段": accident_card.missing_fields or []
        }

        return SceneFeatures(
            scene_category=scene_category,
            scene_fingerprint=scene_fingerprint,
            fine_grained_category=fine_grained_category,
            delay_level=delay_level,
            train_density=train_density,
            operation_period=operation_period,
            urgency_level=urgency_level,
            features_dict=features_dict,
            embedding=None  # 暂不生成嵌入
        )

    def _extract_delay_level(self, accident_card: AccidentCard) -> DelayLevel:
        """提取延误等级"""
        duration = accident_card.expected_duration

        if duration is None:
            return DelayLevel.MODERATE  # 默认值

        if duration <= 5:
            return DelayLevel.TRIVIAL
        elif duration <= 10:
            return DelayLevel.MINOR
        elif duration <= 30:
            return DelayLevel.MODERATE
        elif duration <= 60:
            return DelayLevel.SIGNIFICANT
        else:
            return DelayLevel.SEVERE

    def _extract_train_density(self, accident_card: AccidentCard) -> TrainDensity:
        """提取列车密度"""
        train_count = len(accident_card.affected_train_ids) if accident_card.affected_train_ids else 0

        if train_count <= 3:
            return TrainDensity.SPARSE
        elif train_count <= 10:
            return TrainDensity.MODERATE
        else:
            return TrainDensity.DENSE

    def _extract_operation_period(self) -> OperationPeriod:
        """提取运营时段（高铁24小时运营，不区分高峰/平谷）"""
        now = datetime.now()
        current_hour = now.hour

        if 0 <= current_hour < 6:
            return OperationPeriod.WINDOW
        elif 6 <= current_hour < 9:
            return OperationPeriod.EARLY_OPERATION
        elif 9 <= current_hour < 14:
            return OperationPeriod.DAY_OPERATION
        elif 14 <= current_hour < 18:
            return OperationPeriod.AFTERNOON_OPERATION
        elif 18 <= current_hour < 22:
            return OperationPeriod.EVENING_OPERATION
        else:
            return OperationPeriod.LATE_NIGHT_OPERATION

    def _calculate_urgency_level(
        self,
        delay_level: DelayLevel,
        train_density: TrainDensity,
        operation_period: OperationPeriod
    ) -> UrgencyLevel:
        """
        计算紧急程度

        Args:
            delay_level: 延误等级
            train_density: 列车密度
            operation_period: 运营时段

        Returns:
            UrgencyLevel: 紧急程度
        """
        # 权重计算（分值越高越紧急）
        delay_score = {
            DelayLevel.TRIVIAL: 1,
            DelayLevel.MINOR: 2,
            DelayLevel.MODERATE: 3,
            DelayLevel.SIGNIFICANT: 4,
            DelayLevel.SEVERE: 5
        }[delay_level]

        density_score = {
            TrainDensity.SPARSE: 1,
            TrainDensity.MODERATE: 2,
            TrainDensity.DENSE: 3
        }[train_density]

        period_score = {
            OperationPeriod.WINDOW: 1,  # 天窗期，列车稀疏
            OperationPeriod.LATE_NIGHT_OPERATION: 1,  # 深夜运营，列车较少
            OperationPeriod.EARLY_OPERATION: 2,  # 运营初期
            OperationPeriod.DAY_OPERATION: 2,  # 日间运营
            OperationPeriod.EVENING_OPERATION: 2,  # 晚间运营
            OperationPeriod.AFTERNOON_OPERATION: 3  # 下午运营，列车密度最高
        }[operation_period]

        # 加权总分
        total_score = delay_score * 0.5 + density_score * 0.3 + period_score * 0.2

        # 映射到紧急程度
        if total_score <= 2.0:
            return UrgencyLevel.LOW
        elif total_score <= 3.0:
            return UrgencyLevel.MEDIUM
        elif total_score <= 4.0:
            return UrgencyLevel.HIGH
        else:
            return UrgencyLevel.CRITICAL

    def _infer_fine_grained_category(
        self,
        scene_category: str,
        delay_level: DelayLevel,
        fault_type: str
    ) -> SceneCategory:
        """推断细粒度场景类别"""
        if scene_category == "临时限速":
            if delay_level in [DelayLevel.TRIVIAL, DelayLevel.MINOR]:
                return SceneCategory.TEMP_SPEED_LIMIT_MINOR
            elif delay_level == DelayLevel.MODERATE:
                return SceneCategory.TEMP_SPEED_LIMIT_MODERATE
            else:
                return SceneCategory.TEMP_SPEED_LIMIT_SEVERE

        elif scene_category == "突发故障":
            if delay_level in [DelayLevel.TRIVIAL, DelayLevel.MINOR]:
                return SceneCategory.SUDDEN_FAILURE_MINOR
            elif delay_level == DelayLevel.MODERATE:
                return SceneCategory.SUDDEN_FAILURE_MODERATE
            else:
                return SceneCategory.SUDDEN_FAILURE_SEVERE

        elif scene_category == "区间封锁":
            if delay_level in [DelayLevel.TRIVIAL, DelayLevel.MINOR, DelayLevel.MODERATE]:
                return SceneCategory.SECTION_BLOCK_SHORT
            else:
                return SceneCategory.SECTION_BLOCK_LONG

        # 默认
        return SceneCategory.SUDDEN_FAILURE_MODERATE

    def _generate_fingerprint(
        self,
        fine_grained_category: SceneCategory,
        delay_level: DelayLevel,
        train_density: TrainDensity,
        operation_period: OperationPeriod
    ) -> str:
        """
        生成场景指纹

        Args:
            fine_grained_category: 细粒度场景类别
            delay_level: 延误等级
            train_density: 列车密度
            operation_period: 运营时段

        Returns:
            str: 场景指纹（MD5 hash）
        """
        # 构建特征字符串
        features_str = f"{fine_grained_category.value}|{delay_level.value}|{train_density.value}|{operation_period.value}"

        # 生成hash
        hash_obj = hashlib.md5(features_str.encode('utf-8'))
        fingerprint = hash_obj.hexdigest()

        return fingerprint

    def find_similar_scenes(
        self,
        accident_card: AccidentCard,
        historical_scenes: List[SceneFeatures],
        top_k: int = 5
    ) -> List[Tuple[SceneFeatures, float]]:
        """
        查找相似的历史场景

        Args:
            accident_card: 当前事故卡片
            historical_scenes: 历史场景列表
            top_k: 返回最相似的K个场景

        Returns:
            List[Tuple]: [(场景特征, 相似度分数)]
        """
        # 提取当前场景特征
        current_features = self.extract(accident_card)

        similar_scenes = []

        for scene in historical_scenes:
            # 计算相似度（基于指纹和紧急程度）
            similarity = self._calculate_similarity(current_features, scene)
            similar_scenes.append((scene, similarity))

        # 按相似度排序
        similar_scenes.sort(key=lambda x: x[1], reverse=True)

        return similar_scenes[:top_k]

    def _calculate_similarity(
        self,
        scene1: SceneFeatures,
        scene2: SceneFeatures
    ) -> float:
        """
        计算两个场景的相似度

        Args:
            scene1: 场景1
            scene2: 场景2

        Returns:
            float: 相似度分数（0-1）
        """
        # 简单的相似度计算（可以后续升级为嵌入向量相似度）
        similarity = 0.0

        # 1. 细粒度场景类别（权重最高）
        if scene1.fine_grained_category == scene2.fine_grained_category:
            similarity += 0.4

        # 2. 延误等级
        delay_order = [DelayLevel.TRIVIAL, DelayLevel.MINOR, DelayLevel.MODERATE,
                      DelayLevel.SIGNIFICANT, DelayLevel.SEVERE]
        delay_distance = abs(delay_order.index(scene1.delay_level) - delay_order.index(scene2.delay_level))
        similarity += (4 - delay_distance) * 0.15  # 最大0.6

        # 3. 列车密度
        density_order = [TrainDensity.SPARSE, TrainDensity.MODERATE, TrainDensity.DENSE]
        density_distance = abs(density_order.index(scene1.train_density) - density_order.index(scene2.train_density))
        similarity += (2 - density_distance) * 0.1  # 最大0.2

        # 4. 运营时段
        period_order = [OperationPeriod.WINDOW, OperationPeriod.LATE_NIGHT_OPERATION,
                       OperationPeriod.EARLY_OPERATION, OperationPeriod.DAY_OPERATION,
                       OperationPeriod.EVENING_OPERATION, OperationPeriod.AFTERNOON_OPERATION]
        period_distance = abs(period_order.index(scene1.operation_period) - period_order.index(scene2.operation_period))
        similarity += (5 - period_distance) * 0.05  # 最大0.25

        # 归一化到0-1
        return min(1.0, max(0.0, similarity))

    def export_features_to_text(self, features: SceneFeatures) -> str:
        """
        将场景特征导出为自然语言描述

        Args:
            features: 场景特征

        Returns:
            str: 自然语言描述
        """
        descriptions = []

        # 场景类型
        fine_grained_map = {
            SceneCategory.TEMP_SPEED_LIMIT_MINOR: "轻微临时限速",
            SceneCategory.TEMP_SPEED_LIMIT_MODERATE: "中等临时限速",
            SceneCategory.TEMP_SPEED_LIMIT_SEVERE: "严重临时限速",
            SceneCategory.SUDDEN_FAILURE_MINOR: "轻微突发故障",
            SceneCategory.SUDDEN_FAILURE_MODERATE: "中等突发故障",
            SceneCategory.SUDDEN_FAILURE_SEVERE: "严重突发故障",
            SceneCategory.SECTION_BLOCK_SHORT: "短时区间封锁",
            SceneCategory.SECTION_BLOCK_LONG: "长时区间封锁"
        }
        descriptions.append(f"场景类型：{fine_grained_map.get(features.fine_grained_category, '未知场景')}")

        # 延误等级
        delay_map = {
            DelayLevel.TRIVIAL: "微小（≤5分钟）",
            DelayLevel.MINOR: "轻微（5-10分钟）",
            DelayLevel.MODERATE: "一般（10-30分钟）",
            DelayLevel.SIGNIFICANT: "较大（30-60分钟）",
            DelayLevel.SEVERE: "严重（>60分钟）"
        }
        descriptions.append(f"延误等级：{delay_map.get(features.delay_level, '未知')}")

        # 列车密度
        density_map = {
            TrainDensity.SPARSE: "稀疏（≤3列）",
            TrainDensity.MODERATE: "中等（4-10列）",
            TrainDensity.DENSE: "密集（>10列）"
        }
        descriptions.append(f"列车密度：{density_map.get(features.train_density, '未知')}")

        # 运营时段
        period_map = {
            OperationPeriod.WINDOW: "天窗期（0:00-6:00）：线路检修时段，列车稀疏",
            OperationPeriod.EARLY_OPERATION: "运营初期（6:00-9:00）：列车逐步发出，密度较低",
            OperationPeriod.DAY_OPERATION: "日间运营（9:00-14:00）：多方向列车交叉运行",
            OperationPeriod.AFTERNOON_OPERATION: "下午运营（14:00-18:00）：全天列车密度最高",
            OperationPeriod.EVENING_OPERATION: "晚间运营（18:00-22:00）：列车陆续终到",
            OperationPeriod.LATE_NIGHT_OPERATION: "深夜运营（22:00-24:00）：准备进入天窗期"
        }
        descriptions.append(f"运营时段：{period_map.get(features.operation_period, '未知')}")

        # 紧急程度
        urgency_map = {
            UrgencyLevel.LOW: "低（非紧急）",
            UrgencyLevel.MEDIUM: "中（需关注）",
            UrgencyLevel.HIGH: "高（需快速响应）",
            UrgencyLevel.CRITICAL: "严重（需立即响应）"
        }
        descriptions.append(f"紧急程度：{urgency_map.get(features.urgency_level, '未知')}")

        return "；".join(descriptions)


# 全局实例
_extractor: Optional[SceneFeatureExtractor] = None


def get_scene_feature_extractor() -> SceneFeatureExtractor:
    """获取全局场景特征提取器实例"""
    global _extractor
    if _extractor is None:
        _extractor = SceneFeatureExtractor()
    return _extractor
