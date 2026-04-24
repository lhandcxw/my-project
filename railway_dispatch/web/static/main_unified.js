// 京广高铁调度辅助系统 - 统一前端脚本
// 合并智能调度、调度器对比、LLM工作流功能

// ==================== 全局状态 ====================
let isProcessing = false;
let currentResult = null;
let thinkingBuffer = [];
let thinkingTimer = null;
let workflowSessionId = null;

const quickPrompts = {
    'limit_speed': 'G1563在石家庄站因大风临时限速，预计延误15分钟',
    'failure': 'G1565在保定东站发生设备故障，预计延误30分钟',
    'block': '涿州东到高碑店东区间因施工封锁，预计影响多列列车',
    'delay': 'G1567在北京西站发车延误5分钟，需要调整后续时刻'
};

// ==================== 标签页切换 ====================
function showTab(tabId, event) {
    if (event) event.preventDefault();
    document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(c => c.classList.remove('active'));
    if (event && event.target) {
        event.target.classList.add('active');
    } else {
        const tab = document.querySelector(`.nav-tab[onclick*="'${tabId}'"]`);
        if (tab) tab.classList.add('active');
    }
    const panel = document.getElementById(tabId);
    if (panel) panel.classList.add('active');
}

// ==================== 智能调度（流式Chat）====================
function fillQuickPrompt(type) {
    const prompt = quickPrompts[type];
    if (prompt) {
        document.getElementById('chatInput').value = prompt;
        document.getElementById('chatInput').focus();
    }
}

async function sendChat() {
    if (isProcessing) {
        appendChatMessage('system', '当前有任务正在处理中，请稍候...');
        return;
    }
    const input = document.getElementById('chatInput');
    const prompt = input.value.trim();
    if (!prompt) {
        showToast('请输入调度需求信息（建议包含：车次号、车站或区间名称、故障类型、预计延误时间）', 'warning');
        return;
    }
    appendChatMessage('user', prompt);
    input.value = '';
    input.style.height = '44px';
    isProcessing = true;
    updateSendButton(true);
    resetProgress();

    try {
        const response = await fetch('/api/agent_chat_stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: prompt })
        });
        if (!response.ok) throw new Error(`服务器响应异常 (HTTP ${response.status})`);

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
                        try { handleStreamEvent(JSON.parse(data)); }
                        catch (e) { console.warn('流式数据解析失败:', e, data); }
                    }
                }
            }
        }
    } catch (error) {
        console.error('请求失败:', error);
        appendChatMessage('error', '处理失败：' + error.message);
    } finally {
        isProcessing = false;
        updateSendButton(false);
    }
}

function handleStreamEvent(event) {
    switch (event.type) {
        case 'start':
            flushThinkingMessages();
            appendChatMessage('thinking', '开始处理调度需求...');
            break;
        case 'thinking':
            thinkingBuffer.push(event.content);
            if (thinkingTimer) clearTimeout(thinkingTimer);
            thinkingTimer = setTimeout(() => flushThinkingMessages(), 500);
            break;
        case 'progress':
            flushThinkingMessages();
            updateProgressStep(event.layer, event.message);
            break;
        case 'result':
            flushThinkingMessages();
            currentResult = event.data;
            displayResult(event.data);
            appendChatMessage('agent', '调度方案已生成，请查看右侧信息面板和详细方案。');
            break;
        case 'error':
            flushThinkingMessages();
            appendChatMessage('error', '处理失败：' + event.message);
            break;
    }
}

function flushThinkingMessages() {
    if (thinkingBuffer.length === 0) return;
    appendChatMessage('thinking', thinkingBuffer.join('<br>'));
    thinkingBuffer = [];
    scrollChatToBottom();
}

function appendChatMessage(type, content) {
    const chatMessages = document.getElementById('chatMessages');
    const msgDiv = document.createElement('div');
    msgDiv.className = `msg msg-${type}`;
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    bubble.innerHTML = content;
    msgDiv.appendChild(bubble);
    // 添加时间戳
    const timeDiv = document.createElement('div');
    timeDiv.className = 'msg-time';
    const now = new Date();
    timeDiv.textContent = `${now.getHours().toString().padStart(2,'0')}:${now.getMinutes().toString().padStart(2,'0')}`;
    msgDiv.appendChild(timeDiv);
    chatMessages.appendChild(msgDiv);
    scrollChatToBottom();
}

function scrollChatToBottom() {
    const chatMessages = document.getElementById('chatMessages');
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function updateSendButton(processing) {
    const sendBtn = document.getElementById('sendBtn');
    if (processing) {
        sendBtn.disabled = true;
        sendBtn.innerHTML = '<span class="loading-dots"></span>处理中';
        sendBtn.style.opacity = '0.7';
    } else {
        sendBtn.disabled = false;
        sendBtn.innerHTML = '提交需求';
        sendBtn.style.opacity = '1';
    }
}

function resetProgress() {
    for (let i = 1; i <= 4; i++) {
        const step = document.getElementById(`step${i}`);
        if (step) { step.className = 'progress-step'; }
    }
    document.getElementById('progressText').textContent = '等待输入调度需求...';
    document.getElementById('metricTotalDelay').textContent = '-';
    document.getElementById('metricMaxDelay').textContent = '-';
    document.getElementById('metricAvgDelay').textContent = '-';
    document.getElementById('metricOnTime').textContent = '-';
    document.getElementById('metricGrade').textContent = '-';
    document.getElementById('metricAffected').textContent = '-';
    document.getElementById('metricTerminalOnTime').textContent = '-';
    document.getElementById('metricRecoveryRate').textContent = '-';
    document.getElementById('metricStationPressure').textContent = '-';
    // 重置指标颜色
    ['mb-total','mb-max','mb-avg'].forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.className = 'metric-box'; }
    });
    document.getElementById('riskWarnings').innerHTML = '<div class="risk-empty">暂无风险提示</div>';
    const detailSection = document.getElementById('detailSection');
    if (detailSection) detailSection.classList.remove('visible');
    const detailContent = document.getElementById('detailContent');
    if (detailContent) detailContent.classList.remove('open');
    const detailToggle = document.getElementById('detailToggle');
    if (detailToggle) detailToggle.classList.remove('open');
    const arrow = document.getElementById('detailArrow');
    if (arrow) arrow.innerHTML = '&#9660;';
    thinkingBuffer = [];
    if (thinkingTimer) { clearTimeout(thinkingTimer); thinkingTimer = null; }
}

function updateProgressStep(layer, message) {
    for (let i = 1; i < layer; i++) {
        const step = document.getElementById(`step${i}`);
        if (step) step.classList.add('done');
    }
    const currentStep = document.getElementById(`step${layer}`);
    if (currentStep) currentStep.classList.add('active');
    document.getElementById('progressText').textContent = message;
}

function displayResult(data) {
    if (!data) return;
    try {
        const stats = data.delay_statistics || {};
        const evalReport = data.evaluation_report || {};
        const dm = data.dispatcher_metrics || {};

        // 基础指标
        const totalDelay = Math.round(stats.total_delay_minutes || 0);
        const maxDelay = Math.round(stats.max_delay_minutes || 0);
        const avgDelay = (stats.avg_delay_minutes || 0).toFixed(1);

        document.getElementById('metricTotalDelay').textContent = totalDelay;
        document.getElementById('metricMaxDelay').textContent = maxDelay;
        document.getElementById('metricAvgDelay').textContent = avgDelay;
        document.getElementById('metricOnTime').textContent = evalReport.on_time_rate ? (evalReport.on_time_rate * 100).toFixed(1) + '%' : '-';
        document.getElementById('metricGrade').textContent = evalReport.evaluation_grade || '-';
        document.getElementById('metricAffected').textContent = stats.affected_trains_count || 0;

        // 指标颜色编码
        const mbTotal = document.getElementById('mb-total');
        const mbMax = document.getElementById('mb-max');
        const mbAvg = document.getElementById('mb-avg');
        if (mbTotal) mbTotal.className = 'metric-box ' + (totalDelay > 60 ? 'danger' : totalDelay > 20 ? 'warning' : 'success');
        if (mbMax) mbMax.className = 'metric-box ' + (maxDelay > 30 ? 'danger' : maxDelay > 10 ? 'warning' : 'success');
        if (mbAvg) mbAvg.className = 'metric-box ' + (avgDelay > 15 ? 'danger' : avgDelay > 5 ? 'warning' : 'success');

        // 调度员关心的现实场景指标
        const termOnTime = dm.terminal_on_time_rate !== undefined ? (dm.terminal_on_time_rate * 100).toFixed(1) + '%' : '-';
        const recoveryRate = dm.delay_recovery_rate !== undefined ? (dm.delay_recovery_rate * 100).toFixed(1) + '%' : '-';
        const stationPressure = dm.station_pressure_max || 0;
        const stationPressureName = dm.station_pressure_max_name || '-';

        document.getElementById('metricTerminalOnTime').textContent = termOnTime;
        document.getElementById('metricRecoveryRate').textContent = recoveryRate;
        document.getElementById('metricStationPressure').textContent = stationPressure + (stationPressureName !== '-' ? `(${stationPressureName})` : '');

        // 风险提示
        const risks = evalReport.risk_warnings || [];
        const riskContainer = document.getElementById('riskWarnings');
        if (risks.length > 0) {
            riskContainer.innerHTML = risks.map(risk =>
                `<div class="risk-tag"><span class="risk-icon">&#9888;</span><span>${risk}</span></div>`
            ).join('');
        } else {
            riskContainer.innerHTML = '<div class="risk-empty safe"><span style="font-size:18px;">&#9989;</span> 未发现潜在风险</div>';
        }

        // 详细方案
        const detailSection = document.getElementById('detailSection');
        if (detailSection) detailSection.classList.add('visible');

        const naturalPlanSection = document.getElementById('naturalPlanSection');
        const naturalPlanContent = document.getElementById('naturalPlanContent');
        if (data.natural_language_plan && naturalPlanSection && naturalPlanContent) {
            naturalPlanSection.style.display = 'block';
            naturalPlanContent.innerHTML = data.natural_language_plan.replace(/\n/g, '<br>');
        }
        const opsGuideSection = document.getElementById('opsGuideSection');
        const opsGuideContent = document.getElementById('opsGuideContent');
        const opsGuide = data.operations_guide;
        if (opsGuide && (opsGuide.steps || opsGuide.operations) && opsGuideSection && opsGuideContent) {
            opsGuideSection.style.display = 'block';
            const guide = opsGuide;
            let html = `<div style="margin-bottom:12px;font-size:15px;font-weight:600;color:#e65100;">场景：${escapeHtml(guide.scene_name || '调度操作指南')}</div>`;

            // 优先使用新结构 steps
            if (guide.steps && guide.steps.length > 0) {
                guide.steps.forEach((step, idx) => {
                    const phase = step.phase || `步骤 ${idx + 1}`;
                    const priority = step.priority || 'medium';
                    const timeLimit = step.time_limit || '';
                    const actions = step.actions || [];

                    let badgeClass = 'ops-badge-medium';
                    let badgeText = '一般';
                    if (priority === 'critical') { badgeClass = 'ops-badge-critical'; badgeText = '关键'; }
                    else if (priority === 'high') { badgeClass = 'ops-badge-high'; badgeText = '高优'; }

                    html += `<div class="ops-step-card">`;
                    html += `<div class="ops-step-header">`;
                    html += `<span class="ops-step-phase">${escapeHtml(phase)}</span>`;
                    html += `<span class="ops-badge ${badgeClass}">${badgeText}</span>`;
                    if (timeLimit) {
                        html += `<span class="ops-time-limit">时限：${escapeHtml(timeLimit)}</span>`;
                    }
                    html += `</div>`;
                    html += `<ul class="ops-action-list">`;
                    actions.forEach(action => {
                        html += `<li>${escapeHtml(action)}</li>`;
                    });
                    html += `</ul></div>`;
                });
            } else if (guide.operations && guide.operations.length > 0) {
                // 兼容旧结构：扁平 operations 列表
                html += guide.operations.map((op) => {
                    const trimmed = op.trim();
                    if (/^\d+[\.、\s]/.test(trimmed)) { return `<div style="margin:4px 0;">${escapeHtml(trimmed)}</div>`; }
                    return `<div style="margin:4px 0;">${escapeHtml(trimmed)}</div>`;
                }).join('');
            }

            html += `<div style="margin-top:12px;font-size:12px;color:#9e9e9e;border-top:1px dashed #e0e0e0;padding-top:8px;">`;
            html += `来源：${escapeHtml(guide.source || '系统生成')} | 匹配度：${guide.match_score || 0}`;
            html += `</div>`;
            opsGuideContent.innerHTML = html;
        }

        // 时刻表对比
        const hasOptSchedule = data.optimized_schedule && Object.keys(data.optimized_schedule).length > 0;
        const hasOrigSchedule = data.original_schedule && Object.keys(data.original_schedule).length > 0;
        if (hasOptSchedule && hasOrigSchedule) {
            displayScheduleComparison(data.original_schedule, data.optimized_schedule);
            generateDiagram(data.original_schedule, data.optimized_schedule);
        } else if (hasOptSchedule) {
            displayScheduleTable(data.optimized_schedule);
        }
    } catch (error) {
        console.error('显示结果失败:', error);
    }
}

function displayScheduleTable(schedule) {
    let tableHtml = '<table class="data-table"><thead><tr><th>车次</th><th>车站</th><th>到达</th><th>发车</th><th>延误</th></tr></thead><tbody>';
    for (const [trainId, stops] of Object.entries(schedule)) {
        if (Array.isArray(stops)) {
            for (const stop of stops) {
                const delay = stop.delay_seconds || 0;
                const delayClass = delay > 0 ? 'delay-red' : 'delay-green';
                const delayText = delay > 0 ? `+${Math.round(delay/60)}分` : '准点';
                tableHtml += `<tr class="${delay > 0 ? 'highlight-row' : ''}"><td>${trainId}</td><td>${stop.station_name || stop.station_code}</td><td style="text-align:center;">${stop.arrival_time || '-'}</td><td style="text-align:center;">${stop.departure_time || '-'}</td><td style="text-align:center;" class="${delayClass}">${delayText}</td></tr>`;
            }
        }
    }
    tableHtml += '</tbody></table>';
    document.getElementById('scheduleTable').innerHTML = tableHtml;
}

function displayScheduleComparison(originalSchedule, optimizedSchedule) {
    let tableHtml = '<table class="data-table"><thead><tr><th>车次</th><th>车站</th><th>原计划到达</th><th>优化后到达</th><th>原计划发车</th><th>优化后发车</th><th>变化</th></tr></thead><tbody>';
    let shownTrains = 0;
    const maxTrains = 10;

    for (const [trainId, optStops] of Object.entries(optimizedSchedule)) {
        if (shownTrains >= maxTrains) break;
        const origStops = originalSchedule[trainId] || [];
        let hasChange = false;
        let rows = '';

        for (let i = 0; i < optStops.length; i++) {
            const opt = optStops[i];
            const orig = origStops[i] || {};
            const optDelay = opt.delay_seconds || 0;
            const origArr = orig.arrival_time || '-';
            const origDep = orig.departure_time || '-';
            const optArr = opt.arrival_time || '-';
            const optDep = opt.departure_time || '-';

            if (optDelay > 0) {
                hasChange = true;
                const changeText = `延误+${Math.round(optDelay/60)}分`;
                rows += `<tr class="highlight-row"><td>${trainId}</td><td>${opt.station_name || opt.station_code}</td><td style="text-align:center;">${origArr}</td><td style="text-align:center;" class="delay-red">${optArr}</td><td style="text-align:center;">${origDep}</td><td style="text-align:center;" class="delay-red">${optDep}</td><td style="text-align:center;" class="delay-red">${changeText}</td></tr>`;
            }
        }

        if (hasChange) {
            tableHtml += rows;
            shownTrains++;
        }
    }

    if (shownTrains === 0) {
        tableHtml += '<tr><td colspan="7" style="text-align:center;color:#9e9e9e;padding:24px;">所有列车均准点运行，无调整</td></tr>';
    }
    tableHtml += '</tbody></table>';
    if (shownTrains >= maxTrains) {
        tableHtml += '<p style="color:#9e9e9e;font-size:12px;text-align:center;margin-top:10px;">仅展示前10列有变化的列车</p>';
    }
    document.getElementById('scheduleTable').innerHTML = tableHtml;
}

async function generateDiagram(originalSchedule, optimizedSchedule) {
    const diagramContainer = document.getElementById('diagramContainer');
    diagramContainer.innerHTML = '<div style="color:#616161;font-size:14px;">正在生成运行图对比，请稍候...</div>';
    try {
        const highlightTrainIds = [];
        for (const [trainId, optStops] of Object.entries(optimizedSchedule)) {
            const origStops = originalSchedule[trainId] || [];
            for (let i = 0; i < optStops.length; i++) {
                const opt = optStops[i];
                const orig = origStops[i] || {};
                if (opt.arrival_time !== orig.arrival_time || opt.departure_time !== orig.departure_time || (opt.delay_seconds || 0) > 0) {
                    highlightTrainIds.push(trainId);
                    break;
                }
            }
        }

        const response = await fetch('/api/diagram', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                original_schedule: originalSchedule,
                optimized_schedule: optimizedSchedule,
                highlight_train_ids: highlightTrainIds
            })
        });
        const data = await response.json();
        if (data.success) {
            const infoText = data.trains_drawn ? `<div style="color:#9e9e9e;font-size:12px;margin-bottom:8px;">运行图绘制 ${data.trains_drawn} 列变化列车，渲染耗时 ${data.render_time || '-'} 秒</div>` : '';
            diagramContainer.innerHTML = infoText + `<img src="data:image/png;base64,${data.diagram_image}" style="max-width:100%;height:auto;border-radius:8px;box-shadow:var(--shadow-md);">`;
        } else {
            diagramContainer.innerHTML = '<div style="color:#c62828;font-size:14px;">运行图生成失败：' + (data.message || '未知错误') + '</div>';
        }
    } catch (error) {
        diagramContainer.innerHTML = '<div style="color:#c62828;font-size:14px;">运行图生成失败：' + error.message + '</div>';
    }
}

function toggleDetail() {
    const content = document.getElementById('detailContent');
    const toggle = document.getElementById('detailToggle');
    const arrow = document.getElementById('detailArrow');
    if (!content.classList.contains('open')) {
        content.classList.add('open');
        toggle.classList.add('open');
        arrow.innerHTML = '&#9650;';
    } else {
        content.classList.remove('open');
        toggle.classList.remove('open');
        arrow.innerHTML = '&#9660;';
    }
}

// ==================== 调度器对比 ====================
async function runComparison() {
    const trainId = document.getElementById('compTrainId').value;
    const station = document.getElementById('compStation').value;
    const delayMinutes = parseInt(document.getElementById('compDelayMinutes').value);
    const criteria = document.getElementById('compCriteria').value;

    const loading = document.getElementById('comparisonLoading');
    loading.classList.add('visible');
    document.getElementById('comparisonResultDisplay').style.display = 'none';

    try {
        const response = await fetch('/api/scheduler_comparison', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                train_id: trainId,
                station: station,
                delay_minutes: delayMinutes,
                criteria: criteria
            })
        });
        const data = await response.json();
        loading.classList.remove('visible');
        if (data.success) {
            displayComparisonResult(data);
            document.getElementById('comparisonResultDisplay').style.display = 'block';
        } else {
            showToast('对比失败: ' + (data.message || '未知错误'), 'error');
        }
    } catch (error) {
        loading.classList.remove('visible');
        showToast('请求失败: ' + error.message, 'error');
    }
}

function displayComparisonResult(data) {
    const result = data.comparison_result || data;
    let html = '';
    if (result.all_results && result.all_results.length > 0) {
        html += '<table class="report-table"><thead><tr><th>排名</th><th>调度器</th><th>最大延误</th><th>平均延误</th><th>总延误</th><th>受影响列车</th><th>准点率</th><th>计算时间</th></tr></thead><tbody>';
        result.all_results.sort((a, b) => a.rank - b.rank);
        for (const r of result.all_results) {
            const m = r.metrics;
            const isWinner = r.is_winner;
            const badgeClass = getSchedulerBadgeClass(r.scheduler_name);
            html += `<tr class="${isWinner ? 'winner' : ''}"><td>${r.rank}</td><td><span class="scheduler-badge ${badgeClass}">${r.scheduler_name}</span>${isWinner ? ' &#11088;' : ''}</td><td>${Math.round(m.max_delay_minutes || 0)}分</td><td>${(m.avg_delay_minutes || 0).toFixed(1)}分</td><td>${Math.round(m.total_delay_minutes || 0)}分</td><td>${m.affected_trains_count || 0}列</td><td>${(m.on_time_rate || 0).toFixed(1)}%</td><td>${(m.computation_time || 0).toFixed(2)}秒</td></tr>`;
        }
        html += '</tbody></table>';
        if (result.recommendations && result.recommendations.length > 0) {
            html += '<div class="recommend-box"><strong>推荐结论</strong><ul>';
            for (const rec of result.recommendations) {
                html += `<li>${rec}</li>`;
            }
            html += '</ul></div>';
        }
    } else {
        html = '<p style="color:#9e9e9e;text-align:center;padding:20px;">暂无对比结果</p>';
    }
    document.getElementById('comparisonReport').innerHTML = html;
    drawComparisonChart(result.all_results || []);
}

function getSchedulerBadgeClass(name) {
    if (name.includes('MIP')) return 'badge-mip';
    if (name.includes('FCFS')) return 'badge-fcfs';
    if (name.includes('最大延误') || name.includes('MaxDelay')) return 'badge-maxdelay';
    if (name.includes('分层') || name.includes('Hierarchical')) return 'badge-hierarchical';
    if (name.includes('基线') || name.includes('NoOp')) return 'badge-noop';
    return 'badge-fcfs';
}

// ==================== LLM工作流 ====================
async function startWorkflow() {
    const input = document.getElementById('workflowInput');
    const prompt = input.value.trim();
    if (!prompt) { showToast('请输入调度需求', 'warning'); return; }

    const chatHistory = document.getElementById('workflowChatHistory');
    // 移除空状态提示
    if (chatHistory.querySelector('p[style*="text-align:center"]')) {
        chatHistory.innerHTML = '';
    }
    chatHistory.innerHTML += `<div class="workflow-msg user"><strong>用户：</strong>${escapeHtml(prompt)}</div>`;
    input.value = '';

    try {
        const response = await fetch('/api/workflow/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_input: prompt })
        });
        const data = await response.json();
        if (data.success) {
            workflowSessionId = data.session_id;
            document.getElementById('workflowResult').style.display = 'block';
            document.getElementById('workflowResultContent').textContent = JSON.stringify(data, null, 2);
            document.getElementById('continueWorkflowBtn').disabled = false;
            document.getElementById('resetWorkflowBtn').disabled = false;
            chatHistory.innerHTML += `<div class="workflow-msg system"><strong>系统：</strong>工作流已启动，Session ID: ${workflowSessionId}</div>`;
        } else {
            chatHistory.innerHTML += `<div class="workflow-msg error"><strong>错误：</strong>${escapeHtml(data.message || '启动失败')}</div>`;
        }
    } catch (error) {
        chatHistory.innerHTML += `<div class="workflow-msg error"><strong>错误：</strong>${escapeHtml(error.message)}</div>`;
    }
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function continueWorkflow() {
    if (!workflowSessionId) { showToast('请先启动工作流', 'warning'); return; }
    try {
        const response = await fetch('/api/workflow/next', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: workflowSessionId })
        });
        const data = await response.json();
        document.getElementById('workflowResultContent').textContent = JSON.stringify(data, null, 2);
        const chatHistory = document.getElementById('workflowChatHistory');
        chatHistory.innerHTML += `<div class="workflow-msg system"><strong>系统：</strong>工作流推进完成</div>`;
        chatHistory.scrollTop = chatHistory.scrollHeight;
    } catch (error) {
        showToast('工作流推进失败: ' + error.message, 'error');
    }
}

async function resetWorkflow() {
    if (!workflowSessionId) return;
    try {
        await fetch('/api/workflow/reset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: workflowSessionId })
        });
        workflowSessionId = null;
        document.getElementById('workflowResult').style.display = 'none';
        document.getElementById('continueWorkflowBtn').disabled = true;
        document.getElementById('resetWorkflowBtn').disabled = true;
        document.getElementById('workflowChatHistory').innerHTML = '<p style="color:#9e9e9e;text-align:center;padding:20px;">会话已重置</p>';
    } catch (error) {
        console.error('重置失败:', error);
    }
}

// ==================== Toast 提示 ====================
function showToast(message, type = 'info', duration = 3000) {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    const iconMap = { info: '&#8505;', success: '&#9989;', warning: '&#9888;', error: '&#10060;' };
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span>${iconMap[type] || ''}</span><span style="flex:1;">${message}</span><span class="toast-close" onclick="this.parentElement.remove()">&#10005;</span>`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(30px)';
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

// ==================== 实时时钟 ====================
function updateClock() {
    const now = new Date();
    const timeStr = now.toLocaleTimeString('zh-CN', { hour12: false });
    const clock = document.getElementById('liveClock');
    if (clock) clock.textContent = timeStr;
}
setInterval(updateClock, 1000);
updateClock();

// ==================== 后端状态检测 ====================
async function checkServerStatus() {
    const dot = document.getElementById('statusDot');
    const text = document.getElementById('statusText');
    try {
        const resp = await fetch('/api/health', { method: 'GET', signal: AbortSignal.timeout(3000) });
        if (resp.ok) {
            dot.className = 'dot dot-online';
            text.textContent = '服务正常';
        } else {
            dot.className = 'dot dot-offline';
            text.textContent = '服务异常';
        }
    } catch (e) {
        dot.className = 'dot dot-warn';
        text.textContent = '连接中...';
    }
}
setInterval(checkServerStatus, 15000);
checkServerStatus();

// ==================== 对比结果柱状图 ====================
function drawComparisonChart(results) {
    const container = document.getElementById('comparisonChart');
    if (!container || !results || results.length === 0) {
        if (container) container.innerHTML = '<p style="color:#9e9e9e;text-align:center;padding:20px;">无数据</p>';
        return;
    }
    const canvas = document.createElement('canvas');
    const dpr = window.devicePixelRatio || 1;
    const cssW = container.clientWidth || 800;
    const cssH = 320;
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
    canvas.style.width = '100%';
    canvas.style.height = cssH + 'px';
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);

    const margin = { top: 36, right: 20, bottom: 56, left: 60 };
    const w = cssW - margin.left - margin.right;
    const h = cssH - margin.top - margin.bottom;

    const names = results.map(r => r.scheduler_name);
    const maxDelays = results.map(r => r.metrics?.max_delay_minutes || 0);
    const avgDelays = results.map(r => r.metrics?.avg_delay_minutes || 0);
    const maxVal = Math.max(...maxDelays, ...avgDelays, 1);

    // 背景
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, cssW, cssH);

    // 标题
    ctx.fillStyle = '#1565c0';
    ctx.font = 'bold 14px sans-serif';
    ctx.fillText('各调度器延误对比（分钟）', margin.left, 22);

    // 网格线
    ctx.strokeStyle = '#f0f0f0';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 5; i++) {
        const y = margin.top + h - (i / 5) * h;
        ctx.beginPath(); ctx.moveTo(margin.left, y); ctx.lineTo(margin.left + w, y); ctx.stroke();
        ctx.fillStyle = '#9e9e9e'; ctx.font = '11px sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText((maxVal * i / 5).toFixed(0), margin.left - 10, y + 4);
    }

    const barW = Math.min(28, w / names.length / 3);
    const groupW = w / names.length;

    // 圆角矩形辅助函数（提升到 forEach 外部，供图例也使用）
    function roundRect(x, y, w, h, r) {
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.lineTo(x + w - r, y);
        ctx.quadraticCurveTo(x + w, y, x + w, y + r);
        ctx.lineTo(x + w, y + h);
        ctx.lineTo(x, y + h);
        ctx.lineTo(x, y + r);
        ctx.quadraticCurveTo(x, y, x + r, y);
        ctx.closePath();
    }

    results.forEach((r, i) => {
        const x = margin.left + i * groupW + groupW / 2;
        const maxH = (r.metrics?.max_delay_minutes || 0) / maxVal * h;
        const avgH = (r.metrics?.avg_delay_minutes || 0) / maxVal * h;

        // 最大延误柱
        ctx.fillStyle = r.is_winner ? '#c62828' : '#1565c0';
        roundRect(x - barW - 3, margin.top + h - maxH, barW, maxH, 3);
        ctx.fill();

        // 平均延误柱
        ctx.fillStyle = r.is_winner ? '#ef5350' : '#42a5f5';
        roundRect(x + 3, margin.top + h - avgH, barW, avgH, 3);
        ctx.fill();

        // 标签
        ctx.fillStyle = '#424242';
        ctx.font = '11px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(r.scheduler_name, x, margin.top + h + 18);
        ctx.fillStyle = '#9e9e9e';
        ctx.font = '10px sans-serif';
        ctx.fillText(`最大:${(r.metrics?.max_delay_minutes || 0).toFixed(0)}`, x - barW/2 - 1, margin.top + h + 34);
        ctx.fillText(`平均:${(r.metrics?.avg_delay_minutes || 0).toFixed(1)}`, x + barW/2 + 1, margin.top + h + 34);
    });

    // 图例
    const legendX = margin.left + w - 160;
    ctx.fillStyle = '#1565c0'; roundRect(legendX, 10, 12, 12, 2); ctx.fill();
    ctx.fillStyle = '#424242'; ctx.font = '12px sans-serif'; ctx.textAlign = 'left';
    ctx.fillText('最大延误', legendX + 18, 20);
    ctx.fillStyle = '#42a5f5'; roundRect(legendX + 80, 10, 12, 12, 2); ctx.fill();
    ctx.fillText('平均延误', legendX + 98, 20);

    container.innerHTML = '';
    container.appendChild(canvas);
}

// ==================== 页面初始化 ====================
document.addEventListener('DOMContentLoaded', function() {
    const input = document.getElementById('chatInput');
    if (input) {
        input.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 120) + 'px';
        });
        input.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendChat();
            }
        });
    }
    console.log('京广高铁调度辅助系统 v2.0 已加载');
});
