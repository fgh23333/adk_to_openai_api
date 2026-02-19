import re
import base64
import mimetypes
import magic
from typing import List, Optional, Tuple, Union
import httpx
from app.config import settings
from app.models import ContentPart, ADKPart, ADKInlineData
import logging

logger = logging.getLogger(__name__)


class MultimodalProcessor:
    def __init__(self):
        self.max_file_size = settings.max_file_size_mb * 1024 * 1024  # Convert to bytes
        self.timeout = settings.download_timeout
        
        # 支持的文件类型配置
        self.supported_types = {
            "images": ["image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "image/svg+xml"],
            "documents": [
                "application/pdf",
                "text/plain",
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.ms-excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.ms-powerpoint",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            ],
            "archives": [
                "application/zip",
                "application/x-rar-compressed",
                "application/x-7z-compressed"
            ],
            "videos": [
                "video/mp4",
                "video/avi",
                "video/mov",
                "video/wmv",
                "video/flv"
            ],
            "audio": [
                "audio/mp3",
                "audio/wav",
                "audio/flac",
                "audio/aac"
            ]
        }
        
        # 文件大小限制（字节）
        self.file_size_limits = {
            "image": 10 * 1024 * 1024,      # 10MB
            "document": 20 * 1024 * 1024,   # 20MB
            "archive": 50 * 1024 * 1024,    # 50MB
            "video": 100 * 1024 * 1024,     # 100MB
            "audio": 20 * 1024 * 1024       # 20MB
        }
        
    def validate_file(self, file_data: bytes, filename: str, mime_type: str = None) -> Tuple[bool, str, str]:
        """
        验证文件类型和大小
        Returns: (is_valid, error_message, detected_mime_type)
        """
        try:
            # 检测文件大小
            file_size = len(file_data)
            
            # 检测MIME类型
            if not mime_type:
                mime_type = magic.from_buffer(file_data, mime=True)
            
            # 获取所有支持的类型
            all_supported_types = []
            for types in self.supported_types.values():
                all_supported_types.extend(types)
            
            # 检查文件类型
            if mime_type not in all_supported_types:
                return False, f"不支持的文件类型: {mime_type}", mime_type
            
            # 确定文件类别和大小限制
            category = None
            size_limit = self.max_file_size  # 默认限制
            
            for cat, types in self.supported_types.items():
                if mime_type in types:
                    category = cat.rstrip('s')  # 移除复数形式
                    size_limit = self.file_size_limits.get(category, self.max_file_size)
                    break
            
            # 检查文件大小
            if file_size > size_limit:
                size_mb = size_limit / (1024 * 1024)
                return False, f"文件大小超过限制 ({size_mb:.1f}MB)", mime_type
            
            return True, "", mime_type
            
        except Exception as e:
            logger.error(f"文件验证失败: {e}")
            return False, f"文件验证失败: {str(e)}", ""

    def process_base64_file(self, base64_data: str, filename: str, mime_type: str = None) -> Optional[ADKInlineData]:
        """
        处理Base64编码的文件数据
        """
        try:
            # 解码Base64数据
            if base64_data.startswith(f"data:{mime_type};base64,"):
                # 移除数据URL前缀
                base64_data = base64_data.split(",", 1)[1]
            
            file_data = base64.b64decode(base64_data)
            
            # 验证文件
            is_valid, error_msg, detected_mime = self.validate_file(file_data, filename, mime_type)
            
            if not is_valid:
                logger.error(f"文件验证失败: {error_msg}")
                return None
            
            # 重新编码为Base64（确保格式正确）
            final_base64 = base64.b64encode(file_data).decode('utf-8')
            
            return ADKInlineData(
                mimeType=detected_mime,
                data=final_base64
            )
            
        except Exception as e:
            logger.error(f"处理Base64文件失败: {e}")
            return None

    async def process_content(self, content_parts: List[ContentPart]) -> Tuple[str, List[ADKPart]]:
        """
        Process content parts, extracting text and handling multimodal content.
        Supports: text, image_url, audio_url, video_url, input_audio, file
        Returns tuple of (combined_text, adk_parts)
        """
        text_parts = []
        adk_parts = []

        logger.info(f"Starting multimodal processing for {len(content_parts)} content parts")

        for i, part in enumerate(content_parts):
            logger.info(f"Processing content part {i}: type={part.type}")

            if part.type == "text" and part.text:
                logger.info(f"Found text part: {part.text[:100]}...")
                text_parts.append(part.text)
                # Extract URLs from text for video/file processing
                urls = self._extract_urls_from_text(part.text)
                logger.info(f"Found {len(urls)} URLs in text: {urls}")
                for url in urls:
                    try:
                        logger.info(f"Attempting to download URL: {url}")
                        inline_data = await self._download_and_convert_url(url)
                        if inline_data:
                            logger.info(f"Successfully downloaded and converted URL: {inline_data.mimeType}")
                            adk_parts.append(ADKPart(inlineData=inline_data))
                        else:
                            logger.warning(f"Failed to download URL: {url} - no data returned")
                    except Exception as e:
                        logger.error(f"Failed to process URL {url}: {e}")

            elif part.type == "image_url" and part.image_url:
                await self._process_image_url(part.image_url.url, adk_parts)

            elif part.type == "audio_url" and part.audio_url:
                await self._process_audio_url(part.audio_url.url, adk_parts)

            elif part.type == "video_url" and part.video_url:
                await self._process_video_url(part.video_url.url, adk_parts)

            elif part.type == "input_audio" and part.input_audio:
                self._process_input_audio(part.input_audio, adk_parts)

            elif part.type == "file" and part.file:
                await self._process_file(part.file, adk_parts)

            else:
                logger.warning(f"Unsupported content part type: {part.type}")

        # Combine all text parts
        combined_text = " ".join(text_parts)

        # Add text part if we have combined text
        if combined_text.strip():
            adk_parts.insert(0, ADKPart(text=combined_text))

        return combined_text, adk_parts

    async def _process_image_url(self, url: str, adk_parts: List[ADKPart]):
        """处理图片URL（支持URL和Base64数据）"""
        logger.info(f"Found image_url part: {url[:100]}...")

        if url.startswith("data:"):
            try:
                logger.info(f"Processing Base64 image data")
                mime_type = None
                if ":" in url:
                    mime_type = url.split(":")[1].split(";")[0]

                inline_data = self.process_base64_file(url, "image", mime_type)
                if inline_data:
                    logger.info(f"Successfully processed Base64 image: {inline_data.mimeType}")
                    adk_parts.append(ADKPart(inlineData=inline_data))
                else:
                    logger.warning(f"Failed to process Base64 image data")
            except Exception as e:
                logger.error(f"Failed to process Base64 image: {e}")
        else:
            try:
                logger.info(f"Attempting to download image URL: {url}")
                inline_data = await self._download_and_convert_url(url)
                if inline_data:
                    logger.info(f"Successfully downloaded and converted image: {inline_data.mimeType}")
                    adk_parts.append(ADKPart(inlineData=inline_data))
                else:
                    logger.warning(f"Failed to download image URL: {url}")
            except Exception as e:
                logger.error(f"Failed to process image URL {url}: {e}")

    async def _process_audio_url(self, url: str, adk_parts: List[ADKPart]):
        """处理音频URL"""
        logger.info(f"Found audio_url part: {url[:100]}...")

        if url.startswith("data:"):
            try:
                logger.info(f"Processing Base64 audio data")
                mime_type = None
                if ":" in url:
                    mime_type = url.split(":")[1].split(";")[0]

                inline_data = self.process_base64_file(url, "audio", mime_type)
                if inline_data:
                    logger.info(f"Successfully processed Base64 audio: {inline_data.mimeType}")
                    adk_parts.append(ADKPart(inlineData=inline_data))
                else:
                    logger.warning(f"Failed to process Base64 audio data")
            except Exception as e:
                logger.error(f"Failed to process Base64 audio: {e}")
        else:
            try:
                logger.info(f"Attempting to download audio URL: {url}")
                inline_data = await self._download_and_convert_url(url)
                if inline_data:
                    logger.info(f"Successfully downloaded and converted audio: {inline_data.mimeType}")
                    adk_parts.append(ADKPart(inlineData=inline_data))
                else:
                    logger.warning(f"Failed to download audio URL: {url}")
            except Exception as e:
                logger.error(f"Failed to process audio URL {url}: {e}")

    async def _process_video_url(self, url: str, adk_parts: List[ADKPart]):
        """处理视频URL"""
        logger.info(f"Found video_url part: {url[:100]}...")

        if url.startswith("data:"):
            try:
                logger.info(f"Processing Base64 video data")
                mime_type = None
                if ":" in url:
                    mime_type = url.split(":")[1].split(";")[0]

                inline_data = self.process_base64_file(url, "video", mime_type)
                if inline_data:
                    logger.info(f"Successfully processed Base64 video: {inline_data.mimeType}")
                    adk_parts.append(ADKPart(inlineData=inline_data))
                else:
                    logger.warning(f"Failed to process Base64 video data")
            except Exception as e:
                logger.error(f"Failed to process Base64 video: {e}")
        else:
            try:
                logger.info(f"Attempting to download video URL: {url}")
                inline_data = await self._download_and_convert_url(url)
                if inline_data:
                    logger.info(f"Successfully downloaded and converted video: {inline_data.mimeType}")
                    adk_parts.append(ADKPart(inlineData=inline_data))
                else:
                    logger.warning(f"Failed to download video URL: {url}")
            except Exception as e:
                logger.error(f"Failed to process video URL {url}: {e}")

    def _process_input_audio(self, input_audio, adk_parts: List[ADKPart]):
        """处理 input_audio 类型（OpenAI格式的音频输入）"""
        logger.info(f"Found input_audio part, format: {input_audio.format}")

        try:
            # 根据format推断MIME类型
            format_to_mime = {
                "mp3": "audio/mpeg",
                "wav": "audio/wav",
                "flac": "audio/flac",
                "aac": "audio/aac",
                "ogg": "audio/ogg",
                "m4a": "audio/mp4",
                "webm": "audio/webm"
            }
            mime_type = format_to_mime.get(input_audio.format.lower(), f"audio/{input_audio.format}")

            # 创建内联数据
            inline_data = ADKInlineData(
                mimeType=mime_type,
                data=input_audio.data
            )
            logger.info(f"Successfully processed input_audio: {mime_type}")
            adk_parts.append(ADKPart(inlineData=inline_data))
        except Exception as e:
            logger.error(f"Failed to process input_audio: {e}")

    async def _process_file(self, file_content, adk_parts: List[ADKPart]):
        """处理通用文件类型"""
        logger.info(f"Found file part, filename: {file_content.filename}")

        try:
            # 优先使用Base64数据
            if file_content.data:
                mime_type = file_content.mime_type or "application/octet-stream"

                # 验证文件
                try:
                    file_data = base64.b64decode(file_content.data)
                    is_valid, error_msg, detected_mime = self.validate_file(
                        file_data,
                        file_content.filename or "file",
                        mime_type
                    )
                    if not is_valid:
                        logger.warning(f"File validation failed: {error_msg}")
                        return

                    # 重新编码确保格式正确
                    final_base64 = base64.b64encode(file_data).decode('utf-8')
                    inline_data = ADKInlineData(
                        mimeType=detected_mime,
                        data=final_base64
                    )
                    logger.info(f"Successfully processed file data: {detected_mime}")
                    adk_parts.append(ADKPart(inlineData=inline_data))
                except Exception as e:
                    logger.error(f"Failed to process file data: {e}")

            # 如果没有数据但有URL，尝试下载
            elif file_content.url:
                logger.info(f"Attempting to download file URL: {file_content.url}")
                inline_data = await self._download_and_convert_url(file_content.url)
                if inline_data:
                    logger.info(f"Successfully downloaded and converted file: {inline_data.mimeType}")
                    adk_parts.append(ADKPart(inlineData=inline_data))
                else:
                    logger.warning(f"Failed to download file URL: {file_content.url}")
            else:
                logger.warning("File content has neither data nor url")

        except Exception as e:
            logger.error(f"Failed to process file: {e}")
    
    def _extract_urls_from_text(self, text: str) -> List[str]:
        """Extract HTTP/HTTPS URLs and file paths from text using regex."""
        urls = []
        
        # Extract HTTP/HTTPS URLs
        http_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        http_urls = re.findall(http_pattern, text)
        urls.extend(http_urls)
        
        # Extract file:// URLs
        file_pattern = r'file://[^\s<>"{}|\\^`\[\]]+'
        file_urls = re.findall(file_pattern, text)
        urls.extend(file_urls)
        
        # Extract Windows file paths (e.g., D:\folder\file.txt)
        windows_pattern = r'[A-Za-z]:\\[^\s<>"{}|\\^`\[\]]+\.[A-Za-z0-9]+'
        windows_paths = re.findall(windows_pattern, text)
        urls.extend(windows_paths)
        
        logger.info(f"Extracted URLs: {urls}")
        return urls
    
    async def _download_and_convert_url(self, url: str) -> Optional[ADKInlineData]:
        """
        Download file from URL and convert to base64 inline data.
        Returns None if download fails or file is too large.
        """
        logger.info(f"Starting download of URL: {url}")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # First, get content type and file size with HEAD request
                logger.info(f"Sending HEAD request to: {url}")
                head_response = await client.head(url)
                logger.info(f"HEAD response status: {head_response.status_code}")
                
                content_type = head_response.headers.get('content-type', '')
                content_length = head_response.headers.get('content-length')
                
                logger.info(f"Content-Type: {content_type}, Content-Length: {content_length}")
                
                # Check file size
                if content_length and int(content_length) > self.max_file_size:
                    logger.warning(f"File too large: {content_length} bytes > {self.max_file_size} bytes")
                    return None
                
                # Download the file
                logger.info(f"Starting GET request to download file")
                response = await client.get(url)
                logger.info(f"GET response status: {response.status_code}")
                response.raise_for_status()
                
                # Check actual file size
                actual_size = len(response.content)
                logger.info(f"Downloaded {actual_size} bytes")
                
                if actual_size > self.max_file_size:
                    logger.warning(f"Downloaded file too large: {actual_size} bytes")
                    return None
                
                # Determine MIME type
                if not content_type:
                    content_type, _ = mimetypes.guess_type(url)
                    if not content_type:
                        # Default to binary if we can't determine the type
                        content_type = 'application/octet-stream'
                
                logger.info(f"Final MIME type: {content_type}")
                
                # Convert to base64
                base64_data = base64.b64encode(response.content).decode('utf-8')
                logger.info(f"Converted to base64, length: {len(base64_data)}")
                
                return ADKInlineData(
                    mimeType=content_type,
                    data=base64_data
                )
                
        except httpx.TimeoutException:
            logger.error(f"Timeout downloading URL: {url}")
            return None
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error downloading URL {url}: {e.response.status_code}")
            return None
        except Exception as e:
            logger.error(f"Error downloading URL {url}: {e}")
            return None