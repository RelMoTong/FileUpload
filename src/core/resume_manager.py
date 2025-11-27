# -*- coding: utf-8 -*-
"""
断点续传管理模块

v3.0.2 新增功能：
- 大文件上传中断后可恢复
- 记录上传进度到本地文件
- 支持 SMB 和 FTP 协议
- 自动清理过期的续传记录
"""

import os
import json
import hashlib
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import threading

logger = logging.getLogger(__name__)


class ResumeManager:
    """断点续传管理器
    
    功能：
    - 记录大文件上传进度
    - 支持上传中断后恢复
    - 自动清理过期记录
    - 支持多协议（SMB/FTP）
    """
    
    # 最小续传文件大小（10MB 以上才启用续传）
    MIN_RESUME_SIZE = 10 * 1024 * 1024
    
    # 续传记录过期时间（7天）
    RECORD_EXPIRE_DAYS = 7
    
    def __init__(self, app_dir: Path):
        """初始化续传管理器
        
        Args:
            app_dir: 应用程序目录
        """
        self.app_dir = app_dir
        self.resume_dir = app_dir / 'resume_data'
        self.resume_dir.mkdir(parents=True, exist_ok=True)
        
        self._lock = threading.Lock()
        self._active_uploads: Dict[str, Dict[str, Any]] = {}
        
        # 启动时清理过期记录
        self._cleanup_expired_records()
        
        logger.info(f"断点续传管理器初始化: {self.resume_dir}")
    
    def _get_file_id(self, file_path: str) -> str:
        """生成文件唯一标识
        
        使用文件路径 + 大小 + 修改时间生成唯一 ID
        
        Args:
            file_path: 文件路径
            
        Returns:
            文件唯一标识（MD5哈希）
        """
        try:
            stat = os.stat(file_path)
            id_str = f"{file_path}|{stat.st_size}|{stat.st_mtime}"
            return hashlib.md5(id_str.encode('utf-8')).hexdigest()[:16]
        except Exception as e:
            logger.warning(f"生成文件ID失败: {e}")
            return hashlib.md5(file_path.encode('utf-8')).hexdigest()[:16]
    
    def _get_record_path(self, file_id: str) -> Path:
        """获取续传记录文件路径"""
        return self.resume_dir / f"{file_id}.resume"
    
    def should_resume(self, file_path: str) -> bool:
        """判断文件是否应该启用断点续传
        
        Args:
            file_path: 源文件路径
            
        Returns:
            是否启用断点续传
        """
        try:
            file_size = os.path.getsize(file_path)
            return file_size >= self.MIN_RESUME_SIZE
        except Exception:
            return False
    
    def get_resume_info(self, file_path: str, target_path: str) -> Optional[Dict[str, Any]]:
        """获取续传信息
        
        Args:
            file_path: 源文件路径
            target_path: 目标文件路径
            
        Returns:
            续传信息字典，如果没有续传记录返回 None
            {
                'file_id': 文件ID,
                'uploaded_bytes': 已上传字节数,
                'total_bytes': 文件总大小,
                'temp_file': 临时文件路径,
                'last_update': 最后更新时间,
                'protocol': 上传协议
            }
        """
        with self._lock:
            file_id = self._get_file_id(file_path)
            record_path = self._get_record_path(file_id)
            
            if not record_path.exists():
                return None
            
            try:
                with open(record_path, 'r', encoding='utf-8') as f:
                    record = json.load(f)
                
                # 验证记录有效性
                if record.get('source_path') != file_path:
                    logger.debug(f"源文件路径不匹配: {file_path}")
                    return None
                
                if record.get('target_path') != target_path:
                    logger.debug(f"目标路径不匹配: {target_path}")
                    return None
                
                # 检查源文件是否被修改
                current_size = os.path.getsize(file_path)
                if record.get('total_bytes') != current_size:
                    logger.info(f"文件大小已变化，删除旧记录: {file_path}")
                    self._delete_record(file_id)
                    return None
                
                # 检查临时文件是否存在
                temp_file = record.get('temp_file', '')
                if temp_file and not os.path.exists(temp_file):
                    logger.info(f"临时文件不存在，删除记录: {temp_file}")
                    self._delete_record(file_id)
                    return None
                
                # 验证已上传的字节数
                uploaded_bytes = record.get('uploaded_bytes', 0)
                if temp_file:
                    actual_size = os.path.getsize(temp_file)
                    if actual_size != uploaded_bytes:
                        logger.info(f"临时文件大小不匹配: 记录={uploaded_bytes}, 实际={actual_size}")
                        record['uploaded_bytes'] = actual_size
                
                logger.info(f"找到续传记录: {file_path}, 已上传 {record['uploaded_bytes']}/{record['total_bytes']} 字节")
                return record
                
            except Exception as e:
                logger.warning(f"读取续传记录失败: {e}")
                self._delete_record(file_id)
                return None
    
    def create_resume_record(
        self,
        file_path: str,
        target_path: str,
        protocol: str = 'smb'
    ) -> Dict[str, Any]:
        """创建续传记录
        
        Args:
            file_path: 源文件路径
            target_path: 目标文件路径
            protocol: 上传协议 (smb/ftp_client)
            
        Returns:
            续传记录字典
        """
        with self._lock:
            file_id = self._get_file_id(file_path)
            file_size = os.path.getsize(file_path)
            
            # 生成临时文件路径
            target_dir = os.path.dirname(target_path)
            target_name = os.path.basename(target_path)
            temp_file = os.path.join(target_dir, f".{target_name}.part")
            
            record = {
                'file_id': file_id,
                'source_path': file_path,
                'target_path': target_path,
                'temp_file': temp_file,
                'total_bytes': file_size,
                'uploaded_bytes': 0,
                'protocol': protocol,
                'created_at': datetime.now().isoformat(),
                'last_update': datetime.now().isoformat(),
                'checksum_md5': '',  # 可选：完成后验证
            }
            
            # 保存记录
            record_path = self._get_record_path(file_id)
            with open(record_path, 'w', encoding='utf-8') as f:
                json.dump(record, f, indent=2, ensure_ascii=False)
            
            # 添加到活跃上传列表
            self._active_uploads[file_id] = record
            
            logger.info(f"创建续传记录: {file_path} -> {target_path}")
            return record
    
    def update_progress(self, file_path: str, uploaded_bytes: int) -> bool:
        """更新上传进度
        
        Args:
            file_path: 源文件路径
            uploaded_bytes: 已上传字节数
            
        Returns:
            是否更新成功
        """
        with self._lock:
            file_id = self._get_file_id(file_path)
            record_path = self._get_record_path(file_id)
            
            if not record_path.exists():
                return False
            
            try:
                with open(record_path, 'r', encoding='utf-8') as f:
                    record = json.load(f)
                
                record['uploaded_bytes'] = uploaded_bytes
                record['last_update'] = datetime.now().isoformat()
                
                with open(record_path, 'w', encoding='utf-8') as f:
                    json.dump(record, f, indent=2, ensure_ascii=False)
                
                # 更新内存中的记录
                if file_id in self._active_uploads:
                    self._active_uploads[file_id]['uploaded_bytes'] = uploaded_bytes
                
                return True
                
            except Exception as e:
                logger.warning(f"更新续传进度失败: {e}")
                return False
    
    def complete_upload(self, file_path: str, success: bool = True) -> bool:
        """完成上传（成功或失败）
        
        Args:
            file_path: 源文件路径
            success: 是否上传成功
            
        Returns:
            是否处理成功
        """
        with self._lock:
            file_id = self._get_file_id(file_path)
            
            if success:
                # 删除续传记录和临时文件
                self._delete_record(file_id)
                logger.info(f"上传完成，已删除续传记录: {file_path}")
            else:
                # 保留记录供下次续传
                logger.info(f"上传中断，保留续传记录: {file_path}")
            
            # 从活跃列表移除
            self._active_uploads.pop(file_id, None)
            
            return True
    
    def _delete_record(self, file_id: str):
        """删除续传记录"""
        record_path = self._get_record_path(file_id)
        
        try:
            if record_path.exists():
                # 先读取记录获取临时文件路径
                try:
                    with open(record_path, 'r', encoding='utf-8') as f:
                        record = json.load(f)
                    
                    # 删除临时文件（如果上传成功则不需要）
                    temp_file = record.get('temp_file', '')
                    if temp_file and os.path.exists(temp_file):
                        # 检查是否已经完成（目标文件存在）
                        target_file = record.get('target_path', '')
                        if target_file and os.path.exists(target_file):
                            os.remove(temp_file)
                            logger.debug(f"删除临时文件: {temp_file}")
                except Exception:
                    pass
                
                # 删除记录文件
                os.remove(record_path)
                logger.debug(f"删除续传记录: {file_id}")
        except Exception as e:
            logger.warning(f"删除续传记录失败: {e}")
    
    def _cleanup_expired_records(self):
        """清理过期的续传记录"""
        try:
            expire_time = datetime.now() - timedelta(days=self.RECORD_EXPIRE_DAYS)
            cleaned = 0
            
            for record_file in self.resume_dir.glob("*.resume"):
                try:
                    with open(record_file, 'r', encoding='utf-8') as f:
                        record = json.load(f)
                    
                    last_update = datetime.fromisoformat(record.get('last_update', ''))
                    if last_update < expire_time:
                        file_id = record_file.stem
                        self._delete_record(file_id)
                        cleaned += 1
                except Exception:
                    # 无法解析的记录直接删除
                    record_file.unlink(missing_ok=True)
                    cleaned += 1
            
            if cleaned > 0:
                logger.info(f"清理过期续传记录: {cleaned} 个")
                
        except Exception as e:
            logger.warning(f"清理过期记录失败: {e}")
    
    def get_active_uploads(self) -> Dict[str, Dict[str, Any]]:
        """获取所有活跃的上传任务
        
        Returns:
            活跃上传任务字典
        """
        with self._lock:
            return self._active_uploads.copy()
    
    def get_pending_resumes(self) -> list:
        """获取所有待续传的记录
        
        Returns:
            待续传记录列表
        """
        pending = []
        
        try:
            for record_file in self.resume_dir.glob("*.resume"):
                try:
                    with open(record_file, 'r', encoding='utf-8') as f:
                        record = json.load(f)
                    
                    # 检查源文件是否仍然存在
                    source_path = record.get('source_path', '')
                    if source_path and os.path.exists(source_path):
                        pending.append(record)
                    else:
                        # 源文件不存在，删除记录
                        self._delete_record(record_file.stem)
                        
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"获取待续传记录失败: {e}")
        
        return pending


class ResumableFileUploader:
    """可续传的文件上传器
    
    封装断点续传逻辑，支持 SMB 和 FTP 协议
    """
    
    def __init__(
        self,
        resume_manager: ResumeManager,
        buffer_size: int = 1024 * 1024,  # 1MB 缓冲区
        progress_callback=None
    ):
        """初始化上传器
        
        Args:
            resume_manager: 续传管理器
            buffer_size: 缓冲区大小
            progress_callback: 进度回调 callback(uploaded, total, filename)
        """
        self.resume_manager = resume_manager
        self.buffer_size = buffer_size
        self.progress_callback = progress_callback
        self._stop_flag = False
    
    def stop(self):
        """停止上传"""
        self._stop_flag = True
    
    def reset(self):
        """重置停止标志"""
        self._stop_flag = False
    
    def upload_with_resume(
        self,
        source_path: str,
        target_path: str,
        rate_limit_bytes: int = 0
    ) -> Tuple[bool, str]:
        """带断点续传的文件上传
        
        Args:
            source_path: 源文件路径
            target_path: 目标文件路径
            rate_limit_bytes: 速率限制（字节/秒），0 表示不限速
            
        Returns:
            (是否成功, 错误信息)
        """
        self._stop_flag = False
        
        try:
            file_size = os.path.getsize(source_path)
            filename = os.path.basename(source_path)
            
            # 检查是否需要断点续传
            if not self.resume_manager.should_resume(source_path):
                # 小文件直接复制
                return self._simple_copy(source_path, target_path, rate_limit_bytes)
            
            # 获取续传信息
            resume_info = self.resume_manager.get_resume_info(source_path, target_path)
            
            if resume_info:
                # 续传模式
                uploaded_bytes = resume_info['uploaded_bytes']
                temp_file = resume_info['temp_file']
                logger.info(f"续传模式: {filename}, 从 {uploaded_bytes} 字节继续")
            else:
                # 新建上传
                resume_info = self.resume_manager.create_resume_record(
                    source_path, target_path, 'smb'
                )
                uploaded_bytes = 0
                temp_file = resume_info['temp_file']
                logger.info(f"新建上传: {filename}, 总大小 {file_size} 字节")
            
            # 确保目标目录存在
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            
            # 打开源文件和目标临时文件
            mode = 'ab' if uploaded_bytes > 0 else 'wb'
            
            with open(source_path, 'rb') as src:
                # 跳到已上传的位置
                if uploaded_bytes > 0:
                    src.seek(uploaded_bytes)
                
                with open(temp_file, mode) as dst:
                    while not self._stop_flag:
                        chunk_start = time.time()
                        
                        # 读取数据块
                        chunk = src.read(self.buffer_size)
                        if not chunk:
                            break
                        
                        # 写入数据
                        dst.write(chunk)
                        uploaded_bytes += len(chunk)
                        
                        # 更新进度
                        self.resume_manager.update_progress(source_path, uploaded_bytes)
                        
                        if self.progress_callback:
                            self.progress_callback(uploaded_bytes, file_size, filename)
                        
                        # 速率限制
                        if rate_limit_bytes > 0:
                            expected_time = len(chunk) / rate_limit_bytes
                            elapsed_time = time.time() - chunk_start
                            if elapsed_time < expected_time:
                                time.sleep(expected_time - elapsed_time)
            
            # 检查是否被中断
            if self._stop_flag:
                logger.info(f"上传被中断: {filename}, 已保存进度")
                self.resume_manager.complete_upload(source_path, success=False)
                return False, "上传被用户中断"
            
            # 上传完成，重命名临时文件
            if os.path.exists(target_path):
                os.remove(target_path)
            os.rename(temp_file, target_path)
            
            # 复制文件属性
            import shutil
            shutil.copystat(source_path, target_path)
            
            # 标记完成
            self.resume_manager.complete_upload(source_path, success=True)
            logger.info(f"上传完成: {filename}")
            
            return True, ""
            
        except Exception as e:
            logger.error(f"上传失败: {e}")
            self.resume_manager.complete_upload(source_path, success=False)
            return False, str(e)
    
    def _simple_copy(
        self,
        source_path: str,
        target_path: str,
        rate_limit_bytes: int = 0
    ) -> Tuple[bool, str]:
        """简单复制（不启用续传）"""
        try:
            file_size = os.path.getsize(source_path)
            filename = os.path.basename(source_path)
            uploaded_bytes = 0
            
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            
            with open(source_path, 'rb') as src, open(target_path, 'wb') as dst:
                while not self._stop_flag:
                    chunk_start = time.time()
                    
                    chunk = src.read(self.buffer_size)
                    if not chunk:
                        break
                    
                    dst.write(chunk)
                    uploaded_bytes += len(chunk)
                    
                    if self.progress_callback:
                        self.progress_callback(uploaded_bytes, file_size, filename)
                    
                    if rate_limit_bytes > 0:
                        expected_time = len(chunk) / rate_limit_bytes
                        elapsed_time = time.time() - chunk_start
                        if elapsed_time < expected_time:
                            time.sleep(expected_time - elapsed_time)
            
            if self._stop_flag:
                if os.path.exists(target_path):
                    os.remove(target_path)
                return False, "上传被用户中断"
            
            import shutil
            shutil.copystat(source_path, target_path)
            
            return True, ""
            
        except Exception as e:
            return False, str(e)
