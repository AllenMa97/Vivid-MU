# deploy/apks/ — 需自行下载放置的 APK

本目录用于存放部署所需的两个 APK。**仓库不附带任何 APK 文件**（已通过
`.gitignore` 的 `deploy/apks/*.apk` 规则排除），请自行下载后放入本目录：

```
deploy/apks/termux.apk        # Termux（F-Droid 或 GitHub Release 版）
deploy/apks/ipwebcam.apk      # IP Webcam
```

`make deploy` 会按文件名前缀（不区分大小写）自动识别 `termux*.apk` 与
`ipwebcam*.apk`；若手机上已安装对应应用，会自动跳过安装。

## 为什么 APK 不入库

**版权与再分发限制**：两款应用的作者/发行方保留著作权，第三方仓库随意再分发
二进制既不尊重作者，也可能违反其分发条款（Google Play 应用尤其如此）。
因此本仓库只提供官方下载渠道说明，由用户自行获取。

## 1. Termux（必装）

- **必须使用 F-Droid 版或 GitHub Release 版**。
- ⛔ **不要用 Google Play 版**：Play 版已停止更新（targetSDK 过旧），在新版
  Android 上无法正常 `pkg update`，且与本项目依赖的 RunCommandService 链路
  兼容性差，bootstrap 会失败。
- P20（Kirin 970）请选择 **arm64-v8a** 架构的 APK。

下载渠道（任选其一，二进制内容一致）：

| 渠道 | 地址 |
|---|---|
| F-Droid | https://f-droid.org/packages/com.termux/ |
| GitHub Releases（官方） | https://github.com/termux/termux-app/releases |

GitHub Release 的资产文件名形如
`termux-app_v0.118.0+github-debug_arm64-v8a.apk`，下载后重命名（或直接放置）
为 `deploy/apks/termux.apk` 即可。

> 说明：Termux 本体免费开源（GPLv3 等），此处提供的是官方渠道指引；
> 如需源码与文档见 https://github.com/termux/termux-app 。

## 2. IP Webcam（必装，作者 Pavel Khlebovich）

- 提供 8080 端口的 MJPEG/音频 HTTP 流，是 VividEye 回环录像
  （`http://127.0.0.1:8080/video`）的数据源。
- 包名：`com.pas.webcam`。

获取方式（按推荐顺序）：

1. **手机应用商店**（华为应用市场/Play 商店）直接搜索 "IP Webcam" 安装——
   `make deploy` 检测到已安装会自动跳过 APK 安装，无需放入本目录；
2. Google Play 页面：https://play.google.com/store/apps/details?id=com.pas.webcam
   （APK 提取工具导出后放入本目录，命名 `ipwebcam.apk`）；
3. 第三方 APK 镜像站：**不推荐**，无法保证签名与安全，风险自担。

> 免费版带水印广告，功能对本项目已足够；如需去除可自行购买 Pro 版（可选）。

## 3. 放置完成后

```bash
ls deploy/apks/          # 应看到 termux.apk 与 ipwebcam.apk
cd deploy && make deploy
```
