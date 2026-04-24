// 京广高铁调度辅助系统 - 前端脚本
// 版本: V2.0 专业版

// 全局变量
let isProcessing = false;
let currentResult = null;
let currentThinkingMessage = null;  // 当前的思考消息元素
let thinkingBuffer = [];  // 思考内容缓冲区
let thinkingTimer = null;  // 定时器

// 快捷场景模板 - 专业的调度场景
const quickPrompts = {
    'limit_speed': 'G1563在石家庄站因大风临时限速，预计延误15分钟',
    'failure': 'G1565在保定东站发生设备故障，预计延误30分钟',
    'block': '涿州东到高碑店东区间因施工封锁，预计影响多列列车',
    'delay': 'G1567在北京西站发车延误5分钟，需要调整后续时刻'
};

/**
 * 填充快捷场景提示
 * @param {string} type - 场景类型
 */
function fillQuickPrompt(type) {
    const prompt = quickPrompts[type];
    if (prompt) {
        document.getElementById('chatInput').value = prompt;
        document.getElementById('chatInput').focus();
    }
}

/**
 * 发送调度需求
 * 核心交互函数，处理用户输入并调用流式API
 */
async function sendChat() {
    if (isProcessing) {
        showSystemMessage('当前有任务正在处理中，请稍候...');
        return;
    }

    const input = document.getElementById('chatInput');
    const prompt = input.value.trim();

    if (!prompt) {
        alert('请输入调度需求信息\n\n建议包含以下信息：\n• 车次号（如：G1563）\n• 车站或区间名称\n• 故障类型\n• 预计延误时间');
        return;
    }

    // 显示用户消息
    appendMessage('user', prompt);
    input.value = '';

    // 开始处理
    isProcessing = true;
    updateSendButton(true);
    resetProgress();

    try {
        // 调用流式 API
        const response = await fetch('/api/agent_chat_stream', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ prompt: prompt })
        });

        if (!response.ok) {
            if (response.status === 500) {
                throw new Error('服务器内部错误，请稍后重试或联系技术支持');
            } else if (response.status === 503) {
                throw new Error('服务暂时不可用，请检查API配置或稍后重试');
            } else {
                throw new Error(`服务器响应异常 (HTTP ${response.status})`);
            }
        }

        // 处理流式响应
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const data = line.slice(6);
                    if (data.trim()) {
                        try {
                            const event = JSON.parse(data);
                            handleStreamEvent(event);
                        } catch (e) {
                            console.warn('流式数据解析失败:', e, data);
                        }
                    }
                }
            }
        }

    } catch (error) {
        console.error('请求失败:', error);
        let errorMessage = '';

        // 友好的错误提示
        if (error.message.includes('Failed to fetch')) {
            errorMessage = '网络连接失败，请检查：\n1. 服务器是否正常运行\n2. 网络连接是否正常\n3. 防火墙设置是否正确';
        } else {
            errorMessage = '处理失败：' + error.message + '\n\n如问题持续，请联系技术支持并提供错误信息。';
        }

        appendMessage('error', errorMessage);
    } finally {
        isProcessing = false;
        updateSendButton(false);
    }
}

/**
 * 处理流式事件
 * @param {Object} event - 流式事件对象
 */
function handleStreamEvent(event) {
    switch (event.type) {
        case 'start':
            flushThinkingMessages();  // 先刷新缓冲的消息
            appendMessage('thinking', '【系统】开始处理调度需求...');
            break;

        case 'thinking':
            // 将 thinking 内容加入缓冲区，而不是立即显示
            thinkingBuffer.push(event.content);
            // 500ms 后刷新一次，避免过于频繁的更新
            if (thinkingTimer) clearTimeout(thinkingTimer);
            thinkingTimer = setTimeout(() => {
                flushThinkingMessages();
            }, 500);
            break;

        case 'progress':
            flushThinkingMessages();  // 进度更新时，先刷新缓冲的 thinking 消息
            updateProgressStep(event.layer, event.message);
            break;

        case 'result':
            flushThinkingMessages();  // 结果返回时，刷新剩余的 thinking 消息
            currentResult = event.data;
            displayResult(event.data);
            appendMessage('agent', '【完成】调度方案已生成，请查看右侧信息面板和详细方案。');
            break;

        case 'error':
            flushThinkingMessages();  // 错误时，刷新缓冲的 thinking 消息
            let errorMsg = '处理失败：' + event.message;
            if (event.message.includes('API')) {
                errorMsg = '【API错误】' + event.message + '\n\n请检查DASHSCOPE_API_KEY环境变量是否正确配置。';
            }
            appendMessage('error', errorMsg);
            break;

        default:
            console.warn('未知事件类型:', event.type);
    }
}

/**
 * 刷新缓冲的 thinking 消息
 */
function flushThinkingMessages() {
    if (thinkingBuffer.length === 0) return;

    // 合并所有缓冲的 thinking 内容
    const combinedContent = thinkingBuffer.map(content => content).join('<br>');
    appendMessage('thinking', combinedContent);
    thinkingBuffer = [];
    scrollToBottom();
}

/**
 * 添加消息到聊天区
 * @param {string} type - 消息类型
 * @param {string} content - 消息内容
 */
function appendMessage(type, content) {
    const chatMessages = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `chat-message chat-${type}`;
    messageDiv.innerHTML = content;
    chatMessages.appendChild(messageDiv);
    scrollToBottom();
}

/**
 * 显示系统消息
 * @param {string} message - 消息内容
 */
function showSystemMessage(message) {
    appendMessage('system', message);
}

/**
 * 滚动聊天区到底部
 */
function scrollToBottom() {
    const chatMessages = document.getElementById('chatMessages');
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

/**
 * 更新发送按钮状态
 * @param {boolean} processing - 是否正在处理
 */
function updateSendButton(processing) {
    const sendBtn = document.getElementById('sendBtn');
    if (processing) {
        sendBtn.disabled = true;
        sendBtn.innerHTML = '处理中<span class="loading-dots"></span>';
        sendBtn.style.background = '#9e9e9e';
    } else {
        sendBtn.disabled = false;
        sendBtn.innerHTML = '提交需求';
        sendBtn.style.background = '#1a237e';
    }
}

/**
 * 重置进度和指标
 */
function resetProgress() {
    // 重置进度步骤
    for (let i = 1; i <= 4; i++) {
        const step = document.getElementById(`step${i}`);
        if (step) {
            step.className = 'progress-step';
        }
    }

    // 重置进度文本
    document.getElementById('progressText').textContent = '等待输入调度需求...';

    // 重置指标
    document.getElementById('metricTotalDelay').textContent = '-';
    document.getElementById('metricMaxDelay').textContent = '-';
    document.getElementById('metricOnTime').textContent = '-';
    document.getElementById('metricGrade').textContent = '-';

    // 重置风险提示
    document.getElementById('riskWarnings').innerHTML = '<div style="font-size: 13px; color: #999; text-align: center; padding: 25px;">暂无风险提示</div>';

    // 隐藏详细方案
    document.getElementById('detailSection').style.display = 'none';
    document.getElementById('detailContent').style.display = 'none';

    // 重置 thinking 缓冲区
    thinkingBuffer = [];
    if (thinkingTimer) {
        clearTimeout(thinkingTimer);
        thinkingTimer = null;
    }
    currentThinkingMessage = null;
}

/**
 * 更新进度步骤
 * @param {number} layer - 层级 (1-4)
 * @param {string} message - 进度消息
 */
function updateProgressStep(layer, message) {
    // 标记之前的步骤为完成
    for (let i = 1; i < layer; i++) {
        const step = document.getElementById(`step${i}`);
        if (step) {
            step.classList.add('done');
        }
    }

    // 标记当前步骤为活跃
    const currentStep = document.getElementById(`step${layer}`);
    if (currentStep) {
        currentStep.classList.add('active');
    }

    // 更新进度文本
    document.getElementById('progressText').textContent = message;
}

/**
 * 显示处理结果
 * @param {Object} data - 结果数据
 */
function displayResult(data) {
    if (!data) {
        appendMessage('error', '未收到有效结果数据');
        return;
    }

    try {
        // 更新指标
        const stats = data.delay_statistics || {};
        const evalReport = data.evaluation_report || {};

        const totalDelay = Math.round((stats.total_delay_seconds || 0) / 60);
        const maxDelay = Math.round((stats.max_delay_seconds || 0) / 60);
        const onTimeRate = evalReport.on_time_rate ? (evalReport.on_time_rate * 100).toFixed(1) + '%' : '-';
        const grade = evalReport.evaluation_grade || '-';

        document.getElementById('metricTotalDelay').textContent = totalDelay;
        document.getElementById('metricMaxDelay').textContent = maxDelay;
        document.getElementById('metricOnTime').textContent = onTimeRate;
        document.getElementById('metricGrade').textContent = grade;

        // 显示风险提示
        const risks = evalReport.risk_warnings || [];
        const riskContainer = document.getElementById('riskWarnings');

        if (risks.length > 0) {
            riskContainer.innerHTML = risks.map(risk => `<div class="risk-item">${risk}</div>`).join('');
        } else {
            riskContainer.innerHTML = '<div style="font-size: 13px; color: #43a047; text-align: center; padding: 25px;">✅ 未发现潜在风险</div>';
        }

        // 显示详细方案区域
        document.getElementById('detailSection').style.display = 'block';

        // 自然语言方案
        if (data.natural_language_plan) {
            document.getElementById('naturalPlanSection').style.display = 'block';
            document.getElementById('naturalPlanContent').innerHTML = data.natural_language_plan.replace(/\n/g, '<br>');
        }

        // 调度员操作指南
        if (data.operations_guide && data.operations_guide.operations) {
            document.getElementById('opsGuideSection').style.display = 'block';
            const guide = data.operations_guide;
            let html = `<strong>场景类型：${guide.scene_name || '调度操作指南'}</strong><br><br>`;
            html += guide.operations.map((op, i) => `${i + 1}. ${op}`).join('<br>');
            html += `<br><small style="color: #666;">来源：${guide.source || '系统生成'} | 匹配度：${guide.match_score || 0}</small>`;
            document.getElementById('opsGuideContent').innerHTML = html;
        }

        // 时刻表
        if (data.optimized_schedule) {
            displayScheduleTable(data.optimized_schedule);
        }

        // 生成运行图
        if (data.optimized_schedule && data.original_schedule) {
            generateDiagram(data.original_schedule, data.optimized_schedule);
        }

    } catch (error) {
        console.error('显示结果失败:', error);
        appendMessage('error', '结果数据格式错误，请稍后重试');
    }
}

/**
 * 显示时刻表
 * @param {Object} schedule - 时刻表数据
 */
function displayScheduleTable(schedule) {
    let tableHtml = '<table class="schedule-table" style="width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px;"><thead><tr style="background: #f5f5f5; font-weight: 600;"><th style="padding: 10px; border: 1px solid #ddd; text-align: left;">车次</th><th style="padding: 10px; border: 1px solid #ddd; text-align: left;">车站</th><th style="padding: 10px; border: 1px solid #ddd; text-align: center;">到达</th><th style="padding: 10px; border: 1px solid #ddd; text-align: center;">发车</th><th style="padding: 10px; border: 1px solid #ddd; text-align: center;">延误</th></tr></thead><tbody>';

    for (const [trainId, stops] of Object.entries(schedule)) {
        if (Array.isArray(stops)) {
            for (const stop of stops) {
                const delay = stop.delay_seconds || 0;
                const delayClass = delay > 0 ? 'color: #c62828; font-weight: 600;' : 'color: #43a047; font-weight: 500;';
                const delayText = delay > 0 ? `+${Math.round(delay/60)}分` : '准点';

                tableHtml += `<tr style="background: ${delay > 0 ? '#fff3e0' : 'white'};">
                    <td style="padding: 8px; border: 1px solid #ddd;">${trainId}</td>
                    <td style="padding: 8px; border: 1px solid #ddd;">${stop.station_name || stop.station_code}</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">${stop.arrival_time || '-'}</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">${stop.departure_time || '-'}</td>
                    <td style="padding: 8px; border: 1px solid #ddd; text-align: center; ${delayClass}">${delayText}</td>
                </tr>`;
            }
        }
    }

    tableHtml += '</tbody></table>';
    document.getElementById('scheduleTable').innerHTML = tableHtml;
}

/**
 * 生成运行图
 * @param {Object} originalSchedule - 原始时刻表
 * @param {Object} optimizedSchedule - 优化后时刻表
 */
async function generateDiagram(originalSchedule, optimizedSchedule) {
    const diagramContainer = document.getElementById('diagramContainer');
    diagramContainer.innerHTML = '<div style="color: #666; font-size: 14px;">📊 正在生成运行图对比，请稍候...</div>';

    try {
        const response = await fetch('/api/diagram', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                original_schedule: originalSchedule,
                optimized_schedule: optimizedSchedule
            })
        });

        const data = await response.json();
        if (data.success) {
            diagramContainer.innerHTML = `<img src="data:image/png;base64,${data.diagram_image}" style="max-width: 100%; height: auto; border-radius: 4px; box-shadow: 0 2px 6px rgba(0,0,0,0.1);">`;
        } else {
            diagramContainer.innerHTML = '<div style="color: #c62828; font-size: 14px;">❌ 运行图生成失败</div>';
        }
    } catch (error) {
        console.error('运行图生成失败:', error);
        diagramContainer.innerHTML = '<div style="color: #c62828; font-size: 14px;">❌ 运行图生成失败，请稍后重试</div>';
    }
}

/**
 * 切换详细方案显示
 */
function toggleDetail() {
    const content = document.getElementById('detailContent');
    const toggle = document.getElementById('detailToggle');

    if (content.style.display === 'none') {
        content.style.display = 'block';
        toggle.textContent = '▲ 点击收起';
    } else {
        content.style.display = 'none';
        toggle.textContent = '▼ 点击展开';
    }
}

/**
 * 页面加载完成后的初始化
 */
document.addEventListener('DOMContentLoaded', function() {
    const input = document.getElementById('chatInput');

    // 自动调整文本框高度
    input.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });

    // 回车发送，Shift+Enter 换行
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendChat();
        }
    });

    console.log('京广高铁调度辅助系统 V2.0 已加载');
});
