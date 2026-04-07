import cv2
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from pathlib import Path
import librosa
import logging
from queue import Queue
import threading

logger = logging.getLogger(__name__)

SAMPLE_FPS = 0.25
SCENE_CHANGE_THRESHOLD = 0.3


@dataclass
class FrameFeatures:
    timestamp: float
    frame_idx: int
    dhash_diff: float = 0.0
    histogram_diff: float = 0.0
    blur_score: float = 0.0
    is_blur: bool = False
    frame_diff: float = 0.0
    is_scene_change: bool = False


@dataclass
class AudioFeatures:
    timestamp: float
    loudness: float = 0.0
    energy_change: float = 0.0
    onset: bool = False


@dataclass
class CoarseFeatures:
    timestamp: float
    frame_features: Optional[FrameFeatures] = None
    audio_features: Optional[AudioFeatures] = None
    combined_score: float = 0.0


@dataclass
class Segment:
    start_time: float
    end_time: float
    duration: float
    avg_score: float
    features: List[CoarseFeatures] = field(default_factory=list)


class FrameDecoder:
    def __init__(self, video_path: Path, fps: float, sample_fps: float, processing_height: int = 480):
        self.video_path = video_path
        self.fps = fps
        self.sample_fps = sample_fps
        self.processing_height = processing_height
        self.frame_queue = Queue(maxsize=60)
        self.stop_event = threading.Event()
        self.decoder_thread = None
        
    def _resize_frame(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if h <= self.processing_height:
            return frame
        scale = self.processing_height / h
        new_w = int(w * scale)
        return cv2.resize(frame, (new_w, self.processing_height), interpolation=cv2.INTER_AREA)
    
    def _decode_loop(self):
        cap = cv2.VideoCapture(str(self.video_path))
        if not cap.isOpened():
            self.stop_event.set()
            return
        
        frame_interval = int(self.fps / self.sample_fps) if self.sample_fps > 0 else 1
        frame_interval = max(1, frame_interval)
        
        frame_idx = 0
        while not self.stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                break
            
            if frame_idx % frame_interval == 0:
                timestamp = frame_idx / self.fps
                small_frame = self._resize_frame(frame)
                gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
                
                try:
                    self.frame_queue.put((timestamp, frame_idx, small_frame, gray), timeout=30)
                except:
                    pass
            
            frame_idx += 1
        
        cap.release()
        self.frame_queue.put(None)
    
    def start(self):
        self.decoder_thread = threading.Thread(target=self._decode_loop, daemon=True)
        self.decoder_thread.start()
    
    def stop(self):
        self.stop_event.set()
        if self.decoder_thread:
            self.decoder_thread.join(timeout=5)
    
    def __iter__(self):
        while True:
            item = self.frame_queue.get()
            if item is None:
                break
            yield item


class CoarseFilter:
    def __init__(self, config):
        self.config = config
        self.prev_dhash = None
        self.prev_histogram = None
        self.prev_gray = None
        
    def compute_dhash(self, gray: np.ndarray) -> str:
        small = cv2.resize(gray, (32, 32))
        diff = small[:, 1:] > small[:, :-1]
        return ''.join(['1' if x else '0' for row in diff for x in row])
    
    def compute_dhash_diff(self, dhash: str) -> float:
        if self.prev_dhash is None:
            self.prev_dhash = dhash
            return 0.0
        diff = sum(c1 != c2 for c1, c2 in zip(self.prev_dhash, dhash))
        self.prev_dhash = dhash
        return diff / len(dhash)
    
    def compute_histogram_diff(self, frame: np.ndarray) -> float:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        hist = hist.flatten()
        
        if self.prev_histogram is None:
            self.prev_histogram = hist
            return 0.0
        
        diff = 1.0 - cv2.compareHist(
            self.prev_histogram.reshape(-1, 1).astype(np.float32),
            hist.reshape(-1, 1).astype(np.float32),
            cv2.HISTCMP_CORREL
        )
        self.prev_histogram = hist
        return max(0.0, min(1.0, diff))
    
    def compute_blur_score(self, gray: np.ndarray) -> float:
        return cv2.Laplacian(gray, cv2.CV_64F).var()
    
    def compute_frame_diff(self, prev_gray: np.ndarray, curr_gray: np.ndarray) -> float:
        prev_small = cv2.resize(prev_gray, (64, 64))
        curr_small = cv2.resize(curr_gray, (64, 64))
        diff = cv2.absdiff(prev_small, curr_small)
        return np.mean(diff) / 255.0
    
    def extract_frame_features(self, gray: np.ndarray, frame: np.ndarray,
                                timestamp: float, frame_idx: int,
                                prev_gray: Optional[np.ndarray] = None) -> FrameFeatures:
        dhash = self.compute_dhash(gray)
        dhash_diff = self.compute_dhash_diff(dhash)
        
        histogram_diff = self.compute_histogram_diff(frame)
        
        blur_score = self.compute_blur_score(gray)
        is_blur = blur_score < self.config.blur_threshold
        
        frame_diff = 0.0
        if prev_gray is not None:
            frame_diff = self.compute_frame_diff(prev_gray, gray)
        
        is_scene_change = dhash_diff > SCENE_CHANGE_THRESHOLD or histogram_diff > 0.5
        
        return FrameFeatures(
            timestamp=timestamp,
            frame_idx=frame_idx,
            dhash_diff=dhash_diff,
            histogram_diff=histogram_diff,
            blur_score=blur_score,
            is_blur=is_blur,
            frame_diff=frame_diff,
            is_scene_change=is_scene_change
        )
    
    def extract_audio_features(self, audio_path: Path, sample_fps: float = 0.5) -> dict:
        if audio_path is None or not audio_path.exists():
            return {}
        
        try:
            y, sr = librosa.load(str(audio_path), sr=16000)
        except Exception:
            return {}
        
        hop_length = int(sr / sample_fps)
        
        rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
        rms_db = librosa.amplitude_to_db(rms, ref=1.0)
        
        onset_frames = librosa.onset.onset_detect(y=y, sr=sr, hop_length=hop_length)
        onset_set = set(onset_frames)
        
        features = {}
        prev_energy = 0.0
        
        for i in range(len(rms)):
            timestamp = round(i * hop_length / sr, 1)
            
            loudness = max(0, rms_db[i] + 60) / 60
            
            energy = rms[i] ** 2
            energy_change = abs(energy - prev_energy) if i > 0 else 0.0
            prev_energy = energy
            
            onset = i in onset_set
            
            features[timestamp] = AudioFeatures(
                timestamp=timestamp,
                loudness=loudness,
                energy_change=energy_change,
                onset=onset
            )
        
        return features
    
    def compute_score(self, frame_feat: Optional[FrameFeatures],
                      audio_feat: Optional[AudioFeatures]) -> float:
        score = 0.0
        
        if frame_feat is not None:
            if not frame_feat.is_blur:
                score += self.config.dhash_weight * frame_feat.dhash_diff
                score += self.config.histogram_weight * frame_feat.histogram_diff
        
        if audio_feat is not None:
            score += self.config.loudness_weight * audio_feat.loudness
            if audio_feat.onset:
                score += self.config.onset_weight
        
        return score
    
    def temporal_filter(self, features: List[CoarseFeatures]) -> List[Segment]:
        if not features:
            return []
        
        scores = np.array([f.combined_score for f in features])
        timestamps = np.array([f.timestamp for f in features])
        
        window_size = 3
        if len(scores) >= window_size:
            kernel = np.ones(window_size) / window_size
            smoothed = np.convolve(scores, kernel, mode='same')
        else:
            smoothed = scores
        
        threshold = np.percentile(smoothed, self.config.retain_percentile)
        binary = smoothed > threshold
        
        segments = []
        in_segment = False
        segment_start = 0
        
        for i, is_high in enumerate(binary):
            if is_high and not in_segment:
                in_segment = True
                segment_start = i
            elif not is_high and in_segment:
                in_segment = False
                segments.append((segment_start, i))
        
        if in_segment:
            segments.append((segment_start, len(binary)))
        
        merged = []
        for start, end in segments:
            duration = timestamps[min(end, len(timestamps)-1)] - timestamps[start]
            
            if duration < self.config.min_segment_duration:
                continue
            
            if merged:
                prev_start, prev_end = merged[-1]
                gap = timestamps[start] - timestamps[min(prev_end, len(timestamps)-1)]
                
                if gap < self.config.merge_gap:
                    merged[-1] = (prev_start, end)
                    continue
            
            merged.append((start, end))
        
        result = []
        for start, end in merged:
            start_time = timestamps[start]
            end_time = timestamps[min(end, len(timestamps)-1)]
            duration = end_time - start_time
            avg_score = np.mean(smoothed[start:end]) if end > start else 0.0
            
            result.append(Segment(
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                avg_score=avg_score
            ))
        
        return result
    
    def process(self, video_processor, audio_path: Optional[Path] = None) -> List[Segment]:
        info = video_processor.get_info()
        
        sample_fps = min(self.config.sample_fps, SAMPLE_FPS)
        estimated_frames = int(info.duration * sample_fps)
        
        logger.info(f"Coarse filter: {sample_fps}fps, ~{estimated_frames} frames, multi-threaded decoding")
        
        audio_features = {}
        if audio_path and audio_path.exists():
            logger.info("Extracting audio features...")
            audio_features = self.extract_audio_features(audio_path, sample_fps)
            logger.info(f"Audio features: {len(audio_features)} timestamps")
        
        decoder = FrameDecoder(
            video_processor.video_path,
            info.fps,
            sample_fps,
            video_processor.processing_height
        )
        decoder.start()
        
        all_features = []
        all_scores = []
        all_timestamps = []
        prev_gray = None
        processed = 0
        last_log_time = 0
        
        for timestamp, frame_idx, frame, gray in decoder:
            processed += 1
            
            if timestamp - last_log_time >= 30:
                progress = timestamp / info.duration * 100 if info.duration > 0 else 0
                logger.info(f"Progress: {progress:.1f}% ({timestamp:.1f}s / {info.duration:.1f}s)")
                last_log_time = timestamp
            
            try:
                frame_feat = self.extract_frame_features(gray, frame, timestamp, frame_idx, prev_gray)
                
                audio_feat = audio_features.get(round(timestamp, 1))
                
                score = self.compute_score(frame_feat, audio_feat)
                
                all_features.append(CoarseFeatures(
                    timestamp=timestamp,
                    frame_features=frame_feat,
                    audio_features=audio_feat,
                    combined_score=score
                ))
                all_scores.append(score)
                all_timestamps.append(timestamp)
                
                prev_gray = gray.copy()
            except Exception as e:
                logger.warning(f"Error at {timestamp:.1f}s: {e}")
                continue
        
        decoder.stop()
        
        logger.info(f"Processed {processed} frames")
        
        logger.info("Temporal filtering...")
        segments = self.temporal_filter(all_features)
        
        for seg in segments:
            seg.features = [f for f in all_features 
                          if seg.start_time <= f.timestamp <= seg.end_time]
        
        logger.info(f"Coarse filter complete: {len(segments)} segments")
        return segments
