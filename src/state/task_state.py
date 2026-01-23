"""任务状态管理模块"""
import os
import json
import uuid
import sqlite3
import logging
import datetime
from typing import Dict, Any, Optional, List

from .models import Task
from src.storage import delete_s3_file

_logger = logging.getLogger("yt_dlp_api")


class State:
    """任务状态管理器"""
    
    def __init__(self):
        self.tasks: Dict[str, Task] = {}
        self.db_file = "tasks.db"
        # 初始化数据库
        self._init_db()
        # 从数据库加载任务状态
        self._load_tasks()
    
    def _init_db(self) -> None:
        """初始化SQLite数据库"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # 创建任务表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            output_path TEXT NOT NULL,
            format TEXT NOT NULL,
            status TEXT NOT NULL,
            result TEXT,
            error TEXT,
            s3_url TEXT,
            timestamp TEXT NOT NULL
        )
        ''')
        
        # 检查并添加s3_url列（用于数据库迁移）
        try:
            cursor.execute("ALTER TABLE tasks ADD COLUMN s3_url TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            # 列已存在，忽略错误
            pass
        
        conn.commit()
        conn.close()
    
    def _load_tasks(self) -> None:
        """从数据库加载任务状态"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute("SELECT id, url, output_path, format, status, result, error, s3_url FROM tasks")
            rows = cursor.fetchall()
            
            for row in rows:
                task_id, url, output_path, format, status, result_json, error, s3_url = row
                
                # 解析JSON结果（如果有）
                result = json.loads(result_json) if result_json else None
                
                # 创建Task对象并存储在内存中
                task = Task(
                    id=task_id,
                    url=url,
                    output_path=output_path,
                    format=format,
                    status=status,
                    result=result,
                    error=error,
                    s3_url=s3_url
                )
                self.tasks[task_id] = task
                
            conn.close()
        except Exception as e:
            print(f"Error loading tasks from database: {e}")
    
    def _save_task(self, task: Task) -> None:
        """将任务状态保存到数据库"""
        try:
            # 先更新内存中的任务状态
            self.tasks[task.id] = task
            
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            timestamp = datetime.datetime.now().isoformat()
            result_json = json.dumps(task.result) if task.result else None
            
            # 使用REPLACE策略插入/更新任务
            cursor.execute('''
            INSERT OR REPLACE INTO tasks (id, url, output_path, format, status, result, error, s3_url, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task.id,
                task.url,
                task.output_path,
                task.format,
                task.status,
                result_json,
                task.error,
                task.s3_url,
                timestamp
            ))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error saving task to database: {e}")
    
    def add_task(self, url: str, output_path: str, format: str) -> str:
        """添加新任务"""
        task_id = str(uuid.uuid4())
        task = Task(
            id=task_id,
            url=url,
            output_path=output_path,
            format=format,
            status="pending"
        )
        self.tasks[task_id] = task
        
        # 将任务保存到数据库
        self._save_task(task)
        
        return task_id
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """获取任务"""
        return self.tasks.get(task_id)
    
    def update_task(
        self, 
        task_id: str, 
        status: str, 
        result: Optional[Dict[str, Any]] = None, 
        error: Optional[str] = None, 
        s3_url: Optional[str] = None, 
        clear_fields: bool = False
    ) -> None:
        """更新任务状态"""
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.status = status
            if result is not None or clear_fields:
                task.result = result
            if error is not None or clear_fields:
                task.error = error
            if s3_url is not None or clear_fields:
                task.s3_url = s3_url
            
            # 将更新后的任务状态保存到数据库
            self._save_task(task)
    
    def list_tasks(self) -> List[Task]:
        """列出所有任务"""
        return list(self.tasks.values())
    
    def delete_task(self, task_id: str) -> tuple[bool, Optional[str], Optional[str]]:
        """
        删除任务及其对应的文件
        
        Args:
            task_id: 任务ID
            
        Returns:
            tuple: (是否成功, 删除的文件路径, 错误信息)
        """
        task = self.tasks.get(task_id)
        if not task:
            return False, None, "Task not found"
        
        deleted_file = None
        deleted_s3 = False
        
        # 如果有S3文件，尝试删除
        if task.s3_url:
            try:
                deleted_s3 = delete_s3_file(task.s3_url)
                if deleted_s3:
                    _logger.info(f"[S3] Deleted S3 file for task {task_id}: {task.s3_url}")
            except Exception as e:
                _logger.error(f"[S3] Error deleting S3 file for task {task_id}: {e}")
        
        # 如果任务已完成且本地文件存在，尝试删除对应的本地文件
        if task.status == "completed" and task.result:
            try:
                # 从结果中提取文件名
                filename = task.result.get("requested_downloads", [{}])[0].get("filename")
                if not filename:
                    requested_filename = task.result.get("requested_filename")
                    if requested_filename:
                        filename = requested_filename
                    else:
                        # 尝试构建可能的文件路径
                        title = task.result.get("title", "video")
                        ext = task.result.get("ext", "mp4")
                        filename = os.path.join(task.output_path, f"{title}.{ext}")
                
                # 删除本地文件
                if filename and os.path.exists(filename):
                    os.remove(filename)
                    deleted_file = filename
            except Exception as e:
                print(f"Error deleting file for task {task_id}: {e}")
        
        # 从内存中删除
        if task_id in self.tasks:
            del self.tasks[task_id]
        
        # 从数据库中删除
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            # 执行VACUUM整理数据库，回收空间防止数据库膨胀
            cursor.execute("VACUUM")
            conn.close()
        except Exception as e:
            return False, deleted_file, f"Error deleting from database: {e}"
        
        return True, deleted_file, None


# 创建全局状态对象
state = State()
