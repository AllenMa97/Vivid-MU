"""
Step 3: Video Editing and Post-Production
Takes selected segments and creates final polished video with effects, BGM, etc.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
import shutil

from .video_editor import VideoEditor
from .ai_video_editor import AIVideoEditor, create_ai_enhanced_video
from .step2_filter import Step2Result

logger = logging.getLogger(__name__)


@dataclass
class Step3Result:
    """Result of step 3 processing"""
    scheme_id: int
    scheme_name: str
    input_segments: List[str]
    output_video_path: str
    duration: float
    processing_time: float
    effects_applied: List[str]
    bgm_used: Optional[str]


class Step3Editor:
    """Step 3: Video Editing and Post-Production"""
    
    def __init__(self, output_base_dir: Path):
        self.output_base_dir = Path(output_base_dir)
        self.output_base_dir.mkdir(parents=True, exist_ok=True)
        
    async def process_scheme(
        self,
        scheme_dir: Path,
        scheme_id: int,
        scheme_name: str,
        bgm_path: Optional[Path] = None,
        effects_config: Optional[Dict] = None
    ) -> Optional[Step3Result]:
        """Process a single scheme to create final edited video"""
        
        logger.info(f"Processing scheme {scheme_id} ({scheme_name}) for video editing")
        
        # Get selected segments
        selected_segments_dir = scheme_dir / "selected_segments"
        if not selected_segments_dir.exists():
            logger.warning(f"No selected segments found in {selected_segments_dir}")
            return None
        
        segment_files = sorted(list(selected_segments_dir.glob("*.mp4")))
        if not segment_files:
            logger.warning(f"No segment files found in {selected_segments_dir}")
            return None
        
        logger.info(f"Found {len(segment_files)} segments for scheme {scheme_id}")
        
        # Create output directory for edited video under step3 folder
        step3_dir = self.output_base_dir / "step3_output"
        edited_dir = step3_dir / f"edited_{scheme_name}"
        edited_dir.mkdir(parents=True, exist_ok=True)
        
        # Create output video path
        output_video_path = edited_dir / f"highlights_{scheme_name}.mp4"
        
        # Determine effects to apply
        effects_to_apply = ["concatenation"]
        if bgm_path and bgm_path.exists():
            effects_to_apply.append("bgm_mixing")
        if effects_config:
            if effects_config.get("add_effects", False):
                effects_to_apply.extend(["brightness_contrast", "transitions"])
        
        # Create edited video using traditional method first
        start_time = asyncio.get_event_loop().time()
        
        # Initialize traditional video editor
        editor = VideoEditor(edited_dir)
        
        success = editor.create_highlights_reel(
            segment_paths=segment_files,
            output_path=output_video_path,
            bgm_path=bgm_path,
            transition_duration=effects_config.get("transition_duration", 0.5) if effects_config else 0.5,
            volume_adjust=effects_config.get("bgm_volume", 0.7) if effects_config else 0.7
        )
        
        if success:
            # Apply AI enhancement to the created video
            ai_enhanced_path = edited_dir / f"ai_enhanced_highlights_{scheme_name}.mp4"
            
            # Determine AI enhancement style based on scheme name
            if "balanced" in scheme_name.lower():
                ai_style = "professional"
            elif "engaging" in scheme_name.lower():
                ai_style = "dynamic"
            elif "emotional" in scheme_name.lower():
                ai_style = "cinematic"
            else:
                ai_style = "dynamic"  # Default style
            
            logger.info(f"Applying AI enhancement with style: {ai_style}")
            
            ai_result = await create_ai_enhanced_video(
                input_video_path=output_video_path,
                output_dir=edited_dir,
                edit_style=ai_style
            )
            
            if ai_result.success:
                # Use the AI-enhanced video as the final output
                output_video_path = Path(ai_result.output_path)
                effects_to_apply.append("ai_enhancement")
                logger.info(f"AI enhancement applied successfully: {output_video_path}")
            else:
                logger.warning(f"AI enhancement failed: {ai_result.error}, using original edited video")
        
        processing_time = asyncio.get_event_loop().time() - start_time
        
        if not success:
            logger.error(f"Failed to create edited video for scheme {scheme_id}")
            return None
        
        # Get video duration
        duration = self._get_video_duration(output_video_path)
        
        result = Step3Result(
            scheme_id=scheme_id,
            scheme_name=scheme_name,
            input_segments=[str(f) for f in segment_files],
            output_video_path=str(output_video_path),
            duration=duration,
            processing_time=processing_time,
            effects_applied=effects_to_apply,
            bgm_used=str(bgm_path) if bgm_path else None
        )
        
        logger.info(f"Successfully created edited video for scheme {scheme_id}: {output_video_path}")
        logger.info(f"  Duration: {duration:.2f}s, Processing time: {processing_time:.2f}s")
        
        return result
    
    def _get_video_duration(self, video_path: Path) -> float:
        """Get video duration using ffprobe"""
        try:
            import subprocess
            import json
            
            # Use ffprobe to get video duration
            ffmpeg_path = Path(__file__).parent.parent / "ffmpeg" / "windows" / "bin"
            ffprobe_path = ffmpeg_path / "ffprobe.exe"
            
            if not ffprobe_path.exists():
                ffprobe_path = "ffprobe"  # Use system ffprobe
            
            cmd = [
                str(ffprobe_path),
                '-v', 'quiet',
                '-show_entries', 'format=duration',
                '-of', 'json',
                str(video_path)
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                duration = float(data['format']['duration'])
                return duration
            else:
                logger.warning(f"Could not get video duration: {result.stderr}")
                return 0.0
        except Exception as e:
            logger.warning(f"Error getting video duration: {e}")
            return 0.0
    
    async def process_all_schemes(
        self,
        step2_output_dir: Path,
        bgm_path: Optional[Path] = None,
        effects_config: Optional[Dict] = None
    ) -> List[Step3Result]:
        """Process all schemes from step 2 to create edited videos"""
        
        logger.info(f"Starting Step 3 processing for schemes in {step2_output_dir}")
        
        # Find all scheme directories - they might be in a subdirectory
        scheme_dirs = []
        
        # First check if schemes are directly in step2_output_dir
        for item in step2_output_dir.iterdir():
            if item.is_dir() and ("scheme" in item.name.lower() or "balanced" in item.name.lower() or "engaging" in item.name.lower() or "emotional" in item.name.lower()):
                scheme_dirs.append(item)
        
        # If not found, check subdirectories
        if not scheme_dirs:
            for subdir in step2_output_dir.iterdir():
                if subdir.is_dir():
                    for item in subdir.iterdir():
                        if item.is_dir() and ("scheme" in item.name.lower() or "balanced" in item.name.lower() or "engaging" in item.name.lower() or "emotional" in item.name.lower()):
                            scheme_dirs.append(item)
        
        if not scheme_dirs:
            logger.warning(f"No scheme directories found in {step2_output_dir} or its subdirectories")
            return []
        
        logger.info(f"Found {len(scheme_dirs)} schemes to process")
        
        results = []
        for i, scheme_dir in enumerate(scheme_dirs):
            # Extract scheme ID and name from directory name
            dir_name = scheme_dir.name
            if "_" in dir_name:
                parts = dir_name.split("_")
                if len(parts) >= 2:
                    try:
                        scheme_id = int(parts[1])
                        scheme_name = "_".join(parts[2:]) if len(parts) > 2 else parts[1]
                    except ValueError:
                        scheme_id = i + 1
                        scheme_name = dir_name
                else:
                    scheme_id = i + 1
                    scheme_name = dir_name
            else:
                scheme_id = i + 1
                scheme_name = dir_name
            
            result = await self.process_scheme(
                scheme_dir=scheme_dir,
                scheme_id=scheme_id,
                scheme_name=scheme_name,
                bgm_path=bgm_path,
                effects_config=effects_config
            )
            
            if result:
                results.append(result)
        
        logger.info(f"Step 3 processing complete. Created {len(results)} edited videos.")
        return results
    
    def save_final_report(self, results: List[Step3Result], output_path: Path) -> None:
        """Save final report of step 3 processing"""
        report = {
            "processing_timestamp": "TODO",  # Would add actual timestamp
            "total_videos_created": len(results),
            "results": [
                {
                    "scheme_id": r.scheme_id,
                    "scheme_name": r.scheme_name,
                    "output_video_path": r.output_video_path,
                    "duration": r.duration,
                    "processing_time": r.processing_time,
                    "effects_applied": r.effects_applied,
                    "bgm_used": r.bgm_used
                }
                for r in results
            ]
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Step 3 report saved to: {output_path}")


async def run_step3_editor(
    step2_output_dir: Path,
    output_base_dir: Path,
    video_name: str,
    bgm_path: Optional[Path] = None,
    effects_config: Optional[Dict] = None
) -> List[Step3Result]:
    """Run step 3 editor to create final edited videos"""
    
    logger.info("=" * 60)
    logger.info(f"Step 3 Editor - Creating Final Edited Videos for {video_name}")
    logger.info("=" * 60)
    logger.info(f"Input: {step2_output_dir}")
    logger.info(f"Output base: {output_base_dir}")
    logger.info(f"BGM: {bgm_path if bgm_path else 'None'}")
    
    editor = Step3Editor(output_base_dir)
    
    results = await editor.process_all_schemes(
        step2_output_dir=step2_output_dir,
        bgm_path=bgm_path,
        effects_config=effects_config
    )
    
    # Save report
    report_path = output_base_dir / f"{video_name}_step3_report.json"
    editor.save_final_report(results, report_path)
    
    logger.info("=" * 60)
    logger.info(f"Step 3 Editor Complete for {video_name}")
    logger.info(f"Created {len(results)} edited videos")
    logger.info("=" * 60)
    
    return results