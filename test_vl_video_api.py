"""
API Test Script for Step 2 - Testing Video Input to VL Models
Tests VL models with video input
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
import base64

sys.path.insert(0, str(Path(__file__).parent))

from src.aliyun_client import aliyun_client, VLRequest
from src.step2_config import VISION_MODEL_FALLBACKS

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TEST_VIDEO = Path(r"D:\smart_cliping\data\output\ANM_related\solution_1_exploration\segments\segment_010.mp4")
OUTPUT_DIR = Path(__file__).parent / "test_results"
OUTPUT_DIR.mkdir(exist_ok=True)


def compress_video_for_test(input_path: Path, output_path: Path, target_resolution: str = "1280x720") -> bool:
    """Compress video to a more manageable size for API testing"""
    try:
        ffmpeg_path = Path(__file__).parent / "ffmpeg" / "windows" / "bin" / "ffmpeg.exe"
        if not ffmpeg_path.exists():
            ffmpeg_path = "ffmpeg"  # fallback to system ffmpeg
        
        cmd = [
            str(ffmpeg_path), '-y',
            '-i', str(input_path),
            '-vf', f'scale={target_resolution}',  # Scale down resolution
            '-b:v', '1000k',  # Lower bitrate
            '-c:a', 'aac',  # Audio codec
            '-b:a', '64k',  # Lower audio bitrate
            '-movflags', '+faststart',  # Optimize for streaming
            str(output_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode == 0:
            compressed_size = output_path.stat().st_size / (1024 * 1024)
            logger.info(f"Compressed video size: {compressed_size:.2f} MB")
            return True
        else:
            logger.error(f"Compression failed: {result.stderr.decode()}")
            return False
    except Exception as e:
        logger.error(f"Video compression error: {e}")
        return False


async def test_vl_video_model(video_path: Path) -> dict:
    """Test VL Model with video input"""
    logger.info("=" * 50)
    logger.info("Testing VL Model with Video Input...")
    logger.info(f"Fallback chain: {VISION_MODEL_FALLBACKS}")
    
    prompt = """请详细分析这个视频的内容，包括：
1. 视频的整体主题和主要内容
2. 重要的视觉元素和场景变化
3. 音频内容（对话、音乐、音效等）
4. 视频的情感氛围和节奏
5. 关键时刻或转折点
6. 视频的叙事结构（如果有）

请用150字以内的中文概括以上要点。"""
    
    request = VLRequest(
        video_path=str(video_path),
        prompt=prompt,
        max_tokens=300,
        temperature=0.3
    )
    
    start_time = datetime.now()
    response = await aliyun_client.call_vl_model(request)
    elapsed = (datetime.now() - start_time).total_seconds()
    
    result = {
        "test_type": "vl_video",
        "timestamp": datetime.now().isoformat(),
        "input_file": str(video_path),
        "fallback_chain": VISION_MODEL_FALLBACKS,
        "model_used": response.model_used,
        "success": response.success,
        "content": response.content,
        "error": response.error,
        "usage": response.usage,
        "elapsed_seconds": elapsed
    }
    
    if response.success:
        logger.info(f"✓ VL Video Model SUCCESS - Used: {response.model_used}")
        logger.info(f"  Content: {response.content[:200]}...")
    else:
        logger.error(f"✗ VL Video Model FAILED - Error: {response.error}")
    
    logger.info(f"  Elapsed: {elapsed:.2f}s")
    
    return result


async def main():
    """Main test function"""
    logger.info("=" * 60)
    logger.info("Starting VL Video API Tests")
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
    
    # Compress video if needed
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        compressed_path = temp_path / "compressed_video.mp4"
        
        if compress_video_for_test(TEST_VIDEO, compressed_path):
            video_result = await test_vl_video_model(compressed_path)
            results["tests"].append(video_result)
        else:
            logger.error("Failed to compress video for testing")
            results["tests"].append({
                "test_type": "vl_video",
                "success": False,
                "error": "Failed to compress video"
            })
    
    results["test_session"]["end_time"] = datetime.now().isoformat()
    
    success_count = sum(1 for t in results["tests"] if t.get("success"))
    total_count = len(results["tests"])
    results["test_session"]["summary"] = {
        "total_tests": total_count,
        "successful": success_count,
        "failed": total_count - success_count
    }
    
    output_file = OUTPUT_DIR / f"test_vl_video_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    logger.info("=" * 60)
    logger.info("Test Summary")
    logger.info(f"  Total: {total_count}, Success: {success_count}, Failed: {total_count - success_count}")
    logger.info(f"  Results saved to: {output_file}")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
