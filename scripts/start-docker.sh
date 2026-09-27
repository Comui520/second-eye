#!/usr/bin/env sh
set -eu

login=0
logs=0
no_build=0
for arg in "$@"; do
  case "$arg" in
    --login) login=1 ;;
    --logs) logs=1 ;;
    --no-build) no_build=1 ;;
    *) echo "用法: $0 [--login] [--logs] [--no-build]" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已创建 .env，请先填写模型 API Key/Cookie；空配置也可以先启动界面。"
fi
mkdir -p data

# --login 只临时打开 noVNC，适合没有 Python/Conda、只安装 Docker 的机器。
if [ "$login" -eq 1 ]; then
  password_file="data/.novnc-password"
  if [ ! -s "$password_file" ]; then
    password="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 8 || true)"
    if [ "${#password}" -lt 8 ]; then
      echo "无法生成 noVNC 密码，请手动创建 $password_file" >&2
      exit 1
    fi
    printf '%s' "$password" > "$password_file"
    chmod 600 "$password_file"
    echo "已生成 noVNC 临时密码：$password"
  else
    password="$(head -n 1 "$password_file")"
    echo "使用已有 noVNC 密码：$password"
  fi
  export ENABLE_NOVNC=1
else
  export ENABLE_NOVNC=0
fi

if [ "$no_build" -eq 1 ]; then
  docker compose up -d
else
  docker compose up -d --build
fi

echo "second-eye 已启动：http://127.0.0.1:18000"
if [ "$login" -eq 1 ]; then
  echo "登录模式已启用 noVNC：http://<主机IP>:16080/vnc.html"
  echo "登录完成后请重新执行：$0（关闭 noVNC）"
else
  echo "普通运行模式：noVNC 已关闭。需要登录时执行 $0 --login"
fi

if [ "$logs" -eq 1 ]; then
  docker compose logs -f second-eye
fi
