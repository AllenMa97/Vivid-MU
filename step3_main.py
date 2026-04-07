"""
Step 3 Editor Entry Point
Run video editing, effects, and post-production on step 2 results
"""
import asyncio
import logging
from pathlib import Path
from datetime import datetime

from src.config import config
from src.step3_editor import run_step3_editor

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def setup_file_logging():
    """Setup file logging for step 3"""
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"step3_{timestamp}.log"
    
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    )
    
    logging.getLogger().addHandler(file_handler)
    return log_file


async def main():
    log_file = setup_file_logging()
    
    logger.info("=" * 60)
    logger.info("Step 3 Editor - Video Editing & Post-Production")
    logger.info("=" * 60)
    logger.info(f"Log file: {log_file}")
    
    input_dir = Path(config.input_dir)
    output_dir = Path(config.output_dir)
    
    # Look for step 2 results
    video_dirs = [d for d in output_dir.iterdir() 
                  if d.is_dir() and d.name.endswith("_related")]
    
    if not video_dirs:
        logger.warning(f"No video directories found in {output_dir}")
        logger.info("Please run step 1 and step 2 first")
        return
    
    logger.info(f"Found {len(video_dirs)} videos with step 2 results")
    
    for video_dir in video_dirs:
        video_name = video_dir.name.replace("_related", "")
        logger.info(f"\nProcessing: {video_name}")
        
        # Use the ANM_step2 directory as input
        step2_output_dir = video_dir / "ANM_step2"
        if not step2_output_dir.exists():
            logger.warning(f"No step 2 results found in {step2_output_dir}")
            continue
        
        step3_output_dir = video_dir / f"{video_name}_step3"
        
        # Optional: specify BGM path
        bgm_path = getattr(config, 'bgm_path', None)
        if bgm_path:
            bgm_path = Path(bgm_path)
            if not bgm_path.exists():
                logger.warning(f"BGM file not found: {bgm_path}")
                bgm_path = None
        
        # Effects configuration
        effects_config = {
            "add_effects": True,
            "transition_duration": 0.5,
            "bgm_volume": 0.6,
            "brightness": 0,
            "contrast": 1.1,
            "saturation": 1.1
        }
        
        results = await run_step3_editor(
            step2_output_dir=step2_output_dir,
            output_base_dir=step3_output_dir,
            video_name=video_name,
            bgm_path=bgm_path,
            effects_config=effects_config
        )
        
        if results:
            logger.info(f"Step 3 complete for {video_name}")
            for result in results:
                logger.info(f"  - Scheme {result.scheme_id} ({result.scheme_name}): {result.duration:.1f}s video created")
        else:
            logger.warning(f"No results for {video_name}")
    
    logger.info("=" * 60)
    logger.info("Step 3 Editor Complete")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())