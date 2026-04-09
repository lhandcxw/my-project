# -*- coding: utf-8 -*-
"""
会话管理模块
用于管理LLM驱动的4层工作流的多轮对话状态
"""

import uuid
import threading
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field


@dataclass
class WorkflowSession:
    """工作流会话"""
    session_id: str
    user_input: str
    snapshot_info: Dict[str, Any]
    current_layer: int  # 当前执行到的层 (1-4)

    # 各层执行结果
    layer1_result: Optional[Dict] = None  # accident_card, network_snapshot
    layer2_result: Optional[Dict] = None  # skill_dispatch
    layer3_result: Optional[Dict] = None  # solver_result
    layer4_result: Optional[Dict] = None  # evaluation_report, rollback_feedback

    # 元数据
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    is_complete: bool = False

    # 对话历史 (用于前端展示)
    messages: List[Dict[str, str]] = field(default_factory=list)

    def add_message(self, role: str, content: str):
        """添加对话消息"""
        self.messages.append({"role": role, "content": content})
        self.updated_at = time.time()

    def get_progress(self) -> str:
        """获取执行进度描述"""
        layer_names = {
            1: "数据建模层",
            2: "Planner层",
            3: "求解技能层",
            4: "评估层"
        }
        if self.current_layer == 0:
            return "等待开始"
        elif self.current_layer <= 4:
            return f"执行中: {layer_names.get(self.current_layer, '未知')}"
        else:
            return "已完成"


class SessionManager:
    """
    会话管理器
    线程安全，支持多会话并发
    """

    def __init__(self):
        self._sessions: Dict[str, WorkflowSession] = {}
        self._lock = threading.RLock()

    def create_session(
        self,
        user_input: str,
        snapshot_info: Dict[str, Any]
    ) -> str:
        """
        创建新会话

        Args:
            user_input: 用户输入
            snapshot_info: 快照信息

        Returns:
            str: session_id
        """
        session_id = str(uuid.uuid4())[:8]
        session = WorkflowSession(
            session_id=session_id,
            user_input=user_input,
            snapshot_info=snapshot_info,
            current_layer=0
        )

        with self._lock:
            self._sessions[session_id] = session

        # 添加初始用户消息
        session.add_message("user", user_input)

        return session_id

    def get_session(self, session_id: str) -> Optional[WorkflowSession]:
        """获取会话"""
        with self._lock:
            return self._sessions.get(session_id)

    def update_layer_result(
        self,
        session_id: str,
        layer: int,
        result: Dict[str, Any]
    ) -> bool:
        """
        更新指定层的执行结果

        Args:
            session_id: 会话ID
            layer: 层号 (1-4)
            result: 该层的执行结果

        Returns:
            bool: 是否更新成功
        """
        session = self.get_session(session_id)
        if session is None:
            return False

        with self._lock:
            if layer == 1:
                session.layer1_result = result
                # 添加系统消息 - 处理Pydantic模型或字典
                if isinstance(result.get("accident_card"), dict):
                    acc = result.get("accident_card", {})
                    scene = acc.get("scene_category", "未知")
                    section = acc.get("affected_section", "")
                else:
                    # Pydantic模型
                    acc = result.get("accident_card")
                    scene = acc.scene_category if acc else "未知"
                    section = acc.affected_section if acc else ""
                session.add_message("system", f"[第1层] 识别场景: {scene}，影响区段: {section}")
            elif layer == 2:
                session.layer2_result = result
                skill = result.get("skill_dispatch", {}).get("主技能", "未知")
                session.add_message("system", f"[第2层] 选择求解器: {skill}")
            elif layer == 3:
                session.layer3_result = result
                success = result.get("success", False)
                if success:
                    delay = result.get("total_delay_minutes", 0)
                    session.add_message("system", f"[第3层] 求解完成，总延误: {delay}分钟")
                else:
                    session.add_message("system", f"[第3层] 求解失败: {result.get('message', '未知错误')}")
            elif layer == 4:
                session.layer4_result = result
                eval_report = result.get("evaluation_report")
                # 处理Pydantic模型或字典
                if eval_report:
                    if isinstance(eval_report, dict):
                        is_feasible = eval_report.get("is_feasible", False)
                    else:
                        is_feasible = eval_report.is_feasible if hasattr(eval_report, 'is_feasible') else False

                    if is_feasible:
                        session.add_message("system", "[第4层] 方案评估: 可行")
                        session.is_complete = True
                    else:
                        rollback = result.get("rollback_feedback", {})
                        if isinstance(rollback, dict):
                            needs_rerun = rollback.get("needs_rerun", False)
                        else:
                            needs_rerun = rollback.needs_rerun if hasattr(rollback, 'needs_rerun') else False
                        if needs_rerun:
                            session.add_message("system", f"[第4层] 需要回退")
                        else:
                            session.add_message("system", "[第4层] 方案评估: 可行")

            session.current_layer = layer
            session.updated_at = time.time()

        return True

    def get_session_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话状态"""
        session = self.get_session(session_id)
        if session is None:
            return None

        return {
            "session_id": session.session_id,
            "original_input": session.user_input,
            "progress": session.get_progress(),
            "current_layer": session.current_layer,
            "is_complete": session.is_complete,
            "messages": session.messages,
            "layer1_result": session.layer1_result,
            "layer2_result": session.layer2_result,
            "layer3_result": session.layer3_result,
            "layer4_result": session.layer4_result
        }

    def list_sessions(self) -> List[str]:
        """列出所有会话ID"""
        with self._lock:
            return list(self._sessions.keys())

    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                return True
        return False

    def cleanup_old_sessions(self, max_age_seconds: int = 3600):
        """清理过期会话（默认1小时）"""
        current_time = time.time()
        with self._lock:
            to_delete = [
                sid for sid, sess in self._sessions.items()
                if current_time - sess.updated_at > max_age_seconds
            ]
            for sid in to_delete:
                del self._sessions[sid]
            return len(to_delete)


# 全局会话管理器
_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """获取全局会话管理器"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager