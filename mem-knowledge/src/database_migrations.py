"""知识库发布迁移命令入口。"""

from .infrastructure.database.migrations import main

if __name__ == "__main__":
    main()
