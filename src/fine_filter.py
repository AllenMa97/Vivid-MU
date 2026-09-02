import cv2
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from pathlib import Path
import logging

from device_manager import DeviceManager
from yolo_detector import YoloDetector, ObjectFeatures

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
    # 使用 CLIP 图像 embedding 计算的语义多样性 (真实神经网络信号)
    clip_diversity: float = 0.0
    # CLIP 片段级 embedding (均值池化), 用于跨片段相似度/独特性
    clip_embedding: Optional[np.ndarray] = None


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
    object_features: Optional[ObjectFeatures] = None
    
    coarse_score: float = 0.0
    stability_score: float = 0.5
    audio_onset_count: int = 0
    
    raw_features: Dict[str, Any] = field(default_factory=dict)


class FaceDetector:
    def __init__(self, confidence_threshold: float = 0.5, device_manager: Optional[DeviceManager] = None):
        self.confidence_threshold = confidence_threshold
        self.device_manager = device_manager
        self.net = None
        self.backend = cv2.dnn.DNN_BACKEND_OPENCV
        self.target = cv2.dnn.DNN_TARGET_CPU
        self._init_detector()
    
    def _init_detector(self):
        try:
            proto_path = Path(__file__).parent / "models" / "deploy.prototxt"
            model_path = Path(__file__).parent / "models" / "res10_300x300_ssd_iter_140000.caffemodel"
            
            if not (proto_path.exists() and model_path.exists()):
                logger.warning("人脸检测模型不存在, 请运行 download_models.py 下载")
                self.net = None
                return
            
            if self.device_manager is not None:
                self.backend, self.target = self.device_manager.cv2_dnn_backend_target()
            
            self.net = cv2.dnn.readNetFromCaffe(str(proto_path), str(model_path))
            if hasattr(self.net, "setPreferableBackend"):
                self.net.setPreferableBackend(self.backend)
                self.net.setPreferableTarget(self.target)
            logger.info(f"Loaded Caffe face detection model (backend={self.backend}, target={self.target})")
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
    def __init__(self, device_manager: Optional[DeviceManager] = None):
        self.device_manager = device_manager
        self.model = None
        self.input_name = None
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
            model_path = Path(__file__).parent / "models" / "clip_vit_b32.onnx"
            if not model_path.exists():
                logger.warning(
                    "CLIP 模型不存在 (models/clip_vit_b32.onnx), "
                    "场景特征回退到启发式。可用 download_models.py --with-clip 下载。")
                self.model = None
                return
            if self.device_manager is not None:
                self.model = self.device_manager.create_ort_session(
                    str(model_path), model_group="clip")
            else:
                import onnxruntime as ort
                self.model = ort.InferenceSession(
                    str(model_path), providers=ort.get_available_providers())
            self.input_name = self.model.get_inputs()[0].name
            logger.info(f"Loaded CLIP ONNX model (session: {self.model.get_inputs()[0].name})")
        except Exception as e:
            logger.warning(f"Could not load CLIP model: {e}")
            self.model = None

    @property
    def clip_available(self) -> bool:
        return self.model is not None
    
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
    
    def _clip_preprocess(self, frame: np.ndarray, size: int = 224) -> np.ndarray:
        """CLIP 标准预处理 (BGR -> RGB, resize center crop, normalize)"""
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        scale = size / min(h, w)
        new_h, new_w = int(round(h * scale)), int(round(w * scale))
        resized = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        top = (new_h - size) // 2
        left = (new_w - size) // 2
        cropped = resized[top:top + size, left:left + size]
        x = cropped.astype(np.float32) / 255.0
        mean = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
        std = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
        x = (x - mean) / std
        x = np.transpose(x, (2, 0, 1))[None, ...]
        return x

    def _embed(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """CLIP 图像 embedding (归一化)"""
        if not self.clip_available:
            return None
        try:
            inp = self._clip_preprocess(frame)
            outputs = self.model.run(None, {self.input_name: inp})
            emb = np.asarray(outputs[0]).reshape(-1)
            norm = np.linalg.norm(emb)
            return emb / norm if norm > 0 else emb
        except Exception as e:
            logger.warning(f"CLIP embed failed: {e}")
            return None

    def classify(self, frame: np.ndarray) -> Dict[str, float]:
        return self._estimate_scene_type(frame)

    def extract_features(self, frames: List[np.ndarray]) -> SceneFeatures:
        if not frames:
            return SceneFeatures()
        
        sample_frames = frames[::max(1, len(frames) // 3)]
        sample_frames = sample_frames[:3]
        
        # 1) 启发式场景类型 (始终保留作为兜底)
        all_scores = []
        for frame in sample_frames:
            all_scores.append(self.classify(frame))
        
        avg_scores = {}
        for label in self.scene_labels:
            values = [s.get(label, 0) for s in all_scores if label in s]
            avg_scores[label] = np.mean(values) if values else 0.0
        
        dominant_scene = max(avg_scores, key=lambda k: avg_scores[k]) if avg_scores else ""
        
        # 2) 颜色直方图多样性 (启发式, 作为备选)
        scene_diversity = 0.0
        if len(sample_frames) > 1:
            histograms = [self._extract_color_histogram(f) for f in sample_frames]
            diversities = []
            for i in range(len(histograms) - 1):
                sim = cv2.compareHist(
                    histograms[i].reshape(-1, 1).astype(np.float32),
                    histograms[i+1].reshape(-1, 1).astype(np.float32),
                    cv2.HISTCMP_CORREL
                )
                diversities.append(1.0 - sim)
            scene_diversity = np.mean(diversities) if diversities else 0.0
        
        # 3) CLIP 语义多样性 + 片段级 embedding (真实神经网络信号)
        clip_diversity = 0.0
        clip_embedding = None
        if self.clip_available:
            embeddings = [self._embed(f) for f in sample_frames]
            embeddings = [e for e in embeddings if e is not None]
            if len(embeddings) >= 2:
                sims = [float(np.dot(embeddings[i], embeddings[i + 1]))
                        for i in range(len(embeddings) - 1)]
                clip_diversity = float(np.clip(1.0 - np.mean(sims), 0.0, 1.0))
            if embeddings:
                clip_embedding = np.mean(embeddings, axis=0)
                clip_embedding /= (np.linalg.norm(clip_embedding) or 1.0)
        
        return SceneFeatures(
            scene_scores=avg_scores,
            scene_diversity=scene_diversity,
            dominant_scene=dominant_scene,
            clip_diversity=clip_diversity,
            clip_embedding=clip_embedding
        )


class VADProcessor:
    """语音活动检测 (VAD)

    优先使用 Silero VAD (ONNX, 无需 torch); 模型缺失时回退到能量阈值法。
    """
    def __init__(self, threshold: float = 0.5, device_manager: Optional[DeviceManager] = None):
        self.threshold = threshold
        self.device_manager = device_manager
        self.model = None
        self._init_model()
    
    def _init_model(self):
        try:
            model_path = Path(__file__).parent / "models" / "silero_vad.onnx"
            if not model_path.exists():
                logger.warning("Silero VAD 模型不存在 (models/silero_vad.onnx), "
                               "语音检测回退到能量法。可用 download_models.py 下载。")
                self.model = None
                return
            if self.device_manager is not None:
                self.model = self.device_manager.create_ort_session(
                    str(model_path), model_group="vad")
            else:
                import onnxruntime as ort
                self.model = ort.InferenceSession(
                    str(model_path), providers=ort.get_available_providers())
            self.input_names = [i.name for i in self.model.get_inputs()]
            logger.info(f"Loaded Silero VAD ONNX model (inputs={self.input_names})")
        except Exception as e:
            logger.warning(f"Could not load VAD model: {e}")
            self.model = None

    @property
    def vad_available(self) -> bool:
        return self.model is not None

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
        if self.vad_available and sr == 16000:
            try:
                segments = self._model_based_vad(audio_segment, sr)
                if segments is not None:
                    return segments
            except Exception as e:
                logger.warning(f"Silero VAD 推理失败, 回退能量法: {e}")
        return self._energy_based_vad(audio_segment, sr)

    def _model_based_vad(self, audio_segment: np.ndarray, sr: int = 16000) -> Optional[List[tuple]]:
        """Silero VAD 流式推理 (参考官方 OnnxWrapper 实现)

        官方 ONNX 输入 = 64 样本上下文 + 512 新样本 (16k); 状态 state[2,1,128]。
        """
        chunk_size = 512          # 16k 采样率下每步处理 512 样本
        context_size = 64         # 官方要求拼接的前 64 样本上下文
        n_chunks = len(audio_segment) // chunk_size
        if n_chunks == 0:
            return []

        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, context_size), dtype=np.float32)
        has_sr_input = "sr" in self.input_names
        sr_val = np.array(sr, dtype=np.int64)

        probs = []
        for i in range(n_chunks):
            chunk = audio_segment[i * chunk_size:(i + 1) * chunk_size].astype(np.float32)
            x = np.concatenate([context, chunk[None, :]], axis=1)  # (1, 576)
            feed = {"input": x, "state": state}
            if has_sr_input:
                feed["sr"] = sr_val
            outs = self.model.run(None, feed)
            probs.append(float(np.asarray(outs[0]).reshape(-1)[0]))
            state = np.asarray(outs[1]).reshape((2, 1, 128))
            context = x[..., -context_size:]

        # 阈值 + 滞后, 生成语音段
        start_thr, end_thr = self.threshold, max(self.threshold - 0.15, 0.2)
        hop_sec = chunk_size / sr
        speech_segments = []
        in_speech = False
        start_i = 0
        for i, p in enumerate(probs):
            if not in_speech and p > start_thr:
                in_speech = True
                start_i = i
            elif in_speech and p < end_thr:
                in_speech = False
                st = start_i * hop_sec
                en = i * hop_sec
                if en - st > 0.1:
                    speech_segments.append((st, en))
        if in_speech:
            st = start_i * hop_sec
            en = n_chunks * hop_sec
            if en - st > 0.1:
                speech_segments.append((st, en))
        return speech_segments

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
    def __init__(self, config, device_manager: Optional[DeviceManager] = None,
                 yolo_config=None, yolo_enabled: bool = True):
        self.config = config
        self.device_manager = device_manager
        self.face_detector = FaceDetector(config.face_detection_confidence, device_manager)
        self.scene_classifier = SceneClassifier(device_manager)
        self.vad_processor = VADProcessor(config.vad_threshold, device_manager)

        # YOLO 物体检测 (可通过 yolo_config.enabled 关闭)
        self.yolo_enabled = yolo_enabled
        self.object_detector = None
        if yolo_enabled and yolo_config is not None and getattr(yolo_config, "enabled", True):
            self.object_detector = YoloDetector(
                model_path=yolo_config.model_path,
                conf_threshold=yolo_config.conf_threshold,
                iou_threshold=yolo_config.iou_threshold,
                input_size=yolo_config.input_size,
                track=yolo_config.track,
                device_manager=device_manager,
            )
    
    def process_segment(self, segment, video_processor, audio_data: Optional[np.ndarray] = None,
                        sr: int = 16000, segment_id: int = 0) -> FineFeatures:
        frames = video_processor.get_frames_in_range(
            segment.start_time, segment.end_time, 
            sample_fps=self.config.scene_sample_fps
        )
        
        frame_arrays = [f.frame for f in frames]
        
        # 人脸检测 (通过 DeviceManager 选择 cv2.dnn 后端)
        face_features = None
        if frame_arrays:
            face_features = self.face_detector.extract_features(frame_arrays)
        
        # 场景特征 (CLIP 语义 + 启发式)
        scene_features = None
        if frame_arrays:
            scene_features = self.scene_classifier.extract_features(frame_arrays)
        
        # YOLO 物体级语义特征 (宠物 / 人物 / 互动)
        object_features = None
        if self.object_detector is not None and frame_arrays:
            object_features = self.object_detector.extract_features(frame_arrays)
        
        # 语音活动检测 (Silero VAD / 能量法)
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
            object_features=object_features,
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
