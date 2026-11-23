#!/bin/zsh
set -e
LEAD_TOOL_DIR="${0:A:h}"
cd "$LEAD_TOOL_DIR"
if [[ ! -x .venv/bin/python ]]; then
  print '尚未安装依赖，请先运行：uv sync'
  read 'reply?按回车关闭'
  exit 1
fi
exec .venv/bin/python -m yingsha_leads --open
