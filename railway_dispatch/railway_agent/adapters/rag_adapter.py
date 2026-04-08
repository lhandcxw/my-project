# -*- coding: utf-8 -*-
"""
RAG 适配器
统一 RAG 检索接口
"""

from typing import Optional, List, Dict, Any
import logging

from railway_agent.rag_retriever import get_retriever, RAGRetriever

logger = logging.getLogger(__name__)


class RAGAdapter:
    """
    RAG 适配器
    封装 RAG 检索，提供统一的接口
    """
    
    def __init__(self):
        self._rag_retriever: Optional[RAGRetriever] = None
    
    def _get_rag_retriever(self) -> RAGRetriever:
        """获取 RAG 检索器"""
        if self._rag_retriever is None:
            self._rag_retriever = get_retriever()
        return self._rag_retriever
    
    def retrieve(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        检索相关知识

        Args:
            query: 查询文本
            top_k: 返回结果数量

        Returns:
            List[Dict]: 检索到的知识文档列表
        """
        rag = self._get_rag_retriever()
        try:
            return rag.retrieve(query, top_k)
        except Exception as e:
            logger.error(f"RAG 检索失败: {e}")
            return []
    
    def format_prompt_with_knowledge(
        self,
        base_prompt: str,
        query: str,
        max_knowledge_length: int = 1000
    ) -> str:
        """
        将知识注入到 prompt 中

        Args:
            base_prompt: 基础 prompt
            query: 查询文本
            max_knowledge_length: 最大知识长度

        Returns:
            str: 增强后的 prompt
        """
        rag = self._get_rag_retriever()
        try:
            enhanced_prompt = rag.format_prompt_with_knowledge(base_prompt, query)

            # 限制知识长度，避免prompt过长
            if len(enhanced_prompt) > max_knowledge_length * 2:
                # 截断过长的知识
                if "相关领域知识：" in enhanced_prompt:
                    base_part, knowledge_part = enhanced_prompt.split("相关领域知识：", 1)
                    # 保留知识标题，截断内容
                    knowledge_lines = knowledge_part.split('\n')
                    truncated_knowledge = ["相关领域知识："]
                    total_length = len(base_part) + len(truncated_knowledge[0])

                    for line in knowledge_lines[1:]:
                        if total_length + len(line) > max_knowledge_length:
                            break
                        truncated_knowledge.append(line)
                        total_length += len(line)

                    enhanced_prompt = base_part + "\n".join(truncated_knowledge)
                    if len(knowledge_lines) > len(truncated_knowledge):
                        enhanced_prompt += "\n... (知识已截断)"

            return enhanced_prompt
        except Exception as e:
            logger.error(f"RAG prompt 增强失败: {e}")
            return base_prompt


# 全局实例
_rag_adapter: Optional[RAGAdapter] = None


def get_rag_adapter() -> RAGAdapter:
    """获取 RAG 适配器实例"""
    global _rag_adapter
    if _rag_adapter is None:
        _rag_adapter = RAGAdapter()
    return _rag_adapter