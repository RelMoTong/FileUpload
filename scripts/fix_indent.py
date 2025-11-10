#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
临时脚本:修复 pyqt_app.py 中第995-1030行的缩进问题
"""

import sys

def fix_indentation():
    file_path = r"E:\Python\文件上传\pyqt_app.py"
    
    # 读取文件
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # 需要增加4个空格缩进的行范围(行号是从1开始的,列表索引从0开始)
    # 第995-1030行对应索引994-1029
    indent_start = 994  # 第995行
    indent_end = 1029   # 第1030行
    
    # 对这些行增加4个空格缩进
    for i in range(indent_start, indent_end + 1):
        if i < len(lines):
            # 只给非空行增加缩进
            if lines[i].strip():
                lines[i] = '    ' + lines[i]
    
    # 写回文件
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    
    print(f"✅ 已修复第{indent_start+1}-{indent_end+1}行的缩进")

if __name__ == '__main__':
    fix_indentation()
