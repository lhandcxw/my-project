# -*- coding: utf-8 -*-
"""
用户意图路由模块

【实现类型】LLM 驱动（主）+ 规则兜底（次）
【实验阶段策略】优先调用 LLM 进行意图分类，仅在 LLM 不可用时回退到规则匹配
【规则部分】_classify_with_rules、_infer_query_type、_extract_entities 为规则兜底方法

将用户输入分类为三种意图：
- dispatch: 调度求解类（延误、故障、限速、封锁等）
- query: 信息查询类（时刻表、列车状态、车站负荷等）
- chat: 知识问答类（高铁运营知识、规章制度等）

设计原则：
1. 轻量级：意图识别只做一次 LLM 调用（低 temperature、短输出）
2. 可回退：LLM 失败时使用规则匹配兜底
3. 实体提取：query 意图同时提取车次号、车站名等关键实体
"""

import json
import logging
import re
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


# ========== 意图识别 Prompt ==========

INTENT_CLASSIFICATION_PROMPT = """你是京广高铁智能调度系统的意图分析专家。请准确分析用户输入的文本，判断其真实意图类别。

## 三种意图类别定义（按优先级排序）

1. **dispatch（调度处理）——最高优先级**
   用户描述了需要调度系统进行处置的场景，包括：列车故障、设备故障、信号故障、接触网故障、天气原因限速/延误、区间封锁、施工、晚点恢复、调整运行图等**需要生成或调整调度方案**的情形。
   关键特征：包含"限速"、"故障"、"封锁"、"延误"、"晚点"、"调整"、"处置"、"方案"、"调度"等处置性词汇，或要求系统给出操作建议。
   示例："G1563在石家庄站因大风限速，预计延误15分钟"、"保定东站设备故障，请给出处置建议"、"D1234晚点20分钟，帮我调整后续列车"

2. **query（信息查询）**
   用户希望获取**客观数据或实时状态**，不包含要求生成调度方案的诉求。如列车时刻表、列车当前运行位置/状态、车站负荷统计、线路密度、延误传播分析等。
   关键特征：包含"查询"、"查一下"、"看看"、"状态"、"时刻表"、"负荷"、"密度"、"分析"等询问性词汇。
   示例："查询G1563的时刻表"、"G1565目前运行状态"、"石家庄站当前列车密度"、"分析一下延误传播情况"
   **注意**：如果用户在查询后附加了"请调整"、"怎么处理"等处置诉求，则 intent 应为 dispatch。

3. **chat（知识问答/对话）**
   用户咨询高铁运营相关的**专业知识、规章制度、概念解释**，或进行一般性交流（问候、感谢、确认等）。
   关键特征：询问"是什么"、"为什么"、"技术标准"、"规定"等理论性问题，或纯社交性对话。
   示例："高铁追踪间隔的技术标准是多少"、"什么是区间封锁"、"你好"、"谢谢"

## 边界场景处理规则

- **混合意图**：如果用户输入同时包含查询和调度诉求（如"查一下G1563状态，然后给我调整方案"），优先判定为 **dispatch**。
- **否定/条件句**：如果用户说"不需要调度"、"不用调整"，仅为信息确认，判定为 **query** 或 **chat**。
- **模糊场景**：如果用户仅描述现象但未明确要求处置（如"G1563好像晚点了"），先判定为 **query**（查询确认），confidence 不超过 0.7。
- **单字/无意义输入**：判定为 **chat**，confidence 设为 0.5。

## 输出要求（仅输出纯JSON，禁止包含任何其他文字）

{
    "intent": "dispatch|query|chat",
    "confidence": 0.0-1.0,
    "query_type": "timetable|status|station_load|delay_propagation|line_overview|solver_comparison|unknown",
    "entities": {
        "train_id": "提取的车次号，如G1563",
        "station_name": "提取的车站名称",
        "station_code": "提取的车站编码",
        "delay_minutes": "延误分钟数"
    },
    "reasoning": "简要说明判断依据（不超过30字）"
}

约束条件：
- query_type 仅在 intent=query 时填写有效值，根据用户查询的具体内容选择最匹配的类型；其余情况统一填 unknown
- entities 中缺失的字段必须置为 null，不得省略字段
- confidence 应真实反映判断确定性：明确意图≥0.85，混合或模糊意图0.6-0.84，不确定≤0.59
- 仅输出JSON格式内容，禁止添加解释性文字、markdown代码块标记或注释"""


# ========== 规则回退关键词 ==========

QUERY_KEYWORDS = [
    "查询", "查一下", "看看", "时刻表", "几点到", "几点开",
    "位置", "到哪了", "经停", "停靠",
    "负荷", "密度", "高峰", "有多少", "有几个", "列表"
]

CHAT_KEYWORDS = [
    "你好", "您好", "谢谢", "再见", "什么是", "为什么",
    "怎么", "多少", "吗？", "呢？", "吗?", "呢?",
    "介绍一下", "解释一下", "科普"
]

TRAIN_ID_PATTERN = re.compile(r'[GDCgdc]\d{1,4}')
STATION_NAMES = [
    "北京西", "涿州东", "高碑店东", "保定东站", "保定东", "定州东", "石家庄站", "石家庄", "高邑西",
    "邢台东", "邯郸东", "安阳东"
]


class IntentRouter:
    """
    用户意图路由器
    
    【实现类型】LLM 驱动（主）+ 规则兜底（次）
    使用 LLM 进行意图分类，失败时回退到规则匹配。
    """

    def __init__(self, llm_adapter=None):
        """
        初始化意图路由器
        
        Args:
            llm_adapter: LLM 适配器实例，为 None 时延迟加载
        """
        self._llm_adapter = llm_adapter

    def _get_llm_caller(self):
        """延迟加载 LLM 调用器"""
        if self._llm_adapter is None:
            from railway_agent.adapters.llm_adapter import get_llm_caller
            self._llm_adapter = get_llm_caller()
        return self._llm_adapter

    def classify_with_context(self, user_input: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        基于上下文分类用户意图（支持多轮对话记忆补全）

        当用户输入缺少车次号等关键实体，但上下文（上一轮对话）中存在时，
        自动补全实体并调整意图分类。

        Args:
            user_input: 用户输入文本
            context: 上下文记忆，格式 {"entities": {"train_id": "G1563", ...}}

        Returns:
            Dict: 包含补全后实体的分类结果
        """
        # 先尝试LLM分类，失败时回退到规则（与classify_with_fallback一致）
        try:
            result = self.classify(user_input)
        except RuntimeError:
            result = self.classify_with_fallback(user_input)

        entities = result.get("entities", {})
        context_entities = (context or {}).get("entities", {})

        # 1. 上下文实体补全：当前缺少但上下文有时，自动补全
        if not entities.get("train_id") and context_entities.get("train_id"):
            entities["train_id"] = context_entities["train_id"]
            result["reasoning"] = result.get("reasoning", "") + " | 从上下文补全车次号"
            logger.info(f"[意图路由] 从上下文补全车次号: {entities['train_id']}")

        if not entities.get("station_name") and context_entities.get("station_name"):
            entities["station_name"] = context_entities["station_name"]
            result["reasoning"] = result.get("reasoning", "") + " | 从上下文补全车站名"

        # 2. 特殊追问识别：用户说"显示所有站台/全部站点/所有站"等
        # 且上下文中有车次号 → 识别为对该车次的时刻表查询
        follow_up_patterns = ["所有站台", "全部站台", "所有站", "全部站", "全部站点", "所有站点", "完整时刻表", "详细时刻表"]
        text = user_input.strip()
        is_follow_up = any(p in text for p in follow_up_patterns)

        if is_follow_up and entities.get("train_id"):
            result["intent"] = "query"
            result["query_type"] = "timetable"
            result["reasoning"] = result.get("reasoning", "") + " | 追问识别：用户要求显示该车次的完整站点信息"
            result["confidence"] = min(result.get("confidence", 0.5) + 0.2, 1.0)
            logger.info(f"[意图路由] 追问识别为时刻表查询: {entities['train_id']}")

        result["entities"] = entities
        return result

    def classify(self, user_input: str) -> Dict[str, Any]:
        """
        分类用户意图（纯 LLM 模式，试验阶段）

        设计原则：
        - 完全依赖 LLM 进行意图分类，失败时直接抛出异常
        - 返回结果中包含 classifier 字段，明确标识使用的是 LLM
        - 不自动回退到规则匹配，确保试验阶段能验证 LLM 可行性

        Args:
            user_input: 用户输入文本

        Returns:
            Dict: {
                "intent": "dispatch|query|chat",
                "confidence": float,
                "query_type": "timetable|status|station_load|unknown",
                "entities": dict,
                "reasoning": str,
                "classifier": "llm"
            }

        Raises:
            RuntimeError: LLM 分类失败时抛出，不静默回退
        """
        if not user_input or not user_input.strip():
            return self._make_result("chat", 1.0, "unknown", {}, "空输入，默认闲聊", classifier="system")

        # 纯 LLM 分类，失败即抛出异常
        result = self._classify_with_llm(user_input)
        logger.info(f"[意图路由] LLM分类结果: {result['intent']} (置信度: {result['confidence']})")
        return result

    def classify_with_fallback(self, user_input: str) -> Dict[str, Any]:
        """
        分类用户意图（带规则兜底，正式上线后可选使用）

        Args:
            user_input: 用户输入文本

        Returns:
            Dict: 包含 classifier 字段（"llm" 或 "rule"），明确标识分类方式
        """
        if not user_input or not user_input.strip():
            return self._make_result("chat", 1.0, "unknown", {}, "空输入，默认闲聊", classifier="system")

        try:
            result = self._classify_with_llm(user_input)
            logger.info(f"[意图路由] LLM分类成功: {result['intent']} (置信度: {result['confidence']})")
            return result
        except Exception as e:
            logger.warning(f"[意图路由] LLM分类失败，显式回退到规则匹配: {e}")
            rule_result = self._classify_with_rules(user_input)
            rule_result["classifier"] = "rule"
            rule_result["llm_error"] = str(e)
            return rule_result

    def _classify_with_llm(self, user_input: str) -> Dict[str, Any]:
        """
        使用 LLM 进行意图分类（试验阶段核心方法）

        失败时直接抛出 RuntimeError，不返回 None，确保调用方明确感知 LLM 状态。
        """
        llm = self._get_llm_caller()

        prompt = f"{INTENT_CLASSIFICATION_PROMPT}\n\n用户输入：{user_input.strip()}"
        try:
            response_text, response_type = llm.call(prompt, max_tokens=512, temperature=0.1)
        except Exception as e:
            raise RuntimeError(f"LLM调用失败: {e}") from e

        if not response_text:
            raise RuntimeError("LLM返回空响应")

        # 提取 JSON
        parsed = self._extract_json(response_text)
        if not parsed:
            raise RuntimeError(f"无法从LLM响应中提取JSON。原始响应: {response_text[:200]}")

        intent = parsed.get("intent", "chat")
        confidence = float(parsed.get("confidence", 0.5))
        query_type = parsed.get("query_type", "unknown")
        entities = parsed.get("entities") or {}
        reasoning = parsed.get("reasoning", "")

        # 实体后处理：从用户输入中提取车次号（LLM 可能漏掉）
        if not entities.get("train_id"):
            train_ids = TRAIN_ID_PATTERN.findall(user_input)
            if train_ids:
                entities["train_id"] = train_ids[0]

        # 实体后处理：从用户输入中提取车站名
        if not entities.get("station_name"):
            for name in sorted(STATION_NAMES, key=len, reverse=True):
                if name in user_input:
                    entities["station_name"] = name
                    break

        return self._make_result(intent, confidence, query_type, entities, reasoning, classifier="llm")

    def _classify_with_rules(self, user_input: str) -> Dict[str, Any]:
        """【规则兜底】当 LLM 分类失败时的规则匹配兜底"""
        text = user_input.strip().lower()

        # 先检查明确的调度场景（优先级最高）
        dispatch_keywords = ["延误", "故障", "限速", "封锁", "晚点", "停车", "事故", "施工"]
        has_train = TRAIN_ID_PATTERN.search(user_input) is not None
        has_dispatch_kw = any(kw in text for kw in dispatch_keywords)

        if has_dispatch_kw and has_train:
            entities = self._extract_entities(user_input)
            return self._make_result("dispatch", 0.75, "unknown", entities, "规则匹配：车次号+调度关键词")

        if has_dispatch_kw and ("分钟" in text or "影响" in text):
            entities = self._extract_entities(user_input)
            return self._make_result("dispatch", 0.7, "unknown", entities, "规则匹配：调度场景关键词+时间/影响")

        # 检查是否是查询
        for kw in QUERY_KEYWORDS:
            if kw in text:
                query_type = self._infer_query_type(text)
                entities = self._extract_entities(user_input)
                return self._make_result("query", 0.6, query_type, entities, f"关键词'{kw}'匹配查询意图")

        # 检查是否是闲聊
        for kw in CHAT_KEYWORDS:
            if kw in text:
                return self._make_result("chat", 0.6, "unknown", {}, f"关键词'{kw}'匹配闲聊意图")

        # 弱调度信号
        if has_dispatch_kw:
            entities = self._extract_entities(user_input)
            return self._make_result("dispatch", 0.55, "unknown", entities, "规则匹配：仅含调度关键词")

        # 默认闲聊
        return self._make_result("chat", 0.5, "unknown", {}, "默认回退到闲聊")

    def _infer_query_type(self, text: str) -> str:
        """【规则方法】基于关键词推断查询类型"""
        if "时刻表" in text or "几点" in text or "经停" in text or "停靠" in text:
            return "timetable"
        if "状态" in text or "位置" in text or "到哪" in text or "运行" in text:
            return "status"
        if "负荷" in text or "密度" in text or "高峰" in text:
            return "station_load"
        if "站" in text and TRAIN_ID_PATTERN.search(text) is None:
            return "station_load"
        return "timetable"  # 默认查询时刻表

    def _extract_entities(self, text: str) -> Dict[str, Any]:
        """【规则方法】基于正则从文本中提取实体（LLM 后处理辅助）"""
        entities = {}

        # 车次号
        train_ids = TRAIN_ID_PATTERN.findall(text)
        if train_ids:
            entities["train_id"] = train_ids[0]

        # 车站名（按长度降序，优先匹配更长、更精确的名称）
        for name in sorted(STATION_NAMES, key=len, reverse=True):
            if name in text:
                entities["station_name"] = name
                break

        # 延误分钟数
        delay_match = re.search(r'(\d+)\s*分钟', text)
        if delay_match:
            entities["delay_minutes"] = int(delay_match.group(1))

        return entities

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        """从文本中提取 JSON 对象"""
        # 尝试 markdown 代码块
        if '```json' in text:
            try:
                json_str = text.split('```json')[1].split('```')[0].strip()
                return json.loads(json_str)
            except Exception:
                pass

        if '```' in text:
            try:
                json_str = text.split('```')[1].split('```')[0].strip()
                return json.loads(json_str)
            except Exception:
                pass

        # 尝试直接解析花括号
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                pass

        return None

    def _make_result(
        self,
        intent: str,
        confidence: float,
        query_type: str,
        entities: Dict[str, Any],
        reasoning: str,
        classifier: str = "rule"
    ) -> Dict[str, Any]:
        """构建标准化结果"""
        return {
            "intent": intent,
            "confidence": min(max(float(confidence), 0.0), 1.0),
            "query_type": query_type,
            "entities": entities,
            "reasoning": reasoning,
            "classifier": classifier
        }
