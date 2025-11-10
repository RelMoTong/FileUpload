"""
v1.9 测试数据准备脚本
自动创建测试所需的各种文件
"""

import os
from pathlib import Path
from PIL import Image
import random

def create_test_directories():
    """创建测试目录结构"""
    base_dir = Path(__file__).parent
    test_dir = base_dir / "测试数据"
    
    dirs = {
        'source': test_dir / "源文件夹",
        'target': test_dir / "目标文件夹",
        'backup': test_dir / "备份文件夹",
        'small_files': test_dir / "小文件测试集",
        'large_files': test_dir / "大文件测试集",
        'duplicate_files': test_dir / "重复文件测试集",
        'mixed_files': test_dir / "混合测试集",
    }
    
    for name, path in dirs.items():
        path.mkdir(parents=True, exist_ok=True)
        print(f"✓ 创建目录: {path}")
    
    return dirs

def create_image(path: Path, width: int, height: int, color=None):
    """创建指定大小的测试图片"""
    if color is None:
        color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
    
    img = Image.new('RGB', (width, height), color)
    img.save(path, 'JPEG', quality=95)
    size_mb = path.stat().st_size / (1024 * 1024)
    print(f"  - {path.name} ({size_mb:.2f} MB)")

def create_small_files(directory: Path):
    """创建小文件测试集（1-5MB）"""
    print("\n📁 创建小文件测试集...")
    sizes = [
        (800, 600),   # ~1MB
        (1024, 768),  # ~2MB
        (1280, 1024), # ~3MB
        (1600, 1200), # ~4MB
        (2048, 1536), # ~5MB
    ]
    
    for i, (w, h) in enumerate(sizes, 1):
        for j in range(2):  # 每种大小创建2个
            filename = f"small_{i}_{j+1}.jpg"
            create_image(directory / filename, w, h)

def create_large_files(directory: Path):
    """创建大文件测试集（10-50MB）"""
    print("\n📁 创建大文件测试集...")
    sizes = [
        (3000, 3000),  # ~15MB
        (4000, 4000),  # ~30MB
        (5000, 5000),  # ~50MB
    ]
    
    for i, (w, h) in enumerate(sizes, 1):
        filename = f"large_{i}.jpg"
        create_image(directory / filename, w, h)

def create_duplicate_files(directory: Path):
    """创建重复文件测试集（3个内容相同的文件）"""
    print("\n📁 创建重复文件测试集...")
    
    # 创建一个基础图片
    base_path = directory / "base_image.jpg"
    color = (100, 150, 200)  # 固定颜色确保内容相同
    create_image(base_path, 1024, 768, color)
    
    # 复制为3个不同文件名
    import shutil
    for i in range(1, 4):
        target_path = directory / f"duplicate_{i}.jpg"
        shutil.copy2(base_path, target_path)
        print(f"  - {target_path.name} (副本)")
    
    # 删除基础文件
    base_path.unlink()

def create_long_filename_file(directory: Path):
    """创建超长文件名的文件"""
    print("\n📁 创建超长文件名测试文件...")
    long_name = "这是一个非常非常非常长的文件名用于测试UI显示效果_IMG_20231015_143052_高清风景照片_非常详细的描述信息_还有更多内容.jpg"
    create_image(directory / long_name, 1024, 768)

def create_mixed_files(directory: Path):
    """创建混合测试集"""
    print("\n📁 创建混合测试集...")
    
    # 5个小文件
    for i in range(5):
        create_image(directory / f"mixed_small_{i+1}.jpg", 1024, 768)
    
    # 2个大文件
    for i in range(2):
        create_image(directory / f"mixed_large_{i+1}.jpg", 3000, 3000)
    
    # 3个重复文件
    color = (200, 100, 150)
    base_path = directory / "temp_base.jpg"
    create_image(base_path, 1024, 768, color)
    
    import shutil
    for i in range(1, 4):
        target_path = directory / f"mixed_duplicate_{i}.jpg"
        shutil.copy2(base_path, target_path)
        print(f"  - {target_path.name} (重复)")
    
    base_path.unlink()

def create_test_summary(dirs: dict):
    """创建测试数据说明文件"""
    summary_path = dirs['source'].parent / "测试数据说明.txt"
    
    content = """
╔════════════════════════════════════════════════════════════╗
║           v1.9 测试数据说明                                  ║
╚════════════════════════════════════════════════════════════╝

📁 目录结构：
  ├─ 源文件夹/           （用于实际测试的源文件夹）
  ├─ 目标文件夹/         （上传目标文件夹）
  ├─ 备份文件夹/         （归档备份文件夹）
  ├─ 小文件测试集/       （10个 1-5MB 文件）
  ├─ 大文件测试集/       （3个 10-50MB 文件）
  ├─ 重复文件测试集/     （3个内容相同的文件）
  └─ 混合测试集/         （混合大小和重复文件）

📝 使用方法：

【功能1测试 - 当前文件进度显示】
1. 小文件测试：
   - 将"小文件测试集"中的文件复制到"源文件夹"
   - 观察进度条快速完成
   
2. 大文件测试：
   - 将"大文件测试集"中的文件复制到"源文件夹"
   - 观察进度条逐步增长

【功能2测试 - 智能去重】
1. 跳过策略测试：
   - 将"重复文件测试集"中的3个文件复制到"源文件夹"
   - 启用去重（MD5 + 跳过）
   - 第一次上传：3个文件都上传
   - 第二次上传：只上传1个，跳过2个
   
2. 重命名策略测试：
   - 清空目标文件夹
   - 修改策略为"重命名"
   - 再次上传，查看文件名变化

【功能3测试 - 网络监控】
1. 本地路径测试：
   - 使用本地路径，观察"🟢 正常"状态
   
2. 网络中断测试（可选）：
   - 将目标文件夹设为网络路径或U盘
   - 上传中途断开网络/拔出U盘
   - 观察自动暂停和恢复

【综合测试】
- 将"混合测试集"中的文件复制到"源文件夹"
- 启用所有功能
- 进行完整上传流程测试

⚠️ 注意事项：
1. 每次测试前清空"源文件夹"、"目标文件夹"、"备份文件夹"
2. 测试去重功能时，需要进行两次上传
3. 大文件测试可能需要几分钟时间
4. 建议按照测试指南顺序进行测试

📊 测试数据统计：
- 小文件：10个 (约 30 MB)
- 大文件：3个 (约 95 MB)
- 重复文件：3个 (约 6 MB)
- 混合文件：13个 (约 40 MB)
- 总计：29个文件 (约 171 MB)

🎯 快速开始：
1. 将"混合测试集"中的文件复制到"源文件夹"
2. 在软件中配置路径
3. 点击"开始上传"
4. 观察所有功能的表现

祝测试顺利！🚀
    """
    
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"\n✓ 创建说明文件: {summary_path}")

def main():
    """主函数"""
    print("=" * 60)
    print("          v1.9 测试数据生成工具")
    print("=" * 60)
    
    try:
        # 创建目录
        dirs = create_test_directories()
        
        # 创建各种测试文件
        create_small_files(dirs['small_files'])
        create_large_files(dirs['large_files'])
        create_duplicate_files(dirs['duplicate_files'])
        create_long_filename_file(dirs['small_files'])
        create_mixed_files(dirs['mixed_files'])
        
        # 创建说明文件
        create_test_summary(dirs)
        
        print("\n" + "=" * 60)
        print("✅ 测试数据生成完成！")
        print("=" * 60)
        print(f"\n📂 测试数据位置: {dirs['source'].parent}")
        print("\n📝 下一步:")
        print("1. 查看'测试数据说明.txt'了解使用方法")
        print("2. 将对应测试集的文件复制到'源文件夹'")
        print("3. 在软件中配置路径")
        print("4. 开始测试")
        print("\n🎯 建议：先使用'混合测试集'进行快速验证\n")
        
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
