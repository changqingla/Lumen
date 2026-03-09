"""External services client for document processing."""
import httpx
import zipfile
import io
from typing import Dict, List, Optional, Any
from config.settings import settings
import logging

logger = logging.getLogger(__name__)

# HTTP client with timeout (从配置读取超时时间)
http_client = httpx.AsyncClient(timeout=settings.HTTP_DEFAULT_TIMEOUT)


class MineruService:
    """
    Client for MinerU official API (mineru.net).

    官方 API 文档: https://mineru.net/apiManage/docs

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
            "Authorization": f"Bearer {settings.MINERU_API_TOKEN}"
        }

    @staticmethod
    async def convert_document(file_data: bytes, filename: str) -> Dict[str, Any]:
        """
        Convert PDF/Office document to Markdown using MinerU official API.

        流程:
        1. 获取预签名上传 URL
        2. 上传文件
        3. 返回 batch_id 用于后续状态查询

        Args:
            file_data: File binary data
            filename: Original filename

        Returns:
            Response with batch_id (用于查询任务状态)
        """
        try:
            # Step 1: 获取预签名上传 URL
            logger.info(f"[MinerU] Requesting upload URL for {filename}")

            request_data = {
                "files": [
                    {"name": filename}
                ],
                "model_version": settings.MINERU_MODEL_VERSION
            }

            response = await http_client.post(
                f"{settings.MINERU_API_BASE_URL}/file-urls/batch",
                headers=MineruService._get_headers(),
                json=request_data
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

            # Step 2: 上传文件到预签名 URL
            logger.info(f"[MinerU] Uploading file to presigned URL...")

            # 使用新的 client 避免 Content-Type 冲突
            async with httpx.AsyncClient(timeout=settings.HTTP_UPLOAD_TIMEOUT) as upload_client:
                upload_response = await upload_client.put(
                    upload_url,
                    content=file_data
                )
                upload_response.raise_for_status()

            logger.info(f"[MinerU] File uploaded successfully, batch_id: {batch_id}")

            # 返回 batch_id，用于后续状态查询
            return {
                "batch_id": batch_id,
                "task_id": batch_id  # 保持兼容性
            }

        except httpx.HTTPStatusError as e:
            logger.error(f"[MinerU] HTTP error: {e.response.status_code} - {e.response.text}")
            raise Exception(f"MinerU API error: {e.response.status_code}")
        except Exception as e:
            logger.error(f"[MinerU] Conversion error: {e}")
            raise

    @staticmethod
    async def get_task_status(batch_id: str) -> Dict[str, Any]:
        """
        Get MinerU task status.

        Args:
            batch_id: Batch ID returned from convert_document

        Returns:
            Task status dict with normalized fields:
            - status: "pending" | "running" | "completed" | "failed"
            - full_zip_url: URL to download result (when completed)
            - message: Error message (when failed)
            - progress: Progress info (when running)
        """
        try:
            response = await http_client.get(
                f"{settings.MINERU_API_BASE_URL}/extract-results/batch/{batch_id}",
                headers=MineruService._get_headers()
            )
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 0:
                raise Exception(f"Failed to get task status: {result.get('msg')}")

            # 解析批量结果（我们只上传了一个文件）
            extract_results = result["data"].get("extract_result", [])

            if not extract_results:
                return {
                    "status": "pending",
                    "message": "Waiting for file processing"
                }

            # 取第一个文件的结果
            file_result = extract_results[0]
            state = file_result.get("state", "pending")

            # 标准化状态字段
            status_map = {
                "pending": "pending",
                "waiting-file": "pending",
                "running": "running",
                "converting": "running",
                "done": "completed",
                "failed": "failed"
            }

            normalized_status = status_map.get(state, "pending")

            response_data = {
                "status": normalized_status,
                "state": state,  # 保留原始状态
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
                    "start_time": progress.get("start_time")
                }

            return response_data

        except Exception as e:
            logger.error(f"[MinerU] Get task status error: {e}")
            raise

    @staticmethod
    async def get_content(batch_id: str) -> str:
        """
        Download and extract markdown content from MinerU result.

        Args:
            batch_id: Batch ID

        Returns:
            Markdown content string
        """
        try:
            # 首先获取任务状态以获得 zip URL
            status = await MineruService.get_task_status(batch_id)

            if status["status"] != "completed":
                raise Exception(f"Task not completed, current status: {status['status']}")

            zip_url = status.get("full_zip_url")
            if not zip_url:
                raise Exception("No download URL available")

            logger.info(f"[MinerU] Downloading result from: {zip_url}")

            # 下载 zip 文件
            async with httpx.AsyncClient(timeout=settings.HTTP_DOWNLOAD_TIMEOUT) as download_client:
                response = await download_client.get(zip_url)
                response.raise_for_status()
                zip_data = response.content

            logger.info(f"[MinerU] Downloaded {len(zip_data)} bytes, extracting markdown...")

            # 解压并提取 markdown 内容
            markdown_content = MineruService._extract_markdown_from_zip(zip_data)

            logger.info(f"[MinerU] Extracted {len(markdown_content)} chars of markdown")

            return markdown_content

        except Exception as e:
            logger.error(f"[MinerU] Get content error: {e}")
            raise

    @staticmethod
    def _extract_markdown_from_zip(zip_data: bytes) -> str:
        """
        Extract markdown content from MinerU result zip file.

        MinerU zip 文件结构:
        - {filename}/
          - {filename}.md (主要的 markdown 文件)
          - {filename}.json (结构化数据)
          - images/ (图片目录)

        Args:
            zip_data: ZIP file binary data

        Returns:
            Markdown content string
        """
        try:
            with zipfile.ZipFile(io.BytesIO(zip_data), 'r') as zf:
                # 列出所有文件
                file_list = zf.namelist()
                logger.debug(f"[MinerU] Zip contains: {file_list}")

                # 查找 .md 文件
                md_files = [f for f in file_list if f.endswith('.md')]

                if not md_files:
                    # 如果没有 .md 文件，尝试查找 full.md 或其他变体
                    md_files = [f for f in file_list if '.md' in f.lower()]

                if not md_files:
                    raise Exception(f"No markdown file found in zip. Files: {file_list}")

                # 优先选择主 markdown 文件（通常是最大的或名称最短的）
                md_file = sorted(md_files, key=lambda x: len(x))[0]
                logger.info(f"[MinerU] Extracting markdown from: {md_file}")

                # 读取 markdown 内容
                with zf.open(md_file) as f:
                    content = f.read().decode('utf-8')

                return content

        except zipfile.BadZipFile:
            raise Exception("Invalid zip file received from MinerU")
        except Exception as e:
            raise Exception(f"Failed to extract markdown: {e}")


class DocumentProcessService:
    """Client for document processing service (chunking, embedding, storage)."""
    
    @staticmethod
    async def parse_document(
        file_path: str,
        document_id: str,
        index_name: str,
        filename: str
    ) -> Dict[str, Any]:
        """
        Parse document: chunk + embed + store to ES.
        
        Args:
            file_path: Path to markdown file
            document_id: Document ID
            index_name: ES index name
            filename: Original filename
        
        Returns:
            Response with task_id
        """
        try:
            # Read file content
            with open(file_path, 'rb') as f:
                file_data = f.read()
            
            files = {'file': (filename, file_data)}
            data = {
                'model_factory': settings.EMBEDDING_MODEL_FACTORY,
                'model_name': settings.EMBEDDING_MODEL_NAME,
                'base_url': settings.EMBEDDING_BASE_URL,
                'index_name': index_name,
                'document_id': document_id,
                'parser_type': settings.DEFAULT_PARSER_TYPE,
                'chunk_token_num': str(settings.DEFAULT_CHUNK_TOKEN_NUM),
                'es_host': settings.ES_HOST,
            }
            
            if settings.EMBEDDING_API_KEY:
                data['api_key'] = settings.EMBEDDING_API_KEY
            
            response = await http_client.post(
                f"{settings.DOC_PROCESS_BASE_URL}/parse-document",
                files=files,
                data=data
            )
            response.raise_for_status()
            result = response.json()
            
            if not result.get("success"):
                raise Exception(f"Document parsing failed: {result.get('message')}")
            
            return result["data"]
        
        except Exception as e:
            logger.error(f"Document parsing error: {e}")
            raise
    
    @staticmethod
    async def get_task_status(task_id: str) -> Dict[str, Any]:
        """Get document processing task status."""
        try:
            response = await http_client.get(
                f"{settings.DOC_PROCESS_BASE_URL}/task-status/{task_id}"
            )
            response.raise_for_status()
            result = response.json()
            
            if not result.get("success"):
                raise Exception(f"Failed to get task status: {result.get('message')}")
            
            return result
        
        except Exception as e:
            logger.error(f"Get task status error: {e}")
            raise
    
    @staticmethod
    async def delete_document_from_es(document_id: str, index_name: str) -> Dict[str, Any]:
        """
        Delete document chunks from Elasticsearch.
        
        Args:
            document_id: Document ID
            index_name: ES index name
        
        Returns:
            Deletion result
        """
        try:
            payload = {
                "document_id": document_id,
                "index_name": index_name,
                "es_host": settings.ES_HOST
            }
            
            response = await http_client.post(
                f"{settings.DOC_PROCESS_BASE_URL}/delete-document",
                json=payload
            )
            response.raise_for_status()
            result = response.json()
            
            if not result.get("success"):
                raise Exception(f"ES deletion failed: {result.get('message')}")
            
            return result["data"]
        
        except Exception as e:
            logger.error(f"Delete from ES error: {e}")
            raise
    
async def close_http_client():
    """Close HTTP client on shutdown."""
    await http_client.aclose()

