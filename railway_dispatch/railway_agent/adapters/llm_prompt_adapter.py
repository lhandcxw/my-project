# -*- coding: utf-8 -*-
"""
LLM Prompt适配器
连接Prompt管理器和LLM调用器，提供统一的Prompt调用接口
为微调前的大模型提供结构化prompt支持
"""

import logging
import time
from typing import Dict, List, Optional, Any
import json

from models.prompts import (
    PromptContext,
    PromptRequest,
    PromptResponse,
    FineTuningSample
)
from railway_agent.prompts import get_prompt_manager
from railway_agent.adapters.llm_adapter import get_llm_caller
from railway_agent.rag_retriever import get_retriever

logger = logging.getLogger(__name__)


class LLMPromptAdapter:
    """
    LLM Prompt适配器
    负责Prompt的填充、LLM调用、结果解析和验证
    """

    def __init__(self):
        """初始化LLM Prompt适配器"""
        self.prompt_manager = get_prompt_manager()
        self.llm_caller = None
        self.rag_retriever = None

    def _get_llm_caller(self):
        """获取LLM调用器"""
        if self.llm_caller is None:
            self.llm_caller = get_llm_caller()
        return self.llm_caller

    def _get_rag_retriever(self):
        """获取RAG检索器"""
        if self.rag_retriever is None:
            try:
                self.rag_retriever = get_retriever()
            except Exception as e:
                logger.warning(f"RAG检索器初始化失败: {e}")
        return self.rag_retriever

    def execute_prompt(
        self,
        template_id: str,
        context: PromptContext,
        enable_rag: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        collect_sample: bool = False
    ) -> PromptResponse:
        """
        执行Prompt调用

        Args:
            template_id: 模板ID
            context: Prompt上下文
            enable_rag: 是否启用RAG
            temperature: 温度参数（可选，覆盖模板默认值）
            max_tokens: 最大token数（可选，覆盖模板默认值）
            collect_sample: 是否收集微调样本

        Returns:
            PromptResponse: Prompt响应
        """
        start_time = time.time()

        # 获取模板
        template = self.prompt_manager.get_template(template_id)
        if template is None:
            return PromptResponse(
                request_id=context.request_id,
                template_id=template_id,
                raw_response="",
                is_valid=False,
                validation_errors=[f"模板不存在: {template_id}"],
                error=f"模板不存在: {template_id}",
                error_type="TEMPLATE_NOT_FOUND",
                timestamp=_now_iso()
            )

        # 获取模型参数
        temp = temperature if temperature is not None else template.temperature
        max_tok = max_tokens if max_tokens is not None else template.max_tokens

        # RAG检索
        rag_knowledge = []
        rag_documents = []
        if enable_rag:
            rag = self._get_rag_retriever()
            if rag:
                query = context.user_input or context.scene_type or ""
                rag_documents = rag.retrieve(query, top_k=3)
                rag_knowledge = [doc.get('content', '') for doc in rag_documents]
                context.rag_documents = rag_documents

        # 填充Prompt
        try:
            filled_prompt = self.prompt_manager.fill_template(
                template_id=template_id,
                context=context,
                enable_rag=enable_rag,
                rag_knowledge=rag_knowledge
            )
        except Exception as e:
            logger.error(f"Prompt填充失败: {e}")
            return PromptResponse(
                request_id=context.request_id,
                template_id=template_id,
                raw_response="",
                is_valid=False,
                validation_errors=[f"Prompt填充失败: {str(e)}"],
                error=f"Prompt填充失败: {str(e)}",
                error_type="PROMPT_FILL_ERROR",
                timestamp=_now_iso()
            )

        # 调用LLM
        try:
            llm = self._get_llm_caller()
            raw_response, response_type = llm.call(filled_prompt, max_tokens=max_tok, temperature=temp)

            # 记录响应类型
            model_used = f"{response_type} ({template.model_name or 'default'})"

        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            return PromptResponse(
                request_id=context.request_id,
                template_id=template_id,
                raw_response="",
                is_valid=False,
                validation_errors=[f"LLM调用失败: {str(e)}"],
                error=f"LLM调用失败: {str(e)}",
                error_type="LLM_CALL_ERROR",
                prompt_used=filled_prompt,
                timestamp=_now_iso()
            )

        # 解析响应
        parsed_output = self._parse_json_response(raw_response)

        # 验证输出
        is_valid, validation_errors = self.prompt_manager.validate_output(template_id, parsed_output)

        # 计算响应时间
        response_time = (time.time() - start_time) * 1000

        # 构建响应
        response = PromptResponse(
            request_id=context.request_id,
            template_id=template_id,
            raw_response=raw_response,
            parsed_output=parsed_output,
            structured_output=parsed_output,  # 简化处理，暂不区分
            is_valid=is_valid,
            validation_errors=validation_errors,
            model_used=model_used,
            response_time_ms=response_time,
            prompt_used=filled_prompt,
            rag_documents_used=[doc.get('content', '') for doc in rag_documents],
            timestamp=_now_iso()
        )

        # 收集微调样本
        if collect_sample and is_valid:
            self._collect_sample(template_id, context, parsed_output)

        return response

    def execute_with_fallback(
        self,
        template_id: str,
        context: PromptContext,
        fallback_output: Dict[str, Any],
        **kwargs
    ) -> PromptResponse:
        """
        执行Prompt，失败时使用回退输出

        Args:
            template_id: 模板ID
            context: Prompt上下文
            fallback_output: 回退输出
            **kwargs: 其他参数

        Returns:
            PromptResponse: Prompt响应
        """
        response = self.execute_prompt(template_id, context, **kwargs)

        if not response.is_valid or response.error:
            logger.warning(f"Prompt执行失败，使用回退输出: {template_id}")
            response.parsed_output = fallback_output
            response.structured_output = fallback_output
            response.is_valid = True
            response.validation_errors = []
            response.error = None
            response.error_type = None

        return response

    def _parse_json_response(self, response: str) -> Dict[str, Any]:
        """
        从LLM响应中解析JSON

        Args:
            response: LLM原始响应

        Returns:
            Dict: 解析后的字典
        """
        # 尝试从markdown代码块提取
        if '```json' in response:
            json_str = response.split('```json')[1].split('```')[0]
            try:
                return json.loads(json_str.strip())
            except:
                pass

        if '```' in response:
            json_str = response.split('```')[1].split('```')[0]
            try:
                return json.loads(json_str.strip())
            except:
                pass

        # 尝试直接解析
        if '{' in response and '}' in response:
            start = response.find('{')
            end = response.rfind('}')
            if start >= 0 and end > start:
                json_str = response[start:end+1]
                try:
                    return json.loads(json_str)
                except:
                    pass

        # 解析失败，返回空字典
        logger.warning(f"无法解析JSON响应: {response[:200]}...")
        return {}

    def _collect_sample(
        self,
        template_id: str,
        context: PromptContext,
        output: Dict[str, Any]
    ):
        """
        收集微调样本

        Args:
            template_id: 模板ID
            context: 输入上下文
            output: 输出
        """
        try:
            self.prompt_manager.collect_fine_tuning_sample(
                template_id=template_id,
                context=context,
                expected_output=output,
                model_output=output
            )
        except Exception as e:
            logger.warning(f"收集微调样本失败: {e}")

    def export_samples(self, filepath: str):
        """
        导出微调样本

        Args:
            filepath: 导出文件路径
        """
        self.prompt_manager.export_fine_tuning_samples(filepath)


def _now_iso() -> str:
    """获取当前ISO格式时间"""
    from datetime import datetime
    return datetime.now().isoformat()


# 全局实例
_llm_prompt_adapter: Optional[LLMPromptAdapter] = None


def get_llm_prompt_adapter() -> LLMPromptAdapter:
    """获取全局LLM Prompt适配器实例"""
    global _llm_prompt_adapter
    if _llm_prompt_adapter is None:
        _llm_prompt_adapter = LLMPromptAdapter()
    return _llm_prompt_adapter
