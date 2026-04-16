# -*- coding: utf-8 -*-
"""
LLM Prompt适配器
连接Prompt管理器和LLM调用器，提供统一的Prompt调用接口
为微调前的大模型提供结构化prompt支持
"""

import logging
import time
from typing import Dict, List, Optional, Any, Union
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

        # RAG检索 - 根据模板ID优化查询策略
        rag_knowledge = []
        rag_documents = []
        if enable_rag:
            rag = self._get_rag_retriever()
            if rag:
                # 根据模板ID构建更精准的查询
                queries = self._build_rag_queries(template_id, context)
                all_documents = []
                for query in queries:
                    docs = rag.retrieve(query, top_k=2)
                    all_documents.extend(docs)
                # 去重
                seen = set()
                for doc in all_documents:
                    content = doc.get('content', '')
                    if content not in seen:
                        seen.add(content)
                        rag_documents.append(doc)
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
        raw_response = ""
        response_type = "unknown"
        parsed_output = {}
        model_used = "unknown"

        try:
            llm = self._get_llm_caller()
            raw_response, response_type = llm.call(filled_prompt, max_tokens=max_tok, temperature=temp)

            # 记录响应类型 - 明确显示模型名称
            model_used = f"{response_type}"

            # 记录原始响应用于调试（显示完整响应）
            logger.debug(f"[LLM响应] {template_id}: 原始响应长度={len(raw_response)}, 完整响应={raw_response}")

            # 解析响应（带修复功能的解析器）
            parsed_output = self._parse_json_response(raw_response)

            # 记录解析结果
            if parsed_output:
                logger.debug(f"[LLM解析] {template_id}: 成功解析，包含字段={list(parsed_output.keys())}")
            else:
                logger.error(f"[LLM解析] {template_id}: 解析失败，原始响应={raw_response[:300]}")

            # 检查LLM响应是否为空或解析失败 - 强制要求LLM必须成功
            if not raw_response:
                raise RuntimeError(f"LLM返回空响应 - 模型: {response_type}")
            if not parsed_output:
                raise RuntimeError(f"无法解析LLM响应为JSON - 模型: {response_type}, 原始响应: {raw_response[:500]}")

            # 真实LLM响应，添加标记
            parsed_output["_response_source"] = f"llm_{response_type}"
            parsed_output["_response_note"] = f"【LLM真实响应】模型: {response_type}"
            parsed_output["_is_mock"] = False

        except Exception as e:
            # LLM调用失败时检查FORCE_LLM_MODE配置
            from config import LLMConfig
            if LLMConfig.FORCE_LLM_MODE:
                # 强制LLM模式，抛出异常
                logger.error(f"[LLM调用失败] {template_id}: {str(e)}")
                raise RuntimeError(f"LLM调用失败 ({template_id}): {str(e)}") from e
            else:
                # 非强制模式，返回空结果，让上层使用规则回退
                logger.warning(f"[LLM调用失败] {template_id}: {str(e)}，将使用规则回退")
                return PromptResponse(
                    is_valid=False,
                    raw_response="",
                    parsed_output={},
                    error=str(e),
                    model_used="rule_fallback",
                    fallback_reason="LLM调用失败，使用规则回退"
                )

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
        从LLM响应中解析JSON - 兼容原版逻辑 + 增强修复功能（针对小模型优化）

        Args:
            response: LLM原始响应

        Returns:
            Dict: 解析后的字典
        """
        if not response:
            logger.warning("LLM响应为空")
            return {}

        # 记录原始响应用于调试
        logger.debug(f"LLM原始响应: {response[:500]}...")

        # 方法1: 尝试从 ```json 代码块提取
        if '```json' in response:
            try:
                json_str = response.split('```json')[1].split('```')[0]
                result = json.loads(json_str.strip())
                logger.debug("成功从 ```json 解析JSON")
                return result
            except (json.JSONDecodeError, IndexError):
                pass

        # 方法2: 尝试从 ``` 代码块提取
        if '```' in response:
            try:
                json_str = response.split('```')[1].split('```')[0]
                result = json.loads(json_str.strip())
                logger.debug("成功从 ``` 解析JSON")
                return result
            except (json.JSONDecodeError, IndexError):
                pass

        # 方法3: 直接查找 JSON 对象（处理多个JSON对象的情况，取最大的）
        if '{' in response and '}' in response:
            # 尝试找到所有可能的JSON对象
            results = []
            start = 0
            while True:
                start = response.find('{', start)
                if start == -1:
                    break
                # 找到匹配的结束括号
                end = self._find_matching_brace(response, start)
                if end > start:
                    json_str = response[start:end+1]
                    try:
                        result = json.loads(json_str)
                        results.append((len(json_str), result))
                        logger.debug(f"成功解析JSON对象，长度: {len(json_str)}")
                    except json.JSONDecodeError:
                        # 尝试修复后重新解析
                        fixed = self._try_fix_json(json_str)
                        if fixed:
                            try:
                                result = json.loads(fixed)
                                results.append((len(json_str), result))
                                logger.debug(f"成功从修复后的JSON解析，长度: {len(json_str)}")
                            except:
                                pass
                start += 1

            # 返回最大的有效JSON对象
            if results:
                results.sort(reverse=True, key=lambda x: x[0])
                logger.debug(f"选择最大的JSON对象，长度: {results[0][0]}")
                return results[0][1]

        # 方法4: 尝试使用键值对解析（针对完全不标准的输出）
        kv_result = self._parse_key_value_format(response)
        if kv_result:
            logger.debug("成功从键值对格式解析")
            return kv_result

        logger.warning(f"无法解析JSON响应: {response[:300]}...")
        return {}

    def _find_matching_brace(self, text: str, start: int) -> int:
        """
        找到匹配的大括号位置

        Args:
            text: 文本
            start: 起始大括号位置

        Returns:
            int: 匹配的大括号位置，未找到返回-1
        """
        count = 0
        in_string = False
        string_char = None
        i = start
        while i < len(text):
            char = text[i]
            if not in_string:
                if char in '"\'':
                    in_string = True
                    string_char = char
                elif char == '{':
                    count += 1
                elif char == '}':
                    count -= 1
                    if count == 0:
                        return i
            else:
                if char == string_char and text[i-1] != '\\':
                    in_string = False
                    string_char = None
            i += 1
        return -1

    def _try_fix_json(self, json_str: str) -> Optional[str]:
        """
        尝试修复常见的JSON格式问题

        Args:
            json_str: 待修复的JSON字符串

        Returns:
            修复后的JSON字符串，如果无法修复则返回None
        """
        import re

        if not json_str:
            return None

        # 修复1: 移除可能的前置文本（如 "Here's the JSON:"）
        lines = json_str.strip().split('\n')
        # 找到第一个包含 { 的行，从该行开始
        start_idx = 0
        for i, line in enumerate(lines):
            if '{' in line:
                start_idx = i
                break
        if start_idx > 0:
            json_str = '\n'.join(lines[start_idx:])

        # 修复2: 处理单引号为双引号（简单情况）
        # 注意：这只是简单的替换，不处理嵌套情况
        if "'" in json_str and '"' not in json_str:
            # 简单替换：但要避免替换中文引号
            json_str = json_str.replace("'", '"')

        # 修复3: 移除尾部逗号
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)

        # 修复4: 处理缺失的引号（键没有引号的情况）
        # 将 {key: 改为 {"key":
        json_str = re.sub(r'\{\s*"?(\w+)"?\s*:', r'{"\1":', json_str)

        # 修复5: 处理嵌套对象中的无引号键（如 "accident_card": {scene_category: ...}）
        # 匹配冒号前的单词，确保它是JSON键
        json_str = re.sub(r',\s*"?(\w+)"?\s*:', r',"\1":', json_str)
        json_str = re.sub(r'\{\s*"?(\w+)"?\s*:', r'{"\1":', json_str)

        # 修复6: 处理中文冒号（全角冒号）
        json_str = json_str.replace('：', ':')

        # 修复7: 处理多余的换行和空格
        json_str = re.sub(r'\n+', ' ', json_str)
        json_str = re.sub(r'\s+', ' ', json_str)

        # 修复8: 移除控制字符
        json_str = re.sub(r'[\x00-\x1F\x7F]', '', json_str)

        # 修复9: 再次尝试修复嵌套对象中的键（递归情况）
        # 处理 "key": {value...} 这种情况
        json_str = re.sub(r':\s*\{\s*(\w+)\s*:', r': {"\1":', json_str)

        # 修复5: 移除多余的空白字符
        json_str = json_str.strip()

        return json_str if json_str else None

    def _parse_key_value_format(self, response: str) -> Optional[Dict[str, Any]]:
        """
        尝试从键值对格式解析JSON - 增强版，支持更多非标准格式

        Args:
            response: LLM响应

        Returns:
            解析后的字典，如果失败则返回None
        """
        import re

        # 方法1: 尝试匹配标准 "key": value 模式
        pattern = r'"(\w+)"\s*:\s*("[^"]*"|[\d.\-]+|\{[^}]*\}|\[[^\]]*\]|true|false|null)'
        matches = re.findall(pattern, response)

        if matches:
            result = {}
            for key, value in matches:
                # 解析值
                if value.startswith('"') and value.endswith('"'):
                    result[key] = value[1:-1]
                elif value == 'true':
                    result[key] = True
                elif value == 'false':
                    result[key] = False
                elif value == 'null':
                    result[key] = None
                else:
                    try:
                        result[key] = int(value)
                    except:
                        try:
                            result[key] = float(value)
                        except:
                            result[key] = value

            if result:
                return result

        # 方法2: 尝试匹配无引号键 key: value 模式（小模型常见）
        # 匹配 键: 值 模式，键由字母数字下划线组成
        pattern2 = r'(?<!\w)(\w+)\s*:\s*("[^"]*"|\[[^\]]*\]|[^,\}\n]+)'
        matches2 = re.findall(pattern2, response)

        if matches2:
            result = {}
            for key, value in matches2:
                value = value.strip()
                # 解析值
                if value.startswith('"') and value.endswith('"'):
                    result[key] = value[1:-1]
                elif value.startswith('[') and value.endswith(']'):
                    # 尝试解析数组
                    try:
                        result[key] = json.loads(value)
                    except:
                        # 简单分割
                        inner = value[1:-1].strip()
                        if inner:
                            result[key] = [v.strip().strip('"') for v in inner.split(',')]
                        else:
                            result[key] = []
                elif value == 'true':
                    result[key] = True
                elif value == 'false':
                    result[key] = False
                elif value == 'null':
                    result[key] = None
                else:
                    try:
                        result[key] = int(value)
                    except:
                        try:
                            result[key] = float(value)
                        except:
                            result[key] = value.strip('"')

            if result:
                logger.debug(f"从无引号键值对解析成功: {list(result.keys())}")
                return result

        # 方法3: 针对L4评估的特殊处理 - 提取关键字段
        if 'llm_summary' in response or 'feasibility_score' in response or 'risk_warnings' in response:
            result = {}
            # 提取llm_summary
            summary_match = re.search(r'["\']?llm_summary["\']?\s*:\s*["\']([^"\']+)["\']?', response)
            if summary_match:
                result['llm_summary'] = summary_match.group(1)
            else:
                result['llm_summary'] = "方案评估完成"

            # 提取feasibility_score
            score_match = re.search(r'["\']?feasibility_score["\']?\s*:\s*([\d.]+)', response)
            if score_match:
                result['feasibility_score'] = float(score_match.group(1))
            else:
                result['feasibility_score'] = 0.8

            # 提取risk_warnings（数组）
            risk_match = re.search(r'["\']?risk_warnings["\']?\s*:\s*(\[[^\]]*\])', response)
            if risk_match:
                try:
                    result['risk_warnings'] = json.loads(risk_match.group(1))
                except:
                    result['risk_warnings'] = []
            else:
                result['risk_warnings'] = []

            # 提取constraint_check（对象）
            constraint_match = re.search(r'["\']?constraint_check["\']?\s*:\s*(\{[^}]*\})', response)
            if constraint_match:
                try:
                    result['constraint_check'] = json.loads(constraint_match.group(1))
                except:
                    result['constraint_check'] = {}
            else:
                result['constraint_check'] = {}

            if result:
                logger.debug(f"从L4响应提取成功: {list(result.keys())}")
                return result

        return None

    def _build_rag_queries(self, template_id: str, context: PromptContext) -> List[str]:
        """
        根据模板ID构建RAG查询

        Args:
            template_id: 模板ID
            context: Prompt上下文

        Returns:
            List[str]: 查询列表
        """
        queries = []
        user_input = context.user_input or ""

        # 基础查询：用户输入
        if user_input:
            queries.append(user_input)

        # 根据模板ID添加特定查询
        if template_id == "l1_data_modeling":
            # L1: 场景识别和实体提取
            if "限速" in user_input or "大风" in user_input or "暴雨" in user_input:
                queries.append("临时限速场景定义 暴雨大风天气")
            elif "故障" in user_input:
                queries.append("突发故障场景定义 设备故障信号故障")
            elif "封锁" in user_input:
                queries.append("区间封锁场景定义")
            queries.append("京广高铁车站代码 SJP BDD")

        elif template_id == "l2_planner":
            # L2: 规划意图识别
            scene_category = context.scene_category or ""
            if scene_category:
                queries.append(f"{scene_category} 求解器选择规则")
            queries.append("调度规划策略 求解器选择")

        elif template_id == "l3_solver_selector":
            # L3: 求解器选择
            scene_category = context.scene_category or ""
            if scene_category == "临时限速":
                queries.append("mip_scheduler 优化求解器")
            elif scene_category == "突发故障":
                queries.append("fcfs_scheduler 快速响应")
            elif scene_category == "区间封锁":
                queries.append("noop_scheduler 区间封锁")
            queries.append("求解器选择规则 适用场景")

        elif template_id == "l4_evaluation":
            # L4: 评估
            queries.append("延误处理策略 风险评估")
            queries.append("调度方案评估标准")

        # 如果没有特定查询，使用通用查询
        if not queries:
            queries.append("铁路调度场景类型定义")

        return queries

    def _generate_mock_response(self, template_id: str, context: PromptContext) -> Dict[str, Any]:
        """
        生成基于规则的模拟响应（当LLM返回空或失败时使用）

        Args:
            template_id: 模板ID
            context: Prompt上下文

        Returns:
            Dict: 模拟响应字典
        """
        import re

        user_input = context.user_input or ""
        user_input_lower = user_input.lower()

        # L1数据建模 - 生成事故卡片
        if template_id == "l1_data_modeling":
            # 推断场景类型
            scene_category = "突发故障"
            if any(kw in user_input for kw in ["限速", "大风", "暴雨", "降雪", "天气"]):
                scene_category = "临时限速"
            elif any(kw in user_input for kw in ["封锁", "中断"]):
                scene_category = "区间封锁"

            # 推断故障类型
            fault_type = "未知"
            if "风" in user_input:
                fault_type = "大风"
            elif "雨" in user_input:
                fault_type = "暴雨"
            elif "雪" in user_input:
                fault_type = "大雪"
            elif "设备" in user_input:
                fault_type = "设备故障"
            elif "信号" in user_input:
                fault_type = "信号故障"

            # 提取列车号
            train_matches = re.findall(r'([GCDZ]\d+)', user_input)
            affected_train_ids = train_matches if train_matches else []

            # 提取车站
            station_to_code = {
                "石家庄": "SJP", "北京西": "BJX", "保定东": "BDD", "定州东": "DZD",
                "徐水东": "XSD", "涿州东": "ZBD", "高碑店东": "GBD", "正定机场": "ZDJ",
                "高邑西": "GYX", "邢台东": "XTD", "邯郸东": "HDD", "安阳东": "AYD"
            }
            location_code = ""
            location_name = ""
            for station_name, code in station_to_code.items():
                if station_name in user_input:
                    location_name = station_name
                    location_code = code
                    break

            # 如果没有找到车站，默认使用SJP
            if not location_code:
                location_code = "SJP"
                location_name = "石家庄"

            # 判断完整性
            is_complete = bool(affected_train_ids) and bool(location_code)
            missing_fields = []
            if not affected_train_ids:
                missing_fields.append("列车号")
            if not location_code:
                missing_fields.append("位置")

            return {
                "accident_card": {
                    "scene_category": scene_category,
                    "fault_type": fault_type,
                    "affected_section": f"{location_code}-{location_code}",
                    "location_code": location_code,
                    "location_name": location_name,
                    "affected_train_ids": affected_train_ids,
                    "is_complete": is_complete,
                    "missing_fields": missing_fields
                }
            }

        # L2规划器
        elif template_id == "l2_planner":
            scene_category = context.scene_category or "突发故障"
            intent_mapping = {
                "临时限速": "recalculate_corridor_schedule",
                "突发故障": "recover_from_disruption",
                "区间封锁": "handle_section_block"
            }
            planning_intent = intent_mapping.get(scene_category, "recover_from_disruption")

            return {
                "planning_intent": planning_intent,
                "问题描述": f"{scene_category}场景，需要调度处理",
                "建议窗口": "SJP"
            }

        # L3求解器选择
        elif template_id == "l3_solver_selector":
            scene_category = context.scene_category or "突发故障"
            solver_mapping = {
                "临时限速": "mip",
                "突发故障": "fcfs",
                "区间封锁": "noop"
            }
            solver = solver_mapping.get(scene_category, "fcfs")

            return {
                "solver": solver,
                "reasoning": f"{scene_category}场景，选择{solver}求解器",
                "solver_config": {"optimization_objective": "min_max_delay"}
            }

        # L4评估
        elif template_id == "l4_evaluation":
            return {
                "llm_summary": "方案评估完成（基于规则生成）",
                "risk_warnings": [],
                "feasibility_score": 0.8,
                "constraint_check": {"时间约束": True, "空间约束": True}
            }

        # 默认响应
        return {"status": "mock_response", "template_id": template_id}

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
