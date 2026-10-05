"""Example extension: one API route and one menu entry.

Copy this file into `$PETIT_DATA_DIR/app_extensions/` and restart the dashboard.
"""

from fastapi import Depends


def register(app, ctx):
    @app.get("/ext/hello")
    async def hello(user: dict = Depends(ctx.require_user)):
        return {"hello": user.get("name") or user["id"]}

    ctx.add_nav("Hello", "/ext/hello")
