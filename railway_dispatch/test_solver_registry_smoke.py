# -*- coding: utf-8 -*-
"""
求解器注册器冒烟测试
验证 registry 和 adapter 可导入、可调用
"""

import sys

# 添加项目根目录到 Python 路径
sys.path.insert(0, 'e:/LLM-TTRA/test-agent/railway_dispatch')


def test_imports():
    """测试模块导入"""
    print("测试导入...")

    # 测试导入 base_solver
    from solver.base_solver import BaseSolver, SolverRequest, SolverResponse
    print("  - base_solver: OK")

    # 测试导入 solver_registry
    from solver.solver_registry import SolverRegistry, get_default_registry
    print("  - solver_registry: OK")

    # 测试导入 fcfs_adapter
    from solver.fcfs_adapter import FCFSSolverAdapter
    print("  - fcfs_adapter: OK")

    # 测试导入 mip_adapter
    from solver.mip_adapter import MIPSolverAdapter
    print("  - mip_adapter: OK")

    return True


def test_registry_creation():
    """测试注册器创建"""
    print("\n测试注册器创建...")

    from solver.solver_registry import get_default_registry
    registry = get_default_registry()
    assert registry is not None, "注册器创建失败"

    solvers = registry.list_solvers()
    print(f"  - 已注册求解器: {solvers}")

    return True


def test_solver_selection():
    """测试求解器选择"""
    print("\n测试求解器选择...")

    from solver.solver_registry import SolverRegistry

    # 测试临时限速场景选择 MIP
    solver = SolverRegistry.select_solver("temporary_speed_limit")
    print(f"  - temporary_speed_limit -> {solver}")

    # 测试突发故障场景选择 FCFS
    solver = SolverRegistry.select_solver("sudden_failure")
    print(f"  - sudden_failure -> {solver}")

    # 测试区间中断场景选择 MIP
    solver = SolverRegistry.select_solver("section_interrupt")
    print(f"  - section_interrupt -> {solver}")

    return True


def test_adapter_creation():
    """测试适配器创建"""
    print("\n测试适配器创建...")

    from solver.fcfs_adapter import FCFSSolverAdapter
    from solver.mip_adapter import MIPSolverAdapter

    fcfs_adapter = FCFSSolverAdapter()
    assert fcfs_adapter is not None, "FCFS适配器创建失败"
    assert fcfs_adapter.get_solver_type() == "fcfs", "FCFS类型不匹配"
    print("  - FCFSSolverAdapter: OK")

    mip_adapter = MIPSolverAdapter()
    assert mip_adapter is not None, "MIP适配器创建失败"
    assert mip_adapter.get_solver_type() == "mip", "MIP类型不匹配"
    print("  - MIPSolverAdapter: OK")

    return True


def test_adapter_call():
    """测试适配器调用（空请求）"""
    print("\n测试适配器调用...")

    from solver.fcfs_adapter import FCFSSolverAdapter
    from solver.base_solver import SolverRequest

    fcfs_adapter = FCFSSolverAdapter()

    # 构造最小请求
    request = SolverRequest(
        scene_type="temporary_speed_limit",
        scene_id="test_001",
        trains=[],
        stations=[],
        injected_delays=[]
    )

    # 调用（会失败但不会抛异常）
    response = fcfs_adapter.solve(request)
    print(f"  - FCFS响应: success={response.success}, status={response.status}")

    # 验证返回了正确的结构（空数据时可能返回success）
    assert response.status in ["success", "solver_failed"], f"未知状态: {response.status}"
    print(f"  - FCFS适配器调用: OK (状态={response.status})")

    return True


def main():
    """主函数"""
    print("=" * 60)
    print("求解器注册器冒烟测试")
    print("=" * 60)

    try:
        test_imports()
        test_registry_creation()
        test_solver_selection()
        test_adapter_creation()
        test_adapter_call()

        print("\n" + "=" * 60)
        print("[SUCCESS] 所有测试通过!")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\n[FAILED] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())