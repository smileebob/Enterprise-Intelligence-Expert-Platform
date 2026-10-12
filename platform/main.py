"""ASGI 入口：uvicorn main:app（规则 A-01 / L-01）。"""

from app import create_app

app = create_app()

if __name__ == "__main__":  # 本地开发便捷启动（生产用 uvicorn 多 worker）
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
