# -*- coding: utf-8 -*-
"""
LLM驱动的工作流引擎模块
基于 v3.1 架构：L0 预处理 + 4层工作流

架构说明：
- L0：预处理层 - 将不同输入转换为 CanonicalDispatchRequest（已在 preprocessing/ 模块实现）
- L1：数据建模层 - LLM辅助判断场景类型，NetworkSnapshot由确定性逻辑切出
- L2：Planner层 - LLM决策 planning_intent（问题类型与处理意图）
- L3：Solver执行层 - SolverPolicyAdapter 根据 intent 选择求解器并执行
- L4：评估层 - LLM提供解释/摘要/风险提示，PolicyEngine做最终决策

注意：llm_workflow_engine 内部不再接收 raw_text，只接收 CanonicalDispatchRequest
"""

from typing import Dict, Any, Optional, List
import json
import logging
from datetime import datetime

# 导入RAG检索器
from railway_agent.rag_retriever import RAGRetriever, get_retriever

from models.workflow_models import (
    SceneSpec,
    AccidentCard,
    NetworkSnapshot,
    DispatchContext,
    DispatchContextMetadata,
    TaskPlan,
    SubTask,
    SolverResult,
    WorkflowResult,
    EvaluationReport,
    RollbackFeedback,
    CandidateSolution,
    RankingResult,
    StructuredOutput
)

logger = logging.getLogger(__name__)


# ============== LLM 调用适配器 ==============

class LLMCaller:
    """
    LLM调用适配器
    支持 Ollama 本地模型 和 ModelScope 远程模型
    """

    # ModelScope 模型ID (使用1.8B模型，比0.5B更强)
    MODELSCOPE_MODEL_ID = "Qwen/Qwen2.5-1.8B"

    # 可用的Ollama模型（优先0.8B，如不可用则回退到0.5B）
    OLLAMA_MODEL = "qwen2.5:0.8b"  # 用户指定版本

    def __init__(self, model_path: str = None, use_ollama: bool = False):
        """
        初始化LLM调用器

        Args:
            model_path: ModelScope模型ID或本地路径
            use_ollama: 是否优先使用Ollama（默认False，优先使用ModelScope）
        """
        self.model_path = model_path
        self.use_ollama = use_ollama
        self.model = None
        self.tokenizer = None
        self._ollama_available = None

    def _check_ollama(self) -> bool:
        """检查Ollama是否可用"""
        if self._ollama_available is not None:
            return self._ollama_available

        try:
            import requests
            r = requests.get('http://localhost:11434/api/tags', timeout=3)
            if r.status_code == 200:
                self._ollama_available = True
                return True
        except:
            pass
        self._ollama_available = False
        return False

    def load_model(self):
        """加载模型 - 优先Ollama，其次ModelScope"""
        if self.model is not None:
            return

        # 优先尝试Ollama
        if self.use_ollama and self._check_ollama():
            logger.info("使用Ollama本地模型")
            return

        # 回退到ModelScope
        try:
            from modelscope import AutoModelForCausalLM, AutoTokenizer
            import torch

            model_id = self.model_path if self.model_path else self.MODELSCOPE_MODEL_ID
            logger.info(f"正在从ModelScope加载模型: {model_id}")
            self.model = AutoModelForCausalLM.from_pretrained(
                model_id,
                torch_dtype="auto",
                device_map="cpu"
            )
            self.tokenizer = AutoTokenizer.from_pretrained(model_id)
            logger.info("ModelScope模型加载完成")
        except Exception as e:
            logger.warning(f"ModelScope模型加载失败: {e}，将使用模拟模式")
            self.model = None
            self.tokenizer = None

    def call(self, prompt: str, max_tokens: int = 512, temperature: float = 0.7) -> str:
        """
        调用LLM生成响应

        Args:
            prompt: 输入提示
            max_tokens: 最大生成token数
            temperature: 温度参数

        Returns:
            str: LLM响应文本
        """
        # 优先尝试Ollama
        if self.use_ollama and self._check_ollama():
            try:
                return self._call_ollama(prompt, max_tokens, temperature)
            except Exception as e:
                logger.warning(f"Ollama调用失败: {e}")

        # 回退到ModelScope
        if self.model is None:
            self.load_model()

        if self.model is None or self.tokenizer is None:
            logger.warning("模型未加载，使用模拟响应")
            return self._mock_response(prompt)

        return self._call_modelscope(prompt, max_tokens, temperature)

    def _call_ollama(self, prompt: str, max_tokens: int, temperature: float) -> str:
        """调用Ollama API"""
        import requests

        response = requests.post(
            'http://localhost:11434/api/generate',
            json={
                'model': self.OLLAMA_MODEL,
                'prompt': prompt,
                'stream': False,
                'options': {
                    'num_predict': max_tokens,
                    'temperature': temperature
                }
            },
            timeout=120
        )
        result = response.json()
        return result.get('response', '')

    def _call_modelscope(self, prompt: str, max_tokens: int, temperature: float) -> str:
        """调用ModelScope模型"""
        try:
            from modelscope import AutoModelForCausalLM

            messages = [{"role": "user", "content": prompt}]
            text = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True
            )
            model_inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)

            generated_ids = self.model.generate(
                **model_inputs,
                max_new_tokens=max_tokens,
                temperature=temperature,
                top_p=0.9,
                do_sample=True
            )

            generated_ids = [
                output_ids[len(input_ids):]
                for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
            ]

            response = self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
            return response

        except Exception as e:
            logger.warning(f"ModelScope调用失败: {e}，使用模拟响应")
            return self._mock_response(prompt)

    def _mock_response(self, prompt: str) -> str:
        """
        模拟响应（当模型不可用时）
        用于测试流程 - 返回符合格式要求的完整JSON

        注意：这是临时方案，用于验证流程。最终需要使用真实的LLM
        """
        # 检查prompt中的特定关键词来判断是哪一层
        # 第一层：数据建模层 - 检查故障描述相关
        # 第二层：Planner层 - 检查技能路由相关
        # 第四层：评估层 - 检查求解结果相关

        if "技能规划助手" in prompt or "skill_dispatch" in prompt or ("主技能" in prompt and "调用顺序" in prompt):
            # 第二层 - Planner
            return '{"skill_dispatch": {"是否进入技能求解": true, "主技能": "mip_scheduler", "辅助技能": [], "调用顺序": ["mip_scheduler"], "阻塞项": [], "需补充信息": []}, "reasoning": "根据临时限速场景，选择MIP求解器"}'
        elif "调度方案评估" in prompt or "execution_result" in prompt:
            # 第四层 - 评估
            return '{"evaluation_report": {"solution_id": "solution_001", "is_feasible": true, "total_delay_minutes": 10, "max_delay_minutes": 10, "solving_time_seconds": 1.5, "risk_warnings": [], "constraint_satisfaction": {}}, "ranking_result": {"recommended_solution": "solution_001", "alternative_solutions": [], "ranking_criteria": "min_max_delay"}, "rollback_feedback": {"needs_rerun": false, "rollback_reason": "", "suggested_fixes": []}}'
        else:
            # 第一层 - 数据建模（默认）
            return '{"accident_card": {"scene_category": "临时限速", "fault_type": "暴雨", "affected_section": "XSD-BDD", "start_time": "2024-01-15T10:00:00", "is_complete": true}, "network_snapshot": {"observation_corridor": "XSD-BDD", "train_count": 1}}'


# ============== 工具函数 ==============

def safe_json_dumps(obj: Any) -> str:
    """
    安全的JSON序列化（处理datetime等特殊类型）

    Args:
        obj: 要序列化的对象

    Returns:
        str: JSON字符串
    """
    import datetime as dt
    def default(o):
        if isinstance(o, dt.datetime):
            return o.isoformat()
        if hasattr(o, 'model_dump'):
            return o.model_dump()
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")
    return json.dumps(obj, ensure_ascii=False, indent=2, default=default)


# ============== 全局LLM调用器实例 ==============

_llm_caller: Optional[LLMCaller] = None
_rag_retriever: Optional[RAGRetriever] = None


def get_llm_caller() -> LLMCaller:
    """获取全局LLM调用器实例"""
    global _llm_caller
    if _llm_caller is None:
        _llm_caller = LLMCaller()
    return _llm_caller


def get_rag_retriever() -> RAGRetriever:
    """获取全局RAG检索器实例"""
    global _rag_retriever
    if _rag_retriever is None:
        _rag_retriever = get_retriever()
    return _rag_retriever


def set_llm_caller(caller: LLMCaller):
    """设置全局LLM调用器实例"""
    global _llm_caller
    _llm_caller = caller


# ============== 第一层：数据建模层 ==============

LAYER1_PROMPT = """
根据铁路故障/调整描述，提取关键信息。只输出JSON。

故障描述：{user_input}

输出格式（严格JSON，只输出一行）：
{{"accident_card": {{"scene_category":"临时限速","fault_type":"大风","affected_section":"SJP-SJP","location_code":"SJP","location_name":"石家庄","affected_train_ids":["G1265"],"reported_delay_minutes":10,"start_time":"2024-01-15T10:00:00","is_complete":true}}}}

可选scene_category: 临时限速, 突发故障, 区间封锁
可选fault_type: 暴雨, 大风, 设备故障, 信号故障, 接触网故障, 预计晚点, 晚点

车站名称到站码的映射：
- 北京西 -> BJX, 杜家坎线路所 -> DJK, 涿州东 -> ZBD, 高碑店东 -> GBD
- 徐水东 -> XSD, 保定东 -> BDD, 定州东 -> DZD, 正定机场 -> ZDJ
- 石家庄 -> SJP, 高邑西 -> GYX, 邢台东 -> XTD, 邯郸东 -> HDD, 安阳东 -> AYD

注意：
- 从描述中提取列车号（如G1265）放入affected_train_ids
- 从描述中提取车站名（如石家庄）转换为站码（如SJP）
- 从描述中提取延误时间（如10分钟）放入reported_delay_minutes
- 判断is_complete：如果有列车号+车站/区段+事件类型，则为true，否则为false
- location_code和affected_section应基于车站位置确定区间
- 如果缺少关键信息（如车站位置、事件类型），is_complete设为false，并在missing_fields中列出
"""


def _build_network_snapshot_deterministic(
    location_code: str,
    affected_section: str,
    snapshot_info: Dict[str, Any]
) -> NetworkSnapshot:
    """
    确定性逻辑构建 NetworkSnapshot
    根据位置信息和时刻表数据切出观察窗口
    """
    from models.data_loader import load_trains, load_stations, get_station_codes
    
    # 获取车站顺序
    station_codes = get_station_codes()
    
    # 确定观察区间
    if location_code and location_code in station_codes:
        loc_idx = station_codes.index(location_code)
        # 前后各扩展2个区间作为观察窗口
        start_idx = max(0, loc_idx - 2)
        end_idx = min(len(station_codes) - 1, loc_idx + 2)
        observation_corridor = f"{station_codes[start_idx]}-{station_codes[end_idx]}"
    elif affected_section and "-" in affected_section:
        observation_corridor = affected_section
    else:
        observation_corridor = snapshot_info.get("observation_corridor", "")
    
    # 统计受影响的列车（在观察窗口内的列车）
    # 先确定观察窗口的站码列表
    observation_window_codes = []
    if location_code and location_code in station_codes:
        loc_idx = station_codes.index(location_code)
        start_idx = max(0, loc_idx - 2)
        end_idx = min(len(station_codes), loc_idx + 3)
        observation_window_codes = station_codes[start_idx:end_idx]
    
    trains = load_trains()
    affected_trains = []
    for train in trains:
        stops = train.get("schedule", {}).get("stops", [])
        for stop in stops:
            stop_code = stop.get("station_code")
            if stop_code and observation_window_codes and stop_code in observation_window_codes:
                affected_trains.append(train["train_id"])
                break
    
    return NetworkSnapshot(
        snapshot_time=datetime.now(),
        solving_window={
            "observation_corridor": observation_corridor,
            "planning_time_window": snapshot_info.get("time_window", {})
        },
        train_count=len(affected_trains),
        current_delays=snapshot_info.get("current_delays", {})
    )


def layer1_data_modeling(user_input: str, snapshot_info: Dict[str, Any], canonical_request: Any = None) -> Dict[str, Any]:
    """
    第一层：数据建模层
    LLM根据用户输入生成事故卡片，网络快照由确定性逻辑切出

    Args:
        user_input: 调度员自然语言描述
        snapshot_info: 当前运行状态快照
        canonical_request: L0 预处理的标准化请求（可选）

    Returns:
        Dict: 包含 accident_card 和 network_snapshot 的字典
    """
    logger.info("========== 第一层：数据建模层 (LLM辅助+RAG) ==========")

    # 如果有L0预处理的结果，直接使用
    if canonical_request and canonical_request.scene_category:
        logger.info(f"使用L0预处理结果: scene_category={canonical_request.scene_category}")
        accident_card = AccidentCard(
            fault_type=canonical_request.fault_type or "未知",
            scene_category=canonical_request.scene_category,
            start_time=canonical_request.start_time,
            expected_duration=canonical_request.expected_duration,
            affected_section=canonical_request.affected_section,
            location_code=canonical_request.location_code,
            location_name=canonical_request.location_name,
            is_complete=True,
            missing_fields=[]
        )
        
        # 确定性逻辑构建NetworkSnapshot
        network_snapshot = _build_network_snapshot_deterministic(
            canonical_request.location_code,
            canonical_request.affected_section,
            snapshot_info
        )
        
        dispatch_metadata = DispatchContextMetadata(
            can_solve=True,
            missing_info=[],
            observation_corridor=network_snapshot.solving_window.get("observation_corridor", "")
        )
        
        logger.info(f"第一层完成(L0): scene_category={accident_card.scene_category}")
        
        return {
            "accident_card": accident_card,
            "network_snapshot": network_snapshot,
            "dispatch_context_metadata": dispatch_metadata,
            "llm_response": "使用L0预处理结果"
        }

    # 获取RAG检索器，增强Prompt
    rag = get_rag_retriever()

    # 构建基础Prompt
    base_prompt = LAYER1_PROMPT.format(
        user_input=user_input,
        snapshot_info=safe_json_dumps(snapshot_info)
    )

    # 使用RAG增强Prompt（添加领域知识）
    prompt = rag.format_prompt_with_knowledge(base_prompt, user_input)

    # 调用LLM
    llm = get_llm_caller()
    print(f"[DEBUG] 第一层调用LLM+RAG，prompt长度={len(prompt)}")
    print(f"[DEBUG] 第一层prompt前200字: {prompt[:200]}")
    response = llm.call(prompt, max_tokens=512)
    print(f"[DEBUG] 第一层LLM原始响应: {response[:300]}")

    logger.info(f"第一层LLM响应: {response[:200]}...")

    # 解析LLM响应 - 处理Markdown代码块
    try:
        # 提取JSON（去除```json ... ```）
        json_str = response
        if '```json' in response:
            json_str = response.split('```json')[1].split('```')[0]
        elif '```' in response:
            json_str = response.split('```')[1].split('```')[0]
        elif '{' in response:
            # 找到第一个{和最后一个}
            start = response.find('{')
            end = response.rfind('}')
            if start >= 0 and end > start:
                json_str = response[start:end+1]
            else:
                json_str = response

        result = json.loads(json_str)
        logger.info(f"第一层解析结果keys: {result.keys()}")

        # 兼容不同格式：可能是 {"accident_card": {...}} 或直接 {...}
        acc_card_data = result.get("accident_card", result)

        # 如果关键字段为空，尝试从用户输入中推断
        user_input_lower = user_input.lower()
        
        # 尝试从用户输入中提取列车号
        if not acc_card_data.get("affected_train_ids"):
            import re
            train_match = re.search(r'([GCDZ]\d+)', user_input)
            if train_match:
                acc_card_data["affected_train_ids"] = [train_match.group(1)]
        
        # 尝试推断故障类型
        if not acc_card_data.get("fault_type") or acc_card_data.get("fault_type") == "未知":
            if "风" in user_input:
                acc_card_data["fault_type"] = "大风"
            elif "雨" in user_input:
                acc_card_data["fault_type"] = "暴雨"
            elif "设备" in user_input or "故障" in user_input:
                acc_card_data["fault_type"] = "设备故障"
        
        # 推断scene_category
        if not acc_card_data.get("scene_category"):
            if "限速" in user_input:
                acc_card_data["scene_category"] = "临时限速"
            elif "封锁" in user_input:
                acc_card_data["scene_category"] = "区间封锁"
            else:
                acc_card_data["scene_category"] = "突发故障"
        
        # 尝试从用户输入中提取车站信息
        if not acc_card_data.get("location_code") and not acc_card_data.get("location_name"):
            # 车站名到站码映射
            station_to_code = {
                "石家庄": "SJP", "北京西": "BJX", "保定东": "BDD", "定州东": "DZD",
                "徐水东": "XSD", "涿州东": "ZBD", "高碑店东": "GBD", "正定机场": "ZDJ",
                "高邑西": "GYX", "邢台东": "XTD", "邯郸东": "HDD", "安阳东": "AYD",
                "杜家坎": "DJK"
            }
            for station_name, code in station_to_code.items():
                if station_name in user_input:
                    acc_card_data["location_name"] = station_name
                    acc_card_data["location_code"] = code
                    acc_card_data["affected_section"] = f"{code}-{code}"
                    break
        
        # 判断信息是否完整
        has_train = bool(acc_card_data.get("affected_train_ids"))
        has_location = bool(acc_card_data.get("location_code") or acc_card_data.get("affected_section"))
        has_event = bool(acc_card_data.get("fault_type") and acc_card_data.get("fault_type") != "未知")
        
        if not acc_card_data.get("is_complete"):
            acc_card_data["is_complete"] = has_train and has_location and has_event
        
        if not acc_card_data.get("missing_fields") and not acc_card_data["is_complete"]:
            missing = []
            if not has_train:
                missing.append("列车号")
            if not has_location:
                missing.append("位置")
            if not has_event:
                missing.append("事件类型")
            acc_card_data["missing_fields"] = missing
        
        # 从LLM响应中提取列车ID列表
        affected_train_ids = acc_card_data.get("affected_train_ids", [])
        reported_delay = acc_card_data.get("reported_delay_minutes")
        
        accident_card = AccidentCard(
            fault_type=acc_card_data.get("fault_type", "未知"),
            scene_category=acc_card_data.get("scene_category", "临时限速"),
            start_time=datetime.fromisoformat(acc_card_data.get("start_time", "2024-01-15T10:00:00")) if acc_card_data.get("start_time") else None,
            expected_duration=reported_delay or acc_card_data.get("expected_duration"),
            affected_section=acc_card_data.get("affected_section", ""),
            location_code=acc_card_data.get("location_code", ""),
            location_name=acc_card_data.get("location_name", ""),
            affected_train_ids=affected_train_ids,
            is_complete=acc_card_data.get("is_complete", False),
            missing_fields=acc_card_data.get("missing_fields", [])
        )

        # 使用确定性逻辑构建NetworkSnapshot（不是LLM生成）
        network_snapshot = _build_network_snapshot_deterministic(
            accident_card.location_code,
            accident_card.affected_section,
            snapshot_info
        )

        # 构建DispatchContextMetadata（求解判定）
        dispatch_metadata = DispatchContextMetadata(
            can_solve=accident_card.is_complete,
            missing_info=accident_card.missing_fields,
            observation_corridor=network_snapshot.solving_window.get("observation_corridor", "")
        )

        logger.info(f"第一层完成: scene_category={accident_card.scene_category}, is_complete={accident_card.is_complete}")

        return {
            "accident_card": accident_card,
            "network_snapshot": network_snapshot,
            "dispatch_context_metadata": dispatch_metadata,
            "llm_response": response
        }

    except json.JSONDecodeError as e:
        logger.error(f"第一层JSON解析失败: {e}，使用默认值")

        # 返回默认事故卡片
        default_accident_card = AccidentCard(
            fault_type="未知",
            scene_category="临时限速",
            is_complete=False,
            missing_fields=["scene_category", "start_time", "affected_section"]
        )

        default_metadata = DispatchContextMetadata(
            can_solve=False,
            missing_info=["无法解析LLM响应"]
        )

        return {
            "accident_card": default_accident_card,
            "network_snapshot": NetworkSnapshot(snapshot_time=datetime.now()),
            "dispatch_context_metadata": default_metadata,
            "llm_response": response
        }


# ============== 第二层：Planner层（技能路由） ==============

LAYER2_PROMPT = """
你是铁路调度技能规划助手。根据事故卡片判断问题类型和意图。只输出JSON。

事故卡片：{accident_card}

问题类型和意图（planning_intent）：
- 临时限速场景 -> recalculate_corridor_schedule（重新计算区间时刻表）
- 突发故障恢复 -> recover_from_disruption（恢复故障后运行）
- 区间封锁处理 -> handle_section_block（处理区间中断）

输出格式（严格JSON，只输出一行）：
{{"planning_intent": "recalculate_corridor_schedule", "问题描述": "暴雨导致临时限速", "建议窗口": "XSD-BDD"}}
"""

# ============== L2/L3 分离：求解器策略适配器 ==============

class SolverPolicyAdapter:
    """
    求解器策略适配器
    根据 planning_intent、场景类型、候选列车规模、约束完整度选择合适的求解器
    不在L2层做，而是在L3执行时根据实际情况选择
    """
    
    @staticmethod
    def select_solver(
        planning_intent: str,
        scene_category: str,
        train_count: int,
        is_complete: bool
    ) -> str:
        """
        选择合适的求解器
        
        Args:
            planning_intent: L2层的技能意图
            scene_category: 场景类型
            train_count: 候选列车数量
            is_complete: 信息是否完整
            
        Returns:
            str: 求解器名称
        """
        # 规则1：区间封锁 -> 不求解
        if scene_category == "区间封锁" or planning_intent == "handle_section_block":
            return "noop_scheduler"
        
        # 规则2：信息不完整 -> FCFS
        if not is_complete:
            return "fcfs_scheduler"
        
        # 规则3：列车数量少（<=3）且信息完整 -> MIP
        if train_count <= 3 and is_complete:
            return "mip_scheduler"
        
        # 规则4：列车数量多 -> FCFS
        if train_count > 10:
            return "fcfs_scheduler"
        
        # 规则5：默认 -> MIP
        return "mip_scheduler"


def layer2_planner(
    accident_card: AccidentCard,
    network_snapshot: NetworkSnapshot,
    dispatch_metadata: DispatchContextMetadata
) -> Dict[str, Any]:
    """
    第二层：Planner层（技能路由层）
    LLM决定 planning_intent（问题类型与处理意图），不直接选择求解器

    Args:
        accident_card: 第一层生成的事故卡片
        network_snapshot: 第一层生成的网络快照
        dispatch_metadata: 调度上下文元数据

    Returns:
        Dict: 包含 planning_intent 信息的字典
    """
    logger.info("========== 第二层：Planner层 (LLM决策意图) ==========")

    # 获取RAG检索器，增强Prompt
    rag = get_rag_retriever()

    # 构建基础Prompt
    base_prompt = LAYER2_PROMPT.format(
        accident_card=safe_json_dumps(accident_card.model_dump()),
        network_snapshot=safe_json_dumps(network_snapshot.model_dump()),
        dispatch_context=safe_json_dumps(dispatch_metadata.model_dump())
    )

    # 使用RAG增强Prompt
    query_for_rag = f"场景类型:{accident_card.scene_category},故障类型:{accident_card.fault_type}"
    prompt = rag.format_prompt_with_knowledge(base_prompt, query_for_rag)

    # 调用LLM
    llm = get_llm_caller()
    print(f"[DEBUG] 第二层调用LLM+RAG，prompt长度={len(prompt)}")
    response = llm.call(prompt, max_tokens=512)
    print(f"[DEBUG] 第二层LLM原始响应: {response[:300]}")

    logger.info(f"第二层LLM响应: {response[:200]}...")

    # 解析LLM响应 - 添加JSON容错处理
    try:
        # 提取JSON（处理Markdown代码块）
        json_str = response
        if '```json' in response:
            json_str = response.split('```json')[1].split('```')[0]
        elif '```' in response:
            json_str = response.split('```')[1].split('```')[0]
        elif '{' in response:
            start = response.find('{')
            end = response.rfind('}')
            if start >= 0 and end > start:
                json_str = response[start:end+1]
            else:
                json_str = response

        result = json.loads(json_str)
        logger.info(f"第二层解析结果keys: {result.keys()}")

        # 提取 planning_intent
        planning_intent = result.get("planning_intent", "")
        
        # 如果没有 planning_intent，尝试从旧格式转换
        if not planning_intent:
            if "主技能" in result:
                # 兼容旧格式：根据主技能反推 intent
                main_skill = result.get("主技能", "")
                if main_skill == "mip_scheduler":
                    planning_intent = "recalculate_corridor_schedule"
                elif main_skill == "fcfs_scheduler":
                    planning_intent = "recover_from_disruption"
                elif main_skill == "noop_scheduler":
                    planning_intent = "handle_section_block"

        logger.info(f"第二层完成: planning_intent={planning_intent}")

        logger.info(f"第二层完成: planning_intent={planning_intent}")

        # 如果 LLM 返回的是旧格式 {"主技能": "noop_scheduler", "reasoning": "..."}
        # 需要根据 scene_category 修正为正确的求解器
        # 临时限速场景应使用 mip_scheduler
        
        # 如果 LLM 已经返回了 skill_dispatch，使用它的选择
        if "主技能" in result:
            # 使用 LLM 返回的求解器
            main_skill = result.get("主技能", "mip_scheduler")

            # 根据场景类型强制修正（因为 LLM 经常返回错误的求解器）
            scene = accident_card.scene_category
            logger.info(f"[DEBUG L2] 场景类型: {scene}, LLM返回: {main_skill}")

            # 临时限速场景应该用 MIP（强制覆盖LLM的错误选择）
            if scene == "临时限速":
                logger.warning(f"临时限速场景强制使用mip_scheduler（原LLM返回: {main_skill}）")
                main_skill = "mip_scheduler"
            # 突发故障场景应该用 FCFS
            elif scene == "突发故障":
                logger.warning(f"突发故障场景强制使用fcfs_scheduler（原LLM返回: {main_skill}）")
                main_skill = "fcfs_scheduler"
            # 区间封锁场景应该用 noop
            elif scene == "区间封锁":
                logger.warning(f"区间封锁场景强制使用noop_scheduler（原LLM返回: {main_skill}）")
                main_skill = "noop_scheduler"
            
            logger.info(f"[DEBUG L2] 最终主技能: {main_skill}")
            
            skill_dispatch = {
                "是否进入技能求解": result.get("是否进入技能求解", True),
                "主技能": main_skill,
                "辅助技能": result.get("辅助技能", []),
                "调用顺序": result.get("调用顺序", [main_skill]),
                "阻塞项": result.get("阻塞项", []),
                "需补充信息": result.get("需补充信息", [])
            }
        else:
            # 没有 skill_dispatch，根据 planning_intent 映射
            skill_dispatch = {
                "是否进入技能求解": True,
                "主技能": "mip_scheduler" if planning_intent == "recalculate_corridor_schedule" else 
                         "fcfs_scheduler" if planning_intent == "recover_from_disruption" else 
                         "noop_scheduler",
                "辅助技能": [],
                "调用顺序": ["mip_scheduler"] if planning_intent == "recalculate_corridor_schedule" else 
                           ["fcfs_scheduler"] if planning_intent == "recover_from_disruption" else 
                           ["noop_scheduler"],
                "阻塞项": [],
                "需补充信息": []
            }
        
        return {
            "planning_intent": planning_intent,
            "skill_dispatch": skill_dispatch,
            "问题描述": result.get("问题描述", ""),
            "建议窗口": result.get("建议窗口", ""),
            "reasoning": result.get("reasoning", ""),
            "llm_response": response
        }

    except json.JSONDecodeError as e:
        logger.error(f"第二层JSON解析失败: {e}")
        logger.error(f"LLM原始响应: {response[:500]}")

        # 根据场景类型默认 intent
        if accident_card.scene_category == "临时限速":
            default_intent = "recalculate_corridor_schedule"
        elif accident_card.scene_category == "突发故障":
            default_intent = "recover_from_disruption"
        else:
            default_intent = "handle_section_block"

        logger.info(f"第二层使用默认intent: {default_intent}")

        # 兼容新旧格式
        skill_dispatch = {
            "是否进入技能求解": True,
            "主技能": "mip_scheduler" if default_intent == "recalculate_corridor_schedule" else 
                     "fcfs_scheduler" if default_intent == "recover_from_disruption" else 
                     "noop_scheduler",
            "辅助技能": [],
            "调用顺序": ["mip_scheduler"] if default_intent == "recalculate_corridor_schedule" else 
                       ["fcfs_scheduler"] if default_intent == "recover_from_disruption" else 
                       ["noop_scheduler"],
            "阻塞项": [],
            "需补充信息": []
        }

        return {
            "planning_intent": default_intent,
            "skill_dispatch": skill_dispatch,
            "问题描述": f"JSON解析失败({e})",
            "建议窗口": "",
            "reasoning": "JSON解析失败，使用默认intent",
            "llm_response": response
        }


# ============== 第三层：求解技能层 ==============

def layer3_solver_execution(
    planning_intent: str,
    accident_card: AccidentCard,
    network_snapshot: NetworkSnapshot,
    trains: List[Any],
    stations: List[Any]
) -> Dict[str, Any]:
    """
    第三层：求解技能层
    根据 L2 的 planning_intent 和 SolverPolicyAdapter 选择并执行求解器

    Args:
        planning_intent: 第二层输出的技能意图
        accident_card: 事故卡片
        network_snapshot: 网络快照
        trains: 列车数据
        stations: 车站数据

    Returns:
        Dict: 包含 skill_execution_result 的字典
    """
    logger.info("========== 第三层：求解技能层 (执行) ==========")

    # 使用 SolverPolicyAdapter 选择求解器
    solver_policy = SolverPolicyAdapter()
    main_skill = solver_policy.select_solver(
        planning_intent=planning_intent,
        scene_category=accident_card.scene_category,
        train_count=network_snapshot.train_count if hasattr(network_snapshot, 'train_count') else 0,
        is_complete=accident_card.is_complete
    )

    logger.info(f"第三层执行: planning_intent={planning_intent}, 选择的求解器={main_skill}")

    # 导入求解器
    from solver.solver_registry import get_default_registry
    from solver.base_solver import SolverRequest

    # 获取求解器
    registry = get_default_registry()
    solver = registry.get_solver(main_skill)

    if solver is None:
        # 尝试获取适配器
        from solver.mip_adapter import MIPSolverAdapter
        from solver.fcfs_adapter import FCFSSolverAdapter

        if main_skill == "mip_scheduler":
            solver = MIPSolverAdapter()
        elif main_skill == "fcfs_scheduler":
            solver = FCFSSolverAdapter()
        else:
            solver = MIPSolverAdapter()  # 默认

    # 构建求解器请求
    solver_request = SolverRequest(
        scene_type=accident_card.scene_category,
        scene_id="llm_workflow_001",
        trains=trains,
        stations=stations,
        injected_delays=[{
            "train_id": network_snapshot.trains[0].get("train_id") if network_snapshot.trains else "G1215",
            "location": {"station_code": accident_card.location_code},
            "initial_delay_seconds": 600
        }],
        solver_config={},
        metadata={
            "accident_card": accident_card.model_dump(),
            "network_snapshot": network_snapshot.model_dump()
        }
    )

    # 执行求解
    try:
        solver_response = solver.solve(solver_request)

        logger.info(f"第三层完成: 求解状态={solver_response.status}, 成功={solver_response.success}")

        # 提取metrics中的延误信息（注意：原始数据是秒，需要转换为分钟）
        metrics = solver_response.metrics or {}
        total_delay_seconds = metrics.get("total_delay_seconds", 0)
        max_delay_seconds = metrics.get("max_delay_seconds", 0)
        total_delay = total_delay_seconds // 60
        max_delay = max_delay_seconds // 60

        return {
            "skill_execution_result": {
                "skill_name": main_skill,
                "execution_status": solver_response.status,
                "success": solver_response.success,
                "solving_time": solver_response.solving_time_seconds,
                "total_delay_minutes": total_delay,
                "max_delay_minutes": max_delay
            },
            "solver_response": solver_response.model_dump() if hasattr(solver_response, 'model_dump') else {
                "success": solver_response.success,
                "status": solver_response.status,
                "total_delay_minutes": total_delay,
                "max_delay_minutes": max_delay,
                "message": solver_response.message
            },
            "llm_response": f"执行{main_skill}，状态: {solver_response.status}"
        }

    except Exception as e:
        logger.error(f"第三层执行失败: {e}")

        return {
            "skill_execution_result": {
                "skill_name": main_skill,
                "execution_status": "error",
                "success": False,
                "error_message": str(e),
                "total_delay_minutes": 0,
                "max_delay_minutes": 0
            },
            "solver_response": None,
            "llm_response": f"执行失败: {str(e)}"
        }


# ============== 第四层：结果输出与评估层 ==============

LAYER4_PROMPT = """
你是铁路调度方案评估助手。评估求解结果，生成解释和风险提示。只输出JSON。

求解结果：{execution_result}

输出格式（严格JSON，只输出一行）：
{{"llm_summary": "方案可行，总延误10分钟", "risk_warnings": ["建议监控G1215列车"], "feasibility_score": 0.9, "constraint_check": {{"时间约束": true, "空间约束": true}}}}
"""

# ============== L4 决策策略引擎 ==============

class PolicyEngine:
    """
    策略引擎
    根据结构化评估结果做最终决策（是否采用主解/回退基线/重新求解）
    不由LLM直接决策，而是根据固定规则
    """
    
    @staticmethod
    def make_decision(
        feasibility_score: float,
        is_successful: bool,
        max_delay_minutes: int,
        risk_warnings: List[str]
    ) -> Dict[str, Any]:
        """
        根据评估结果做最终决策
        
        Args:
            feasibility_score: 可行性评分 (0-1)
            is_successful: 求解是否成功
            max_delay_minutes: 最大延误（分钟）
            risk_warnings: 风险提示列表
            
        Returns:
            Dict: 包含 needs_rerun, decision, reason
        """
        # 规则1：求解失败 -> 重新求解
        if not is_successful:
            return {
                "needs_rerun": True,
                "decision": "rerun",
                "reason": "求解执行失败",
                "suggested_fixes": ["检查求解器配置", "尝试其他求解器"]
            }
        
        # 规则2：可行评分过低 (<0.5) -> 重新求解
        if feasibility_score < 0.5:
            return {
                "needs_rerun": True,
                "decision": "rerun",
                "reason": f"可行评分过低 ({feasibility_score:.2f})",
                "suggested_fixes": ["补充更多约束条件", "调整求解时间限制"]
            }
        
        # 规则3：最大延误过大 (>30分钟) -> 回退到基线
        if max_delay_minutes > 30:
            return {
                "needs_rerun": False,
                "decision": "rollback_baseline",
                "reason": f"最大延误过大 ({max_delay_minutes}分钟)",
                "suggested_fixes": ["等待延误自然消除", "调整列车发车间隔"]
            }
        
        # 规则4：有严重风险警告 -> 回退
        severe_warnings = [w for w in risk_warnings if "严重" in w or "危险" in w]
        if severe_warnings:
            return {
                "needs_rerun": False,
                "decision": "rollback_baseline",
                "reason": f"存在严重风险: {severe_warnings}",
                "suggested_fixes": ["人工干预确认"]
            }
        
        # 规则5：默认 -> 采用主解
        return {
            "needs_rerun": False,
            "decision": "adopt_primary",
            "reason": "方案可行，采用主解",
            "suggested_fixes": []
        }


def layer4_evaluation(
    skill_execution_result: Dict[str, Any],
    solver_response: Any
) -> Dict[str, Any]:
    """
    第四层：结果输出与评估层
    LLM提供解释/摘要/风险提示，PolicyEngine做最终决策

    Args:
        skill_execution_result: 第三层执行结果
        solver_response: 求解器响应

    Returns:
        Dict: 包含 evaluation_report, ranking_result, rollback_feedback
    """
    logger.info("========== 第四层：结果输出与评估层 (LLM解释+Policy决策) ==========")

    # 如果求解失败，直接返回回退反馈
    if not skill_execution_result.get("success", False):
        rollback_feedback = RollbackFeedback(
            needs_rerun=True,
            rollback_reason="求解执行失败",
            suggested_fixes=["检查求解器配置", "尝试其他求解器"]
        )

        logger.info("第四层完成: 需要回退（求解失败）")

        return {
            "evaluation_report": None,
            "ranking_result": None,
            "rollback_feedback": rollback_feedback,
            "llm_summary": "求解执行失败，无法生成摘要"
        }

    # 获取RAG检索器，增强Prompt
    rag = get_rag_retriever()

    # 构建基础Prompt
    base_prompt = LAYER4_PROMPT.format(
        execution_result=safe_json_dumps(skill_execution_result),
        solver_result=safe_json_dumps(solver_response.model_dump() if hasattr(solver_response, 'model_dump') else {})
    )

    # 使用RAG增强Prompt
    prompt = rag.format_prompt_with_knowledge(base_prompt, "评估调度方案")

    # 调用LLM
    llm = get_llm_caller()
    print(f"[DEBUG] 第四层调用LLM+RAG，prompt长度={len(prompt)}")
    print(f"[DEBUG] 第四层prompt前200字: {prompt[:200]}")
    response = llm.call(prompt, max_tokens=512)
    print(f"[DEBUG] 第四层LLM原始响应: {response[:300]}")

    logger.info(f"第四层LLM响应: {response[:200]}...")

    # 解析LLM响应
    try:
        # 提取JSON（处理Markdown代码块）
        json_str = response
        if '```json' in response:
            json_str = response.split('```json')[1].split('```')[0]
        elif '```' in response:
            json_str = response.split('```')[1].split('```')[0]
        elif '{' in response:
            start = response.find('{')
            end = response.rfind('}')
            if start >= 0 and end > start:
                json_str = response[start:end+1]
            else:
                json_str = response

        result = json.loads(json_str)
        logger.info(f"第四层解析结果keys: {result.keys()}")

        # 提取 LLM 生成的解释和评分
        llm_summary = result.get("llm_summary", "方案评估完成")
        risk_warnings = result.get("risk_warnings", [])
        feasibility_score = result.get("feasibility_score", 0.8)  # 默认0.8
        constraint_check = result.get("constraint_check", {})

        # 使用 PolicyEngine 做最终决策
        policy = PolicyEngine()
        policy_decision = policy.make_decision(
            feasibility_score=feasibility_score,
            is_successful=skill_execution_result.get("success", False),
            max_delay_minutes=skill_execution_result.get("max_delay_minutes", 0),
            risk_warnings=risk_warnings
        )

        logger.info(f"第四层Policy决策: {policy_decision['decision']}, reason={policy_decision['reason']}")

        # 构建评估报告
        evaluation_report = EvaluationReport(
            solution_id="solution_001",
            is_feasible=feasibility_score >= 0.5,
            total_delay_minutes=skill_execution_result.get("total_delay_minutes", 0),
            max_delay_minutes=skill_execution_result.get("max_delay_minutes", 0),
            solving_time_seconds=skill_execution_result.get("solving_time", 0),
            risk_warnings=risk_warnings,
            constraint_satisfaction=constraint_check
        )

        # 构建回退反馈
        rollback_feedback = RollbackFeedback(
            needs_rerun=policy_decision["needs_rerun"],
            rollback_reason=policy_decision["reason"],
            suggested_fixes=policy_decision.get("suggested_fixes", [])
        )

        logger.info(f"第四层完成: 可行={evaluation_report.is_feasible}, 需要回退={rollback_feedback.needs_rerun}")

        return {
            "evaluation_report": evaluation_report,
            "ranking_result": None,  # L4不再做排序，由Policy决定
            "rollback_feedback": rollback_feedback,
            "llm_summary": llm_summary
        }

    except json.JSONDecodeError as e:
        logger.error(f"第四层JSON解析失败: {e}，使用默认评估")

        # 默认评估（假设可行）
        default_evaluation = EvaluationReport(
            solution_id="solution_001",
            is_feasible=skill_execution_result.get("success", False),
            solving_time_seconds=skill_execution_result.get("solving_time", 0)
        )

        default_rollback = RollbackFeedback(
            needs_rerun=not skill_execution_result.get("success", False),
            rollback_reason="JSON解析失败，使用默认评估"
        )

        return {
            "evaluation_report": default_evaluation,
            "ranking_result": None,
            "rollback_feedback": default_rollback,
            "llm_summary": "JSON解析失败，使用默认评估"
        }


# ============== 统一入口：LLM驱动的完整工作流 ==============

def run_llm_workflow(
    user_input: str,
    snapshot_info: Dict[str, Any],
    trains: List[Any] = None,
    stations: List[Any] = None,
    canonical_request: Any = None
) -> WorkflowResult:
    """
    运行LLM驱动的完整5层工作流

    架构说明（v3.1）：
    - L0：预处理层 - 将不同输入转换为 CanonicalDispatchRequest
    - L1：数据建模层 - LLM辅助判断场景类型，NetworkSnapshot由确定性逻辑切出
    - L2：Planner层 - LLM决策 planning_intent（问题类型与处理意图）
    - L3：Solver执行层 - SolverPolicyAdapter 根据 intent 选择求解器并执行
    - L4：评估层 - LLM提供解释/摘要/风险提示，PolicyEngine做最终决策

    Args:
        user_input: 调度员自然语言描述
        snapshot_info: 运行状态快照
        trains: 列车数据
        stations: 车站数据
        canonical_request: L0 预处理的标准化请求（可选）

    Returns:
        WorkflowResult: 完整的工作流结果
    """
    logger.info("=" * 60)
    logger.info("开始 LLM 驱动的4层工作流")
    logger.info(f"输入: user_input={user_input}")
    logger.info(f"输入: snapshot_info keys={snapshot_info.keys()}")
    logger.info("=" * 60)

    debug_trace = {}
    start_time = datetime.now()

    try:
        logger.info("=== 执行第一层 ===")
        # 第一层：数据建模层（如果L0有预处理结果则传入）
        layer1_result = layer1_data_modeling(user_input, snapshot_info, canonical_request)
        accident_card = layer1_result["accident_card"]
        network_snapshot = layer1_result["network_snapshot"]
        dispatch_metadata = layer1_result["dispatch_context_metadata"]

        debug_trace["layer1"] = {
            "stage": "data_modeling",
            "accident_card": accident_card.model_dump(),
            "network_snapshot": network_snapshot.model_dump(),
            "llm_response": layer1_result.get("llm_response", "")[:100]
        }

        # 如果第一层判定不可求解，返回失败
        if not dispatch_metadata.can_solve:
            return WorkflowResult(
                success=False,
                scene_spec=SceneSpec(scene_type=accident_card.scene_category, scene_id="llm_001"),
                solver_result=None,
                validation_report=None,
                debug_trace=debug_trace,
                message="第一层判定：信息不完整，无法进入求解",
                error="layer1_incomplete",
                metadata={"layer": 1, "missing_info": dispatch_metadata.missing_info}
            )

        # 第二层：Planner层（技能路由）
        logger.info("=== 执行第二层 ===")
        layer2_result = layer2_planner(accident_card, network_snapshot, dispatch_metadata)
        planning_intent = layer2_result["planning_intent"]
        logger.info(f"第二层返回planning_intent: {planning_intent}")

        debug_trace["layer2"] = {
            "stage": "planner",
            "planning_intent": planning_intent,
            "llm_response": layer2_result.get("llm_response", "")[:100]
        }

        # 第三层：求解技能层（实际执行）
        logger.info("调用第三层: layer3_solver_execution")
        layer3_result = layer3_solver_execution(
            planning_intent,
            accident_card,
            network_snapshot,
            trains or [],
            stations or []
        )
        logger.info(f"第三层返回: {layer3_result.keys()}")
        skill_execution = layer3_result["skill_execution_result"]
        solver_response = layer3_result["solver_response"]

        debug_trace["layer3"] = {
            "stage": "solver_execution",
            "execution_result": skill_execution,
            "llm_response": layer3_result.get("llm_response", "")[:100]
        }

        # 第四层：评估层
        layer4_result = layer4_evaluation(skill_execution, solver_response)
        evaluation_report = layer4_result["evaluation_report"]
        ranking_result = layer4_result["ranking_result"]
        rollback_feedback = layer4_result["rollback_feedback"]

        debug_trace["layer4"] = {
            "stage": "evaluation",
            "evaluation": evaluation_report.model_dump() if evaluation_report else None,
            "rollback": rollback_feedback.model_dump() if rollback_feedback else None
        }

        # 构建SolverResult
        solver_result = None
        if solver_response:
            schedule = solver_response.schedule
            if isinstance(schedule, dict):
                schedule_list = []
                for train_id, stops in schedule.items():
                    if isinstance(stops, list):
                        for stop in stops:
                            schedule_list.append({"train_id": train_id, **stop})
                schedule = schedule_list

            solver_result = SolverResult(
                success=solver_response.success,
                schedule=schedule if isinstance(schedule, list) else [],
                metrics=solver_response.metrics,
                solving_time_seconds=solver_response.solving_time_seconds,
                solver_type=solver_response.solver_type
            )

        # 判断最终结果
        final_success = (
            skill_execution.get("success", False) and
            (evaluation_report is None or evaluation_report.is_feasible) and
            not rollback_feedback.needs_rerun
        )

        execution_time = (datetime.now() - start_time).total_seconds()

        logger.info(f"LLM工作流完成: 成功={final_success}, 耗时={execution_time:.2f}秒")

        return WorkflowResult(
            success=final_success,
            scene_spec=SceneSpec(
                scene_type=accident_card.scene_category,
                scene_id="llm_001",
                description=accident_card.fault_type
            ),
            task_plan=None,
            solver_result=solver_result,
            validation_report=None,
            evaluation_report=evaluation_report,
            ranking_result=ranking_result,
            rollback_feedback=rollback_feedback,
            debug_trace=debug_trace,
            message="LLM驱动的4层工作流执行完成",
            metadata={
                "execution_time": execution_time,
                "layers_executed": [1, 2, 3, 4]
            }
        )

    except Exception as e:
        import traceback
        logger.error(f"LLM工作流执行失败: {e}")
        logger.error(f"完整堆栈: {traceback.format_exc()}")

        return WorkflowResult(
            success=False,
            scene_spec=None,
            task_plan=None,
            solver_result=None,
            validation_report=None,
            debug_trace=debug_trace,
            message=f"LLM工作流执行失败: {str(e)}",
            error=str(e),
            metadata={"execution_time": (datetime.now() - start_time).total_seconds()}
        )


# ============== 测试代码 ==============

if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(level=logging.INFO)

    # 测试输入
    test_user_input = "G1215列车在徐水东站因暴雨限速60km/h，预计延误10分钟"

    test_snapshot_info = {
        "snapshot_time": "2024-01-15T10:00:00",
        "time_window": {
            "start": "2024-01-15T10:00:00",
            "end": "2024-01-15T12:00:00"
        },
        "current_delays": {
            "G1215": 600
        },
        "trains": [
            {"train_id": "G1215", "current_position": "XSD", "current_delay": 600}
        ],
        "stations": [
            {"station_code": "XSD", "track_count": 4}
        ]
    }

    # 运行LLM工作流
    result = run_llm_workflow(test_user_input, test_snapshot_info)

    print("\n" + "=" * 60)
    print("LLM工作流执行结果")
    print("=" * 60)
    print(f"成功: {result.success}")
    print(f"消息: {result.message}")
    print(f"场景类型: {result.scene_spec.scene_type if result.scene_spec else 'N/A'}")
    print(f"执行时间: {result.metadata.get('execution_time', 0):.2f}秒")
    print(f"执行层级: {result.metadata.get('layers_executed', [])}")

    if result.rollback_feedback:
        print(f"\n回退反馈:")
        print(f"  需要回退: {result.rollback_feedback.needs_rerun}")
        print(f"  回退原因: {result.rollback_feedback.rollback_reason}")

    if result.debug_trace:
        print(f"\n层级追踪:")
        for layer, info in result.debug_trace.items():
            print(f"  {layer}: {info.get('stage', 'unknown')}")