"""
官方電子聯單 CSV 自動下載＋自動對帳（在你自己的電腦上執行，不是 Streamlit 網頁的一部分）。

流程：
  1. 開瀏覽器登入「營建剩餘土石方電子聯單」(ld.nlma.gov.tw)
  2. 即時查詢(單筆) → 場所流向編號 EYG10099 → 查詢 → 找出每一種土質的聯單列表
  3. 每個土質列表設定出場日期 → 匯出 CSV
  4. 用 reconcile.py（跟「🧾 官方聯單對帳」分頁同一套規則）對帳，直接寫進雲端 dispatch_logs
  5. Discord 通知結果

第一次使用：
  python auto_manifest.py --setup                 設定帳號、密碼（密碼存在 Windows 認證管理員）
  python auto_manifest.py --download-only --show  只下載CSV、看得到瀏覽器，先確認抓得到
  python auto_manifest.py --dry-run               下載＋對帳試算，不寫入雲端
  python auto_manifest.py                         正式執行（預設處理昨天和今天）
其他：
  python auto_manifest.py --date 2026-11-20       指定日期（可重複多次）
"""

import argparse
import getpass
import json
import re
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "auto_manifest_config.json"
SECRETS_PATH = BASE_DIR / ".streamlit" / "secrets.toml"
DOWNLOAD_DIR = BASE_DIR / "manifest_downloads"
LOG_DIR = BASE_DIR / "manifest_logs"
PROFILE_DIR = BASE_DIR / ".manifest_browser"   # 瀏覽器登入狀態，不必每次重新登入
KEYRING_SERVICE = "nlma-manifest"
FINAL_HOUR = 20   # 當天 20:00 以後跑，才把「今天」視為所有土質都到齊（可清殘留紀錄）

DEFAULT_CONFIG = {
    "site_url": "https://ld.nlma.gov.tw/",
    "flow_code": "EYG10099",
    "username": "",
    "discord_webhook": "",
}

_log_lines = []


def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    _log_lines.append(line)


# ───────────────────────── 設定與密碼 ─────────────────────────
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def setup():
    import keyring
    cfg = load_config()
    print("=== 電子聯單自動下載 設定 ===")
    u = input(f"電子聯單帳號 [{cfg['username']}]：").strip() or cfg["username"]
    p = getpass.getpass("電子聯單密碼（輸入時不會顯示）：")
    if p:
        keyring.set_password(KEYRING_SERVICE, u, p)
        print("✅ 密碼已存進 Windows 認證管理員（不會寫進任何檔案）")
    w = input(f"Discord Webhook 網址（不用通知直接按 Enter）[{cfg['discord_webhook'][:30]}]：").strip()
    cfg["username"] = u
    if w:
        cfg["discord_webhook"] = w
    save_config(cfg)
    print(f"✅ 設定已存到 {CONFIG_PATH.name}")
    if not SECRETS_PATH.exists():
        print(f"⚠️ 找不到 {SECRETS_PATH}：請把 Streamlit Cloud 上這個 App 的 Secrets 內容複製一份存到這個位置，才能寫入雲端。")


def get_password(username):
    import keyring
    p = keyring.get_password(KEYRING_SERVICE, username)
    if not p:
        raise RuntimeError("還沒設定密碼，請先執行：python auto_manifest.py --setup")
    return p


def notify(cfg, text):
    if not cfg.get("discord_webhook"):
        return
    try:
        import requests
        requests.post(cfg["discord_webhook"], json={"content": text[:1900]}, timeout=15)
    except Exception as e:
        log(f"Discord 通知失敗：{e}")


# ───────────────────────── 官方網站操作 ─────────────────────────
ZH_MONTH = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}


def ensure_logged_in(page, cfg):
    page.goto(cfg["site_url"], wait_until="networkidle")
    pw = page.locator("input[type=password]")
    if pw.count() == 0 or not pw.first.is_visible():
        log("已是登入狀態")
        return
    log("登入中…")
    user_box = page.locator(
        "input:not([type=password]):not([type=hidden]):not([type=checkbox]):not([type=radio])").first
    user_box.fill(cfg["username"])
    pw.first.fill(get_password(cfg["username"]))
    page.get_by_role("button", name=re.compile("登入|登錄|Login|LOGIN|確定")).first.click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)
    if page.locator("input[type=password]").count() and page.locator("input[type=password]").first.is_visible():
        raise RuntimeError("登入失敗（帳密錯誤、密碼過期，或出現了驗證碼）。請手動登入一次確認，必要時重新執行 --setup。")
    log("登入成功")


def discover_lists(page, cfg):
    """即時查詢(單筆) → 場所流向編號 → 查詢 → 每個土質各點進去，記下它的聯單列表網址。回傳 [(土質, url), ...]"""
    code = cfg["flow_code"]

    def open_query():
        page.get_by_text(re.compile(r"即時查詢\s*\(單筆\)")).first.click()
        page.wait_for_load_state("networkidle")
        box = page.get_by_label("場所流向編號")
        if box.count() == 0:
            box = page.locator("input").first
        box.first.fill(code)
        page.get_by_role("button", name=re.compile(r"^\s*查詢\s*$")).first.click()
        page.wait_for_load_state("networkidle")
        page.locator("tr", has_text=code).first.wait_for(timeout=20000)
        return page.locator("tbody tr", has_text=code)

    rows = open_query()
    n = rows.count()
    log(f"場所流向 {code} 共有 {n} 種土質")
    result = []
    for i in range(n):
        rows = open_query() if i else rows
        row = rows.nth(i)
        cells = row.locator("td")
        soil = cells.nth(2).inner_text().strip() if cells.count() > 2 else f"第{i + 1}列"
        link = row.locator("a[href*='listReceipt']")
        if link.count():
            href = link.first.get_attribute("href")
            url = href if href.startswith("http") else cfg["site_url"].rstrip("/") + "/" + href.lstrip("/")
        else:
            cells.first.click()
            page.wait_for_url(re.compile(r"/listReceipt/"), timeout=15000)
            url = page.url
        log(f"  土質 {soil} → {url}")
        result.append((soil, url))
    if not result:
        raise RuntimeError(f"查不到場所流向 {code} 的任何土質，請確認編號。")
    return result


def _date_input(page):
    box = page.get_by_label("出場日期區間")
    if box.count():
        return box.first
    idx = page.evaluate(r"""() => [...document.querySelectorAll('input')]
        .findIndex(i => /\d{4}\/\d{1,2}\/\d{1,2}\s*-\s*\d{4}/.test(i.value))""")
    if idx < 0:
        raise RuntimeError("找不到「出場日期區間」欄位")
    return page.locator("input").nth(idx)


def set_exit_date(page, d):
    """把「出場日期區間」設成 d ~ d。先試直接輸入，不行就用月曆點選。"""
    target = f"{d:%Y/%m/%d} - {d:%Y/%m/%d}"
    box = _date_input(page)
    if (box.input_value() or "").strip() == target:
        return
    # 方法1：直接打字
    try:
        box.click()
        box.press("Control+A")
        box.type(target, delay=20)
        box.press("Enter")
        page.wait_for_timeout(500)
        if (box.input_value() or "").strip() == target:
            page.keyboard.press("Escape")
            return
    except Exception:
        pass
    # 方法2：月曆點選（左邊那個月翻到目標月份，點同一天兩次＝起訖同一天）
    page.keyboard.press("Escape")
    box.click()
    page.wait_for_timeout(400)
    for _ in range(36):
        info = page.evaluate(_CALENDAR_JS, {"y": d.year, "m": d.month, "d": d.day, "months": ZH_MONTH})
        if not info or info.get("err"):
            raise RuntimeError(f"月曆操作失敗：{info}")
        page.mouse.click(info["x"], info["y"])
        page.wait_for_timeout(250)
        if info["action"] == "day":
            page.wait_for_timeout(150)
            info2 = page.evaluate(_CALENDAR_JS, {"y": d.year, "m": d.month, "d": d.day, "months": ZH_MONTH})
            if info2 and info2.get("action") == "day":
                page.mouse.click(info2["x"], info2["y"])
            break
    page.wait_for_timeout(500)
    page.keyboard.press("Escape")
    if (box.input_value() or "").strip() != target:
        raise RuntimeError(f"出場日期沒設成功，目前欄位是「{box.input_value()}」，要的是「{target}」")


# 找月曆左邊月份的標題（如「十一月 2026」），決定要往前/往後翻，或回傳目標日期格子的座標
_CALENDAR_JS = r"""
({y, m, d, months}) => {
  const re = /^(十二|十一|十|一|二|三|四|五|六|七|八|九)月\s*(\d{4})$/;
  const all = [...document.querySelectorAll('body *')].filter(e => e.children.length === 0 && e.offsetParent !== null);
  const heads = all.filter(e => re.test(e.textContent.trim()));
  if (!heads.length) return {err: 'no calendar header'};
  const h = heads[0];
  const [, mz, yy] = h.textContent.trim().match(re);
  const cy = +yy, cm = months[mz];
  const diff = (y - cy) * 12 + (m - cm);
  const nums = el => [...el.querySelectorAll('*')].filter(e => e.children.length === 0 && /^\d{1,2}$/.test(e.textContent.trim()) && e.offsetParent !== null);
  let panel = h.parentElement;
  while (panel && nums(panel).length < 28) panel = panel.parentElement;
  if (!panel) return {err: 'no panel'};
  const box = r => ({x: r.left + r.width / 2, y: r.top + r.height / 2});
  if (diff !== 0) {
    let root = panel;
    while (root && root.querySelectorAll('button').length < 2) root = root.parentElement;
    if (!root) return {err: 'no nav buttons'};
    const btns = [...root.querySelectorAll('button')].filter(b => b.offsetParent !== null);
    const b = diff < 0 ? btns[0] : btns[btns.length - 1];
    return {action: diff < 0 ? 'prev' : 'next', ...box(b.getBoundingClientRect())};
  }
  const cells = nums(panel);
  const first = cells.findIndex(e => e.textContent.trim() === '1');
  const cell = cells[first + d - 1];
  if (!cell || +cell.textContent.trim() !== d) return {err: 'day not found'};
  return {action: 'day', ...box(cell.getBoundingClientRect())};
}
"""


def download_csv(page, soil, url, d):
    page.goto(url, wait_until="networkidle")
    page.get_by_text("聯單資料列表").first.wait_for(timeout=20000)
    set_exit_date(page, d)
    page.get_by_role("button", name=re.compile("重設查詢條件|查詢")).first.click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    safe = re.sub(r"[^\w\-]+", "_", soil)
    out = DOWNLOAD_DIR / f"{d:%Y-%m-%d}_{safe}.csv"
    with page.expect_download(timeout=60000) as dl:
        page.get_by_role("button", name=re.compile("匯出\\s*CSV")).first.click()
    dl.value.save_as(out)
    log(f"  ✔ {d:%m/%d} 土質 {soil} 已下載 → {out.name}")
    return out


def fetch_all(cfg, dates, show):
    """回傳 {date: (合併後的官方DataFrame, 是否所有土質都下載成功)}"""
    from playwright.sync_api import sync_playwright
    res = {}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=not show, accept_downloads=True,
            viewport={"width": 1600, "height": 950}, locale="zh-TW")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(30000)
        try:
            ensure_logged_in(page, cfg)
            lists = discover_lists(page, cfg)
            for d in dates:
                frames, all_ok = [], True
                for soil, url in lists:
                    try:
                        f = download_csv(page, soil, url, d)
                        df = read_official_csv(f)
                        if len(df):
                            frames.append(df)
                    except Exception as e:
                        all_ok = False
                        log(f"  ✘ {d:%m/%d} 土質 {soil} 下載失敗：{e}")
                        _snap(page, f"fail_{d:%Y%m%d}_{soil}")
                off = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                res[d] = (off, all_ok)
        except Exception:
            _snap(page, "fail")
            raise
        finally:
            ctx.close()
    return res


def _snap(page, name):
    try:
        LOG_DIR.mkdir(exist_ok=True)
        f = LOG_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{re.sub(r'[^\w]+', '_', name)}.png"
        page.screenshot(path=str(f), full_page=True)
        log(f"  已存錯誤畫面截圖：{f}")
    except Exception:
        pass


def read_official_csv(path):
    # 跟「🧾 官方聯單對帳」分頁一樣用 pandas 預設型別讀，聯單序號格式才會和雲端既有資料一致
    for enc in ("utf-8-sig", "big5", "cp950"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"{path.name} 編碼無法辨識")


# ───────────────────────── 雲端試算表 ─────────────────────────
def open_sheet():
    import tomllib
    import gspread
    from config import SHEET_URL
    if not SECRETS_PATH.exists():
        raise RuntimeError(f"找不到 {SECRETS_PATH}，無法連線雲端試算表。")
    sec = tomllib.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    info = dict(sec["connections"]["gsheets"])
    info.pop("spreadsheet", None)
    info.pop("worksheet", None)
    return gspread.service_account_from_dict(info).open_by_url(SHEET_URL)


def read_logs(sh):
    from gspread_dataframe import get_as_dataframe
    # 跟網頁（st-gsheets-connection）讀法一樣
    return get_as_dataframe(sh.worksheet("dispatch_logs"), evaluate_formulas=True).dropna(how="all")


def _signature(df):
    return (len(df), tuple(df.tail(3).astype(str).values.ravel()))


def write_logs(sh, df, n_before):
    from data_store import _cell, _is_voided
    # 跟網頁 save_sheet_data 同樣的防呆：筆數一次少太多就拒寫並備份
    n_new = len(df)
    if n_before >= 20 and (n_before - n_new) > max(20, n_before * 0.1):
        raise RuntimeError(f"要寫入 {n_new} 筆，雲端有 {n_before} 筆，一次少太多，疑似讀取異常，已擋下不寫入。")
    if "作廢" in df.columns:
        df = df.copy()
        df["作廢"] = df["作廢"].apply(lambda v: "TRUE" if _is_voided(v) else "FALSE")
    ws = sh.worksheet("dispatch_logs")
    values = [[str(c) for c in df.columns]] + [[_cell(v) for v in r] for r in df.to_numpy("object")]
    n_rows, n_cols = len(values), len(df.columns)
    if n_rows > ws.row_count:
        ws.add_rows(n_rows - ws.row_count)
    if n_cols > ws.col_count:
        ws.add_cols(n_cols - ws.col_count)
    tot_rows, tot_cols = max(n_rows, ws.row_count), max(n_cols, ws.col_count)
    values = [r + [""] * (tot_cols - len(r)) for r in values]
    values += [[""] * tot_cols for _ in range(tot_rows - n_rows)]
    ws.update(range_name="A1", values=values, value_input_option="USER_ENTERED")   # 一次覆寫，不先清空


def reconcile_and_save(fetched, dry_run):
    from reconcile import missing_columns, reconcile_day
    sh = open_sheet()
    now = datetime.now()
    summary = []
    for attempt in range(3):
        base = read_logs(sh)
        df = base
        summary = []
        for d, (off, all_ok) in sorted(fetched.items()):
            if off.empty:
                summary.append(f"{d:%m/%d}：官方沒有出土資料，略過")
                continue
            miss = missing_columns(off)
            if miss:
                summary.append(f"{d:%m/%d}：CSV 缺少欄位 {miss}，略過")
                continue
            final = all_ok and (d < now.date() or now.hour >= FINAL_HOUR)
            df, s = reconcile_day(df, off, d, final_of_day=final)
            line = (f"{d:%m/%d}：官方 {s['official_rows']} 車｜更新 {s['updated']}、補齊 {s['added']}、剔除 {s['deleted']}"
                    f"（其中殘留清除 {s['extra_cleaned']}）、已對過 {s['already']}、補官方欄位 {s['enriched']}")
            if not all_ok:
                line += "｜⚠️有土質下載失敗，未清殘留紀錄"
            if len(s["leftover"]):
                line += f"｜還有 {len(s['leftover'])} 筆沒有序號"
            summary.append(line)
        if dry_run:
            summary.append("（試算模式，未寫入雲端）")
            return summary
        # 寫入前再讀一次：如果這段時間工班剛好新增了車次，重新對帳，避免把新紀錄蓋掉
        latest = read_logs(sh)
        if _signature(latest) != _signature(base):
            log("雲端在對帳期間有新資料，重新讀取再對帳一次…")
            continue
        write_logs(sh, df, len(latest))
        summary.append("✅ 已寫入雲端 dispatch_logs")
        return summary
    raise RuntimeError("雲端資料一直在變動（連續 3 次），這次先不寫入，請稍後再執行。")


# ───────────────────────── 主程式 ─────────────────────────
def main():
    ap = argparse.ArgumentParser(description="官方電子聯單 CSV 自動下載＋對帳")
    ap.add_argument("--setup", action="store_true", help="設定帳號密碼與 Discord")
    ap.add_argument("--date", action="append", help="指定日期 YYYY-MM-DD，可重複；預設昨天＋今天")
    ap.add_argument("--show", action="store_true", help="顯示瀏覽器畫面（除錯用）")
    ap.add_argument("--download-only", action="store_true", help="只下載CSV，不對帳")
    ap.add_argument("--dry-run", action="store_true", help="下載＋對帳試算，但不寫入雲端")
    a = ap.parse_args()

    if a.setup:
        setup()
        return
    cfg = load_config()
    if not cfg["username"]:
        print("請先執行：python auto_manifest.py --setup")
        sys.exit(1)

    today = date.today()
    dates = sorted({datetime.strptime(x, "%Y-%m-%d").date() for x in a.date} if a.date
                   else {today - timedelta(days=1), today})
    log(f"處理日期：{', '.join(f'{d:%Y-%m-%d}' for d in dates)}")
    try:
        fetched = fetch_all(cfg, dates, a.show)
        if a.download_only:
            for d, (off, ok) in fetched.items():
                log(f"{d:%m/%d}：共 {len(off)} 筆官方聯單" + ("" if ok else "（有土質下載失敗）"))
            return
        summary = reconcile_and_save(fetched, a.dry_run)
        for s in summary:
            log(s)
        notify(cfg, "🚚 電子聯單自動對帳\n" + "\n".join(summary))
    except Exception as e:
        log("❌ 失敗：" + str(e))
        log(traceback.format_exc())
        notify(cfg, f"❌ 電子聯單自動對帳失敗：{e}\n（詳細記錄在電腦的 manifest_logs 資料夾）")
        sys.exit(1)
    finally:
        LOG_DIR.mkdir(exist_ok=True)
        with open(LOG_DIR / f"auto_{datetime.now():%Y%m}.log", "a", encoding="utf-8") as f:
            f.write("\n".join(_log_lines) + "\n\n")


if __name__ == "__main__":
    main()
