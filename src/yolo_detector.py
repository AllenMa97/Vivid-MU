"""
YOLO 目标检测模块 (YOLOv8 ONNX)

为视频片段提供物体级语义信息:
- 检测宠物 (dog/cat)、人物、玩具等 COCO 类别
- 轻量 IoU 跟踪, 统计宠物在场时长 / 人宠互动 / 动作强度
- 通过 DeviceManager 在 CPU/NPU/GPU 上运行

模型: models/yolov8n.onnx (约 6MB, 用 download_models.py 下载)
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# COCO 80 类名称 (YOLOv8 默认)
COCO_NAMES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush",
]

# 宠物类 (重点关注的类别)
PET_CLASSES = {"dog", "cat"}
# 与宠物互动相关的常见玩具/物品
TOY_CLASSES = {"ball", "frisbee", "teddy bear", "sports ball", "kite", "bird"}


@dataclass
class Detection:
    class_id: int
    class_name: str
    confidence: float
    bbox: Tuple[int, int, int, int]  # x1, y1, x2, y2

    @property
    def center(self) -> Tuple[float, float]:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    @property
    def area(self) -> float:
        x1, y1, x2, y2 = self.bbox
        return max(0, (x2 - x1)) * max(0, (y2 - y1))


@dataclass
class ObjectFeatures:
    """一个视频片段的物体级语义特征"""
    pet_presence: float = 0.0       # 帧中出现宠物的比例 (0-1)
    pet_count: float = 0.0          # 平均宠物数量
    person_count: float = 0.0       # 平均人数
    interaction_ratio: float = 0.0  # 人与宠物同框的帧比例 (0-1)
    toy_presence: float = 0.0       # 帧中出现玩具/互动物的比例 (0-1)
    object_diversity: float = 0.0   # 出现过的目标类别数 / 关注类别数 (0-1)
    action_intensity: float = 0.0   # 宠物运动强度 (bbox 位移, 归一化 0-1)
    has_close_pet: float = 0.0      # 出现大尺寸宠物的帧比例 (0-1)
    det_confidence: float = 0.0     # 平均检测置信度 (0-1)
    tracked_segments: int = 0       # 跟踪到的宠物轨迹数
    sample_frames: int = 0          # 实际采样帧数


class YoloDetector:
    def __init__(self, model_path: str = "models/yolov8n.onnx",
                 conf_threshold: float = 0.25, iou_threshold: float = 0.45,
                 input_size: int = 640, track: bool = True,
                 device_manager=None):
        import os
        from pathlib import Path
        self.model_path = str(Path(__file__).parent / model_path) if not os.path.isabs(model_path) else model_path
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.input_size = input_size
        self.track = track
        self.device_manager = device_manager
        self.session = None
        self.input_name = None
        self._load()

    # ------------------------------------------------------------------
    # 模型加载
    # ------------------------------------------------------------------
    def _load(self) -> None:
        import os
        if not os.path.exists(self.model_path):
            logger.warning(
                f"YOLO 模型不存在: {self.model_path}\n"
                f"请运行 python download_models.py 下载 (或到 src/models/ 放置 yolov8n.onnx)。"
                f"未加载时将跳过物体检测。")
            return
        try:
            if self.device_manager is not None:
                self.session = self.device_manager.create_ort_session(
                    self.model_path, model_group="yolo")
            else:
                import onnxruntime as ort
                self.session = ort.InferenceSession(
                    self.model_path,
                    providers=ort.get_available_providers())
            self.input_name = self.session.get_inputs()[0].name
            logger.info(f"YOLO 模型加载成功: {self.model_path}")
        except Exception as e:
            logger.warning(f"YOLO 模型加载失败: {e}")
            self.session = None

    @property
    def available(self) -> bool:
        return self.session is not None

    # ------------------------------------------------------------------
    # 预处理 / 后处理
    # ------------------------------------------------------------------
    @staticmethod
    def _letterbox(img: np.ndarray, size: int) -> Tuple[np.ndarray, float, Tuple[int, int]]:
        h, w = img.shape[:2]
        scale = min(size / h, size / w)
        new_w, new_h = int(round(w * scale)), int(round(h * scale))
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        pad_x = (size - new_w) // 2
        pad_y = (size - new_h) // 2
        canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
        return canvas, scale, (pad_x, pad_y)

    @staticmethod
    def _non_max_suppression(boxes: np.ndarray, scores: np.ndarray,
                             classes: np.ndarray, iou_thr: float) -> List[Detection]:
        """box: (N,4) xyxy, scores: (N,), classes: (N,)"""
        keep = []
        if len(boxes) == 0:
            return keep
        for cls in np.unique(classes):
            idx = np.where(classes == cls)[0]
            cls_boxes = boxes[idx].astype(np.float32)
            cls_scores = scores[idx].astype(np.float32)
            nms_idx = cv2.dnn.NMSBoxes(
                cls_boxes.tolist(), cls_scores.tolist(), 0.0, iou_thr)
            if nms_idx is None:
                continue
            if isinstance(nms_idx, tuple):  # OpenCV 不同版本返回格式
                nms_idx = nms_idx[0]
            nms_idx = np.asarray(nms_idx).ravel()
            for i in nms_idx:
                orig = int(idx[i])
                keep.append(orig)
        return keep

    def detect(self, frame: np.ndarray) -> List[Detection]:
        """对单帧做 YOLO 检测, 返回 Detection 列表 (已按 NMS 去重)"""
        if not self.available:
            return []
        h, w = frame.shape[:2]
        letterboxed, scale, (pad_x, pad_y) = self._letterbox(frame, self.input_size)
        blob = letterboxed.astype(np.float32) / 255.0
        blob = np.transpose(blob, (2, 0, 1))[None, ...]  # 1,3,size,size

        outputs = self.session.run(None, {self.input_name: blob})
        pred = outputs[0]  # (1, 84, 8400)
        pred = pred[0]  # (84, 8400)
        pred = np.transpose(pred, (1, 0))  # (8400, 84)

        boxes_xywh = pred[:, :4]
        cls_scores = pred[:, 4:]
        scores = cls_scores.max(axis=1)
        class_ids = cls_scores.argmax(axis=1)

        mask = scores >= self.conf_threshold
        if not mask.any():
            return []
        boxes_xywh = boxes_xywh[mask]
        scores = scores[mask]
        class_ids = class_ids[mask]

        # xywh -> xyxy, 并映射回原图坐标
        cx, cy, bw, bh = boxes_xywh[:, 0], boxes_xywh[:, 1], boxes_xywh[:, 2], boxes_xywh[:, 3]
        x1 = (cx - bw / 2 - pad_x) / scale
        y1 = (cy - bh / 2 - pad_y) / scale
        x2 = (cx + bw / 2 - pad_x) / scale
        y2 = (cy + bh / 2 - pad_y) / scale
        boxes = np.stack([x1, y1, x2, y2], axis=1)
        # 裁剪到图像范围
        boxes[:, 0] = np.clip(boxes[:, 0], 0, w)
        boxes[:, 2] = np.clip(boxes[:, 2], 0, w)
        boxes[:, 1] = np.clip(boxes[:, 1], 0, h)
        boxes[:, 3] = np.clip(boxes[:, 3], 0, h)

        keep_idx = self._non_max_suppression(boxes, scores, class_ids, self.iou_threshold)
        detections = []
        for i in keep_idx:
            cid = int(class_ids[i])
            name = COCO_NAMES[cid] if cid < len(COCO_NAMES) else f"cls{cid}"
            x1i, y1i, x2i, y2i = (int(v) for v in boxes[i])
            detections.append(Detection(
                class_id=cid, class_name=name,
                confidence=float(scores[i]), bbox=(x1i, y1i, x2i, y2i)))
        return detections

    # ------------------------------------------------------------------
    # 轻量 IoU 跟踪
    # ------------------------------------------------------------------
    def _track(self, frames_detections: List[List[Detection]],
               max_age: int = 3) -> List[Dict]:
        """
        简单的跨帧 IoU 跟踪。返回轨迹列表:
        [{id, class_name, ages, frames: [Detection,...]}]
        """
        tracks: List[Dict] = []
        next_id = 0

        for frame_dets in frames_detections:
            # 匹配: 同类别 + IoU 最大且 > 0.3
            unmatched = list(range(len(frame_dets)))
            for tr in tracks:
                best_iou, best_det = 0.0, None
                best_idx = None
                if not tr["frames"]:
                    continue
                prev = tr["frames"][-1]
                for di in unmatched:
                    det = frame_dets[di]
                    if det.class_name != prev.class_name:
                        continue
                    iou = self._iou(prev.bbox, det.bbox)
                    if iou > best_iou:
                        best_iou, best_det, best_idx = iou, det, di
                if best_det is not None and best_iou >= 0.3:
                    tr["frames"].append(best_det)
                    tr["age"] = 0
                    unmatched.remove(best_idx)
                else:
                    tr["age"] += 1

            # 新建轨迹
            for di in unmatched:
                tracks.append({"id": next_id, "class_name": frame_dets[di].class_name,
                               "age": 0, "frames": [frame_dets[di]]})
                next_id += 1

            # 清理长期未更新的轨迹
            tracks = [t for t in tracks if t["age"] <= max_age or t["frames"]]

        # 过滤掉太短的轨迹 (少于 2 帧)
        return [t for t in tracks if len(t["frames"]) >= 2]

    @staticmethod
    def _iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        if inter == 0:
            return 0.0
        union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
        return inter / union if union > 0 else 0.0

    # ------------------------------------------------------------------
    # 片段级特征提取
    # ------------------------------------------------------------------
    def extract_features(self, frames: List[np.ndarray],
                         frame_area: Optional[float] = None) -> ObjectFeatures:
        """从片段的一组帧中提取物体级语义特征"""
        feat = ObjectFeatures()
        if not self.available or not frames:
            return feat

        dets_per_frame: List[List[Detection]] = []
        seen_classes: set = set()

        for frame in frames:
            dets = self.detect(frame)
            dets_per_frame.append(dets)
            seen_classes.update(d.class_name for d in dets)

        n = len(dets_per_frame)
        if n == 0:
            return feat
        feat.sample_frames = n

        if frame_area is None:
            frame_area = frames[0].shape[0] * frames[0].shape[1]

        pet_frames = person_frames = interact_frames = toy_frames = close_pet_frames = 0
        pet_counts, person_counts, confs = [], [], []

        for dets in dets_per_frame:
            pets = [d for d in dets if d.class_name in PET_CLASSES]
            persons = [d for d in dets if d.class_name == "person"]
            toys = [d for d in dets if d.class_name in TOY_CLASSES]

            if pets:
                pet_frames += 1
                pet_counts.append(len(pets))
                if any(d.area / frame_area > 0.10 for d in pets):
                    close_pet_frames += 1
            if persons:
                person_frames += 1
                person_counts.append(len(persons))
            if pets and persons:
                interact_frames += 1
            if toys:
                toy_frames += 1
            confs.extend(d.confidence for d in dets)

        feat.pet_presence = pet_frames / n
        feat.person_count = float(np.mean(person_counts)) if person_counts else 0.0
        feat.pet_count = float(np.mean(pet_counts)) if pet_counts else 0.0
        feat.interaction_ratio = interact_frames / n
        feat.toy_presence = toy_frames / n
        feat.has_close_pet = close_pet_frames / n
        feat.det_confidence = float(np.mean(confs)) if confs else 0.0

        # 目标类别多样性: 出现过类别数 / 关注类别数
        focus_classes = PET_CLASSES | {"person"} | TOY_CLASSES
        feat.object_diversity = len(seen_classes & focus_classes) / len(focus_classes)

        # 宠物动作强度: 相邻帧宠物 bbox 中心位移 (用轨迹计算, 归一化到画面宽度)
        if self.track and n > 1:
            tracks = self._track(dets_per_frame)
            pet_tracks = [t for t in tracks if t["class_name"] in PET_CLASSES]
            feat.tracked_segments = len(pet_tracks)
            frame_w = frames[0].shape[1]
            displacements = []
            for tr in pet_tracks:
                centers = [d.center for d in tr["frames"]]
                for i in range(1, len(centers)):
                    dx = abs(centers[i][0] - centers[i - 1][0])
                    dy = abs(centers[i][1] - centers[i - 1][1])
                    displacements.append(np.hypot(dx, dy) / max(frame_w, 1))
            if displacements:
                feat.action_intensity = float(np.clip(np.mean(displacements) * 3.0, 0.0, 1.0))

        return feat
