#!/usr/bin/env bash
# ============================================================================
# VividEye 手机端 bootstrap —— 在华为 P20 的 Termux 中运行
#
# 两种运行方式（效果相同，幂等可重跑）：
#   A. 自动：PC 端 deploy.sh 通过 RunCommandService 触发
#        bash /sdcard/vivideye/bootstrap.sh
#   B. 手动：在 Termux 里直接执行上面同一条命令；
#        项目已解压过的话，也可以执行仓库内副本：
#        bash ~/vivideye/deploy/scripts/phone-bootstrap.sh
#
# 工作原理：
#   - 若脚本位于 Termux 家目录($HOME)之外（即 /sdcard 启动器模式）：
#       1) 从同目录读取 vivideye.tar.gz 并解压到 ~/vivideye
#       2) 转而执行仓库内的本脚本副本（exec，避免 sdcard noexec 限制）
#   - 正式阶段：termux-setup-storage → pkg 更新/装包 → venv → pip 依赖
# ============================================================================
set -euo pipefail

info() { printf '%s\n' "==> $*"; }
ok()   { printf '%s\n' "[ 成功 ] $*"; }
warn() { printf '%s\n' "[ 警告 ] $*"; }
err()  { printf '%s\n' "[ 错误 ] $*" >&2; }

# ---- 日志：所有输出同时写入 ~/vivideye/deploy.log（幂等，避免二次 tee） ----
if [ "${VIVIDEYE_BOOTSTRAP_LOGGED:-0}" != "1" ]; then
  mkdir -p "$HOME/vivideye"
  exec > >(tee -a "$HOME/vivideye/deploy.log") 2>&1
  export VIVIDEYE_BOOTSTRAP_LOGGED=1
fi

printf '%s\n' "=============================================================="
printf '%s\n' " VividEye 手机端 bootstrap  $(date '+%F %T')"
printf '%s\n' "=============================================================="

# ================= 阶段 1：定位自身，必要时先解压项目 =================
# cd 失败最常见的原因：脚本在 /sdcard 但存储权限未授予，给出友好提示
if ! SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; then
  err "无法进入脚本所在目录：${BASH_SOURCE[0]}"
  err "最常见原因：尚未授权存储权限。请先在 Termux 执行 termux-setup-storage"
  err "并在弹窗中点“允许”，然后重新运行本脚本"
  exit 1
fi

# ${SELF_DIR#$HOME}：若去掉 $HOME 前缀后没有变化，说明脚本不在家目录内
if [ "${SELF_DIR#"$HOME"}" = "$SELF_DIR" ]; then
  info "启动器模式：脚本位于 $SELF_DIR（家目录之外），先解压项目"
  TARBALL="$SELF_DIR/vivideye.tar.gz"
  if [ ! -r "$TARBALL" ]; then
    err "找不到可读的 $TARBALL"
    err "请确认：1) PC 端 deploy.sh 已完成 push；2) 已在 Termux 里执行过"
    err "         termux-setup-storage 并在弹窗中点了“允许”（一次性授权）"
    exit 1
  fi
  # Termux 基础环境自带 tar；缺失时先补装，装不上给出明确排查提示
  if ! command -v tar >/dev/null 2>&1; then
    if pkg install -y tar; then
      ok "已安装：tar"
    else
      err "tar 安装失败（多为网络/镜像问题），无法解压项目"
      err "建议：运行 termux-change-repo 选择镜像（如清华 TUNA）后，重新执行本脚本"
      exit 1
    fi
  fi
  TARGET="$HOME/vivideye"
  mkdir -p "$TARGET"
  info "解压 $TARBALL -> $TARGET ..."
  tar -xzf "$TARBALL" -C "$TARGET"
  REPO_BOOTSTRAP="$TARGET/deploy/scripts/phone-bootstrap.sh"
  if [ ! -f "$REPO_BOOTSTRAP" ]; then
    err "解压后未找到 $REPO_BOOTSTRAP（tarball 内容异常，请重跑 PC 端 deploy.sh）"
    exit 1
  fi
  ok "解压完成，转入仓库内副本继续（后续日志同样写入 ~/vivideye/deploy.log）"
  exec bash "$REPO_BOOTSTRAP"
fi

# ================= 阶段 2：正式安装（此时必在 ~/vivideye 内） =================
REPO_ROOT="$(cd "$SELF_DIR/../.." && pwd)"
info "仓库位置：$REPO_ROOT"

if ! command -v pkg >/dev/null 2>&1; then
  err "未检测到 pkg 命令——本脚本必须在 Termux 中运行，请勿在 PC 上执行"
  exit 1
fi

# ---------- [1/6] 存储授权 ----------
info "[1/6] 授予存储权限（termux-setup-storage，弹窗请点“允许”）"
if termux-setup-storage; then
  ok "存储权限已就绪（~/storage 指向共享存储）"
else
  warn "termux-setup-storage 未成功（可能被拒绝）——不影响本项目运行"
  warn "（项目数据都在 ~/vivideye 下；仅日后导出文件到相册/下载目录时需要）"
fi

# ---------- [2/6] 更新软件源 ----------
info "[2/6] 更新软件源（pkg update；国内网络慢可先执行 termux-change-repo 换镜像）"
if ! pkg update -y; then
  err "pkg update 失败（多为网络/镜像问题）"
  err "建议：运行 termux-change-repo 选择镜像（如清华 TUNA）后，重新执行本脚本"
  exit 1
fi

# ---------- [3/6] 安装基础软件包 ----------
# python      : 运行时           ffmpeg : 录像/转码
# git         : 调试与更新       libjpeg-turbo / zlib : Pillow 编译所需
info "[3/6] 安装基础软件包 python / ffmpeg / git / libjpeg-turbo / zlib"
FAILED_PKGS=""
for p in python ffmpeg git libjpeg-turbo zlib; do
  if pkg install -y "$p" >/dev/null; then
    ok "已安装：$p"
  else
    FAILED_PKGS="$FAILED_PKGS $p"
  fi
done
if [ -n "$FAILED_PKGS" ]; then
  warn "以下包安装失败（脚本继续）：$FAILED_PKGS"
  warn "可稍后手动补装：pkg install -y$FAILED_PKGS"
  warn "注意：libjpeg-turbo/zlib 缺失会导致 Pillow（pip 依赖）编译失败"
fi

# ---------- [4/6] 创建 Python 虚拟环境 ----------
info "[4/6] 创建 Python 虚拟环境 .venv（已存在则复用）"
cd "$REPO_ROOT"
if python -m venv .venv; then
  ok "虚拟环境就绪：$REPO_ROOT/.venv"
else
  err "创建 venv 失败（python 包未装好？），请重跑本脚本"
  exit 1
fi

# ---------- [5/6] 安装 Python 依赖 ----------
info "[5/6] 安装 Python 依赖（requirements.txt）"
REQ="$REPO_ROOT/requirements.txt"
if [ -f "$REQ" ]; then
  if .venv/bin/pip install -r "$REQ"; then
    ok "Python 依赖安装完成"
  else
    err "pip install 失败。排查建议："
    err "  1) 编译类依赖（如 Pillow）需要工具链：pkg install -y build-essential binutils libjpeg-turbo zlib"
    err "  2) 网络问题可换镜像：.venv/bin/pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple"
    err "  3) 修复后重跑本脚本（幂等，已装部分会自动跳过）"
    exit 1
  fi
else
  # 仓库当前尚未提交 requirements.txt（部署工具先行），属预期情况
  warn "未找到 $REQ（暂未提交到仓库，属预期），已跳过 pip 依赖安装"
fi

# ---------- [6/6] 生成用户配置 ----------
info "[6/6] 准备用户配置（user_config.yaml）"
if [ ! -f "$REPO_ROOT/user_config.yaml" ] && [ -f "$REPO_ROOT/config_template.yaml" ]; then
  cp "$REPO_ROOT/config_template.yaml" "$REPO_ROOT/user_config.yaml"
  ok "已从模板生成 user_config.yaml（该文件已被 git 忽略，可放心填写密钥）"
else
  ok "user_config.yaml 已存在或模板缺失，保持现状"
fi

# ================= 完成：启动说明 =================
cat <<'EOF'

==============================================================
 VividEye 手机端 bootstrap 完成！
   项目位置 : ~/vivideye
   部署日志 : ~/vivideye/deploy.log
--------------------------------------------------------------
 接下来在 Termux 中手动执行：

   cd ~/vivideye
   source .venv/bin/activate

   # 启动主服务（Web 界面端口 8666）：
   python main.py start

   # 配置 AI 密钥（推荐）：启动后用浏览器打开
   #   http://<手机IP>:8666 →「设置」页 → API Key 输入框粘贴并保存
   # 进阶方式：export VIVIDEYE_AI__API_KEY=sk-xxx（写入 ~/.bashrc 可持久化）
--------------------------------------------------------------
 别忘记（本项目 = 手机自己拍自己）：
   1) 打开手机上的 IP Webcam 应用 → 启动视频服务器（默认端口 8080）
      VividEye 通过 http://127.0.0.1:8080/video 回环取流
   2) EMUI 杀后台极狠：设置 → 电池 → 启动管理 →
      把 Termux 与 IP Webcam 改为“手动管理”（三个开关全开），
      并在最近任务里把两个应用下拉“上锁”
   3) 手机与 PC 同一 WiFi 时，PC 浏览器访问 http://<手机IP>:8666
      （PC 端可随时运行 make doctor 体检）
==============================================================
EOF
