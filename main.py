import yt_dlp
import os
import glob
import uuid

import asyncio

import json
import datetime
import sqlite3
from typing import Dict, Any, Optional, List, Callable, Set
from fastapi import FastAPI, HTTPException, Query, Depends, Header
from fastapi.responses import JSONResponse, FileResponse
import uvicorn
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor
from yt_dlp.utils import DownloadCancelled

def NormalizeString(s: str, max_length: int = 200) -> str:
    """
    去掉头尾的空格， 所有特殊字符转换成 _，并限制长度
    """
    s = s.strip()
    # 替换特殊字符
    special_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    for char in special_chars:
        s = s.replace(char, '_')
    
    # 限制长度，如果超长则截断并保持可读性
    if len(s) > max_length:
        # 保留前面的内容，并在末尾添加省略标记
        s = s[:max_length-3] + "..."
    
    return s

def create_safe_filename(title: str, format_str: str, ext: str, max_length: int = 200) -> str:
    """
    创建安全的文件名，确保不超过指定长度
    
    Args:
        title (str): 视频标题
        format_str (str): 格式字符串
        ext (str): 文件扩展名
        max_length (int): 最大文件名长度
        
    Returns:
        str: 安全的文件名
    """
    # 标准化格式字符串和扩展名
    safe_format = NormalizeString(format_str, 50)  # 格式前缀限制50字符
    safe_ext = ext.lower()
    
    # 计算标题可用的最大长度
    # 预留空间给格式前缀、分隔符和扩展名
    reserved_length = len(safe_format) + len(safe_ext) + 2  # 2个字符用于连接符
    available_title_length = max_length - reserved_length
    
    # 确保至少有20个字符用于标题
    if available_title_length < 20:
        available_title_length = 20
        safe_format = safe_format[:10]  # 缩短格式前缀
    
    # 标准化并截断标题
    safe_title = NormalizeString(title, available_title_length)
    
    # 构建最终文件名
    if safe_format:
        return f"{safe_format}-{safe_title}.{safe_ext}"
    else:
        return f"{safe_title}.{safe_ext}"

class Task(BaseModel):
    id: str
    url: str
    output_path: str
    format: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    progress: Optional[Dict[str, Any]] = None

class State:
    def __init__(self):
        self.tasks: Dict[str, Task] = {}
        self.cancel_requested: Set[str] = set()
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
            progress TEXT,
            error TEXT,
            timestamp TEXT NOT NULL
        )
        ''')

        cursor.execute("PRAGMA table_info(tasks)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "progress" not in existing_columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN progress TEXT")
        
        conn.commit()
        conn.close()
    
    def _load_tasks(self) -> None:
        """从数据库加载任务状态"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute("SELECT id, url, output_path, format, status, result, error, progress FROM tasks")
            rows = cursor.fetchall()
            
            for row in rows:
                task_id, url, output_path, format, status, result_json, error, progress_json = row
                
                # 解析JSON结果（如果有）
                result = json.loads(result_json) if result_json else None
                progress = json.loads(progress_json) if progress_json else None
                
                # 创建Task对象并存储在内存中
                task = Task(
                    id=task_id,
                    url=url,
                    output_path=output_path,
                    format=format,
                    status=status,
                    result=result,
                    error=error,
                    progress=progress
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
            progress_json = json.dumps(task.progress) if task.progress else None
            
            # 使用REPLACE策略插入/更新任务
            cursor.execute('''
            INSERT OR REPLACE INTO tasks (id, url, output_path, format, status, result, progress, error, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task.id,
                task.url,
                task.output_path,
                task.format,
                task.status,
                result_json,
                progress_json,
                task.error,
                timestamp
            ))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error saving task to database: {e}")
    
    def add_task(self, url: str, output_path: str, format: str) -> str:
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
        return self.tasks.get(task_id)
    
    def update_task(self, task_id: str, status: str, result: Optional[Dict[str, Any]] = None, error: Optional[str] = None, progress: Optional[Dict[str, Any]] = None) -> None:
        if task_id in self.tasks:
            task = self.tasks[task_id]
            terminal_statuses = {"completed", "failed", "canceled"}
            if task.status in terminal_statuses and status not in terminal_statuses:
                return
            if task.status == "canceling" and status == "downloading":
                return
            task.status = status
            if result is not None:
                task.result = result
            if error is not None:
                task.error = error
            if progress is not None:
                task.progress = progress
            
            # 将更新后的任务状态保存到数据库
            self._save_task(task)

    def request_cancel(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task:
            return False
        if task.status in ("completed", "failed", "canceled"):
            return False
        self.cancel_requested.add(task_id)
        return True

    def is_cancel_requested(self, task_id: str) -> bool:
        return task_id in self.cancel_requested

    def clear_cancel(self, task_id: str) -> None:
        self.cancel_requested.discard(task_id)

    def delete_task(self, task_id: str) -> bool:
        task = self.tasks.pop(task_id, None)
        if not task:
            return False
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error deleting task from database: {e}")
        return True

    def restart_task(self, task_id: str) -> Optional[Task]:
        task = self.tasks.get(task_id)
        if not task:
            return None
        task.status = "pending"
        task.result = None
        task.error = None
        task.progress = None
        self.clear_cancel(task_id)
        self._save_task(task)
        return task
    
    def list_tasks(self) -> List[Task]:
        return list(self.tasks.values())

# 创建全局状态对象
state = State()

def download_video(url: str, output_path: str = "./downloads", format: str = "best", quiet: bool = False, progress_hook: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """
    Download a video from the specified URL using yt-dlp.
    
    Args:
        url (str): The URL of the video to download
        output_path (str): Directory where the video will be saved
        format (str): Video format to download (e.g., "best", "bestvideo+bestaudio", "mp4")
        quiet (bool): If True, suppress output
        
    Returns:
        Dict[str, Any]: Information about the downloaded video
    """
    # Create output directory if it doesn't exist
    os.makedirs(output_path, exist_ok=True)
    
    # Configure yt-dlp options
    # 使用自定义函数生成安全的文件名模板
    def get_safe_outtmpl(info_dict):
        """为每个视频生成安全的输出文件名"""
        title = info_dict.get('title', 'video')
        ext = info_dict.get('ext', 'mp4')
        safe_filename = create_safe_filename(title, format, ext)
        return os.path.join(output_path, safe_filename)
    
    progress_hooks = []
    if progress_hook:
        progress_hooks.append(progress_hook)
    
    ydl_opts = {
        'outtmpl': os.path.join(output_path, '%(title).180s.%(ext)s'),
        'quiet': quiet,
        'no_warnings': quiet,
        'format': format,
        'no_abort_on_error': True,
        # 添加进度钩子来处理文件名
        'progress_hooks': progress_hooks,
    }
    
    # 如果需要更安全的处理，我们可以在下载前先获取信息
    temp_ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    
    try:
        # 先获取视频信息来生成安全的文件名
        with yt_dlp.YoutubeDL(temp_ydl_opts) as temp_ydl:
            info = temp_ydl.extract_info(url, download=False)
            if info:
                title = info.get('title', 'video')
                ext = info.get('ext', 'mp4')
                safe_filename = create_safe_filename(title, format, ext)
                ydl_opts['outtmpl'] = os.path.join(output_path, safe_filename)
    except Exception:
        # 如果获取信息失败，使用默认的安全模板
        pass
    
    # Download the video
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.sanitize_info(info)

def get_video_info(url: str, quiet: bool = False) -> Dict[str, Any]:
    """
    Get information about a video without downloading it.
    
    Args:
        url (str): The URL of the video
        quiet (bool): If True, suppress output
        
    Returns:
        Dict[str, Any]: Information about the video
    """
    ydl_opts = {
        'quiet': quiet,
        'no_warnings': quiet,
        'skip_download': True,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return ydl.sanitize_info(info)

def list_available_formats(url: str) -> List[Dict[str, Any]]:
    """
    List all available formats for a video.
    
    Args:
        url (str): The URL of the video
        
    Returns:
        List[Dict[str, Any]]: List of available formats
    """
    info = get_video_info(url)
    if not info:
        return []
    
    return info.get('formats', [])

def build_progress_payload(progress_data: Dict[str, Any]) -> Dict[str, Any]:
    total = progress_data.get("total_bytes") or progress_data.get("total_bytes_estimate")
    downloaded = progress_data.get("downloaded_bytes")
    percent = None
    if total and downloaded is not None:
        percent = round(downloaded / total * 100, 2)
    
    return {
        "status": progress_data.get("status"),
        "downloaded_bytes": downloaded,
        "total_bytes": progress_data.get("total_bytes"),
        "total_bytes_estimate": progress_data.get("total_bytes_estimate"),
        "speed": progress_data.get("speed"),
        "eta": progress_data.get("eta"),
        "elapsed": progress_data.get("elapsed"),
        "percent": percent,
        "filename": progress_data.get("filename"),
    }

def is_within_directory(path: str, base_dir: str) -> bool:
    try:
        return os.path.commonpath([path, base_dir]) == base_dir
    except ValueError:
        return False

def normalize_candidate_paths(paths: List[Optional[str]], output_path: str) -> Set[str]:
    output_dir = os.path.abspath(output_path)
    candidates: Set[str] = set()
    for path in paths:
        if not path:
            continue
        abs_path = os.path.abspath(path)
        if is_within_directory(abs_path, output_dir):
            candidates.add(abs_path)
            continue
        alt_path = os.path.abspath(os.path.join(output_dir, path))
        if is_within_directory(alt_path, output_dir):
            candidates.add(alt_path)
    return candidates

def expand_cache_paths(base_path: str) -> Set[str]:
    patterns = [
        base_path,
        f"{base_path}.part",
        f"{base_path}.ytdl",
        f"{base_path}.part.ytdl",
        f"{base_path}-Frag*",
        f"{base_path}.part-Frag*",
        f"{base_path}.part-Frag*.part",
    ]
    expanded: Set[str] = set()
    for pattern in patterns:
        if any(ch in pattern for ch in "*?[]"):
            expanded.update(glob.glob(pattern))
        else:
            expanded.add(pattern)
    return expanded

def delete_task_files(task: Task) -> int:
    raw_paths: List[Optional[str]] = []
    if task.result:
        for key in ("filepath", "_filename", "filename", "requested_filename"):
            raw_paths.append(task.result.get(key))
        for entry in task.result.get("requested_downloads") or []:
            raw_paths.append(entry.get("filepath") or entry.get("filename"))
    if task.progress:
        raw_paths.append(task.progress.get("filename"))

    output_dir = os.path.abspath(task.output_path)
    candidates = normalize_candidate_paths(raw_paths, task.output_path)
    file_paths: Set[str] = set()
    for base_path in candidates:
        for path in expand_cache_paths(base_path):
            abs_path = os.path.abspath(path)
            if is_within_directory(abs_path, output_dir):
                file_paths.add(abs_path)

    deleted = 0
    for path in sorted(file_paths, key=len, reverse=True):
        try:
            if os.path.isfile(path):
                os.remove(path)
                deleted += 1
        except Exception as e:
            print(f"Error deleting file {path}: {e}")
    return deleted

def require_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None),
) -> None:
    api_key = os.getenv("YTDLP_API_KEY")
    if not api_key:
        return
    if x_api_key == api_key:
        return
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        if token == api_key:
            return
    raise HTTPException(status_code=401, detail="Invalid API key")

app = FastAPI(
    title="yt-dlp API",
    description="API for downloading videos using yt-dlp",
    dependencies=[Depends(require_api_key)],
)

class DownloadRequest(BaseModel):
    url: str
    output_path: str = "./downloads"
    format: str = "bestvideo+bestaudio/best"
    quiet: bool = False

async def process_download_task(task_id: str, url: str, output_path: str, format: str, quiet: bool):
    """Asynchronously process download task"""
    try:
        if state.is_cancel_requested(task_id):
            state.update_task(task_id, "canceled", error="Canceled by user")
            return

        def progress_hook(progress_data: Dict[str, Any]) -> None:
            status = progress_data.get("status")
            if status not in ("downloading", "finished"):
                return
            if state.is_cancel_requested(task_id):
                raise DownloadCancelled("Canceled by user")
            progress = build_progress_payload(progress_data)
            state.update_task(task_id, "downloading", progress=progress)

        state.update_task(task_id, "downloading")
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            result = await loop.run_in_executor(
                executor,
                lambda: download_video(
                    url=url,
                    output_path=output_path,
                    format=format,
                    quiet=quiet,
                    progress_hook=progress_hook,
                )
            )
        state.update_task(task_id, "completed", result=result)
    except DownloadCancelled as e:
        state.update_task(task_id, "canceled", error=str(e))
    except Exception as e:
        state.update_task(task_id, "failed", error=str(e))
    finally:
        state.clear_cancel(task_id)

@app.post("/download", response_class=JSONResponse)
async def api_download_video(request: DownloadRequest):
    """
    Submit a video download task and return a task ID to track progress.
    """
    # 如果有相同的url和output_path的任务已经存在，直接返回该任务
    existing_task = next((task for task in state.tasks.values() if task.format == request.format and task.url == request.url and task.output_path == request.output_path), None)
    if existing_task:
        return {"status": "success", "task_id": existing_task.id}
    task_id = state.add_task(request.url, request.output_path, request.format)
    
    # Asynchronously execute download task
    asyncio.create_task(process_download_task(
        task_id=task_id,
        url=request.url,
        output_path=request.output_path,
        format=request.format,
        quiet=request.quiet
    ))
    
    return {"status": "success", "task_id": task_id}

@app.get("/task/{task_id}", response_class=JSONResponse)
async def get_task_status(task_id: str):
    """
    Get the status of a specific download task.
    """
    task = state.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
    
    response = {
        "status": "success",
        "data": {
            "id": task.id,
            "url": task.url,
            "status": task.status,
            "progress": task.progress
        }
    }
    
    if task.status == "completed" and task.result:
        response["data"]["result"] = task.result
    elif task.status in ("failed", "canceled") and task.error:
        response["data"]["error"] = task.error
    
    return response

@app.post("/task/{task_id}/stop", response_class=JSONResponse)
async def stop_task(task_id: str):
    """
    Request to stop a running download task.
    """
    task = state.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")

    if not state.request_cancel(task_id):
        return {"status": "success", "data": {"id": task.id, "status": task.status}}

    state.update_task(task_id, "canceling")
    return {"status": "success", "data": {"id": task.id, "status": "canceling"}}

@app.post("/task/{task_id}/restart", response_class=JSONResponse)
async def restart_task(task_id: str, quiet: bool = False):
    """
    Restart a finished/failed/canceled download task.
    """
    task = state.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
    if task.status in ("pending", "downloading", "canceling"):
        raise HTTPException(status_code=400, detail=f"Task is running. Stop it before restarting. Current status: {task.status}")

    restarted = state.restart_task(task_id)
    if not restarted:
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")

    asyncio.create_task(process_download_task(
        task_id=task_id,
        url=restarted.url,
        output_path=restarted.output_path,
        format=restarted.format,
        quiet=quiet
    ))

    return {"status": "success", "data": {"id": task.id, "status": "pending"}}

@app.delete("/task/{task_id}", response_class=JSONResponse)
async def delete_task(task_id: str):
    """
    Delete a task record. If task is running, request cancellation.
    """
    task = state.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")

    cancel_requested = False
    if task.status in ("pending", "downloading", "canceling"):
        cancel_requested = state.request_cancel(task_id)

    deleted_files = delete_task_files(task)
    if not state.delete_task(task_id):
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")

    return {
        "status": "success",
        "data": {
            "id": task.id,
            "cancel_requested": cancel_requested,
            "deleted_files": deleted_files,
        },
    }

@app.get("/tasks", response_class=JSONResponse)
async def list_all_tasks():
    """
    List all download tasks and their status.
    """
    tasks = state.list_tasks()
    return {"status": "success", "data": tasks}

@app.get("/info", response_class=JSONResponse)
async def api_get_video_info(url: str = Query(..., description="The URL of the video")):
    """
    Get information about a video without downloading it.
    """
    try:
        result = get_video_info(url)
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/formats", response_class=JSONResponse)
async def api_list_formats(url: str = Query(..., description="The URL of the video")):
    """
    List all available formats for a video.
    """
    try:
        result = list_available_formats(url)
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/{task_id}/file", response_class=FileResponse)
async def download_completed_video(task_id: str):
    """
    返回已完成下载任务的视频文件。
    如果任务未完成或未找到，将返回相应的错误。
    """
    task = state.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
    
    if task.status != "completed":
        raise HTTPException(status_code=400, detail=f"Task is not completed yet. Current status: {task.status}")
    
    if not task.result:
        raise HTTPException(status_code=500, detail="Task completed but no result information available")
    
    # 获取文件路径
    try:
        # 从结果中提取文件名和路径 tast.result.requested_downloads[0].filename
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
        
        # 检查文件是否存在
        if not os.path.exists(filename):
            raise HTTPException(status_code=404, detail="Video file not found on server")
        
        # 提取实际文件名用于Content-Disposition头
        file_basename = os.path.basename(filename)
        
        # 返回文件
        return FileResponse(
            path=filename,
            filename=file_basename,
            media_type="application/octet-stream"
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error accessing video file: {str(e)}")

def start_api():
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    print("Starting yt-dlp API server...")
    start_api()
