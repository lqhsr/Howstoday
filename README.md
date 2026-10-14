# 每日记录

每几分钟截一张桌面，千问 2.5 读图，千问 3.6 按小时收成一两句话。当天报告只读取这些小时摘要。

## 流程

```
每 5 分钟
  截屏 → data/screenshots/2026-10-14_103000.png
  千问 2.5 读图 → data/descriptions/2026-10-14_103000.txt
  时间用截图文件的创建时间

整点，或生成日报之前
  这一小时的读图文字 → 千问 3.6 → data/hours/2026-10-14.json
  每个小时一两句话

生成日报
  只把各时段摘要交给千问 3.6，写成简短的「当天完成任务」列表
  → output/日报_2026-10-14.md
```

## 启动模型

```bash
bash start-models.sh
```

- `http://127.0.0.1:8080/v1`：千问 3.6，写小时摘要和日报
- `http://127.0.0.1:8081/v1`：千问 2.5-VL，读截图

## 运行

```bash
cd /home/lqhsr/文档/Howstoday
source .venv/bin/activate

python main.py --once          # 截一张并读图
python main.py --loop          # 每 5 分钟截一张；整点写上一小时
python main.py --hour          # 立刻汇总当前小时
python main.py --hour 10       # 汇总今天 10 点这一小时
python main.py --report        # 补齐今天的小时摘要并写日报
python main.py --report 2026-10-13
```

当前这一小时在循环里会留到整点再汇总。`--report` 会把当天尚未结束的小时也一并写入日报。
