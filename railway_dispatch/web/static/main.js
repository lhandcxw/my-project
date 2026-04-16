
        // 切换表单输入显示
        function toggleFormInput() {
            const section = document.getElementById('formInputSection');
            const icon = document.getElementById('formToggleIcon');
            if (section.style.display === 'none') {
                section.style.display = 'block';
                icon.textContent = '▲ 点击收起';
            } else {
                section.style.display = 'none';
                icon.textContent = '▼ 点击展开';
            }
        }

        // 标签页切换
        function showTab(tabId, event) {
            console.log('Switching to tab:', tabId);
            console.log('Event:', event);
            console.log('Event target:', event ? event.target : 'none');
            if (event) {
                event.preventDefault();
            }
            // 移除所有tab的active状态
            console.log('Removing active from all tabs...');
            document.querySelectorAll('.tab').forEach(t => {
                console.log('Removing from:', t.textContent, t.classList.contains('active'));
                t.classList.remove('active');
            });
            // 移除所有tab-content的active状态
            console.log('Removing active from all tab-contents...');
            document.querySelectorAll('.tab-content').forEach(c => {
                console.log('Removing from:', c.id, c.classList.contains('active'));
                c.classList.remove('active');
            });

            // 为当前点击的tab添加active状态
            if (event && event.target) {
                console.log('Adding active to clicked tab:', event.target.textContent);
                event.target.classList.add('active');
            } else {
                // 如果没有event，尝试通过索引查找对应的tab按钮
                // 注意：现在只有3个tab，所以索引是 0, 1, 2
                var tabMap = {'dispatch': 0, 'llm_workflow': 1, 'comparison': 2};
                var idx = tabMap[tabId];
                console.log('No event, using index:', idx);
                if (idx !== undefined) {
                    var tabs = document.querySelectorAll('.tab');
                    if (tabs[idx]) {
                        console.log('Adding active to tab index:', idx, tabs[idx].textContent);
                        tabs[idx].classList.add('active');
                    }
                }
            }

            // 显示对应的tab内容
            var tabContent = document.getElementById(tabId);
            console.log('Looking for tab content:', tabId, 'Found:', !!tabContent);
            if (tabContent) {
                console.log('Adding active to tab content:', tabId);
                tabContent.classList.add('active');
                console.log('Tab content now has classes:', tabContent.className);
                console.log('Tab', tabId, 'activated successfully');
            } else {
                console.error('Tab content not found:', tabId);
            }
        }

        // 页面加载后确保默认tab正确显示
        window.onload = function() {
            // 确保默认tab可见
            document.getElementById('dispatch').classList.add('active');
            console.log('Page loaded, dispatch tab should be visible');
        };

        // 填充快速输入
        function fillPrompt(type) {
            const prompts = {
                '限速': 'G1001和G1003列车在天津西站因临时限速延误10分钟和15分钟',
                '故障': 'G1005列车在天津西站发生设备故障，延误40分钟',
                '延误': 'G1001列车在北京西站发车延误5分钟，需要调整'
            };
            document.getElementById('dispatchPrompt').value = prompts[type] || '';
        }

        // 格式化时间
        function formatTime(seconds) {
            if (seconds === undefined || seconds === null) return '-';
            const mins = Math.floor(seconds / 60);
            const secs = Math.round(seconds % 60);
            return mins + '分' + secs + '秒';
        }

        // 发送智能调度（对话模式）
        function sendDispatch() {
            const prompt = document.getElementById('dispatchPrompt').value.trim();
            if (!prompt) {
                alert('请输入调度需求');
                return;
            }

            document.getElementById('dispatchLoading').style.display = 'block';
            document.getElementById('dispatchResult').style.display = 'none';

            fetch('/api/agent_chat', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({prompt: prompt})
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP错误! 状态码: ${response.status}`);
                }
                return response.json();
            })
            .then(result => {
                document.getElementById('dispatchLoading').style.display = 'none';

                if (result.success) {
                    // 智能调度模块返回格式直接使用（使用后端返回的正式字段）
                    const unified = {
                        success: true,
                        recognized_scenario: result.recognized_scenario || '',
                        selected_skill: result.selected_skill || '',  // 使用后端正式字段，不再猜测
                        selected_solver: result.selected_solver || '',
                        reasoning: result.reasoning || '',
                        delay_statistics: result.delay_statistics || {},
                        message: result.message || '',
                        computation_time: result.computation_time || 0,
                        optimized_schedule: result.optimized_schedule || {},
                        original_schedule: result.original_schedule || {},
                        ranking: result.ranking || result.comparison_details || []
                    };
                    showDispatchResult(unified);
                } else {
                    alert('执行失败: ' + result.message);
                }
            })
            .catch(error => {
                document.getElementById('dispatchLoading').style.display = 'none';
                console.error('请求失败:', error);
                alert('请求失败: ' + error.message + '\\n\\n请检查：\\n1. 后端服务是否正常运行\\n2. 浏览器控制台是否有更多错误信息');
            });
        }

        // 发送表单调度
        function runFormDispatch() {
            const selectedTrains = Array.from(document.getElementById('selectedTrains').selectedOptions).map(o => o.value);
            if (selectedTrains.length === 0) {
                alert('请至少选择一列列车');
                return;
            }

            const scenarioType = document.getElementById('scenarioType').value;
            const objective = document.getElementById('objective').value;
            const delayStation = document.getElementById('delayStation').value;
            const delaySeconds = parseInt(document.getElementById('delaySeconds').value);

            const data = {
                scenario_type: scenarioType,
                objective: objective,
                selected_trains: selectedTrains,
                delay_config: [{
                    train_id: selectedTrains[0],
                    delay_seconds: delaySeconds,
                    station_code: delayStation
                }]
            };

            document.getElementById('dispatchLoading').style.display = 'block';
            document.getElementById('dispatchResult').style.display = 'none';

            fetch('/api/dispatch', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP错误! 状态码: ${response.status}`);
                }
                return response.json();
            })
            .then(result => {
                document.getElementById('dispatchLoading').style.display = 'none';

                if (result.success) {
                    // 转换为统一格式，添加空值检查
                    // 优先使用后端返回的正式字段（selected_skill），不再猜测
                    const backend_skill = result.skill_result && result.skill_result.selected_skill
                        ? result.skill_result.selected_skill
                        : (result.planner && result.planner.selected_skill);
                    const recognized_scenario = result.planner ? result.planner.recognized_scenario : '';

                    // 备用映射逻辑（仅当后端没有返回正式字段时使用）
                    let selected_skill = backend_skill;
                    if (!selected_skill) {
                        const skillMessage = result.skill_result && result.skill_result.message ? result.skill_result.message : '';
                        selected_skill = skillMessage.includes('限速') ? 'temporary_speed_limit_skill' : 'sudden_failure_skill';
                    }

                    const unified = {
                        success: true,
                        recognized_scenario: recognized_scenario,
                        selected_skill: selected_skill,  // 使用后端正式字段
                        selected_solver: result.planner ? result.planner.selected_solver : '',
                        reasoning: '基于表单输入执行调度优化',
                        delay_statistics: result.skill_result ? result.skill_result.delay_statistics : {},
                        message: result.skill_result ? result.skill_result.message : '',
                        computation_time: result.skill_result ? result.skill_result.computation_time : 0,
                        optimized_schedule: result.skill_result ? result.skill_result.optimized_schedule : {},
                        original_schedule: result.original_schedule
                    };
                    showDispatchResult(unified);
                } else {
                    alert('执行失败: ' + result.message);
                }
            })
            .catch(error => {
                document.getElementById('dispatchLoading').style.display = 'none';
                console.error('请求失败:', error);
                alert('请求失败: ' + error.message + '\\n\\n请检查：\\n1. 后端服务是否正常运行\\n2. 浏览器控制台是否有更多错误信息');
            });
        }

        // 显示调度结果
        function showDispatchResult(result) {
            document.getElementById('dispatchResult').style.display = 'block';

            // 基本信息
            document.getElementById('resultScenario').textContent = result.recognized_scenario || '-';
            document.getElementById('resultSkill').textContent = result.selected_skill || '-';
            document.getElementById('resultTime').textContent = (result.computation_time || 0).toFixed(2) + 's';

            // 推理过程
            document.getElementById('resultReasoning').textContent = result.reasoning || '-';

            // 延误统计
            const stats = result.delay_statistics || {};
            document.getElementById('resultMaxDelay').textContent = formatTime(stats.max_delay_seconds);
            document.getElementById('resultAvgDelay').textContent = formatTime(stats.avg_delay_seconds);
            document.getElementById('resultTotalDelay').textContent = formatTime(stats.total_delay_seconds);

            // 消息
            document.getElementById('resultMessage').textContent = result.message || '-';

            // LLM评估建议
            const llmEvalDiv = document.getElementById('llmEvaluationSummary');
            const llmSummaryText = document.getElementById('llmSummaryText');
            if (result.llm_summary) {
                llmEvalDiv.style.display = 'block';
                llmSummaryText.textContent = result.llm_summary;
            } else {
                llmEvalDiv.style.display = 'none';
            }

            // 高铁专用评估指标
            const evalReport = result.evaluation_report || {};
            document.getElementById('resultOnTimeRate').textContent = (evalReport.on_time_rate !== undefined ? (evalReport.on_time_rate * 100).toFixed(1) : '-') + '%';
            document.getElementById('resultAffectedTrains').textContent = evalReport.affected_trains_count !== undefined ? evalReport.affected_trains_count : '-';
            document.getElementById('resultPropagationDepth').textContent = evalReport.delay_propagation_depth !== undefined ? evalReport.delay_propagation_depth : '-';
            document.getElementById('resultDelayStdDev').textContent = evalReport.delay_std_dev !== undefined ? (evalReport.delay_std_dev / 60).toFixed(2) : '-';
            document.getElementById('resultPunctualityStrict').textContent = (evalReport.punctuality_strict !== undefined ? (evalReport.punctuality_strict * 100).toFixed(1) : '-') + '%';
            document.getElementById('resultEvaluationGrade').textContent = evalReport.evaluation_grade || '-';

            // 风险提示
            const riskSection = document.getElementById('riskWarningsSection');
            const riskList = document.getElementById('riskWarningsList');
            if (evalReport.risk_warnings && evalReport.risk_warnings.length > 0) {
                riskSection.style.display = 'block';
                riskList.innerHTML = evalReport.risk_warnings.map(r => '<li>' + r + '</li>').join('');
            } else {
                riskSection.style.display = 'none';
            }

            // 自然语言调度方案
            const planSection = document.getElementById('naturalLanguagePlanSection');
            const planText = document.getElementById('naturalLanguagePlanText');
            if (result.natural_language_plan) {
                planSection.style.display = 'block';
                planText.textContent = result.natural_language_plan;
            } else {
                planSection.style.display = 'none';
            }

            // 调度员操作指南
            const opsGuideDiv = document.getElementById('dispatcherOperationsSection');
            const opsGuideScene = document.getElementById('dispatcherOperationsScene');
            const opsGuideList = document.getElementById('dispatcherOperationsList');
            const opsGuideSource = document.getElementById('dispatcherOperationsSource');
            if (result.operations_guide && result.operations_guide.operations && result.operations_guide.operations.length > 0) {
                opsGuideDiv.style.display = 'block';
                const guide = result.operations_guide;
                opsGuideScene.textContent = guide.scene_name || '调度操作指南';
                opsGuideList.innerHTML = guide.operations.map(op => '<li>' + op + '</li>').join('');
                opsGuideSource.textContent = '来源: ' + (guide.source || '-') + ' | 匹配度: ' + (guide.match_score || 0);
                console.log('显示调度员操作指南，步骤数:', guide.operations.length);
            } else {
                opsGuideDiv.style.display = 'none';
                console.log('调度员操作指南为空，不显示');
            }

            // 时刻表
            let tableHtml = '<table class="schedule-table"><thead><tr><th>车次</th><th>车站</th><th>到达</th><th>发车</th><th>延误</th></tr></thead><tbody>';
            for (let [trainId, stops] of Object.entries(result.optimized_schedule || {})) {
                // 安全检查：确保stops是可迭代的数组
                if (!stops || !Array.isArray(stops)) {
                    console.warn('列车 ' + trainId + ' 的stops不是有效数组:', stops);
                    continue;
                }
                for (let stop of stops) {
                    // 安全检查：确保stop是对象
                    if (!stop || typeof stop !== 'object') {
                        console.warn('列车 ' + trainId + ' 的stop项不是有效对象:', stop);
                        continue;
                    }
                    const delay = stop.delay_seconds || 0;
                    const delayClass = delay > 0 ? 'delay-red' : 'delay-green';
                    const delayText = delay > 0 ? '+' + delay + '秒' : '准点';
                    tableHtml += '<tr><td>' + trainId + '</td><td>' + (stop.station_name || stop.station_code) + '</td><td>' + stop.arrival_time + '</td><td>' + stop.departure_time + '</td><td><span class="delay-tag ' + delayClass + '">' + delayText + '</span></td></tr>';
                }
            }
            tableHtml += '</tbody></table>';
            document.getElementById('scheduleTable').innerHTML = tableHtml;

            // 运行图
            if (result.optimized_schedule && result.original_schedule) {
                fetch('/api/diagram', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        original_schedule: result.original_schedule,
                        optimized_schedule: result.optimized_schedule
                    })
                })
                .then(resp => resp.json())
                .then(data => {
                    if (data.success) {
                        const html = '<img src="data:image/png;base64,' + data.diagram_image + '" style="max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 8px;">';
                        document.getElementById('diagramContainer').innerHTML = html;
                    }
                });
            }
            
            // 显示比较结果（如果有）
            if (stats.ranking && stats.ranking.length > 0) {
                showComparisonResult(stats);
            }
        }

        // LLM多轮对话 - 全局变量
        let currentSessionId = null;
        let currentLayer = 0;
        let waitingForInfo = false;  // 是否正在等待用户补充信息
        let missingFields = [];      // 缺失的字段列表

        // LLM多轮对话 - 开始工作流
        function startLlmWorkflow() {
            const userInput = document.getElementById('llmChatInput').value.trim();
            if (!userInput) {
                alert('请输入调度需求');
                return;
            }

            // 如果正在等待补充信息，使用补充信息API
            if (waitingForInfo && currentSessionId) {
                continueWithAdditionalInfo(userInput);
                return;
            }

            // 显示加载状态
            document.getElementById('llmProgress').textContent = '正在启动...';
            document.getElementById('continueBtn').disabled = true;

            fetch('/api/workflow/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    user_input: userInput,
                    snapshot_info: {}
                })
            })
            .then(response => response.json())
            .then(result => {
                if (result.success) {
                    currentSessionId = result.session_id;
                    currentLayer = result.current_layer;
                    updateChatHistory(result.messages);
                    updateProgress(result.current_layer, result.progress);
                    updateLayerBadges(result.current_layer);
                    document.getElementById('continueBtn').disabled = false;
                    document.getElementById('resetBtn').disabled = false;
                    document.getElementById('llmResultSection').style.display = 'none';

                    // 检查是否需要补充信息
                    if (result.needs_more_info) {
                        waitingForInfo = true;
                        missingFields = result.missing_fields || [];
                        showInfoRequest(result.message, missingFields);
                    } else {
                        waitingForInfo = false;
                        missingFields = [];
                    }
                } else {
                    alert('启动失败: ' + result.message);
                }
            })
            .catch(error => {
                alert('请求失败: ' + error.message);
            });
        }

        // 显示信息补充请求
        function showInfoRequest(message, fields) {
            const chatDiv = document.getElementById('chatHistory');
            const infoRequestHtml = `
                <div class="chat-message chat-system info-request">
                    <span class="msg-content">
                        <strong>需要补充信息：</strong><br>
                        ${message}<br>
                        <small style="color: #666;">请在下方输入框中补充信息后再次点击"开始工作流"</small>
                    </span>
                </div>
            `;
            chatDiv.innerHTML += infoRequestHtml;
            chatDiv.scrollTop = chatDiv.scrollHeight;

            // 清空输入框并设置提示
            const input = document.getElementById('llmChatInput');
            input.placeholder = `请补充: ${fields.join(', ')}...`;
            input.value = '';
            input.focus();
        }

        // 使用补充信息继续工作流
        function continueWithAdditionalInfo(additionalInfo) {
            document.getElementById('llmProgress').textContent = '正在处理补充信息...';

            fetch('/api/workflow/continue', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    session_id: currentSessionId,
                    additional_info: additionalInfo
                })
            })
            .then(response => response.json())
            .then(result => {
                if (result.success) {
                    currentLayer = result.current_layer;
                    updateChatHistory(result.messages);
                    updateProgress(result.current_layer, result.progress);
                    updateLayerBadges(result.current_layer);

                    // 检查是否还需要更多信息
                    if (result.needs_more_info) {
                        waitingForInfo = true;
                        missingFields = result.missing_fields || [];
                        showInfoRequest(result.message, missingFields);
                    } else {
                        waitingForInfo = false;
                        missingFields = [];
                        document.getElementById('llmChatInput').placeholder = '请输入调度需求（如：G1563在石家庄因大风限速）...';
                    }
                } else {
                    alert('处理失败: ' + result.message);
                }
            })
            .catch(error => {
                alert('请求失败: ' + error.message);
            });
        }

        // LLM多轮对话 - 继续执行下一层
        function continueLlmWorkflow() {
            if (!currentSessionId) {
                alert('请先开始一个新会话');
                return;
            }

            document.getElementById('llmProgress').textContent = '正在执行...';
            document.getElementById('continueBtn').disabled = true;

            fetch('/api/workflow/next', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    session_id: currentSessionId,
                    continue_layer: true
                })
            })
            .then(response => response.json())
            .then(result => {
                if (result.success) {
                    currentLayer = result.current_layer;
                    updateChatHistory(result.messages);
                    updateProgress(result.current_layer, result.progress);
                    updateLayerBadges(result.current_layer);

                    // 显示结果详情
                    const resultContent = document.getElementById('llmResultContent');

                    // 显示LLM响应类型
                    let responseTypeInfo = "";
                    if (currentLayer === 1 && result.layer1_result && result.layer1_result.llm_response_type) {
                        responseTypeInfo = "\n\n[LLM Response Type: " + result.layer1_result.llm_response_type + "]";
                    } else if (currentLayer === 2 && result.layer2_result && result.layer2_result.llm_response_type) {
                        responseTypeInfo = "\n\n[LLM Response Type: " + result.layer2_result.llm_response_type + "]";
                    } else if (currentLayer === 4 && result.layer4_result && result.layer4_result.llm_response_type) {
                        responseTypeInfo = "\n\n[LLM Response Type: " + result.layer4_result.llm_response_type + "]";
                    }

                    if (currentLayer === 1) {
                        resultContent.textContent = JSON.stringify(result.layer1_result, null, 2) + responseTypeInfo;
                        document.getElementById('continueBtn').disabled = false;
                    } else if (currentLayer === 2) {
                        resultContent.textContent = JSON.stringify(result.layer2_result, null, 2) + responseTypeInfo;
                        document.getElementById('continueBtn').disabled = false;
                    } else if (currentLayer === 3) {
                        resultContent.textContent = JSON.stringify(result.layer3_result, null, 2);
                        document.getElementById('continueBtn').disabled = false;
                    } else if (currentLayer === 4) {
                        resultContent.textContent = JSON.stringify(result.layer4_result, null, 2) + responseTypeInfo;
                        document.getElementById('continueBtn').disabled = true;
                        document.getElementById('llmProgress').textContent = '已完成';

                        // 显示调度结果摘要（总延误信息和推荐方案）
                        // 优先使用all_layer_results（后端已整理好的完整数据）
                        const allResults = result.all_layer_results || {
                            layer1_result: result.layer1_result,
                            layer2_result: result.layer2_result,
                            layer3_result: result.layer3_result,
                            layer4_result: result.layer4_result
                        };
                        showLlmDispatchSummary(allResults);
                    } else {
                        document.getElementById('continueBtn').disabled = true;
                    }
                    document.getElementById('llmResultSection').style.display = 'block';
                } else {
                    alert('执行失败: ' + result.message);
                    document.getElementById('continueBtn').disabled = false;
                }
            })
            .catch(error => {
                alert('请求失败: ' + error.message);
                document.getElementById('continueBtn').disabled = false;
            });
        }

        // LLM多轮对话 - 重置会话
        function resetLlmWorkflow() {
            if (currentSessionId) {
                fetch('/api/workflow/reset', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({session_id: currentSessionId})
                });
            }
            currentSessionId = null;
            currentLayer = 0;
            waitingForInfo = false;
            missingFields = [];
            document.getElementById('llmChatInput').value = '';
            document.getElementById('llmChatInput').placeholder = '请输入调度需求（如：G1563在石家庄因大风限速）...';
            document.getElementById('chatHistory').innerHTML = '<p style="color: #999; text-align: center;">暂无对话记录，请输入调度需求开始</p>';
            document.getElementById('llmProgress').textContent = '等待开始';
            document.getElementById('continueBtn').disabled = true;
            document.getElementById('resetBtn').disabled = true;
            document.getElementById('llmResultSection').style.display = 'none';
            document.getElementById('llmDispatchSummary').style.display = 'none';
            updateLayerBadges(0);
        }

        // 更新对话历史
        function updateChatHistory(messages) {
            const chatDiv = document.getElementById('chatHistory');
            chatDiv.innerHTML = messages.map(msg => {
                const cssClass = msg.role === 'user' ? 'chat-user' : 'chat-system';
                return `<div class="chat-message ${cssClass}"><span class="msg-content">${msg.content}</span></div>`;
            }).join('');
            chatDiv.scrollTop = chatDiv.scrollHeight;
        }

        // 更新进度显示
        function updateProgress(layer, progress) {
            document.getElementById('llmProgress').textContent = progress;
        }

        // 更新层级标签
        function updateLayerBadges(currentLayer) {
            for (let i = 1; i <= 4; i++) {
                const badge = document.getElementById('layer' + i + 'Badge');
                if (i < currentLayer) {
                    badge.className = 'layer-badge-done';
                } else if (i === currentLayer) {
                    badge.className = 'layer-badge-active';
                } else {
                    badge.className = '';
                }
            }
        }

        // 显示多轮对话调度结果摘要（总延误信息和推荐方案）
        function showLlmDispatchSummary(allResults) {
            const summarySection = document.getElementById('llmDispatchSummary');

            // 从L3结果获取延误统计
            const l3Result = allResults.layer3_result || {};
            const skillExecution = l3Result.skill_execution_result || {};
            const solverResponse = l3Result.solver_response || {};

            // 延误统计 - 优先使用L3层的精确数据
            let totalDelayMinutes = skillExecution.total_delay_minutes || 0;
            let maxDelayMinutes = skillExecution.max_delay_minutes || 0;

            // 如果L3没有数据，尝试从solver_response获取
            if (totalDelayMinutes === 0 && solverResponse.total_delay_seconds) {
                totalDelayMinutes = Math.round(solverResponse.total_delay_seconds / 60);
            }
            if (maxDelayMinutes === 0 && solverResponse.max_delay_seconds) {
                maxDelayMinutes = Math.round(solverResponse.max_delay_seconds / 60);
            }

            const avgDelayMinutes = totalDelayMinutes > 0 ? Math.round(totalDelayMinutes / Math.max(1, l3Result.affected_trains_count || 1)) : 0;

            // 更新延误显示
            document.getElementById('llmMaxDelay').textContent = maxDelayMinutes + '分钟';
            document.getElementById('llmAvgDelay').textContent = avgDelayMinutes + '分钟';
            document.getElementById('llmTotalDelay').textContent = totalDelayMinutes + '分钟';

            // 从L2结果获取推荐方案
            const l2Result = allResults.layer2_result || {};
            const skillDispatch = l2Result.skill_dispatch || {};

            // 推荐方案 - 显示求解器名称
            const recommendedSolver = skillDispatch['主技能'] || l3Result.skill_name || '未知';
            const solverNames = {
                'mip': 'MIP优化求解器（整数规划）',
                'fcfs': 'FCFS先到先服务',
                'max_delay_first': '最大延误优先',
                'noop': '无操作（基线）'
            };
            document.getElementById('llmRecommendedScheduler').textContent = recommendedSolver + ' - ' + (solverNames[recommendedSolver] || recommendedSolver);
            document.getElementById('llmRecommendedReason').textContent = skillDispatch['阻塞项'] && skillDispatch['阻塞项'].length > 0
                ? '阻塞项: ' + skillDispatch['阻塞项'].join(', ')
                : '执行状态: ' + (skillExecution.execution_status || '完成');

            // LLM评估摘要 - 从L4结果获取
            const l4Result = allResults.layer4_result || {};
            const evalReport = l4Result.evaluation_report || {};
            const llmSummary = evalReport.llm_summary || '';
            if (llmSummary) {
                document.getElementById('llmEvalSummary').style.display = 'block';
                document.getElementById('llmEvalText').textContent = llmSummary;
            } else {
                document.getElementById('llmEvalSummary').style.display = 'none';
            }

            // 场景和技能信息 - 从L1结果获取
            const l1Result = allResults.layer1_result || {};
            const accidentCard = l1Result.accident_card || {};
            document.getElementById('llmScenario').textContent = accidentCard.scene_category || '-';
            document.getElementById('llmSkill').textContent = accidentCard.fault_type || '-';
            document.getElementById('llmSolver').textContent = recommendedSolver;

            // 显示摘要区域
            summarySection.style.display = 'block';
        }

        // 发送智能调度（带比较）
        function sendDispatchWithComparison() {
            const prompt = document.getElementById('dispatchPrompt').value.trim();
            if (!prompt) {
                alert('请输入调度需求');
                return;
            }

            document.getElementById('dispatchLoading').style.display = 'block';
            document.getElementById('dispatchResult').style.display = 'none';
            document.getElementById('comparisonResultSection').style.display = 'none';

            fetch('/api/agent_chat_with_comparison', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({prompt: prompt, comparison_criteria: 'balanced'})
            })
            .then(response => response.json())
            .then(result => {
                document.getElementById('dispatchLoading').style.display = 'none';

                if (result.success) {
                    showDispatchResult(result);
                } else {
                    alert('执行失败: ' + result.message);
                }
            })
            .catch(error => {
                document.getElementById('dispatchLoading').style.display = 'none';
                console.error('请求失败:', error);
                alert('请求失败: ' + error.message + '\\\n\\\n请检查：\\\n1. 后端服务是否正常运行\\\n2. 浏览器控制台是否有更多错误信息');
            });
        }
        
        // 显示比较结果
        function showComparisonResult(stats) {
            const section = document.getElementById('comparisonResultSection');
            const rankingDiv = document.getElementById('comparisonRanking');
            const recDiv = document.getElementById('comparisonRecommendations');
            
            // 生成排名表格
            let rankingHtml = '<table class="schedule-table"><thead><tr><th>排名</th><th>调度器</th><th>最大延误</th><th>平均延误</th><th>得分</th></tr></thead><tbody>';
            for (let r of stats.ranking || []) {
                const winner = r.rank === 1 ? ' ⭐' : '';
                rankingHtml += '<tr><td>' + r.rank + winner + '</td><td>' + r.scheduler + '</td><td>' + r.max_delay_minutes + '分钟</td><td>' + r.avg_delay_minutes + '分钟</td><td>' + r.score.toFixed(1) + '</td></tr>';
            }
            rankingHtml += '</tbody></table>';
            rankingDiv.innerHTML = rankingHtml;
            
            // 显示推荐
            let recHtml = '<div class="recommendation"><h4>推荐方案</h4><ul>';
            for (let rec of stats.recommendations || []) {
                recHtml += '<li>' + rec + '</li>';
            }
            recHtml += '</ul></div>';
            recDiv.innerHTML = recHtml;
            
            section.style.display = 'block';
        }
        
        // 运行调度比较
        function runComparison() {
            const trainId = document.getElementById('comparisonTrainId').value;
            const station = document.getElementById('comparisonStation').value;
            const delayMinutes = parseInt(document.getElementById('comparisonDelayMinutes').value);
            const criteria = document.getElementById('comparisonCriteria').value;

            document.getElementById('comparisonLoading').style.display = 'block';
            document.getElementById('comparisonResultDisplay').style.display = 'none';

            fetch('/api/scheduler_comparison', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    train_id: trainId,
                    station_code: station,
                    delay_seconds: delayMinutes * 60,
                    criteria: criteria
                })
            })
            .then(response => response.json())
            .then(result => {
                document.getElementById('comparisonLoading').style.display = 'none';

                if (result.success && result.comparison) {
                    // 显示比较报告
                    displayComparisonReport(result);
                } else {
                    alert('执行失败: ' + result.message);
                }
            })
            .catch(error => {
                document.getElementById('comparisonLoading').style.display = 'none';
                console.error('请求失败:', error);
                alert('请求失败: ' + error.message);
            });
        }
        
        // 显示比较报告
        function displayComparisonReport(result) {
            const display = document.getElementById('comparisonResultDisplay');
            const reportDiv = document.getElementById('comparisonReport');
            
            let html = '';
            
            // 推荐方案
            if (result.comparison && result.comparison.recommendation) {
                const rec = result.comparison.recommendation;
                html += '<div class="recommendation" style="margin-bottom: 20px;">';
                html += '<h4>推荐方案: ' + rec.scheduler_name + '</h4>';
                html += '<div class="grid">';
                html += '<div class="metric"><div class="metric-value">' + rec.key_metrics.max_delay_minutes + '分钟</div><div class="metric-label">最大延误</div></div>';
                html += '<div class="metric"><div class="metric-value">' + rec.key_metrics.avg_delay_minutes + '分钟</div><div class="metric-label">平均延误</div></div>';
                html += '<div class="metric"><div class="metric-value">' + rec.key_metrics.on_time_rate + '%</div><div class="metric-label">准点率</div></div>';
                html += '</div></div>';
            }
            
            // 所有方案
            if (result.comparison && result.comparison.all_options) {
                html += '<h4 style="margin: 15px 0 10px;">所有方法对比</h4>';
                html += '<table class="schedule-table"><thead><tr><th>排名</th><th>调度器</th><th>最大延误</th><th>平均延误</th><th>计算时间</th></tr></thead><tbody>';
                for (let opt of result.comparison.all_options) {
                    const winner = opt.rank === 1 ? ' ⭐' : '';
                    html += '<tr><td>' + opt.rank + winner + '</td><td>' + opt.name + '</td><td>' + opt.max_delay_minutes + '分钟</td><td>' + opt.avg_delay_minutes + '分钟</td><td>' + opt.computation_time.toFixed(2) + '秒</td></tr>';
                }
                html += '</tbody></table>';
            }
            
            // 分析建议
            if (result.comparison && result.comparison.analysis) {
                html += '<h4 style="margin: 15px 0 10px;">分析</h4><ul>';
                for (let a of result.comparison.analysis) {
                    html += '<li>' + a + '</li>';
                }
                html += '</ul>';
            }
            
            reportDiv.innerHTML = html;
            display.style.display = 'block';
        }
    