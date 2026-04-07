"""
API Test Script for Step 2 - Testing Video Model
Tests video model with fallback logging
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

from src.aliyun_client import aliyun_client, VideoRequest
from src.step2_config import VIDEO_MODEL_FALLBACKS

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TEST_VIDEO = Path(r"D:\smart_cliping\data\output\ANM_related\solution_1_exploration\segments\segment_010.mp4")
OUTPUT_DIR = Path(__file__).parent / "test_results"
OUTPUT_DIR.mkdir(exist_ok=True)


def extract_frame(video_path: Path, output_path: Path, timestamp: float = 0.5) -> bool:
    """Extract a frame from video"""
    try:
        cmd = [
            'ffmpeg', '-y',
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
            'ffmpeg', '-y',
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


async def test_video_model(video_path: Path) -> dict:
    """Test Video Model"""
    logger.info("=" * 50)
    logger.info("Testing Video Model...")
    logger.info(f"Fallback chain: {VIDEO_MODEL_FALLBACKS}")
    
    prompt = """请详细分析这个视频的内容，包括：
1. 视频的整体主题和主要内容
2. 重要的视觉元素和场景变化
3. 音频内容（对话、音乐、音效等）
4. 视频的情感氛围和节奏
5. 关键时刻或转折点
6. 视频的叙事结构（如果有）

请用150字以内的中文概括以上要点。"""
    
    request = VideoRequest(
        video_path=str(video_path),
        prompt=prompt,
        max_tokens=300,
        temperature=0.3
    )
    
    start_time = datetime.now()
    response = await aliyun_client.call_video_model(request)
    elapsed = (datetime.now() - start_time).total_seconds()
    
    result = {
        "test_type": "video",
        "timestamp": datetime.now().isoformat(),
        "input_file": str(video_path),
        "fallback_chain": VIDEO_MODEL_FALLBACKS,
        "model_used": response.model_used,
        "success": response.success,
        "content": response.content,
        "error": response.error,
        "usage": response.usage,
        "elapsed_seconds": elapsed
    }
    
    if response.success:
        logger.info(f"✓ Video Model SUCCESS - Used: {response.model_used}")
        logger.info(f"  Content: {response.content[:100]}...")
    else:
        logger.error(f"✗ Video Model FAILED - Error: {response.error}")
    
    logger.info(f"  Elapsed: {elapsed:.2f}s")
    
    return result


async def main():
    """Main test function"""
    logger.info("=" * 60)
    logger.info("Starting Video API Tests")
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
    
    video_result = await test_video_model(TEST_VIDEO)
    results["tests"].append(video_result)
    
    results["test_session"]["end_time"] = datetime.now().isoformat()
    
    success_count = sum(1 for t in results["tests"] if t.get("success"))
    total_count = len(results["tests"])
    results["test_session"]["summary"] = {
        "total_tests": total_count,
        "successful": success_count,
        "failed": total_count - success_count
    }
    
    output_file = OUTPUT_DIR / f"test_video_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    logger.info("=" * 60)
    logger.info("Test Summary")
    logger.info(f"  Total: {total_count}, Success: {success_count}, Failed: {total_count - success_count}")
    logger.info(f"  Results saved to: {output_file}")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
