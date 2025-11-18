# -*- coding: utf-8 -*-
"""
智能去重服务 - 负责文件去重检测和处理

职责：
1. 基于哈希值的文件去重
2. 去重记录管理
3. 增量上传优化
4. 去重统计报告
"""

import os
import hashlib
from pathlib import Path
from typing import Dict, Set, Optional, List, Tuple
from dataclasses import dataclass
from datetime import datetime
import json


@dataclass
class DedupRecord:
    """去重记录"""
    file_hash: str  # 文件哈希值
    file_path: str  # 文件路径
    file_size: int  # 文件大小
    upload_time: datetime  # 上传时间
    
    def to_dict(self) -> Dict:
        return {
            'hash': self.file_hash,
            'path': self.file_path,
            'size': self.file_size,
            'time': self.upload_time.isoformat()
        }
    
    @staticmethod
    def from_dict(data: Dict) -> 'DedupRecord':
        return DedupRecord(
            file_hash=data['hash'],
            file_path=data['path'],
            file_size=data['size'],
            upload_time=datetime.fromisoformat(data['time'])
        )


class DedupService:
    """智能去重服务"""
    
    def __init__(self, cache_file: Optional[str] = None):
        """初始化去重服务
        
        Args:
            cache_file: 去重缓存文件路径，None 则使用默认路径
        """
        if cache_file is None:
            cache_file = os.path.join(os.getcwd(), '.upload_dedup_cache.json')
        
        self._cache_file = cache_file
        self._hash_cache: Dict[str, DedupRecord] = {}  # hash -> record
        self._path_to_hash: Dict[str, str] = {}  # path -> hash
        self._enabled: bool = True
        
        self._load_cache()
    
    # ============ 缓存管理 ============
    
    def _load_cache(self):
        """从文件加载去重缓存"""
        if not os.path.exists(self._cache_file):
            return
        
        try:
            with open(self._cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for item in data.get('records', []):
                    record = DedupRecord.from_dict(item)
                    self._hash_cache[record.file_hash] = record
                    self._path_to_hash[record.file_path] = record.file_hash
        except Exception:
            pass
    
    def _save_cache(self):
        """保存去重缓存到文件"""
        try:
            data = {
                'version': '1.0',
                'updated_at': datetime.now().isoformat(),
                'records': [r.to_dict() for r in self._hash_cache.values()]
            }
            with open(self._cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    
    def clear_cache(self):
        """清空去重缓存"""
        self._hash_cache.clear()
        self._path_to_hash.clear()
        try:
            if os.path.exists(self._cache_file):
                os.remove(self._cache_file)
        except Exception:
            pass
    
    # ============ 哈希计算 ============
    
    def calculate_hash(self, file_path: str, algorithm: str = 'md5') -> Optional[str]:
        """计算文件哈希值
        
        Args:
            file_path: 文件路径
            algorithm: 哈希算法 (md5, sha1, sha256)
        
        Returns:
            哈希值字符串，失败返回 None
        """
        try:
            hash_obj = hashlib.new(algorithm)
            with open(file_path, 'rb') as f:
                while chunk := f.read(8192):
                    hash_obj.update(chunk)
            return hash_obj.hexdigest()
        except Exception:
            return None
    
    def calculate_quick_hash(self, file_path: str) -> Optional[str]:
        """快速哈希（仅读取文件头尾）- 用于大文件预检
        
        读取策略：
        - 文件 < 1MB: 全文件哈希
        - 文件 >= 1MB: 头部 1MB + 尾部 1MB
        """
        try:
            file_size = os.path.getsize(file_path)
            hash_obj = hashlib.md5()
            
            with open(file_path, 'rb') as f:
                if file_size < 1024 * 1024:
                    # 小文件，全文件哈希
                    hash_obj.update(f.read())
                else:
                    # 大文件，读取头尾
                    hash_obj.update(f.read(1024 * 1024))  # 头部 1MB
                    f.seek(-1024 * 1024, 2)  # 从文件末尾倒数 1MB
                    hash_obj.update(f.read(1024 * 1024))  # 尾部 1MB
            
            return hash_obj.hexdigest()
        except Exception:
            return None
    
    # ============ 去重检测 ============
    
    def is_duplicate(self, file_path: str, use_quick_hash: bool = False) -> Tuple[bool, Optional[str]]:
        """检测文件是否重复
        
        Args:
            file_path: 文件路径
            use_quick_hash: 是否使用快速哈希（大文件）
        
        Returns:
            (is_duplicate, existing_path)
        """
        if not self._enabled:
            return False, None
        
        # 计算哈希
        if use_quick_hash:
            file_hash = self.calculate_quick_hash(file_path)
        else:
            file_hash = self.calculate_hash(file_path)
        
        if not file_hash:
            return False, None
        
        # 检查是否存在
        if file_hash in self._hash_cache:
            record = self._hash_cache[file_hash]
            return True, record.file_path
        
        return False, None
    
    def add_file(self, file_path: str, use_quick_hash: bool = False):
        """添加文件到去重记录
        
        Args:
            file_path: 文件路径
            use_quick_hash: 是否使用快速哈希
        """
        if not self._enabled:
            return
        
        # 计算哈希
        if use_quick_hash:
            file_hash = self.calculate_quick_hash(file_path)
        else:
            file_hash = self.calculate_hash(file_path)
        
        if not file_hash:
            return
        
        # 创建记录
        try:
            file_size = os.path.getsize(file_path)
            record = DedupRecord(
                file_hash=file_hash,
                file_path=file_path,
                file_size=file_size,
                upload_time=datetime.now()
            )
            
            self._hash_cache[file_hash] = record
            self._path_to_hash[file_path] = file_hash
            
            # 保存到磁盘
            self._save_cache()
            
        except Exception:
            pass
    
    def remove_file(self, file_path: str):
        """从去重记录中移除文件"""
        if file_path in self._path_to_hash:
            file_hash = self._path_to_hash[file_path]
            del self._hash_cache[file_hash]
            del self._path_to_hash[file_path]
            self._save_cache()
    
    # ============ 批量操作 ============
    
    def batch_check_duplicates(self, file_paths: List[str], use_quick_hash: bool = False) -> Dict[str, bool]:
        """批量检测重复文件
        
        Returns:
            {file_path: is_duplicate}
        """
        results = {}
        for file_path in file_paths:
            is_dup, _ = self.is_duplicate(file_path, use_quick_hash)
            results[file_path] = is_dup
        return results
    
    def batch_add_files(self, file_paths: List[str], use_quick_hash: bool = False):
        """批量添加文件到去重记录"""
        for file_path in file_paths:
            self.add_file(file_path, use_quick_hash)
    
    # ============ 统计信息 ============
    
    def get_statistics(self) -> Dict:
        """获取去重统计信息"""
        total_records = len(self._hash_cache)
        total_size = sum(r.file_size for r in self._hash_cache.values())
        
        return {
            'total_records': total_records,
            'total_size_mb': total_size / 1024 / 1024,
            'cache_file': self._cache_file,
            'enabled': self._enabled
        }
    
    def get_all_records(self) -> List[DedupRecord]:
        """获取所有去重记录"""
        return list(self._hash_cache.values())
    
    # ============ 配置管理 ============
    
    def enable(self):
        """启用去重"""
        self._enabled = True
    
    def disable(self):
        """禁用去重"""
        self._enabled = False
    
    @property
    def is_enabled(self) -> bool:
        """是否启用"""
        return self._enabled
    
    def set_cache_file(self, cache_file: str):
        """设置缓存文件路径"""
        self._cache_file = cache_file
        self._load_cache()
