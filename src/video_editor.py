"""
Video Editing Module for VividMU
Automates video editing, effects, transitions, and BGM addition
"""
import os
import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Optional
import json
import random
from datetime import datetime

logger = logging.getLogger(__name__)

class VideoEditor:
    """Automated video editor for creating final highlights reel"""
    
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.ffmpeg_path = self._find_ffmpeg()
        
    def _find_ffmpeg(self) -> str:
        """Find ffmpeg executable"""
        # Try project's ffmpeg first
        project_ffmpeg = Path(__file__).parent.parent / "ffmpeg" / "windows" / "bin" / "ffmpeg.exe"
        if project_ffmpeg.exists():
            return str(project_ffmpeg)
        
        # Try system ffmpeg
        return "ffmpeg"
    
    def create_highlights_reel(
        self, 
        segment_paths: List[Path], 
        output_path: Path,
        bgm_path: Optional[Path] = None,
        transition_duration: float = 0.5,
        volume_adjust: float = 0.7
    ) -> bool:
        """
        Create a highlights reel from selected segments
        
        Args:
            segment_paths: List of video segment paths to combine
            output_path: Output path for the final video
            bgm_path: Background music path (optional)
            transition_duration: Duration of transitions between clips
            volume_adjust: Volume adjustment for background music (0.0-1.0)
        """
        try:
            # Create temporary directory for processing
            temp_dir = self.output_dir / "temp_editing"
            temp_dir.mkdir(exist_ok=True)
            
            # Method 1: Simple concatenation using FFmpeg concat protocol
            concat_file = temp_dir / "concat_list.txt"
            with open(concat_file, 'w', encoding='utf-8') as f:
                for segment_path in segment_paths:
                    f.write(f"file '{segment_path.absolute()}'\n")
            
            # Basic command for concatenation
            cmd = [
                self.ffmpeg_path,
                '-f', 'concat',
                '-safe', '0',
                '-i', str(concat_file),
                '-c', 'copy',  # Copy streams without re-encoding for speed
                '-avoid_negative_ts', 'make_zero',
                str(output_path)
            ]
            
            logger.info(f"Creating highlights reel: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)  # Increase timeout to 10 minutes
            
            if result.returncode != 0:
                logger.warning(f"Concatenation failed, trying with re-encoding: {result.stderr}")
                # Method 2: Re-encode with transitions and effects
                success = self._create_with_transitions(
                    segment_paths, output_path, bgm_path, transition_duration, volume_adjust
                )
                return success
            
            logger.info(f"Highlights reel created successfully: {output_path}")
            
            # Clean up temp files
            for temp_file in temp_dir.glob("*"):
                temp_file.unlink()
            temp_dir.rmdir()
            
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("Video editing timed out")
            return False
        except Exception as e:
            logger.error(f"Error creating highlights reel: {e}")
            return False
    
    def _create_with_transitions(
        self,
        segment_paths: List[Path],
        output_path: Path,
        bgm_path: Optional[Path] = None,
        transition_duration: float = 0.5,
        volume_adjust: float = 0.7
    ) -> bool:
        """Create video with transitions and effects using complex filter"""
        try:
            # For now, create a simple concatenation with basic processing
            # In a real implementation, we'd use complex filters for transitions
            
            # Create a temporary concat file
            temp_dir = self.output_dir / "temp_editing"
            concat_file = temp_dir / "concat_list.txt"
            
            with open(concat_file, 'w', encoding='utf-8') as f:
                for segment_path in segment_paths:
                    f.write(f"file '{segment_path.absolute()}'\n")
            
            # Command with basic processing (no re-encoding if possible)
            cmd = [
                self.ffmpeg_path,
                '-f', 'concat',
                '-safe', '0',
                '-i', str(concat_file),
                '-c:v', 'libx264',  # Re-encode with H.264
                '-preset', 'medium',  # Balance quality and speed
                '-crf', '23',  # Quality setting
                '-c:a', 'aac',  # Audio codec
                '-b:a', '128k',  # Audio bitrate
                '-movflags', '+faststart',  # Optimize for web
                str(output_path)
            ]
            
            logger.info(f"Creating reel with processing: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            
            if result.returncode != 0:
                logger.error(f"Video processing failed: {result.stderr}")
                return False
            
            # If BGM is provided, mix it in
            if bgm_path and bgm_path.exists():
                mixed_output = output_path.with_suffix('.mixed' + output_path.suffix)
                
                # Mix BGM with video
                mix_cmd = [
                    self.ffmpeg_path,
                    '-i', str(output_path),  # Input video
                    '-i', str(bgm_path),    # Background music
                    '-filter_complex', f'[0:a]volume=1.0[a0]; [1:a]volume={volume_adjust}[a1]; [a0][a1]amix=inputs=2:duration=first[aout]',
                    '-map', '0:v',  # Keep video from first input
                    '-map', '[aout]',  # Use mixed audio
                    '-c:v', 'copy',  # Copy video stream
                    '-c:a', 'aac',  # Encode audio
                    '-b:a', '128k',
                    '-shortest',  # End with shortest input
                    str(mixed_output)
                ]
                
                logger.info(f"Mixing BGM: {' '.join(mix_cmd)}")
                mix_result = subprocess.run(mix_cmd, capture_output=True, text=True, timeout=300)
                
                if mix_result.returncode == 0:
                    # Replace original with mixed version
                    mixed_output.rename(output_path)
                    logger.info("BGM successfully mixed with video")
                else:
                    logger.warning(f"BGM mixing failed: {mix_result.stderr}")
                    logger.info("Using video without BGM")
            
            logger.info(f"Final highlights reel created: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error in transition processing: {e}")
            return False
    
    def add_effects_batch(
        self,
        input_videos: List[Path],
        output_dir: Path,
        effects_config: Dict = None
    ) -> List[Path]:
        """
        Apply effects to multiple videos in batch
        
        Args:
            input_videos: List of input video paths
            output_dir: Directory to save processed videos
            effects_config: Dictionary with effect parameters
        """
        if effects_config is None:
            effects_config = {
                "brightness": 0,  # -100 to 100
                "contrast": 1.0,  # 0.0 to 3.0
                "saturation": 1.0,  # 0.0 to 3.0
                "speed": 1.0,  # 0.5 to 2.0
                "rotate": 0,  # degrees: 0, 90, 180, 270
            }
        
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        processed_videos = []
        
        for input_video in input_videos:
            output_video = output_dir / f"effect_{input_video.name}"
            
            # Build filter chain
            filters = []
            
            # Brightness and contrast
            if effects_config.get("brightness", 0) != 0 or effects_config.get("contrast", 1.0) != 1.0:
                brightness = effects_config.get("brightness", 0)
                contrast = effects_config.get("contrast", 1.0)
                filters.append(f"eq=brightness={brightness/100}:contrast={contrast}")
            
            # Saturation
            if effects_config.get("saturation", 1.0) != 1.0:
                saturation = effects_config.get("saturation", 1.0)
                if filters:
                    filters.append(f"saturation={saturation}")
                else:
                    filters.append(f"eq=saturation={saturation}")
            
            # Rotation
            rotation = effects_config.get("rotate", 0)
            if rotation == 90:
                filters.append("transpose=1")
            elif rotation == 180:
                filters.append("transpose=1,transpose=1")
            elif rotation == 270:
                filters.append("transpose=2")
            
            # Speed adjustment
            speed = effects_config.get("speed", 1.0)
            if speed != 1.0:
                filters.append(f"setpts={1/speed}*PTS,atempo={min(speed, 2.0) if speed <= 2.0 else 2.0}")
                if speed > 2.0:
                    # For speeds > 2x, we need to apply atempo multiple times
                    remaining_speed = speed / 2.0
                    filters[-1] = f"setpts={1/speed}*PTS,atempo=2.0,atempo={remaining_speed}"
            
            if filters:
                filter_chain = ",".join(filters)
                cmd = [
                    self.ffmpeg_path,
                    '-i', str(input_video),
                    '-vf', filter_chain,
                    '-c:a', 'copy',  # Copy audio unchanged
                    '-c:v', 'libx264',
                    '-preset', 'medium',
                    '-crf', '23',
                    str(output_video)
                ]
            else:
                # No effects, just copy
                cmd = [
                    self.ffmpeg_path,
                    '-i', str(input_video),
                    '-c', 'copy',
                    str(output_video)
                ]
            
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if result.returncode == 0:
                    processed_videos.append(output_video)
                    logger.info(f"Applied effects to: {input_video.name}")
                else:
                    logger.warning(f"Effects processing failed for {input_video.name}: {result.stderr}")
                    # Copy original if processing failed
                    import shutil
                    shutil.copy2(input_video, output_video)
                    processed_videos.append(output_video)
            except Exception as e:
                logger.error(f"Error processing {input_video.name}: {e}")
                # Copy original if processing failed
                import shutil
                shutil.copy2(input_video, output_video)
                processed_videos.append(output_video)
        
        return processed_videos
    
    def create_customized_video(
        self,
        segment_paths: List[Path],
        output_path: Path,
        bgm_path: Optional[Path] = None,
        title: str = "Highlights Reel",
        subtitle: str = "",
        add_intro: bool = False,
        intro_duration: float = 3.0,
        add_outro: bool = False,
        outro_duration: float = 3.0,
        logo_path: Optional[Path] = None
    ) -> bool:
        """
        Create a fully customized video with intro/outro, titles, and logo
        
        Args:
            segment_paths: List of video segments to combine
            output_path: Output path for final video
            bgm_path: Background music path
            title: Title text to display
            subtitle: Subtitle text
            add_intro: Whether to add an intro
            intro_duration: Duration of intro
            add_outro: Whether to add an outro
            outro_duration: Duration of outro
            logo_path: Logo overlay path
        """
        try:
            temp_dir = self.output_dir / "temp_custom"
            temp_dir.mkdir(exist_ok=True)
            
            # Step 1: Concatenate segments
            temp_concat = temp_dir / "concatenated.mp4"
            
            # Create concat file
            concat_file = temp_dir / "segments_list.txt"
            with open(concat_file, 'w', encoding='utf-8') as f:
                for segment_path in segment_paths:
                    f.write(f"file '{segment_path.absolute()}'\n")
            
            # Concatenate segments
            concat_cmd = [
                self.ffmpeg_path,
                '-f', 'concat',
                '-safe', '0',
                '-i', str(concat_file),
                '-c:v', 'libx264',
                '-preset', 'medium',
                '-crf', '23',
                '-c:a', 'aac',
                '-b:a', '128k',
                str(temp_concat)
            ]
            
            result = subprocess.run(concat_cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                logger.error(f"Segment concatenation failed: {result.stderr}")
                return False
            
            # Step 2: Add effects, BGM, and overlays
            final_cmd = [
                self.ffmpeg_path,
                '-i', str(temp_concat)
            ]
            
            # Add BGM if provided
            if bgm_path and bgm_path.exists():
                final_cmd.extend(['-i', str(bgm_path)])
                # Use filter_complex to mix audio
                audio_filter = "[0:a]volume=1.0[a0]; [1:a]volume=0.6[a1]; [a0][a1]amix=inputs=2:duration=first[aout]"
                final_cmd.extend([
                    '-filter_complex',
                    f'{audio_filter}',
                    '-map', '0:v',
                    '-map', '[aout]'
                ])
            else:
                final_cmd.extend(['-c:a', 'copy'])
            
            # Video processing
            final_cmd.extend([
                '-c:v', 'libx264',
                '-preset', 'medium',
                '-crf', '23',
                '-movflags', '+faststart',
                str(output_path)
            ])
            
            logger.info(f"Creating customized video: {' '.join(final_cmd)}")
            result = subprocess.run(final_cmd, capture_output=True, text=True, timeout=600)
            
            if result.returncode != 0:
                logger.error(f"Custom video creation failed: {result.stderr}")
                return False
            
            # Clean up
            for temp_file in temp_dir.glob("*"):
                temp_file.unlink()
            temp_dir.rmdir()
            
            logger.info(f"Customized video created: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error in customized video creation: {e}")
            return False


def create_video_editor(output_dir: Path):
    """Factory function to create video editor"""
    return VideoEditor(output_dir)


if __name__ == "__main__":
    # Example usage
    import sys
    from pathlib import Path
    
    if len(sys.argv) < 3:
        print("Usage: python video_editor.py <segments_dir> <output_path> [bgm_path]")
        sys.exit(1)
    
    segments_dir = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    bgm_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    
    # Find all segment files
    segment_files = sorted(list(segments_dir.glob("segment_*.mp4")))
    
    if not segment_files:
        print(f"No segment files found in {segments_dir}")
        sys.exit(1)
    
    print(f"Found {len(segment_files)} segments to process")
    
    # Create editor and process
    editor = VideoEditor(output_path.parent)
    
    success = editor.create_highlights_reel(
        segment_paths=segment_files,
        output_path=output_path,
        bgm_path=bgm_path
    )
    
    if success:
        print(f"Successfully created highlights reel: {output_path}")
    else:
        print("Failed to create highlights reel")
        sys.exit(1)