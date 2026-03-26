# 前端网络错误调试指南

## 问题描述
前端报错：`请求失败: TypeError: NetworkError when attempting to fetch resource.`

## 可能的原因和解决方案

### 1. 后端服务未启动
**症状**：所有API请求都失败，返回网络错误

**解决方案**：
```bash
cd railway_dispatch
python web/app.py
```

**预期输出**：
```
2024-03-26 10:00:00 - __main__ - INFO - ==================================================
2024-03-26 10:00:00 - __main__ - INFO - 铁路调度Agent系统 v1.0
2024-03-26 10:00:00 - __main__ - INFO - ==================================================
2024-03-26 10:00:00 - __main__ - INFO - 访问地址: http://localhost:8080
2024-03-26 10:00:00 - __main__ - INFO - 按 Ctrl+C 停止服务
2024-03-26 10:00:00 - __main__ - INFO - ==================================================
2024-03-26 10:00:00 - web.app - INFO - 已启用真实数据模式
```

---

### 2. 端口被占用
**症状**：启动时显示端口被占用错误

**解决方案**：
```bash
# 查找占用8080端口的进程
lsof -i :8080
# 或
netstat -tulnp | grep 8080

# 杀死占用端口的进程
kill -9 <PID>

# 重新启动服务
python web/app.py
```

---

### 3. CORS跨域问题
**症状**：浏览器控制台显示CORS相关错误

**已修复**：已添加CORS支持
```python
from flask_cors import CORS
app = Flask(__name__)
CORS(app)  # 启用跨域支持
```

---

### 4. 依赖未安装
**症状**：启动时显示模块导入错误

**解决方案**：
```bash
pip install flask flask-cors
```

---

### 5. Python代码错误
**症状**：后端启动失败或运行时崩溃

**解决方案**：
```bash
# 检查Python语法
python -m py_compile web/app.py

# 查看详细错误信息
python web/app.py
```

---

## 调试步骤

### 第一步：检查后端是否正常启动
```bash
cd railway_dispatch
python web/app.py
```

看到以下输出说明启动成功：
```
Running on http://0.0.0.0:8080
```

### 第二步：测试API端点
```bash
# 测试根路径
curl http://localhost:8080/

# 测试API
curl -X POST http://localhost:8080/api/agent_chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "测试"}'
```

### 第三步：检查浏览器控制台
1. 打开浏览器开发者工具（F12）
2. 切换到Console（控制台）标签
3. 查看是否有JavaScript错误
4. 切换到Network（网络）标签
5. 刷新页面，查看失败的请求
6. 点击失败的请求，查看Response

### 第四步：检查后端日志
查看终端输出，查找以下信息：
```
2024-03-26 10:00:00 - web.app - INFO - 收到agent_chat请求，prompt: ...
2024-03-26 10:00:00 - web.app - ERROR - agent_chat处理异常: ...
```

---

## 常见错误和解决方案

### 错误1：Connection refused
**原因**：后端服务未启动或端口错误

**解决方案**：
```bash
# 确保后端已启动
python web/app.py

# 检查端口是否正确（默认8080）
```

### 错误2：404 Not Found
**原因**：API路径错误

**解决方案**：
- 确保使用正确的API路径：
  - `/api/agent_chat` - 智能对话
  - `/api/dispatch` - 表单调度
  - `/api/diagram` - 生成运行图

### 错误3：500 Internal Server Error
**原因**：后端代码错误

**解决方案**：
```bash
# 查看后端日志，定位错误
# 常见原因：
# 1. 模型未初始化
# 2. 数据加载失败
# 3. 求解器错误
```

### 错误4：CORS错误
**原因**：跨域请求被阻止

**已修复**：已添加CORS支持

---

## 日志级别控制

如果想要减少日志输出，修改 `web/app.py` 中的日志级别：

```python
# 只显示WARNING和ERROR
logging.basicConfig(level=logging.WARNING)

# 只显示ERROR
logging.basicConfig(level=logging.ERROR)

# 显示所有信息（包括DEBUG）
logging.basicConfig(level=logging.DEBUG)
```

---

## 快速诊断脚本

创建一个简单的测试脚本：

```python
# test_api.py
import requests

def test_api():
    base_url = "http://localhost:8080"

    # 测试根路径
    try:
        response = requests.get(base_url)
        print(f"✓ 根路径访问成功: {response.status_code}")
    except Exception as e:
        print(f"✗ 根路径访问失败: {e}")

    # 测试API
    try:
        response = requests.post(
            f"{base_url}/api/agent_chat",
            json={"prompt": "测试"}
        )
        print(f"✓ API调用成功: {response.status_code}")
        print(f"  响应: {response.json()}")
    except Exception as e:
        print(f"✗ API调用失败: {e}")

if __name__ == "__main__":
    test_api()
```

运行测试：
```bash
python test_api.py
```

---

## 联系支持

如果以上方法都无法解决问题，请提供以下信息：
1. 完整的错误信息（浏览器控制台和后端日志）
2. 操作系统和Python版本
3. 已尝试的解决方法
4. 后端启动的完整输出

---

## 更新日志

### 2024-03-26
- ✅ 添加CORS跨域支持
- ✅ 改进前端错误处理，显示HTTP状态码
- ✅ 添加详细的日志记录
- ✅ 优化异常处理和错误信息
