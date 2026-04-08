"""不启用热重载的 Gateway 启动入口。"""

import uvicorn


def main() -> None:
    """在不使用文件监视器的情况下启动 Gateway API。"""
    uvicorn.run(
        "src.gateway.app:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
    )


if __name__ == "__main__":
    main()
