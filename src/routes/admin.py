"""管理页面路由"""
import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
@router.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """
    返回管理界面HTML页面
    """
    # 从项目根目录读取 admin.html
    admin_html_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "admin.html")
    if os.path.exists(admin_html_path):
        with open(admin_html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    else:
        return HTMLResponse(content="<h1>Admin page not found</h1>", status_code=404)
