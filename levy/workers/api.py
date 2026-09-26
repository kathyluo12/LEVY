"""API worker: launches the FastAPI gateway via uvicorn."""

from __future__ import annotations

from levy.settings import get_settings


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "levy.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
