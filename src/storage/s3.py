"""S3 存储服务模块"""
import os
import logging
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from src.config import get_s3_config, is_s3_configured

_logger = logging.getLogger("yt_dlp_api")


def get_s3_client():
    """获取S3客户端"""
    cfg = get_s3_config()
    
    client_kwargs = {
        "aws_access_key_id": cfg.get("access_key"),
        "aws_secret_access_key": cfg.get("secret_key"),
        "region_name": cfg.get("region"),
    }
    
    # 如果配置了自定义endpoint（如MinIO），则添加
    if cfg.get("endpoint_url"):
        client_kwargs["endpoint_url"] = cfg.get("endpoint_url")
    
    return boto3.client("s3", **client_kwargs)


def upload_file_to_s3(file_path: str, task_id: str) -> Optional[str]:
    """
    上传文件到S3
    
    Args:
        file_path (str): 本地文件路径
        task_id (str): 任务ID
        
    Returns:
        Optional[str]: S3 key路径，如果上传失败返回None
    """
    if not is_s3_configured():
        _logger.warning("[S3] S3 is not configured, skipping upload")
        return None
    
    if not os.path.exists(file_path):
        _logger.error(f"[S3] File not found: {file_path}")
        return None
    
    cfg = get_s3_config()
    bucket = cfg.get("bucket")
    
    # 构建S3 key: yt-dlp/task_id/文件名
    filename = os.path.basename(file_path)
    s3_key = f"yt-dlp/{task_id}/{filename}"
    
    try:
        s3_client = get_s3_client()
        
        _logger.info(f"[S3] Uploading {file_path} to s3://{bucket}/{s3_key}")
        
        # 上传文件
        s3_client.upload_file(file_path, bucket, s3_key)
        
        _logger.info(f"[S3] File uploaded successfully: s3://{bucket}/{s3_key}")
        # 返回S3 key，而不是完整URL（因为需要预签名才能访问）
        return s3_key
        
    except ClientError as e:
        _logger.error(f"[S3] Failed to upload file: {e}")
        return None
    except Exception as e:
        _logger.error(f"[S3] Unexpected error during upload: {e}")
        return None


def generate_presigned_url(s3_key: str, expiration: int = 3600) -> Optional[str]:
    """
    生成S3预签名URL
    
    Args:
        s3_key (str): S3对象的key
        expiration (int): URL过期时间（秒），默认1小时
        
    Returns:
        Optional[str]: 预签名URL，如果生成失败返回None
    """
    if not is_s3_configured():
        return None
    
    cfg = get_s3_config()
    bucket = cfg.get("bucket")
    print(f"key to generate presigned url: {s3_key} in bucket: {bucket}")
    
    try:
        s3_client = get_s3_client()
        
        # 从S3 key中提取纯文件名，避免下载时带有S3目录路径
        filename = os.path.basename(s3_key)
        
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': bucket,
                'Key': s3_key,
                'ResponseContentDisposition': f'attachment; filename="{filename}"'
            },
            ExpiresIn=expiration
        )
        
        _logger.debug(f"[S3] Generated presigned URL for {s3_key}")
        return presigned_url
        
    except ClientError as e:
        _logger.error(f"[S3] Failed to generate presigned URL: {e}")
        return None
    except Exception as e:
        _logger.error(f"[S3] Unexpected error generating presigned URL: {e}")
        return None


def delete_s3_file(s3_key: str) -> bool:
    """
    删除S3上的文件
    
    Args:
        s3_key (str): S3对象的key
        
    Returns:
        bool: 是否删除成功
    """
    if not is_s3_configured():
        return False
    
    if not s3_key:
        return False
    
    cfg = get_s3_config()
    bucket = cfg.get("bucket")
    
    try:
        s3_client = get_s3_client()
        
        _logger.info(f"[S3] Deleting s3://{bucket}/{s3_key}")
        s3_client.delete_object(Bucket=bucket, Key=s3_key)
        _logger.info(f"[S3] File deleted successfully: s3://{bucket}/{s3_key}")
        return True
        
    except ClientError as e:
        _logger.error(f"[S3] Failed to delete file: {e}")
        return False
    except Exception as e:
        _logger.error(f"[S3] Unexpected error deleting file: {e}")
        return False
