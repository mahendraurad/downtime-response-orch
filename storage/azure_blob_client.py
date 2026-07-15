"""
Azure Blob Storage client for the Learning and Memory Agent.

Used to store:
- Timestamped log files (manager requirement for auditability)
- Embedding artifacts and FAISS index backups
- Learned case documents as JSON files

Dev mode: if AZURE_BLOB_URL is not set in .env, falls back to saving locally
at data/outputs/. This means development works without any Azure credentials.

Prod mode: requires AZURE_BLOB_URL in .env. Uploads to Azure Blob Storage.
"""
import json
import logging
import os
from pathlib import Path
from typing import Optional

from azure.storage.blob import BlobClient
from config.settings import settings
from schemas.learned_case import LearnedCaseDocument

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(module)s | %(message)s'
)
logger = logging.getLogger(__name__)


class AzureBlobClient:
    async def upload_log(self, log_content: str, filename: str) -> str:
        """
        Uploads a log file. If dev mode: saves to data/outputs/logs/filename.
        Returns path or blob URL.
        """
        if not settings.AZURE_BLOB_URL:
            output_dir = Path("data/outputs/logs")
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / filename
            path.write_text(log_content)
            logger.info("Saved log to local path %s", path)
            return str(path)
        blob = BlobClient.from_blob_url(f"{settings.AZURE_BLOB_URL}/{filename}")
        blob.upload_blob(log_content, overwrite=True)
        logger.info("Uploaded log to Azure Blob %s", filename)
        return blob.url

    async def upload_learned_case(self, case: LearnedCaseDocument) -> str:
        """Serialises case to JSON and uploads. Returns storage path."""
        content = json.dumps(case.model_dump(), indent=2)
        filename = f"learned_case_{case.case_id}.json"
        if not settings.AZURE_BLOB_URL:
            output_dir = Path("data/outputs")
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / filename
            path.write_text(content)
            logger.info("Saved learned case locally %s", path)
            return str(path)
        blob = BlobClient.from_blob_url(f"{settings.AZURE_BLOB_URL}/{filename}")
        blob.upload_blob(content, overwrite=True)
        logger.info("Uploaded learned case %s", filename)
        return blob.url

    async def upload_embedding_artifact(self, data: bytes, filename: str) -> str:
        """Stores FAISS index backup or embedding artifact."""
        if not settings.AZURE_BLOB_URL:
            output_dir = Path("data/outputs")
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / filename
            path.write_bytes(data)
            logger.info("Saved embedding artifact locally %s", path)
            return str(path)
        blob = BlobClient.from_blob_url(f"{settings.AZURE_BLOB_URL}/{filename}")
        blob.upload_blob(data, overwrite=True)
        logger.info("Uploaded artifact %s", filename)
        return blob.url
