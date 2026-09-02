import urllib.request
import sys
from pathlib import Path

MODELS_DIR = Path(__file__).parent / "src" / "models"

# 核心模型 (建议全部下载)
MODELS = {
    # 人脸检测 (OpenCV Caffe)
    "deploy.prototxt": "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt",
    "res10_300x300_ssd_iter_140000.caffemodel": "https://github.com/opencv/opencv_3rdparty/raw/dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel",
    # Silero VAD (语音活动检测, P2, 约 2MB)
    "silero_vad.onnx": "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx",
}

# YOLOv8n 目标检测 (P1, 约 12MB) - 提供宠物/人物/互动语义特征
# 官方不直接分发 onnx, 这里提供社区预导出版本 (opset13, 静态 640) + 备用源
YOLO_ONNX_URLS = [
    "https://github.com/chiwei085/yolo_models_repo/raw/refs/heads/master/exports/onnx/yolov8n_img640_static_opset13.onnx",
    "https://github.com/yoobright/yolo-onnx/raw/master/yolov8n.onnx",
]

# 可选大模型 (默认不下载, 磁盘紧张可跳过)
# CLIP ViT-B/32 图像编码器 (P2, 约 330MB) - 提供真实语义多样性/独特性
OPTIONAL_MODELS = {
    "clip_vit_b32.onnx": "https://huggingface.co/onnx-community/clip-vit-base-patch32/resolve/main/model.onnx",
}


def download(url: str, target: Path) -> bool:
    if target.exists() and target.stat().st_size > 0:
        print(f"[跳过] {target.name} 已存在")
        return True
    print(f"[下载] {target.name} ...")
    try:
        urllib.request.urlretrieve(url, str(target))
        print(f"[完成] {target.name} 下载成功 ({target.stat().st_size / 1024 / 1024:.1f} MB)")
        return True
    except Exception as e:
        print(f"[错误] {target.name} 下载失败: {e}")
        return False


def main():
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("下载核心模型 (YOLO / 人脸 / VAD)")
    print("=" * 60)
    for filename, url in MODELS.items():
        download(url, MODELS_DIR / filename)

    # YOLO 使用多源下载
    yolo_target = MODELS_DIR / "yolov8n.onnx"
    if yolo_target.exists() and yolo_target.stat().st_size > 0:
        print("[跳过] yolov8n.onnx 已存在")
    else:
        ok = False
        for url in YOLO_ONNX_URLS:
            if download(url, yolo_target):
                ok = True
                break
        if not ok:
            print("[错误] yolov8n.onnx 所有源均下载失败")

    if "--with-clip" in sys.argv:
        print("\n" + "=" * 60)
        print("下载可选大模型 (CLIP, 约 330MB)")
        print("=" * 60)
        for filename, url in OPTIONAL_MODELS.items():
            download(url, MODELS_DIR / filename)
    else:
        print("\n[提示] 未下载 CLIP 模型 (可选, 约 330MB)。")
        print("       如需真实场景语义多样性, 可运行: python download_models.py --with-clip")

    print("\n模型下载完成!")
    print(f"模型目录: {MODELS_DIR}")


if __name__ == "__main__":
    main()
