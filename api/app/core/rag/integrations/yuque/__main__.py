"""Main entry point for Yuque integration testing."""

import asyncio
import sys
from app.core.rag.integrations.yuque.client import YuqueAPIClient
from app.core.rag.integrations.yuque.models import YuqueDocInfo


def main(yuque_user_id: str,  # yuque User ID
               yuque_token: str,    # yuque Token
               save_dir: str,       # save file directory
               ):
    """Main entry point for the YuqueAPIClient."""
    # Create feishuAPIClient
    api_client = YuqueAPIClient(
        user_id=yuque_user_id,
        token=yuque_token
    )

    # Get all files from all repos
    async def async_get_files(api_client: YuqueAPIClient):
        async with api_client as client:
            print("\n=== Fetching repositories ===")
            repos = await client.get_user_repos()
            print(f"Found {len(repos)} repositories:")
            all_files = []
            for repo in repos:
                # Get documents from repository
                print(f"\n=== Fetching documents from '{repo.name}' ===")
                docs = await client.get_repo_docs(repo.id)
                all_files.extend(docs)
            return all_files
    files = asyncio.run(async_get_files(api_client))

    try:
        for doc in files:
            print(f"\n{'=' * 80}")
            print(f"id: {doc.id}")
            print(f"type: {doc.type}")
            print(f"slug: {doc.slug}")
            print(f"title: {doc.title}")
            print(f"book_id: {doc.book_id}")
            # print(f"format: {doc.format}")
            # print(f"body: {doc.body}")
            # print(f"body_draft: {doc.body_draft}")
            # print(f"body_html: {doc.body_html}")
            print(f"public: {doc.public}")
            print(f"status: {doc.status}")
            print(f"created_at: {doc.created_at}")
            print(f"updated_at: {doc.updated_at}")
            print(f"published_at: {doc.published_at}")
            print(f"word_count: {doc.word_count}")
            print(f"cover: {doc.cover}")
            print(f"description: {doc.description}")
            print(f"{'=' * 80}\n")
            # download document from Feishu FileInfo
            async def async_download_document(api_client: YuqueAPIClient, doc: YuqueDocInfo, save_dir: str):
                async with api_client as client:
                    file_path = await client.download_document(doc, save_dir)
                    return file_path

            file_path = asyncio.run(async_download_document(api_client, doc, save_dir))
            print(file_path)

    except KeyboardInterrupt:
        print("\n\nfeishu integration interrupted by user.")

    except Exception as e:
        print(f"\n\nError during feishu integration: {e}")
        sys.exit(1)


if __name__ == "__main__":
    yuque_user_id = ""
    yuque_token = ""
    save_dir = "/Volumes/MacintoshBD/Repository/RedBearAI/MemoryBear/api/files/"
    main(yuque_user_id, yuque_token, save_dir)
