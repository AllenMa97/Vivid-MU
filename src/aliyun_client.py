"""
Aliyun API Client for Step 2 Filter
Supports: Qwen-VL (Vision Language), Qwen-Audio, Qwen-Max (Text LLM)
With model fallback support
"""
import asyncio
import base64
import json
import logging
import mimetypes
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass
import httpx

from .step2_config import (
    ALIYUN_API_BASE, ALIYUN_API_KEY,
    VISION_MODEL_FALLBACKS, AUDIO_MODEL_FALLBACKS, 
    TEXT_MODEL_FALLBACKS, FAST_MODEL_FALLBACKS,
    MAX_CONCURRENT_REQUESTS, REQUEST_TIMEOUT, MAX_RETRIES,
    OUTPUT_LANGUAGE
)

logger = logging.getLogger(__name__)

OMNI_MODELS = [
    "qwen3-omni-flash",
    "qwen3-omni-flash-2025-09-15", 
    "qwen3-omni-flash-2025-12-01",
    "qwen3-omni-flash-realtime"
]

DASHSCOPE_MULTIMODAL_API = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

AUDIO_ONLY_MODELS = [
    "qwen-audio-turbo",
    "qwen-audio-turbo-latest",
    "qwen2-audio-instruct"
]


@dataclass
class VLRequest:
    """Vision Language Model Request"""
    image_path: Optional[str] = None
    image_base64: Optional[str] = None
    video_path: Optional[str] = None  # Support video input
    video_base64: Optional[str] = None  # Support video input
    prompt: str = ""
    max_tokens: int = 500
    temperature: float = 0.3


@dataclass
class AudioRequest:
    """Audio Model Request"""
    audio_path: Optional[str] = None
    audio_base64: Optional[str] = None
    prompt: str = ""
    max_tokens: int = 500
    temperature: float = 0.3


@dataclass
class VideoRequest:
    """Video Model Request for Qwen-Omni series"""
    video_path: Optional[str] = None
    video_base64: Optional[str] = None
    prompt: str = ""
    max_tokens: int = 1000
    temperature: float = 0.3


@dataclass
class TextRequest:
    """Text LLM Request"""
    system_prompt: str = ""
    user_prompt: str = ""
    max_tokens: int = 2000
    temperature: float = 0.3
    response_format: Optional[Dict] = None


@dataclass
class APIResponse:
    """API Response"""
    success: bool
    content: str
    error: Optional[str] = None
    usage: Optional[Dict] = None
    model_used: Optional[str] = None


class AliyunClient:
    """Aliyun API Client with rate limiting, retry logic and model fallback"""
    
    def __init__(self):
        self.api_base = ALIYUN_API_BASE
        self.api_key = ALIYUN_API_KEY
        self.timeout = REQUEST_TIMEOUT
        self.max_retries = MAX_RETRIES
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        
    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
    
    def _get_mime_type(self, file_path: str) -> str:
        """Get MIME type for file"""
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type:
            return mime_type
        ext = Path(file_path).suffix.lower()
        mime_map = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav',
            '.m4a': 'audio/mp4',
            '.aac': 'audio/aac',
            '.mp4': 'video/mp4',
        }
        return mime_map.get(ext, 'application/octet-stream')
    
    def _encode_file_to_base64(self, file_path: str) -> Tuple[str, str]:
        """Encode file to base64 and return (base64_data, mime_type)"""
        with open(file_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        mime_type = self._get_mime_type(file_path)
        return data, mime_type
    
    async def _make_request_with_fallback(
        self,
        messages: List[Dict],
        model_fallbacks: List[str],
        max_tokens: int = 2000,
        temperature: float = 0.3,
        response_format: Optional[Dict] = None,
        use_stream: bool = False
    ) -> APIResponse:
        """Make API request with model fallback logic (OpenAI compatible format)"""
        
        last_error = None
        
        for model in model_fallbacks:
            is_omni_model = model in OMNI_MODELS
            need_stream = use_stream or is_omni_model
            
            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            
            if need_stream:
                payload["stream"] = True
            
            if response_format:
                payload["response_format"] = response_format
            
            async with self._semaphore:
                for attempt in range(self.max_retries):
                    try:
                        async with httpx.AsyncClient(timeout=self.timeout) as client:
                            if need_stream:
                                response = await client.post(
                                    self.api_base,
                                    headers=self._get_headers(),
                                    json=payload
                                )
                                
                                if response.status_code == 200:
                                    content = await self._parse_stream_response(response)
                                    return APIResponse(
                                        success=True,
                                        content=content,
                                        model_used=model
                                    )
                            else:
                                response = await client.post(
                                    self.api_base,
                                    headers=self._get_headers(),
                                    json=payload
                                )
                                
                                if response.status_code == 200:
                                    result = response.json()
                                    content = result["choices"][0]["message"]["content"]
                                    usage = result.get("usage", {})
                                    return APIResponse(
                                        success=True,
                                        content=content,
                                        usage=usage,
                                        model_used=model
                                    )
                            
                            if response.status_code == 429:
                                wait_time = 2 ** attempt
                                logger.warning(f"Rate limited for model {model}, waiting {wait_time}s...")
                                await asyncio.sleep(wait_time)
                            elif response.status_code in [400, 401, 403, 404]:
                                error_msg = f"Model {model} error: {response.status_code} - {response.text}"
                                logger.warning(error_msg)
                                last_error = error_msg
                                break
                            else:
                                error_msg = f"API error for model {model}: {response.status_code} - {response.text}"
                                logger.warning(error_msg)
                                last_error = error_msg
                                if attempt < self.max_retries - 1:
                                    await asyncio.sleep(1)
                                    
                    except httpx.TimeoutException:
                        logger.warning(f"Request timeout for model {model}, attempt {attempt + 1}")
                        last_error = f"Timeout for model {model}"
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(1)
                            
                    except Exception as e:
                        error_msg = f"Request error for model {model}: {str(e)}"
                        logger.error(error_msg)
                        last_error = error_msg
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(1)
        
        return APIResponse(success=False, content="", error=last_error or "All models failed")
    
    async def _make_dashscope_multimodal_request(
        self,
        content_items: List[Dict],
        model_fallbacks: List[str],
        system_prompt: Optional[str] = None
    ) -> APIResponse:
        """Make DashScope native multimodal API request (for qwen-audio-turbo)"""
        
        last_error = None
        
        for model in model_fallbacks:
            messages = []
            
            if system_prompt:
                messages.append({
                    "role": "system",
                    "content": [{"text": system_prompt}]
                })
            
            messages.append({
                "role": "user",
                "content": content_items
            })
            
            payload = {
                "model": model,
                "input": {
                    "messages": messages
                }
            }
            
            async with self._semaphore:
                for attempt in range(self.max_retries):
                    try:
                        async with httpx.AsyncClient(timeout=self.timeout) as client:
                            response = await client.post(
                                DASHSCOPE_MULTIMODAL_API,
                                headers=self._get_headers(),
                                json=payload
                            )
                            
                            if response.status_code == 200:
                                result = response.json()
                                try:
                                    content = result["output"]["choices"][0]["message"]["content"][0]["text"]
                                    usage = result.get("usage", {})
                                    return APIResponse(
                                        success=True,
                                        content=content,
                                        usage=usage,
                                        model_used=model
                                    )
                                except (KeyError, IndexError) as e:
                                    error_msg = f"Unexpected response format: {result}"
                                    logger.error(error_msg)
                                    last_error = error_msg
                                    break
                            
                            if response.status_code == 429:
                                wait_time = 2 ** attempt
                                logger.warning(f"Rate limited for model {model}, waiting {wait_time}s...")
                                await asyncio.sleep(wait_time)
                            elif response.status_code in [400, 401, 403, 404]:
                                error_msg = f"Model {model} error: {response.status_code} - {response.text}"
                                logger.warning(error_msg)
                                last_error = error_msg
                                break
                            else:
                                error_msg = f"API error for model {model}: {response.status_code} - {response.text}"
                                logger.warning(error_msg)
                                last_error = error_msg
                                if attempt < self.max_retries - 1:
                                    await asyncio.sleep(1)
                                    
                    except httpx.TimeoutException:
                        logger.warning(f"Request timeout for model {model}, attempt {attempt + 1}")
                        last_error = f"Timeout for model {model}"
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(1)
                            
                    except Exception as e:
                        error_msg = f"Request error for model {model}: {str(e)}"
                        logger.error(error_msg)
                        last_error = error_msg
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(1)
        
        return APIResponse(success=False, content="", error=last_error or "All models failed")
    
    async def _parse_stream_response(self, response) -> str:
        """Parse streaming response and collect content"""
        content_parts = []
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    if "choices" in data and len(data["choices"]) > 0:
                        delta = data["choices"][0].get("delta", {})
                        if "content" in delta:
                            content_parts.append(delta["content"])
                        # Also check for audio content in Qwen-Omni responses
                        elif hasattr(delta, 'audio') and delta.get('audio'):
                            audio_data = delta['audio'].get('data', '')
                            if audio_data:
                                content_parts.append("[AUDIO]")
                except json.JSONDecodeError:
                    continue
        return "".join(content_parts)
    
    async def call_vl_model(self, request: VLRequest) -> APIResponse:
        """Call Vision Language Model for image/video understanding with fallback"""
        
        # Check if video input is provided
        if request.video_path or request.video_base64:
            # Handle video input
            if request.video_path:
                video_data, mime_type = self._encode_file_to_base64(request.video_path)
            elif request.video_base64:
                video_data = request.video_base64
                mime_type = "video/mp4"
            else:
                return APIResponse(success=False, content="", error="No video provided")
            
            video_url = f"data:{mime_type};base64,{video_data}"
            
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": request.prompt},
                        {"type": "video_url", "video_url": {"url": video_url}}
                    ]
                }
            ]
            
        elif request.image_path or request.image_base64:
            # Handle image input (existing functionality)
            if request.image_path:
                image_data, mime_type = self._encode_file_to_base64(request.image_path)
            elif request.image_base64:
                image_data = request.image_base64
                mime_type = "image/jpeg"
            else:
                return APIResponse(success=False, content="", error="No image provided")
            
            image_url = f"data:{mime_type};base64,{image_data}"
            
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": request.prompt},
                        {"type": "image_url", "image_url": {"url": image_url}}
                    ]
                }
            ]
        else:
            return APIResponse(success=False, content="", error="No image or video provided")
        
        return await self._make_request_with_fallback(
            messages=messages,
            model_fallbacks=VISION_MODEL_FALLBACKS,
            max_tokens=request.max_tokens,
            temperature=request.temperature
        )
    
    async def call_audio_model(self, request: AudioRequest) -> APIResponse:
        """Call Audio Model for audio understanding with fallback
        
        Uses DashScope native multimodal API for qwen-audio-turbo models.
        Note: Qwen-Omni models do NOT support audio-only input.
        """
        
        if request.audio_path:
            audio_data, _ = self._encode_file_to_base64(request.audio_path)
        elif request.audio_base64:
            audio_data = request.audio_base64
        else:
            return APIResponse(success=False, content="", error="No audio provided")
        
        audio_only_fallbacks = [m for m in AUDIO_MODEL_FALLBACKS if m in AUDIO_ONLY_MODELS]
        
        if not audio_only_fallbacks:
            return APIResponse(
                success=False, 
                content="", 
                error="No audio-only models available in fallback list. Qwen-Omni models do not support audio-only input."
            )
        
        content_items = [
            {"audio": f"data:;base64,{audio_data}"},
            {"text": request.prompt}
        ]
        
        return await self._make_dashscope_multimodal_request(
            content_items=content_items,
            model_fallbacks=audio_only_fallbacks
        )
    
    async def call_video_model(self, request: VideoRequest) -> APIResponse:
        """Call Video Model for video understanding with fallback
        
        Uses OpenAI-compatible API for Qwen-Omni series that support direct video input.
        For local files, we use file:// URL format which DashScope supports.
        """
        
        if request.video_path:
            # Use file:// URL for local files which is more efficient than Base64
            video_url = f"file://{Path(request.video_path).absolute()}"
        elif request.video_base64:
            # Fallback to Base64 if provided
            video_url = f"data:;base64,{request.video_base64}"
        else:
            return APIResponse(success=False, content="", error="No video provided")
        
        # Filter for Qwen-Omni models that support video input
        video_fallbacks = [
            m for m in AUDIO_MODEL_FALLBACKS 
            if m in ["qwen3-omni-flash", "qwen3-omni-flash-2025-09-15", 
                    "qwen3-omni-flash-2025-12-01", "qwen3-omni-flash-realtime",
                    "qwen3.5-omni-plus", "qwen3.5-omni-max", "qwen3.5-omni-turbo"]
        ]
        
        if not video_fallbacks:
            # Use the first available model as fallback
            video_fallbacks = ["qwen3-omni-flash"]
        
        # Prepare messages with video input
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "video_url",
                        "video_url": {"url": video_url}
                    },
                    {"type": "text", "text": request.prompt}
                ]
            }
        ]
        
        # Call with streaming required for Qwen-Omni
        return await self._make_request_with_fallback(
            messages=messages,
            model_fallbacks=video_fallbacks,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            use_stream=True
        )
    
    async def call_text_model(self, request: TextRequest) -> APIResponse:
        """Call Text LLM for text understanding with fallback"""
        
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.user_prompt})
        
        return await self._make_request_with_fallback(
            messages=messages,
            model_fallbacks=TEXT_MODEL_FALLBACKS,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            response_format=request.response_format
        )
    
    async def call_fast_model(self, request: TextRequest) -> APIResponse:
        """Call Fast Model for simple tasks with fallback"""
        
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.user_prompt})
        
        return await self._make_request_with_fallback(
            messages=messages,
            model_fallbacks=FAST_MODEL_FALLBACKS,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            response_format=request.response_format
        )


aliyun_client = AliyunClient()
