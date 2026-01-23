"""任务管理路由"""
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse

from src.state import state
from src.storage import generate_presigned_url
from src.config.settings import get_domain

router = APIRouter()


@router.delete("/task/{task_id}", response_class=JSONResponse)
async def delete_task(task_id: str):
    """
    删除指定的下载任务及其对应的文件。
    """
    success, deleted_file, error = state.delete_task(task_id)
    
    if not success:
        raise HTTPException(status_code=404, detail=error or f"Task with ID {task_id} not found")
    
    response = {
        "status": "success",
        "message": f"Task {task_id} deleted successfully"
    }
    
    if deleted_file:
        response["deleted_file"] = deleted_file
    
    return response


@router.get("/task/{task_id}", response_class=JSONResponse)
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
            "status": task.status
        }
    }
    
    if task.status == "completed" and task.result:
        response["data"]["result"] = task.result
        if task.s3_url:
            response["data"]["s3_url"] = task.s3_url
    elif task.status == "failed" and task.error:
        response["data"]["error"] = task.error
    
    return response


@router.get("/tasks", response_class=JSONResponse)
async def list_all_tasks():
    """
    List all download tasks and their status.
    """
    tasks = state.list_tasks()
    return {"status": "success", "data": tasks}


@router.get("/download/{task_id}/file_url", response_class=JSONResponse)
async def get_download_url(task_id: str):
    """
    获取已完成下载任务的视频文件下载URL。
    如果文件保存在S3，返回临时预签名URL。
    如果文件保存在本地，返回本地下载地址。
    """
    task = state.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
    
    if task.status != "completed":
        raise HTTPException(status_code=400, detail=f"Task is not completed yet. Current status: {task.status}")
    
    if not task.result:
        raise HTTPException(status_code=500, detail="Task completed but no result information available")
    
    # 如果有S3 key，生成预签名URL
    if task.s3_url:
        presigned_url = generate_presigned_url(task.s3_url, expiration=3600)  # 1小时有效期
        if presigned_url:
            return {
                "status": "success",
                "data": {
                    "url": presigned_url,
                    "type": "s3",
                    "expires_in": 3600
                }
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to generate download URL for S3 file")
    
    # 本地文件，返回 DOMAIN + /download/{task_id}/file
    domain = get_domain()
    if not domain:
        raise HTTPException(status_code=500, detail="DOMAIN not configured in environment")
    
    # 确保 domain 末尾没有斜杠
    domain = domain.rstrip("/")
    local_url = f"{domain}/download/{task_id}/file"
    
    return {
        "status": "success",
        "data": {
            "url": local_url,
            "type": "local"
        }
    }


@router.get("/download/{task_id}/file")
async def download_completed_video(task_id: str):
    """
    返回已完成下载任务的视频文件。
    如果文件已上传到S3，则生成预签名URL并重定向。
    如果任务未完成或未找到，将返回相应的错误。
    """
    task = state.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
    
    if task.status != "completed":
        raise HTTPException(status_code=400, detail=f"Task is not completed yet. Current status: {task.status}")
    
    if not task.result:
        raise HTTPException(status_code=500, detail="Task completed but no result information available")
    
    # 如果有S3 key，生成预签名URL并重定向
    if task.s3_url:
        presigned_url = generate_presigned_url(task.s3_url, expiration=3600)  # 1小时有效期
        if presigned_url:
            return RedirectResponse(url=presigned_url, status_code=302)
        else:
            raise HTTPException(status_code=500, detail="Failed to generate download URL for S3 file")
    
    # 否则返回本地文件
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
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error accessing video file: {str(e)}")
