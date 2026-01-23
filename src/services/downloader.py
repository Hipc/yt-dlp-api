"""视频下载服务模块"""
import os
import logging
from typing import Dict, Any, List

import yt_dlp

from src.utils import create_safe_filename
from src.cookies import apply_cookie_options, cleanup_cookie_file

_logger = logging.getLogger("yt_dlp_api")


def download_video(
    url: str, 
    output_path: str = "./downloads", 
    format: str = "best", 
    quiet: bool = False
) -> Dict[str, Any]:
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
    ydl_opts = {
        'outtmpl': os.path.join(output_path, '%(title).180s.%(ext)s'),
        'quiet': quiet,
        'no_warnings': quiet,
        'format': format,
        'no_abort_on_error': True,
        'progress_hooks': [],
    }
    
    # 如果需要更安全的处理，我们可以在下载前先获取信息
    temp_ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }

    # 应用 cookie 设置
    ydl_opts, cookie_file_path = apply_cookie_options(ydl_opts, url, "[Download]")
    
    # temp_ydl_opts 复用相同的 cookie 文件（如果有）
    if cookie_file_path:
        temp_ydl_opts['cookiefile'] = cookie_file_path
    if 'http_headers' in ydl_opts:
        temp_ydl_opts['http_headers'] = ydl_opts['http_headers']
    
    _logger.debug(f"[Download] ydl_opts: {ydl_opts}")
    
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
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.sanitize_info(info)
    finally:
        # 清理临时 cookie 文件
        cleanup_cookie_file(cookie_file_path, "[Download]")


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
    
    # 应用 cookie 设置
    ydl_opts, cookie_file_path = apply_cookie_options(ydl_opts, url, "[VideoInfo]")
    
    _logger.debug(f"[VideoInfo] ydl_opts: {ydl_opts}")
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return ydl.sanitize_info(info)
    finally:
        # 清理临时 cookie 文件
        cleanup_cookie_file(cookie_file_path, "[VideoInfo]")


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
