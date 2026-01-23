"""下载任务路由"""
import os
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.state import state
from src.config import is_s3_configured
from src.storage import upload_file_to_s3
from src.services import download_video
from .schemas import DownloadRequest

router = APIRouter()
_logger = logging.getLogger("yt_dlp_api")


async def process_download_task(
    task_id: str, 
    url: str, 
    output_path: str, 
    format: str, 
    quiet: bool
):
    """Asynchronously process download task"""
    try:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            result = await loop.run_in_executor(
                executor,
                lambda: download_video(
                    url=url,
                    output_path=output_path,
                    format=format,
                    quiet=quiet,
                )
            )
            
            # 获取下载的文件路径
            filename = result.get("requested_downloads", [{}])[0].get("filename")
            if not filename:
                filename = result.get("requested_filename")
            if not filename:
                title = result.get("title", "video")
                ext = result.get("ext", "mp4")
                filename = os.path.join(output_path, f"{title}.{ext}")
            
            # 检查是否配置了S3
            if is_s3_configured():
                # 更新状态为 uploading
                state.update_task(task_id, "uploading", result=result)
                
                s3_url = None
                try:
                    if filename and os.path.exists(filename):
                        s3_url = await loop.run_in_executor(
                            executor,
                            lambda: upload_file_to_s3(filename, task_id)
                        )
                        
                        # 上传成功后删除本地文件
                        if s3_url:
                            try:
                                os.remove(filename)
                                _logger.info(f"[S3] Local file deleted after upload: {filename}")
                            except Exception as e:
                                _logger.warning(f"[S3] Failed to delete local file {filename}: {e}")
                except Exception as e:
                    _logger.error(f"[S3] Error uploading file for task {task_id}: {e}")
                
                # 上传完成后变成 completed 状态
                state.update_task(task_id, "completed", result=result, s3_url=s3_url)
            else:
                # S3未配置，直接变成 completed 状态
                state.update_task(task_id, "completed", result=result)
    except Exception as e:
        state.update_task(task_id, "failed", error=str(e))


@router.post("/download", response_class=JSONResponse)
async def api_download_video(request: DownloadRequest):
    """
    Submit a video download task and return a task ID to track progress.
    """
    # 如果有相同的url和output_path的任务已经存在，检查状态
    existing_task = next(
        (task for task in state.tasks.values() 
         if task.format == request.format 
         and task.url == request.url 
         and task.output_path == request.output_path), 
        None
    )
    if existing_task:
        # 如果任务状态为失败，重置状态并重新尝试下载
        if existing_task.status == "failed":
            state.update_task(existing_task.id, "pending", result=None, error=None, clear_fields=True)
            # 重新执行下载任务
            asyncio.create_task(process_download_task(
                task_id=existing_task.id,
                url=request.url,
                output_path=request.output_path,
                format=request.format,
                quiet=request.quiet
            ))
            return {"status": "success", "task_id": existing_task.id, "message": "Task restarted"}
        # 非失败状态直接返回该任务
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
