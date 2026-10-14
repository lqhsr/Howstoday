#!/usr/bin/env bash
# 为本项目同时拉起：
#   Qwen3.6-35B   → http://127.0.0.1:8080/v1  （文本分析）
#   Qwen2.5-VL-7B → http://127.0.0.1:8081/v1  （读图）
set -euo pipefail
exec bash /home/lqhsr/文档/LLM/scripts/serve-both.sh
