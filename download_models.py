import urllib.request
import sys
from pathlib import Path

MODELS_DIR = Path(__file__).parent / "src" / "models"

MODELS = {
    "deploy.prototxt": "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt",
    "res10_300x300_ssd_iter_140000.caffemodel": "https://github.com/opencv/opencv_3rdparty/raw/dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel"
}


def download_models():
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    
    for filename, url in MODELS.items():
        target_path = MODELS_DIR / filename
        
        if target_path.exists():
            print(f"[跳过] {filename} 已存在")
            continue
        
        print(f"[下载] {filename}...")
        try:
            urllib.request.urlretrieve(url, target_path)
            print(f"[完成] {filename} 下载成功")
        except Exception as e:
            print(f"[错误] {filename} 下载失败: {e}")
    
    print("\n模型下载完成!")
    print(f"模型目录: {MODELS_DIR}")


if __name__ == "__main__":
    download_models()
