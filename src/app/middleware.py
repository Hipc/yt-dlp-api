"""URL解码中间件"""
import urllib.parse
import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

_logger = logging.getLogger("yt_dlp_api")


class URLDecodeMiddleware(BaseHTTPMiddleware):
    """URL 解码中间件 - 处理被编码的请求路径和代理请求"""
    
    async def dispatch(self, request: Request, call_next):
        # 获取原始路径和 raw_path
        original_path = request.scope.get('path', '')
        raw_path = request.scope.get('raw_path', b'').decode('utf-8', errors='ignore')
        
        _logger.info(f"[Middleware] Original path: {original_path}")
        _logger.info(f"[Middleware] Raw path: {raw_path}")
        print(f"[Middleware] Original path: {original_path}", flush=True)
        print(f"[Middleware] Raw path: {raw_path}", flush=True)
        
        # 优先检查 raw_path（包含原始未解码的路径）
        path_to_check = raw_path if raw_path else original_path
        
        # 解码路径
        decoded_path = urllib.parse.unquote(path_to_check)
        _logger.info(f"[Middleware] Decoded path: {decoded_path}")
        print(f"[Middleware] Decoded path: {decoded_path}", flush=True)
        
        # 检查是否是代理请求（路径以 http:// 或 https:// 开头）
        if decoded_path.startswith("http://") or decoded_path.startswith("https://"):
            parsed = urllib.parse.urlparse(decoded_path)
            new_path = parsed.path if parsed.path else "/"
            _logger.info(f"[Middleware] Proxy request detected, extracting path: {new_path}")
            print(f"[Middleware] Proxy request detected, extracting path: {new_path}", flush=True)
            request.scope["path"] = new_path
        else:
            request.scope['path'] = decoded_path
        
        return await call_next(request)
