#!/usr/bin/env bash
# ============================================================================
# EMUI 恢复出厂 / 三清助手（危险操作，需二次确认）
#
# 两种模式：
#   默认（recovery 模式）：adb reboot recovery 重启到恢复模式，
#       由用户在手机上手动完成“清除数据/恢复出厂”（官方支持路径，未解锁可用）
#   --fastboot 模式（需已解锁 Bootloader）：
#       adb reboot bootloader && fastboot erase userdata && fastboot erase cache
#
# 用法：
#   bash scripts/flash/reset-emui.sh              # recovery 模式（推荐）
#   bash scripts/flash/reset-emui.sh --fastboot   # 需已解锁 BL
#   bash scripts/flash/reset-emui.sh --yes        # 跳过交互确认（自动化）
#   make reset
#
# 重要：恢复出厂前务必先备份！备份清单见 deploy/README-FLASH.md 第 0 步。
# ============================================================================
set -euo pipefail

MODE="recovery"
CONFIRMED=0

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

for a in "$@"; do
  case "$a" in
    --fastboot) MODE="fastboot" ;;
    -y|--yes)   CONFIRMED=1 ;;
    -h|--help)  sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "未知参数: $a（支持 --fastboot / --yes / --help）" ;;
  esac
done

cat <<EOF
${C_RED}
****************************************************************
*   !!! 危 险 操 作 ：恢 复 出 厂 设 置 / 三 清 !!!
****************************************************************
 将被【永久清除】的内容：
   - 照片、视频、下载文件（DCIM/Download/...）
   - 微信/QQ 聊天记录、通讯录、短信、通话记录
   - 两步验证器（Google Authenticator 等！先迁移！）
   - 所有已安装应用及其数据、已登录账号
   - Termux 与 VividEye（部署后也会被清掉，需重新 make deploy）
   - pm uninstall --user 0 精简掉的预装应用将全部恢复
 其他须知：
   - 若开启了“查找我的手机”，恢复后需输入原华为账号密码（激活锁）
   - 三清后首次开机初始化需 5~15 分钟，属正常现象
****************************************************************${C_RST}
EOF

if [ "$CONFIRMED" -ne 1 ]; then
  if [ -t 0 ]; then
    ans=""
    read -r -p "确认已备份并明白后果？输入大写 YES 继续，其他任意输入取消: " ans || true
    [ "$ans" = "YES" ] || die "已取消（未做任何更改）"
  else
    die "非交互环境必须显式传 --yes 才能执行恢复出厂"
  fi
else
  warn "（--yes 模式）已跳过交互确认"
fi

command -v adb >/dev/null 2>&1 || die "PC 缺少 adb（Debian/Ubuntu: sudo apt install adb）"
state="$(adb get-state 2>/dev/null || true)"
[ "$state" = "device" ] || die "手机未连接（state=${state:-none}）：解锁屏幕/允许 USB 调试后重试"

MODEL="$(adb shell getprop ro.product.model 2>/dev/null | tr -d '\r' || true)"
info_note="目标设备：${MODEL:-未知型号}，模式：$MODE"
printf '%s\n' "$info_note"

if [ "$MODE" = "recovery" ]; then
  # ---------- 官方路径：重启到 Recovery，手机上手动操作 ----------
  step "重启手机进入 Recovery 模式（adb reboot recovery）"
  adb reboot recovery
  ok "重启指令已发送，请拿起手机按以下指引完成三清："
  cat <<'EOF'
------------------------------------------------------------------
 EMUI Recovery 手动操作指引（音量键选择，电源键确认；部分为触摸）：

   1. 等待出现 Recovery 菜单；若显示“无命令/No Command”画面：
      长按电源键，再按一下音量上键即可进入菜单
   2. 选择 “Wipe data / Clear data（清除数据）”
   3. 选择 “Factory reset / 恢复出厂设置”，确认
   4. （可选三清）返回后选择 “Wipe cache partition（清除缓存）”
   5. 完成后选择 “Reboot system now” 重启
   6. 首次开机 5~15 分钟属正常；期间勿断电/强制重启

 说明：
   - 这是官方支持路径，未解锁 Bootloader 也能用；
     代价是必须在手机上手动点选（无法全程 PC 代劳）
   - 未解锁设备不存在 fastboot erase 这条捷径（会被拒绝）
   - 恢复完成后重新走部署流程：cd deploy && make deploy
------------------------------------------------------------------
EOF
  warn "脚本已把手机送入 Recovery；后续操作需在手机屏幕上完成"
else
  # ---------- 进阶路径：fastboot erase（仅限已解锁 Bootloader） ----------
  command -v fastboot >/dev/null 2>&1 \
    || die "PC 缺少 fastboot（android-tools 包）"
  warn "fastboot 模式要求 Bootloader 已解锁；未解锁会卡在 <waiting for device> 或报 FAILED"
  step "重启到 Bootloader（adb reboot bootloader）"
  adb reboot bootloader

  step "等待 fastboot 设备就绪"
  fb_deadline=$(( $(date +%s) + 60 ))
  while :; do
    if [ -n "$(fastboot devices 2>/dev/null | awk 'NF')" ]; then
      break
    fi
    if [ "$(date +%s)" -ge "$fb_deadline" ]; then
      die "60s 内未发现 fastboot 设备。排查：BL 是否解锁？换 USB 口？换回 recovery 模式更稳妥"
    fi
    sleep 2
  done
  ok "fastboot 设备已连接"

  # 等价替代：fastboot -w（擦 userdata 并格式化）；此处按传统三清逐分区擦除
  step "擦除 userdata（用户数据分区）"
  fastboot erase userdata || die "fastboot erase userdata 失败：设备很可能未解锁 BL，请改用默认 recovery 模式"

  step "擦除 cache（缓存分区）"
  fastboot erase cache || warn "cache 擦除失败（部分固件无独立 cache，可忽略）"

  step "重启手机"
  fastboot reboot || true
  ok "三清完成。首次开机初始化 5~15 分钟属正常；之后重新执行 cd deploy && make deploy"
fi
