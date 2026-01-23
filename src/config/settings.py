"""配置管理模块"""
import os
from typing import Dict, Optional
from dotenv import load_dotenv

# 读取 .env 配置（如果存在）
load_dotenv()


def get_cookie_cloud_config() -> Dict[str, Optional[str]]:
    """获取 CookieCloud 配置"""
    return {
        "server": os.getenv("COOKIE_CLOUD_SERVER"),
        "password": os.getenv("COOKIE_CLOUD_PASSWORD"),
        "uuid": os.getenv("COOKIE_CLOUD_UUID"),
    }


def get_s3_config() -> Dict[str, Optional[str]]:
    """获取S3配置"""
    return {
        "endpoint_url": os.getenv("S3_ENDPOINT_URL"),  # 可选，用于兼容S3的服务（如MinIO）
        "access_key": os.getenv("S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID"),
        "secret_key": os.getenv("S3_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY"),
        "bucket": os.getenv("S3_BUCKET"),
        "region": os.getenv("S3_REGION") or os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    }


def is_s3_configured() -> bool:
    """检查S3是否已配置"""
    cfg = get_s3_config()
    return bool(cfg.get("access_key") and cfg.get("secret_key") and cfg.get("bucket"))


def get_domain() -> Optional[str]:
    """获取服务域名配置"""
    return os.getenv("DOMAIN")
