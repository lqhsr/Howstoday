#!/usr/bin/env python3
"""
daily-summary - 双模型架构（OpenAI 兼容，支持本地与远程）：
  1. 视觉模型  读截图 → 文字描述
  2. 文本模型  按小时收成一两句话，再生成当天完成任务报告
"""

import argparse
import base64
import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).resolve().parent

# ─── 配置 ─────────────────────────────────────────────────────

def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("daily-summary")


# ─── 工具函数 ───────────────────────────────────────────────────

LOCAL_API_KEY_FILE = Path("/home/lqhsr/文档/LLM/config/api.key")


def is_local_endpoint(base_url: str) -> bool:
    """判断是否为本机 OpenAI 兼容端点（127.0.0.1 / localhost）。"""
    from urllib.parse import urlparse

    host = (urlparse(base_url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"} or host.startswith("127.")


def resolve_api_key(section: dict, *, default_env: str) -> str:
    """解析 API Key，兼容本地与远程。

    优先级：
      1. 配置里的 api_key（非空）
      2. 配置 api_key_env 或默认环境变量
      3. LLM_API_KEY / OPENAI_API_KEY
      4. 本地 api.key 文件（旧行为）
      5. EMPTY（本地 llama-server 可无 key）
    """
    key = str(section.get("api_key") or "").strip()
    if key:
        return key

    env_name = str(section.get("api_key_env") or default_env or "").strip()
    for name in (env_name, "LLM_API_KEY", "OPENAI_API_KEY"):
        if name and os.environ.get(name, "").strip():
            return os.environ[name].strip()

    if LOCAL_API_KEY_FILE.exists():
        file_key = LOCAL_API_KEY_FILE.read_text(encoding="utf-8").strip()
        if file_key:
            return file_key
    return "EMPTY"


def should_use_proxy(section: dict) -> bool:
    """本地默认直连（避开 socks:// 报错）；远程默认跟随系统代理。可用 use_proxy 覆盖。"""
    if "use_proxy" in section and section["use_proxy"] is not None:
        return bool(section["use_proxy"])
    return not is_local_endpoint(section["base_url"])


def make_openai_client(section: dict, *, default_env: str):
    """创建 OpenAI 兼容客户端，本地/远程自动切换代理策略。"""
    import httpx2
    from openai import OpenAI

    base_url = section["base_url"]
    timeout = section.get("timeout", 120)
    use_proxy = should_use_proxy(section)
    api_key = resolve_api_key(section, default_env=default_env)

    log.debug(
        "API client %s proxy=%s key=%s",
        base_url,
        use_proxy,
        "set" if api_key and api_key != "EMPTY" else "empty",
    )
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
        http_client=httpx2.Client(trust_env=use_proxy, timeout=timeout),
    )


def completion_extra_body(section: dict, *, local_defaults: dict) -> dict | None:
    """本地 llama-server 默认带 extra_body；远程默认不传，避免不兼容字段。

    配置里写 extra_body: {} 或不兼容时设为 null，可显式关闭；
    写自定义 dict 则原样传给远端。
    """
    if "extra_body" in section:
        extra = section.get("extra_body")
        return extra if isinstance(extra, dict) and extra else None
    if is_local_endpoint(section["base_url"]):
        return local_defaults
    return None

def get_data_dir(cfg: dict) -> Path:
    return Path(cfg["data"]["base_dir"])

def get_screenshot_dir(cfg: dict) -> Path:
    return Path(cfg["data"]["screenshot_dir"])

def hours_path(cfg: dict, d: date) -> Path:
    return get_data_dir(cfg) / "hours" / f"{d}.json"

def load_hours(cfg: dict, d: date) -> list[dict]:
    path = hours_path(cfg, d)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []

def save_hours(cfg: dict, d: date, hours: list[dict]):
    path = hours_path(cfg, d)
    path.parent.mkdir(parents=True, exist_ok=True)
    hours.sort(key=lambda item: item["hour"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(hours, f, ensure_ascii=False, indent=2)

def encode_image(path: Path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def image_time(path: Path) -> datetime:
    """截图文件的创建时间。文件系统没有创建时间时用修改时间。"""
    st = path.stat()
    ts = getattr(st, "st_birthtime", None) or st.st_mtime
    return datetime.fromtimestamp(ts)

# ─── 1. 视觉模型：截图读图 → 文字描述 ─────────────────────────

def describe_screenshot(cfg: dict, screenshot_path: Path) -> str:
    """调用视觉模型读取截图，返回文字描述（本地或远程 OpenAI 兼容接口）。"""
    vision = cfg["vision"]
    client = make_openai_client(vision, default_env="VISION_API_KEY")

    # 截屏描述提示词：引导模型输出结构化文本
    prompt = """请详细描述这张桌面截图，按以下格式输出：

## 当前状态
- 主窗口/应用：[应用名称]
- 窗口内容：[窗口在显示什么]
- 是否有多个窗口：[是/否]

## 活动描述
- 用户当前在做什么：[一句话描述]
- 正在编写的代码/文档内容：[简述]
- 是否有对话/聊天：[内容摘要]

## 环境信息
- 操作系统界面风格
- 是否有终端/代码编辑器/浏览器等
- 截图中的文字内容（如果清晰可读，提取关键部分）

请直接输出，不要多余解释。不要推断或填写时间。
"""

    model = vision["model"]
    where = "本地" if is_local_endpoint(vision["base_url"]) else "远程"
    log.info("👁️  [%s] %s 读图中...", where, model)
    create_kwargs = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{encode_image(screenshot_path)}"
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 2048,
        "temperature": 0.3,
    }
    extra = completion_extra_body(
        vision,
        local_defaults={
            "top_k": 20,
            "chat_template_kwargs": {"enable_thinking": False},
        },
    )
    if extra:
        create_kwargs["extra_body"] = extra

    try:
        resp = client.chat.completions.create(**create_kwargs)
    except Exception as e:
        log.error("❌ 视觉模型调用失败: %s", e)
        return ""

    content = resp.choices[0].message.content or ""
    return content.strip()

# ─── 2. 文本模型：小时摘要与日报 ──────────────────────────────

def ask_llm(cfg: dict, prompt: str, max_tokens: int = 1024) -> str:
    """调用文本模型，返回纯文本（本地或远程 OpenAI 兼容接口）。"""
    llm = cfg["llm"]
    client = make_openai_client(llm, default_env="LLM_API_KEY")
    create_kwargs = {
        "model": llm["model"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": llm.get("temperature", 0.3),
    }
    extra = completion_extra_body(
        llm,
        local_defaults={
            "top_k": 20,
            "chat_template_kwargs": {
                "enable_thinking": llm.get("enable_thinking", False),
            },
        },
    )
    if extra:
        create_kwargs["extra_body"] = extra

    resp = client.chat.completions.create(**create_kwargs)
    text = (resp.choices[0].message.content or "").strip()
    if "</think>" in text:
        text = text.split("</think>", 1)[-1].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return text

def load_descriptions(cfg: dict, d: date) -> list[tuple[datetime, str]]:
    """读取某一天的读图结果。时间来自文件名，与截图保存时刻一致。"""
    desc_dir = get_data_dir(cfg) / "descriptions"
    if not desc_dir.exists():
        return []
    items: list[tuple[datetime, str]] = []
    for path in sorted(desc_dir.glob(f"{d}_*.txt")):
        try:
            stamp = datetime.strptime(path.stem, "%Y-%m-%d_%H%M%S")
        except ValueError:
            continue
        items.append((stamp, path.read_text(encoding="utf-8").strip()))
    return items

def hour_label(hour: int) -> str:
    return f"{hour:02d}:00–{(hour + 1) % 24:02d}:00"

def summarize_hour(cfg: dict, d: date, hour: int, force: bool = False) -> Optional[dict]:
    """把某一小时的读图结果收成一两句话。"""
    shots = [(stamp, text) for stamp, text in load_descriptions(cfg, d) if stamp.hour == hour]
    if not shots:
        log.info("%s %s 没有读图记录", d, hour_label(hour))
        return None

    hours = load_hours(cfg, d)
    existing = next((item for item in hours if item.get("hour") == hour), None)
    if existing and not force and existing.get("shots") == len(shots) and existing.get("summary"):
        log.info("已有小时摘要：%s", hour_label(hour))
        return existing

    notes = "\n\n".join(f"[{stamp:%H:%M}]\n{text}" for stamp, text in shots)
    prompt = f"""下面是 {d} {hour_label(hour)} 的桌面读图记录。每条开头的时间是截图创建时间。

{notes}

请用一两句话概括这一小时主要在做什么。
根据读图内容自己归纳，不要套用预设分类，不要写列表，不要推测精确耗时。
直接输出摘要。"""

    log.info("🧠 汇总 %s ...", hour_label(hour))
    try:
        summary = ask_llm(cfg, prompt, max_tokens=256)
    except Exception as e:
        log.error("❌ 小时摘要失败: %s", e)
        return existing

    entry = {
        "hour": hour,
        "label": hour_label(hour),
        "summary": summary,
        "shots": len(shots),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    hours = [item for item in hours if item.get("hour") != hour]
    hours.append(entry)
    save_hours(cfg, d, hours)
    log.info("✅ %s %s", hour_label(hour), summary)
    return entry

def refresh_hours(cfg: dict, d: date, before_hour: int | None = None) -> list[dict]:
    """按读图记录补齐小时摘要。before_hour 给定时，不汇总尚未结束的小时。"""
    hours_seen = {stamp.hour for stamp, _ in load_descriptions(cfg, d)}
    if before_hour is not None:
        hours_seen = {hour for hour in hours_seen if hour < before_hour}
    for hour in sorted(hours_seen):
        summarize_hour(cfg, d, hour)
    return load_hours(cfg, d)

def catch_up_finished_hours(cfg: dict):
    """补写已经结束、但还没有摘要的小时。当前这一小时留到整点或生成日报时再写。"""
    now = datetime.now()
    refresh_hours(cfg, now.date() - timedelta(days=1))
    refresh_hours(cfg, now.date(), before_hour=now.hour)

def generate_report(cfg: dict, d: date | None = None) -> Optional[str]:
    """先补齐当天小时摘要，再交给本地模型写成简短的当天完成任务报告。"""
    from jinja2 import Template

    d = d or date.today()
    hours = refresh_hours(cfg, d)
    if not hours:
        log.warning("这一天没有读图记录，无法生成日报。")
        return None

    hour_text = "\n".join(f"{item['label']}\n{item['summary']}" for item in hours)
    prompt = f"""下面是 {d} 各时段已经写好的记录。请据此写一份简短的「当天完成任务」报告。

{hour_text}

要求：
- 归纳当天实际做过的几件事，写成任务列表（通常 3–8 条）
- 每条一句话，说清「完成了 / 做了什么」
- 合并同类时段，不要按小时逐条复述
- 不要补充这些记录里没有的内容，不要写耗时估算，不要加开场白或结尾总结
直接输出任务列表。"""

    log.info("🧠 根据 %d 条时段记录写当天完成任务报告...", len(hours))
    try:
        tasks = ask_llm(cfg, prompt, max_tokens=1024)
    except Exception as e:
        log.error("❌ 任务报告生成失败，改为直接列出时段记录: %s", e)
        tasks = ""

    template = Template(TEMPLATE_MD)
    md = template.render(d=d, hours=hours, tasks=tasks, now=datetime.now())
    output_dir = ROOT / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"日报_{d.strftime('%Y-%m-%d')}.md"
    report_path.write_text(md, encoding="utf-8")
    log.info("📄 日报: %s", report_path)
    return str(report_path)

# ─── 截屏 ───────────────────────────────────────────────────────

def capture_screenshot(dest: Path) -> None:
    """GNOME Wayland 走桌面门户；其他环境用 mss。"""
    import os

    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        _capture_gnome_portal(dest)
        return
    from mss import MSS
    with MSS() as sct:
        sct.shot(output=str(dest))


def _capture_gnome_portal(dest: Path) -> None:
    """用系统 Python 的 Gio 调用桌面门户。虚拟环境里没有 gi。"""
    import shutil
    import subprocess
    from urllib.parse import unquote, urlparse

    script = r"""
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
proxy = Gio.DBusProxy.new_sync(
    bus, Gio.DBusProxyFlags.NONE, None,
    "org.freedesktop.portal.Desktop",
    "/org/freedesktop/portal/desktop",
    "org.freedesktop.portal.Screenshot",
    None,
)
loop = GLib.MainLoop()
result = {}

def on_signal(_conn, _sender, _path, _iface, _signal, params, _data):
    result["params"] = params
    loop.quit()

bus.signal_subscribe(
    "org.freedesktop.portal.Desktop",
    "org.freedesktop.portal.Request",
    "Response",
    None, None, Gio.DBusSignalFlags.NONE, on_signal, None,
)
proxy.call_sync(
    "Screenshot",
    GLib.Variant("(sa{sv})", ("", {"interactive": GLib.Variant("b", False)})),
    Gio.DBusCallFlags.NONE, 15000, None,
)
GLib.timeout_add_seconds(20, loop.quit)
loop.run()
if "params" not in result:
    raise SystemExit("timeout")
code, options = result["params"].unpack()
if code != 0:
    raise SystemExit(f"denied:{code}")
print(options["uri"])
"""
    proc = subprocess.run(
        ["/usr/bin/python3", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "未知错误").strip()
        raise RuntimeError(detail)
    uri = proc.stdout.strip().splitlines()[-1]
    src = Path(unquote(urlparse(uri).path))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(src, dest)


# ─── 主流程 ─────────────────────────────────────────────────────

def run_cycle(cfg: dict):
    """截一张屏，交给千问 2.5 读图，并按截图创建时间保存文字。"""
    screenshot_dir = get_screenshot_dir(cfg)
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = screenshot_dir / f"{datetime.now():%Y-%m-%d_%H%M%S}.png"
    try:
        capture_screenshot(screenshot_path)
    except Exception as e:
        log.error("截屏失败: %s", e)
        return

    stamp = image_time(screenshot_path)
    named = screenshot_dir / f"{stamp:%Y-%m-%d_%H%M%S}.png"
    if named != screenshot_path:
        if named.exists():
            named = screenshot_dir / f"{stamp:%Y-%m-%d_%H%M%S_%f}.png"
        screenshot_path.rename(named)
        screenshot_path = named
    log.info("📸 截图: %s", screenshot_path)

    text_desc = describe_screenshot(cfg, screenshot_path)
    if not text_desc:
        log.warning("（读图失败）")
        return

    desc_dir = get_data_dir(cfg) / "descriptions"
    desc_dir.mkdir(parents=True, exist_ok=True)
    desc_path = desc_dir / f"{screenshot_path.stem}.txt"
    desc_path.write_text(text_desc, encoding="utf-8")
    log.info("📝 描述已保存: %s", desc_path)

# ─── 循环 ───────────────────────────────────────────────────────

def run_loop(cfg: dict):
    interval = cfg["screenshot_interval"]
    log.info("🔄 循环模式启动，间隔 %d 分钟（Ctrl+C 停止）", interval)
    catch_up_finished_hours(cfg)
    current_hour = datetime.now().hour
    while True:
        try:
            run_cycle(cfg)
            now = datetime.now()
            if now.hour != current_hour:
                ended = now - timedelta(hours=1)
                summarize_hour(cfg, ended.date(), ended.hour)
                current_hour = now.hour
            next_run = now.replace(second=0, microsecond=0) + timedelta(minutes=interval)
            wait = (next_run - datetime.now()).total_seconds()
            if wait > 0:
                log.info("⏳ 下次: %s（%.0fs）", next_run.strftime("%H:%M"), wait)
            time.sleep(wait)
        except KeyboardInterrupt:
            log.info("\n👋 已停止")
            break
        except Exception as e:
            log.error("❌ 异常: %s", e, exc_info=True)
            time.sleep(30)

# ─── CLI ────────────────────────────────────────────────────────

TEMPLATE_MD = """# 日报 | {{ d.strftime('%Y年%m月%d日 %A') }}

## 当天完成任务

{% if tasks %}
{{ tasks }}
{% else %}
（未能生成任务摘要，请查看下方分时记录。）
{% endif %}

## 分时记录

{% for item in hours %}
### {{ item.label }}

{{ item.summary }}

{% endfor %}
---
*由千问 2.5 读图、千问 3.6 按时段汇总并生成任务报告 | {{ now.strftime('%Y-%m-%d %H:%M') }}*
"""

def main():
    parser = argparse.ArgumentParser(description="每日任务总结：截屏读图，按小时汇总，再写日报")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--loop", action="store_true", help="定时截屏；整点汇总上一小时")
    actions.add_argument("--once", action="store_true", help="只截一张屏并读图")
    actions.add_argument("--report", nargs="?", const="today", default=None, help="补齐小时摘要并写日报，可跟日期 YYYY-MM-DD")
    actions.add_argument("--hour", nargs="?", const="now", default=None, help="汇总某一小时，可跟 0-23，默认当前小时")
    actions.add_argument("--test-vision", action="store_true", help="截一张屏并打印读图结果")
    args = parser.parse_args()

    cfg = load_config()

    if args.loop:
        run_loop(cfg)
    elif args.once:
        run_cycle(cfg)
    elif args.report:
        d = date.today() if args.report in (None, "", "today") else datetime.strptime(args.report, "%Y-%m-%d").date()
        result = generate_report(cfg, d)
        print(f"\n✅ 日报: {result}" if result else "这一天没有读图记录。")
    elif args.hour is not None:
        if args.hour == "now":
            hour = datetime.now().hour
        else:
            hour = int(args.hour)
            if not 0 <= hour <= 23:
                parser.error("小时必须是 0 到 23")
        entry = summarize_hour(cfg, date.today(), hour, force=True)
        print(entry["summary"] if entry else "这一小时没有读图记录。")
    elif args.test_vision:
        screenshot_dir = get_screenshot_dir(cfg)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        sp = screenshot_dir / "test.png"
        capture_screenshot(sp)
        print(describe_screenshot(cfg, sp))
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
