#!/usr/bin/env bash
# ============================================================================
# VividEye 一键部署脚本（PC 端 · Linux）
#
# 流程：
#   1. 检查 adb / fastboot 工具链
#   2. 等待华为 P20 接入并完成 USB 调试授权
#   3. 校验机型（EML* = P20，CLT* = P20 Pro 亦可）
#   4. 安装 Termux 与 IP Webcam APK（deploy/apks/ 下；已安装则自动跳过）
#   5. 打包项目（排除 .git/__pycache__/data/APK）推送到手机 /sdcard/vivideye/
#   6. 通过 Termux RunCommandService 触发手机端 bootstrap
#
# 特性：幂等可重跑；每步失败都会给出修复提示后退出，修复后重新执行即可。
#
# 用法：
#   bash scripts/deploy.sh [--yes]     # --yes 跳过所有交互确认（自动化用）
#   make deploy                        # 等价（在 deploy/ 目录下）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/.." && pwd)"
APK_DIR="$DEPLOY_DIR/apks"

# ---- 与手机端约定的路径 / 常量 ----
PHONE_DIR="/sdcard/vivideye"
TARBALL_NAME="vivideye.tar.gz"
BOOTSTRAP_NAME="bootstrap.sh"
TERMUX_PKG="com.termux"
IPWEBCAM_PKG="com.pas.webcam"
TERMUX_SVC="com.termux/com.termux.app.RunCommandService"
TERMUX_BASH="/data/data/com.termux/files/usr/bin/bash"
WAIT_TIMEOUT="${VIVIDEYE_WAIT_TIMEOUT:-300}"   # 等待设备接入的最长秒数
AUTO_YES=0

# ---------------- 彩色输出（非终端自动去色） ----------------
if [ -t 1 ]; then
  C_RED=$'\e[0;31m'; C_GRN=$'\e[0;32m'; C_YEL=$'\e[1;33m'
  C_BLU=$'\e[0;34m'; C_CYA=$'\e[0;36m'; C_RST=$'\e[0m'
else
  C_RED=''; C_GRN=''; C_YEL=''; C_BLU=''; C_CYA=''; C_RST=''
fi
step() { printf '%s\n' "${C_BLU}==>${C_RST} ${C_CYA}$*${C_RST}"; }
ok()   { printf '%s\n' "${C_GRN}[ 成功 ]${C_RST} $*"; }
warn() { printf '%s\n' "${C_YEL}[ 警告 ]${C_RST} $*"; }
fail() { printf '%s\n' "${C_RED}[ 失败 ]${C_RST} $*" >&2; }
die()  { fail "$*"; exit 1; }

usage() {
  cat <<'EOF'
VividEye 一键部署（PC 端）

用法: bash scripts/deploy.sh [--yes]

选项:
  -y, --yes    跳过交互确认（机型不符不拦截、不暂停等待手动步骤）
  -h, --help   显示本帮助

环境变量:
  VIVIDEYE_WAIT_TIMEOUT=300   等待设备接入的超时秒数（默认 300）
EOF
}

# ---------------- 参数解析 ----------------
for a in "$@"; do
  case "$a" in
    -y|--yes) AUTO_YES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数: $a（仅支持 --yes / --help）" ;;
  esac
done

# adb 输出统一去 \r（Android shell 会带回车），失败不炸脚本
adb_sh() { adb shell "$@" 2>/dev/null | tr -d '\r' || true; }

# 交互确认（AUTO_YES=1 或非终端时由调用方决定是否放行）
confirm_or_die() {
  if [ "$AUTO_YES" -eq 1 ]; then
    warn "（--yes 模式）跳过确认，继续执行"
    return 0
  fi
  if [ -t 0 ]; then
    local ans=""
    read -r -p "是否继续？[y/N] " ans || true
    case "$ans" in
      y|Y|yes|YES) return 0 ;;
      *) die "已按用户要求中止" ;;
    esac
  else
    die "当前为非交互环境；如确认要在该机型上继续，请追加 --yes 参数重跑"
  fi
}

cat <<EOF
${C_CYA}============================================================
 VividEye 一键部署（PHONE-FIRST：部署完成后手机独立运行）
============================================================${C_RST}
 PC   仓库根目录 : $REPO_ROOT
 手机 目标目录   : $PHONE_DIR/（代码最终安装到 Termux 家目录 ~/vivideye）
============================================================
EOF

# ================= 步骤 1：检查 adb / fastboot =================
step "步骤 1/6：检查 adb / fastboot 工具链"
if command -v adb >/dev/null 2>&1; then
  ok "adb 已安装：$(adb --version 2>/dev/null | head -n 1 || echo adb)"
else
  cat <<'EOF'
[ 失败 ] PC 上未安装 adb。安装方式：
  Debian/Ubuntu : sudo apt install adb
  Fedora        : sudo dnf install android-tools
  Arch          : sudo pacman -S android-tools
（安装后可插拔手机重跑本脚本，进度可断点续跑）
EOF
  die "缺少 adb，无法继续"
fi
if command -v fastboot >/dev/null 2>&1; then
  ok "fastboot 已安装（仅“第2B方案 刷机”时需要；本脚本用不到）"
else
  warn "fastboot 未安装——不影响本次部署（仅在解锁刷机时才需要，见 README-FLASH.md 第2B方案）"
fi

# ================= 步骤 2：等待设备接入并授权 =================
step "步骤 2/6：等待手机接入（保持亮屏，用数据线连接 PC）"
deadline=$(( $(date +%s) + WAIT_TIMEOUT ))
while true; do
  state="$(adb get-state 2>/dev/null || true)"
  case "$state" in
    device)
      ok "设备已连接并授权"
      break
      ;;
    unauthorized)
      warn "已检测到设备但未授权：请在手机弹窗中勾选“始终允许此计算机”并点“允许”"
      ;;
    *)
      warn "等待设备接入中……（剩余 $(( deadline - $(date +%s) ))s）"
      [ "$(date +%s)" -ge "$deadline" ] && die "等待设备超时（${WAIT_TIMEOUT}s）。排查提示：
  1) 换一条能传数据的 USB 线 / 换 USB 口
  2) 手机：设置→系统和更新→开发人员选项→打开 USB 调试
  3) EMUI 专属坑：同时打开“仅充电模式下允许 ADB 调试”，否则插入即 offline
  4) 手机上重新插拔，留意授权弹窗"
      ;;
  esac
  sleep 3
done

# ================= 步骤 3：校验机型 =================
step "步骤 3/6：校验设备机型"
MODEL="$(adb_sh getprop ro.product.model)"
DEVICE="$(adb_sh getprop ro.product.device)"
ANDROID="$(adb_sh getprop ro.build.version.release)"
[ -n "$MODEL" ] || die "无法读取 ro.product.model（设备连接异常，请重跑）"
info_model="$MODEL（device=$DEVICE，Android $ANDROID）"
case "$MODEL $DEVICE" in
  EML*|*"EML"*)
    ok "华为 P20 系列确认：$info_model"
    ;;
  CLT*|*"CLT"*)
    warn "检测到 P20 Pro（$info_model）——同为 Kirin 970，本项目同样适用"
    ;;
  *)
    warn "非 P20 机型：$info_model"
    warn "继续部署可能因 Android 版本/性能差异出现不可预期行为"
    confirm_or_die
    ;;
esac

# ================= 步骤 4：安装 APK =================
step "步骤 4/6：安装 Termux 与 IP Webcam（已安装则跳过）"

pkg_installed() { adb_sh pm list packages | grep -qx "package:$1"; }

# 在 apks/ 目录下按文件名前缀（不区分大小写）查找 APK
find_apk() {
  local want="$1" f base found=""
  for f in "$APK_DIR"/*.apk; do
    [ -e "$f" ] || continue
    base="$(basename "$f" | tr '[:upper:]' '[:lower:]')"
    case "$base" in
      "$want"*) found="$f"; break ;;
    esac
  done
  printf '%s' "$found"
}

install_apk() {
  local label="$1" pkg="$2" prefix="$3" apk out
  if pkg_installed "$pkg"; then
    ok "$label 已安装（$pkg），跳过"
    # Play 版 Termux 已弃更，在 Android 10+ 上无法正常工作，特别提醒
    if [ "$pkg" = "$TERMUX_PKG" ]; then
      local installer
      installer="$(adb_sh pm list packages -i | grep "^package:$pkg " || true)"
      if printf '%s' "$installer" | grep -q 'installer=com.android.vending'; then
        warn "检测到 Google Play 版 Termux（已停止更新，bootstrap 大概率失败）"
        warn "建议：在手机上卸载 Termux，按 deploy/apks/README.md 换用 F-Droid/GitHub 版后重跑本脚本"
      fi
    fi
    return 0
  fi
  apk="$(find_apk "$prefix")"
  if [ -z "$apk" ]; then
    cat <<EOF
[ 失败 ] deploy/apks/ 中未找到 $label 的 APK（期望文件名前缀：${prefix}*.apk）

请自行下载后放入（版权原因不入库，详见 deploy/apks/README.md）：
  1) Termux —— 务必 F-Droid 或 GitHub Release 版（arm64-v8a），Play 版不可用
       https://f-droid.org/packages/com.termux/
       https://github.com/termux/termux-app/releases
       保存为: deploy/apks/termux.apk
  2) IP Webcam（作者 Pavel Khlebovich）
       https://play.google.com/store/apps/details?id=com.pas.webcam
       保存为: deploy/apks/ipwebcam.apk
       （也可直接在手机自带应用商店安装，本脚本检测到已装会自动跳过）
EOF
    die "缺少 $label 的 APK，无法继续"
  fi
  step "  正在安装 $label：$(basename "$apk")"
  out="$(adb install -r "$apk" 2>&1 || true)"
  if printf '%s' "$out" | grep -q 'Success'; then
    ok "$label 安装成功"
  else
    fail "adb install 输出：$out"
    cat <<'EOF'
修复提示：
  - 看手机屏幕！EMUI 通过 adb 安装时会弹“是否安装”确认框，需手动点允许
  - 开发人员选项中打开“USB 安装 / 仅充电模式下允许 ADB 调试”相关开关
  - 清理手机存储空间后重试（本步骤幂等，adb install -r 可重复执行）
EOF
    die "$label 安装失败"
  fi
}

install_apk "Termux"   "$TERMUX_PKG"   "termux"
install_apk "IP Webcam" "$IPWEBCAM_PKG" "ipwebcam"

# ================= 步骤 5：打包项目并推送到手机 =================
step "步骤 5/6：打包项目并推送到手机 $PHONE_DIR/"

# bootstrap 脚本要推到手机执行，先确保没有 CRLF 换行（否则手机上无法运行）
if grep -q $'\r' "$SCRIPT_DIR/phone-bootstrap.sh" 2>/dev/null; then
  die "scripts/phone-bootstrap.sh 含 CRLF 换行符；请先执行 sed -i 's/\r\$//' 后重跑"
fi

TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT
TARBALL="$TMPD/$TARBALL_NAME"

tar -czf "$TARBALL" \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.apk' \
    --exclude='data' \
    --exclude='.venv' \
    -C "$REPO_ROOT" .
ok "项目打包完成：$TARBALL_NAME（$(du -h "$TARBALL" | cut -f1)）"

adb shell mkdir -p "$PHONE_DIR" >/dev/null 2>&1 || true
if ! adb push "$TARBALL" "$PHONE_DIR/$TARBALL_NAME" 2>&1; then
  die "推送 tarball 失败。排查：数据线/手机解锁屏幕/存储空间是否充足"
fi
ok "已推送 $PHONE_DIR/$TARBALL_NAME"
if ! adb push "$SCRIPT_DIR/phone-bootstrap.sh" "$PHONE_DIR/$BOOTSTRAP_NAME" 2>&1; then
  die "推送 bootstrap.sh 失败。排查：数据线/手机解锁屏幕/存储空间是否充足"
fi
ok "已推送 $PHONE_DIR/$BOOTSTRAP_NAME"

# ================= 步骤 6：触发 Termux bootstrap =================
step "步骤 6/6：触发 Termux 执行 bootstrap（RunCommandService）"

cat <<'EOF'
------------------------------------------------------------------
【一次性手动步骤】如果这是首次安装 Termux，请现在拿起手机：
  1. 打开 Termux 应用，等待首次初始化完成（出现 $ 提示符）
  2. 允许 PC 远程触发（本脚本的核心依赖）：
       mkdir -p ~/.termux
       echo "allow-external-apps=true" >> ~/.termux/termux.properties
       termux-reload-settings
  3. 授予存储权限（弹窗点“允许”）：
       termux-setup-storage
  以上三步仅需做这一次；之后 PC 即可随时远程触发 Termux 执行脚本。
------------------------------------------------------------------
EOF

if [ "$AUTO_YES" -eq 1 ]; then
  echo "（--yes 模式：不暂停，直接触发；若 Termux 未完成上述设置会失败并给出提示）"
elif [ -t 0 ]; then
  read -r -p "手机上完成上述步骤后，按回车继续（Ctrl+C 取消）..." || true
else
  warn "非交互环境：将直接触发（若 Termux 未完成上述一次性设置，触发会失败）"
fi

# 触发 RunCommandService：让 Termux 用 bash 执行 /sdcard/vivideye/bootstrap.sh
# bootstrap.sh 会自动解压项目到 ~/vivideye 并安装 Python 环境（详见脚本注释）
trigger_bootstrap() {
  local mode="$1" out
  out="$(adb shell "$mode" --user 0 \
      -n "$TERMUX_SVC" \
      -a "${TERMUX_PKG}.RUN_COMMAND" \
      --es "${TERMUX_PKG}.RUN_COMMAND_PATH" "$TERMUX_BASH" \
      --esa "${TERMUX_PKG}.RUN_COMMAND_ARGUMENTS" "$PHONE_DIR/$BOOTSTRAP_NAME" \
      --ez "${TERMUX_PKG}.RUN_COMMAND_BACKGROUND" true 2>&1 || true)"
  printf '%s\n' "$out"
  # am 命令报错时输出含 Error/Exception 等关键字
  if printf '%s' "$out" | grep -qiE 'error|exception|not allowed'; then
    return 1
  fi
  return 0
}

if trigger_bootstrap "am startservice"; then
  ok "bootstrap 已触发（后台运行）"
elif trigger_bootstrap "am start-foreground-service"; then
  ok "bootstrap 已触发（Android 新版本限制，已改用 start-foreground-service）"
else
  cat <<'EOF'
[ 失败 ] 无法通过 RunCommandService 触发 Termux。排查：
  1. ~/.termux/termux.properties 是否已写入 allow-external-apps=true
     并执行过 termux-reload-settings（见上方一次性手动步骤）
  2. Termux 是否至少成功打开过一次（$PREFIX 初始化完成）
  3. 手机是否为 F-Droid/GitHub 版 Termux（Play 版不可用）
  4. 手动兜底方案（不依赖 PC 触发）：在手机 Termux 中执行
       bash /sdcard/vivideye/bootstrap.sh
     效果完全相同，bootstrap 幂等可重跑。
EOF
  die "触发 bootstrap 失败"
fi

# ================= 完成 =================
cat <<EOF
${C_GRN}============================================================
 部署触发完成！接下来：
   1. 手机上打开 Termux 可查看安装进度（日志：~/vivideye/deploy.log）
      （pkg/pip 下载较慢属正常，首次约 5~15 分钟）
   2. PC 上体检： cd deploy && make doctor
   3. 手机 Termux 里启动主服务：
        cd ~/vivideye && source .venv/bin/activate
        python main.py start       # Web 界面: http://<手机IP>:8666
   4. 手机上打开 IP Webcam → 启动视频服务器（默认端口 8080）
============================================================${C_RST}
EOF
