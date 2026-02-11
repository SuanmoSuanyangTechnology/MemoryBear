"""Command-line interface for feishu integration."""

import asyncio
import sys
from app.core.rag.integrations.feishu.client import FeishuAPIClient
from app.core.rag.integrations.feishu.models import FileInfo


def main(feishu_app_id: str,  # Feishu application ID
               feishu_app_secret: str,  # Feishu application secret
               feishu_folder_token: str,  # Feishu Folder Token
               save_dir: str,       # save file directory
               feishu_api_base_url: str = "https://open.feishu.cn/open-apis",  # Feishu API base URL
               timeout: int = 30,  # Request timeout in seconds
               max_retries: int = 3,  # Maximum number of retries
               recursive: bool = True # recursive: Whether to sync subfolders recursively,
               ):
    """Main entry point for the feishuAPIClient."""
    # Create feishuAPIClient
    api_client = FeishuAPIClient(
        app_id=feishu_app_id,
        app_secret=feishu_app_secret,
        api_base_url=feishu_api_base_url,
        timeout=timeout,
        max_retries=max_retries
    )

    # Get all files from folder
    async def async_get_files(api_client: FeishuAPIClient, feishu_folder_token: str):
        async with api_client as client:
            if recursive:
                files = await client.list_all_folder_files(feishu_folder_token, recursive=True)
            else:
                all_files = []
                page_token = None
                while True:
                    files_page, page_token = await client.list_folder_files(
                        feishu_folder_token, page_token
                    )
                    all_files.extend(files_page)
                    if not page_token:
                        break
                files = all_files
            return files
    files = asyncio.run(async_get_files(api_client,feishu_folder_token))

    # Filter out folders, only sync documents
    # documents = [f for f in files if f.type in ["doc", "docx", "sheet", "bitable", "file", "slides"]]
    documents = [f for f in files if f.type in ["doc", "docx", "sheet", "bitable", "file"]]

    try:
        for doc in documents:
            print(f"\n{'=' * 80}")
            print(f"token: {doc.token}")
            print(f"name: {doc.name}")
            print(f"type: {doc.type}")
            print(f"created_time: {doc.created_time}")
            print(f"modified_time: {doc.modified_time}")
            print(f"owner_id: {doc.owner_id}")
            print(f"url: {doc.url}")
            print(f"{'=' * 80}\n")
            # download document from Feishu FileInfo
            async def async_download_document(api_client: FeishuAPIClient, doc: FileInfo, save_dir: str):
                async with api_client as client:
                    file_path = await client.download_document(document=doc, save_dir=save_dir)
                    return file_path

            file_path = asyncio.run(async_download_document(api_client, doc, save_dir))
            print(file_path)

    except KeyboardInterrupt:
        print("\n\nfeishu integration interrupted by user.")

    except Exception as e:
        print(f"\n\nError during feishu integration: {e}")
        sys.exit(1)


if __name__ == '__main__':
    feishu_app_id = ""
    feishu_app_secret = ""
    feishu_folder_token = ""
    save_dir = "/Volumes/MacintoshBD/Repository/RedBearAI/MemoryBear/api/files/"
    main(feishu_app_id, feishu_app_secret, feishu_folder_token, save_dir)
