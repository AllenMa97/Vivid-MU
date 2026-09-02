#!/usr/bin/env bash
# ============================================================================
# VividEye 部署后体检（PC 端）
#
# 检查项：
#   1. 设备连接 / 机型 / 电池 / 存储空间
#   2. Termux 与 IP Webcam 是否已安装
#   3. Termux 进程是否存活
#   4. VividEye Web 服务（手机端口 8666，经 adb forward 转发到 PC 本机）
#   5. IP Webcam 视频服务（手机端口 8080，经 adb forward 转发到 PC 本机）
#   6. 手机局域网 IP（供浏览器直连 http://<手机IP>:8666）
#
# 用法：bash scripts/doctor.sh   或   make doctor
# 退出码：0 = 全部通过；1 = 存在未通过项（输出中附修复提示）
# ============================================================================
set -euo pipefail

TERMUX_PKG="com.termux"
IPWEBCAM_PKG="com.pas.webcam"

# ---------------- 彩色输出 ----------------
if [ -t 1 ]; then
  C_RED=$'\e[0;31m'; C_GRN=$'\e[0;32m'; C_YEL=$'\e[1;33m'
  C_BLU=$'\e[0;34m'; C_CYA=$'\e[0;36m'; C_RST=$'\e[0m'
else
  C_RED=''; C_GRN=''; C_YEL=''; C_BLU=''; C_CYA=''; C_RST=''
fi
step() { printf '%s\n' "${C_BLU}==>${C_RST} ${C_CYA}$*${C_RST}"; }
ok()   { printf '%s\n' "${C_GRN}[ 通过 ]${C_RST} $*"; }
warn() { printf '%s\n' "${C_YEL}[ 警告 ]${C_RST} $*"; }
fail() { printf '%s\n' "${C_RED}[ 未过 ]${C_RST} $*" >&2; }
info() { printf '%s\n' "${C_BLU}[ 信息 ]${C_RST} $*"; }
die()  { fail "$*"; exit 1; }

FAILS=0
record() { # record <ok|fail> <检查项> <详情>
  if [ "$1" = "ok" ]; then ok "$2：$3"; else FAILS=$((FAILS + 1)); fail "$2：$3"; fi
}

adb_sh() { adb shell "$@" 2>/dev/null | tr -d '\r' || true; }

# ================= 前置：工具与设备 =================
step "VividEye 部署体检开始 $(date '+%F %T')"
command -v adb >/dev/null 2>&1 \
  || die "PC 缺少 adb（Debian/Ubuntu: sudo apt install adb）"
command -v curl >/dev/null 2>&1 \
  || die "PC 缺少 curl（Debian/Ubuntu: sudo apt install curl）"

state="$(adb get-state 2>/dev/null || true)"
[ "$state" = "device" ] \
  || die "手机未就绪（adb state=${state:-none}）。提示：解锁屏幕/插好数据线/在手机上允许 USB 调试授权"

MODEL="$(adb_sh getprop ro.product.model)"
ANDROID="$(adb_sh getprop ro.build.version.release)"
BATT="$(adb_sh dumpsys battery | grep -oE 'level: [0-9]+' | head -n 1 || true)"
info "设备：$MODEL（Android ${ANDROID:-?}） 电池${BATT:-未知}"

# ================= 检查 1/4：APK 安装情况 =================
step "检查 1/4：应用安装情况"
if adb_sh pm list packages | grep -qx "package:$TERMUX_PKG"; then
  record ok "Termux 安装" "已安装（$TERMUX_PKG）"
else
  record fail "Termux 安装" "未安装 → 重跑 make deploy，或将 termux.apk 放入 deploy/apks/（见 apks/README.md）"
fi
if adb_sh pm list packages | grep -qx "package:$IPWEBCAM_PKG"; then
  record ok "IP Webcam 安装" "已安装（$IPWEBCAM_PKG）"
else
  record fail "IP Webcam 安装" "未安装 → 手机应用商店搜索“IP Webcam”安装，或放 ipwebcam.apk 到 deploy/apks/ 后重跑 make deploy"
fi

# ================= 检查 2/4：Termux 进程 =================
step "检查 2/4：Termux 进程"
PSOUT="$(adb_sh "ps -A" | grep -i "$TERMUX_PKG" || true)"
if [ -n "$PSOUT" ]; then
  record ok "Termux 进程" "运行中（$(printf '%s' "$PSOUT" | head -n 1 | awk '{print $1, $NF}')）"
else
  record fail "Termux 进程" "未运行 → 手机上打开 Termux 应用；bootstrap 也可能尚未跑过（可重跑 make deploy 或手动执行 bash /sdcard/vivideye/bootstrap.sh）"
fi

# ================= 检查 3/4：VividEye Web 服务（8666） =================
step "检查 3/4：VividEye Web 服务（手机端口 8666）"
adb forward tcp:8666 tcp:8666 >/dev/null 2>&1 || true
CODE="$(curl -s -o /dev/null -m 6 -w '%{http_code}' 'http://127.0.0.1:8666/' || true)"
if [ -n "$CODE" ] && [ "$CODE" != "000" ]; then
  record ok "VividEye Web(8666)" "HTTP $CODE（服务存活，已通过 adb forward 映射到 PC 的 127.0.0.1:8666）"
else
  record fail "VividEye Web(8666)" "无法连接 → 在手机 Termux 中执行：cd ~/vivideye && source .venv/bin/activate && vivideye main start"
fi

# ================= 检查 4/4：IP Webcam 视频服务（8080） =================
step "检查 4/4：IP Webcam 视频服务（手机端口 8080）"
adb forward tcp:8080 tcp:8080 >/dev/null 2>&1 || true
CODE2="$(curl -s -o /dev/null -m 6 -w '%{http_code}' 'http://127.0.0.1:8080/' || true)"
if [ -n "$CODE2" ] && [ "$CODE2" != "000" ]; then
  record ok "IP Webcam(8080)" "HTTP $CODE2（服务存活，已通过 adb forward 映射到 PC 的 127.0.0.1:8080）"
else
  record fail "IP Webcam(8080)" "无法连接 → 手机上打开 IP Webcam 应用，点“启动视频服务器”（VividEye 依赖 127.0.0.1:8080 回环取流）"
fi

# ================= 附加信息 =================
IP="$(adb_sh ip -f inet addr show wlan0 | grep -oE 'inet [0-9.]+' | head -n 1 | awk '{print $2}' || true)"
if [ -n "$IP" ]; then
  info "手机局域网 IP：$IP → PC/其他设备浏览器直接访问 http://$IP:8666"
else
  warn "未获取到 wlan0 IP（手机未连 WiFi？可先连接同一 WiFi 再体检）"
fi
DF="$(adb_sh df -h /data | tail -n 1 || true)"
[ -n "$DF" ] && info "手机 /data 分区：$DF（注意为 72h 滚动录像留足空间）"
FORWARDS="$(adb forward --list 2>/dev/null || true)"
[ -n "$FORWARDS" ] && info "当前 adb 端口转发：\n$FORWARDS"

# ================= 汇总 =================
echo
if [ "$FAILS" -eq 0 ]; then
  printf '%s\n' "${C_GRN}==================== 体检结论：全部通过 ====================${C_RST}"
  info "PC 浏览器可直接打开 http://127.0.0.1:8666 查看手机上的 VividEye"
  exit 0
else
  printf '%s\n' "${C_RED}==================== 体检结论：${FAILS} 项未通过 ====================${C_RST}"
  warn "请按上方各项“→”后的提示修复；修复后重跑 make doctor"
  exit 1
fi
