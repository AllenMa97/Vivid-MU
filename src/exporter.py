import subprocess
import json
import csv
from pathlib import Path
from typing import List, Optional
from dataclasses import asdict
import logging
import shutil
import platform

logger = logging.getLogger(__name__)


def get_builtin_ffmpeg_path() -> Optional[Path]:
    """获取项目内置的FFmpeg路径"""
    project_root = Path(__file__).parent.parent
    
    system = platform.system()
    if system == "Windows":
        ffmpeg_path = project_root / "ffmpeg" / "windows" / "bin" / "ffmpeg.exe"
    elif system == "Darwin":
        ffmpeg_path = project_root / "ffmpeg" / "mac" / "ffmpeg"
    else:
        ffmpeg_path = project_root / "ffmpeg" / "ubuntu" / "ffmpeg"
    
    if ffmpeg_path.exists():
        return ffmpeg_path
    return None


def check_ffmpeg() -> tuple:
    """检查FFmpeg是否可用，返回 (是否可用, ffmpeg路径)"""
    builtin_ffmpeg = get_builtin_ffmpeg_path()
    if builtin_ffmpeg:
        logger.info(f"Using builtin FFmpeg: {builtin_ffmpeg}")
        return True, str(builtin_ffmpeg)
    
    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        logger.info(f"Using system FFmpeg: {system_ffmpeg}")
        return True, 'ffmpeg'
    
    return False, None


class Exporter:
    def __init__(self, output_dir: Path, video_name: str):
        self.output_dir = Path(output_dir)
        self.video_name = video_name
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.ffmpeg_available, self.ffmpeg_path = check_ffmpeg()
        if not self.ffmpeg_available:
            logger.warning("FFmpeg not available, video export will be skipped")
    
    def export_solution(self, solution, video_path: Path, solution_index: int) -> Path:
        solution_dir = self.output_dir / f"solution_{solution_index}_{solution.strategy_name}"
        solution_dir.mkdir(parents=True, exist_ok=True)
        
        if self.ffmpeg_available and video_path.exists():
            segments_dir = solution_dir / "segments"
            segments_dir.mkdir(parents=True, exist_ok=True)
            self._export_individual_segments(video_path, solution.segments, segments_dir)
        else:
            if not self.ffmpeg_available:
                logger.warning("FFmpeg not available, skipping video export")
            elif not video_path.exists():
                logger.warning(f"Video file not found: {video_path}")
        
        self._export_segments_json(solution, solution_dir / "segments.json")
        self._export_scores_csv(solution, solution_dir / "scores.csv")
        self._export_metadata(solution, solution_dir / "metadata.json")
        
        return solution_dir
    
    def _export_individual_segments(self, video_path: Path, segments: List, output_dir: Path) -> None:
        """导出各个独立的片段视频"""
        if not segments:
            return
        
        video_path = video_path.absolute()
        if not video_path.exists():
            logger.error(f"Video file not found: {video_path}")
            return
        
        logger.info(f"Exporting {len(segments)} individual segments...")
        
        for i, seg in enumerate(segments, 1):
            output_file = output_dir / f"segment_{i:03d}.mp4"
            
            if output_file.exists():
                logger.info(f"Segment {i} already exists, skipping: {output_file}")
                continue
            
            cmd = [
                self.ffmpeg_path, '-y',
                '-ss', str(seg.start_time),
                '-i', str(video_path),
                '-t', str(seg.duration),
                '-c', 'copy',
                str(output_file)
            ]
            
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=60
                )
                
                if result.returncode != 0:
                    logger.warning(f"Segment {i} export failed, trying re-encode...")
                    self._export_segment_reencode(video_path, seg, output_file, i)
                else:
                    if i % 10 == 0 or i == 1 or i == len(segments):
                        logger.info(f"Segment {i}/{len(segments)} saved: {output_file.name}")
                    
            except subprocess.TimeoutExpired:
                logger.warning(f"Segment {i} export timeout")
            except Exception as e:
                logger.warning(f"Segment {i} export error: {e}")
        
        logger.info(f"Individual segments exported to: {output_dir}")
    
    def _export_segment_reencode(self, video_path: Path, segment, output_path: Path, index: int) -> None:
        """重新编码导出单个片段"""
        cmd = [
            self.ffmpeg_path, '-y',
            '-ss', str(segment.start_time),
            '-i', str(video_path),
            '-t', str(segment.duration),
            '-c:v', 'libx264', '-preset', 'fast',
            '-c:a', 'aac',
            str(output_path)
        ]
        
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )
            
            if result.returncode != 0:
                logger.error(f"Segment {index} re-encode failed: {result.stderr}")
            else:
                logger.info(f"Segment {index} re-encoded: {output_path.name}")
                
        except Exception as e:
            logger.error(f"Segment {index} re-encode error: {e}")
    
    def _export_segments_json(self, solution, output_path: Path):
        segments_data = []
        for seg in solution.segments:
            segment_dict = {
                'segment_id': seg.segment_id,
                'start_time': seg.start_time,
                'end_time': seg.end_time,
                'duration': seg.duration,
                'score': seg.score,
                'features': seg.features
            }
            segments_data.append(segment_dict)
        
        data = {
            'strategy_name': solution.strategy_name,
            'strategy_description': solution.strategy_description,
            'total_duration': solution.total_duration,
            'avg_score': solution.avg_score,
            'segments': segments_data
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Segments JSON saved: {output_path}")
    
    def _export_scores_csv(self, solution, output_path: Path):
        if not solution.segments:
            return
        
        fieldnames = ['segment_id', 'start_time', 'end_time', 'duration', 'score']
        fieldnames.extend(solution.segments[0].features.keys())
        
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for seg in solution.segments:
                row = {
                    'segment_id': seg.segment_id,
                    'start_time': seg.start_time,
                    'end_time': seg.end_time,
                    'duration': seg.duration,
                    'score': seg.score
                }
                row.update(seg.features)
                writer.writerow(row)
        
        logger.info(f"Scores CSV saved: {output_path}")
    
    def _export_metadata(self, solution, output_path: Path):
        metadata = {
            'strategy_name': solution.strategy_name,
            'strategy_description': solution.strategy_description,
            'total_duration_seconds': solution.total_duration,
            'total_duration_minutes': solution.total_duration / 60,
            'average_score': solution.avg_score,
            'segment_count': len(solution.segments),
            'video_name': self.video_name
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Metadata saved: {output_path}")
    
    def export_all_solutions(self, solutions: List, video_path: Path) -> List[Path]:
        output_paths = []
        
        for i, solution in enumerate(solutions, 1):
            solution_dir = self.export_solution(solution, video_path, i)
            output_paths.append(solution_dir)
        
        summary_path = self.output_dir / "summary.json"
        summary = {
            'video_name': self.video_name,
            'solution_count': len(solutions),
            'solutions': [
                {
                    'index': i,
                    'strategy_name': s.strategy_name,
                    'total_duration': s.total_duration,
                    'avg_score': s.avg_score,
                    'segment_count': len(s.segments)
                }
                for i, s in enumerate(solutions, 1)
            ]
        }
        
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Summary saved: {summary_path}")
        
        return output_paths
