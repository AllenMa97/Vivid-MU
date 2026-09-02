# VividEye · 华为 P20 刷机与清理完全指南（诚实版）

> **目标设备**：华为 P20（Kirin 970，EML-L29 / EML-L09 / 国行 EML-AL00 / EML-TL00；P20 Pro = CLT-\* 同样适用）
> **架构**：PHONE-FIRST——部署完成后，手机在 Termux 中独立运行全部项目（Python + ffmpeg + Web 服务），PC 只做首次部署工具。
> **本文承诺**：只写真实情况，不画饼。凡是社区传闻、不可靠渠道，都会明确标注风险。

---

## 路线图：先做选择

```
                     ┌─────────────────────────────┐
                     │  第0步：备份数据（必做）      │
                     └──────────────┬──────────────┘
                                    ▼
                     ┌─────────────────────────────┐
                     │  第1步：要不要解锁 Bootloader？│
                     └──────────┬───────────┬──────┘
                          不解锁(推荐)      解锁(进阶,自担风险)
                                ▼               ▼
                     ┌──────────────┐  ┌──────────────────┐
                     │ 第2A方案：    │  │ 第2B方案：        │
                     │ 保留EMUI清理  │  │ 刷第三方 ROM      │
                     │ (Termux免root)│  │ (可用性有限)      │
                     └──────────────┘  └──────────────────┘
                                ▼               ▼
                          ┌─────────────────────────┐
                          │ 三、make deploy 一键部署 │
                          └─────────────────────────┘
```

**结论先行**：本项目（Termux + Python + ffmpeg + Web 服务）**完全不需要 root、不需要解锁、不需要刷机**。
推荐直接走 **第2A方案（不解锁）**，保底可用、可随时反悔、不影响保修与支付安全。

---

## 第 0 步：数据备份（无论走哪条路线，先做这一步）

恢复出厂/三清/刷机会**永久清除**以下内容，请逐项确认已备份或迁移：

| 数据 | 建议备份方式 |
|---|---|
| 照片/视频 | USB 拖到 PC，或上传云盘（注意华为云空间免费容量） |
| 微信/QQ 聊天记录 | 微信"我→设置→聊天→聊天记录迁移"（换机/PC 均可） |
| 通讯录/短信 | 同步到华为账号或导出 .vcf 到 PC |
| **两步验证器**（Google Authenticator 等） | **务必先迁移**！恢复后无法找回，可能永久锁死账号 |
| 已登录 App 清单 | 截图一份（含银行/证券/邮箱），方便恢复后逐个重登 |
| 华为钱包/交通卡 | 先在"钱包"App 内移除/退卡 |
| Termux / VividEye 数据 | 若已部署过：`adb pull /sdcard/vivideye/ ./`（仅 tarball）；Termux 内部数据（~/vivideye）需自行 `tar` 到 /sdcard 再 pull |

---

## 第 1 步：Bootloader 解锁——现实情况（必读）

**诚实现状**：

1. 华为自 **2018 年 5 月** 起关闭了官方解锁码申请网站，此后新机型（含 P20 后期批次）**无法通过官方渠道获取解锁码**。
2. 目前能解锁 P20 的途径只剩：
   - **第三方付费解锁码服务**（如 DC-Unlocker 等）：需要付费、提供设备识别码，且**需把手机置于测试模式**。此类服务鱼龙混杂，存在拿钱跑路、泄露设备信息、解锁失败的风险。**风险自担，本文不作任何推荐。**
   - 某些"深度刷机/维修店"代解锁：同样有隐私与变砖风险。
3. 即使成功解锁，P20 的第三方 ROM 生态也很贫瘠（详见第 2B 方案），**解锁的收益远低于风险**。

**为什么本项目不需要解锁**：

| 需求 | 是否需要 root/解锁 |
|---|---|
| Termux 运行 Python/ffmpeg/Web 服务 | ❌ 不需要 |
| adb 安装应用、push 文件 | ❌ 不需要 |
| IP Webcam 回环录像 | ❌ 不需要 |
| pm uninstall --user 0 精简预装 | ❌ 不需要（adb shell 即可） |
| 调用云端 AI API | ❌ 不需要 |

**✅ 明确推荐：不解锁方案（第 2A）**。保留 EMUI，保底可用，随时可恢复原状，保修与华为支付/钱包安全不受影响。

---

## 第 2A 方案（推荐）：不刷机，深度清理 EMUI

按 2A-1 → 2A-5 顺序执行。核心思路：**先恢复出厂"清零"，再精简预装**（顺序反了的话，恢复出厂会把精简全部还原）。

### 2A-1 开发者模式与 USB 调试

1. `设置 → 系统和更新 → 关于手机` → 连点"版本号" 7 次，提示"您已处于开发者模式"。
2. 返回 `设置 → 系统和更新 → 开发人员选项`（EMUI 8 在 `设置 → 系统`），打开：
   - ✅ **USB 调试**
   - ✅ **仅充电模式下允许 ADB 调试**（华为专属坑！不开的话插 PC 就 offline）
   - ✅ **USB 安装**（允许通过 USB 安装应用；make deploy 装 APK 时需要）
3. USB 连 PC 后手机弹"是否允许 USB 调试"→ 勾选"始终允许此计算机"→ 允许。
4. PC 验证：`adb devices` 应显示 `device`（而非 `unauthorized`/`offline`）。

### 2A-2 恢复出厂（三清）

两种等价方式任选：

- **PC 一键**：`cd deploy && make reset`（内部即 `scripts/flash/reset-emui.sh`，会二次确认并把手机送入 Recovery，随后按屏幕指引操作）
- **纯手机**：`设置 → 系统和更新 → 重置 → 恢复出厂设置`（EMUI 8：`设置 → 系统 → 重置`）

三清 = 恢复出厂设置（清 userdata）+ 清缓存（cache）+ 清除应用数据。EMUI Recovery 内路径：`清除数据 → 恢复出厂设置`，可选再执行 `清除缓存分区`。

> 提醒：若开启了"查找我的手机"，恢复后开机会要求输入原华为账号密码（激活锁），属正常安全机制。
> 恢复后首次开机 5~15 分钟属正常，勿断电。

### 2A-3 精简预装应用（`pm uninstall --user 0`）

原理：`pm uninstall --user 0 <包名>` 只对**当前用户**隐藏/卸载，不改动 `/system` 分区——**不需要 root**，且：

- 幂等：重复执行无副作用；
- **可恢复**：`adb shell cmd package install-existing <包名>` 即可找回；
- 恢复出厂会自动还原全部精简。

先看机器上实际有哪些包（不同 EMUI 版本包名有差异，**删前先确认存在**）：

```bash
adb shell pm list packages | grep -i huawei
```

**✅ 一般可安全删除（常用第三方替代/不用即删）**：

| 包名 | 说明 |
|---|---|
| `com.huawei.himovie` | 华为视频 |
| `com.android.mediacenter` | 华为音乐 |
| `com.huawei.browser` | 华为浏览器（EMUI 8 老版本可能叫 `com.android.browser`） |
| `com.huawei.gamecenter` | 游戏中心 |
| `com.huawei.vassistant` | 语音助手 |
| `com.huawei.phoneservice` | 服务/我的华为 |
| `com.huawei.hiboard` | 负一屏智能助手（删后负一屏清空，桌面更流畅） |

**⚠️ 谨慎删除（删了会损失对应功能，自己权衡）**：

| 包名 | 说明 | 影响 |
|---|---|---|
| `com.huawei.android.thememanager` | 主题 | 无法换主题/壁纸 |
| `com.huawei.hidisk` | 云空间 | 云备份、查找手机受影响 |
| `com.huawei.hicloud` | 华为云服务 | 同上 |
| `com.huawei.android.hwpay` | 华为钱包/支付 | NFC 支付不可用 |
| `com.huawei.appmarket` | 应用市场 | **建议保留**：用它装 IP Webcam 最方便 |
| `com.huawei.android.hwouc` | 系统更新 | 删后无法 OTA（本项目反而建议关自动更新即可，不必删） |

**⛔ 绝对不能删（删了轻则功能异常，重则变砖/反复报错）**：

| 包名 | 说明 |
|---|---|
| `com.huawei.hwid` | **华为账号服务**——大量系统组件依赖它，删后设置闪退、云服务崩溃 |
| `com.huawei.systemmanager` | 手机管家——本项目后台白名单就靠它（见 2A-5） |
| `com.huawei.powergenie` | 智电/电源管理——删后充电与耗电策略异常 |
| `com.huawei.android.launcher` | 桌面（Launcher） |
| `com.android.systemui` / `com.android.phone` / `com.android.settings` / `android` | 系统框架级组件 |
| `com.termux` / `com.pas.webcam` | 本项目要用的两个应用 😄 |

批量执行示例（保存为文本逐行跑即可；**每行删前核对包名**）：

```bash
adb shell pm uninstall --user 0 com.huawei.himovie
adb shell pm uninstall --user 0 com.android.mediacenter
# 若提示 Failure，可尝试加 -k 参数：
# adb shell pm uninstall -k --user 0 <包名>
# 恢复某个应用：
# adb shell cmd package install-existing <包名>
```

### 2A-4 关闭自动更新（防止后台偷流量、偷电量、偷空间）

- **系统更新**：`设置 → 系统和更新 → 软件更新 → 右上角⚙设置` → 关闭"WLAN 环境下自动下载"与"夜间安装"。
- **应用市场**：`应用市场 → 我的 → 设置` → 关闭"WLAN 自动安装/自动更新"。
- **EMUI 8 路径略有不同**：在 `设置 → 系统 → 系统更新` 的菜单里找设置项。

### 2A-5 电池与后台白名单（EMUI 杀后台极狠，必做！）

这是 P20 长期挂机录像**成败的关键**，全部在手机上手动设置：

1. **启动管理**：`设置 → 电池 → 启动管理`（EMUI 8：`设置 → 应用 → 启动管理`），
   找到 **Termux** 与 **IP Webcam**：
   - 关闭"自动管理"，改为**手动管理**；
   - 三个开关全开：✅允许自启动 ✅允许关联启动 ✅允许后台活动。
2. **最近任务上锁**：多任务界面里，把 Termux 与 IP Webcam 的卡片**下拉/点击锁图标**上锁，防一键清理误杀。
3. **省电模式关闭**：`设置 → 电池` → 关闭"省电模式/超级省电"（会限制后台）。
4. **锁屏不断网**：`设置 → 电池 → 更多电池设置`（如有"休眠时始终保持网络连接"则打开）。
5. **充电挂机建议**：VividEye 是 7×24 录像场景，建议长期插电 + `设置 → 电池 → 更多电池设置 → 智能充电` 打开（限制满充，保护旧电池）。

---

## 第 2B 方案（进阶可选）：解锁后刷第三方 ROM —— 现状与风险

**诚实现状（2024-2026 观察，不构成任何镜像下载承诺）**：

1. **LineageOS 官方不支持 P20**：官方支持设备列表中**没有 EML**（P20）。存在的只是 XDA 等社区的非官方构建（多为 LineageOS 16/18.1 时代的产物），维护断续、版本偏旧。
2. **postmarketOS 未正式支持 P20**：不在官方设备列表；仅有社区实验性探索，**无法作为日常系统使用**。
3. **根本原因**：Kirin 970 是海思自研 SoC，内核/驱动闭源程度高（华为虽开源过 EML 内核源码，但 GPU Mali-G72 之外的 NPU/ISP/基带固件社区适配极难），导致第三方 ROM 相机、指纹、NFC、双卡、VoLTE 常有残缺。
4. **麒麟没有公开救砖通道**：高通设备有 9008 模式可线刷救砖，Kirin 没有等效公开方案。**一旦变砖，基本只能返修**。
5. 解锁 BL 后：华为钱包/NFC 支付降级、DRM 级别下降（视频高清 DRM 播放受影响）、OTA 通道失效。

**结论：除非你明确想折腾 ROM 本身，否则没有理由为 VividEye 走这条路。**

如果仍要尝试，**流程框架**如下（每一步都需要你自行寻找**当前仍有效的**资源，本仓库不提供镜像、不提供教程链接承诺）：

```
1. 备份（同第 0 步）
2. 解锁 Bootloader（第三方付费服务，风险自担；解锁会清数据）
3. 刷入第三方 Recovery（如 TWRP 的 EML 非官方移植版）
4. 双清后刷入社区 ROM 的 zip
5. （可选）刷 GApps / 固件补丁包
6. 首次开机 → 重装 Termux → 重新走本仓库 make deploy
```

**风险声明**：变砖、数据永久丢失、支付功能降级、相机/指纹异常、无法回锁、保修失效——全部由操作者自行承担。回刷 EMUI 官方包理论上可行（华为固件包 + 第三方工具），但同样依赖已失效/半失效的社区渠道，不保证成功。

---

## 三、把 VividEye 部署到 P20（PC 一次性操作）

### 3.0 前置条件

- 一台 Linux PC（已装 `adb`；`android-tools` 包）
- 一根**能传数据的** USB 线
- 两个 APK 已下载放入 `deploy/apks/`（下载地址与原因见 [`apks/README.md`](apks/README.md)）：
  - `termux.apk`（**必须 F-Droid 或 GitHub Release 版**，Play 版已弃更不可用；选 arm64-v8a）
  - `ipwebcam.apk`（或直接在手机应用商店装好，部署脚本会自动跳过）
- 手机已完成 2A-1（USB 调试）

### 3.1 一键部署

```bash
cd vivideye/deploy
make deploy            # 等价于 bash scripts/deploy.sh
```

脚本会依次：检查工具链 → 等待设备 → 校验 EML 机型 → 装 APK（已装跳过）→
推送项目 tarball 到 `/sdcard/vivideye/` → **暂停**，此时在手机上完成
**Termux 一次性手动步骤**（仅首次）：

```bash
# 手机上打开 Termux，逐行执行：
mkdir -p ~/.termux
echo "allow-external-apps=true" >> ~/.termux/termux.properties
termux-reload-settings
termux-setup-storage        # 弹窗点“允许”
```

> 为什么需要这一步：Termux 出于安全默认禁止其他 App（含 adb）远程执行命令；
> `allow-external-apps=true` 显式放开，是 RunCommandService 触发链路的必要条件。
> 属一次性配置，之后 PC 可随时远程触发。

回车继续后，脚本通过 `RunCommandService` 触发手机端 bootstrap（pkg 装包 → venv →
pip 依赖），全程幂等、失败可重跑。进度在手机 Termux 界面可见，日志在
`~/vivideye/deploy.log`。

### 3.2 部署体检

```bash
make doctor            # 检查 Termux 进程 / 8666(Web) / 8080(IP Webcam)，附修复提示
```

体检会自动建立 `adb forward`（PC 的 127.0.0.1:8666 → 手机 8666），PC 浏览器可直接打开。

### 3.3 手机上启动业务

```bash
# Termux 中：
cd ~/vivideye && source .venv/bin/activate
export VIVIDEYE_AI__API_KEY=sk-xxx        # 或编辑 user_config.yaml
vivideye main start                        # 或 python -m vivideye main start
```

然后打开 **IP Webcam → 启动视频服务器**（8080）。同一 WiFi 下任意设备访问
`http://<手机IP>:8666`。

### 3.4 故障排查速查表

| 现象 | 处理 |
|---|---|
| `adb devices` 显示 `unauthorized` | 手机上勾选"始终允许此计算机" |
| `adb devices` 显示 `offline` | 开发人员选项打开"仅充电模式下允许 ADB 调试"，重新插拔 |
| `adb install` 卡住/失败 | 看手机屏幕是否有安装确认弹窗；打开"USB 安装"开关 |
| RunCommandService 报错 | `termux.properties` 未配置 `allow-external-apps=true` 或未 `termux-reload-settings`；兜底：手机 Termux 里手动 `bash /sdcard/vivideye/bootstrap.sh` |
| `pkg update` 卡住/失败 | 网络问题：`termux-change-repo` 换镜像（国内推荐 TUNA/清华） |
| pip 装 Pillow 失败 | `pkg install -y build-essential binutils libjpeg-turbo zlib` 后重跑 |
| 8080 连不上 | IP Webcam 未点"启动视频服务器" |
| 8666 连不上 | Termux 里没启动主服务（`vivideye main start`） |
| 挂机几小时后断流 | EMUI 杀后台：重做 2A-5（启动管理 + 最近任务上锁） |
| 手机发烫/掉电快 | 正常（持续录像）；建议插电 + 智能充电；夏天注意散热 |

---

## 附：本工具包文件清单

| 文件 | 作用 |
|---|---|
| `Makefile` | `make deploy` / `make doctor` / `make reset` 入口 |
| `scripts/deploy.sh` | PC 端一键部署（本指南 3.1） |
| `scripts/phone-bootstrap.sh` | 手机端环境安装（被 deploy.sh 远程触发，也可手动跑） |
| `scripts/doctor.sh` | 部署后体检（3.2） |
| `scripts/flash/reset-emui.sh` | 恢复出厂/三清助手（2A-2，危险操作二次确认） |
| `apks/README.md` | 需自行下载放置的 APK 清单与版权说明 |
