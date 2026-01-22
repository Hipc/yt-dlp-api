import yt_dlp
import os
import uuid
import urllib.parse
import urllib.request
import logging
import tempfile
import time

import asyncio
import boto3
from botocore.exceptions import ClientError

import json
import datetime
import sqlite3
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# 读取 .env 配置（如果存在）
load_dotenv()

_app_logger = logging.getLogger("yt_dlp_api")

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
    s3_url: Optional[str] = None

class State:
    def __init__(self):
        self.tasks: Dict[str, Task] = {}
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
            error TEXT,
            s3_url TEXT,
            timestamp TEXT NOT NULL
        )
        ''')
        
        # 检查并添加s3_url列（用于数据库迁移）
        try:
            cursor.execute("ALTER TABLE tasks ADD COLUMN s3_url TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            # 列已存在，忽略错误
            pass
        
        conn.commit()
        conn.close()
    
    def _load_tasks(self) -> None:
        """从数据库加载任务状态"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            cursor.execute("SELECT id, url, output_path, format, status, result, error, s3_url FROM tasks")
            rows = cursor.fetchall()
            
            for row in rows:
                task_id, url, output_path, format, status, result_json, error, s3_url = row
                
                # 解析JSON结果（如果有）
                result = json.loads(result_json) if result_json else None
                
                # 创建Task对象并存储在内存中
                task = Task(
                    id=task_id,
                    url=url,
                    output_path=output_path,
                    format=format,
                    status=status,
                    result=result,
                    error=error,
                    s3_url=s3_url
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
            
            # 使用REPLACE策略插入/更新任务
            cursor.execute('''
            INSERT OR REPLACE INTO tasks (id, url, output_path, format, status, result, error, s3_url, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task.id,
                task.url,
                task.output_path,
                task.format,
                task.status,
                result_json,
                task.error,
                task.s3_url,
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
    
    def update_task(self, task_id: str, status: str, result: Optional[Dict[str, Any]] = None, error: Optional[str] = None, s3_url: Optional[str] = None, clear_fields: bool = False) -> None:
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.status = status
            if result is not None or clear_fields:
                task.result = result
            if error is not None or clear_fields:
                task.error = error
            if s3_url is not None or clear_fields:
                task.s3_url = s3_url
            
            # 将更新后的任务状态保存到数据库
            self._save_task(task)
    
    def list_tasks(self) -> List[Task]:
        return list(self.tasks.values())
    
    def delete_task(self, task_id: str) -> tuple[bool, Optional[str], Optional[str]]:
        """
        删除任务及其对应的文件
        
        Args:
            task_id: 任务ID
            
        Returns:
            tuple: (是否成功, 删除的文件路径, 错误信息)
        """
        task = self.tasks.get(task_id)
        if not task:
            return False, None, "Task not found"
        
        deleted_file = None
        deleted_s3 = False
        
        # 如果有S3文件，尝试删除
        if task.s3_url:
            try:
                deleted_s3 = delete_s3_file(task.s3_url)
                if deleted_s3:
                    _app_logger.info(f"[S3] Deleted S3 file for task {task_id}: {task.s3_url}")
            except Exception as e:
                _app_logger.error(f"[S3] Error deleting S3 file for task {task_id}: {e}")
        
        # 如果任务已完成且本地文件存在，尝试删除对应的本地文件
        if task.status == "completed" and task.result:
            try:
                # 从结果中提取文件名
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
                
                # 删除本地文件
                if filename and os.path.exists(filename):
                    os.remove(filename)
                    deleted_file = filename
            except Exception as e:
                print(f"Error deleting file for task {task_id}: {e}")
        
        # 从内存中删除
        if task_id in self.tasks:
            del self.tasks[task_id]
        
        # 从数据库中删除
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            # 执行VACUUM整理数据库，回收空间防止数据库膨胀
            cursor.execute("VACUUM")
            conn.close()
        except Exception as e:
            return False, deleted_file, f"Error deleting from database: {e}"
        
        return True, deleted_file, None

# 创建全局状态对象
state = State()

def _get_cookie_cloud_config() -> Dict[str, Optional[str]]:
    return {
        "server": os.getenv("COOKIE_CLOUD_SERVER"),
        "password": os.getenv("COOKIE_CLOUD_PASSWORD"),
        "uuid": os.getenv("COOKIE_CLOUD_UUID"),
    }


def _get_s3_config() -> Dict[str, Optional[str]]:
    """获取S3配置"""
    return {
        "endpoint_url": os.getenv("S3_ENDPOINT_URL"),  # 可选，用于兼容S3的服务（如MinIO）
        "access_key": os.getenv("S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID"),
        "secret_key": os.getenv("S3_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY"),
        "bucket": os.getenv("S3_BUCKET"),
        "region": os.getenv("S3_REGION") or os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    }


def _is_s3_configured() -> bool:
    """检查S3是否已配置"""
    cfg = _get_s3_config()
    return bool(cfg.get("access_key") and cfg.get("secret_key") and cfg.get("bucket"))


def _get_s3_client():
    """获取S3客户端"""
    cfg = _get_s3_config()
    
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
    if not _is_s3_configured():
        _app_logger.warning("[S3] S3 is not configured, skipping upload")
        return None
    
    if not os.path.exists(file_path):
        _app_logger.error(f"[S3] File not found: {file_path}")
        return None
    
    cfg = _get_s3_config()
    bucket = cfg.get("bucket")
    
    # 构建S3 key: yt-dlp/task_id/文件名
    filename = os.path.basename(file_path)
    s3_key = f"yt-dlp/{task_id}/{filename}"
    
    try:
        s3_client = _get_s3_client()
        
        _app_logger.info(f"[S3] Uploading {file_path} to s3://{bucket}/{s3_key}")
        
        # 上传文件
        s3_client.upload_file(file_path, bucket, s3_key)
        
        _app_logger.info(f"[S3] File uploaded successfully: s3://{bucket}/{s3_key}")
        # 返回S3 key，而不是完整URL（因为需要预签名才能访问）
        return s3_key
        
    except ClientError as e:
        _app_logger.error(f"[S3] Failed to upload file: {e}")
        return None
    except Exception as e:
        _app_logger.error(f"[S3] Unexpected error during upload: {e}")
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
    if not _is_s3_configured():
        return None
    
    cfg = _get_s3_config()
    bucket = cfg.get("bucket")
    print(f"key to generate presigned url: {s3_key} in bucket: {bucket}")
    
    try:
        s3_client = _get_s3_client()
        
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': s3_key},
            ExpiresIn=expiration
        )
        
        _app_logger.debug(f"[S3] Generated presigned URL for {s3_key}")
        return presigned_url
        
    except ClientError as e:
        _app_logger.error(f"[S3] Failed to generate presigned URL: {e}")
        return None
    except Exception as e:
        _app_logger.error(f"[S3] Unexpected error generating presigned URL: {e}")
        return None


def delete_s3_file(s3_key: str) -> bool:
    """
    删除S3上的文件
    
    Args:
        s3_key (str): S3对象的key
        
    Returns:
        bool: 是否删除成功
    """
    if not _is_s3_configured():
        return False
    
    if not s3_key:
        return False
    
    cfg = _get_s3_config()
    bucket = cfg.get("bucket")
    
    try:
        s3_client = _get_s3_client()
        
        _app_logger.info(f"[S3] Deleting s3://{bucket}/{s3_key}")
        s3_client.delete_object(Bucket=bucket, Key=s3_key)
        _app_logger.info(f"[S3] File deleted successfully: s3://{bucket}/{s3_key}")
        return True
        
    except ClientError as e:
        _app_logger.error(f"[S3] Failed to delete file: {e}")
        return False
    except Exception as e:
        _app_logger.error(f"[S3] Unexpected error deleting file: {e}")
        return False

def _is_bilibili_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        return "bilibili.com" in host or host.endswith(".bilibili.com") or host == "b23.tv"
    except Exception:
        return "bilibili.com" in url or "b23.tv" in url

def _build_cookiecloud_url(server: str, uuid_val: str) -> str:
    server = server.strip()
    if not server:
        return ""
    if "//" not in server:
        server = "http://" + server
    server = server.rstrip("/")
    return server + "/get/" + uuid_val
    

def _cookies_list_to_header(cookies: List[Dict[str, Any]], domain_keyword: str) -> Optional[str]:
    parts: List[str] = []
    for item in cookies:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain", ""))
        if domain_keyword not in domain:
            continue
        name = item.get("name")
        value = item.get("value")
        if not name or value is None:
            continue
        parts.append(f"{name}={value}")
    return "; ".join(parts) if parts else None

def _extract_bilibili_cookie_header(cookie_data: Any) -> Optional[str]:
    if isinstance(cookie_data, dict):
        for key, val in cookie_data.items():
            if "bilibili.com" in str(key):
                if isinstance(val, str):
                    return val
                if isinstance(val, list):
                    return _cookies_list_to_header(val, "bilibili.com")
        # 兜底：如果是 name->value 结构
        if all(isinstance(v, str) for v in cookie_data.values()):
            return "; ".join([f"{k}={v}" for k, v in cookie_data.items()])
    if isinstance(cookie_data, list):
        return _cookies_list_to_header(cookie_data, "bilibili.com")
    return None

def _extract_bilibili_cookies_list(cookie_data: Any) -> Optional[List[Dict[str, Any]]]:
    """
    从 CookieCloud 数据中提取 Bilibili 的完整 Cookie 列表。
    返回包含 domain, name, value, path, expiry 等字段的 Cookie 列表。
    """
    _app_logger.debug(f"[Cookie] _extract_bilibili_cookies_list called, cookie_data type: {type(cookie_data).__name__}")
    
    if isinstance(cookie_data, dict):
        _app_logger.debug(f"[Cookie] cookie_data is dict, keys: {list(cookie_data.keys())}")
        for key, val in cookie_data.items():
            _app_logger.debug(f"[Cookie] Checking key: {key}, value type: {type(val).__name__}")
            if "bilibili.com" in str(key):
                _app_logger.debug(f"[Cookie] Found bilibili.com in key: {key}")
                if isinstance(val, list):
                    cookies = [c for c in val if isinstance(c, dict) and "bilibili.com" in str(c.get("domain", ""))]
                    _app_logger.debug(f"[Cookie] Extracted {len(cookies)} bilibili cookies from list")
                    if cookies:
                        for c in cookies[:5]:  # 只打印前5个
                            _app_logger.debug(f"[Cookie]   - {c.get('name')}: {c.get('value', '')[:20]}... (domain: {c.get('domain')})")
                    return cookies
    if isinstance(cookie_data, list):
        _app_logger.debug(f"[Cookie] cookie_data is list with {len(cookie_data)} items")
        cookies = [c for c in cookie_data if isinstance(c, dict) and "bilibili.com" in str(c.get("domain", ""))]
        _app_logger.debug(f"[Cookie] Extracted {len(cookies)} bilibili cookies from list")
        return cookies
    
    _app_logger.debug(f"[Cookie] No bilibili cookies found, returning None")
    return None

def _write_cookies_to_netscape_file(cookies: List[Dict[str, Any]], filepath: str) -> None:
    """
    将 Cookie 列表写入 Netscape 格式的 cookie 文件。
    这是 yt-dlp 和 curl 等工具使用的标准格式。
    """
    _app_logger.debug(f"[Cookie] Writing {len(cookies)} cookies to Netscape file: {filepath}")
    written_count = 0
    important_cookies = ['SESSDATA', 'bili_jct', 'DedeUserID', 'buvid3', 'buvid4']
    found_important = []
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write("# Netscape HTTP Cookie File\n")
        f.write("# This file was generated by yt-dlp-api\n\n")
        
        for cookie in cookies:
            domain = cookie.get("domain", "")
            # 处理域名前缀
            if not domain.startswith("."):
                domain = "." + domain
            
            # Netscape 格式: domain, flag, path, secure, expiry, name, value
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            path = cookie.get("path", "/")
            secure = "TRUE" if cookie.get("secure", False) else "FALSE"
            # 使用 expirationDate 或 expiry，如果都没有则设置一个较长的过期时间
            expiry = cookie.get("expirationDate") or cookie.get("expiry")
            if expiry is None:
                expiry = int(time.time()) + 86400 * 365  # 1年后过期
            else:
                expiry = int(expiry)
            name = cookie.get("name", "")
            value = cookie.get("value", "")
            
            if name and value:
                line = f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n"
                f.write(line)
                written_count += 1
                
                # 检查重要的 cookie
                if name in important_cookies:
                    found_important.append(name)
                    _app_logger.debug(f"[Cookie] Important cookie found: {name}={value[:20]}... (expires: {expiry})")
    
    _app_logger.debug(f"[Cookie] Written {written_count} cookies to file")
    _app_logger.debug(f"[Cookie] Important cookies found: {found_important}")
    missing = set(important_cookies) - set(found_important)
    if missing:
        _app_logger.warning(f"[Cookie] Missing important cookies: {missing}")

def _fetch_bilibili_cookies_list() -> Optional[List[Dict[str, Any]]]:
    """
    从 CookieCloud 获取 Bilibili 的完整 Cookie 列表。
    """
    _app_logger.debug("[Cookie] _fetch_bilibili_cookies_list called")
    
    cfg = _get_cookie_cloud_config()
    server = cfg.get("server")
    password = cfg.get("password")
    uuid_val = cfg.get("uuid")
    
    _app_logger.debug(f"[Cookie] CookieCloud config - server: {server}, uuid: {uuid_val}, password: {'*' * len(password) if password else 'None'}")

    if not server or not password or not uuid_val:
        _app_logger.warning("[Cookie] CookieCloud config incomplete, missing server/password/uuid")
        return None

    payload = json.dumps({"password": password}).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    url = _build_cookiecloud_url(server, uuid_val)
    _app_logger.debug(f"[Cookie] Requesting CookieCloud URL: {url}")

    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            status_code = resp.getcode()
            body = resp.read().decode("utf-8")
        
        _app_logger.debug(f"[Cookie] CookieCloud response status: {status_code}")
        _app_logger.debug(f"[Cookie] CookieCloud response length: {len(body)} bytes")
        
        data = json.loads(body)
        _app_logger.debug(f"[Cookie] CookieCloud response keys: {list(data.keys())}")

        cookie_data = data.get("cookie_data")
        if cookie_data is None and isinstance(data.get("data"), dict):
            _app_logger.debug("[Cookie] cookie_data not in root, checking data.cookie_data")
            cookie_data = data["data"].get("cookie_data")
        
        if cookie_data is None:
            _app_logger.warning("[Cookie] No cookie_data found in CookieCloud response")
            _app_logger.debug(f"[Cookie] Full response: {body[:500]}...")
            return None

        cookies_list = _extract_bilibili_cookies_list(cookie_data)
        if cookies_list:
            _app_logger.info(f"[Cookie] Fetched {len(cookies_list)} Bilibili cookies from CookieCloud")
            return cookies_list
        else:
            _app_logger.warning("[Cookie] No Bilibili cookies found in CookieCloud data")
            if isinstance(cookie_data, dict):
                _app_logger.debug(f"[Cookie] Available domains in cookie_data: {list(cookie_data.keys())}")
            return None
    except Exception as e:
        _app_logger.error(f"[Cookie] CookieCloud request failed for {url}: {e}")
        import traceback
        _app_logger.debug(f"[Cookie] Traceback: {traceback.format_exc()}")

    return None

def _fetch_bilibili_cookie_header() -> Optional[str]:
    cfg = _get_cookie_cloud_config()
    server = cfg.get("server")
    password = cfg.get("password")
    uuid_val = cfg.get("uuid")

    if not server or not password or not uuid_val:
        return None

    payload = json.dumps({"password": password}).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    url = _build_cookiecloud_url(server, uuid_val)

    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
        data = json.loads(body)

        cookie_data = data.get("cookie_data")
        if cookie_data is None and isinstance(data.get("data"), dict):
            cookie_data = data["data"].get("cookie_data")

        cookie_header = _extract_bilibili_cookie_header(cookie_data)
        if cookie_header:
            return cookie_header
    except Exception as e:
        _app_logger.warning(f"CookieCloud request failed for {url}: {e}")

    return None


def _apply_cookie_options(opts: Dict[str, Any], url: str, log_prefix: str = "[Cookie]") -> tuple[Dict[str, Any], Optional[str]]:
    """
    根据 URL 判断是否需要添加 cookie 配置到 yt-dlp 选项中。
    
    Args:
        opts (Dict[str, Any]): yt-dlp 的选项字典
        url (str): 视频 URL
        log_prefix (str): 日志前缀，用于区分调用来源
        
    Returns:
        tuple[Dict[str, Any], Optional[str]]: 返回修改后的 opts 和临时 cookie 文件路径（如果有）
            - opts: 修改后的 yt-dlp 选项字典
            - cookie_file_path: 临时 cookie 文件路径，调用方需要在使用完后清理
    """
    cookie_file_path = None
    is_bilibili = _is_bilibili_url(url)
    
    _app_logger.debug(f"{log_prefix} URL: {url}")
    _app_logger.debug(f"{log_prefix} Is Bilibili: {is_bilibili}")
    
    if is_bilibili:
        _app_logger.info(f"{log_prefix} Bilibili URL detected, attempting to fetch cookies from CookieCloud")
        cookies_list = _fetch_bilibili_cookies_list()
        if cookies_list:
            # 创建临时 cookie 文件
            cookie_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
            _write_cookies_to_netscape_file(cookies_list, cookie_file.name)
            cookie_file.close()
            cookie_file_path = cookie_file.name
            _app_logger.info(f"{log_prefix} Cookie file created at: {cookie_file_path}")
            
            # 设置 cookiefile 选项
            opts['cookiefile'] = cookie_file_path
            _app_logger.debug(f"{log_prefix} Using cookiefile: {cookie_file_path}")
            
            # 打印 cookie 文件内容用于调试
            try:
                with open(cookie_file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    _app_logger.debug(f"{log_prefix} Cookie file content:\n{content}")
            except Exception as e:
                _app_logger.debug(f"{log_prefix} Failed to read cookie file: {e}")
        else:
            _app_logger.warning(f"{log_prefix} No cookies fetched from CookieCloud!")
        
        # Bilibili 需要正确的 Referer 和 User-Agent
        opts['http_headers'] = {
            'Referer': 'https://www.bilibili.com/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
        _app_logger.debug(f"{log_prefix} Added Bilibili headers: Referer and User-Agent")
    
    return opts, cookie_file_path


def _cleanup_cookie_file(cookie_file_path: Optional[str], log_prefix: str = "[Cookie]") -> None:
    """
    清理临时 cookie 文件。
    
    Args:
        cookie_file_path (Optional[str]): cookie 文件路径
        log_prefix (str): 日志前缀
    """
    if cookie_file_path and os.path.exists(cookie_file_path):
        try:
            os.unlink(cookie_file_path)
            _app_logger.debug(f"{log_prefix} Cleaned up cookie file: {cookie_file_path}")
        except Exception as e:
            _app_logger.warning(f"{log_prefix} Failed to clean up cookie file: {e}")


def download_video(url: str, output_path: str = "./downloads", format: str = "best", quiet: bool = False) -> Dict[str, Any]:
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
    ydl_opts, cookie_file_path = _apply_cookie_options(ydl_opts, url, "[Download]")
    
    # temp_ydl_opts 复用相同的 cookie 文件（如果有）
    if cookie_file_path:
        temp_ydl_opts['cookiefile'] = cookie_file_path
    if 'http_headers' in ydl_opts:
        temp_ydl_opts['http_headers'] = ydl_opts['http_headers']
    
    _app_logger.debug(f"[Download] ydl_opts: {ydl_opts}")
    
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
        _cleanup_cookie_file(cookie_file_path, "[Download]")

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
    ydl_opts, cookie_file_path = _apply_cookie_options(ydl_opts, url, "[VideoInfo]")
    
    _app_logger.debug(f"[VideoInfo] ydl_opts: {ydl_opts}")
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return ydl.sanitize_info(info)
    finally:
        # 清理临时 cookie 文件
        _cleanup_cookie_file(cookie_file_path, "[VideoInfo]")

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

app = FastAPI(title="yt-dlp API", description="API for downloading videos using yt-dlp")

# 确保应用日志可见（无论是否通过 uvicorn CLI 启动）
if not _app_logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)  # 必须同时设置 handler 的级别
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    _app_logger.addHandler(handler)
_app_logger.setLevel(logging.DEBUG)
_app_logger.propagate = False  # 防止日志被根 logger 重复处理或过滤

_app_logger.info("Logger initialized with DEBUG level")

# URL 解码中间件 - 处理被编码的请求路径和代理请求
class URLDecodeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        logger = _app_logger
        
        # 获取原始路径和 raw_path
        original_path = request.scope.get('path', '')
        raw_path = request.scope.get('raw_path', b'').decode('utf-8', errors='ignore')
        
        logger.info(f"[Middleware] Original path: {original_path}")
        logger.info(f"[Middleware] Raw path: {raw_path}")
        print(f"[Middleware] Original path: {original_path}", flush=True)
        print(f"[Middleware] Raw path: {raw_path}", flush=True)
        
        # 优先检查 raw_path（包含原始未解码的路径）
        path_to_check = raw_path if raw_path else original_path
        
        # 解码路径
        decoded_path = urllib.parse.unquote(path_to_check)
        logger.info(f"[Middleware] Decoded path: {decoded_path}")
        print(f"[Middleware] Decoded path: {decoded_path}", flush=True)
        
        # 检查是否是代理请求（路径以 http:// 或 https:// 开头）
        if decoded_path.startswith("http://") or decoded_path.startswith("https://"):
            parsed = urllib.parse.urlparse(decoded_path)
            new_path = parsed.path if parsed.path else "/"
            logger.info(f"[Middleware] Proxy request detected, extracting path: {new_path}")
            print(f"[Middleware] Proxy request detected, extracting path: {new_path}", flush=True)
            request.scope["path"] = new_path
        else:
            request.scope['path'] = decoded_path
        
        return await call_next(request)

app.add_middleware(URLDecodeMiddleware)

# 管理页面路由
@app.get("/", response_class=HTMLResponse)
@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """
    返回管理界面HTML页面
    """
    admin_html_path = os.path.join(os.path.dirname(__file__), "admin.html")
    if os.path.exists(admin_html_path):
        with open(admin_html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    else:
        return HTMLResponse(content="<h1>Admin page not found</h1>", status_code=404)

class DownloadRequest(BaseModel):
    url: str
    output_path: str = "./downloads"
    format: str = "bestvideo+bestaudio/best"
    quiet: bool = False

async def process_download_task(task_id: str, url: str, output_path: str, format: str, quiet: bool):
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
            if _is_s3_configured():
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
                                _app_logger.info(f"[S3] Local file deleted after upload: {filename}")
                            except Exception as e:
                                _app_logger.warning(f"[S3] Failed to delete local file {filename}: {e}")
                except Exception as e:
                    _app_logger.error(f"[S3] Error uploading file for task {task_id}: {e}")
                
                # 上传完成后变成 completed 状态
                state.update_task(task_id, "completed", result=result, s3_url=s3_url)
            else:
                # S3未配置，直接变成 completed 状态
                state.update_task(task_id, "completed", result=result)
    except Exception as e:
        state.update_task(task_id, "failed", error=str(e))

@app.post("/download", response_class=JSONResponse)
async def api_download_video(request: DownloadRequest):
    """
    Submit a video download task and return a task ID to track progress.
    """
    # 如果有相同的url和output_path的任务已经存在，检查状态
    existing_task = next((task for task in state.tasks.values() if task.format == request.format and task.url == request.url and task.output_path == request.output_path), None)
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

@app.delete("/task/{task_id}", response_class=JSONResponse)
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

@app.get("/download/{task_id}/file")
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

def start_api():
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    print("Starting yt-dlp API server...")
    start_api()