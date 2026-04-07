"""
Step 2 Filter Entry Point
Run semantic analysis and LLM selection on step 1 results
"""
import sys
import asyncio
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.config import config
from src.step2_filter import run_step2_filter

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def setup_file_logging():
    """Setup file logging for step 2"""
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"step2_{timestamp}.log"
    
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
    logger.info("Step 2 Filter - Semantic Analysis & LLM Selection")
    logger.info("=" * 60)
    logger.info(f"Log file: {log_file}")
    
    input_dir = Path(config.input_dir)
    output_dir = Path(config.output_dir)
    
    target_duration = config.selector.target_duration / 60  # Convert to minutes
    
    video_dirs = [d for d in output_dir.iterdir() 
                  if d.is_dir() and d.name.endswith("_related")]
    
    if not video_dirs:
        logger.warning(f"No step 1 results found in {output_dir}")
        logger.info("Please run step 1 (main.py) first")
        return
    
    logger.info(f"Found {len(video_dirs)} videos with step 1 results")
    
    for video_dir in video_dirs:
        video_name = video_dir.name.replace("_related", "")
        logger.info(f"\nProcessing: {video_name}")
        
        step2_output_dir = output_dir / f"{video_name}_related"
        
        results = await run_step2_filter(
            step1_output_dir=video_dir,
            output_base_dir=step2_output_dir,
            video_name=video_name,
            target_duration=target_duration
        )
        
        if results:
            logger.info(f"Step 2 complete for {video_name}")
            for result in results:
                logger.info(f"  - {result.total_input_segments} segments processed")
                logger.info(f"  - {len(result.selection_results)} selection schemes generated")
        else:
            logger.warning(f"No results for {video_name}")
    
    logger.info("=" * 60)
    logger.info("Step 2 Filter Complete")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
