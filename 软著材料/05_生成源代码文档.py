# -*- coding: utf-8 -*-
"""
软件著作权申请 - 源代码文档生成工具

功能：生成符合软著申请要求的源代码文档
要求：
  - 前30页 + 后30页
  - 每页不少于50行有效代码
  - 页眉标注软件名称和版本
  - 左侧标注行号

使用方法：
  python 05_生成源代码文档.py

输出文件：
  - 03_源代码_前30页.txt
  - 04_源代码_后30页.txt
  - 源代码_完整版.txt
"""

import os
from pathlib import Path
from datetime import datetime
from typing import Optional


# ============== 配置区域 ==============
SOFTWARE_NAME = "图片异步上传工具软件"
SOFTWARE_VERSION = "V3.1.0"
LINES_PER_PAGE = 50  # 每页行数
FRONT_PAGES = 30     # 前N页
BACK_PAGES = 30      # 后N页
# =====================================


def get_project_root():
    """获取项目根目录"""
    # 当前脚本在 软著材料 文件夹中，项目根目录是上一级
    return Path(__file__).parent.parent


def collect_source_files(project_root: Path) -> list:
    """收集所有Python源代码文件"""
    source_files = []
    
    # 主要源代码目录
    source_dirs = [
        project_root / "src",
    ]
    
    # 根目录的主要文件
    root_files = [
        project_root / "pyqt_app.py",
        project_root / "qt_types.py",
    ]
    
    # 要排除的目录
    exclude_dirs = [
        "__pycache__",
        ".git",
        "tests",
        "dist",
        "build",
        "venv",
        ".venv",
        "软著材料",
        "backup",
        "tools",
    ]
    
    # 添加根目录文件
    for f in root_files:
        if f.exists() and f.is_file():
            source_files.append(f)
    
    # 递归收集src目录下的文件
    for source_dir in source_dirs:
        if not source_dir.exists():
            continue
            
        for py_file in source_dir.rglob("*.py"):
            # 检查是否在排除列表中
            skip = False
            for exclude in exclude_dirs:
                if exclude in str(py_file):
                    skip = True
                    break
            
            if not skip and py_file.is_file():
                source_files.append(py_file)
    
    # 按文件路径排序，确保顺序一致
    source_files.sort(key=lambda x: str(x))
    
    return source_files


def read_file_content(file_path: Path) -> list:
    """读取文件内容，返回行列表（去除末尾换行符）"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            # 去除末尾换行符，保留内容
            return [line.rstrip('\n\r') for line in lines]
    except UnicodeDecodeError:
        try:
            with open(file_path, 'r', encoding='gbk') as f:
                lines = f.readlines()
                return [line.rstrip('\n\r') for line in lines]
        except:
            return []


def format_line_number(num: int, width: int = 5) -> str:
    """格式化行号"""
    return str(num).rjust(width)


def generate_page_header(page_num: int, total_pages: Optional[int] = None) -> str:
    """生成页眉"""
    header = f"{SOFTWARE_NAME}_{SOFTWARE_VERSION} 源代码"
    if total_pages is not None:
        page_info = f"第 {page_num} 页 / 共 {total_pages} 页"
    else:
        page_info = f"第 {page_num} 页"
    
    # 计算间距，使页眉左右对齐
    total_width = 70
    padding = total_width - len(header) - len(page_info)
    
    return f"{header}{' ' * max(padding, 4)}{page_info}"


def generate_page_separator() -> str:
    """生成页分隔线"""
    return "\n" + "─" * 70 + "\n"


def generate_source_document(project_root: Path, output_dir: Path):
    """生成源代码文档"""
    
    print("=" * 60)
    print(f"  {SOFTWARE_NAME} {SOFTWARE_VERSION}")
    print("  软件著作权申请 - 源代码文档生成工具")
    print("=" * 60)
    print()
    
    # 收集源文件
    source_files = collect_source_files(project_root)
    
    print(f"项目根目录: {project_root}")
    print(f"发现源文件: {len(source_files)} 个")
    print()
    
    # 读取所有代码行
    all_code_lines = []  # 存储 (行号, 代码内容, 所属文件)
    current_global_line = 0
    
    for file_path in source_files:
        relative_path = file_path.relative_to(project_root)
        lines = read_file_content(file_path)
        
        if lines:
            # 添加文件头注释
            all_code_lines.append((None, f"# {'=' * 60}", str(relative_path)))
            all_code_lines.append((None, f"# 文件: {relative_path}", str(relative_path)))
            all_code_lines.append((None, f"# {'=' * 60}", str(relative_path)))
            
            # 添加代码行
            for i, line in enumerate(lines, 1):
                current_global_line += 1
                all_code_lines.append((current_global_line, line, str(relative_path)))
            
            # 文件间空行
            all_code_lines.append((None, "", str(relative_path)))
    
    total_lines = current_global_line
    total_pages = (len(all_code_lines) + LINES_PER_PAGE - 1) // LINES_PER_PAGE
    
    print(f"有效代码行数: {total_lines}")
    print(f"总行数（含文件头）: {len(all_code_lines)}")
    print(f"预计页数: {total_pages} (每页 {LINES_PER_PAGE} 行)")
    print()
    
    # 文件列表
    print("源文件清单:")
    print("-" * 60)
    for i, f in enumerate(source_files, 1):
        rel_path = f.relative_to(project_root)
        line_count = len(read_file_content(f))
        print(f"  {i:2}. {str(rel_path):<45} ({line_count} 行)")
    print("-" * 60)
    print()
    
    # ========== 生成前30页 ==========
    front_file = output_dir / "03_源代码_前30页.txt"
    front_line_count = FRONT_PAGES * LINES_PER_PAGE
    
    with open(front_file, 'w', encoding='utf-8') as f:
        f.write("=" * 70 + "\n")
        f.write(f"  {SOFTWARE_NAME} {SOFTWARE_VERSION}\n")
        f.write(f"  源代码文档（前 {FRONT_PAGES} 页）\n")
        f.write("=" * 70 + "\n")
        f.write(f"  生成日期: {datetime.now().strftime('%Y年%m月%d日')}\n")
        f.write(f"  每页行数: {LINES_PER_PAGE} 行\n")
        f.write("=" * 70 + "\n\n")
        
        # 写入前N页
        for page in range(1, FRONT_PAGES + 1):
            # 页眉
            f.write(generate_page_header(page, total_pages) + "\n")
            f.write("-" * 70 + "\n")
            
            # 计算本页行范围
            start_idx = (page - 1) * LINES_PER_PAGE
            end_idx = min(start_idx + LINES_PER_PAGE, len(all_code_lines))
            
            # 写入代码
            for idx in range(start_idx, end_idx):
                if idx < len(all_code_lines):
                    line_num, code, _ = all_code_lines[idx]
                    if line_num is not None:
                        f.write(f"{format_line_number(line_num)}  {code}\n")
                    else:
                        f.write(f"{'':5}  {code}\n")
            
            # 页脚
            f.write(generate_page_separator())
    
    print(f"✓ 已生成: {front_file.name}")
    
    # ========== 生成后30页 ==========
    back_file = output_dir / "04_源代码_后30页.txt"
    
    # 计算后30页的起始行
    back_start_page = max(1, total_pages - BACK_PAGES + 1)
    
    with open(back_file, 'w', encoding='utf-8') as f:
        f.write("=" * 70 + "\n")
        f.write(f"  {SOFTWARE_NAME} {SOFTWARE_VERSION}\n")
        f.write(f"  源代码文档（后 {BACK_PAGES} 页）\n")
        f.write("=" * 70 + "\n")
        f.write(f"  生成日期: {datetime.now().strftime('%Y年%m月%d日')}\n")
        f.write(f"  每页行数: {LINES_PER_PAGE} 行\n")
        f.write(f"  页码范围: 第 {back_start_page} 页 - 第 {total_pages} 页\n")
        f.write("=" * 70 + "\n\n")
        
        # 写入后N页
        for page in range(back_start_page, total_pages + 1):
            # 页眉
            f.write(generate_page_header(page, total_pages) + "\n")
            f.write("-" * 70 + "\n")
            
            # 计算本页行范围
            start_idx = (page - 1) * LINES_PER_PAGE
            end_idx = min(start_idx + LINES_PER_PAGE, len(all_code_lines))
            
            # 写入代码
            for idx in range(start_idx, end_idx):
                if idx < len(all_code_lines):
                    line_num, code, _ = all_code_lines[idx]
                    if line_num is not None:
                        f.write(f"{format_line_number(line_num)}  {code}\n")
                    else:
                        f.write(f"{'':5}  {code}\n")
            
            # 页脚
            f.write(generate_page_separator())
    
    print(f"✓ 已生成: {back_file.name}")
    
    # ========== 生成完整版 ==========
    full_file = output_dir / "源代码_完整版.txt"
    
    with open(full_file, 'w', encoding='utf-8') as f:
        f.write("=" * 70 + "\n")
        f.write(f"  {SOFTWARE_NAME} {SOFTWARE_VERSION}\n")
        f.write(f"  源代码文档（完整版）\n")
        f.write("=" * 70 + "\n")
        f.write(f"  生成日期: {datetime.now().strftime('%Y年%m月%d日')}\n")
        f.write(f"  源文件数: {len(source_files)} 个\n")
        f.write(f"  代码行数: {total_lines} 行\n")
        f.write(f"  总页数: {total_pages} 页\n")
        f.write("=" * 70 + "\n\n")
        
        # 写入文件清单
        f.write("【源文件清单】\n")
        f.write("-" * 70 + "\n")
        for i, file_path in enumerate(source_files, 1):
            rel_path = file_path.relative_to(project_root)
            line_count = len(read_file_content(file_path))
            f.write(f"{i:3}. {str(rel_path):<50} ({line_count} 行)\n")
        f.write("-" * 70 + "\n\n")
        
        # 写入所有页
        for page in range(1, total_pages + 1):
            # 页眉
            f.write(generate_page_header(page, total_pages) + "\n")
            f.write("-" * 70 + "\n")
            
            # 计算本页行范围
            start_idx = (page - 1) * LINES_PER_PAGE
            end_idx = min(start_idx + LINES_PER_PAGE, len(all_code_lines))
            
            # 写入代码
            for idx in range(start_idx, end_idx):
                if idx < len(all_code_lines):
                    line_num, code, _ = all_code_lines[idx]
                    if line_num is not None:
                        f.write(f"{format_line_number(line_num)}  {code}\n")
                    else:
                        f.write(f"{'':5}  {code}\n")
            
            # 页脚
            f.write(generate_page_separator())
    
    print(f"✓ 已生成: {full_file.name}")
    
    # 统计信息
    print()
    print("=" * 60)
    print("  生成完成！")
    print("=" * 60)
    print()
    print("【提交软著申请时，请提交以下文件】")
    print(f"  1. {front_file.name} → 转换为PDF")
    print(f"  2. {back_file.name} → 转换为PDF")
    print()
    print("【转换PDF方法】")
    print("  方法一: 用Word打开txt文件，调整格式后另存为PDF")
    print("  方法二: 使用在线txt转pdf工具")
    print("  方法三: 使用打印功能，选择'打印到PDF'")
    print()
    print("【格式调整建议】")
    print("  - 字体: Courier New 或 宋体（等宽字体）")
    print("  - 字号: 五号 (10.5pt) 或 小五 (9pt)")
    print("  - 行距: 单倍行距")
    print("  - 页面: A4纸张")
    print()


if __name__ == "__main__":
    project_root = get_project_root()
    output_dir = Path(__file__).parent
    
    generate_source_document(project_root, output_dir)
    
    print("按回车键退出...")
    input()
