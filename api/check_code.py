#!/usr/bin/env python3
"""
代码质量检查脚本
自动检查代码中的导入错误、未使用变量、语法问题等

用法:
    python check_code.py                    # 检查整个 app/ 目录
    python check_code.py file1.py file2.py  # 检查指定文件
"""

import subprocess
import sys
from pathlib import Path


def run_command(cmd: list[str], description: str) -> tuple[bool, str]:
    """运行命令并返回结果"""
    print(f"\n{'=' * 60}")
    print(f"🔍 {description}")
    print(f"{'=' * 60}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        output = result.stdout + result.stderr
        success = result.returncode == 0

        if success:
            print(f"✅ {description} - 通过")
        else:
            print(f"❌ {description} - 发现问题")
            if output:
                print(output[:2000])  # 只显示前2000字符

        return success, output

    except Exception as e:
        print(f"❌ 执行失败: {e}")
        return False, str(e)


def main():
    """主函数"""
    # 获取命令行参数中的文件列表
    target_files = sys.argv[1:] if len(sys.argv) > 1 else None
    
    if target_files:
        # 检查指定文件
        print(f"🚀 开始代码质量检查 (指定文件: {len(target_files)} 个)...")
        target_paths = target_files
        ruff_target = target_files
        py_compile_files = [f for f in target_files if f.endswith('.py')]
    else:
        # 检查整个 app/ 目录
        print("🚀 开始代码质量检查 (整个 app/ 目录)...")
        target_paths = ["app/"]
        ruff_target = ["app/"]
        py_compile_files = list(Path("app").rglob("*.py"))

    checks = [
        {
            "cmd": ["ruff", "check"] + ruff_target + ["--output-format=concise"],
            "description": "Ruff 代码检查 (导入、语法、风格)",
            "auto_fix": ["ruff", "check"] + ruff_target + ["--fix", "--unsafe-fixes"],
        },
        {
            "cmd": ["python", "-m", "py_compile"] + [str(f) for f in py_compile_files],
            "description": "Python 语法检查",
            "auto_fix": None,
        },
    ]

    results = []
    for check in checks:
        success, output = run_command(check["cmd"], check["description"])
        results.append(
            {"name": check["description"], "success": success, "output": output, "auto_fix": check.get("auto_fix")}
        )

    # 汇总报告
    print(f"\n{'=' * 60}")
    print("📊 检查汇总")
    print(f"{'=' * 60}")

    all_passed = True
    for result in results:
        status = "✅ 通过" if result["success"] else "❌ 失败"
        print(f"{status} - {result['name']}")
        if not result["success"]:
            all_passed = False
            if result["auto_fix"]:
                print(f"   💡 可以运行自动修复: {' '.join(result['auto_fix'])}")

    if all_passed:
        print("\n🎉 所有检查通过！")
        return 0
    else:
        print("\n⚠️  发现问题，请查看上面的详细信息")
        print("\n💡 快速修复命令:")
        if target_files:
            print(f"   ruff check {' '.join(target_files)} --fix --unsafe-fixes")
        else:
            print("   ruff check app/ --fix --unsafe-fixes")
        return 1


if __name__ == "__main__":
    sys.exit(main())
