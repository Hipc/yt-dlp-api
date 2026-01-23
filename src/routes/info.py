"""视频信息路由"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from src.services import get_video_info, list_available_formats

router = APIRouter()


@router.get("/info", response_class=JSONResponse)
async def api_get_video_info(url: str = Query(..., description="The URL of the video")):
    """
    Get information about a video without downloading it.
    """
    try:
        result = get_video_info(url)
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/formats", response_class=JSONResponse)
async def api_list_formats(url: str = Query(..., description="The URL of the video")):
    """
    List all available formats for a video.
    """
    try:
        result = list_available_formats(url)
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
