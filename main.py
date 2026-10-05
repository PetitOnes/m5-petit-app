"""Thin entry point: keeps `python main.py` and `uvicorn main:app` working."""

from petit_app.main import app, main

__all__ = ["app", "main"]

if __name__ == "__main__":
    main()
