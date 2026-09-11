from src.infrastructure.logger.config import LoggingConfig, get_logger
from src.interfaces.http.server import create_http_server

LoggingConfig.setup_logging()
logger = get_logger()


app = create_http_server()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8081)
