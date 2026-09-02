"""
Device Manager - 多设备 (CPU / NPU / GPU) 推理抽象层

负责:
1. 启动时探测本机可用计算设备 (ONNX Runtime / OpenVINO / PyTorch / cv2.dnn)
2. 按模型分组策略为每个模型选择目标设备
3. 提供统一的推理会话创建 / 设备降级 / cv2.dnn 后端选择

设计原则:
- 探测失败或目标设备不可用时自动降级到 CPU, 保证纯 CPU 机器也能运行
- 所有推理调用都应放在 try/except 中, 由本模块提供兜底
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Device:
    """一个可用的计算设备"""
    name: str          # 设备标识, 如 "cuda" / "openvino-npu" / "cpu"
    kind: str          # 设备类型: "cpu" / "gpu" / "npu"
    backend: str       # 后端: "onnxruntime" / "openvino" / "torch" / "cv2_dnn"
    detail: str = ""   # 补充说明

    @property
    def rank(self) -> int:
        """设备优先级, 越小越优先"""
        order = {"gpu": 0, "npu": 1, "cpu": 2}
        return order.get(self.kind, 3)


# onnxruntime provider 名 → 设备类型映射
_ORT_PROVIDER_DEVICE = {
    "CUDAExecutionProvider": "gpu",
    "TensorrtExecutionProvider": "gpu",
    "DmlExecutionProvider": "gpu",
    "CoreMLExecutionProvider": "gpu",
    "MIGraphXExecutionProvider": "gpu",
    "OpenVINOExecutionProvider": "npu",   # 实际可能是 CPU/GPU/NPU, 由 device_id 决定
    "CPUExecutionProvider": "cpu",
}


class DeviceManager:
    """计算设备探测与调度管理器"""

    def __init__(self, policy: Optional[Dict[str, str]] = None,
                 fallback: bool = True, verbose: bool = True):
        # 模型分组 → 目标设备 (auto/cpu/gpu/npu), 未配置的走 auto
        self.policy: Dict[str, str] = {k: v for k, v in (policy or {}).items()}
        self.fallback = fallback
        self.devices: List[Device] = []
        self._probe()
        if verbose:
            self._print_summary()

    # ------------------------------------------------------------------
    # 设备探测
    # ------------------------------------------------------------------
    def _probe(self) -> None:
        devices: List[Device] = []

        # 1) ONNX Runtime providers
        try:
            import onnxruntime as ort
            providers = ort.get_available_providers()
            for p in providers:
                kind = _ORT_PROVIDER_DEVICE.get(p)
                if kind is None:
                    continue
                if p == "OpenVINOExecutionProvider":
                    # OpenVINO EP 单独处理 (device_id 决定 CPU/GPU/NPU)
                    if "OpenVINOExecutionProvider" in providers:
                        devices.append(Device("openvino-ep", "auto", "onnxruntime",
                                              detail="ONNX Runtime OpenVINO EP"))
                elif kind == "gpu":
                    devices.append(Device(p.replace("ExecutionProvider", "").lower(),
                                          "gpu", "onnxruntime", detail=f"ONNX Runtime {p}"))
                elif kind == "cpu":
                    devices.append(Device("cpu", "cpu", "onnxruntime",
                                          detail="ONNX Runtime CPU"))
        except Exception as e:  # pragma: no cover
            logger.warning(f"ONNX Runtime 探测失败: {e}")

        # 2) OpenVINO (Intel CPU / iGPU / NPU)
        try:
            import openvino as ov
            core = ov.Core()
            for d in core.available_devices:
                dev = str(d).split(".")[0].upper()
                if dev == "CPU":
                    devices.append(Device("openvino-cpu", "cpu", "openvino",
                                          detail="OpenVINO CPU"))
                elif dev == "GPU":
                    devices.append(Device("openvino-gpu", "gpu", "openvino",
                                          detail="OpenVINO iGPU"))
                elif dev == "NPU":
                    devices.append(Device("openvino-npu", "npu", "openvino",
                                          detail="OpenVINO NPU (Intel Core Ultra)"))
        except ImportError:
            pass
        except Exception as e:  # pragma: no cover
            logger.warning(f"OpenVINO 探测失败: {e}")

        # 3) PyTorch CUDA
        try:
            import torch
            if torch.cuda.is_available():
                devices.append(Device("torch-cuda", "gpu", "torch",
                                      detail=f"PyTorch CUDA {torch.version.cuda}"))
        except ImportError:
            pass
        except Exception as e:  # pragma: no cover
            logger.warning(f"PyTorch 探测失败: {e}")

        # 4) cv2.dnn CUDA 后端 (用于 OpenCV 加载的模型, 如人脸检测)
        try:
            import cv2
            cuda_backend = cv2.dnn.DNN_BACKEND_CUDA
            if cv2.cuda.getCudaEnabledDeviceCount() > 0:
                devices.append(Device("cv2-cuda", "gpu", "cv2_dnn",
                                      detail="OpenCV DNN CUDA"))
        except Exception:
            pass

        # 去重 (按 name), 并按优先级排序
        seen = set()
        unique: List[Device] = []
        for d in devices:
            if d.name not in seen:
                seen.add(d.name)
                unique.append(d)
        unique.sort(key=lambda d: d.rank)
        self.devices = unique

    def _print_summary(self) -> None:
        if not self.devices:
            logger.info("未探测到可用推理设备, 将全部使用 CPU")
            return
        names = ", ".join(f"{d.name}({d.kind})" for d in self.devices)
        logger.info(f"可用推理设备: {names}")

    # ------------------------------------------------------------------
    # 设备选择
    # ------------------------------------------------------------------
    def available_kinds(self) -> List[str]:
        return sorted({d.kind for d in self.devices})

    def has_gpu(self) -> bool:
        return any(d.kind == "gpu" for d in self.devices)

    def has_npu(self) -> bool:
        return any(d.kind == "npu" for d in self.devices)

    def best_device(self, kind: Optional[str] = None) -> Device:
        """返回指定类型中最优设备; kind=None 时返回全局最优"""
        if kind is None:
            return self.devices[0] if self.devices else Device("cpu", "cpu", "onnxruntime")
        for d in self.devices:
            if d.kind == kind:
                return d
        # 找不到则回退 CPU
        for d in self.devices:
            if d.kind == "cpu":
                return d
        return Device("cpu", "cpu", "onnxruntime")

    def target_kind(self, model_group: str) -> str:
        """按策略解析某模型分组的目标设备类型 (gpu/npu/cpu)"""
        target = self.policy.get(model_group, "auto").lower()
        if target == "auto":
            return self.best_device().kind
        if target in ("gpu", "npu", "cpu"):
            if self.has_gpu() and target == "gpu":
                return "gpu"
            if self.has_npu() and target == "npu":
                return "npu"
            return "cpu"
        return self.best_device().kind

    # ------------------------------------------------------------------
    # 推理会话 / 后端解析
    # ------------------------------------------------------------------
    def resolve_onnx_providers(self, model_group: str) -> List[str]:
        """
        返回 onnxruntime 的 providers 列表 (按优先级排序, ORT 会依次尝试)。

        auto: gpu -> npu -> cpu
        gpu : 仅 gpu -> cpu (降级)
        npu : OpenVINO NPU -> cpu
        cpu : 仅 cpu
        """
        import onnxruntime as ort
        available = set(ort.get_available_providers())

        def _has(prefix: str) -> bool:
            return any(p.startswith(prefix) for p in available)

        target = self.target_kind(model_group)

        if target == "gpu":
            for p in ("CUDAExecutionProvider", "TensorrtExecutionProvider",
                      "DmlExecutionProvider", "CoreMLExecutionProvider"):
                if _has(p):
                    providers = [p]
                    if "CPUExecutionProvider" in available:
                        providers.append("CPUExecutionProvider")
                    return providers
        elif target == "npu":
            # NPU 优先走 OpenVINO EP
            if _has("OpenVINOExecutionProvider"):
                return ["OpenVINOExecutionProvider", "CPUExecutionProvider"]
            # 否则尝试用 GPU EP 凑合 (部分机器 NPU 兼容)
            for p in ("CUDAExecutionProvider", "DmlExecutionProvider"):
                if _has(p):
                    return [p, "CPUExecutionProvider"]

        # 默认 CPU
        if "CPUExecutionProvider" in available:
            return ["CPUExecutionProvider"]
        return []

    def create_ort_session(self, model_path: str, model_group: str, **kwargs):
        """
        创建 onnxruntime 推理会话。目标设备不可用时降级到 CPU。
        调用方应继续用 try/except 包裹, 以防模型本身加载失败。
        """
        import onnxruntime as ort
        providers = self.resolve_onnx_providers(model_group)
        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_options.intra_op_num_threads = 0  # 使用全部核
        try:
            return ort.InferenceSession(model_path, sess_options=sess_options,
                                        providers=providers, **kwargs)
        except Exception as e:
            if self.fallback and len(providers) > 1:
                logger.warning(f"设备 {providers[0]} 推理失败, 降级到 CPU: {e}")
                cpu_providers = [p for p in providers if p == "CPUExecutionProvider"]
                return ort.InferenceSession(model_path, sess_options=sess_options,
                                            providers=cpu_providers, **kwargs)
            raise

    def cv2_dnn_backend_target(self) -> Tuple[int, int]:
        """
        返回 cv2.dnn 的 (backend, target), 用于 OpenCV 加载的模型 (人脸检测等)。
        """
        import cv2
        backend = cv2.dnn.DNN_BACKEND_OPENCV
        target = cv2.dnn.DNN_TARGET_CPU
        if self.has_gpu():
            try:
                if cv2.cuda.getCudaEnabledDeviceCount() > 0:
                    backend = cv2.dnn.DNN_BACKEND_CUDA
                    target = cv2.dnn.DNN_TARGET_CUDA
            except Exception:
                pass
        return backend, target


# 全局单例: 进程内只探测一次
_device_manager: Optional[DeviceManager] = None


def get_device_manager(policy: Optional[Dict[str, str]] = None,
                       fallback: bool = True) -> DeviceManager:
    """获取全局 DeviceManager 单例"""
    global _device_manager
    if _device_manager is None:
        _device_manager = DeviceManager(policy=policy, fallback=fallback)
    return _device_manager
