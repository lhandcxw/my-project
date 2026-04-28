# -*- coding: utf-8 -*-
"""
混合检索调度员操作指南

架构：多路召回 + 加权融合 + 置信度阈值

召回路：
1. Semantic (Embedding): DashScope text-embedding-v3, cosine similarity
2. Statistical (TF-IDF): numpy本地实现，零额外依赖
3. Keyword (规则): 同义词扩展 + 多字段加权匹配

过滤：
- Hard Filter: scene_category/fault_type 不匹配直接降权到0.2倍
- Soft Filter: 关键词黑名单/白名单机制

融合：
- 三路得分分别归一化到 [0, 1]
- 加权求和: semantic*0.45 + statistical*0.25 + keyword*0.30
- 置信度阈值: 融合得分 < 0.35 时返回 None，避免误匹配

可解释性：
- 返回 match_details 字段，记录每一路得分，便于调试
"""

import logging
import os
import json
import re
import math
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

# ================================================================
# 铁路领域同义词词典
# ================================================================

RAILWAY_SYNONYMS: Dict[str, List[str]] = {
    # 天气类
    "暴雨": ["大雨", "降雨", "雨水", "雨量", "洪水", "水害", "积水", "倾盆大雨", "强降雨"],
    "大风": ["强风", "狂风", "暴风", "台风", "飓风", "侧风", "风速", "风力", "大风天气"],
    "冰雪": ["降雪", "结冰", "冻雨", "道岔冻结", "覆冰", "暴雪", "雪天", "积雪"],
    "地震": ["地震报警", "地震预警", "震感", "地震监测"],
    "异物": ["异物侵线", "侵限", "障碍物", "落石", "塌方", "泥石流"],
    # 故障类
    "设备故障": ["故障", "设备异常", "设备损坏", "失效", "报警"],
    "信号故障": ["信号机故障", "信号异常", "信号失效", "信号灯", "信号显示"],
    "接触网故障": ["接触网跳闸", "接触网异常", "停电", "断电", "弓网故障", "受电弓"],
    "道岔故障": ["道岔失去表示", "道岔异常", "转辙机故障", "道岔卡阻"],
    "列控故障": ["列控车载设备故障", "ATP故障", "ATP失效", "车载设备"],
    "线路故障": ["线路异常", "轨道故障", "轨道电路", "红光带", "分路不良"],
    # 行车类
    "限速": ["降速", "减速", "减速运行", "限速运行", "限速命令", "临时限速", "列控限速"],
    "封锁": ["区间封锁", "线路封锁", "封锁区间", "封锁命令", "禁止通行"],
    "扣停": ["停车", "立即停车", "扣车", "停止发车", "呼停"],
    "晚点": ["延误", "晚点", "迟到", "拖延", "预计晚点", "预计延误"],
    "救援": ["热备", "备用动车组", "救援列车", "机车救援", "动车组救援"],
    # 其他
    "火灾": ["起火", "燃烧", "火情", "爆炸", "冒烟"],
    "旅客": ["乘客", "人员", "疏散", "换乘", "退行", "返回"],
}

# 关键词 -> 同义词组 的反向索引
SYNONYM_INDEX: Dict[str, str] = {}
for canonical, synonyms in RAILWAY_SYNONYMS.items():
    SYNONYM_INDEX[canonical.lower()] = canonical
    for s in synonyms:
        SYNONYM_INDEX[s.lower()] = canonical


# ================================================================
# 类别映射（L1提取的scene_category -> 知识库category）
# ================================================================

SCENE_TO_CATEGORIES: Dict[str, List[str]] = {
    "临时限速": ["自然灾害"],
    "区间封锁": ["非正常行车", "自然灾害"],
    "区间中断": ["非正常行车", "自然灾害"],
    "突发故障": ["设备故障行车", "设备故障", "非正常行车"],
    "设备故障": ["设备故障行车", "设备故障"],
    "救援组织": ["救援组织"],
    "自然灾害": ["自然灾害"],
    "非正常行车": ["非正常行车"],
}


# ================================================================
# 数据结构
# ================================================================

@dataclass
class KnowledgeDoc:
    """标准化知识库文档"""
    scene_name: str
    scene_id: str
    category: str
    keywords_primary: List[str]
    keywords_secondary: List[str]
    key_notes: List[str]
    steps: List[Dict]
    source: str
    # 以下字段在初始化后填充
    search_text: str = ""           # 用于embedding/TF-IDF的完整文本
    keyword_set: set = field(default_factory=set)  # 所有关键词集合（含同义词扩展）


@dataclass
class MatchResult:
    """单条匹配结果"""
    doc: KnowledgeDoc
    semantic_score: float = 0.0
    tfidf_score: float = 0.0
    keyword_score: float = 0.0
    category_penalty: float = 1.0   # 类别不匹配时的惩罚系数
    fusion_score: float = 0.0


# ================================================================
# 混合检索器
# ================================================================

class HybridOperationRetriever:
    """
    调度员操作指南混合检索器

    使用方式：
        retriever = HybridOperationRetriever()
        result = retriever.retrieve(
            scene_category="临时限速",
            fault_type="暴雨",
            user_input="G1563在石家庄站因暴雨临时限速..."
        )
    """

    # 融合权重（可配置）
    WEIGHT_SEMANTIC = 0.45
    WEIGHT_TFIDF = 0.25
    WEIGHT_KEYWORD = 0.30

    # 置信度阈值
    CONFIDENCE_THRESHOLD = 0.35
    # 类别不匹配惩罚系数
    CATEGORY_MISMATCH_PENALTY = 0.15

    def __init__(self, use_embedding: bool = True):
        """
        Args:
            use_embedding: 是否尝试使用Embedding语义检索（需要DashScope API Key）
        """
        self.knowledge_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'data', 'knowledge'
        )
        self.docs: List[KnowledgeDoc] = []
        self.use_embedding = use_embedding

        # Embedding 缓存
        self._embeddings: Optional[np.ndarray] = None  # (n_docs, dim)
        self._embedding_available = False

        # TF-IDF 缓存
        self._tfidf_matrix: Optional[np.ndarray] = None  # (n_docs, n_terms)
        self._tfidf_idf: Optional[np.ndarray] = None
        self._tfidf_vocab: Dict[str, int] = {}
        self._tfidf_available = False

        # 加载知识库
        self._load_knowledge()

        # 预计算索引
        if self.docs:
            self._build_tfidf_index()
            if self.use_embedding:
                self._build_embedding_index()

    # ----------------------------------------------------------------
    # 知识库加载
    # ----------------------------------------------------------------

    def _load_knowledge(self):
        """从operations目录加载JSON知识库"""
        operations_dir = os.path.join(self.knowledge_dir, "operations")
        if not os.path.exists(operations_dir):
            logger.warning("[HybridRetriever] operations目录不存在")
            return

        loaded = 0
        for root, _, files in os.walk(operations_dir):
            for filename in files:
                if not filename.endswith('.json'):
                    continue
                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, operations_dir)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    scenes = data.get('scenes', [])
                    if not scenes and 'scene_id' in data:
                        scenes = [data]

                    for scene in scenes:
                        doc = self._parse_scene(scene, rel_path)
                        if doc:
                            self.docs.append(doc)
                            loaded += 1
                except Exception as e:
                    logger.warning(f"[HybridRetriever] 加载 {rel_path} 失败: {e}")

        logger.info(f"[HybridRetriever] 加载了 {loaded} 个知识库文档")

    def _parse_scene(self, scene: Dict, rel_path: str) -> Optional[KnowledgeDoc]:
        """解析单个场景为KnowledgeDoc"""
        scene_name = scene.get('scene_name', '')
        if not scene_name:
            return None

        keywords_data = scene.get('keywords', {})
        keywords_primary = keywords_data.get('primary', [])
        keywords_secondary = keywords_data.get('secondary', [])

        steps = []
        for op in scene.get('operations', []):
            steps.append({
                "step_id": op.get('step_id', 0),
                "phase": op.get('phase', ''),
                "priority": op.get('priority', 'medium'),
                "actions": op.get('actions', [])
            })

        doc = KnowledgeDoc(
            scene_name=scene_name,
            scene_id=scene.get('scene_id', ''),
            category=scene.get('category', ''),
            keywords_primary=keywords_primary,
            keywords_secondary=keywords_secondary,
            key_notes=scene.get('key_notes', []),
            steps=steps,
            source=f"operations/{rel_path}"
        )

        # 构建检索文本
        doc.search_text = self._build_search_text(doc)
        # 构建关键词集合（含同义词扩展）
        doc.keyword_set = self._build_keyword_set(doc)

        return doc

    def _build_search_text(self, doc: KnowledgeDoc) -> str:
        """构建用于语义/统计检索的统一文本"""
        parts = [doc.scene_name]
        parts.extend(doc.keywords_primary)
        parts.extend(doc.keywords_secondary)
        parts.extend(doc.key_notes)
        for step in doc.steps:
            parts.append(step.get("phase", ""))
            parts.extend(step.get("actions", []))
        return " ".join(p for p in parts if p).lower()

    def _build_keyword_set(self, doc: KnowledgeDoc) -> set:
        """构建关键词集合，含同义词扩展"""
        keywords = set()
        for kw in doc.keywords_primary + doc.keywords_secondary:
            kw_lower = kw.lower()
            keywords.add(kw_lower)
            # 扩展同义词
            canonical = SYNONYM_INDEX.get(kw_lower)
            if canonical:
                keywords.add(canonical.lower())
                for syn in RAILWAY_SYNONYMS.get(canonical, []):
                    keywords.add(syn.lower())
        # 加入scene_name的2-3gram（中文分词近似）
        name = doc.scene_name.lower()
        for n in (2, 3):
            for i in range(len(name) - n + 1):
                keywords.add(name[i:i + n])
        return keywords

    # ----------------------------------------------------------------
    # TF-IDF 索引（本地，零额外依赖）
    # ----------------------------------------------------------------

    def _tokenize(self, text: str) -> List[str]:
        """
        轻量级中文分词：
        - 提取2-4字中文n-gram
        - 提取英文/数字token
        """
        text = text.lower()
        tokens = []
        # 中文n-gram (2-4)
        chars = re.findall(r'[\u4e00-\u9fff]', text)
        for n in (2, 3, 4):
            for i in range(len(chars) - n + 1):
                tokens.append(''.join(chars[i:i + n]))
        # 英文/数字
        tokens.extend(re.findall(r'[a-z]+\d+', text))
        tokens.extend(re.findall(r'\d+', text))
        return tokens

    def _build_tfidf_index(self):
        """构建TF-IDF矩阵"""
        try:
            # 构建词表
            term_freqs = []
            all_terms = set()
            for doc in self.docs:
                tokens = self._tokenize(doc.search_text)
                freq = {}
                for t in tokens:
                    freq[t] = freq.get(t, 0) + 1
                    all_terms.add(t)
                term_freqs.append(freq)

            self._tfidf_vocab = {term: idx for idx, term in enumerate(sorted(all_terms))}
            n_docs = len(self.docs)
            n_terms = len(self._tfidf_vocab)

            # 计算IDF
            idf = np.zeros(n_terms)
            for freq in term_freqs:
                for term in freq:
                    idx = self._tfidf_vocab[term]
                    idf[idx] += 1
            idf = np.log((n_docs + 1) / (idf + 1)) + 1
            self._tfidf_idf = idf

            # 计算TF-IDF矩阵
            tfidf = np.zeros((n_docs, n_terms))
            for i, freq in enumerate(term_freqs):
                total = sum(freq.values())
                for term, count in freq.items():
                    idx = self._tfidf_vocab[term]
                    tf = count / total if total > 0 else 0
                    tfidf[i, idx] = tf * idf[idx]

            # L2归一化
            norms = np.linalg.norm(tfidf, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._tfidf_matrix = tfidf / norms
            self._tfidf_available = True
            logger.info(f"[HybridRetriever] TF-IDF索引构建完成: {n_docs} docs x {n_terms} terms")
        except Exception as e:
            logger.warning(f"[HybridRetriever] TF-IDF索引构建失败: {e}")
            self._tfidf_available = False

    def _tfidf_score(self, query: str) -> np.ndarray:
        """计算query与所有文档的TF-IDF余弦相似度"""
        if not self._tfidf_available or self._tfidf_matrix is None:
            return np.zeros(len(self.docs))

        tokens = self._tokenize(query)
        freq = {}
        for t in tokens:
            freq[t] = freq.get(t, 0) + 1

        n_terms = len(self._tfidf_vocab)
        vec = np.zeros(n_terms)
        total = sum(freq.values())
        for term, count in freq.items():
            if term in self._tfidf_vocab:
                idx = self._tfidf_vocab[term]
                tf = count / total if total > 0 else 0
                vec[idx] = tf * self._tfidf_idf[idx]

        norm = np.linalg.norm(vec)
        if norm == 0:
            return np.zeros(len(self.docs))
        vec = vec / norm

        # 余弦相似度 = dot product（因为已经L2归一化）
        scores = self._tfidf_matrix @ vec
        return np.clip(scores, 0, 1)

    # ----------------------------------------------------------------
    # Embedding 索引（DashScope API）
    # ----------------------------------------------------------------

    def _build_embedding_index(self):
        """预计算所有知识库的Embedding向量"""
        api_key = self._get_dashscope_key()
        if not api_key:
            logger.info("[HybridRetriever] 未配置DashScope API Key，跳过Embedding索引")
            return

        try:
            texts = [doc.search_text for doc in self.docs]
            embeddings = self._batch_embed(texts, api_key)
            if embeddings is not None and len(embeddings) == len(self.docs):
                self._embeddings = embeddings
                self._embedding_available = True
                logger.info(f"[HybridRetriever] Embedding索引构建完成: {len(self.docs)} docs, dim={embeddings.shape[1]}")
            else:
                logger.warning("[HybridRetriever] Embedding返回数量不匹配")
        except Exception as e:
            logger.warning(f"[HybridRetriever] Embedding索引构建失败: {e}")

    def _get_dashscope_key(self) -> str:
        """获取DashScope API Key"""
        try:
            from config import LLMConfig
            return LLMConfig.DASHSCOPE_API_KEY or ""
        except Exception:
            return os.getenv("DASHSCOPE_API_KEY", "")

    def _get_embedding_client(self, api_key: str):
        """获取OpenAI兼容的Embedding客户端（DashScope兼容模式）"""
        try:
            from openai import OpenAI
            return OpenAI(
                api_key=api_key,
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
            )
        except Exception as e:
            logger.warning(f"[HybridRetriever] 无法初始化Embedding客户端: {e}")
            return None

    def _batch_embed(self, texts: List[str], api_key: str) -> Optional[np.ndarray]:
        """调用DashScope Embedding API（OpenAI兼容模式）"""
        client = self._get_embedding_client(api_key)
        if client is None:
            return None

        all_embeddings = []
        batch_size = 8  # DashScope兼容模式建议batch不要太大
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            try:
                resp = client.embeddings.create(
                    model="text-embedding-v3",
                    input=batch,
                    encoding_format="float"
                )
                for item in resp.data:
                    all_embeddings.append(np.array(item.embedding, dtype=np.float32))
            except Exception as e:
                logger.warning(f"[HybridRetriever] Embedding batch {i//batch_size} 失败: {e}")
                return None

        if not all_embeddings:
            return None
        return np.vstack(all_embeddings)

    def _embed_query(self, text: str, api_key: str) -> Optional[np.ndarray]:
        """Embed单条query"""
        client = self._get_embedding_client(api_key)
        if client is None:
            return None
        try:
            resp = client.embeddings.create(
                model="text-embedding-v3",
                input=[text],
                encoding_format="float"
            )
            return np.array(resp.data[0].embedding, dtype=np.float32)
        except Exception as e:
            logger.warning(f"[HybridRetriever] Query Embedding失败: {e}")
        return None

    def _semantic_score(self, query: str) -> np.ndarray:
        """计算query与所有文档的语义余弦相似度"""
        if not self._embedding_available or self._embeddings is None:
            return np.zeros(len(self.docs))

        api_key = self._get_dashscope_key()
        if not api_key:
            return np.zeros(len(self.docs))

        q_emb = self._embed_query(query, api_key)
        if q_emb is None:
            return np.zeros(len(self.docs))

        # 余弦相似度
        q_norm = np.linalg.norm(q_emb)
        if q_norm == 0:
            return np.zeros(len(self.docs))
        q_emb = q_emb / q_norm

        scores = self._embeddings @ q_emb
        return np.clip(scores, 0, 1)

    # ----------------------------------------------------------------
    # 关键词得分（规则路）
    # ----------------------------------------------------------------

    def _keyword_score(self, query: str, fault_type: str) -> Tuple[np.ndarray, np.ndarray]:
        """
        计算关键词匹配得分和category惩罚系数
        返回: (scores, penalties)
        """
        scores = np.zeros(len(self.docs))
        penalties = np.ones(len(self.docs))
        query_lower = query.lower()
        query_terms = self._extract_query_terms(query_lower, fault_type)

        for i, doc in enumerate(self.docs):
            score = 0.0

            # 1. fault_type 匹配 scene_name（权重5）
            if fault_type and fault_type.lower() in doc.scene_name.lower():
                score += 5

            # 2. fault_type 匹配 keywords_primary（权重4），含同义词扩展
            if fault_type:
                match_terms = {fault_type.lower()}
                canonical = SYNONYM_INDEX.get(fault_type.lower())
                if canonical:
                    match_terms.add(canonical.lower())
                    match_terms.update(s.lower() for s in RAILWAY_SYNONYMS.get(canonical, []))
                for kw in doc.keywords_primary:
                    kw_lower = kw.lower()
                    if any(mt in kw_lower or kw_lower in mt for mt in match_terms):
                        score += 4

            # 3. query term 匹配 scene_name（权重2）
            for term in query_terms:
                if term in doc.scene_name.lower():
                    score += 2

            # 4. 关键词集合匹配（权重3/1）
            for term in query_terms:
                if term in doc.keyword_set:
                    # 判断是否是primary关键词
                    is_primary = any(term == kw.lower() for kw in doc.keywords_primary)
                    score += 3 if is_primary else 1

            # 5. key_notes 辅助匹配（权重0.5）
            for note in doc.key_notes:
                note_lower = note.lower()
                for term in query_terms:
                    if term in note_lower:
                        score += 0.5

            scores[i] = score

            # category 惩罚
            if not self._category_match(doc.category, query_lower):
                penalties[i] = self.CATEGORY_MISMATCH_PENALTY

        return scores, penalties

    def _extract_query_terms(self, query_lower: str, fault_type: str) -> List[str]:
        """提取query中的有效匹配词（含同义词扩展）"""
        terms = set()

        # 提取2-6字中文词
        terms.update(re.findall(r'[\u4e00-\u9fff]{2,6}', query_lower))

        # 加入fault_type及其同义词
        if fault_type:
            ft = fault_type.lower()
            terms.add(ft)
            canonical = SYNONYM_INDEX.get(ft)
            if canonical:
                terms.add(canonical.lower())
                terms.update(s.lower() for s in RAILWAY_SYNONYMS.get(canonical, []))

        # 对query中每个2-4gram，检查是否是同义词
        chars = re.findall(r'[\u4e00-\u9fff]', query_lower)
        for n in (2, 3, 4):
            for i in range(len(chars) - n + 1):
                gram = ''.join(chars[i:i + n])
                canonical = SYNONYM_INDEX.get(gram)
                if canonical:
                    terms.add(canonical.lower())
                    terms.update(s.lower() for s in RAILWAY_SYNONYMS.get(canonical, []))

        return list(terms)

    def _category_match(self, doc_category: str, query_lower: str) -> bool:
        """
        判断知识库category是否与query的语义类别兼容。
        这里用简化策略：如果query中包含某些类别强提示词，则做判断。
        """
        # 实际上这个函数应由外部传入scene_category判断
        # 为保持接口兼容，这里仅做关键词级别的弱判断
        # 真正的硬过滤在 retrieve() 中由 scene_category 参数控制
        return True

    # ----------------------------------------------------------------
    # 融合检索（主入口）
    # ----------------------------------------------------------------

    def _is_fault_compatible(self, doc: KnowledgeDoc, fault_type: str) -> bool:
        """
        判断fault_type与知识库文档是否语义兼容。
        防止语义/统计路的模糊匹配导致完全无关的场景被选中
        （如"施工"误匹配到"雨天天气"）。
        """
        ft = fault_type.lower()
        # 直接匹配scene_name
        if ft in doc.scene_name.lower():
            return True
        # 直接匹配primary keywords
        if any(ft in kw.lower() or kw.lower() in ft for kw in doc.keywords_primary):
            return True
        # 同义词匹配
        canonical = SYNONYM_INDEX.get(ft)
        if canonical:
            c = canonical.lower()
            if c in doc.scene_name.lower():
                return True
            if any(c in kw.lower() or kw.lower() in c for kw in doc.keywords_primary):
                return True
            # 扩展同义词也匹配primary keywords
            for syn in RAILWAY_SYNONYMS.get(canonical, []):
                s = syn.lower()
                if any(s in kw.lower() or kw.lower() in s for kw in doc.keywords_primary):
                    return True
        return False

    def retrieve(
        self,
        scene_category: str,
        fault_type: str,
        user_input: str
    ) -> Optional[Dict[str, Any]]:
        """
        混合检索主入口

        Args:
            scene_category: L1提取的场景大类（如"临时限速"）
            fault_type: L1提取的故障类型（如"暴雨"）
            user_input: 用户原始输入

        Returns:
            最佳匹配的操作指南字典，含match_score和match_details；
            若置信度不足则返回None
        """
        if not self.docs:
            return None

        query = f"{fault_type or ''} {user_input}".strip()
        n_docs = len(self.docs)

        # --- 三路召回得分 ---
        semantic_scores = self._semantic_score(query)
        tfidf_scores = self._tfidf_score(query)
        keyword_scores, category_penalties = self._keyword_score(user_input, fault_type)

        # --- 类别硬过滤 ---
        matched_cats = set(SCENE_TO_CATEGORIES.get(scene_category, [scene_category]))
        for i, doc in enumerate(self.docs):
            if scene_category and doc.category:
                # 双向子串匹配：如 "设备故障" <-> "设备故障行车"
                cat_match = (
                    doc.category in matched_cats or
                    any(cat in doc.category or doc.category in cat for cat in matched_cats)
                )
                if not cat_match:
                    category_penalties[i] = self.CATEGORY_MISMATCH_PENALTY

        # --- 得分归一化 ---
        sem_norm = self._normalize(semantic_scores)
        tfidf_norm = self._normalize(tfidf_scores)
        key_norm = self._normalize(keyword_scores)

        # --- 加权融合 ---
        fusion = (
            self.WEIGHT_SEMANTIC * sem_norm +
            self.WEIGHT_TFIDF * tfidf_norm +
            self.WEIGHT_KEYWORD * key_norm
        ) * category_penalties

        # --- 选择最佳 ---
        best_idx = int(np.argmax(fusion))
        best_score = float(fusion[best_idx])
        best_doc = self.docs[best_idx]

        # --- 日志（可解释性）---
        logger.info(
            f"[HybridRetriever] 最佳匹配: {best_doc.scene_name} (融合得分={best_score:.3f}, "
            f"semantic={sem_norm[best_idx]:.3f}, tfidf={tfidf_norm[best_idx]:.3f}, "
            f"keyword={key_norm[best_idx]:.3f}, penalty={category_penalties[best_idx]:.2f})"
        )

        # --- 二次校验：fault_type与最佳匹配的兼容性 ---
        if fault_type and best_score > 0:
            compatible = self._is_fault_compatible(best_doc, fault_type)
            if not compatible:
                logger.warning(
                    f"[HybridRetriever] fault_type='{fault_type}'与最佳匹配'{best_doc.scene_name}'不兼容，拒绝返回"
                )
                return None

        # --- 置信度阈值 ---
        if best_score < self.CONFIDENCE_THRESHOLD:
            logger.warning(
                f"[HybridRetriever] 最佳匹配得分{best_score:.3f}低于阈值{self.CONFIDENCE_THRESHOLD}，返回None"
            )
            return None

        # --- 构建返回结果（兼容原有接口）---
        filtered_steps = [
            s for s in best_doc.steps
            if s.get("priority") in ("critical", "high")
        ]
        if not filtered_steps and best_doc.steps:
            filtered_steps = best_doc.steps[:2]

        return {
            "scene_name": best_doc.scene_name,
            "scene_id": best_doc.scene_id,
            "category": best_doc.category,
            "steps": filtered_steps,
            "key_notes": best_doc.key_notes,
            "source": best_doc.source,
            "match_score": round(best_score, 2),
            "match_details": {
                "fusion_score": round(best_score, 3),
                "semantic_score": round(float(sem_norm[best_idx]), 3),
                "tfidf_score": round(float(tfidf_norm[best_idx]), 3),
                "keyword_score": round(float(key_norm[best_idx]), 3),
                "category_penalty": round(float(category_penalties[best_idx]), 2),
            }
        }

    @staticmethod
    def _normalize(scores: np.ndarray) -> np.ndarray:
        """Min-Max归一化到[0,1]"""
        min_v = scores.min()
        max_v = scores.max()
        if max_v - min_v < 1e-9:
            return np.zeros_like(scores)
        return (scores - min_v) / (max_v - min_v)
