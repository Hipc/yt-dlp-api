"""任务数据模型"""
from typing import Dict, Any, Optional
from pydantic import BaseModel


class Task(BaseModel):
    """下载任务模型"""
    id: str
    url: str
    output_path: str
    format: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    s3_url: Optional[str] = None
