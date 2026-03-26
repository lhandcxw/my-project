#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API测试脚本
用于诊断前端网络错误问题
"""

import requests
import json
import sys

def test_api():
    """测试API端点"""
    base_url = "http://localhost:8080"

    print("=" * 60)
    print("API连接测试")
    print("=" * 60)

    # 测试1: 根路径
    print("\n测试1: 访问根路径")
    try:
        response = requests.get(base_url, timeout=5)
        if response.status_code == 200:
            print(f"✓ 根路径访问成功: {response.status_code}")
        else:
            print(f"✗ 根路径访问失败: {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("✗ 无法连接到服务器，请确认后端服务已启动")
        print("  启动命令: cd railway_dispatch && python web/app.py")
        return False
    except Exception as e:
        print(f"✗ 根路径访问失败: {e}")
        return False

    # 测试2: agent_chat API
    print("\n测试2: 测试 /api/agent_chat")
    try:
        response = requests.post(
            f"{base_url}/api/agent_chat",
            json={"prompt": "G1001在北京西延误10分钟"},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        if response.status_code == 200:
            result = response.json()
            print(f"✓ API调用成功: {response.status_code}")
            print(f"  请求成功: {result.get('success')}")
            print(f"  消息: {result.get('message', 'N/A')}")
        else:
            print(f"✗ API调用失败: {response.status_code}")
            try:
                error_data = response.json()
                print(f"  错误信息: {error_data}")
            except:
                print(f"  响应内容: {response.text[:200]}")
    except requests.exceptions.Timeout:
        print("✗ API调用超时（可能正在初始化模型）")
    except Exception as e:
        print(f"✗ API调用失败: {e}")

    # 测试3: dispatch API
    print("\n测试3: 测试 /api/dispatch")
    try:
        response = requests.post(
            f"{base_url}/api/dispatch",
            json={
                "scenario_type": "temporary_speed_limit",
                "objective": "min_max_delay",
                "selected_trains": ["G1001"],
                "delay_config": [{
                    "train_id": "G1001",
                    "delay_seconds": 600,
                    "station_code": "BJX"
                }]
            },
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        if response.status_code == 200:
            result = response.json()
            print(f"✓ API调用成功: {response.status_code}")
            print(f"  请求成功: {result.get('success')}")
            print(f"  消息: {result.get('message', 'N/A')}")
        else:
            print(f"✗ API调用失败: {response.status_code}")
            try:
                error_data = response.json()
                print(f"  错误信息: {error_data}")
            except:
                print(f"  响应内容: {response.text[:200]}")
    except requests.exceptions.Timeout:
        print("✗ API调用超时（可能正在初始化模型）")
    except Exception as e:
        print(f"✗ API调用失败: {e}")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

    return True

if __name__ == "__main__":
    try:
        test_api()
    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
        sys.exit(0)
