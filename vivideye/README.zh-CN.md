<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <img src="docs/promo_hero.jpg" alt="VividEye —— 把旧安卓手机变成 AI 高光摄像机" width="800">
</p>

<h1 align="center">VividEye</h1>

<p align="center"><strong>它一直在看，只留精彩。</strong></p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/made_with-%F0%9F%92%9B-yellow" alt="Made with 💛">
</p>

**VividEye** 把一台旧安卓手机（在华为 P20 上开发实测）变成养宠/有娃家庭的 AI 高光摄像机：7×24 小时不停录制，云端大模型只看抽出来的少量帧来打分筛选，把真正值得回味的瞬间留下来，配上每天一页的"萌眼日报"；家人用家里任意设备的浏览器就能回看——而所有视频文件，始终只存在你自己手机里，不出家门。

<p align="center">
  <video src="docs/promo_vivideye.mp4" controls muted playsinline width="800" poster="docs/promo_hero.jpg"></video>
</p>
<p align="center"><sub>🎬 视频没有播放？可以<a href="docs/promo_vivideye.mp4">直接打开宣传片文件</a>。</sub></p>

---

## 🧭 它是怎么工作的

```
┌──────────────────── 华为 P20 · Termux ─────────────────────┐
│                                                             │
│  IP Webcam App ─▶ ffmpeg ─▶ 10 分钟一片 ─▶ 抽帧采样        │
│  （手机当眼睛）      切片落盘   滚动缓存 24h   （0.5 帧/秒）│
│                    （回环取流 :8080）                        │
└─────────────────────────────┬───────────────────────────────┘
                              │  只有抽帧图片
                              │  会离开家门
                              ▼
                ☁️  云端 VLM / LLM（阿里云百炼 · 通义千问）
                    打分 · 标题 · 标签 · 精彩时间段
                              │
┌─────────────────────────────┴───────────────────────────────┐
│  高光库（mp4 + 缩略图 + SQLite 数据库）                     │
│  日报（Markdown，AI 撰写，超时自动降级本地模板）            │
│                              │                              │
│                    FastAPI Web 界面（:8666）                │
└─────────────────────────────┬───────────────────────────────┘
                              ▼
             📱 💻 📺  家里同一 WiFi 下的任意设备
                  http://<手机IP>:8666
```

**手机是眼睛，云端是大脑，家里任何设备是遥控器。** 手机自己拍自己、自己存视频、自己跑网页服务；云端"大脑"每次只会收到一个片段里的十几张抽帧图片——你的完整视频一个字节都不会上传。

> 小白说明：Termux 是一个安卓上的"迷你 Linux"，装上它手机就能跑 Python 和 ffmpeg，这正是本项目不需要刷机的原因；IP Webcam 则负责把摄像头变成一个本地视频流。

## 🐾 适合谁 / 需要什么

家里有毛孩子或小朋友，抽屉里躺着一台旧安卓手机，而你更愿意每天看 3 分钟精选、而不是翻 24 小时录像——VividEye 就是为你做的。

| 需要什么 | 说明 |
|---|---|
| 一台旧安卓手机 | Android 7 及以上；在**华为 P20** 上开发实测（P20 Pro 同样适用） |
| 家里 WiFi | 手机和看回放的设备连同一个网络 |
| 常供电 | 7×24 录制意味着手机需要一直插着电 |
| 一台电脑 | **只在部署时用一次**：Linux、Windows 上的 Git Bash 或 WSL 均可 |
| 一个 DashScope API Key | 注册免费，见下方[密钥指南](#-api-key-获取与费用指南) |

## 🔓 不需要刷机、不需要解锁、更不需要 root

**先把最重要的结论放在这里：VividEye 完全不需要 root、不需要解锁 Bootloader、不需要刷任何第三方 ROM。** 它只是两个普通 App（[Termux](https://termux.dev/) + [IP Webcam](https://play.google.com/store/apps/details?id=com.pas.webcam)）加一段 Python 代码，装完随时可以卸载还原，保修、华为钱包/支付、系统更新统统不受影响。

如果想让这台旧手机长期"值班"更稳，我们推荐（可选，不强制）做一次 EMUI 深度清理：卸载不用预装、关掉自动更新、把两个 App 加入电池白名单。完整且诚实的操作指南（包括哪些预装绝对不能删）在 [`deploy/README-FLASH.md`](deploy/README-FLASH.md)。

## 🚀 快速上手

完整详细版（含数据备份、EMUI 清理、每一个坑）在 [`deploy/README-FLASH.md`](deploy/README-FLASH.md)，这里是最短路径：

```bash
# 1) 下载两个 APK 放进 deploy/apks/（详见 deploy/apks/README.md）：
#    termux.apk    —— 必须是 F-Droid 或 GitHub 版（Google Play 版已停止维护，不可用）
#    ipwebcam.apk  —— 或者直接在手机自带应用商店搜 "IP Webcam" 安装
cd deploy
make deploy          # 装 APK → 推送项目到手机 → 触发手机端自动装环境
make doctor          # 部署体检：检查两个 App、Termux 进程、8666/8080 服务
```

然后在手机上：打开 **IP Webcam → 启动视频服务器**；再打开 Termux 执行 `cd ~/vivideye && source .venv/bin/activate && python main.py start`。家里任意设备浏览器打开 `http://<手机IP>:8666`，在**设置页**贴上 API Key，就跑起来了。

**诚实时间表**（因人而异，网络慢会更久）：

| 步骤 | 大约耗时 |
|---|---|
| 可选：EMUI 清理与数据备份 | ~20 分钟 |
| `make deploy` + 手机端自动装环境 | ~15 分钟 |
| 首次配置（贴 Key、选场景模式） | ~15 分钟 |
| 等来第一支高光 | 之后再等 ≤ 30 分钟 |

## 🔑 API Key 获取与费用指南

VividEye 的"大脑"默认用阿里云百炼（DashScope，通义千问系列），也兼容任何 OpenAI 风格的接口。

1. 注册阿里云账号，进入 [百炼控制台](https://bailian.console.aliyun.com/)；
2. 开通 DashScope 模型服务（新用户通常有免费额度，先薅一薅）；
3. 在控制台创建一个 API Key；
4. 粘贴到 VividEye 网页的**设置页**即可（只保存在手机本地的 `user_config.yaml`，不上传到任何地方；也可以 `export VIVIDEYE_AI__API_KEY=sk-xxx`）。

**费用估算：** 按默认设置（每段只抽 0.5 帧/秒、每批最多 8 段），家庭日常使用**大约每天几毛到几元**。想更省，把 `pipeline.sample_fps` 或 `pipeline.max_segments_per_run` 调小即可；追着跑的猫狗越多、越热闹，花费略高。

**还没有 Key？也能先玩：** 录制和网页功能完全不受影响，7×24 视频照存——只是不会生成高光和分析，日报会降级为本地统计模板。什么时候把 Key 贴上，积压的片段会自动补处理，一天都不浪费。

## 📅 日常使用

手机往角落一架，剩下的就是打开 `http://<手机IP>:8666`（手机、平板、电脑、电视浏览器都行）：

- **✨ 高光墙** —— 卡片式瀑布流：缩略图 + AI 写的标题、评分和标签；点开播放，可以 ❤️ 收藏或 🗑️ 删除
- **❤️ 收藏** —— 收藏的高光**永不**被自动清理，是真正的"家庭相册"
- **📹 实时画面** —— 随时看一眼摄像头当下在拍什么
- **📖 日报** —— 每天一页，AI 把当天的精彩瞬间写成一篇小故事
- **⚙️ 设置** —— 场景模式（自动/宠物/萌娃/居家）、API Key，以及**"立即处理"按钮**：不想等 30 分钟定时任务时，点它马上分析最新录像

## 🔒 隐私与安全

- **视频不出家门。** 原始录像和高光片段全部只存在手机本地；云端只收到抽帧图片（默认每秒 0.5 张）并返回打分和文字，仅此而已。
- **没有账号、没有遥测、没有云上传。**
- **诚实的风险说明：** 网页界面**没有登录鉴权**，同一 WiFi 下的任何人都能看到高光和设置。这是家庭工具刻意换来的简单——请只在**自己家的 WiFi** 下使用，不要接公共/共享网络。确有外出访问需求，请走 VPN 隧道（如 Tailscale/WireGuard），不要直接在路由器上做端口映射暴露到公网。

## 💾 存储与维护

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `capture.retention_hours` | 24 小时 | 原始片段滚动保留窗口，过期自动删除 |
| `storage.highlights_retention_days` | 30 天 | 非收藏高光的保留期；**收藏的永不删除** |
| `storage.min_free_gb` | 2 GB | 剩余空间低于此值自动暂停录制，腾出空间后自动续录 |

**容量账（640×480 MJPEG 约 1–3 GB/小时）：**

| 分辨率 | 每小时占用 | 24 小时滚动缓存 |
|---|---|---|
| 640×480（推荐） | ~1–3 GB | ~8–30 GB |
| 1280×720 | ~2–5 GB | ~16–50 GB——对 P20 仍然偏重 |

P20 总共只有 64–128 GB 存储，所以默认只滚动保留 24 小时原始录像（存储安全优先）。空间充裕想留更久的滚动窗口，可调大 `capture.retention_hours`，或在 IP Webcam 里降低分辨率。高光本身很小（只是几秒到几十秒的短片），不用担心。随时可以在 Termux 里跑 `python main.py status` 查看磁盘水位、待处理片段数和最近文件。

## 🔁 重启之后怎么办

诚实回答：目前手机重启后不会自动满血复活，需要两步手动：

1. 打开 **IP Webcam** → 点 **启动视频服务器**；
2. 打开 **Termux** → `cd ~/vivideye && source .venv/bin/activate && python main.py start`。

进阶玩法：安装 Termux 官方搭档 [Termux:Boot](https://github.com/termux/termux-boot)，写一个开机自动执行上面命令的脚本，就能免手动。另外每次大版本系统更新后，记得回 [`deploy/README-FLASH.md`](deploy/README-FLASH.md) §2A-5 复查电池白名单——EMUI 悄悄杀后台是出了名的。

## 🩺 故障排查

第一招永远是：`make doctor`（在 `deploy/` 目录下执行）。它会自动检查设备连接、两个 App 安装、Termux 进程、8666/8080 两个服务，每一项失败都附带修复提示。

| 症状 | 处理 |
|---|---|
| 录制几小时后悄悄停了 | EMUI 杀后台——重做 [`deploy/README-FLASH.md`](deploy/README-FLASH.md) §2A-5 电池白名单，并在最近任务里把两个 App 上锁 |
| `8666` 打不开 | Termux 主服务没跑——重开 Termux，`python main.py start` |
| `8080` 连不上 | IP Webcam 没启动——打开 App 点"启动视频服务器" |
| 等了几个小时还没有高光 | 按链路排查：IP Webcam 在跑吗 → Termux 在跑吗 → API Key 保存了吗 → 设置页点**立即处理**看结果；再检查 `pipeline.min_highlight_score` 是否被调得过高 |
| `adb devices` 显示 `unauthorized`/`offline` | 手机上重新授权；华为专属坑：打开"仅充电模式下允许 ADB 调试" |
| `pkg`/`pip` 下载慢或失败 | 执行 `termux-change-repo` 换国内镜像（推荐清华 TUNA） |

## ⚙️ 配置参考

把 [`config_template.yaml`](config_template.yaml) 复制成 `user_config.yaml`（已被 git 忽略，放心填 Key）直接改——大部分常用项在网页设置页就能改。环境变量也行：`VIVIDEYE_AI__API_KEY` 对应 `ai.api_key`（双下划线 `__` 表示层级）。

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `capture.source_url` | `http://127.0.0.1:8080/video` | 相机 App 的回环 MJPEG 视频流地址 |
| `capture.segment_seconds` | 600 | 每个原始片段时长（秒） |
| `capture.retention_hours` | 24 | 原始录像滚动保留窗口 |
| `pipeline.run_interval_minutes` | 30 | 多久自动分析一批新片段 |
| `pipeline.max_segments_per_run` | 8 | 每批最多处理几段（对手机和钱包都友好） |
| `pipeline.min_highlight_score` | 0.55 | AI 打分达到多少才算高光 |
| `pipeline.scene_mode` | auto | `auto`/`pet`/`kid`/`home`，调识别偏好 |
| `pipeline.sample_fps` | 0.5 | 每秒抽几帧送云端（最直接的费用旋钮） |
| `ai.provider` | dashscope | `dashscope`/`openai`/`compatible`（任何 OpenAI 兼容接口） |
| `ai.api_key` | 空 | 你的密钥；或环境变量 `VIVIDEYE_AI__API_KEY` |
| `ai.vision_model` | qwen3-vl-flash | 负责看图打分的视觉大模型 |
| `storage.highlights_retention_days` | 30 | 非收藏高光保留天数（收藏永久） |
| `storage.min_free_gb` | 2 | 录制暂停的剩余空间阈值 |
| `server.port` | 8666 | 网页服务端口 |

全部默认值见 [`vivideye/config.py`](vivideye/config.py)。

## 🗺️ 路线图

- 🎥 多机位：几台旧手机一起看，一面高光墙
- 🕹️ 多视角"子弹时间"：房间四角多台设备多视角采集、分布式计算与存储，把某个高光时刻三维重建成 NBA"立体暂停"式的名场面
- 🧠 本地小模型：ONNX 端侧推理，更强的手机可完全离线
- 📱 App 壳：给网页套个原生壳，桌面一点就开
- 🌍 公网访问方案：整理一份安全的 Tailscale/WireGuard 远程访问指南
- 🎨 更多 AI 玩法：自动生成海报、配乐、每周精华混剪

## 🤝 贡献与致谢

欢迎提 Issue 和 PR——这是用爱维护的业余项目，回复可能不快，但每一条都会认真看。

- **VividMU 项目** —— 本项目的高光分析管线源自 VividMU，感谢它打下的地基
- [Termux](https://termux.dev/) —— 不需要 root 的完整 Linux 环境
- [IP Webcam](https://play.google.com/store/apps/details?id=com.pas.webcam)（作者 Pavel Khlebovich）—— 负责当眼睛
- [FastAPI](https://fastapi.tiangolo.com/)、[ffmpeg](https://ffmpeg.org/)，以及阿里云百炼上的通义千问模型家族

## 💛 作者与赞助

**AllenMa** · 邮箱：851132789@qq.com

如果 VividEye 让你家的旧手机重获新生，欢迎请我喝杯咖啡——**让更多旧手机重获新生**。

- 微信 / 支付宝：**13760777424**

对这个项目感兴趣的投资人，欢迎邮件联系。

## 📄 许可证

[MIT](LICENSE) © 2026 AllenMa
