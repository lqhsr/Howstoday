# 每日记录

每几分钟截一张桌面，视觉模型读图，文本模型按小时收成一两句话，再生成简短的「当天完成任务」报告。

支持**本机 OpenAI 兼容服务**与**远程 API**（通义 / OpenAI 等），配置方式见下文。

> **开发环境声明：** 本项目全程使用铭凡 MS-S1 MAX 高性能工作站，搭载 Qwen3.6 35B-A3B 本地大模型完成编写。

## 流程

```
每 5 分钟
  截屏 → data/screenshots/2026-10-14_103000.png
  视觉模型读图 → data/descriptions/2026-10-14_103000.txt
  时间用截图文件的创建时间

整点，或生成日报之前
  这一小时的读图文字 → 文本模型 → data/hours/2026-10-14.json
  每个小时一两句话

生成日报
  只把各时段摘要交给文本模型，写成简短的「当天完成任务」列表
  → output/日报_2026-10-14.md
```

## 配置：本地或远程

编辑 `config.yaml`（也可参考 `config.example.yaml`）。

| 字段 | 说明 |
|------|------|
| `base_url` | OpenAI 兼容根路径，需含 `/v1` |
| `model` | 模型名 |
| `api_key` | 直接写密钥；为空则读环境变量 |
| `api_key_env` | 默认 `VISION_API_KEY` / `LLM_API_KEY` |
| `use_proxy` | `null`=本地关代理、远程开；可强制 `true`/`false` |
| `extra_body` | 本地默认带 llama 参数；远程默认不传。可自定义或设为 `{}` 关闭 |

密钥优先级：`api_key` → `api_key_env` → `LLM_API_KEY` / `OPENAI_API_KEY` → 本地 `~/文档/LLM/config/api.key` → `EMPTY`。

### 本机模型（默认）

```bash
bash start-models.sh
```

- `http://127.0.0.1:8080/v1`：文本（小时摘要 / 日报）
- `http://127.0.0.1:8081/v1`：视觉（读截图）

### 远程 API 示例（通义兼容模式）

```yaml
vision:
  model: "qwen-vl-plus"
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  api_key_env: "VISION_API_KEY"

llm:
  model: "qwen-plus"
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  api_key_env: "LLM_API_KEY"
```

```bash
export VISION_API_KEY=sk-...
export LLM_API_KEY=sk-...
```

视觉与文本可一个本地、一个远程，互不影响。

## 运行

```bash
cd /home/lqhsr/文档/Howstoday
source .venv/bin/activate   # 或 .venv/bin/python ...

python main.py --once          # 截一张并读图
python main.py --loop          # 每 5 分钟截一张；整点写上一小时
python main.py --hour          # 立刻汇总当前小时
python main.py --hour 10       # 汇总今天 10 点这一小时
python main.py --report        # 补齐今天的小时摘要并写日报
python main.py --report 2026-10-13
```

当前这一小时在循环里会留到整点再汇总。`--report` 会把当天尚未结束的小时也一并写入日报。
