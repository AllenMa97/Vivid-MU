import cv2
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


@dataclass
class FaceInfo:
    bbox: tuple
    confidence: float
    area: float
    center: tuple
    
    @property
    def center_distance(self) -> float:
        cx, cy = self.center
        return np.sqrt((cx - 0.5) ** 2 + (cy - 0.5) ** 2)


@dataclass
class FaceFeatures:
    face_count: int = 0
    avg_face_size: float = 0.0
    max_face_size: float = 0.0
    has_large_face: bool = False
    avg_center_distance: float = 1.0
    face_detection_confidence: float = 0.0


@dataclass
class SceneFeatures:
    scene_scores: Dict[str, float] = field(default_factory=dict)
    scene_diversity: float = 0.0
    dominant_scene: str = ""


@dataclass
class SpeechFeatures:
    speech_ratio: float = 0.0
    speech_density: float = 0.0
    speech_segments: int = 0


@dataclass
class FineFeatures:
    segment_id: int
    start_time: float
    end_time: float
    duration: float
    
    face_features: Optional[FaceFeatures] = None
    scene_features: Optional[SceneFeatures] = None
    speech_features: Optional[SpeechFeatures] = None
    
    coarse_score: float = 0.0
    stability_score: float = 0.5
    audio_onset_count: int = 0
    
    raw_features: Dict[str, Any] = field(default_factory=dict)


class FaceDetector:
    def __init__(self, confidence_threshold: float = 0.5):
        self.confidence_threshold = confidence_threshold
        self.net = None
        self._init_detector()
    
    def _init_detector(self):
        try:
            proto_path = Path(__file__).parent / "models" / "deploy.prototxt"
            model_path = Path(__file__).parent / "models" / "res10_300x300_ssd_iter_140000.caffemodel"
            
            if proto_path.exists() and model_path.exists():
                self.net = cv2.dnn.readNetFromCaffe(str(proto_path), str(model_path))
                logger.info("Loaded Caffe face detection model")
            else:
                self.net = cv2.dnn.readNetFromCaffe(
                    str(Path(__file__).parent / "models" / "deploy.prototxt"),
                    str(Path(__file__).parent / "models" / "res10_300x300_ssd_iter_140000.caffemodel")
                )
        except Exception as e:
            logger.warning(f"Could not load face detection model: {e}")
            self.net = None
    
    def detect(self, frame: np.ndarray) -> List[FaceInfo]:
        if self.net is None:
            return []
        
        small = cv2.resize(frame, (640, 360))
        scale_x = frame.shape[1] / 640
        scale_y = frame.shape[0] / 360
        
        h, w = small.shape[:2]
        blob = cv2.dnn.blobFromImage(small, 1.0, (300, 300), (104.0, 177.0, 123.0))
        self.net.setInput(blob)
        detections = self.net.forward()
        
        faces = []
        for i in range(detections.shape[2]):
            confidence = detections[0, 0, i, 2]
            if confidence < self.confidence_threshold:
                continue
            
            box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
            x1, y1, x2, y2 = box.astype(int)
            
            x1, x2 = int(x1 * scale_x), int(x2 * scale_x)
            y1, y2 = int(y1 * scale_y), int(y2 * scale_y)
            
            frame_area = frame.shape[0] * frame.shape[1]
            area = (x2 - x1) * (y2 - y1)
            
            center_x = (x1 + x2) / 2 / frame.shape[1]
            center_y = (y1 + y2) / 2 / frame.shape[0]
            
            faces.append(FaceInfo(
                bbox=(x1, y1, x2, y2),
                confidence=float(confidence),
                area=area / frame_area,
                center=(center_x, center_y)
            ))
        
        return faces
    
    def extract_features(self, frames: List[np.ndarray]) -> FaceFeatures:
        if not frames:
            return FaceFeatures()
        
        sample_frames = frames[::max(1, len(frames) // 3)]
        
        all_faces = []
        for frame in sample_frames:
            faces = self.detect(frame)
            all_faces.extend(faces)
        
        if not all_faces:
            return FaceFeatures()
        
        face_count = len(all_faces)
        avg_face_size = np.mean([f.area for f in all_faces])
        max_face_size = max(f.area for f in all_faces)
        has_large_face = any(f.area > 0.1 for f in all_faces)
        avg_center_distance = np.mean([f.center_distance for f in all_faces])
        avg_confidence = np.mean([f.confidence for f in all_faces])
        
        return FaceFeatures(
            face_count=face_count / len(sample_frames),
            avg_face_size=avg_face_size,
            max_face_size=max_face_size,
            has_large_face=has_large_face,
            avg_center_distance=avg_center_distance,
            face_detection_confidence=avg_confidence
        )


class SceneClassifier:
    def __init__(self):
        self.model = None
        self.scene_labels = [
            "a person talking to camera",
            "outdoor street scene",
            "indoor room",
            "crowded place",
            "quiet environment",
            "nature scene",
            "vehicle interior",
            "office space",
            "restaurant or cafe",
            "home environment"
        ]
        self._init_model()
    
    def _init_model(self):
        try:
            import onnxruntime as ort
            model_path = Path(__file__).parent / "models" / "clip_vit_b32.onnx"
            if model_path.exists():
                self.model = ort.InferenceSession(str(model_path))
                logger.info("Loaded CLIP ONNX model")
        except Exception as e:
            logger.warning(f"Could not load CLIP model: {e}")
            self.model = None
    
    def _extract_color_histogram(self, frame: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 8, 4], [0, 180, 0, 256, 0, 256])
        cv2.normalize(hist, hist)
        return hist.flatten()
    
    def _compute_brightness(self, frame: np.ndarray) -> float:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return np.mean(gray) / 255.0
    
    def _compute_colorfulness(self, frame: np.ndarray) -> float:
        b, g, r = cv2.split(frame.astype(float))
        rg = r - g
        yb = 0.5 * (r + g) - b
        
        std_root = np.sqrt(np.std(rg) ** 2 + np.std(yb) ** 2)
        mean_root = np.sqrt(np.mean(rg) ** 2 + np.mean(yb) ** 2)
        
        return std_root + 0.3 * mean_root
    
    def _estimate_scene_type(self, frame: np.ndarray) -> Dict[str, float]:
        brightness = self._compute_brightness(frame)
        colorfulness = self._compute_colorfulness(frame)
        
        scores = {}
        
        if brightness > 0.6:
            scores["outdoor street scene"] = 0.3
            scores["nature scene"] = 0.2
        elif brightness < 0.3:
            scores["indoor room"] = 0.3
            scores["home environment"] = 0.2
        else:
            scores["indoor room"] = 0.2
            scores["office space"] = 0.2
        
        if colorfulness > 50:
            scores["crowded place"] = 0.2
            scores["restaurant or cafe"] = 0.15
        else:
            scores["quiet environment"] = 0.25
        
        scores["a person talking to camera"] = 0.1
        
        return scores
    
    def classify(self, frame: np.ndarray) -> Dict[str, float]:
        if self.model is not None:
            return self._classify_with_model(frame)
        else:
            return self._estimate_scene_type(frame)
    
    def _classify_with_model(self, frame: np.ndarray) -> Dict[str, float]:
        return self._estimate_scene_type(frame)
    
    def extract_features(self, frames: List[np.ndarray]) -> SceneFeatures:
        if not frames:
            return SceneFeatures()
        
        sample_frames = frames[::max(1, len(frames) // 3)]
        
        all_scores = []
        for frame in sample_frames:
            scores = self.classify(frame)
            all_scores.append(scores)
        
        avg_scores = {}
        for label in self.scene_labels:
            values = [s.get(label, 0) for s in all_scores if label in s]
            avg_scores[label] = np.mean(values) if values else 0.0
        
        if avg_scores:
            dominant_scene = max(avg_scores.keys(), key=lambda k: avg_scores[k])
        else:
            dominant_scene = ""
        
        if len(sample_frames) > 1:
            histograms = [self._extract_color_histogram(f) for f in sample_frames[:3]]
            diversities = []
            for i in range(len(histograms) - 1):
                sim = cv2.compareHist(
                    histograms[i].reshape(-1, 1).astype(np.float32),
                    histograms[i+1].reshape(-1, 1).astype(np.float32),
                    cv2.HISTCMP_CORREL
                )
                diversities.append(1.0 - sim)
            scene_diversity = np.mean(diversities) if diversities else 0.0
        else:
            scene_diversity = 0.0
        
        return SceneFeatures(
            scene_scores=avg_scores,
            scene_diversity=scene_diversity,
            dominant_scene=dominant_scene
        )


class VADProcessor:
    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold
        self.model = None
        self._init_model()
    
    def _init_model(self):
        try:
            import torch
            model_path = Path(__file__).parent / "models" / "silero_vad.jit"
            if model_path.exists():
                self.model = torch.jit.load(str(model_path))
                logger.info("Loaded Silero VAD model")
        except Exception as e:
            logger.warning(f"Could not load VAD model: {e}")
            self.model = None
    
    def _energy_based_vad(self, audio_segment: np.ndarray, sr: int = 16000) -> List[tuple]:
        frame_length = int(sr * 0.025)
        hop_length = int(sr * 0.010)
        
        energy = []
        for i in range(0, len(audio_segment) - frame_length, hop_length):
            frame = audio_segment[i:i+frame_length]
            energy.append(np.sum(frame ** 2))
        
        if not energy:
            return []
        
        energy = np.array(energy)
        threshold = np.mean(energy) * 0.5
        
        speech_segments = []
        in_speech = False
        start_frame = 0
        
        for i, e in enumerate(energy):
            if e > threshold and not in_speech:
                in_speech = True
                start_frame = i
            elif e <= threshold and in_speech:
                in_speech = False
                start_time = start_frame * hop_length / sr
                end_time = i * hop_length / sr
                if end_time - start_time > 0.1:
                    speech_segments.append((start_time, end_time))
        
        if in_speech:
            start_time = start_frame * hop_length / sr
            end_time = len(energy) * hop_length / sr
            speech_segments.append((start_time, end_time))
        
        return speech_segments
    
    def detect_speech(self, audio_segment: np.ndarray, sr: int = 16000) -> List[tuple]:
        if self.model is not None:
            return self._model_based_vad(audio_segment, sr)
        else:
            return self._energy_based_vad(audio_segment, sr)
    
    def _model_based_vad(self, audio_segment: np.ndarray, sr: int = 16000) -> List[tuple]:
        return self._energy_based_vad(audio_segment, sr)
    
    def extract_features(self, audio_segment: np.ndarray, sr: int = 16000) -> SpeechFeatures:
        if audio_segment is None or len(audio_segment) == 0:
            return SpeechFeatures()
        
        speech_segments = self.detect_speech(audio_segment, sr)
        
        total_duration = len(audio_segment) / sr
        speech_duration = sum(end - start for start, end in speech_segments)
        
        speech_ratio = speech_duration / total_duration if total_duration > 0 else 0
        speech_density = len(speech_segments) / total_duration if total_duration > 0 else 0
        
        return SpeechFeatures(
            speech_ratio=speech_ratio,
            speech_density=speech_density,
            speech_segments=len(speech_segments)
        )


class FineFilter:
    def __init__(self, config):
        self.config = config
        self.face_detector = FaceDetector(config.face_detection_confidence)
        self.scene_classifier = SceneClassifier()
        self.vad_processor = VADProcessor(config.vad_threshold)
    
    def process_segment(self, segment, video_processor, audio_data: Optional[np.ndarray] = None,
                        sr: int = 16000, segment_id: int = 0) -> FineFeatures:
        frames = video_processor.get_frames_in_range(
            segment.start_time, segment.end_time, 
            sample_fps=self.config.scene_sample_fps
        )
        
        frame_arrays = [f.frame for f in frames]
        
        # Skip face detection to reduce computation (MTCNN is computationally expensive)
        # and face features are not decisive for pet video highlight extraction
        face_features = None
        
        scene_features = None
        if frame_arrays:
            scene_features = self.scene_classifier.extract_features(frame_arrays)
        
        speech_features = None
        if audio_data is not None and sr > 0:
            start_sample = int(segment.start_time * sr)
            end_sample = int(segment.end_time * sr)
            
            if end_sample <= len(audio_data):
                segment_audio = audio_data[start_sample:end_sample]
                speech_features = self.vad_processor.extract_features(segment_audio, sr)
        
        coarse_score = segment.avg_score
        
        onset_count = 0
        for cf in segment.features:
            if cf.audio_features and cf.audio_features.onset:
                onset_count += 1
        
        stability_score = 0.5
        
        return FineFeatures(
            segment_id=segment_id,
            start_time=segment.start_time,
            end_time=segment.end_time,
            duration=segment.duration,
            face_features=face_features,
            scene_features=scene_features,
            speech_features=speech_features,
            coarse_score=coarse_score,
            stability_score=stability_score,
            audio_onset_count=onset_count
        )
    
    def process_segments(self, segments: List, video_processor, 
                         audio_path: Optional[Path] = None) -> List[FineFeatures]:
        logger.info(f"开始细过滤处理，共 {len(segments)} 个片段")
        
        audio_data = None
        sr = 16000
        
        if audio_path and audio_path.exists():
            try:
                import soundfile as sf
                audio_data, sr = sf.read(str(audio_path))
                if len(audio_data.shape) > 1:
                    audio_data = audio_data[:, 0]
                logger.info(f"音频数据加载完成: {len(audio_data)} 采样点, {sr}Hz")
            except Exception as e:
                logger.warning(f"Could not load audio: {e}")
        
        results = []
        for i, segment in enumerate(segments):
            if (i + 1) % 10 == 0 or i == 0 or i == len(segments) - 1:
                logger.info(f"细过滤进度: {i+1}/{len(segments)} 片段, "
                           f"时间段: [{segment.start_time:.1f}s - {segment.end_time:.1f}s]")
            
            try:
                fine_features = self.process_segment(
                    segment, video_processor, audio_data, sr, segment_id=i
                )
                results.append(fine_features)
            except Exception as e:
                logger.warning(f"处理片段 {i} 时出错: {e}")
                continue
        
        logger.info(f"细过滤完成: {len(results)} 个片段特征提取完成")
        return results
