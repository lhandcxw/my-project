# -*- coding: utf-8 -*-
"""
领域知识RAG检索器
用于为LLM提供铁路调度领域的专业知识

当前实现：简单的关键词匹配
后续可升级为向量检索（使用embedding模型）
"""

import os
import re
from typing import List, Dict, Optional


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
        self.knowledge_base = self._load_knowledge()

    def _ensure_knowledge_dir(self):
        """确保知识库目录存在"""
        if not os.path.exists(self.knowledge_dir):
            os.makedirs(self.knowledge_dir, exist_ok=True)

    def _load_knowledge(self) -> Dict[str, str]:
        """加载领域知识库"""
        return {
            "场景类型": """
## 场景类型定义

1. 临时限速 (TEMPORARY_SPEED_LIMIT)
   - 原因：暴雨、大风、冰雪等天气因素
   - 影响：区间内列车需减速运行
   - 典型位置：XSD-BDD区间、BDD-DZD区间
   - 推荐求解器：mip_scheduler（优化调整）

2. 突发故障 (SUDDEN_FAILURE)
   - 原因：设备故障、线路异常等
   - 影响：列车延误或停运
   - 典型位置：任一区间或车站
   - 推荐求解器：fcfs_scheduler（快速响应）

3. 区间封锁 (SECTION_INTERRUPT)
   - 原因：严重事故、施工等
   - 影响：区间内列车无法运行
   - 推荐求解器：noop_scheduler（仅记录不调度）
""",
            "求解器选择": """
## 求解器选择规则

1. mip_scheduler (混合整数规划)
   - 适用场景：临时限速、优化调整
   - 优点：全局最优、考虑多目标
   - 缺点：计算时间长
   - 规模限制：建议50列以内

2. fcfs_scheduler (先到先服务)
   - 适用场景：突发故障、快速响应
   - 优点：计算快速、简单可靠
   - 缺点：不考虑全局优化
   - 规模限制：无（可处理大规模）

3. max_delay_first_scheduler (最大延误优先)
   - 适用场景：延误传播、优先级调度
   - 优点：减少最大延误
   - 缺点：可能产生更多延误列车
   - 适用场景：高延误场景

4. noop_scheduler (空操作)
   - 适用场景：区间封锁、无需调度
   - 用途：记录故障信息，不执行调度
""",
            "调度规则": """
## 铁路调度基本规则

1. 追踪间隔
   - 同向追踪间隔：通常8-10分钟
   - 区间闭塞：必须确认区段空闲

2. 优先级别
   - 动车组 > 普通列车
   - 大站直达 > 普通停靠
   - 高等级车次优先

3. 车站会让
   - 优先使用有避让线的车站
   - 考虑列车等级和延误情况

4. 限速要求
   - 临时限速：通常60-120km/h
   - 限速区段需重新计算运行时分
""",
            "车站信息": """
## 车站数据（京广高铁北京-安阳段）

北京西(BJX) → 杜家坎线路所(DJK) → 涿州东(ZBD) → 高碑店东(GBD) → 徐水东(XSD) → 保定东(BDD) → 定州东(DZD) → 正定机场(ZDJ) → 石家庄(SJP) → 高邑西(GYX) → 邢台东(XTD) → 邯郸东(HDD) → 安阳东(AYD)

共13个站点，包含1个线路所（杜家坎）。
"""
        }

    def retrieve(self, query: str, top_k: int = 2) -> str:
        """
        根据查询检索相关知识

        Args:
            query: 用户查询
            top_k: 返回最相关的top_k条知识

        Returns:
            str: 检索到的领域知识
        """
        # 简单的关键词匹配
        query_lower = query.lower()

        # 计算每个知识库条目与查询的相关性
        relevance_scores = {}
        for key, content in self.knowledge_base.items():
            score = 0
            # 检查关键词匹配
            if '限速' in query or 'speed' in query_lower:
                if '限速' in content:
                    score += 3
            if '故障' in query or 'failure' in query_lower:
                if '故障' in content:
                    score += 3
            if '封锁' in query or 'interrupt' in query_lower:
                if '封锁' in content:
                    score += 3
            if '求解器' in query or 'solver' in query_lower:
                if '求解器' in content:
                    score += 3
            if '调度' in query or 'dispatch' in query_lower:
                if '调度' in content:
                    score += 2
            if '车站' in query or 'station' in query_lower:
                if '车站' in content:
                    score += 2
            # 默认匹配度
            if score == 0:
                score = 1
            relevance_scores[key] = score

        # 按相关性排序
        sorted_keys = sorted(relevance_scores.items(), key=lambda x: x[1], reverse=True)
        top_keys = [k for k, _ in sorted_keys[:top_k]]

        # 拼接检索到的知识
        retrieved = "\n\n".join([self.knowledge_base[k] for k in top_keys])

        return retrieved

    def format_prompt_with_knowledge(self, base_prompt: str, query: str) -> str:
        """
        将检索到的知识格式化到prompt中

        Args:
            base_prompt: 基础prompt
            query: 用户查询

        Returns:
            str: 包含领域知识的完整prompt
        """
        knowledge = self.retrieve(query)
        return f"""【领域知识】
{knowledge}

【用户请求】
{base_prompt}

请根据以上领域知识，结合用户请求进行推理和决策。"""


# 全局RAG检索器实例
_default_retriever = None


def get_retriever() -> RAGRetriever:
    """获取默认的RAG检索器实例"""
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = RAGRetriever()
    return _default_retriever