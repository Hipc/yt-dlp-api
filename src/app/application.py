"""FastAPI 应用配置"""
import logging

import uvicorn
from fastapi import FastAPI

from .middleware import URLDecodeMiddleware
from src.routes import (
    download_router,
    tasks_router,
    info_router,
    admin_router,
)

_logger = logging.getLogger("yt_dlp_api")


def _setup_logger():
    """配置应用日志"""
    # 确保应用日志可见（无论是否通过 uvicorn CLI 启动）
    if not _logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)  # 必须同时设置 handler 的级别
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        _logger.addHandler(handler)
    _logger.setLevel(logging.DEBUG)
    _logger.propagate = False  # 防止日志被根 logger 重复处理或过滤
    _logger.info("Logger initialized with DEBUG level")


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用"""
    # 配置日志
    _setup_logger()
    
    # 创建应用
    app = FastAPI(
        title="yt-dlp API", 
        description="API for downloading videos using yt-dlp"
    )
    
    # 添加中间件
    app.add_middleware(URLDecodeMiddleware)
    
    # 注册路由
    app.include_router(admin_router)
    app.include_router(download_router)
    app.include_router(tasks_router)
    app.include_router(info_router)
    
    return app


def start_api(host: str = "0.0.0.0", port: int = 8000):
    """启动 API 服务"""
    app = create_app()
    uvicorn.run(app, host=host, port=port)
