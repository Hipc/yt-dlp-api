"""下载相关的请求模型"""
from pydantic import BaseModel


class DownloadRequest(BaseModel):
    """下载请求模型"""
    url: str
    output_path: str = "./downloads"
    format: str = "bestvideo+bestaudio/best"
    quiet: bool = False
