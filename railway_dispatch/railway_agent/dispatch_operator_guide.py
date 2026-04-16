# -*- coding: utf-8 -*-
"""
调度员操作指南检索模块
基于关键词匹配的RAG检索，为调度员提供场景操作指导

功能：
1. 根据事故卡片识别场景类型
2. 检索对应的调度员操作指南
3. 输出结构化的操作步骤
"""

import os
import re
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class OperatorGuide:
    """调度员操作指南数据结构"""
    scene_type: str  # 场景类型
    scene_name: str  # 场景名称
    keywords: List[str]  # 匹配关键词
    operations: List[Dict[str, Any]]  # 操作步骤列表
    emergency_level: str  # 紧急程度: high/medium/low
    references: List[str]  # 参考规章


class DispatchOperatorGuideRetriever:
    """
    调度员操作指南检索器
    基于关键词匹配，从知识库中检索调度员操作指南
    """

    def __init__(self, knowledge_dir: Optional[str] = None):
        """
        初始化检索器

        Args:
            knowledge_dir: 知识库目录路径
        """
        if knowledge_dir is None:
            knowledge_dir = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                'data', 'knowledge'
            )
        self.knowledge_dir = knowledge_dir

        # 加载操作指南知识库
        self.guides = self._load_operator_guides()

        # 构建关键词索引
        self.keyword_index = self._build_keyword_index()

    def _load_operator_guides(self) -> Dict[str, OperatorGuide]:
        """
        加载调度员操作指南
        从知识库文件解析结构化数据
        """
        guides = {}

        # 内置核心操作指南（确保基础功能可用）
        core_guides = self._load_core_guides()
        guides.update(core_guides)

        # 从文件加载额外指南
        file_guides = self._load_guides_from_file()
        guides.update(file_guides)

        logger.info(f"[OperatorGuide] 加载了 {len(guides)} 个操作指南")
        return guides

    def _load_core_guides(self) -> Dict[str, OperatorGuide]:
        """加载核心操作指南"""
        core_guides = {}

        # 1. 大风天气操作指南
        core_guides["strong_wind"] = OperatorGuide(
            scene_type="TEMPORARY_SPEED_LIMIT",
            scene_name="大风天气",
            keywords=["大风", "强风", "台风", "侧风", "风速", "风"],
            operations=[
                {
                    "step": 1,
                    "phase": "信息确认",
                    "title": "确认大风报警信息",
                    "actions": [
                        "立即确认大风报警地点（区段、里程）",
                        "确认风速监测子系统显示的风速值",
                        "确认报警类型：常态报警/预警报警/禁止运行报警",
                        "查看气象部门发布的专项天气预报"
                    ],
                    "time_limit": "3分钟内"
                },
                {
                    "step": 2,
                    "phase": "列车处置",
                    "title": "处置已进入区间的列车",
                    "actions": [
                        "立即呼叫已进入区间的列车司机",
                        "通知列车司机当前风速值和限速要求",
                        "若显示禁止运行报警，立即命令列车停车",
                        "记录停车位置和停车时间",
                        "通知后续列车在就近车站停车待命"
                    ],
                    "time_limit": "立即"
                },
                {
                    "step": 3,
                    "phase": "限速设置",
                    "title": "设置列控限速",
                    "actions": [
                        "根据风速监测子系统提示设置列控限速",
                        "风速≤15m/s：正常运行",
                        "15m/s/s<风速≤20m/s：限速200km/h",
                        "20m/s/s<风速≤25m/s：限速120km/h",
                        "风速>25m/s：禁止运行",
                        "确认限速设置成功，核对列控中心回执"
                    ],
                    "time_limit": "5分钟内"
                },
                {
                    "step": 4,
                    "phase": "后续监控",
                    "title": "持续监控与恢复",
                    "actions": [
                        "持续监控风速变化，每5分钟确认一次",
                        "风速降低后，逐步恢复常速运行",
                        "确认线路设备无异常后方可恢复正常运营"
                    ],
                    "time_limit": "持续"
                }
            ],
            emergency_level="high",
            references=[《铁路技术管理规程》, 《高速铁路调度规则》]
        )

        # 2. 暴雨天气操作指南
        core_guides["heavy_rain"] = OperatorGuide(
            scene_type="TEMPORARY_SPEED_LIMIT",
            scene_name="暴雨天气",
            keywords=["暴雨", "大雨", "降雨", "洪水", "积水", "雨量"],
            operations=[
                {
                    "step": 1,
                    "phase": "信息确认",
                    "title": "确认降雨报警信息",
                    "actions": [
                        "立即确认降雨报警地点和等级",
                        "确认降雨量监测数据（小时雨量/连续雨量）",
                        "确认警戒等级：出巡警戒/限速警戒/封锁警戒",
                        "查看工务部门雨量监测系统数据"
                    ],
                    "time_limit": "3分钟内"
                },
                {
                    "step": 2,
                    "phase": "线路确认",
                    "title": "组织线路巡视",
                    "actions": [
                        "立即通知工务部门开展区间巡视",
                        "对重点地段（路堑、桥涵、隧道口、边坡）重点确认",
                        "确认线路几何尺寸是否变化",
                        "确认排水设施是否畅通"
                    ],
                    "time_limit": "10分钟内"
                },
                {
                    "step": 3,
                    "phase": "限速设置",
                    "title": "根据雨量设置限速",
                    "actions": [
                        "出巡警戒：正常运行，加强监控",
                        "限速警戒：限速120km/h或160km/h",
                        "封锁警戒：禁止进入区间",
                        "限速里程按雨量监测点覆盖范围确定",
                        "确认限速设置成功"
                    ],
                    "time_limit": "5分钟内"
                },
                {
                    "step": 4,
                    "phase": "列车处置",
                    "title": "处置列车运行",
                    "actions": [
                        "呼叫已进入区间的列车司机确认运行状态",
                        "通知司机注意运行，加强瞭望",
                        "发现异常立即停车",
                        "对尚未进入区间的列车执行限速或扣停"
                    ],
                    "time_limit": "立即"
                },
                {
                    "step": 5,
                    "phase": "恢复运营",
                    "title": "解除警戒恢复运行",
                    "actions": [
                        "解除警戒后逐步恢复常速运行",
                        "重点确认路基状态、隧道状况、接触网状态",
                        "恢复正常运营前必须组织添乘检查"
                    ],
                    "time_limit": "根据检查结果"
                }
            ],
            emergency_level="high",
            references=[《铁路技术管理规程》, 《高速铁路自然灾害及异物侵限监测系统运用维护办法》]
        )

        # 3. 冰雪天气操作指南
        core_guides["snow_ice"] = OperatorGuide(
            scene_type="TEMPORARY_SPEED_LIMIT",
            scene_name="冰雪天气",
            keywords=["冰雪", "降雪", "结冰", "冻雨", "道岔冻结", "接触网覆冰"],
            operations=[
                {
                    "step": 1,
                    "phase": "信息确认",
                    "title": "确认冰雪天气信息",
                    "actions": [
                        "立即确认冰雪天气报警地点和类型",
                        "确认降雪量/覆冰厚度监测数据",
                        "确认线路、供电、信号设备状态",
                        "确认道岔融雪装置是否启动"
                    ],
                    "time_limit": "3分钟内"
                },
                {
                    "step": 2,
                    "phase": "道岔处置",
                    "title": "处置道岔状况",
                    "actions": [
                        "立即确认相关车站的道岔状况",
                        "通知车务部门启动道岔融雪装置",
                        "对重点道岔安排人员现场值守",
                        "道岔故障时及时组织电务部门现场处置"
                    ],
                    "time_limit": "立即"
                },
                {
                    "step": 3,
                    "phase": "接触网处置",
                    "title": "处置接触网覆冰",
                    "actions": [
                        "确认接触网覆冰情况",
                        "确认动车组受电弓状况",
                        "覆冰严重时组织电力部门进行热滑除冰",
                        "确认除冰完成后方可恢复供电"
                    ],
                    "time_limit": "根据覆冰情况"
                },
                {
                    "step": 4,
                    "phase": "限速设置",
                    "title": "设置运行限速",
                    "actions": [
                        "小雪/薄冰：限速200km/h",
                        "中雪/中度覆冰：限速120km/h",
                        "大雪/严重覆冰：限速80km/h或停车",
                        "道岔区段单独设置限速"
                    ],
                    "time_limit": "5分钟内"
                },
                {
                    "step": 5,
                    "phase": "恢复运营",
                    "title": "恢复正常运营",
                    "actions": [
                        "降雪结束后组织添乘检查线路状况",
                        "确认道岔转换灵活、线路正常",
                        "确认各专业设备状态良好后恢复正常"
                    ],
                    "time_limit": "根据检查结果"
                }
            ],
            emergency_level="medium",
            references=[《铁路技术管理规程》, 《铁路冰雪天气行车组织办法》]
        )

        # 4. 设备故障操作指南
        core_guides["equipment_failure"] = OperatorGuide(
            scene_type="SUDDEN_FAILURE",
            scene_name="设备故障",
            keywords=["设备故障", "信号故障", "接触网故障", "线路故障", "道岔故障"],
            operations=[
                {
                    "step": 1,
                    "phase": "信息确认",
                    "title": "确认故障信息",
                    "actions": [
                        "立即确认故障类型和位置",
                        "确认故障影响范围",
                        "确认故障发生时间",
                        "通知相关设备管理部门"
                    ],
                    "time_limit": "3分钟内"
                },
                {
                    "step": 2,
                    "phase": "安全防护",
                    "title": "确保列车安全",
                    "actions": [
                        "立即扣停后续列车",
                        "通知已进入区间的列车注意运行",
                        "必要时命令区间列车停车",
                        "设置防护，防止列车进入故障区域"
                    ],
                    "time_limit": "立即"
                },
                {
                    "step": 3,
                    "phase": "故障处置",
                    "title": "组织故障处理",
                    "actions": [
                        "通知设备管理部门现场检查",
                        "根据故障类型启动相应应急预案",
                        "评估故障恢复时间",
                        "做好故障记录和信息通报"
                    ],
                    "time_limit": "根据故障类型"
                },
                {
                    "step": 4,
                    "phase": "行车调整",
                    "title": "调整列车运行",
                    "actions": [
                        "根据故障影响调整列车时刻表",
                        "安排备用车底（如需要）",
                        "做好旅客转运安排",
                        "发布调度命令"
                    ],
                    "time_limit": "根据故障恢复时间"
                }
            ],
            emergency_level="high",
            references=[《铁路技术管理规程》, 《高速铁路突发事件应急预案》]
        )

        # 5. 区间封锁操作指南
        core_guides["section_blockade"] = OperatorGuide(
            scene_type="SECTION_INTERRUPT",
            scene_name="区间封锁",
            keywords=["区间封锁", "线路封锁", "施工封锁", "事故封锁"],
            operations=[
                {
                    "step": 1,
                    "phase": "信息确认",
                    "title": "确认封锁信息",
                    "actions": [
                        "立即确认封锁区段和原因",
                        "确认封锁起止时间",
                        "评估封锁影响范围",
                        "通知相关车站和调度台"
                    ],
                    "time_limit": "3分钟内"
                },
                {
                    "step": 2,
                    "phase": "列车处置",
                    "title": "处置区间内列车",
                    "actions": [
                        "停止新列车发车（进入封锁区段）",
                        "区间内列车就近停靠",
                        "通知区间内列车停车待命",
                        "做好旅客安抚工作"
                    ],
                    "time_limit": "立即"
                },
                {
                    "step": 3,
                    "phase": "绕行安排",
                    "title": "组织绕行或停运",
                    "actions": [
                        "评估绕行可行性",
                        "如可行，安排列车绕行其他线路",
                        "如不可行，组织列车停运",
                        "做好旅客转运安排"
                    ],
                    "time_limit": "根据情况"
                },
                {
                    "step": 4,
                    "phase": "恢复准备",
                    "title": "准备恢复运营",
                    "actions": [
                        "确认封锁解除条件",
                        "确认线路设备状态良好",
                        "确认具备恢复运营条件",
                        "逐步恢复列车运行"
                    ],
                    "time_limit": "根据封锁原因"
                }
            ],
            emergency_level="high",
            references=[《铁路技术管理规程》, 《铁路营业线施工安全管理办法》]
        )

        # 6. 列车故障操作指南
        core_guides["train_failure"] = OperatorGuide(
            scene_type="SUDDEN_FAILURE",
            scene_name="列车故障",
            keywords=["列车故障", "动车组故障", "车辆故障", "机械故障"],
            operations=[
                {
                    "step": 1,
                    "phase": "信息确认",
                    "title": "确认故障信息",
                    "actions": [
                        "立即确认故障列车车次和位置",
                        "确认故障类型和影响",
                        "确认故障发生时间",
                        "通知随车机械师和司机"
                    ],
                    "time_limit": "3分钟内"
                },
                {
                    "step": 2,
                    "phase": "安全防护",
                    "title": "确保行车安全",
                    "actions": [
                        "立即扣停后续列车",
                        "通知故障列车停车或限速运行",
                        "设置防护，防止后续列车追尾",
                        "通知邻线列车注意运行"
                    ],
                    "time_limit": "立即"
                },
                {
                    "step": 3,
                    "phase": "故障处置",
                    "title": "组织故障处理",
                    "actions": [
                        "通知随车机械师检查处理",
                        "必要时组织救援",
                        "安排备用车底（如需要）",
                        "做好旅客安抚和转运准备"
                    ],
                    "time_limit": "根据故障类型"
                },
                {
                    "step": 4,
                    "phase": "恢复运行",
                    "title": "恢复正常运行",
                    "actions": [
                        "确认故障排除",
                        "确认列车可以安全运行",
                        "逐步恢复正常运行秩序",
                        "做好晚点列车调整"
                    ],
                    "time_limit": "根据故障处理情况"
                }
            ],
            emergency_level="high",
            references=[《铁路技术管理规程》, 《动车组故障应急处置办法》]
        )

        return core_guides

    def _load_guides_from_file(self) -> Dict[str, OperatorGuide]:
        """从知识库文件加载操作指南"""
        guides = {}

        # 尝试加载调度员操作知识库
        operator_guide_file = os.path.join(
            self.knowledge_dir,
            "High-Speed Train Dispatcher Emergency Operation Knowledge.txt"
        )

        if os.path.exists(operator_guide_file):
            try:
                guides.update(self._parse_operator_guide_file(operator_guide_file))
                logger.info(f"[OperatorGuide] 从文件加载操作指南: {operator_guide_file}")
            except Exception as e:
                logger.warning(f"[OperatorGuide] 加载文件失败: {e}")

        return guides

    def _parse_operator_guide_file(self, filepath: str) -> Dict[str, OperatorGuide]:
        """
        解析调度员操作指南文件
        将非结构化文本解析为结构化的OperatorGuide
        """
        guides = {}

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

            # 按场景分割（以##开头）
            sections = re.split(r'\n##\s+', content)

            for section in sections:
                if not section.strip():
                    continue

                # 解析场景名称
                lines = section.strip().split('\n')
                if not lines:
                    continue

                scene_name = lines[0].strip()

                # 解析关键词
                keywords = []
                keyword_match = re.search(r'###\s*关键词[：:](.+?)(?=###|\Z)', section, re.DOTALL)
                if keyword_match:
                    keyword_text = keyword_match.group(1)
                    keywords = [k.strip() for k in keyword_text.split('、') if k.strip()]

                # 解析操作步骤
                operations = []
                operation_sections = re.findall(
                    r'####\s*(\d+)\.\s*(.+?)\n(.+?)(?=####|\Z)',
                    section,
                    re.DOTALL
                )

                for step_num, step_title, step_content in operation_sections:
                    # 解析操作内容
                    actions = []
                    for line in step_content.split('\n'):
                        line = line.strip()
                        if line.startswith('-') or line.startswith('*'):
                            actions.append(line[1:].strip())

                    if actions:
                        operations.append({
                            "step": int(step_num),
                            "phase": "执行阶段",
                            "title": step_title.strip(),
                            "actions": actions,
                            "time_limit": "根据具体情况"
                        })

                # 如果成功解析出操作步骤，创建OperatorGuide
                if operations:
                    scene_key = self._normalize_scene_name(scene_name)
                    guides[scene_key] = OperatorGuide(
                        scene_type="UNKNOWN",
                        scene_name=scene_name,
                        keywords=keywords,
                        operations=operations,
                        emergency_level="medium",
                        references=[]
                    )

        except Exception as e:
            logger.error(f"[OperatorGuide] 解析文件失败: {e}")

        return guides

    def _normalize_scene_name(self, name: str) -> str:
        """规范化场景名称作为key"""
        # 移除常见前缀
        name = re.sub(r'^[一二三四五六七八九十]+、\s*', '', name)
        name = re.sub(r'^(\d+)\.\s*', '', name)

        # 转换为小写并替换非字母数字字符
        name = re.sub(r'[^\w]', '_', name.lower())
        return name.strip('_')

    def _build_keyword_index(self) -> Dict[str, List[str]]:
        """
        构建关键词索引
        关键词 -> 指南key列表
        """
        index = {}

        for guide_key, guide in self.guides.items():
            for keyword in guide.keywords:
                keyword_lower = keyword.lower()
                if keyword_lower not in index:
                    index[keyword_lower] = []
                index[keyword_lower].append(guide_key)

        return index

    def retrieve(self, query: str, accident_card: Optional[Dict] = None) -> Optional[OperatorGuide]:
        """
        检索调度员操作指南

        Args:
            query: 查询文本（用户输入或事故描述）
            accident_card: 事故卡片（可选，用于精确匹配）

        Returns:
            OperatorGuide: 最匹配的操作指南，如果没有匹配则返回None
        """
        query_lower = query.lower()

        # 计算每个指南的匹配分数
        scores = {}

        for guide_key, guide in self.guides.items():
            score = 0

            # 1. 关键词匹配
            for keyword in guide.keywords:
                if keyword.lower() in query_lower:
                    score += 2  # 关键词匹配得2分

            # 2. 场景名称匹配
            if guide.scene_name.lower() in query_lower:
                score += 3  # 场景名称匹配得3分

            # 3. 使用事故卡片信息增强匹配
            if accident_card:
                # 故障类型匹配
                fault_type = accident_card.get("fault_type", "")
                if fault_type and any(kw in fault_type for kw in guide.keywords):
                    score += 2

                # 场景类别匹配
                scene_category = accident_card.get("scene_category", "")
                if scene_category and scene_category in guide.scene_name:
                    score += 2

            if score > 0:
                scores[guide_key] = score

        if not scores:
            logger.debug(f"[OperatorGuide] 未找到匹配的操作指南: {query[:50]}...")
            return None

        # 返回得分最高的指南
        best_match = max(scores.items(), key=lambda x: x[1])
        guide_key = best_match[0]
        match_score = best_match[1]

        logger.info(f"[OperatorGuide] 匹配到操作指南: {guide_key}, 得分: {match_score}")
        return self.guides[guide_key]

    def format_guide_for_display(self, guide: OperatorGuide) -> str:
        """
        格式化操作指南为可读文本

        Args:
            guide: 操作指南

        Returns:
            str: 格式化后的文本
        """
        lines = []

        # 标题
        lines.append(f"=" * 60)
        lines.append(f"调度员操作指南：{guide.scene_name}")
        lines.append(f"=" * 60)

        # 紧急程度
        emergency_text = {
            "high": "高",
            "medium": "中",
            "low": "低"
        }.get(guide.emergency_level, "中")
        lines.append(f"紧急程度：{emergency_text}")
        lines.append(f"")

        # 操作步骤
        lines.append(f"操作步骤：")
        lines.append(f"-" * 60)

        for op in guide.operations:
            lines.append(f"")
            lines.append(f"步骤 {op['step']}：{op['title']}")
            lines.append(f"阶段：{op['phase']} | 时限：{op['time_limit']}")
            lines.append(f"")

            for i, action in enumerate(op['actions'], 1):
                lines.append(f"  {i}. {action}")

        # 参考规章
        if guide.references:
            lines.append(f"")
            lines.append(f"-" * 60)
            lines.append(f"参考规章：")
            for ref in guide.references:
                lines.append(f"  • {ref}")

        lines.append(f"")
        lines.append(f"=" * 60)

        return "\n".join(lines)

    def get_guide_dict(self, guide: OperatorGuide) -> Dict[str, Any]:
        """
        将操作指南转换为字典格式（用于API返回）

        Args:
            guide: 操作指南

        Returns:
            Dict: 操作指南字典
        """
        return {
            "scene_type": guide.scene_type,
            "scene_name": guide.scene_name,
            "emergency_level": guide.emergency_level,
            "keywords": guide.keywords,
            "operations": guide.operations,
            "references": guide.references
        }


# 全局实例
_operator_guide_retriever: Optional[DispatchOperatorGuideRetriever] = None


def get_operator_guide_retriever() -> DispatchOperatorGuideRetriever:
    """获取全局操作指南检索器实例"""
    global _operator_guide_retriever
    if _operator_guide_retriever is None:
        _operator_guide_retriever = DispatchOperatorGuideRetriever()
    return _operator_guide_retriever


def retrieve_operator_guide(query: str, accident_card: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    """
    便捷函数：检索调度员操作指南

    Args:
        query: 查询文本
        accident_card: 事故卡片（可选）

    Returns:
        Dict: 操作指南字典，如果没有匹配则返回None
    """
    retriever = get_operator_guide_retriever()
    guide = retriever.retrieve(query, accident_card)

    if guide:
        return retriever.get_guide_dict(guide)
    return None


# 测试代码
if __name__ == "__main__":
    # 测试检索功能
    retriever = DispatchOperatorGuideRetriever()

    test_queries = [
        "保定东到定州东区间大风，风速达到20m/s",
        "石家庄站暴雨，雨量超标",
        "G1234次列车在徐水东故障",
        "XSD-BDD区间需要封锁"
    ]

    print("=" * 70)
    print("调度员操作指南检索测试")
    print("=" * 70)

    for query in test_queries:
        print(f"\n查询：{query}")
        print("-" * 70)

        guide = retriever.retrieve(query)
        if guide:
            print(retriever.format_guide_for_display(guide))
        else:
            print("未找到匹配的操作指南")

    print("\n" + "=" * 70)
    print("测试完成")
    print("=" * 70)
