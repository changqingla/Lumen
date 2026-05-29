"""MinerU API client."""

import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Dict

import httpx

from config.settings import settings
from utils.http_client import get_http_client

logger = logging.getLogger(__name__)

MINERU_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


@dataclass(frozen=True)
class MineruMarkdownResult:
    """Markdown and companion assets extracted from a MinerU result archive."""

    markdown: str
    assets: dict[str, bytes]
    markdown_path: str


class MineruService:
    """
    Client for MinerU official API (mineru.net).

    流程:
    1. 调用 /file-urls/batch 获取预签名上传 URL
    2. PUT 上传文件到预签名 URL
    3. 系统自动开始解析任务
    4. 轮询 /extract-results/batch/{batch_id} 获取任务状态
    5. 任务完成后下载 zip 并提取 markdown
    """

    @staticmethod
    def _get_headers() -> Dict[str, str]:
        """获取 API 请求头（包含认证信息）"""
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.MINERU_API_TOKEN}",
        }

    @staticmethod
    async def convert_document(file_data: bytes, filename: str) -> Dict[str, Any]:
        """Convert PDF/Office document to Markdown using MinerU official API."""
        try:
            logger.info(f"[MinerU] Requesting upload URL for {filename}")

            request_data = {
                "files": [
                    {"name": filename},
                ],
                "model_version": settings.MINERU_MODEL_VERSION,
            }

            response = await get_http_client().post(
                f"{settings.MINERU_API_BASE_URL}/file-urls/batch",
                headers=MineruService._get_headers(),
                json=request_data,
            )
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 0:
                raise Exception(f"Failed to get upload URL: {result.get('msg')}")

            batch_id = result["data"]["batch_id"]
            file_urls = result["data"]["file_urls"]

            if not file_urls:
                raise Exception("No upload URL returned from MinerU API")

            upload_url = file_urls[0]
            logger.info(f"[MinerU] Got upload URL, batch_id: {batch_id}")
            logger.info("[MinerU] Uploading file to presigned URL...")

            async with httpx.AsyncClient(timeout=settings.HTTP_UPLOAD_TIMEOUT) as upload_client:
                upload_response = await upload_client.put(
                    upload_url,
                    content=file_data,
                )
                upload_response.raise_for_status()

            logger.info(f"[MinerU] File uploaded successfully, batch_id: {batch_id}")

            return {
                "batch_id": batch_id,
                "task_id": batch_id,
            }

        except httpx.HTTPStatusError as e:
            logger.error(f"[MinerU] HTTP error: {e.response.status_code} - {e.response.text}")
            raise Exception(f"MinerU API error: {e.response.status_code}")
        except Exception as e:
            logger.error(f"[MinerU] Conversion error: {e}")
            raise

    @staticmethod
    async def get_task_status(batch_id: str) -> Dict[str, Any]:
        """Get MinerU task status."""
        try:
            response = await get_http_client().get(
                f"{settings.MINERU_API_BASE_URL}/extract-results/batch/{batch_id}",
                headers=MineruService._get_headers(),
            )
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 0:
                raise Exception(f"Failed to get task status: {result.get('msg')}")

            extract_results = result["data"].get("extract_result", [])

            if not extract_results:
                return {
                    "status": "pending",
                    "message": "Waiting for file processing",
                }

            file_result = extract_results[0]
            state = file_result.get("state", "pending")
            status_map = {
                "pending": "pending",
                "waiting-file": "pending",
                "running": "running",
                "converting": "running",
                "done": "completed",
                "failed": "failed",
            }

            normalized_status = status_map.get(state, "pending")

            response_data = {
                "status": normalized_status,
                "state": state,
            }

            if normalized_status == "completed":
                response_data["full_zip_url"] = file_result.get("full_zip_url")
            elif normalized_status == "failed":
                response_data["message"] = file_result.get("err_msg", "Unknown error")
            elif normalized_status == "running":
                progress = file_result.get("extract_progress", {})
                response_data["progress"] = {
                    "extracted_pages": progress.get("extracted_pages", 0),
                    "total_pages": progress.get("total_pages", 0),
                    "start_time": progress.get("start_time"),
                }

            return response_data

        except Exception as e:
            logger.error(f"[MinerU] Get task status error: {e}")
            raise

    @staticmethod
    async def get_content(batch_id: str) -> str:
        """Download and extract markdown content from MinerU result."""
        result = await MineruService.get_content_with_assets(batch_id)
        return result.markdown

    @staticmethod
    async def get_content_with_assets(batch_id: str) -> MineruMarkdownResult:
        """Download and extract markdown content plus image assets from MinerU result."""
        try:
            status = await MineruService.get_task_status(batch_id)

            if status["status"] != "completed":
                raise Exception(f"Task not completed, current status: {status['status']}")

            zip_url = status.get("full_zip_url")
            if not zip_url:
                raise Exception("No download URL available")

            logger.info(f"[MinerU] Downloading result from: {zip_url}")

            async with httpx.AsyncClient(timeout=settings.HTTP_DOWNLOAD_TIMEOUT) as download_client:
                response = await download_client.get(zip_url)
                response.raise_for_status()
                zip_data = response.content

            logger.info(f"[MinerU] Downloaded {len(zip_data)} bytes, extracting markdown...")
            result = MineruService._extract_markdown_result_from_zip(zip_data)
            logger.info(
                "[MinerU] Extracted %s chars of markdown and %s asset(s)",
                len(result.markdown),
                len(result.assets),
            )

            return result

        except Exception as e:
            logger.error(f"[MinerU] Get content error: {e}")
            raise

    @staticmethod
    def _extract_markdown_from_zip(zip_data: bytes) -> str:
        """Extract markdown content from MinerU result zip file."""
        return MineruService._extract_markdown_result_from_zip(zip_data).markdown

    @staticmethod
    def _extract_markdown_result_from_zip(zip_data: bytes) -> MineruMarkdownResult:
        """Extract markdown and companion image assets from MinerU result zip file."""
        try:
            with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zf:
                file_list = zf.namelist()
                logger.debug(f"[MinerU] Zip contains: {file_list}")

                md_files = [f for f in file_list if f.endswith(".md")]

                if not md_files:
                    md_files = [f for f in file_list if ".md" in f.lower()]

                if not md_files:
                    raise Exception(f"No markdown file found in zip. Files: {file_list}")

                md_file = sorted(md_files, key=lambda x: len(x))[0]
                logger.info(f"[MinerU] Extracting markdown from: {md_file}")

                with zf.open(md_file) as f:
                    content = f.read().decode("utf-8")

                md_parent = PurePosixPath(md_file).parent
                assets: dict[str, bytes] = {}
                for name in file_list:
                    if name.endswith("/"):
                        continue
                    path = PurePosixPath(name)
                    if any(part in {"", ".", ".."} for part in path.parts):
                        continue
                    suffix = PurePosixPath(name).suffix.lower()
                    if suffix not in MINERU_IMAGE_EXTENSIONS:
                        continue
                    try:
                        relative_path = path.relative_to(md_parent)
                    except ValueError:
                        relative_path = path.name
                    normalized = str(relative_path).lstrip("/")
                    if not normalized or normalized.startswith("../") or "/../" in normalized:
                        continue
                    with zf.open(name) as asset_file:
                        assets[normalized] = asset_file.read()

                return MineruMarkdownResult(
                    markdown=content,
                    assets=assets,
                    markdown_path=md_file,
                )

        except zipfile.BadZipFile:
            raise Exception("Invalid zip file received from MinerU")
        except Exception as e:
            raise Exception(f"Failed to extract markdown: {e}")
