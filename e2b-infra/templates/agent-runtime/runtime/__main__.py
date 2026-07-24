"""Allow running as: python -m runtime.entrypoint"""
from runtime.entrypoint import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
