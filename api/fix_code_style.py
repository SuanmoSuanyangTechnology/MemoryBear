"""
自动修复代码风格问题的脚本
"""
import os
import re
from pathlib import Path


def fix_trailing_whitespace(content):
    """移除行尾空白"""
    lines = content.split('\n')
    fixed_lines = [line.rstrip() for line in lines]
    return '\n'.join(fixed_lines)


def ensure_newline_at_eof(content):
    """确保文件末尾有换行符"""
    if content and not content.endswith('\n'):
        return content + '\n'
    return content


def remove_blank_line_whitespace(content):
    """移除空行中的空白字符"""
    lines = content.split('\n')
    fixed_lines = []
    for line in lines:
        if line.strip() == '':
            fixed_lines.append('')
        else:
            fixed_lines.append(line)
    return '\n'.join(fixed_lines)


def fix_file(filepath):
    """修复单个文件"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        original_content = content
        
        # 应用修复
        content = fix_trailing_whitespace(content)
        content = remove_blank_line_whitespace(content)
        content = ensure_newline_at_eof(content)
        
        # 只有内容改变时才写入
        if content != original_content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        return False
    except Exception as e:
        print(f"Error fixing {filepath}: {e}")
        return False


def main():
    """主函数"""
    memory_path = Path('app/core/memory')
    
    if not memory_path.exists():
        print(f"Path {memory_path} does not exist")
        return
    
    fixed_count = 0
    total_count = 0
    
    # 遍历所有 Python 文件
    for py_file in memory_path.rglob('*.py'):
        total_count += 1
        if fix_file(py_file):
            fixed_count += 1
            print(f"Fixed: {py_file}")
    
    print(f"\n修复完成:")
    print(f"  总文件数: {total_count}")
    print(f"  已修复: {fixed_count}")
    print(f"  未改变: {total_count - fixed_count}")


if __name__ == '__main__':
    main()
