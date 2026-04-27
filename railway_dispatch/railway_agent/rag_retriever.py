# -*- coding: utf-8 -*-
"""
领域知识RAG检索器
用于为LLM提供铁路调度领域的专业知识

v3.2增强：添加真实高铁调度场景的领域知识
"""

import os
import re
from typing import List, Dict, Optional, Any
import json
import logging

logger = logging.getLogger(__name__)


class DomainKnowledge:
    """领域知识类"""

    def __init__(self):
        self.knowledge_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'data', 'knowledge'
        )
        self._ensure_knowledge_dir()

    def _ensure_knowledge_dir(self):
        """确保知识库目录存在"""
        if not os.path.exists(self.knowledge_dir):
            os.makedirs(self.knowledge_dir, exist_ok=True)


class RAGRetriever:
    """
    RAG检索器 - 为LLM提供领域知识
    v3.2增强：支持真实高铁调度场景
    """

    def __init__(self, knowledge_dir: Optional[str] = None):
        """
        初始化RAG检索器

        Args:
            knowledge_dir: 知识库目录路径
        """
        if knowledge_dir is None:
            knowledge_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                'railway_dispatch', 'data', 'knowledge'
            )
        self.knowledge_dir = knowledge_dir
        self._ensure_knowledge_dir()
        self.knowledge_base = self._load_enhanced_knowledge()

    def _ensure_knowledge_dir(self):
        """确保知识库目录存在"""
        if not os.path.exists(self.knowledge_dir):
            os.makedirs(self.knowledge_dir, exist_ok=True)

    def _load_enhanced_knowledge(self) -> Dict[str, str]:
        """
        加载增强的领域知识库
        包含真实高铁调度场景的知识
        """
        # 基础知识
        knowledge = {
            "场景类型": """
## 场景类型定义（真实高铁调度）

1. 临时限速 (TEMPORARY_SPEED_LIMIT)
   - 原因：暴雨、大风、冰雪、异物侵入等
   - 典型限速值：60km/h, 80km/h, 120km/h
   - 影响：区间内列车需减速运行，影响追踪间隔
   - 京广高铁常见位置：
     * XSD-BDD区间（暴雨多发）
     * BDD-DZD区间（大风多发）
     * GYX-XTD区间（冬季冰雪）
   - 推荐求解器：mip_scheduler（优化调整）

2. 突发故障 (SUDDEN_FAILURE)
   - 原因：接触网故障、信号故障、列车故障、线路异常
   - 常见类型：
     * 接触网故障：导致电力供应中断
     * 信号故障：影响列车运行安全
     * 列车故障：列车无法正常运行
   - 影响：单列或多列列车延误，可能触发连锁延误
   - 推荐求解器：fcfs_scheduler（快速响应）或 max-delay-first（优先延误列车）

3. 区间封锁 (SECTION_INTERRUPT)
   - 原因：严重事故、接触网断线、线路损毁、施工
   - 影响：区间内列车无法运行，需要绕行或停运
   - 处理方式：
     * 区间封锁期间不调度新列车
     * 已在区间内的列车就近停靠
     * 通过其他线路绕行（如可行）
   - 推荐求解器：noop_scheduler（仅记录不调度）
""",
            "求解器选择": """
## 求解器选择规则（真实场景）

1. mip_scheduler (混合整数规划求解器)
   - 适用场景：
     * 临时限速场景（需优化调整时刻表）
     * 列车数量≤10且信息完整的场景
     * 需要全局最优解的场景
   - 优点：
     * 全局最优，考虑多目标优化
     * 精确满足约束条件
     * 可以处理复杂的调度约束
   - 缺点：
     * 计算时间长（秒级到分钟级）
     * 对大规模问题（>100列）效率下降
   - 推荐使用：
     * 临时限速场景：优先使用
     * 突发故障（≤10列）：可以使用
     * 区间封锁：不适用

2. fcfs_scheduler (先到先服务调度器)
   - 适用场景：
     * 突发故障场景（需要快速响应）
     * 列车数量>10的场景
     * 信息不完整或需要粗略调整的场景
   - 优点：
     * 计算快速（毫秒级）
     * 简单可靠，易于实现
     * 可处理大规模问题
   - 缺点：
     * 不考虑全局优化
     * 可能产生次优解
     * 对复杂约束处理能力有限
   - 推荐使用：
     * 突发故障：优先使用
     * 临时限速（>10列）：可以使用
     * 区间封锁：不适用

3. max-delay-first (最大延误优先调度器)
   - 适用场景：
     * 延误传播场景
     * 需要优先处理延误列车的场景
     * 延误已较严重（>30分钟）的场景
   - 优点：
     * 减少最大延误
     * 防止延误进一步扩散
     * 对严重延误列车优先恢复
   - 缺点：
     * 可能导致更多列车延误
     * 需要频繁更新延误信息
   - 推荐使用：
     * 高延误场景（>30分钟）
     * 需要快速恢复严重延误

4. noop_scheduler (空操作调度器)
   - 适用场景：
     * 区间封锁场景
     * 只需记录情况无需调整的场景
   - 优点：
     * 计算最快
     * 不产生新的调整
   - 推荐使用：
     * 区间封锁：唯一推荐
""",
            "京广高铁网络": """
## 京广高铁北京西-安阳东段网络信息

1. 线路概况
   - 全长约500公里
   - 设计速度350km/h
   - 运营速度300km/h
   - 车站数量：13个
   - 列车数量：147列（G字头高速动车组）

2. 车站列表（自北向南）
   - BJX: 北京西
   - DJK: 杜家坎线路所
   - ZBD: 涿州东
   - GBD: 高碑店东
   - XSD: 徐水东
   - BDD: 保定东
   - DZD: 定州东
   - ZDJ: 正定机场
   - SJP: 石家庄
   - GYX: 高邑西
   - XTD: 邢台东
   - HDD: 邯郸东
   - AYD: 安阳东

3. 关键区间（常见限速/故障位置）
   - XSD-BDD: 保定地区，暴雨多发
   - BDD-DZD: 冀中平原，大风多发
   - SJP-GYX: 石家庄南，冬季冰雪
   - XTD-HDD: 冀南地区，气候多变

4. 列车运行特点
   - G字头列车：高速动车组
   - 平均停站：5-9个站
   - 典型停站时间：2-3分钟
   - 追踪间隔：3-5分钟
""",
            "调度约束": """
## 高铁调度核心约束

1. 时间约束
   - 到发时间关系：到达时间 + 停站时间 ≤ 发车时间
   - 最小停站时间：一般2分钟（作业时间）
   - 最大停站时间：一般不超过10分钟（防止占用站台）

2. 空间约束（安全间隔）
   - 同向追踪间隔：3-5分钟（根据速度）
   - 对向避让间隔：10-15分钟
   - 进站间隔：2-3分钟
   - 出站间隔：2-3分钟

3. 容量约束
   - 车站股道容量：每站2-4条到发线
   - 站台占用：每条股道同时只能一列车停靠
   - 区间通过能力：每小时12-20列

4. 列车运行时间
   - 最小运行时间：必须满足区间运行时间
   - 图定运行时间：一般比最小时间长10-20%
   - 临时限速：根据限速值调整运行时间

5. 调整原则
   - 优先级：G1次>G2次>G3次（车次号越小优先级越高）
   - 高速列车优先：>G字头>D字头
   - 正点优先：已延误列车的调整幅度受限
   - 安全优先：所有调整不得违反安全约束
""",
            "延误处理": """
## 延误处理策略

1. 延误分类
   - 轻微延误：<10分钟，简单调整
   - 中等延误：10-30分钟，重点调整
   - 严重延误：>30分钟，优先恢复

2. 延误恢复策略
   - 顺延调整：延误列车后续到发时间顺延
   - 压缩停站：适当缩短停站时间（不低于最小停站时间）
   - 改变越行：调整越行关系，优先正点列车
   - 侧线避让：延误列车在侧线避让后续列车

3. 延误传播控制
   - 识别关键列车：延误可能触发连锁延误的列车
   - 设置缓冲：在关键节点设置时间缓冲
   - 分段调整：将大范围延误分段处理

4. 风险提示
   - 延误超过30分钟：可能触发退票、改签等操作
   - 延误超过60分钟：可能触发备用列车、停运等措施
   - 多车延误：需防范延误连锁反应
""",
            "车站作业": """
## 车站作业时间标准

1. 停站作业时间
   - 高速列车（G字头）：2-3分钟
   - 动车组（D字头）：3-5分钟
   - 普速列车：5-8分钟

2. 最小停站时间
   - 旅客乘降：2分钟
   - 机外停车：1分钟
   - 技术停车：5分钟

3. 作业类型
   - 旅客乘降：常规停站
   - 技术作业：列车检查、换端等
   - 机外停车：避让、待避等
                   - 越行作业：快速列车越行慢速列车
"""
        }

        # 从文件加载额外知识
        knowledge.update(self._load_knowledge_files())

        return knowledge

    def _load_knowledge_files(self) -> Dict[str, str]:
        """
        从知识库目录加载额外的知识文件
        支持从operations/、rules/、reference/子目录加载
        """
        file_knowledge = {}

        # 定义要加载的文件（支持子目录）
        knowledge_files = {
            "reference/station_knowledge.txt": "车站知识",
            "reference/timetable_knowledge.txt": "时刻表知识",
            "rules/operational_rules.txt": "操作规则"
        }

        for filepath, key in knowledge_files.items():
            full_path = os.path.join(self.knowledge_dir, filepath)
            if os.path.exists(full_path):
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        file_knowledge[key] = f.read()
                    logger.info(f"成功加载知识库文件: {filepath}")
                except Exception as e:
                    logger.warning(f"加载知识库文件失败 {filepath}: {e}")

        # 加载operations/目录下的JSON操作知识库
        operations_dir = os.path.join(self.knowledge_dir, "operations")
        if os.path.exists(operations_dir):
            json_knowledge = self._load_operations_json(operations_dir)
            file_knowledge.update(json_knowledge)

        return file_knowledge

    def _load_operations_json(self, operations_dir: str) -> Dict[str, str]:
        """
        从operations目录递归加载JSON格式的调度员操作知识库（支持子文件夹层级）

        Args:
            operations_dir: operations目录路径

        Returns:
            Dict[str, str]: 解析后的知识库字典
        """
        operations_knowledge = {}
        loaded_files = 0

        try:
            for root, _, files in os.walk(operations_dir):
                for filename in files:
                    if not filename.endswith('.json'):
                        continue
                    filepath = os.path.join(root, filename)
                    rel_path = os.path.relpath(filepath, operations_dir)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            data = json.load(f)

                        # 解析JSON知识库（支持 scenes 数组或单场景结构）
                        scenes = data.get('scenes', [])
                        if not scenes and 'scene_id' in data:
                            scenes = [data]

                        # 提取场景名称作为key
                        for scene in scenes:
                            scene_id = scene.get('scene_id', 'unknown')
                            scene_id_name = scene.get('scene_name', scene_id)
                            # 注入来源路径，方便追溯
                            scene['_source_path'] = rel_path
                            operations_knowledge[scene_id_name] = self._format_scene_knowledge(scene)

                        loaded_files += 1
                    except Exception as e:
                        logger.warning(f"加载操作知识库失败 {rel_path}: {e}")
            logger.info(f"成功加载操作知识库: {loaded_files} 个文件，共 {len(operations_knowledge)} 个场景")
        except Exception as e:
            logger.warning(f"读取operations目录失败: {e}")

        return operations_knowledge

    def _format_scene_knowledge(self, scene: Dict) -> str:
        """
        将JSON场景格式化为可检索的知识文本

        Args:
            scene: 场景字典

        Returns:
            str: 格式化后的知识文本
        """
        lines = []
        lines.append(f"## 场景: {scene.get('scene_name', '未知')}")
        lines.append(f"类别: {scene.get('category', '未知')}")
        lines.append(f"场景ID: {scene.get('scene_id', '未知')}")

        # 关键词
        keywords = scene.get('keywords', {})
        if keywords:
            primary = keywords.get('primary', [])
            secondary = keywords.get('secondary', [])
            lines.append(f"关键词: {', '.join(primary + secondary)}")

        # 触发条件
        trigger_conditions = scene.get('trigger_conditions', [])
        if trigger_conditions:
            lines.append("触发条件:")
            for tc in trigger_conditions:
                field = tc.get('field', '')
                op = tc.get('op', '')
                value = tc.get('value', '')
                unit = tc.get('unit', '')
                lines.append(f"  - {field} {op} {value} {unit}")

        # 操作步骤
        operations = scene.get('operations', [])
        if operations:
            lines.append("\n操作步骤:")
            for op in operations:
                step_id = op.get('step_id', '')
                phase = op.get('phase', '')
                title = op.get('title', '')
                time_limit = op.get('time_limit', '')
                actions = op.get('actions', [])

                lines.append(f"\n[{step_id}] {title}")
                lines.append(f"  阶段: {phase} | 时限: {time_limit}")
                for action in actions:
                    lines.append(f"    - {action}")

        # 验证项
        lines.append("\n验证要点:")
        for op in operations:
            verification = op.get('verification', [])
            for v in verification:
                lines.append(f"  - {v}")

        # 参考规章
        references = scene.get('references', [])
        if references:
            lines.append("\n参考规章:")
            for ref in references:
                lines.append(f"  - {ref}")

        return "\n".join(lines)

    def retrieve(self, query: str, top_k: int = 3) -> List[Dict[str, str]]:
        """
        检索相关知识

        Args:
            query: 查询文本
            top_k: 返回结果数量

        Returns:
            List[Dict]: 检索到的知识文档列表
        """
        results = []
        query_lower = query.lower()

        # 计算每个知识的相关性分数
        scores = []
        for key, content in self.knowledge_base.items():
            score = self._calculate_relevance(query_lower, content)
            if score > 0:
                scores.append((key, content, score))

        # 按分数排序
        scores.sort(key=lambda x: x[2], reverse=True)

        # 返回top_k结果
        for key, content, score in scores[:top_k]:
            results.append({
                "key": key,
                "content": content,
                "score": score,
                "metadata": {
                    "source": "domain_knowledge_base",
                    "type": "static_knowledge"
                }
            })

        return results

    def _calculate_relevance(self, query: str, content: str) -> float:
        """
        计算查询与知识的相关性分数

        Args:
            query: 查询文本（小写）
            content: 知识内容

        Returns:
            float: 相关性分数
        """
        score = 0.0
        content_lower = content.lower()

        # 关键词匹配
        keywords = ["临时限速", "突发故障", "区间封锁", "mip", "fcfs",
                   "延误", "调度", "车站", "区间", "列车", "限速", "故障"]
        for kw in keywords:
            if kw in query:
                if kw in content_lower:
                    score += 1.0

        # 场景类型匹配
        if "临时限速" in query and "临时限速" in content_lower:
            score += 2.0
        if "突发故障" in query and "突发故障" in content_lower:
            score += 2.0
        if "区间封锁" in query and "区间封锁" in content_lower:
            score += 2.0

        # 求解器匹配
        if "mip" in query and "mip_scheduler" in content_lower:
            score += 2.0
        if "fcfs" in query and "fcfs_scheduler" in content_lower:
            score += 2.0

        # 车站匹配
        station_codes = ["bjx", "djk", "zbd", "gbd", "xsd", "bdd",
                        "dzd", "zdj", "sjp", "gyx", "xtd", "hdd", "ayd"]
        for code in station_codes:
            if code in query and code in content_lower:
                score += 1.5

        return score

    def format_prompt_with_knowledge(self, base_prompt: str, query: str) -> str:
        """
        将检索到的知识注入到prompt中

        Args:
            base_prompt: 基础prompt
            query: 查询文本

        Returns:
            str: 增强后的prompt
        """
        # 检索相关知识
        documents = self.retrieve(query, top_k=3)

        if not documents:
            return base_prompt

        # 格式化知识
        knowledge_parts = ["\n相关领域知识：\n"]
        for i, doc in enumerate(documents, 1):
            knowledge_parts.append(f"【知识{i}】")
            knowledge_parts.append(doc["content"])
            knowledge_parts.append("\n")

        # 将知识插入到prompt中
        # 策略：在问题描述后插入知识
        if "描述：" in base_prompt:
            parts = base_prompt.split("描述：", 1)
            if len(parts) == 2:
                # 先提取 split 结果，避免在 f-string 中使用反斜杠
                second_part_split = parts[1].split('\n\n', 1)
                enhanced_prompt = f"{parts[0]}描述：{second_part_split[0]}\n"
                enhanced_prompt += "\n".join(knowledge_parts)
                if len(second_part_split) > 1:
                    enhanced_prompt += "\n\n" + second_part_split[1]
                return enhanced_prompt

        # 默认：在开头插入知识
        return "\n".join(knowledge_parts) + "\n\n" + base_prompt


    def retrieve_dispatcher_guide(
        self,
        accident_card: Dict[str, Any],
        top_k: int = 1
    ) -> List[Dict[str, Any]]:
        """
        检索调度员操作指南（基于关键词匹配）

        Args:
            accident_card: 事故卡片数据
            top_k: 返回结果数量

        Returns:
            List[Dict]: 检索到的调度员操作指南
        """
        # 从 layer1_data_modeling 导入 DispatcherOperationGuideRetriever
        from railway_agent.workflow.layer1_data_modeling import DispatcherOperationGuideRetriever

        guide_retriever = DispatcherOperationGuideRetriever()

        # 提取场景类别和故障类型
        scene_category = accident_card.get("scene_category", "")
        fault_type = accident_card.get("fault_type", "")
        user_input = accident_card.get("raw_input", "")

        # 检索操作指南
        guide = guide_retriever.retrieve_operations(scene_category, fault_type, user_input)

        if guide:
            # 转换为统一格式
            return [{
                "scene_name": guide["scene_name"],
                "operations": guide["operations"],
                "source": guide["source"],
                "relevance_score": guide["match_score"]
            }]
        return []

    def format_prompt_with_dispatcher_guide(
        self,
        base_prompt: str,
        accident_card: Dict[str, Any]
    ) -> str:
        """
        将调度员操作指南注入到prompt中

        Args:
            base_prompt: 基础prompt
            accident_card: 事故卡片数据

        Returns:
            str: 增强后的prompt
        """
        guides = self.retrieve_dispatcher_guide(accident_card, top_k=1)

        if not guides:
            return base_prompt

        guide = guides[0]

        # 构建操作指南文本（按step分组，减少prompt长度）
        guide_parts = ["\n" + "=" * 60]
        guide_parts.append("【调度员操作指南】")
        guide_parts.append(f"场景: {guide['scene_name']}")
        guide_parts.append(f"匹配度: {guide['relevance_score']:.1f}")
        guide_parts.append("-" * 60)

        # 添加操作步骤（支持新step结构或旧operations结构）
        steps = guide.get('steps', [])
        if steps:
            for step in steps:
                phase = step.get('phase', '')
                actions = step.get('actions', [])
                if not actions:
                    continue
                guide_parts.append(f"\n▶ {phase}")
                for action in actions:
                    guide_parts.append(f"  - {action}")
        else:
            # 兼容旧结构
            for i, operation in enumerate(guide.get('operations', []), 1):
                guide_parts.append(f"{i}. {operation}")

        guide_parts.append("")
        guide_parts.append(f"来源: {guide['source']}")
        guide_parts.append("=" * 60 + "\n")

        # 将操作指南插入到prompt中
        guide_text = "\n".join(guide_parts)

        # 策略：在prompt末尾插入操作指南
        if "请输出JSON格式" in base_prompt:
            parts = base_prompt.split("请输出JSON格式", 1)
            return parts[0] + guide_text + "\n请输出JSON格式" + (parts[1] if len(parts) > 1 else "")

        return base_prompt + "\n" + guide_text


# 全局实例
_rag_retriever: Optional[RAGRetriever] = None


def get_retriever() -> RAGRetriever:
    """获取全局RAG检索器实例"""
    global _rag_retriever
    if _rag_retriever is None:
        _rag_retriever = RAGRetriever()
    return _rag_retriever
