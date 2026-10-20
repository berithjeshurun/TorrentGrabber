from torrentsearch.api.app import app
import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(__import__("os").environ.get("PORT", "8000")),
        reload=False,
    )
