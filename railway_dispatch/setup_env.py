# -*- coding: utf-8 -*-
"""
环境配置脚本
自动安装铁路调度系统所需的所有依赖
"""

import subprocess
import sys
import os


def run_command(cmd, description):
    """运行命令并显示进度"""
    print("\n" + "="*60)
    print(description)
    print("="*60)
    print(f"执行: {cmd}")
    print("-" * 60)

    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True
    )

    if result.stdout:
        print(result.stdout[:2000])
    if result.stderr:
        print("WARNING:", result.stderr[:500])

    if result.returncode != 0:
        print(f"FAILED (return code: {result.returncode})")
        return False
    else:
        print("OK")
        return True


def main():
    print("\n============================================================")
    print("Railway Dispatch System - Environment Setup")
    print("============================================================")

    print(f"Python version: {sys.version}")

    in_venv = sys.prefix != sys.base_prefix
    print(f"Virtual environment: {'Yes' if in_venv else 'No'}")

    if not in_venv:
        print("\nNOTE: It is recommended to use a virtual environment:")
        print("    python -m venv venv")
        print("    venv\\Scripts\\activate")

    project_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_dir)
    print(f"\nProject directory: {os.getcwd()}")

    # Step 1: Upgrade pip
    print("\n" + "="*60)
    print("Step 1: Upgrade pip")
    print("="*60)
    run_command(f"{sys.executable} -m pip install --upgrade pip", "Upgrading pip")

    # Step 2: Install core dependencies
    print("\n" + "="*60)
    print("Step 2: Install core dependencies")
    print("="*60)
    core_packages = "pulp flask flask-cors werkzeug pydantic numpy pandas matplotlib python-dateutil"
    run_command(f"{sys.executable} -m pip install {core_packages}", "Installing core packages")

    # Step 3: Install PyTorch
    print("\n" + "="*60)
    print("Step 3: Install PyTorch (CPU version)")
    print("="*60)
    run_command(
        f"{sys.executable} -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu",
        "Installing PyTorch CPU"
    )

    # Step 4: Install ModelScope
    print("\n" + "="*60)
    print("Step 4: Install ModelScope")
    print("="*60)
    run_command(f"{sys.executable} -m pip install modelscope", "Installing ModelScope")

    # Step 5: Verify installation
    print("\n" + "="*60)
    print("Step 5: Verify installation")
    print("="*60)

    packages_to_check = [
        ("pulp", "PuLP"),
        ("flask", "Flask"),
        ("pydantic", "Pydantic"),
        ("numpy", "NumPy"),
        ("matplotlib", "Matplotlib"),
        ("torch", "PyTorch"),
        ("modelscope", "ModelScope"),
        ("pandas", "Pandas"),
    ]

    all_ok = True
    for pkg, name in packages_to_check:
        try:
            __import__(pkg)
            print(f"OK - {name}")
        except ImportError:
            print(f"FAILED - {name}")
            all_ok = False

    print("\n" + "="*60)
    if all_ok:
        print("Environment setup completed!")
    else:
        print("Some packages failed to install.")
    print("="*60)

    print("""
Next steps:
1. Start web service: cd railway_dispatch && python web/app.py
2. Visit: http://localhost:8080
3. Test Agent: python -c "from railway_dispatch.qwen import create_qwen_agent; print(create_qwen_agent())"
""")


if __name__ == "__main__":
    main()
