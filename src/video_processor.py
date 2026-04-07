import cv2
import numpy as np
from pathlib import Path
from typing import Generator, Tuple, Optional
from dataclasses import dataclass
import subprocess
import json
import logging
import shutil
import platform

logger = logging.getLogger(__name__)

PROCESSING_HEIGHT = 480


def get_ffmpeg_path() -> Optional[str]:
    project_root = Path(__file__).parent.parent
    
    system = platform.system()
    if system == "Windows":
        ffmpeg_path = project_root / "ffmpeg" / "windows" / "bin" / "ffmpeg.exe"
    elif system == "Darwin":
        ffmpeg_path = project_root / "ffmpeg" / "mac" / "ffmpeg"
    else:
        ffmpeg_path = project_root / "ffmpeg" / "ubuntu" / "ffmpeg"
    
    if ffmpeg_path.exists():
        return str(ffmpeg_path)
    
    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        return system_ffmpeg
    
    return None


@dataclass
class VideoInfo:
    path: Path
    fps: float
    width: int
    height: int
    duration: float
    frame_count: int
    has_audio: bool
    audio_fs: Optional[float] = None
    original_path: Optional[Path] = None


@dataclass
class FrameData:
    frame: np.ndarray
    timestamp: float
    frame_idx: int
    gray: np.ndarray


class VideoProcessor:
    def __init__(self, video_path: Path, processing_height: int = PROCESSING_HEIGHT):
        self.original_path = Path(video_path)
        self.video_path = self.original_path
        self.processing_height = processing_height
        self.cap: Optional[cv2.VideoCapture] = None
        self.info: Optional[VideoInfo] = None
        
    def get_info(self) -> VideoInfo:
        if self.info is not None:
            return self.info
        
        cap = cv2.VideoCapture(str(self.video_path))
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {self.video_path}")
        
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps if fps > 0 else 0
        
        cap.release()
        
        has_audio, audio_fs = self._check_audio()
        
        self.info = VideoInfo(
            path=self.video_path,
            fps=fps,
            width=width,
            height=height,
            duration=duration,
            frame_count=frame_count,
            has_audio=has_audio,
            audio_fs=audio_fs,
            original_path=self.original_path
        )
        return self.info
    
    def _check_audio(self) -> Tuple[bool, Optional[float]]:
        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
            return False, None
        
        if platform.system() == "Windows":
            ffprobe_path = ffmpeg_path.replace('ffmpeg.exe', 'ffprobe.exe')
        else:
            ffprobe_path = ffmpeg_path.replace('ffmpeg', 'ffprobe')
        
        try:
            cmd = [
                ffprobe_path, '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                str(self.video_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            data = json.loads(result.stdout)
            
            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'audio':
                    audio_fs = float(stream.get('sample_rate', 0))
                    return True, audio_fs
            return False, None
        except Exception:
            return False, None
    
    def _resize_frame(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if h <= self.processing_height:
            return frame
        scale = self.processing_height / h
        new_w = int(w * scale)
        return cv2.resize(frame, (new_w, self.processing_height), interpolation=cv2.INTER_AREA)
    
    def extract_frames(self, sample_fps: float = 1.0) -> Generator[FrameData, None, None]:
        info = self.get_info()
        cap = cv2.VideoCapture(str(self.video_path))
        
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {self.video_path}")
        
        frame_interval = int(info.fps / sample_fps) if sample_fps > 0 else 1
        frame_interval = max(1, frame_interval)
        
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            
            if frame_idx % frame_interval == 0:
                timestamp = frame_idx / info.fps
                small_frame = self._resize_frame(frame)
                gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
                yield FrameData(
                    frame=small_frame,
                    timestamp=timestamp,
                    frame_idx=frame_idx,
                    gray=gray
                )
            
            frame_idx += 1
        
        cap.release()
    
    def extract_frames_batch(self, sample_fps: float = 1.0, batch_size: int = 100) -> Generator[list, None, None]:
        batch = []
        for frame_data in self.extract_frames(sample_fps):
            batch.append(frame_data)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch
    
    def extract_audio(self, output_path: Optional[Path] = None) -> Optional[Path]:
        info = self.get_info()
        if not info.has_audio:
            return None
        
        if output_path is None:
            output_path = self.video_path.with_suffix('.wav')
        
        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
            return None
        
        cmd = [
            ffmpeg_path, '-y', '-i', str(self.video_path),
            '-vn', '-acodec', 'pcm_s16le',
            '-ar', '16000', '-ac', '1',
            str(output_path)
        ]
        
        try:
            subprocess.run(cmd, capture_output=True, timeout=300)
            return output_path if output_path.exists() else None
        except Exception:
            return None
    
    def get_frame_at_time(self, timestamp: float, original_size: bool = False) -> Optional[np.ndarray]:
        info = self.get_info()
        cap = cv2.VideoCapture(str(self.video_path))
        
        if not cap.isOpened():
            return None
        
        frame_idx = int(timestamp * info.fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        
        ret, frame = cap.read()
        cap.release()
        
        if not ret:
            return None
        
        if not original_size:
            frame = self._resize_frame(frame)
        
        return frame
    
    def get_frames_in_range(self, start_time: float, end_time: float, sample_fps: float = 1.0) -> list:
        info = self.get_info()
        cap = cv2.VideoCapture(str(self.video_path))
        
        if not cap.isOpened():
            return []
        
        start_frame = int(start_time * info.fps)
        end_frame = int(end_time * info.fps)
        frame_interval = int(info.fps / sample_fps) if sample_fps > 0 else 1
        
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        
        frames = []
        current_frame = start_frame
        
        while current_frame <= end_frame:
            ret, frame = cap.read()
            if not ret:
                break
            
            if (current_frame - start_frame) % frame_interval == 0:
                timestamp = current_frame / info.fps
                small_frame = self._resize_frame(frame)
                gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
                frames.append(FrameData(
                    frame=small_frame,
                    timestamp=timestamp,
                    frame_idx=current_frame,
                    gray=gray
                ))
            
            current_frame += 1
        
        cap.release()
        return frames


def find_videos(input_dir: Path, supported_formats: list) -> list:
    videos = []
    input_dir = Path(input_dir)
    
    if not input_dir.exists():
        return videos
    
    for fmt in supported_formats:
        videos.extend(input_dir.glob(f"*{fmt}"))
        videos.extend(input_dir.glob(f"*{fmt.upper()}"))
    
    return sorted(set(videos))
