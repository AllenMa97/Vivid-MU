"""
API Test Script for Step 2
Tests VL, Audio, and Text models with fallback logging
"""
import asyncio
import json
import logging
import sys
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import asdict

sys.path.insert(0, str(Path(__file__).parent))

from src.aliyun_client import aliyun_client, VLRequest, AudioRequest, TextRequest
from src.step2_config import (
    VISION_MODEL_FALLBACKS, AUDIO_MODEL_FALLBACKS, 
    TEXT_MODEL_FALLBACKS, FAST_MODEL_FALLBACKS
)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TEST_VIDEO = Path(r"D:\smart_cliping\data\output\ANM_related\solution_1_exploration\segments\segment_010.mp4")
OUTPUT_DIR = Path(__file__).parent / "test_results"
OUTPUT_DIR.mkdir(exist_ok=True)

FFMPEG_PATH = Path(__file__).parent / "ffmpeg" / "windows" / "bin" / "ffmpeg.exe"
if not FFMPEG_PATH.exists():
    FFMPEG_PATH = Path("ffmpeg")


def extract_frame(video_path: Path, output_path: Path, timestamp: float = 0.5) -> bool:
    """Extract a frame from video"""
    try:
        cmd = [
            str(FFMPEG_PATH), '-y',
            '-ss', str(timestamp),
            '-i', str(video_path),
            '-vframes', '1',
            '-q:v', '2',
            str(output_path)
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Frame extraction error: {e}")
        return False


def extract_audio(video_path: Path, output_path: Path) -> bool:
    """Extract audio from video"""
    try:
        cmd = [
            str(FFMPEG_PATH), '-y',
            '-i', str(video_path),
            '-vn',
            '-acodec', 'libmp3lame',
            '-q:a', '2',
            str(output_path)
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Audio extraction error: {e}")
        return False


async def test_vl_model(frame_path: Path) -> dict:
    """Test Vision Language Model"""
    logger.info("=" * 50)
    logger.info("Testing VL Model...")
    logger.info(f"Fallback chain: {VISION_MODEL_FALLBACKS}")
    
    prompt = """请分析这张视频截图，用简洁的中文描述画面内容。
请包含以下信息：
1. 场景类型（如：室内/室外/街道/自然风光等）
2. 主要物体或人物
3. 正在发生的动作或事件
4. 整体氛围或情绪
请用100字以内的中文回答。"""
    
    request = VLRequest(
        image_path=str(frame_path),
        prompt=prompt,
        max_tokens=200,
        temperature=0.3
    )
    
    start_time = datetime.now()
    response = await aliyun_client.call_vl_model(request)
    elapsed = (datetime.now() - start_time).total_seconds()
    
    result = {
        "test_type": "vision_language",
        "timestamp": datetime.now().isoformat(),
        "input_file": str(frame_path),
        "fallback_chain": VISION_MODEL_FALLBACKS,
        "model_used": response.model_used,
        "success": response.success,
        "content": response.content,
        "error": response.error,
        "usage": response.usage,
        "elapsed_seconds": elapsed
    }
    
    if response.success:
        logger.info(f"✓ VL Model SUCCESS - Used: {response.model_used}")
        logger.info(f"  Content: {response.content[:100]}...")
    else:
        logger.error(f"✗ VL Model FAILED - Error: {response.error}")
    
    logger.info(f"  Elapsed: {elapsed:.2f}s")
    
    return result


async def test_audio_model(audio_path: Path) -> dict:
    """Test Audio Model"""
    logger.info("=" * 50)
    logger.info("Testing Audio Model...")
    logger.info(f"Fallback chain: {AUDIO_MODEL_FALLBACKS}")
    
    prompt = """请分析这段音频，用简洁的中文描述音频内容。
请包含以下信息：
1. 主要声音类型（如：人声/音乐/环境音等）
2. 如果有人声，请描述大致内容或情绪
3. 整体音频氛围
请用100字以内的中文回答。"""
    
    request = AudioRequest(
        audio_path=str(audio_path),
        prompt=prompt,
        max_tokens=200,
        temperature=0.3
    )
    
    start_time = datetime.now()
    response = await aliyun_client.call_audio_model(request)
    elapsed = (datetime.now() - start_time).total_seconds()
    
    result = {
        "test_type": "audio",
        "timestamp": datetime.now().isoformat(),
        "input_file": str(audio_path),
        "fallback_chain": AUDIO_MODEL_FALLBACKS,
        "model_used": response.model_used,
        "success": response.success,
        "content": response.content,
        "error": response.error,
        "usage": response.usage,
        "elapsed_seconds": elapsed
    }
    
    if response.success:
        logger.info(f"✓ Audio Model SUCCESS - Used: {response.model_used}")
        logger.info(f"  Content: {response.content[:100]}...")
    else:
        logger.error(f"✗ Audio Model FAILED - Error: {response.error}")
    
    logger.info(f"  Elapsed: {elapsed:.2f}s")
    
    return result


async def test_text_model() -> dict:
    """Test Text Model"""
    logger.info("=" * 50)
    logger.info("Testing Text Model...")
    logger.info(f"Fallback chain: {TEXT_MODEL_FALLBACKS}")
    
    request = TextRequest(
        system_prompt="你是一个视频内容分析专家。",
        user_prompt="请用一句话描述什么是'视频精彩片段提取'？",
        max_tokens=100,
        temperature=0.3
    )
    
    start_time = datetime.now()
    response = await aliyun_client.call_text_model(request)
    elapsed = (datetime.now() - start_time).total_seconds()
    
    result = {
        "test_type": "text",
        "timestamp": datetime.now().isoformat(),
        "fallback_chain": TEXT_MODEL_FALLBACKS,
        "model_used": response.model_used,
        "success": response.success,
        "content": response.content,
        "error": response.error,
        "usage": response.usage,
        "elapsed_seconds": elapsed
    }
    
    if response.success:
        logger.info(f"✓ Text Model SUCCESS - Used: {response.model_used}")
        logger.info(f"  Content: {response.content}")
    else:
        logger.error(f"✗ Text Model FAILED - Error: {response.error}")
    
    logger.info(f"  Elapsed: {elapsed:.2f}s")
    
    return result


async def test_fast_model() -> dict:
    """Test Fast Model"""
    logger.info("=" * 50)
    logger.info("Testing Fast Model...")
    logger.info(f"Fallback chain: {FAST_MODEL_FALLBACKS}")
    
    request = TextRequest(
        system_prompt="你是一个关键词提取专家。",
        user_prompt="从以下文本中提取3个关键词：'今天天气很好，阳光明媚，适合出门散步'",
        max_tokens=50,
        temperature=0.1
    )
    
    start_time = datetime.now()
    response = await aliyun_client.call_fast_model(request)
    elapsed = (datetime.now() - start_time).total_seconds()
    
    result = {
        "test_type": "fast_text",
        "timestamp": datetime.now().isoformat(),
        "fallback_chain": FAST_MODEL_FALLBACKS,
        "model_used": response.model_used,
        "success": response.success,
        "content": response.content,
        "error": response.error,
        "usage": response.usage,
        "elapsed_seconds": elapsed
    }
    
    if response.success:
        logger.info(f"✓ Fast Model SUCCESS - Used: {response.model_used}")
        logger.info(f"  Content: {response.content}")
    else:
        logger.error(f"✗ Fast Model FAILED - Error: {response.error}")
    
    logger.info(f"  Elapsed: {elapsed:.2f}s")
    
    return result


async def main():
    """Main test function"""
    logger.info("=" * 60)
    logger.info("Starting API Tests")
    logger.info(f"Test video: {TEST_VIDEO}")
    logger.info(f"Output directory: {OUTPUT_DIR}")
    logger.info("=" * 60)
    
    if not TEST_VIDEO.exists():
        logger.error(f"Test video not found: {TEST_VIDEO}")
        return
    
    results = {
        "test_session": {
            "start_time": datetime.now().isoformat(),
            "test_video": str(TEST_VIDEO),
            "output_dir": str(OUTPUT_DIR)
        },
        "tests": []
    }
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        frame_path = temp_path / "frame.jpg"
        audio_path = temp_path / "audio.mp3"
        
        logger.info("Extracting frame from video...")
        has_frame = extract_frame(TEST_VIDEO, frame_path, timestamp=1.0)
        if has_frame:
            logger.info(f"  Frame extracted: {frame_path}")
        else:
            logger.warning("  Frame extraction failed!")
        
        logger.info("Extracting audio from video...")
        has_audio = extract_audio(TEST_VIDEO, audio_path)
        if has_audio:
            logger.info(f"  Audio extracted: {audio_path}")
        else:
            logger.warning("  Audio extraction failed!")
        
        if has_frame:
            vl_result = await test_vl_model(frame_path)
            results["tests"].append(vl_result)
        else:
            results["tests"].append({
                "test_type": "vision_language",
                "success": False,
                "error": "Frame extraction failed"
            })
        
        if has_audio:
            audio_result = await test_audio_model(audio_path)
            results["tests"].append(audio_result)
        else:
            results["tests"].append({
                "test_type": "audio",
                "success": False,
                "error": "Audio extraction failed"
            })
        
        text_result = await test_text_model()
        results["tests"].append(text_result)
        
        fast_result = await test_fast_model()
        results["tests"].append(fast_result)
    
    results["test_session"]["end_time"] = datetime.now().isoformat()
    
    success_count = sum(1 for t in results["tests"] if t.get("success"))
    total_count = len(results["tests"])
    results["test_session"]["summary"] = {
        "total_tests": total_count,
        "successful": success_count,
        "failed": total_count - success_count
    }
    
    output_file = OUTPUT_DIR / f"test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    logger.info("=" * 60)
    logger.info("Test Summary")
    logger.info(f"  Total: {total_count}, Success: {success_count}, Failed: {total_count - success_count}")
    logger.info(f"  Results saved to: {output_file}")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
