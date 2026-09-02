#!/usr/bin/env python3
"""D2 探针：验证直接原子写 user_config.yaml 后，运行中的 main.py start
是否能在 60s 巡检周期内感知 min_free_gb 恢复并续录。"""
import subprocess, sys, time, json, signal, os
from pathlib import Path

REPO = Path("/workspace/vivideye")
PY = "/root/miniconda3/envs/gnt/bin/python"
WORK = Path("/workspace/.d2_probe")
WORK.mkdir(exist_ok=True)
LOGS = WORK / "logs"; LOGS.mkdir(exist_ok=True)

sys.path.insert(0, str(REPO / "tests"))
import test_e2e_blackbox as T

# 复用测试基建：假摄像头 + mock AI
frames_dir = WORK / "frames"; frames_dir.mkdir(exist_ok=True)
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=5",
                "-t", "2", "-q:v", "7", str(frames_dir / "f_%03d.jpg")], check=True)
frames = [p.read_bytes() for p in sorted(frames_dir.glob("f_*.jpg"))]
cam = T.FakeMjpegCamera(frames, fps=25); cam.start()
mock = T.MockOpenAI(json.dumps(T.MOCK_VISION, ensure_ascii=False)); mock.start()
print(f"camera={cam.url} mock={mock.port}")

web_port = T.free_port()
cfg = T.base_cfg(cam.url, web_port, mock.port, min_free_gb=999999)
T.write_user_config(cfg)
log = LOGS / "d2.log"
fh = open(log, "wb")
proc = subprocess.Popen([PY, "main.py", "start"], cwd=str(REPO), stdout=fh,
                        stderr=subprocess.STDOUT,
                        env={**os.environ, "PYTHONPATH": str(REPO)})
try:
    ok = T.wait_web(web_port, 30)
    print(f"web up: {ok}")
    paused = T.wait_for(lambda: "暂停录制" in T.read_log(log), 30)
    print(f"paused: {paused}")
    cnt = T.seg_count()
    print(f"seg at pause: {cnt}")

    # --- 步骤1：POST /api/config 尝试写 storage.min_free_gb（应被忽略） ---
    import requests
    r = requests.post(f"http://127.0.0.1:{web_port}/api/config",
                      json={"storage": {"min_free_gb": 0.0001}}, timeout=5)
    print(f"POST storage: status={r.status_code} body={json.dumps(r.json(), ensure_ascii=False)[:300]}")

    # --- 步骤2：直接原子写 user_config.yaml 恢复 min_free_gb ---
    cfg["storage"]["min_free_gb"] = 0.0001
    import yaml
    tmp = REPO / "user_config.yaml.tmp"
    tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(tmp, REPO / "user_config.yaml")
    print(f"user_config.yaml atomically rewritten (min_free_gb=0.0001) at {time.strftime('%T')}")

    # --- 步骤3：等 90s 巡检，看是否出现“继续录制” ---
    resumed = T.wait_for(lambda: "继续录制" in T.read_log(log), 90)
    print(f"resumed (pure file edit, no POST): {resumed}")

    # 若纯文件编辑无效，尝试 POST 一个白名单段触发 config 重载
    if not resumed:
        r2 = requests.post(f"http://127.0.0.1:{web_port}/api/config",
                           json={"app": {"language": "zh_CN"}}, timeout=5)
        print(f"POST benign app section: status={r2.status_code} ok={r2.json().get('ok')}")
        resumed2 = T.wait_for(lambda: "继续录制" in T.read_log(log), 90)
        print(f"resumed after benign POST (reload trigger): {resumed2}")
        got = T.wait_for(lambda: T.seg_count() > cnt, 40)
        print(f"segments resumed: {got} ({cnt} -> {T.seg_count()})")
    else:
        got = T.wait_for(lambda: T.seg_count() > cnt, 40)
        print(f"segments resumed: {got} ({cnt} -> {T.seg_count()})")
finally:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try: proc.wait(35)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(5)
    fh.close()
    subprocess.run(["pkill", "-f", str(T.RAW)], capture_output=True)
    try: cam.kill()
    except Exception: pass
    mock.stop()
    print("--- log tail ---")
    print(T.read_log(log)[-2000:])
