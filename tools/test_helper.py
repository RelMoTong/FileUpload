"""
快速测试辅助工具
提供快捷命令进行测试
"""

import os
import shutil
from pathlib import Path

# 测试数据路径
BASE_DIR = Path(__file__).parent
TEST_DATA = BASE_DIR / "测试数据"
SOURCE_DIR = TEST_DATA / "源文件夹"
TARGET_DIR = TEST_DATA / "目标文件夹"
BACKUP_DIR = TEST_DATA / "备份文件夹"

def clear_folders():
    """清空所有测试文件夹"""
    print("\n🗑️ 清空测试文件夹...")
    for folder in [SOURCE_DIR, TARGET_DIR, BACKUP_DIR]:
        if folder.exists():
            for item in folder.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            print(f"  ✓ 清空: {folder.name}")

def copy_test_files(test_set_name):
    """复制测试文件到源文件夹"""
    test_sets = {
        '1': ('混合测试集', '测试当前文件进度显示'),
        '2': ('重复文件测试集', '测试智能去重'),
        '3': ('小文件测试集', '测试网络监控'),
        '4': ('大文件测试集', '测试大文件进度'),
        '5': ('所有文件', '压力测试'),
    }
    
    if test_set_name not in test_sets:
        print("❌ 无效的测试集编号")
        return False
    
    set_name, purpose = test_sets[test_set_name]
    print(f"\n📋 测试场景: {purpose}")
    print(f"📁 复制文件集: {set_name}")
    
    if test_set_name == '5':
        # 复制所有文件
        count = 0
        for test_dir in TEST_DATA.iterdir():
            if test_dir.is_dir() and test_dir.name.endswith('测试集'):
                for file in test_dir.iterdir():
                    if file.is_file():
                        shutil.copy2(file, SOURCE_DIR / file.name)
                        count += 1
        print(f"  ✓ 已复制 {count} 个文件到源文件夹")
    else:
        src_folder = TEST_DATA / set_name
        if not src_folder.exists():
            print(f"❌ 找不到测试集: {set_name}")
            return False
        
        count = 0
        for file in src_folder.iterdir():
            if file.is_file():
                shutil.copy2(file, SOURCE_DIR / file.name)
                count += 1
        print(f"  ✓ 已复制 {count} 个文件到源文件夹")
    
    return True

def show_menu():
    """显示菜单"""
    print("\n" + "=" * 60)
    print("           v1.9 快速测试工具")
    print("=" * 60)
    print("\n📋 可用操作:")
    print("  0. 清空所有测试文件夹")
    print("  1. 复制混合测试集（测试当前文件进度）")
    print("  2. 复制重复文件测试集（测试智能去重）")
    print("  3. 复制小文件测试集（测试网络监控）")
    print("  4. 复制大文件测试集（测试大文件进度）")
    print("  5. 复制所有文件（压力测试）")
    print("  6. 查看文件夹状态")
    print("  q. 退出")
    print("=" * 60)

def show_status():
    """显示当前文件夹状态"""
    print("\n📊 当前文件夹状态:")
    
    for folder, name in [(SOURCE_DIR, "源文件夹"), 
                         (TARGET_DIR, "目标文件夹"), 
                         (BACKUP_DIR, "备份文件夹")]:
        if not folder.exists():
            print(f"  {name}: ❌ 不存在")
            continue
        
        files = list(folder.glob("*.jpg")) + list(folder.glob("*.jpeg"))
        total_size = sum(f.stat().st_size for f in files) / (1024 * 1024)
        print(f"  {name}: {len(files)} 个文件 ({total_size:.2f} MB)")

def main():
    """主函数"""
    while True:
        show_menu()
        choice = input("\n请选择操作 (0-6, q): ").strip()
        
        if choice.lower() == 'q':
            print("\n👋 再见！")
            break
        elif choice == '0':
            confirm = input("⚠️ 确认清空所有文件夹? (y/n): ").strip().lower()
            if confirm == 'y':
                clear_folders()
                print("✅ 清空完成")
            else:
                print("❌ 已取消")
        elif choice in ['1', '2', '3', '4', '5']:
            confirm = input(f"⚠️ 这将覆盖源文件夹中的现有文件，继续? (y/n): ").strip().lower()
            if confirm == 'y':
                clear_folders()  # 先清空
                if copy_test_files(choice):
                    print("✅ 准备完成")
                    print("\n📝 下一步:")
                    print("1. 在软件中配置测试路径")
                    print("2. 根据测试场景调整设置")
                    print("3. 点击'开始上传'")
                    print("4. 观察并记录测试结果")
            else:
                print("❌ 已取消")
        elif choice == '6':
            show_status()
        else:
            print("❌ 无效的选择，请重试")
        
        input("\n按回车键继续...")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 已取消")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
