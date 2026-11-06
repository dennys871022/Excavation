"""🏗️ 3D 工地管理中心（數位孿生）：three.js 等角積木風 3D 場景＋管理卡片。

畫面：
- 中間：整個工地的 3D 場景（各分區依累計實挖方量往下挖成坑；安全支撐、中間柱、吊車、歷建、大門、洗車台、地磅站、構台）
- 上方：KPI（累計出土、整體進度、當日車次…）
- 左側：圖層開關、上色模式（階段進度／完成度熱圖／目前挖到第幾挖）、作業階段、深度放大、視角
- 右側：點選分區的詳細資料（各挖進度條、近 30 天出土）；沒選分區時顯示當日出土分區排行
- 下方：時間軸（拖曳或播放，一天一天回放工地狀態），以及每日車次小圖

所有互動（切圖層、換顏色、拖時間軸、點分區）都在瀏覽器端完成，不會讓 Streamlit 重新執行，
所以操作很順。three.js 由瀏覽器從 CDN 載入，伺服器端不用裝任何東西。
"""
import json
from datetime import date

import pandas as pd
import streamlit.components.v1 as components

from config import LOOSE_SOIL_FACTOR, STAGE_VOLUME_COLS
from render_3d import THREE_CDN
from render_anim import build_model_layer, default_site, load_site_model
from zones import local_stage_index, zone_active_in_stage

STAGE_NAMES = ["第1挖", "第2挖", "第3挖", "第4挖", "第5挖(油槽)"]

# 安全支撐實際施作進度：由西往東架設。每一筆＝(日期, 做到哪一個分區的西緣)；日期之間線性推進，最後一筆之後維持不動。
# 有新進度時在這裡往下加一行即可，例如 ("2026-11-20", "A10")。
STRUT_PROGRESS = [
    ("2026-10-06", None),     # 開始施作（從最西側開始）
    ("2026-10-14", "A7"),     # 做到 A6 與 A7 中間（剛過歷建）
]
# ---- 以下座標取自 Revit「假設工程 吊裝模擬」模型俯視圖（用模型裡 7 台吊車對位換算成圖資座標）----
# 全區構台（第1挖完成後）：[x0, x1, y0, y1, 名稱]
MODEL_DECKS = [
    [-2694.0, -2608.3, -262.7, -248.7, "北側構台"],
    [-2678.7, -2667.2, -287.6, -262.7, "南北向構台①"],
    [-2636.4, -2624.2, -287.6, -262.8, "南北向構台②"],
    [-2695.4, -2613.4, -297.6, -287.6, "南側構台"],
]
# 前段構台（L 形，FRONT_GANTRY_DONE 完成）：滯洪池B 東側南北向＋北側東西向（接到北側構台）
MODEL_FRONT_DECKS = [
    [-2749.2, -2733.3, -285.3, -248.6, "前段構台（西側南北向）"],
    [-2749.2, -2694.0, -262.7, -248.7, "前段構台（北側）"],
]
# 工地圍籬（多邊形，依序連線）
MODEL_FENCE = [(-2764.8, -249.2), (-2748.1, -237.7), (-2608.7, -237.8), (-2582.5, -291.3), (-2584.8, -299.8),
               (-2593.8, -301.7), (-2613.6, -304.0), (-2727.0, -303.9), (-2778.4, -279.8)]
# 地磅（歷建正南方、東西向）：[x0, x1, y0, y1]；出場車道就沿這條東西線往西 → 洗車台 → 大門
MODEL_WEIGH = [-2729.0, -2710.9, -293.0, -289.7]
# 第1挖進場：大門 → 西側車道往南 → 沿西南角斜圍籬內側 → 最南側車道（圍籬內約 2.7m）往東
MODEL_P1_SOUTH_Y = -301.2
MODEL_P1_BEND = [(-2741.6, -294.0), (-2726.2, -301.2)]
# 工務所（綠色屋頂、三層樓，在南側圍籬外）：[x0, x1, y0, y1]
MODEL_OFFICE = [[-2672.2, -2656.8, -312.0, -304.4], [-2656.9, -2635.4, -310.6, -304.6]]
FRONT_GANTRY_DONE = "2026-10-14"   # 前段構台（滯洪池B東側南北向＋北側東西向，L 形）完成日


def _num(v, default=0.0):
    try:
        f = float(v)
        return default if pd.isna(f) else f
    except (TypeError, ValueError):
        return default


def build_twin_payload(logs, df_results, stage_idx, gl_off=0.0, title="CDC 工地 3D 管理中心"):
    """把分區圖資與派車紀錄整理成前端要用的資料（全部轉成簡單的 list／dict）。"""
    zones = []
    for _, r in df_results.iterrows():
        th = [float(x) for x in (r.get("各階累計方量", []) or [])]
        li = []
        for s in range(len(STAGE_VOLUME_COLS)):
            if zone_active_in_stage(r, s):
                k = local_stage_index(r, s)
                li.append(k if 0 <= k < len(th) else -1)
            else:
                li.append(-1)
        zones.append({
            "c": str(r["分區代號"]),
            "x0": round(float(r["x_min"]), 3), "x1": round(float(r["x_max"]), 3),
            "y0": round(float(r["y_min"]), 3), "y1": round(float(r["y_max"]), 3),
            "a": _num(r.get("區域面積(㎡)", 0)), "th": th, "li": li,
        })
    codes = {z["c"]: k for k, z in enumerate(zones)}

    days = []
    if logs is not None and not logs.empty and "日期" in logs.columns:
        d = logs.copy()
        d["_date"] = pd.to_datetime(d["日期"], errors="coerce")
        d = d[d["_date"].notna()]
        d["_date"] = d["_date"].dt.strftime("%Y-%m-%d")
        d["_vol"] = pd.to_numeric(d["載運方量(m³)"], errors="coerce").fillna(0.0) if "載運方量(m³)" in d.columns else 0.0
        d["_trips"] = pd.to_numeric(d["車次數"], errors="coerce").fillna(1) if "車次數" in d.columns else 1
        dest = d["運送目的地"].fillna("台北港").astype(str) if "運送目的地" in d.columns else pd.Series("台北港", index=d.index)
        d["_fda"] = dest.eq("FDA")
        d["_zone"] = d["出土分區"].fillna("").astype(str).str.strip() if "出土分區" in d.columns else ""
        for dt_, g in d.groupby("_date", sort=True):
            zl = []
            gz = g[g["_zone"].isin(codes)]
            if not gz.empty:
                agg = gz.groupby("_zone").agg(v=("_vol", "sum"), t=("_trips", "sum"))
                for zc, row in agg.iterrows():
                    # 載運方量是鬆方，換成實方才跟分區門檻／面積比
                    zl.append([codes[zc], round(float(row["v"]) / LOOSE_SOIL_FACTOR, 2), int(row["t"])])
            days.append({
                "d": dt_,
                "t": int(g.loc[~g["_fda"], "_trips"].sum()),
                "f": int(g.loc[g["_fda"], "_trips"].sum()),
                "v": round(float(g["_vol"].sum()), 1),
                "z": zl,
            })
    if not days:
        days = [{"d": date.today().isoformat(), "t": 0, "f": 0, "v": 0.0, "z": []}]

    zinfo = [{"c": z["c"], "x0": z["x0"], "x1": z["x1"], "y0": z["y0"], "y1": z["y1"]} for z in zones]
    try:
        model = build_model_layer(load_site_model(), zinfo, gl_off)
    except Exception:
        model = None
    try:
        site = default_site(df_results)
    except Exception:
        site = None

    # 第一挖（各分區第1階門檻）全部挖完的那一天：構台從這天起出現
    th0 = [z["th"][0] if z["th"] else 0.0 for z in zones]
    th0_sum, cum, slab_day = sum(th0), [0.0] * len(zones), None
    for i, day in enumerate(days):
        for k, v, _t in day["z"]:
            cum[k] += v
        if slab_day is None and th0_sum > 0 and sum(min(cum[k], th0[k]) for k in range(len(zones))) >= 0.995 * th0_sum:
            slab_day = i

    # 歷建西側（分區中心在歷建西緣以西）第1挖全部出完的那一天：繞歷建的鐵板收掉，改走到 F16 迴轉
    west_day = None
    if site and site.get("hist"):
        hx0 = site["hist"][0]
        west = {k: z["th"][z["li"][0]] for k, z in enumerate(zones)
                if (z["x0"] + z["x1"]) / 2 < hx0 and z["li"][0] >= 0 and z["th"]}
        if west:
            cum2 = [0.0] * len(zones)
            for i, day in enumerate(days):
                for k, v, _t in day["z"]:
                    cum2[k] += v
                if all(cum2[k] >= 0.98 * t for k, t in west.items()):
                    west_day = i
                    break

    # 構台位置改用 Revit 模型：覆蓋預設的構台設定，土車路線（北側／南北向②／南側）也跟著走模型位置
    front = [list(d) for d in MODEL_FRONT_DECKS]
    if site and site.get("gantry"):
        g = dict(site["gantry"])
        n, ns2, so = MODEL_DECKS[0], MODEL_DECKS[2], MODEL_DECKS[3]
        g.update({"decks": [list(d) for d in MODEL_DECKS], "n_y": (n[2] + n[3]) / 2, "s_y": (so[2] + so[3]) / 2,
                  "ns_x": (ns2[0] + ns2[1]) / 2, "deck_x0": so[0]})
        # 覆工板與地面齊平（土車直接開上去，沒有坡道）
        wx0, wx1, wy0, wy1 = MODEL_WEIGH
        g.update({"deck_top": 0.15, "ramp_len": 0.0, "jog_x": max(g.get("jog_x", wx1 + 5), wx1 + 5)})
        site = dict(site, gantry=g, weigh=list(MODEL_WEIGH), lane_out=(wy0 + wy1) / 2,
                    y_e=MODEL_P1_SOUTH_Y, p1_bend=[list(p) for p in MODEL_P1_BEND])
    xmin = {z["c"]: z["x0"] for z in zones}
    west_x = min(z["x0"] for z in zones) - 1.0
    strut_plan = [[d, (xmin.get(c, west_x) if c else west_x)] for d, c in STRUT_PROGRESS]

    return {
        "strutPlan": strut_plan, "frontGantry": FRONT_GANTRY_DONE, "frontDecks": front, "fence": [list(p) for p in MODEL_FENCE], "office": MODEL_OFFICE,
        "title": title, "zones": zones, "days": days, "stage": stage_idx, "stageNames": STAGE_NAMES,
        "loose": LOOSE_SOIL_FACTOR, "model": model, "site": site, "slabDay": slab_day, "westDay": west_day,
    }


def render_twin(payload, height=860, base_url=THREE_CDN):
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    html = _HTML.replace("__BASE__", base_url).replace("__DATA__", data)
    components.html(html, height=height, scrolling=False)
    return html


_HTML = r"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
:root{--bg:#eef2f6;--card:#fff;--tx:#1d2433;--mut:#8a93a3;--line:#e8ebf0;--red:#ef4b4b;--org:#f39a3d;--grn:#22b07d;--blu:#4b6bfb;--amb:#d99a1e;--ink:#151a24}
*{box-sizing:border-box}
html,body{margin:0;height:100%;overflow:hidden;background:var(--bg);color:var(--tx);font-family:-apple-system,'Segoe UI','PingFang TC','Microsoft JhengHei',sans-serif;font-size:13px}
#wrap{position:relative;width:100%;height:100%;background:radial-gradient(ellipse at 50% 35%,#fbfcfd 0%,#eef2f6 60%,#e6ebf1 100%)}
canvas#gl{display:block}
.mono{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;letter-spacing:1px;text-transform:uppercase}
#hud{position:absolute;left:18px;top:14px;pointer-events:none}
#hud .eb{font-size:10.5px;color:#9aa3b2}
#hud h1{margin:2px 0 7px;font-size:21px;font-weight:800;letter-spacing:.3px}
.chips{display:flex;gap:6px;flex-wrap:wrap;pointer-events:auto}
.chip{background:rgba(255,255,255,.92);border:1px solid var(--line);border-radius:7px;padding:3px 9px;font-size:12px;color:#4a5263;font-weight:600;font-variant-numeric:tabular-nums}
.chip b{color:var(--tx)}
.chip.amb{background:#fff4dc;border-color:#f5dfae;color:#a8740f}
.chip.red{background:#ffecec;border-color:#ffd0d0;color:#c43a3a}
#tr{position:absolute;right:14px;top:14px;display:flex;gap:6px}
.ib{width:34px;height:34px;border-radius:10px;border:1px solid var(--line);background:#fff;cursor:pointer;font-size:15px;display:grid;place-items:center;box-shadow:0 2px 8px rgba(30,40,60,.06)}
.ib:hover{background:#f5f7fa}
#br{position:absolute;right:14px;bottom:96px;display:flex;gap:8px;align-items:center}
.pill{border:1px solid var(--line);background:#fff;border-radius:999px;padding:7px 13px;font:inherit;font-size:12.5px;font-weight:600;color:#3a4252;cursor:pointer;box-shadow:0 3px 12px rgba(30,40,60,.08);display:flex;gap:6px;align-items:center;white-space:nowrap}
.pill:hover{background:#f6f8fb}
.pill.dark{background:var(--ink);color:#fff;border-color:var(--ink)}
.pill.dark:hover{background:#262d3b}
.pill.on{background:#eef2ff;border-color:#c9d3ff;color:#3346c9}
.badge{background:var(--red);color:#fff;border-radius:999px;font-size:10.5px;padding:0 6px;min-width:18px;text-align:center;line-height:17px}
#bl{position:absolute;left:14px;bottom:96px;display:flex;gap:6px;flex-wrap:wrap;max-width:46%}
/* 底部時間軸 */
#tl{position:absolute;left:14px;right:14px;bottom:12px;background:rgba(255,255,255,.95);border:1px solid var(--line);border-radius:14px;padding:8px 12px 9px;box-shadow:0 6px 20px rgba(30,40,60,.08);display:flex;gap:12px;align-items:center}
#play{width:36px;height:36px;border-radius:50%;border:none;background:var(--ink);color:#fff;font-size:13px;cursor:pointer;flex:none}
#tlMid{flex:1;min-width:0}
#phases{display:flex;align-items:center;gap:0;margin-bottom:5px;overflow:hidden}
.ph{display:flex;align-items:center;font-size:11.5px;color:#a3abb9;font-weight:600;white-space:nowrap}
.ph i{width:9px;height:9px;border-radius:50%;background:#d6dbe3;margin-right:5px;flex:none}
.ph.done{color:#22a06b}.ph.done i{background:#22b07d}
.ph.now{color:#1d2433}.ph.now i{background:#4b6bfb;box-shadow:0 0 0 4px rgba(75,107,251,.18)}
.ph .ar{color:#cfd5de;margin:0 8px;font-weight:400}
#chart{width:100%;height:30px;display:block;cursor:pointer}
#day{width:100%;margin:2px 0 0;accent-color:#151a24;height:14px}
#dateLbl{min-width:112px;text-align:right;font-weight:800;font-size:15px;font-variant-numeric:tabular-nums}
#dateLbl small{display:block;font-size:11px;color:var(--mut);font-weight:500}
#speed{border:1px solid var(--line);border-radius:8px;padding:4px;font:inherit;font-size:12px;background:#fff;flex:none}
/* 卡片 */
.card{position:absolute;background:var(--card);border-radius:18px;box-shadow:0 18px 50px rgba(30,40,60,.18),0 2px 6px rgba(30,40,60,.06);border:1px solid #edf0f4}
#sheet{right:14px;top:62px;width:340px;max-height:calc(100% - 222px);overflow:auto;padding:16px 18px;display:none}
#sheet.show{display:block;animation:up .22s ease}
@keyframes up{from{transform:translateY(14px);opacity:0}to{transform:none;opacity:1}}
.sh-h{display:flex;gap:11px;align-items:center;margin-bottom:10px}
.sh-ic{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;font-size:17px;flex:none}
.sh-t{font-size:17px;font-weight:800;line-height:1.2}
.sh-s{font-size:11.5px;font-weight:700;margin-top:2px}
.x{margin-left:auto;width:26px;height:26px;border-radius:50%;border:none;background:#f1f3f6;cursor:pointer;color:#7a8394;flex:none}
.kv{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #f1f3f6;font-size:13px}
.kv span{color:#7d8696}.kv b{font-variant-numeric:tabular-nums}
.sec{font-size:10.5px;color:#a0a8b5;margin:14px 0 6px}
.fold{color:var(--red);font-size:12px;font-weight:700;margin:8px 0 2px;cursor:pointer;user-select:none}
.note{background:#f7f8fb;border-radius:10px;padding:9px 11px;font-size:12.5px;line-height:1.6}
.note.r{background:#fff4f4;color:#9c2f2f}.note.g{background:#effaf5;color:#16704e}
.li{display:flex;gap:8px;align-items:flex-start;padding:6px 0;border-bottom:1px solid #f3f4f7;cursor:pointer}
.li:hover{background:#fafbfd}
.dot{width:8px;height:8px;border-radius:50%;margin-top:5px;flex:none}
.li .m{flex:1;min-width:0}.li .m b{font-size:13px}.li .m div{font-size:11px;color:#9aa2af}
.li .n{font-size:12px;font-variant-numeric:tabular-nums;color:#4a5263;white-space:nowrap}
.bar{height:6px;background:#eef0f4;border-radius:4px;overflow:hidden;position:relative}
.bar i{display:block;height:100%;border-radius:4px}
.sb{margin:7px 0}.sb .r{display:flex;justify-content:space-between;font-size:12px;margin-bottom:3px}.sb .r b{font-variant-numeric:tabular-nums}
/* Dashboard */
#dash{left:50%;top:50%;transform:translate(-50%,-50%);width:min(900px,calc(100% - 28px));max-height:calc(100% - 28px);overflow:auto;padding:22px 26px;display:none;z-index:20}
#dash.show{display:block}
#dash h2{margin:2px 0 2px;font-size:22px}
#dimmer{position:absolute;inset:0;background:rgba(238,242,246,.55);backdrop-filter:blur(3px);display:none;z-index:19}
.dgrid{display:grid;grid-template-columns:1fr 1fr;gap:22px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(64px,1fr));gap:6px}
.tile{border-radius:8px;padding:6px 4px;text-align:center;font-size:12px;font-weight:800;cursor:pointer;border:1px solid transparent;border-top-width:3px}
.tile small{display:block;font-size:10px;font-weight:600;opacity:.8}
.big{font-size:26px;font-weight:800}.big small{font-size:13px;color:var(--mut);font-weight:600}
.hb{display:grid;grid-template-columns:92px 1fr 52px;gap:8px;align-items:center;font-size:12px;margin:6px 0}
.hb .bar{height:9px}
.hb .th{position:absolute;top:-2px;bottom:-2px;width:1.5px;background:#2b3240;opacity:.35}
#tip{position:absolute;pointer-events:none;background:#fff;border:1px solid var(--line);border-radius:10px;padding:7px 10px;font-size:12px;line-height:1.55;box-shadow:0 8px 24px rgba(30,40,60,.14);display:none;white-space:nowrap;z-index:10}
#pop{left:14px;bottom:140px;width:250px;padding:14px 16px;display:none;max-height:calc(100% - 230px);overflow:auto}
#pop.show{display:block}
#pop h5{margin:10px 0 5px;font-size:11px;color:#9aa2af;font-weight:600}#pop h5:first-child{margin-top:0}
#pop label{display:flex;gap:7px;align-items:center;padding:2px 0;font-size:12.5px;cursor:pointer}
#pop select,#pop input[type=text]{width:100%;border:1px solid var(--line);border-radius:8px;padding:6px;font:inherit;background:#fff}
#pop input[type=range]{width:100%;accent-color:#151a24}
.seg{display:flex;gap:4px;flex-wrap:wrap}.seg button{flex:1;border:1px solid var(--line);background:#fff;border-radius:7px;padding:5px 4px;font:inherit;font-size:12px;cursor:pointer}
.seg button.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.sw{display:inline-block;width:10px;height:10px;border-radius:3px;vertical-align:-1px;margin-right:4px}
#legend{font-size:11.5px;line-height:1.8;color:#4a5263}
#toast{position:absolute;left:50%;top:16px;transform:translateX(-50%);background:#fff;border:1px solid var(--line);box-shadow:0 8px 24px rgba(30,40,60,.14);border-radius:12px;padding:8px 14px;display:none;z-index:30;font-size:12.5px}
#err{position:absolute;left:50%;top:45%;transform:translate(-50%,-50%);color:#c43a3a;font-size:14px;text-align:center}
@media (max-width:820px){#sheet{left:10px;right:10px;width:auto;top:auto;bottom:92px;max-height:55%}#br{bottom:92px}#bl{display:none}.dgrid{grid-template-columns:1fr}#hud h1{font-size:17px}}
</style>
<script type="importmap">{"imports":{"three":"__BASE__/build/three.module.js","three/addons/":"__BASE__/examples/jsm/"}}</script>
</head><body><div id="wrap">
<div id="hud"><div class="eb mono" id="eb"></div><h1 id="ttl"></h1><div class="chips" id="chips"></div></div>
<div id="tr"><button class="ib" id="bShot" title="存成圖片">📷</button><button class="ib" id="bFull" title="全螢幕">⛶</button></div>
<div id="bl">
  <button class="pill" data-c="iso">◇ 全景</button><button class="pill" data-c="top">▣ 鳥瞰</button><button class="pill" data-c="w">⇥ 大門</button>
  <button class="pill" id="bRot">⟳ 環繞</button><button class="pill" id="bPop">⚙ 圖層／上色</button>
</div>
<div id="br">
  <button class="pill" id="bToday">🚚 今日出土</button>
  <button class="pill" id="bAlert">⚠ 異常 <span class="badge" id="nAlert">0</span></button>
  <button class="pill dark" id="bDash">▮ 土方 Dashboard</button>
</div>
<div id="tl">
  <button id="play" title="播放／暫停（空白鍵）">▶</button>
  <div id="tlMid"><div id="phases"></div><canvas id="chart"></canvas><input type="range" id="day" min="0" value="0"></div>
  <select id="speed" title="播放速度"><option value="0.125">每天 8 秒</option><option value="0.25" selected>每天 4 秒</option><option value="0.5">每天 2 秒</option><option value="1">每天 1 秒</option><option value="4">快轉</option></select>
  <div id="dateLbl">–</div>
</div>
<div id="sheet" class="card"></div>
<div id="pop" class="card">
  <h5>作業階段（上色依據）</h5><select id="selStage"></select>
  <h5>上色模式</h5>
  <div class="seg" id="segMode"><button data-m="prog" class="on">階段進度</button><button data-m="heat">完成度</button><button data-m="layer">挖到第幾挖</button></div>
  <div id="legend" style="margin-top:6px"></div>
  <h5>圖層</h5>
  <label><input type="checkbox" id="lyLabel" checked>分區代號</label>
  <label><input type="checkbox" id="lyFlow" checked>土車動線＋土車＋怪手</label>
  <label><input type="checkbox" id="lyPin" checked>異常標記</label>
  <label><input type="checkbox" id="lyGhost">預估開挖範圍（虛線）</label>
  <label><input type="checkbox" id="lyS1" checked><span class="sw" style="background:#ff6b6b"></span>第1層支撐</label>
  <label><input type="checkbox" id="lyS2" checked><span class="sw" style="background:#ffb84d"></span>第2層支撐</label>
  <label><input type="checkbox" id="lyS3" checked><span class="sw" style="background:#9b8cff"></span>第3層支撐</label>
  <label><input type="checkbox" id="lyKp" checked><span class="sw" style="background:#8a94a6"></span>中間柱</label>
  <label><input type="checkbox" id="lyCrane"><span class="sw" style="background:#ffc53d"></span>吊車＋作業半徑</label>
  <label><input type="checkbox" id="lyGantry" checked>構台（前段 10/14 完成，全區第1挖後）</label>
  <label><input type="checkbox" id="lySite" checked>歷建／大門／洗車台／地磅／工務所</label>
  <h5>停滯判定：進行中超過 <b id="stallV">7</b> 天沒出土</h5><input type="range" id="stall" min="3" max="21" value="7">
  <h5>深度放大 <span id="zexV"></span></h5><input type="range" id="zex" min="1" max="5" step="0.25" value="1.5">
  <h5>找分區</h5><input type="text" id="find" list="zlist" placeholder="輸入代號，例如 D'12"><datalist id="zlist"></datalist>
  <div style="font-size:11px;color:#9aa2af;margin-top:10px;line-height:1.6">左鍵旋轉・右鍵平移・滾輪縮放・Esc 關閉卡片<br>空白鍵播放・←→ 換日</div>
</div>
<div id="dimmer"></div><div id="dash" class="card"></div>
<div id="tip"></div><div id="toast"></div><div id="err"></div>
</div>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
const D=__DATA__;
const $=id=>document.getElementById(id);
const fmt=(n,d=0)=>Number(n).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const wrap=$('wrap');
function toast(t){const e=$('toast');e.innerHTML=t;e.style.display='block';clearTimeout(toast._t);toast._t=setTimeout(()=>e.style.display='none',2600);}
try{
// ================= 資料 =================
const Z=D.zones,NZ=Z.length,DAYS=D.days,ND=DAYS.length,SN=D.stageNames,LF=D.loose;
const tot=Z.map(z=>z.th.length?z.th[z.th.length-1]:0),SUMTOT=tot.reduce((a,b)=>a+b,0);
const cum=new Float32Array(ND*NZ),ctr=new Float32Array(ND*NZ),dly=new Float32Array(ND*NZ),dtr=new Float32Array(ND*NZ);
const cT=new Float64Array(ND),cF=new Float64Array(ND),cV=new Float64Array(ND),lastDig=new Int32Array(ND*NZ).fill(-1);
const dayMs=DAYS.map(d=>Date.parse(d.d+'T00:00:00'));
for(let d=0;d<ND;d++){
  if(d>0){cum.copyWithin(d*NZ,(d-1)*NZ,d*NZ);ctr.copyWithin(d*NZ,(d-1)*NZ,d*NZ);lastDig.copyWithin(d*NZ,(d-1)*NZ,d*NZ);}
  for(const [k,v,t] of DAYS[d].z){cum[d*NZ+k]+=v;ctr[d*NZ+k]+=t;dly[d*NZ+k]=v;dtr[d*NZ+k]=t;if(v>0)lastDig[d*NZ+k]=d;}
  cT[d]=(d?cT[d-1]:0)+DAYS[d].t;cF[d]=(d?cF[d-1]:0)+DAYS[d].f;cV[d]=(d?cV[d-1]:0)+DAYS[d].v;}
const depthOf=(d,k)=>(Z[k].a>0&&tot[k]>0)?Math.min(cum[d*NZ+k],tot[k])/Z[k].a:0;
const curDep=new Float32Array(NZ),cd=k=>curDep[k];
const planDepth=k=>(Z[k].a>0&&tot[k]>0)?tot[k]/Z[k].a:0;
// ---- 狀態（規則同 2D 地圖 zones.zone_progress_style），配色改成粉彩 ----
const C_DONE=[95,211,164],C_RUN=[255,159,110],C_NONE=[226,230,236],C_NA=[208,213,221];
const C_OVR=[[255,209,102],[255,159,110],[122,167,255],[176,140,255]];
const C_LAYER=[[255,209,102],[255,159,110],[122,167,255],[176,140,255],[72,201,176]];
function band(k,s){const z=Z[k],li=(s===null||s===undefined)?-1:z.li[s];if(li<0||li>=z.th.length)return null;return [li>0?z.th[li-1]:0,z.th[li]];}
function status(k,cv,s){const z=Z[k],b=band(k,s);
  if(s!==null&&b){const sz=b[1]-b[0],p=sz>0?Math.min(Math.max((cv-b[0])/sz*100,0),100):100;
    if(p>=98)return {t:SN[s]+' 已完成',c:C_DONE,p,k:'done'};if(p>0)return {t:SN[s]+' 進行中 '+p.toFixed(0)+'%',c:C_RUN,p,k:'run'};return {t:SN[s]+' 尚未開始',c:C_NONE,p,k:'none'};}
  if(s!==null&&!b)return {t:'本區沒有'+SN[s],c:C_NA,p:null,k:'na'};
  const th=z.th;if(cv>0&&th.length){if(cv>=th[th.length-1]*0.98)return {t:'開挖完成',c:C_DONE,p:100,k:'done'};
    for(let i=0;i<th.length;i++)if(cv<th[i]*0.98)return {t:i===0?'1挖進行中':(i+'挖完成・'+(i+1)+'挖進行中'),c:C_OVR[Math.min(i,3)],p:cv/th[th.length-1]*100,k:'run'};}
  return {t:'尚未開挖',c:C_NONE,p:0,k:'none'};}
function heat(t){const st=[[0,[226,230,236]],[0.33,[122,167,255]],[0.66,[255,209,102]],[1,[95,211,164]]];
  for(let i=1;i<st.length;i++)if(t<=st[i][0]){const a=st[i-1],b=st[i],u=(t-a[0])/(b[0]-a[0]);return a[1].map((v,j)=>v+(b[1][j]-v)*u);}return st[st.length-1][1];}
function layerIdx(k,cv){const z=Z[k];let last=-1;for(let s=0;s<5;s++){const li=z.li[s];if(li<0)continue;const b0=li>0?z.th[li-1]:0;if(cv>b0+0.01)last=s;}return last;}
let T=ND-0.0001,MODE='prog',STAGE=(D.stage===null||D.stage===undefined)?null:D.stage,DAY=ND-1,ZEX=1.5,SEL=-1,STALL=7,PANEL=null;
// 播放時坑深在前一天與當天之間平滑過渡（frac＝當天已過的比例）
function updateDepth(frac){const e0=Math.min(1,Math.max(0,frac/0.85)),e=e0*e0*(3-2*e0);
  for(let k=0;k<NZ;k++){const a=DAY>0?depthOf(DAY-1,k):0,b=depthOf(DAY,k);curDep[k]=a+(b-a)*e;}}
function zoneColor(k){const cv=cum[DAY*NZ+k];
  if(MODE==='heat')return tot[k]>0?heat(Math.min(cv/tot[k],1)):C_NA;
  if(MODE==='layer'){const l=layerIdx(k,cv);return l<0?C_NONE:C_LAYER[l];}
  return status(k,cv,STAGE).c;}
const css=c=>`rgb(${c.map(Math.round).join(',')})`;
function stageProg(d,s){let done=0,all=0;for(let k=0;k<NZ;k++){const b=band(k,s);if(!b)continue;all+=b[1]-b[0];done+=Math.min(Math.max(cum[d*NZ+k]-b[0],0),b[1]-b[0]);}return all>0?done/all:0;}
function curStage(d){for(let s=0;s<5;s++)if(stageProg(d,s)<0.98)return s;return 5;}
// 異常：進行中但停滯、超出預估
function alerts(d){const out=[];for(let k=0;k<NZ;k++){const cv=cum[d*NZ+k];if(cv<=0)continue;
    if(tot[k]>0&&cv>tot[k]*1.02){out.push({k,lv:'amb',t:'超出預估 '+((cv/tot[k]-1)*100).toFixed(0)+'%',why:`累計實挖 ${fmt(cv)} m³ 已超過圖資預估 ${fmt(tot[k])} m³，建議核對分區登記或圖資 GL。`});continue;}
    const s=STAGE===null?curStage(d):STAGE,st=status(k,cv,s===5?null:s);
    if(st.k==='run'){const ld=lastDig[d*NZ+k];const gap=ld>=0?Math.round((dayMs[d]-dayMs[ld])/864e5):999;
      if(gap>=STALL)out.push({k,lv:'red',t:'停滯 '+gap+' 天',why:`${st.t}，最後出土 ${DAYS[ld].d.slice(5).replace('-','/')}，已 ${gap} 天沒有出土紀錄。`});}}
  return out.sort((a,b)=>(a.lv==='red'?0:1)-(b.lv==='red'?0:1));}
// ================= 場景 =================
let X0=1e18,X1=-1e18,Y0=1e18,Y1=-1e18;Z.forEach(z=>{X0=Math.min(X0,z.x0);X1=Math.max(X1,z.x1);Y0=Math.min(Y0,z.y0);Y1=Math.max(Y1,z.y1);});
const W=X1-X0,H=Y1-Y0,size=Math.max(W,H),cx=(X0+X1)/2,cy=(Y0+Y1)/2;
const WX=x=>x-cx,WZ=y=>-(y-cy);
const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true,preserveDrawingBuffer:true});
renderer.domElement.id='gl';renderer.setPixelRatio(Math.min(2,window.devicePixelRatio||1));
renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;renderer.setClearColor(0x000000,0);
wrap.prepend(renderer.domElement);
const scene=new THREE.Scene();
const VIEW=size*0.44;
const camera=new THREE.OrthographicCamera(-VIEW,VIEW,VIEW,-VIEW,-size*6,size*6);
const controls=new OrbitControls(camera,renderer.domElement);
controls.enableDamping=true;controls.maxPolarAngle=Math.PI/2-0.05;controls.minZoom=0.5;controls.maxZoom=12;controls.zoomToCursor=true;
const ISO=[size,size*0.95,size*1.05];camera.position.set(...ISO);controls.target.set(0,-2,0);
scene.add(new THREE.HemisphereLight(0xffffff,0xdfe6ee,1.9));
const sun=new THREE.DirectionalLight(0xffffff,1.6);sun.position.set(-size*0.35,size*0.9,size*0.45);sun.castShadow=true;
sun.shadow.mapSize.set(4096,4096);{const c=sun.shadow.camera,r=size*0.75;c.left=-r;c.right=r;c.top=r;c.bottom=-r;c.near=1;c.far=size*3;}
sun.shadow.bias=-0.0005;sun.shadow.normalBias=0.05;sun.shadow.radius=4;scene.add(sun);
const MAT=(c,o={})=>new THREE.MeshStandardMaterial({color:c,roughness:o.r??0.9,metalness:o.m??0,transparent:!!o.op,opacity:o.op??1});
function box(w,h,d,c,o){const m=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),typeof c==='object'?c:MAT(c,o));m.castShadow=true;m.receiveShadow=true;return m;}
const lin=v=>{v/=255;return v<=0.04045?v/12.92:Math.pow((v+0.055)/1.055,2.4);};
// ---- 地形格網 ----
const step=size/240,pad=step*14;
const gx0=X0-pad,gy0=Y0-pad,nx=Math.ceil((W+2*pad)/step),ny=Math.ceil((H+2*pad)/step),NV=(nx+1)*(ny+1);
const zi=new Int16Array(NV).fill(-1);
Z.forEach((z,k)=>{const i0=Math.max(0,Math.ceil((z.x0-gx0)/step)),i1=Math.min(nx,Math.ceil((z.x1-gx0)/step)-1);
  const j0=Math.max(0,Math.ceil((z.y0-gy0)/step)),j1=Math.min(ny,Math.ceil((z.y1-gy0)/step)-1);
  for(let j=j0;j<=j1;j++)for(let i=i0;i<=i1;i++)zi[j*(nx+1)+i]=k;});
const pos=new Float32Array(NV*3),col=new Float32Array(NV*3),hts=new Float32Array(NV);
for(let j=0;j<=ny;j++)for(let i=0;i<=nx;i++){const n=j*(nx+1)+i;pos[n*3]=WX(gx0+i*step);pos[n*3+2]=WZ(gy0+j*step);}
const idx=[];for(let j=0;j<ny;j++)for(let i=0;i<nx;i++){const a=j*(nx+1)+i,b=a+1,c=a+nx+1,d=c+1;idx.push(a,b,c,b,d,c);}
const tgeo=new THREE.BufferGeometry();tgeo.setAttribute('position',new THREE.BufferAttribute(pos,3));tgeo.setAttribute('color',new THREE.BufferAttribute(col,3));tgeo.setIndex(idx);
const terrain=new THREE.Mesh(tgeo,new THREE.MeshStandardMaterial({vertexColors:true,roughness:1}));terrain.receiveShadow=true;terrain.castShadow=true;scene.add(terrain);
const PAD=[214,242,235];
const strata=d=>d<1?[238,220,190]:d<3?[232,207,168]:d<6?[222,200,170]:[205,200,192];
function updateTerrain(){const dep=Z.map((z,k)=>cd(k)),zc=Z.map((z,k)=>zoneColor(k));
  for(let n=0;n<NV;n++){const k=zi[n];hts[n]=k>=0?-dep[k]*ZEX:0;pos[n*3+1]=hts[n];}
  for(let j=0;j<=ny;j++)for(let i=0;i<=nx;i++){const n=j*(nx+1)+i,k=zi[n];let hm=0;
    if(i>0)hm=Math.max(hm,Math.abs(hts[n-1]-hts[n]));if(i<nx)hm=Math.max(hm,Math.abs(hts[n+1]-hts[n]));
    if(j>0)hm=Math.max(hm,Math.abs(hts[n-nx-1]-hts[n]));if(j<ny)hm=Math.max(hm,Math.abs(hts[n+nx+1]-hts[n]));
    let c;if(hm>step*0.35)c=strata(-hts[n]/ZEX);
    else if(k>=0){const z=zc[k];c=dep[k]>0.02?z:[250*0.5+z[0]*0.5,250*0.5+z[1]*0.5,252*0.5+z[2]*0.5];}
    else c=PAD;
    col[n*3]=lin(c[0]);col[n*3+1]=lin(c[1]);col[n*3+2]=lin(c[2]);}
  tgeo.attributes.position.needsUpdate=true;tgeo.attributes.color.needsUpdate=true;tgeo.computeVertexNormals();tgeo.computeBoundingSphere();}
// 外圍地面＋工區邊框
{const hx0=WX(gx0)+step*0.5,hx1=WX(gx0+nx*step)-step*0.5,hz0=WZ(gy0+ny*step)+step*0.5,hz1=WZ(gy0)-step*0.5,R=size*8;
 const sh=new THREE.Shape();sh.moveTo(-R,-R);sh.lineTo(R,-R);sh.lineTo(R,R);sh.lineTo(-R,R);sh.lineTo(-R,-R);
 const ho=new THREE.Path();ho.moveTo(hx0,-hz0);ho.lineTo(hx1,-hz0);ho.lineTo(hx1,-hz1);ho.lineTo(hx0,-hz1);ho.lineTo(hx0,-hz0);sh.holes.push(ho);
 const g=new THREE.Mesh(new THREE.ShapeGeometry(sh),MAT(0xf5f7f9,{r:1}));g.rotation.x=-Math.PI/2;g.position.y=-0.03;g.receiveShadow=true;scene.add(g);
 // 圍籬（照 Revit 模型的工地外框；大門進出口留開口）
 const fm=MAT(0x8f93ea,{r:0.8}),FP=D.fence&&D.fence.length>2?D.fence:null,ST=D.site;
 const inGate=(x,y)=>ST&&x<ST.gate[0]+16&&y<ST.lane_in+6&&y>ST.lane_out-6;
 if(FP){for(let i=0;i<FP.length;i++){const [x0,y0]=FP[i],[x1,y1]=FP[(i+1)%FP.length],L=Math.hypot(x1-x0,y1-y0),n=Math.max(1,Math.round(L/2.5));
   for(let j=0;j<n;j++){const xa=x0+(x1-x0)*j/n,ya=y0+(y1-y0)*j/n,xb=x0+(x1-x0)*(j+1)/n,yb=y0+(y1-y0)*(j+1)/n;if(inGate((xa+xb)/2,(ya+yb)/2))continue;
     const m=box(Math.hypot(xb-xa,yb-ya)+0.05,1.8,0.22,fm);m.position.set(WX((xa+xb)/2),0.9,WZ((ya+yb)/2));m.rotation.y=Math.atan2(-(WZ(yb)-WZ(ya)),WX(xb)-WX(xa));scene.add(m);}}}
 else{const fw=W+2*pad,fh=H+2*pad,fx=WX(gx0+fw/2),fz=WZ(gy0+fh/2);
 [[fw,0.25,fx,fz-fh/2],[fw,0.25,fx,fz+fh/2],[0.25,fh,fx-fw/2,fz],[0.25,fh,fx+fw/2,fz]].forEach(([a,b,x,z])=>{const m=box(a,1.6,b,fm);m.position.set(x,0.8,z);scene.add(m);});}}
// 工務所（綠色屋頂三層樓）
const officeGrp=new THREE.Group();scene.add(officeGrp);
// 樹
{const tg=new THREE.Group(),trunk=MAT(0xc9a882),leaf=[MAT(0x8fdcb1),MAT(0x6fcf9d),MAT(0xa6e6c2)];
 const hsh=(a,b)=>{const s=Math.sin(a*12.9898+b*78.233)*43758.5453;return s-Math.floor(s);};
 for(let i=0;i<70;i++){const side=i%4,u=hsh(i,1),off=pad+4+hsh(i,2)*size*0.12;let x,y;
   if(side===0){x=X0-pad+u*(W+2*pad);y=Y1+off;}else if(side===1){x=X0-pad+u*(W+2*pad);y=Y0-off;}else if(side===2){x=X1+off;y=Y0+u*H;}else{x=X0-off-size*0.12;y=Y0+u*H;}
   if(D.site&&Math.abs(x-D.site.road_x)<9)continue;
   if(D.office&&D.office.some(([a0,a1,b0,b1])=>x>a0-4&&x<a1+4&&y>b0-4&&y<b1+4))continue;
   const s=0.8+hsh(i,3)*0.7,t=box(0.5*s,1.6*s,0.5*s,trunk);t.position.set(WX(x),0.8*s,WZ(y));tg.add(t);
   const l=new THREE.Mesh(new THREE.SphereGeometry(1.5*s,10,8),leaf[i%3]);l.position.set(WX(x),2.4*s,WZ(y));l.castShadow=true;tg.add(l);}
 scene.add(tg);}
// ---- 分區邊線（白色細縫，像磚塊拼接）、標籤 ----
const outlinePos=new Float32Array(NZ*8*3);
const outline=new THREE.LineSegments(new THREE.BufferGeometry().setAttribute('position',new THREE.BufferAttribute(outlinePos,3)),new THREE.LineBasicMaterial({color:0xffffff,transparent:true,opacity:0.95}));scene.add(outline);
function updateOutline(){Z.forEach((z,k)=>{const y=-cd(k)*ZEX+0.04,a=WX(z.x0),b=WX(z.x1),c=WZ(z.y0),d=WZ(z.y1);
  const p=[a,c,b,c,b,c,b,d,b,d,a,d,a,d,a,c];for(let s=0;s<8;s++){outlinePos[(k*8+s)*3]=p[s*2];outlinePos[(k*8+s)*3+1]=y;outlinePos[(k*8+s)*3+2]=p[s*2+1];}});
  outline.geometry.attributes.position.needsUpdate=true;outline.geometry.computeBoundingSphere();}
function rr(g,x,y,w,h,r){g.beginPath();g.moveTo(x+r,y);g.lineTo(x+w-r,y);g.quadraticCurveTo(x+w,y,x+w,y+r);g.lineTo(x+w,y+h-r);g.quadraticCurveTo(x+w,y+h,x+w-r,y+h);g.lineTo(x+r,y+h);g.quadraticCurveTo(x,y+h,x,y+h-r);g.lineTo(x,y+r);g.quadraticCurveTo(x,y,x+r,y);g.closePath();}
function card(lines,o={}){// 白色小招牌 sprite
  const fs=o.fs||30,pd=fs*0.45,cv=document.createElement('canvas'),g=cv.getContext('2d');
  const f1=`800 ${fs}px -apple-system,'PingFang TC','Microsoft JhengHei',sans-serif`,f2=`600 ${fs*0.68}px -apple-system,'PingFang TC','Microsoft JhengHei',sans-serif`;
  g.font=f1;let w=g.measureText(lines[0]).width;if(lines[1]){g.font=f2;w=Math.max(w,g.measureText(lines[1]).width);}
  const W_=Math.ceil(w+pd*2+(o.dot?fs*0.7:0)),H_=Math.ceil(fs*1.25+(lines[1]?fs*0.85:0)+pd*0.6);cv.width=W_+8;cv.height=H_+10;
  g.shadowColor='rgba(30,40,60,.18)';g.shadowBlur=6;g.shadowOffsetY=2;g.fillStyle=o.bg||'#fff';rr(g,4,3,W_,H_,fs*0.32);g.fill();g.shadowColor='transparent';
  if(o.bar){g.fillStyle=o.bar;rr(g,4,3,fs*0.18,H_,2);g.fill();}
  let x0=4+pd;if(o.dot){g.fillStyle=o.dot;g.beginPath();g.arc(x0+fs*0.22,3+pd*0.3+fs*0.62,fs*0.2,0,7);g.fill();x0+=fs*0.7;}
  g.textBaseline='middle';g.fillStyle=o.fg||'#1d2433';g.font=f1;g.fillText(lines[0],x0,3+pd*0.3+fs*0.62);
  if(lines[1]){g.font=f2;g.fillStyle=o.fg2||'#8a93a3';g.fillText(lines[1],x0,3+pd*0.3+fs*1.25+fs*0.3);}
  const t=new THREE.CanvasTexture(cv);t.colorSpace=THREE.SRGBColorSpace;t.anisotropy=4;
  const sp=new THREE.Sprite(new THREE.SpriteMaterial({map:t,depthTest:false,transparent:true}));const sc=(o.scale||size*0.013);
  sp.scale.set(sc*cv.width/cv.height,sc,1);sp.center.set(0.5,0);sp.renderOrder=12;return sp;}
const labels=new THREE.Group();scene.add(labels);
const labelSp=Z.map(z=>{const s=card([z.c],{fs:26,scale:size*0.0105});labels.add(s);return s;});
function updateLabels(){Z.forEach((z,k)=>labelSp[k].position.set(WX((z.x0+z.x1)/2),-cd(k)*ZEX+0.5,WZ((z.y0+z.y1)/2)));}
const ghost=new THREE.Group();scene.add(ghost);ghost.visible=false;
function buildGhost(){ghost.clear();const m=new THREE.LineDashedMaterial({color:0x4b6bfb,dashSize:0.8,gapSize:0.6,transparent:true,opacity:0.55});
  Z.forEach((z,k)=>{const pd=planDepth(k);if(pd<=0)return;const l=new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(z.x1-z.x0,pd*ZEX,z.y1-z.y0)),m);
    l.computeLineDistances();l.position.set(WX((z.x0+z.x1)/2),-pd*ZEX/2,WZ((z.y0+z.y1)/2));ghost.add(l);});}
// 選取／指到的高亮
const selBox=new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1,1,1)),new THREE.LineBasicMaterial({color:0x4b6bfb,depthTest:false,transparent:true}));selBox.renderOrder=20;selBox.visible=false;scene.add(selBox);
const selFill=new THREE.Mesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshBasicMaterial({color:0x4b6bfb,transparent:true,opacity:0.1,depthWrite:false}));selFill.visible=false;scene.add(selFill);
const hovBox=new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1,1,1)),new THREE.LineBasicMaterial({color:0x1d2433,depthTest:false,transparent:true,opacity:0.35}));hovBox.renderOrder=19;hovBox.visible=false;scene.add(hovBox);
function placeBox(b,k){if(k<0){b.visible=false;return;}const z=Z[k],d=Math.max(cd(k)*ZEX,0.3),top=1.2;
  b.scale.set(z.x1-z.x0,d+top,z.y1-z.y0);b.position.set(WX((z.x0+z.x1)/2),(top-d)/2,WZ((z.y0+z.y1)/2));b.visible=true;}
// ---- 安全支撐／中間柱／吊車 ----
const M=D.model,strutMesh=[],tmp=new THREE.Object3D();let kpMesh=null;const craneGrp=new THREE.Group();scene.add(craneGrp);craneGrp.visible=false;
const STRUT_COL=[0xff6b6b,0xffb84d,0x9b8cff];
if(M){for(let lv=0;lv<3;lv++){const list=M.segs.filter(s=>s[8]===lv);
    const im=new THREE.InstancedMesh(new THREE.BoxGeometry(1,1,1),MAT(STRUT_COL[lv],{r:0.6}),Math.max(1,list.length));im.userData.list=list;im.castShadow=true;im.count=list.length;scene.add(im);strutMesh.push(im);}
  if(M.kps.length){kpMesh=new THREE.InstancedMesh(new THREE.BoxGeometry(1,1,1),MAT(0x8a94a6,{r:0.6}),M.kps.length);kpMesh.castShadow=true;scene.add(kpMesh);}
  M.cranes.forEach(([x,y,r,h])=>{const g=new THREE.Group();g.position.set(WX(x),0,WZ(y));
    const b=box(5,2.2,3.4,0xffc53d);b.position.y=1.1;g.add(b);const t=box(1.6,1.6,1.6,0xffffff);t.position.set(1.4,2.9,0);g.add(t);
    const hh=Math.max(h,8),ms=box(0.7,hh,0.7,0xffc53d);ms.position.y=hh/2+1;ms.rotation.z=-0.25;g.add(ms);
    const ring=new THREE.Mesh(new THREE.RingGeometry(Math.max(r-0.3,0.1),r,96),new THREE.MeshBasicMaterial({color:0xffb020,transparent:true,opacity:0.6,side:THREE.DoubleSide,depthWrite:false}));ring.rotation.x=-Math.PI/2;ring.position.y=0.3;g.add(ring);
    const disk=new THREE.Mesh(new THREE.CircleGeometry(r,96),new THREE.MeshBasicMaterial({color:0xffc53d,transparent:true,opacity:0.08,side:THREE.DoubleSide,depthWrite:false}));disk.rotation.x=-Math.PI/2;disk.position.y=0.25;g.add(disk);craneGrp.add(g);});}
const strutCount=[0,0,0];
// 支撐施作進度：回傳這一天支撐做到的 X（由西往東）；開始日之前回傳 -Infinity
function strutLimit(){const P=D.strutPlan||[];if(!P.length)return Infinity;const ds=DAYS[DAY].d,ms=Date.parse(ds)+(T-DAY)*864e5;
  if(ds<P[0][0])return -Infinity;for(let i=1;i<P.length;i++){const a=Date.parse(P[i-1][0]),b=Date.parse(P[i][0]);if(ms<=b)return P[i-1][1]+(P[i][1]-P[i-1][1])*Math.max(0,Math.min(1,(ms-a)/(b-a)));}
  return P[P.length-1][1];}
function updateModel(){if(!M)return;const dep=Z.map((z,k)=>cd(k)),lim=strutLimit();
  strutMesh.forEach((im,lv)=>{let c=0;im.userData.list.forEach((s,i)=>{const [x,y,ang,len,w,dt,db,k,_l,thr]=s,on=dep[k]>=thr&&x<=lim;
    tmp.position.set(WX(x),-(dt+db)/2*ZEX,WZ(y));tmp.rotation.set(0,ang,0);tmp.scale.set(on?len:1e-4,on?Math.max(db-dt,0.3)*ZEX:1e-4,on?Math.max(w,0.4):1e-4);
    tmp.updateMatrix();im.setMatrixAt(i,tmp.matrix);if(on)c++;});im.instanceMatrix.needsUpdate=true;strutCount[lv]=c;});
  if(kpMesh){M.kps.forEach(([x,y,dt,db],i)=>{const t=Math.max(dt,0);tmp.position.set(WX(x),-(t+db)/2*ZEX,WZ(y));tmp.rotation.set(0,0,0);tmp.scale.set(0.45,Math.max(db-t,0.1)*ZEX,0.45);tmp.updateMatrix();kpMesh.setMatrixAt(i,tmp.matrix);});kpMesh.instanceMatrix.needsUpdate=true;}}
// ---- 場區設施（積木風）----
const siteGrp=new THREE.Group(),gantryGrp=new THREE.Group();scene.add(siteGrp);scene.add(gantryGrp);
const S=D.site;let GATE=null,WEIGH=null,WASH=null,ROADX=null,GUARD=null;
function at(m,x0,x1,y0,y1,y){m.position.set(WX((x0+x1)/2),y,WZ((y0+y1)/2));return m;}
function signAt(lines,x,y,h,grp,o={}){const s=card(lines,{fs:28,scale:size*0.012,...o});s.position.set(WX(x),h,WZ(y));grp.add(s);
  const pole=box(0.15,h,0.15,0xc5ccd6);pole.position.set(WX(x),h/2,WZ(y));grp.add(pole);return s;}
if(S){try{
  // 歷史建物：暖色磚牆＋白色屋頂＋窗
  const [hx0,hx1,hy0,hy1]=S.hist,hw=hx1-hx0,hd=hy1-hy0;
  siteGrp.add(at(box(hw,3.6,hd,0xf2a07b),hx0,hx1,hy0,hy1,1.8));siteGrp.add(at(box(hw-0.4,0.25,hd-0.4,0xffffff),hx0,hx1,hy0,hy1,3.75));
  siteGrp.add(at(box(hw*0.96,3.4,hd*0.96,0xf6b08f),hx0,hx1,hy0,hy1,5.6));siteGrp.add(at(box(hw+0.6,0.5,hd+0.6,0xfdfdfd),hx0,hx1,hy0,hy1,7.5));
  const winM=MAT(0x9fd8f0,{r:0.3});for(let f=0;f<2;f++)for(let i=1;i<6;i++){const w=box(hw/9,1.3,0.12,winM);w.position.set(WX(hx0+hw*i/6),1.9+f*3.8,WZ(hy0)+0.03);w.castShadow=false;siteGrp.add(w);}
  {const cw=3.6,cz0=hy0+hd*0.15,cz1=hy1-hd*0.15,chh=3.6;
   siteGrp.add(at(box(cw,0.3,cz1-cz0,0xe8ecf1),hx0-cw,hx0,cz0,cz1,chh));
   const hm=MAT(0x5d6672,{r:0.5,m:0.4});for(const [px,pz] of [[hx0-cw+0.35,cz0+0.35],[hx0-cw+0.35,cz1-0.35],[hx0-0.45,cz0+0.35],[hx0-0.45,cz1-0.35]]){
     const fl=box(0.4,chh,0.1,hm),wb=box(0.1,chh,0.4,hm);fl.position.set(WX(px),chh/2,WZ(pz));wb.position.set(WX(px),chh/2,WZ(pz));siteGrp.add(fl);siteGrp.add(wb);}}
  signAt(['歷史建物','保留・不開挖'],(hx0+hx1)/2,(hy0+hy1)/2,11,siteGrp,{bar:'#f2a07b'});
  // 洗車台：淺藍底＋薄荷綠棚架
  const [wx0,wx1,wy0,wy1]=S.wash;WASH=[(wx0+wx1)/2,(wy0+wy1)/2];siteGrp.add(at(box(wx1-wx0,0.3,wy1-wy0,0x9fd8f0,{r:0.4}),wx0,wx1,wy0,wy1,0.15));
  [-1,1].forEach(sd=>{const w=box(wx1-wx0,0.9,0.25,0x5fd3a4);w.position.set(WX(WASH[0]),0.75,WZ(WASH[1]+sd*(wy1-wy0)/2));siteGrp.add(w);});  // 兩側擋水板，沒有頂棚
  signAt(['洗車台'],WASH[0],WASH[1],4,siteGrp,{bar:'#5fd3a4'});
  // 地磅
  const [mx0,mx1,my0,my1]=S.weigh;WEIGH=[(mx0+mx1)/2,(my0+my1)/2];siteGrp.add(at(box(mx1-mx0,0.3,my1-my0,0xc9d0da,{r:0.5,m:0.2}),mx0,mx1,my0,my1,0.15));
  const kiosk=box(2.2,2.4,2.2,0xffffff);kiosk.position.set(WX(mx1)+1.6,1.2,WZ(WEIGH[1]));siteGrp.add(kiosk);signAt(['地磅站'],WEIGH[0],WEIGH[1],5,siteGrp,{bar:'#8a94a6'});
  // 警衛室：白盒子＋珊瑚色棚
  const [gux,guy]=S.guard;GUARD=[gux,guy];const gb=box(4,2.8,3.2,0xffffff);gb.position.set(WX(gux),1.4,WZ(guy));siteGrp.add(gb);
  const gr=box(5,0.3,4.2,0xff8f6b);gr.position.set(WX(gux),2.95,WZ(guy));siteGrp.add(gr);signAt(['警衛室'],gux,guy,5.5,siteGrp);
  // 大門＋馬路＋斑馬線
  const [gx]=S.gate,gz=(S.gate_z[0]+S.gate_z[1])/2;GATE=[gx,gz];ROADX=S.road_x;
  S.gate_z.forEach(y=>{const p=box(0.8,3.4,0.8,0x6b6fe0);p.position.set(WX(gx),1.7,WZ(y));siteGrp.add(p);});
  const beam=box(0.6,0.6,Math.abs(S.gate_z[1]-S.gate_z[0])+0.8,0x6b6fe0);beam.position.set(WX(gx),3.6,WZ(gz));siteGrp.add(beam);
  signAt(['大門','唯一出入口'],gx,gz,6.5,siteGrp,{bar:'#6b6fe0'});
  const rx=S.road_x,rl=H+size*0.9;const road=box(11,0.06,rl,0xd4d9e1,{r:1});road.position.set(WX(rx),0.03,WZ(cy));road.castShadow=false;siteGrp.add(road);
  for(let i=-24;i<=24;i++){const s=box(0.25,0.07,2.4,0xffffff);s.castShadow=false;s.position.set(WX(rx),0.05,WZ(cy)+i*5);siteGrp.add(s);}
  for(let i=-3;i<=3;i++){const s=box(9,0.08,0.7,0xffffff);s.castShadow=false;s.position.set(WX(rx),0.06,WZ(gz)+i*1.4);siteGrp.add(s);}
  const lane=box(Math.abs(gx-rx),0.05,Math.abs(S.lane_in-S.lane_out)+3,0xd4d9e1,{r:1});lane.castShadow=false;lane.position.set(WX((gx+rx)/2),0.03,WZ(gz));siteGrp.add(lane);
  signAt(['外部道路','南北向・左轉進場／右轉出場'],rx,Y1+size*0.08,3,siteGrp);
  // 構台
  const G=S.gantry;if(G&&G.decks){const dt=G.deck_top||1.2;G.decks.forEach(([x0,x1,y0,y1,nm])=>{const a=Math.min(x0,x1),b=Math.max(x0,x1),c=Math.min(y0,y1),d=Math.max(y0,y1);
    gantryGrp.add(at(box(b-a,0.45,d-c,0xc9d0da,{r:0.6,op:0.92}),a,b,c,d,dt));
    const rl2=box(b-a,0.5,0.15,0xff8f6b);rl2.position.set(WX((a+b)/2),dt+0.5,WZ(c));gantryGrp.add(rl2);const rl3=rl2.clone();rl3.position.z=WZ(d);gantryGrp.add(rl3);
    signAt([nm],(a+b)/2,(c+d)/2,dt+4,gantryGrp);});}
}catch(e){console.warn('site',e);}}
if(D.office&&D.office.length){const wallM=MAT(0xf3efe6),roofM=MAT(0x3cc46b,{r:0.7}),winM2=MAT(0x7fa3c9,{r:0.3});
  D.office.forEach(([x0,x1,y0,y1],i)=>{const h=9;officeGrp.add(at(box(x1-x0,h,y1-y0,wallM),x0,x1,y0,y1,h/2));officeGrp.add(at(box(x1-x0+0.6,0.5,y1-y0+0.6,roofM),x0,x1,y0,y1,h+0.25));
    for(let f=0;f<3;f++){const n=Math.max(2,Math.round((x1-x0)/2.4));for(let k=0;k<n;k++){const w=box(1.1,1.2,0.1,winM2);w.castShadow=false;w.position.set(WX(x0+(x1-x0)*(k+0.5)/n),1.6+f*3,WZ(y1)+0.06);officeGrp.add(w);
      const w2=w.clone();w2.position.z=WZ(y0)-0.06;officeGrp.add(w2);}}});
  const o=D.office;signAt(['工務所'],(o[0][0]+o[o.length-1][1])/2,(o[0][2]+o[0][3])/2,13,officeGrp,{bar:'#3cc46b'});}
// ---- 今日出土：動線、流動光點、土車、怪手 ----
const flowGrp=new THREE.Group();scene.add(flowGrp);
const dotGeo=new THREE.SphereGeometry(0.42,10,8);
let flows=[],trucks=[],diggers=[];
function makeTruck(color){const g=new THREE.Group();const bed=box(4.2,1.5,2.3,color);bed.position.set(-0.9,1.35,0);g.add(bed);
  const soil=box(3.8,0.5,2.0,0xc9a274);soil.position.set(-0.9,2.2,0);g.add(soil);g.userData.soil=soil;g.userData.bed=bed;bed.material=bed.material.clone();const cab=box(1.8,1.9,2.3,0xffffff);cab.position.set(2.1,1.55,0);g.add(cab);
  const ws=box(0.08,0.8,1.9,0x9fd8f0);ws.position.set(3.02,1.9,0);ws.castShadow=false;g.add(ws);
  const wm=MAT(0x3a4252);[[-2.2,1.1],[-2.2,-1.1],[0.2,1.1],[0.2,-1.1],[2.1,1.1],[2.1,-1.1]].forEach(([x,z])=>{const w=new THREE.Mesh(new THREE.CylinderGeometry(0.5,0.5,0.4,12),wm);w.rotation.x=Math.PI/2;w.position.set(x,0.5,z);g.add(w);});return g;}
function makeDigger(){const g=new THREE.Group();const tr=box(4,0.9,3.2,0x4a5263);tr.position.y=0.45;g.add(tr);const bd=box(3,1.6,2.6,0xffc53d);bd.position.y=1.7;g.add(bd);
  const cb=box(1.3,1.4,1.3,0xffffff);cb.position.set(0.7,3.1,0.6);g.add(cb);const arm=new THREE.Group();arm.position.set(1.2,2.2,-0.4);
  const a1=box(4,0.5,0.5,0xffc53d);a1.position.set(2,0.9,0);a1.rotation.z=0.45;arm.add(a1);const a2=box(0.5,3,0.5,0xffc53d);a2.position.set(3.8,0.4,0);arm.add(a2);
  const bk=box(1,0.9,1.1,0x4a5263);bk.position.set(3.9,-1.2,0);arm.add(bk);g.add(arm);g.userData.arm=arm;return g;}
const TRUCK_COLS=[0xff8f6b,0x7aa7ff,0x5fd3a4,0xb08cff,0xffc53d];
// ===== 土車動線（照「出土動線規劃」，與 3D 開挖動畫相同）=====
// 第一挖：門外馬路左轉進場 → 西側便道往南 → 最南側車道往東 →（繞歷建走開挖區鐵板 或 走到 F16 繞一圈）
//        → 土堆南側車道往西、停在土堆旁裝土 → 地磅 → 洗車台 → 出大門右轉往北
// 二挖起：進場上北側構台（經保留通行段）→ 構台上長臂怪手裝土 → 南北向構台②往南 → 南側構台往西 → 下坡 → 地磅 → 洗車台 → 出場
const frontGrp=new THREE.Group();scene.add(frontGrp);frontGrp.visible=false;
let RT=null;const p1Grp=new THREE.Group(),p1aGrp=new THREE.Group(),moundGrp=new THREE.Group();scene.add(p1Grp);scene.add(p1aGrp);scene.add(moundGrp);
let longArm=null,moundDig=null;
if(S&&S.gantry&&S.x_w!==undefined){try{
  const G=S.gantry;
  RT={roadX:WX(S.road_x),gZ:WZ(S.gate[1]),zIn:WZ(S.lane_in),zOut:WZ(S.lane_out),xW:WX(S.x_w),zW:WZ(S.y_w),zE:WZ(S.y_e),
    rgX0:WX(S.ring[0]),rgX1:WX(S.ring[1]),rgZ0:WZ(S.ring[3]),xF:WX(S.x_f),xU:WX(S.x_u),xLoad:WX(S.x_load),
    xJog:WX(G.jog_x),zN:WZ(G.n_y),zS:WZ(G.s_y),xD0:WX(G.deck_x0),xNS:WX(G.ns_x),xLG:WX(G.load_x),zEx:WZ(G.ex_y),DT:G.deck_top||1.2,RL:G.ramp_len||12,
    wcx:WX((S.weigh[0]+S.weigh[1])/2),wsx:WX((S.wash[0]+S.wash[1])/2),zWs:WZ((S.wash[2]+S.wash[3])/2),wx1:WX(S.weigh[1]),xL1:WX(S.mound[0])+6,
    bend:(S.p1_bend||[[S.x_w,S.y_e],[S.x_w,S.y_e]]).map(([x,y])=>[WX(x),WZ(y)])};
  const R=RT;
  // 地面車道（進場／出場、西側便道、最南側車道）
  const lane=(x0,z0,x1,z1,w,c,grp,h=0.08)=>{const l=Math.hypot(x1-x0,z1-z0)+w*0.5,m=box(l,h,w,c,{r:1});m.castShadow=false;m.position.set((x0+x1)/2,h/2,(z0+z1)/2);m.rotation.y=Math.atan2(-(z1-z0),x1-x0);grp.add(m);return m;};
  const LC=0xd4d9e1;
  lane(R.roadX,R.zIn,R.xW,R.zIn,6.5,LC,siteGrp);lane(R.roadX,R.zOut,R.xJog,R.zOut,6.5,LC,siteGrp);lane(R.wx1,R.zOut,R.xD0,R.zOut,6.5,LC,gantryGrp);
  {const [b0,b1]=R.bend;lane(R.xW,R.zIn,R.xW,b0[1],6,LC,p1Grp);lane(b0[0],b0[1],b1[0],b1[1],6,LC,p1Grp);lane(b1[0],b1[1],R.xF,R.zE,6,LC,p1Grp);}lane(R.xF,R.zE,R.xF,R.zW,6,LC,p1Grp);lane(R.xF,R.zW,R.xJog,R.zW,6,LC,p1Grp);lane(R.xJog,R.zW,R.xJog,R.zOut,6,LC,p1Grp);
  // 鋪鐵板便道：繞歷建（走開挖區）
  // 繞歷建的鐵板（走開挖區）：西側往北 → 北側往東 → 東側往南 → 土堆旁；歷建西側出完後收掉
  const PL=0xb9c3cf;[[R.bend[0][0],R.bend[0][1],R.rgX0,R.zE],[R.rgX0,R.zE,R.rgX0,R.rgZ0],[R.rgX0,R.rgZ0,R.rgX1,R.rgZ0],[R.rgX1,R.rgZ0,R.rgX1,R.zW],[R.rgX1,R.zW,R.xL1+3,R.zW]]
    .forEach(a=>lane(...a,6.4,PL,p1aGrp,0.6));
  // 構台出入：地面鐵板＋坡道（二挖起）
  if(R.RL>1){lane(R.xD0-R.RL,R.zS,R.xJog,R.zS,6.4,PL,gantryGrp,0.6);lane(R.xJog,R.zS,R.xJog,R.zOut,6,LC,gantryGrp);}
  if(R.RL>1){const ang=Math.atan2(R.DT-0.5,R.RL),len=Math.hypot(R.RL,R.DT-0.5),m=box(len,0.4,6.4,PL);m.position.set(R.xD0-R.RL/2,(R.DT+0.5)/2-0.1,R.zS);m.rotation.z=ang;gantryGrp.add(m);} // 南側構台下坡（覆工板與地面齊平時不用）
  if(G.keep){const [a,b,c,d]=G.keep;const k=at(box(Math.abs(b-a),0.08,Math.abs(d-c),0xffb84d,{op:0.55}),a,b,c,d,R.DT+0.25);k.castShadow=false;gantryGrp.add(k);signAt(['保留通行構台'],(a+b)/2,(c+d)/2,R.DT+7,gantryGrp,{bar:'#ffb84d'});}
  // 前段構台（L 形，10/14 完成）：滯洪池B 東側南北向＋北側東西向
  if(D.frontDecks&&D.frontDecks.length===2){const [v,n]=D.frontDecks;
    for(const [x0,x1,y0,y1,nm] of D.frontDecks){frontGrp.add(at(box(x1-x0,0.45,y1-y0,0xc9d0da,{r:0.6,op:0.92}),x0,x1,y0,y1,R.DT));
      const nxp=Math.max(1,Math.round((x1-x0)/8)),nyp=Math.max(1,Math.round((y1-y0)/8));
      for(let i=0;i<=nxp;i++)for(let j=0;j<=nyp;j++){if(i>0&&i<nxp&&j>0&&j<nyp)continue;const c2=box(0.6,4,0.6,0x8a94a6);c2.position.set(WX(x0+(x1-x0)*i/nxp),R.DT-2,WZ(y0+(y1-y0)*j/nyp));frontGrp.add(c2);}}
    const rl=(x0,z0,x1,z1)=>{const l=Math.hypot(x1-x0,z1-z0),m=box(l,0.5,0.15,0xff8f6b);m.position.set((x0+x1)/2,R.DT+0.5,(z0+z1)/2);m.rotation.y=Math.atan2(-(z1-z0),x1-x0);frontGrp.add(m);};
    rl(WX(v[0]),WZ(v[2]),WX(v[0]),WZ(n[3]));rl(WX(v[1]),WZ(v[2]),WX(v[1]),WZ(n[2]));rl(WX(v[0]),WZ(n[3]),WX(n[1]),WZ(n[3]));rl(WX(v[1]),WZ(n[2]),WX(n[1]),WZ(n[2]));
    // 西側南北向構台南端的上坡（從大門進場車道往北上去）
    const xv=(v[0]+v[1])/2;R.xV=WX(xv);R.zVb=WZ(v[2]);R.zNf=WZ((n[2]+n[3])/2);
    // 覆工板與地面齊平：土車從大門進場車道直接轉進西側南北向構台往北
    signAt(['前段構台','10/14 完成'],xv,(v[2]+n[2])/2,R.DT+6,frontGrp,{bar:'#8a94a6'});}
  // 第一階段土堆（D'9、D'14、E'9、E'14 圍起）＋站在土堆上的怪手
  const [mx0,mx1,my0,my1]=S.mound,mh=S.mound_h||5;const mg=new THREE.CylinderGeometry(0.62,1,1,4,1);mg.rotateY(Math.PI/4);
  const mound=new THREE.Mesh(mg,MAT(0xdcb98d,{r:1}));mound.castShadow=true;mound.receiveShadow=true;mound.scale.set((mx1-mx0)/1.4142,mh,(my1-my0)/1.4142);mound.position.set(WX((mx0+mx1)/2),mh/2,WZ((my0+my1)/2));moundGrp.add(mound);
  signAt(['第一階段土堆','土車在這裡裝土'],(mx0+mx1)/2,(my0+my1)/2,mh+6,moundGrp,{bar:'#dcb98d'});
}catch(e){console.warn('route',e);RT=null;}}
// fda=true：FDA（隔壁工地）的車從南邊來，沿馬路往北、右轉進場；出場左轉往南回去
function routeNodes(variant,p2,fda){const R=RT,N=[],add=(x,z,y=0.6,st=0,ld=0)=>N.push({x,z,y,st,ld});const YD=R.DT+0.75;
  if(fda){add(R.roadX+2,R.gZ+70);add(R.roadX+2,R.zIn+4);add(R.roadX+4,R.zIn);}else{add(R.roadX-2,R.gZ-70);add(R.roadX-2,R.zIn-5);add(R.roadX+1.5,R.zIn);}add(R.xW,R.zIn);
  if(!p2){add(R.xW,R.bend[0][1]);if(variant!==0)add(R.bend[1][0],R.bend[1][1]);
    if(variant===0){add(R.rgX0,R.zE);add(R.rgX0,R.rgZ0);add(R.rgX1,R.rgZ0);add(R.rgX1,R.zW);add(R.xL1,R.zW,0.6,0.1,1);}  // 繞歷建 → 往東迴轉、停土堆旁裝土
    else{add(R.xF,R.zE);add(R.xF,R.zW);add(R.xLoad,R.zW,0.6,0.1,1);}                                        // 走到 F16 迴轉 → 土堆旁裝土
    add(R.xJog,R.zW);add(R.xJog,R.zOut);}
  else{if(R.xV!==undefined){add(R.xV,R.zIn);add(R.xV,R.zIn-3,YD);add(R.xV,R.zNf,YD);add(R.xD0,R.zNf,YD);}  // 從大門往東 → 西側南北向構台往北 → 北側構台往東
    else{add(R.xW,R.zN);add(R.xD0-R.RL,R.zN);add(R.xD0,R.zN,YD);}add(R.xLG,R.zN,YD,0.1,1);add(R.xNS,R.zN,YD);add(R.xNS,R.zS,YD);add(R.xD0,R.zS,YD);add(R.xD0-3,R.zOut);}
  add(R.wx1+2,R.zOut);add(R.wcx,R.zOut,0.6,0.08);add(R.wsx+6,R.zOut);add(R.wsx,R.zWs,0.6,0.06);add(R.wsx-6,R.zOut);  // 過地磅 → 洗車台 → 大門
  if(fda){add(R.roadX+3,R.zOut);add(R.roadX-2,R.zOut+4);add(R.roadX-2,R.gZ+70);}else{add(R.roadX+2,R.zOut);add(R.roadX+2,R.gZ-70);}
  let tot=0;N[0].s=0;for(let i=1;i<N.length;i++){tot+=Math.hypot(N[i].x-N[i-1].x,N[i].z-N[i-1].z);N[i].s=tot;}
  const stops=N.filter(n=>n.st).map(n=>({s:n.s,f:n.st})),D=stops.reduce((a,b)=>a+b.f,0),loadS=(N.find(n=>n.ld)||{s:0}).s;
  return {N,tot,stops,D,loadS};}
function ptAt(P,sd){const N=P.N;sd=Math.max(0,Math.min(P.tot,sd));let k=1;while(k<N.length-1&&N[k].s<sd)k++;const A=N[k-1],B=N[k],q=(sd-A.s)/((B.s-A.s)||1);
  return {x:A.x+(B.x-A.x)*q,z:A.z+(B.z-A.z)*q,y:A.y+(B.y-A.y)*q};}
function posOn(P,u){const mv=1-P.D;let t=0,s0=0,sd=null;
  for(const st of P.stops){const tm=(st.s-s0)/P.tot*mv;if(u<t+tm){sd=s0+(u-t)/tm*(st.s-s0);break;}t+=tm;if(u<t+st.f){sd=st.s;break;}t+=st.f;s0=st.s;}
  if(sd===null){const tm=(P.tot-s0)/P.tot*mv;sd=s0+Math.min(1,(u-t)/(tm||1))*(P.tot-s0);}
  const a=ptAt(P,sd),b1=ptAt(P,sd-3),b2=ptAt(P,sd+3);return {x:a.x,z:a.z,y:a.y,dx:b2.x-b1.x,dz:b2.z-b1.z,sd};}
// 階段：0＝第1挖前段（繞歷建鐵板）、1＝歷建西側出完（走到 F16 迴轉）、2＝第1挖完成（走構台）
function phaseAt(d){if(D.slabDay!==null&&d>=D.slabDay)return 2;if(D.westDay!==null&&D.westDay!==undefined&&d>=D.westDay)return 1;return 0;}
const isP2=()=>phaseAt(DAY)===2;
const PHASE_TXT=['第1挖：繞歷建鐵板 → 土堆旁裝土','第1挖：歷建西側已出完，走到 F16 迴轉 → 土堆旁裝土','二挖起：走構台，構台上長臂怪手裝土'];
const plantGrp=new THREE.Group(),digGrp=new THREE.Group();scene.add(plantGrp);scene.add(digGrp);
const NSLOT=14;let curPhase=-1,fdGrp=null,routeTP=null,routeFD=null,screen=null,falls=[],arcs=[];
const soilM=MAT(0xc9a274),soilGeo=new THREE.BoxGeometry(0.7,0.55,0.7);
function routeMesh(P,col,grp){const cp=new THREE.CurvePath();for(let j=1;j<P.N.length;j++){const a=P.N[j-1],b=P.N[j];cp.add(new THREE.LineCurve3(new THREE.Vector3(a.x,a.y+0.25,a.z),new THREE.Vector3(b.x,b.y+0.25,b.z)));}
  const c=new THREE.Color(col),tube=new THREE.Mesh(new THREE.TubeGeometry(cp,Math.max(60,Math.round(P.tot/1.5)),0.3,6,false),new THREE.MeshBasicMaterial({color:c,transparent:true,opacity:0.42,depthWrite:false}));tube.renderOrder=5;grp.add(tube);
  const dots=[],nd=Math.round(P.tot/14);for(let k=0;k<nd;k++){const m=new THREE.Mesh(dotGeo,new THREE.MeshBasicMaterial({color:c}));m.userData.o=k/nd;grp.add(m);dots.push(m);}flows.push({P,dots});}
function makeScreener(){// 旋轉篩斗怪手：把盤上來的土篩過再倒到土堆
  const g=makeDigger(),arm=g.userData.arm;arm.children[arm.children.length-1].visible=false;
  // 照片的篩斗：灰色斗框（兩側厚側板＋上方背板＋斗口斗齒），中間是會轉的網狀滾筒
  const bk=new THREE.Group();bk.position.set(3.9,-1.25,0);const fm=MAT(0x7b828c,{r:0.5,m:0.5});
  [-1.05,1.05].forEach(z=>{const sp=box(2.4,2.1,0.16,fm);sp.position.set(0,0,z);bk.add(sp);});
  const top=box(2.0,0.18,2.2,fm);top.position.set(-0.2,1.0,0);bk.add(top);const back=box(0.18,1.6,2.2,fm);back.position.set(-1.1,0.2,0);bk.add(back);
  const lip=box(1.2,0.16,2.2,fm);lip.position.set(0.9,-1.0,0);lip.rotation.z=-0.25;bk.add(lip);
  for(let i=0;i<6;i++){const t=box(0.45,0.14,0.16,0x3a4252);t.position.set(1.6,-1.15,-0.9+i*0.36);bk.add(t);}
  const drum=new THREE.Group();const core=new THREE.Mesh(new THREE.CylinderGeometry(0.85,0.85,1.9,20,1,true),new THREE.MeshStandardMaterial({color:0x3a4252,roughness:0.6,side:THREE.DoubleSide}));
  core.rotation.z=Math.PI/2;drum.add(core);const mesh=new THREE.Mesh(new THREE.CylinderGeometry(0.88,0.88,1.92,20,8,true),new THREE.MeshBasicMaterial({color:0xb8c0ca,wireframe:true}));mesh.rotation.z=Math.PI/2;drum.add(mesh);
  bk.add(drum);arm.add(bk);g.userData.drum=drum;return g;}
function buildPhase(){const p=phaseAt(DAY);if(p===curPhase)return;curPhase=p;
  flowGrp.clear();plantGrp.clear();flows=[];trucks=[];falls=[];screen=null;
  const sv=$('lySite').checked;p1aGrp.visible=p===0&&sv;p1Grp.visible=p<2&&sv;moundGrp.visible=p<2;
  if(!RT)return;
  const v=p===0?0:1,p2=p===2;
  routeTP=routeNodes(v,p2,false);routeFD=routeNodes(v,p2,true);
  routeMesh(routeTP,0xff8f6b,flowGrp);fdGrp=new THREE.Group();flowGrp.add(fdGrp);routeMesh(routeFD,0x4b6bfb,fdGrp);
  for(let k=0;k<NSLOT;k++){const t=makeTruck(TRUCK_COLS[k%TRUCK_COLS.length]);t.scale.setScalar(0.85);t.userData.o=k/NSLOT;t.userData.col=TRUCK_COLS[k%TRUCK_COLS.length];t.visible=false;flowGrp.add(t);trucks.push(t);}
  // 裝土怪手＋旋轉篩斗
  if(p2){const dg=makeDigger();dg.scale.setScalar(1.15);dg.position.set(RT.xLG,RT.DT+0.25,RT.zEx);dg.userData.arm.scale.set(1.6,1,1);dg.rotation.y=Math.PI/2;dg.userData.ph=7;plantGrp.add(dg);}
  else{const [mx0,mx1,my0,my1]=S.mound,mh=S.mound_h||5,lx=p===0?RT.xL1+2:RT.xLoad;
    const ld=makeDigger();ld.position.set(lx,mh*0.45,WZ(my0+(my1-my0)*0.28));ld.rotation.y=-Math.PI/2;ld.userData.ph=7;plantGrp.add(ld);
    screen=makeScreener();screen.position.set(WX(mx0)-4.5,0,WZ((my0+my1)/2+2));screen.rotation.y=0;screen.userData.ph=3;plantGrp.add(screen);
    signAt(['旋轉篩斗','篩完倒到土堆'],mx0-6,(my0+my1)/2+6,9,plantGrp,{bar:'#ffc53d'});
    for(let i=0;i<10;i++){const m=new THREE.Mesh(soilGeo,soilM);m.userData.o=i/10;plantGrp.add(m);falls.push(m);}}}
function buildFlow(){buildPhase();const p=curPhase,day=DAYS[DAY],trips=day.t+day.f;
  // 土車：14 個車位沿路線等距排開、一直在跑；當天車次決定有幾台出現（每 3 車畫 1 台）
  const n=trips>0?Math.max(1,Math.min(NSLOT,Math.round(trips/3))):0,nF=day.f>0&&n?Math.max(1,Math.round(n*day.f/trips)):0;
  const act=[];for(let k=0;k<NSLOT;k++)if(Math.floor((k+1)*n/NSLOT)>Math.floor(k*n/NSLOT))act.push(k);
  trucks.forEach(t=>t.visible=false);
  act.forEach((k,i)=>{const t=trucks[k];if(!t)return;const fda=i>=act.length-nF;t.visible=true;t.userData.P=fda?routeFD:routeTP;t.userData.bed.material.color.setHex(fda?0x4b6bfb:t.userData.col);});
  if(fdGrp)fdGrp.visible=nF>0;
  // 當天出土的分區：小怪手把土盤上來（第1挖送到旋轉篩斗）
  digGrp.clear();diggers=[];arcs=[];
  const az=[];for(let k=0;k<NZ;k++)if(dly[DAY*NZ+k]>0)az.push(k);az.sort((a,b)=>dly[DAY*NZ+b]-dly[DAY*NZ+a]);
  az.slice(0,3).forEach((k,ai)=>{const z=Z[k],dg=makeDigger(),x=WX((z.x0+z.x1)/2-2),y=-cd(k)*ZEX,zz=WZ((z.y0+z.y1)/2);dg.scale.setScalar(0.9);dg.position.set(x,y,zz);dg.rotation.y=ai*1.3;dg.userData.ph=ai;dg.userData.k=k;digGrp.add(dg);diggers.push(dg);
    if(screen&&p<2&&S&&(z.x0+z.x1)/2>S.hist[1])for(let i=0;i<7;i++){const m=new THREE.Mesh(soilGeo,soilM);m.userData={o:i/7,from:dg};digGrp.add(m);arcs.push(m);}});
  plantGrp.children.forEach(c=>{if(c.userData.arm)diggers.push(c);});}
// ---- 異常標記（紅色浮動大頭針）----
const pinGrp=new THREE.Group();scene.add(pinGrp);
function buildPins(list){pinGrp.clear();list.slice(0,12).forEach(a=>{const z=Z[a.k],x=WX((z.x0+z.x1)/2),zz=WZ((z.y0+z.y1)/2),c=a.lv==='red'?0xef4b4b:0xf39a3d,cs=a.lv==='red'?'#ef4b4b':'#e08a25';
  const g=new THREE.Group();g.position.set(x,0,zz);const h=9;
  const ball=new THREE.Mesh(new THREE.SphereGeometry(1.05,16,12),MAT(c,{r:0.4}));ball.position.y=h;g.add(ball);
  const cone=new THREE.Mesh(new THREE.ConeGeometry(0.75,2.2,14),MAT(c,{r:0.4}));cone.rotation.x=Math.PI;cone.position.y=h-1.4;g.add(cone);
  const line=new THREE.Mesh(new THREE.CylinderGeometry(0.06,0.06,h,6),new THREE.MeshBasicMaterial({color:c,transparent:true,opacity:0.5}));line.position.y=h/2-cd(a.k)*ZEX/2;line.scale.y=(h+cd(a.k)*ZEX)/h;g.add(line);
  const ring=new THREE.Mesh(new THREE.RingGeometry(1.2,1.6,32),new THREE.MeshBasicMaterial({color:c,transparent:true,opacity:0.6,side:THREE.DoubleSide,depthWrite:false}));ring.rotation.x=-Math.PI/2;ring.position.y=-cd(a.k)*ZEX+0.1;g.add(ring);g.userData.ring=ring;
  const lb=card([Z[a.k].c+'　'+a.t],{fs:26,scale:size*0.011,fg:cs,dot:cs});lb.position.y=h+1.6;g.add(lb);g.userData.k=a.k;pinGrp.add(g);});}
// ================= 互動 =================
const ray=new THREE.Raycaster(),mp=new THREE.Vector2(),tip=$('tip');
function pick(e){const rc=renderer.domElement.getBoundingClientRect();mp.x=((e.clientX-rc.left)/rc.width)*2-1;mp.y=-((e.clientY-rc.top)/rc.height)*2+1;ray.setFromCamera(mp,camera);
  const hp=ray.intersectObjects(pinGrp.children,true)[0];if(hp){let o=hp.object;while(o&&o.userData.k===undefined)o=o.parent;if(o)return o.userData.k;}
  const h=ray.intersectObject(terrain)[0];if(!h)return -1;const X=h.point.x+cx,Y=-h.point.z+cy,i=Math.round((X-gx0)/step),j=Math.round((Y-gy0)/step);return (i>=0&&j>=0&&i<=nx&&j<=ny)?zi[j*(nx+1)+i]:-1;}
let downAt=null;
renderer.domElement.addEventListener('pointerdown',e=>{downAt=[e.clientX,e.clientY];});
renderer.domElement.addEventListener('pointerup',e=>{if(!downAt)return;const mv=Math.hypot(e.clientX-downAt[0],e.clientY-downAt[1]);downAt=null;if(mv>5||e.button!==0)return;const k=pick(e);if(k>=0)openZone(k);else closeSheet();});
renderer.domElement.addEventListener('pointermove',e=>{const k=pick(e);placeBox(hovBox,k===SEL?-1:k);
  if(k<0){tip.style.display='none';renderer.domElement.style.cursor='';return;}renderer.domElement.style.cursor='pointer';
  const cv=cum[DAY*NZ+k],st=status(k,cv,STAGE);
  tip.innerHTML=`<b>${Z[k].c}</b>　<span style="color:${css(st.c.map(v=>v*0.7))}">● ${st.t}</span><br>已挖 ${fmt(cv)} / ${fmt(tot[k])} m³（實方）・深 ${cd(k).toFixed(1)} m`;
  const rc=wrap.getBoundingClientRect();tip.style.display='block';tip.style.left=Math.min(e.clientX-rc.left+14,rc.width-280)+'px';tip.style.top=(e.clientY-rc.top+14)+'px';});
renderer.domElement.addEventListener('pointerleave',()=>{tip.style.display='none';hovBox.visible=false;});
window.addEventListener('keydown',e=>{if(e.target.tagName==='INPUT'&&e.target.type==='text')return;
  if(e.key==='Escape'){closeSheet();closeDash();$('pop').classList.remove('show');}
  if(e.code==='Space'){e.preventDefault();togglePlay();}if(e.key==='ArrowLeft')setDay(DAY-1);if(e.key==='ArrowRight')setDay(DAY+1);});
// 視角
let camGoal=null;
function camTo(p,t,zoom=1){camGoal={p:new THREE.Vector3(...p),t:new THREE.Vector3(...t),z:zoom};}
function focusZone(k){const z=Z[k],x=WX((z.x0+z.x1)/2),zz=WZ((z.y0+z.y1)/2),y=-cd(k)*ZEX;camTo([x+size,y+size*0.95,zz+size*1.05],[x,y,zz],2.6);}
const CAMS={iso:[ISO,[0,-2,0],1],top:[[0.01,size*2,0.3],[0,-2,0],1],w:[[-size*1.1,size*0.6,size*0.5],[-size*0.18,-2,0],1.7]};
document.querySelectorAll('#bl [data-c]').forEach(b=>b.onclick=()=>camTo(...CAMS[b.dataset.c]));
$('bRot').onclick=e=>{controls.autoRotate=!controls.autoRotate;controls.autoRotateSpeed=0.7;$('bRot').classList.toggle('on',controls.autoRotate);};
// 圖層面板
$('bPop').onclick=()=>{$('pop').classList.toggle('show');$('bPop').classList.toggle('on');};
$('selStage').innerHTML='<option value="">總體（不分階段）</option>'+SN.map((n,i)=>`<option value="${i}">${n}</option>`).join('');
$('selStage').value=STAGE===null?'':String(STAGE);
$('selStage').onchange=e=>{STAGE=e.target.value===''?null:+e.target.value;refresh();};
$('segMode').onclick=e=>{const m=e.target.dataset.m;if(!m)return;MODE=m;[...$('segMode').children].forEach(b=>b.classList.toggle('on',b.dataset.m===m));refresh();};
function legend(){const sw=c=>`<span class="sw" style="background:${css(c)}"></span>`;let h;
  if(MODE==='heat')h='累計實挖 ÷ 預估總實方<br>'+[0,0.33,0.66,1].map(t=>sw(heat(t))+(t*100).toFixed(0)+'%').join('　');
  else if(MODE==='layer')h=SN.map((n,i)=>sw(C_LAYER[i])+n).join('　')+'　'+sw(C_NONE)+'未開挖';
  else if(STAGE===null)h=sw(C_NONE)+'未開挖　'+C_OVR.map((c,i)=>sw(c)+(i+1)+'挖中').join('　')+'　'+sw(C_DONE)+'完成';
  else h=sw(C_NONE)+'尚未開始　'+sw(C_RUN)+'進行中　'+sw(C_DONE)+'已完成　'+sw(C_NA)+'無此階段';
  $('legend').innerHTML=h;}
const LY={lyLabel:v=>labels.visible=v,lyFlow:v=>{flowGrp.visible=v;digGrp.visible=v;plantGrp.visible=v;},lyPin:v=>pinGrp.visible=v,lyGhost:v=>ghost.visible=v,
  lyS1:v=>strutMesh[0]&&(strutMesh[0].visible=v),lyS2:v=>strutMesh[1]&&(strutMesh[1].visible=v),lyS3:v=>strutMesh[2]&&(strutMesh[2].visible=v),
  lyKp:v=>kpMesh&&(kpMesh.visible=v),lyCrane:v=>craneGrp.visible=v,lySite:v=>{siteGrp.visible=v;officeGrp.visible=v;p1Grp.visible=v&&curPhase<2;p1aGrp.visible=v&&curPhase===0;},lyGantry:()=>gantryVis()};
function gantryVis(){const on=$('lyGantry').checked;gantryGrp.visible=on&&D.slabDay!==null&&DAY>=D.slabDay;frontGrp.visible=on&&(gantryGrp.visible||!!D.frontGantry&&DAYS[DAY].d>=D.frontGantry);}
Object.keys(LY).forEach(id=>{const el=$(id);LY[id](el.checked);el.addEventListener('change',()=>{LY[id](el.checked);if(id==='lyGantry')buildFlow();});});
if(!M)['lyS1','lyS2','lyS3','lyKp','lyCrane'].forEach(id=>$(id).closest('label').style.display='none');
if(!S)['lySite','lyGantry'].forEach(id=>$(id).closest('label').style.display='none');
$('zex').oninput=e=>{ZEX=+e.target.value;$('zexV').textContent='×'+ZEX;refresh(true);};$('zexV').textContent='×'+ZEX;
$('stall').oninput=e=>{STALL=+e.target.value;$('stallV').textContent=STALL;refresh();};
$('zlist').innerHTML=Z.map(z=>`<option value="${z.c}">`).join('');
$('find').onchange=e=>{const v=e.target.value.trim().toUpperCase().replace(/[’‘`]/g,"'");const k=Z.findIndex(z=>z.c.toUpperCase()===v);if(k<0){toast('找不到分區 '+e.target.value);return;}openZone(k);};
$('bShot').onclick=()=>{renderer.render(scene,camera);const a=document.createElement('a');a.download=`3D工地_${DAYS[DAY].d}.png`;a.href=renderer.domElement.toDataURL('image/png');a.click();};
$('bFull').onclick=()=>{if(document.fullscreenElement){document.exitFullscreen();return;}(wrap.requestFullscreen?wrap.requestFullscreen():Promise.reject()).catch(()=>toast('這個瀏覽器框架不允許全螢幕'));};
// ================= 卡片 =================
const sheet=$('sheet');
function closeSheet(){SEL=-1;PANEL=null;sheet.classList.remove('show');placeBox(selBox,-1);placeBox(selFill,-1);}
function showSheet(html){sheet.innerHTML=html;sheet.classList.add('show');sheet.scrollTop=0;const x=sheet.querySelector('.x');if(x)x.onclick=closeSheet;
  sheet.querySelectorAll('[data-k]').forEach(el=>el.onclick=()=>openZone(+el.dataset.k));
  sheet.querySelectorAll('.fold').forEach(f=>f.onclick=()=>{const t=f.nextElementSibling;const h=t.style.display==='none';t.style.display=h?'':'none';f.textContent=h?'收合 ▴':'展開 ▾';});}
function openZone(k){SEL=k;PANEL='zone';placeBox(selBox,k);placeBox(selFill,k);hovBox.visible=false;focusZone(k);renderZone();}
function nextStep(k,cv){const s=STAGE===null?null:STAGE;let b=s!==null?band(k,s):null;
  if(!b){const th=Z[k].th;for(let i=0;i<th.length;i++)if(cv<th[i]*0.98){b=[i?th[i-1]:0,th[i]];return {n:i,rem:th[i]-cv};}return null;}
  return cv<b[1]*0.98?{n:s,rem:b[1]-cv}:null;}
function renderZone(){const k=SEL;if(k<0)return;const z=Z[k],cv=cum[DAY*NZ+k],st=status(k,cv,STAGE),ld=lastDig[DAY*NZ+k],al=alerts(DAY).find(a=>a.k===k);
  const icCol=al?(al.lv==='red'?'#ffe5e5':'#fff1dc'):css(st.c.map(v=>v*0.15+255*0.85)),dotCol=al?(al.lv==='red'?'#ef4b4b':'#e08a25'):css(st.c.map(v=>v*0.75));
  const ns=nextStep(k,cv);
  let bars='';for(let s=0;s<5;s++){const b=band(k,s);if(!b)continue;const sz=b[1]-b[0],dn=Math.min(Math.max(cv-b[0],0),sz),p=sz>0?dn/sz*100:0;
    bars+=`<div class="sb"><div class="r"><span>${SN[s]}${s===STAGE?' ◀':''}</span><b>${fmt(dn)} / ${fmt(sz)} m³</b></div><div class="bar"><i style="width:${p}%;background:${p>=98?'#22b07d':css(C_LAYER[s])}"></i></div></div>`;}
  const same=[];for(let j=0;j<NZ;j++)if(j!==k&&dly[DAY*NZ+j]>0)same.push(j);
  showSheet(`<div class="sh-h"><div class="sh-ic" style="background:${icCol}">⛏️</div><div><div class="sh-t">${z.c} 分區</div><div class="sh-s" style="color:${dotCol}">● ${al?al.t+'・':''}${st.t}</div></div><button class="x">✕</button></div>
   <div class="kv"><span>預估總實方</span><b>${fmt(tot[k])} m³</b></div>
   <div class="kv"><span>累計實挖（實方）</span><b style="color:${dotCol}">${fmt(cv)} m³</b></div>
   <div class="kv"><span>目前深度／預估深度</span><b>${cd(k).toFixed(2)} / ${planDepth(k).toFixed(2)} m</b></div>
   <div class="kv"><span>累計車次</span><b>${fmt(ctr[DAY*NZ+k])} 台</b></div>
   <div class="kv"><span>最近出土</span><b>${ld>=0?DAYS[ld].d.slice(5).replace('-','/'):'–'}${ld===DAY?'（今天）':''}</b></div>
   <div class="fold">收合 ▴</div><div>
   <div class="sec mono">累計出土趨勢 · 近 30 天</div><canvas id="spark" style="width:100%;height:96px"></canvas>
   <div class="sec mono">目前卡點</div><div class="note ${al?'r':''}">${al?al.why:(st.k==='done'?'這一階段已完成，可以轉往下一挖或支撐作業。':st.k==='none'?'尚未開始出土。':'正常出土中，沒有異常。')}</div>
   <div class="sec mono">下一步</div><div class="note ${ns?'':'g'}">${ns?`再挖 <b>${fmt(ns.rem)} m³</b> 實方（鬆方約 ${fmt(ns.rem*LF)} m³，約 <b>${Math.ceil(ns.rem*LF/12)} 車</b>）完成 ${SN[ns.n]}。`:'本區預估方量已全部挖完。'}</div>
   <div class="sec mono">各挖進度（實方）</div>${bars||'<div class="note">本區沒有分階資料</div>'}
   ${same.length?`<div class="sec mono">同一天也在出土 · 點擊前往</div>${same.slice(0,6).map(j=>`<div class="li" data-k="${j}"><span class="dot" style="background:${css(zoneColor(j))}"></span><div class="m"><b>${Z[j].c}</b><div>${status(j,cum[DAY*NZ+j],STAGE).t}</div></div><div class="n">${fmt(dly[DAY*NZ+j]*LF)} m³</div></div>`).join('')}`:''}
   </div>`);
  const cv2=$('spark'),r=window.devicePixelRatio||1,w=cv2.clientWidth,h=cv2.clientHeight;cv2.width=w*r;cv2.height=h*r;const g=cv2.getContext('2d');g.scale(r,r);
  const d0=Math.max(0,DAY-29),n=DAY-d0+1,ys=[];for(let i=d0;i<=DAY;i++)ys.push(cum[i*NZ+k]);const top=Math.max(tot[k],...ys)*1.05||1;
  const X=i=>6+(w-12)*(n>1?i/(n-1):0.5),Y=v=>h-16-(h-24)*v/top;
  z.th.forEach((t,i)=>{g.strokeStyle=i===z.th.length-1?'rgba(34,176,125,.6)':'rgba(138,147,163,.45)';g.setLineDash([4,4]);g.beginPath();g.moveTo(6,Y(t));g.lineTo(w-6,Y(t));g.stroke();
    g.setLineDash([]);g.fillStyle='#a0a8b5';g.font='9.5px sans-serif';g.textAlign='right';g.fillText((i+1)+'挖 '+fmt(t),w-6,Y(t)-2);});
  g.strokeStyle='#ef4b4b';g.lineWidth=2;g.beginPath();ys.forEach((v,i)=>i?g.lineTo(X(i),Y(v)):g.moveTo(X(i),Y(v)));g.stroke();
  g.fillStyle='#ef4b4b';g.beginPath();g.arc(X(n-1),Y(ys[n-1]),3,0,7);g.fill();
  g.fillStyle='#a0a8b5';g.textAlign='left';g.fillText(DAYS[d0].d.slice(5),6,h-3);g.textAlign='right';g.fillText(DAYS[DAY].d.slice(5),w-6,h-3);}
function renderToday(){PANEL='today';SEL=-1;placeBox(selBox,-1);placeBox(selFill,-1);const d=DAYS[DAY],rows=[];for(let k=0;k<NZ;k++)if(dly[DAY*NZ+k]>0)rows.push(k);rows.sort((a,b)=>dly[DAY*NZ+b]-dly[DAY*NZ+a]);
  showSheet(`<div class="sh-h"><div class="sh-ic" style="background:#eef2ff">🚚</div><div><div class="sh-t">今日出土</div><div class="sh-s" style="color:#4b6bfb">● ${d.d}・${rows.length} 個分區</div></div><button class="x">✕</button></div>
   <div class="kv"><span>台北港車次</span><b>${d.t} 台</b></div><div class="kv"><span>FDA 土方交換</span><b>${d.f} 台</b></div><div class="kv"><span>出土方量（鬆方）</span><b>${fmt(d.v)} m³</b></div>
   <div class="kv"><span>已架設支撐段</span><b>${M?fmt(strutCount.reduce((a,b)=>a+b,0)):'–'}</b></div>
   <div class="sec mono">出土分區 · 點擊在 3D 場景定位</div>${rows.length?rows.map(k=>`<div class="li" data-k="${k}"><span class="dot" style="background:${css(zoneColor(k))}"></span><div class="m"><b>${Z[k].c}</b><div>${status(k,cum[DAY*NZ+k],STAGE).t}</div></div><div class="n">${dtr[DAY*NZ+k]} 車・${fmt(dly[DAY*NZ+k]*LF)} m³</div></div>`).join(''):'<div class="note">這天沒有分區出土紀錄。</div>'}`);}
function renderAlerts(){PANEL='alert';SEL=-1;placeBox(selBox,-1);placeBox(selFill,-1);const al=alerts(DAY);
  showSheet(`<div class="sh-h"><div class="sh-ic" style="background:#ffe5e5">⚠️</div><div><div class="sh-t">異常分區</div><div class="sh-s" style="color:#ef4b4b">● ${al.length} 筆・${DAYS[DAY].d}</div></div><button class="x">✕</button></div>
   <div class="note" style="margin-bottom:6px">紅色＝進行中但 ${STALL} 天以上沒出土；橘色＝累計實挖超過圖資預估。判定天數可在「⚙ 圖層／上色」調整。</div>
   ${al.length?al.map(a=>`<div class="li" data-k="${a.k}"><span class="dot" style="background:${a.lv==='red'?'#ef4b4b':'#f39a3d'}"></span><div class="m"><b>${Z[a.k].c}　${a.t}</b><div>${a.why}</div></div></div>`).join(''):'<div class="note g">目前沒有異常 🎉</div>'}`);}
$('bToday').onclick=()=>PANEL==='today'?closeSheet():renderToday();
$('bAlert').onclick=()=>PANEL==='alert'?closeSheet():renderAlerts();
// ================= Dashboard =================
const dash=$('dash'),dim=$('dimmer');
function closeDash(){dash.classList.remove('show');dim.style.display='none';}
dim.onclick=closeDash;$('bDash').onclick=renderDash;
function weekKey(ms){const d=new Date(ms),t=new Date(Date.UTC(d.getFullYear(),d.getMonth(),d.getDate()));const dn=t.getUTCDay()||7;t.setUTCDate(t.getUTCDate()+4-dn);const y0=new Date(Date.UTC(t.getUTCFullYear(),0,1));return Math.ceil(((t-y0)/864e5+1)/7);}
function renderDash(){const s0=STAGE===null?Math.min(curStage(DAY),4):STAGE;
  const wk=[];for(let i=0;i<=DAY;i++){const w=weekKey(dayMs[i]);const last=wk[wk.length-1];if(last&&last.w===w)last.v+=DAYS[i].t+DAYS[i].f;else wk.push({w,v:DAYS[i].t+DAYS[i].f});}
  const W2=wk.slice(-12);const tiles=[];for(let k=0;k<NZ;k++){const b=band(k,s0);if(!b)continue;tiles.push([k,status(k,cum[DAY*NZ+k],s0)]);}
  const doneN=tiles.filter(t=>t[1].k==='done').length;
  const rows={};Z.forEach((z,k)=>{const m=z.c.match(/^[A-Z]+'?/);const key=m?m[0]:z.c;(rows[key]=rows[key]||[]).push(k);});
  const rowHtml=Object.entries(rows).map(([key,ks])=>{let a=0,b=0;ks.forEach(k=>{const bd=band(k,s0);if(!bd)return;b+=bd[1]-bd[0];a+=Math.min(Math.max(cum[DAY*NZ+k]-bd[0],0),bd[1]-bd[0]);});if(b<=0)return '';const p=a/b*100;
    return `<div class="hb"><span>${key.length<=2?key+' 列':key}</span><div class="bar"><i style="width:${p}%;background:${p>=98?'#22b07d':p>=60?'#ffb84d':'#ff6b6b'}"></i><span class="th" style="left:98%"></span></div><b style="text-align:right">${p.toFixed(1)}%</b></div>`;}).join('');
  const al=alerts(DAY);
  dash.innerHTML=`<div style="display:flex;align-items:start"><div><div class="mono" style="font-size:10.5px;color:#9aa3b2">EXCAVATION DASHBOARD · ${DAYS[DAY].d}</div><h2>土方 Dashboard</h2>
     <div style="color:#8a93a3;font-size:12.5px">${D.title}・依雲端派車紀錄${STAGE===null?'・目前推進到 '+SN[s0]:''}</div></div><button class="x" id="dx">✕</button></div>
   <div class="dgrid" style="margin-top:14px">
    <div><div class="sec mono" style="margin-top:0">每週車次（台北港＋FDA）</div><svg id="wk" width="100%" height="150"></svg></div>
    <div><div class="sec mono" style="margin-top:0">各挖累計進度</div>${SN.map((n,s)=>{const p=stageProg(DAY,s)*100;return `<div class="hb"><span>${n}</span><div class="bar"><i style="width:${p}%;background:${css(C_LAYER[s])}"></i><span class="th" style="left:98%"></span></div><b style="text-align:right">${p.toFixed(1)}%</b></div>`;}).join('')}
     <div style="display:flex;gap:22px;margin-top:10px"><div><div class="sec mono" style="margin:0">累計出土</div><div class="big">${fmt(cV[DAY])}<small> m³</small></div></div><div><div class="sec mono" style="margin:0">累計車次</div><div class="big">${fmt(cT[DAY]+cF[DAY])}<small> 台</small></div></div></div></div>
   </div>
   <div class="sec mono">${SN[s0]} 分區完成 · ${tiles.length} 個分區</div><div class="big" style="margin-bottom:8px">${doneN} / ${tiles.length}<small> 已完成</small></div>
   <div class="tiles">${tiles.map(([k,st])=>{const c=st.k==='done'?['#effaf5','#22b07d','#16704e']:st.k==='run'?['#fff4ec','#ff9f6e','#b65a24']:['#f5f6f8','#d4d9e1','#8a93a3'];
     return `<div class="tile" data-k="${k}" style="background:${c[0]};border-color:${c[1]};color:${c[2]}">${Z[k].c}<small>${st.k==='done'?'已完成':st.k==='run'?st.p.toFixed(0)+'%':'未開始'}</small></div>`;}).join('')}</div>
   <div class="dgrid" style="margin-top:8px"><div><div class="sec mono">各列分區 ${SN[s0]} 進度（虛線＝98% 完成門檻）</div>${rowHtml}</div>
    <div><div class="sec mono">異常 · ${al.length} 筆</div>${al.length?al.slice(0,8).map(a=>`<div class="li" data-k="${a.k}"><span class="dot" style="background:${a.lv==='red'?'#ef4b4b':'#f39a3d'}"></span><div class="m"><b>${Z[a.k].c}　${a.t}</b><div>${a.why}</div></div></div>`).join(''):'<div class="note g">目前沒有異常</div>'}</div></div>`;
  dash.classList.add('show');dim.style.display='block';$('dx').onclick=closeDash;
  dash.querySelectorAll('[data-k]').forEach(el=>el.onclick=()=>{closeDash();openZone(+el.dataset.k);});
  const svg=$('wk'),sw=svg.clientWidth||400,sh=150,mx=Math.max(1,...W2.map(w=>w.v)),nn=W2.length;const X=i=>30+(sw-40)*(nn>1?i/(nn-1):0.5),Y=v=>sh-22-(sh-36)*v/mx;
  let s='';[0,0.5,1].forEach(f=>{s+=`<line x1="30" x2="${sw-10}" y1="${Y(mx*f)}" y2="${Y(mx*f)}" stroke="#eef0f4"/><text x="24" y="${Y(mx*f)+4}" font-size="10" fill="#a0a8b5" text-anchor="end">${Math.round(mx*f)}</text>`;});
  s+=`<path d="${W2.map((w,i)=>(i?'L':'M')+X(i)+','+Y(w.v)).join('')}" fill="none" stroke="#ef4b4b" stroke-width="2"/>`;
  s+=`<path d="${W2.map((w,i)=>(i?'L':'M')+X(i)+','+Y(w.v)).join('')}L${X(nn-1)},${sh-22}L${X(0)},${sh-22}Z" fill="rgba(239,75,75,.07)"/>`;
  W2.forEach((w,i)=>{s+=`<circle cx="${X(i)}" cy="${Y(w.v)}" r="2.5" fill="#ef4b4b"/><text x="${X(i)}" y="${sh-6}" font-size="10" fill="#a0a8b5" text-anchor="middle">W${w.w}</text>`;});svg.innerHTML=s;}
// ================= HUD／時間軸 =================
function renderHud(){const d=DAYS[DAY],s0=curStage(DAY),p=STAGE===null?(()=>{let a=0;for(let k=0;k<NZ;k++)a+=Math.min(cum[DAY*NZ+k],tot[k]);return SUMTOT>0?a/SUMTOT:0;})():stageProg(DAY,STAGE);
  $('eb').textContent=`CDC SITE · ${s0<5?'STAGE '+(s0+1):'DONE'} · ${d.d}`;$('ttl').textContent=D.title;
  const al=alerts(DAY),nr=al.filter(a=>a.lv==='red').length;
  $('chips').innerHTML=`<span class="chip">${STAGE===null?'整體進度':SN[STAGE]+' 進度'} <b>${(p*100).toFixed(1)}%</b></span><span class="chip">累計出土 <b>${fmt(cV[DAY])}</b> m³</span>
    <span class="chip">今日 <b>${d.t}</b> 車${d.f?'＋FDA '+d.f:''}</span><span class="chip amb">${s0<5?'推進中：'+SN[s0]:'開挖完成'}</span>${nr?`<span class="chip red">停滯 ${nr} 區</span>`:''}`;
  $('nAlert').textContent=al.length;$('eb').textContent+=' · '+(RT?PHASE_TXT[phaseAt(DAY)]:'');$('nAlert').style.display=al.length?'':'none';buildPins(al);
  const wkd='日一二三四五六'[new Date(d.d+'T00:00:00').getDay()];$('dateLbl').innerHTML=`${d.d}<small>週${wkd}・${DAY+1}/${ND} 天</small>`;
  $('phases').innerHTML=['開挖前',...SN].map((n,i)=>{const cls=i===0?'done':(i-1<s0?'done':i-1===s0?'now':'');return (i?'<span class="ar">→</span>':'')+`<span class="ph ${cls}"><i></i>${n}</span>`;}).join('');}
const slider=$('day');slider.max=ND-1;slider.value=DAY;slider.oninput=e=>setDay(+e.target.value);
const chart=$('chart');
function drawChart(){const r=window.devicePixelRatio||1,w=chart.clientWidth,h=chart.clientHeight;if(!w)return;chart.width=w*r;chart.height=h*r;const g=chart.getContext('2d');g.scale(r,r);
  const mx=Math.max(1,...DAYS.map(d=>d.t+d.f)),bw=w/ND;
  DAYS.forEach((d,i)=>{const ht=(h-2)*(d.t+d.f)/mx;g.fillStyle=i===DAY?'#151a24':i<DAY?'#c9d0da':'#e9ecf1';g.fillRect(i*bw,h-ht,Math.max(bw-0.6,0.6),ht);});
  if(D.slabDay!==null){g.fillStyle='#4b6bfb';g.fillRect(D.slabDay*bw,0,1.5,h);}}
chart.onclick=e=>{const rc=chart.getBoundingClientRect();setDay(Math.floor((e.clientX-rc.left)/rc.width*ND));};
chart.onmousemove=e=>{const rc=chart.getBoundingClientRect(),i=Math.min(ND-1,Math.max(0,Math.floor((e.clientX-rc.left)/rc.width*ND))),d=DAYS[i];chart.title=`${d.d}　台北港 ${d.t}・FDA ${d.f} 車・${fmt(d.v)} m³`;};
let playing=false,frameN=0;
function togglePlay(){playing=!playing;if(playing&&T>=ND-0.01){DAY=-1;T=0;setDay(0,true);}if(!playing){T=DAY+0.9999;geomTick(1);}$('play').textContent=playing?'❚❚':'▶';}
$('play').onclick=togglePlay;
function setDay(d,keepT){d=Math.max(0,Math.min(ND-1,d|0));if(!keepT)T=d+(playing?0:0.9999);if(d===DAY)return;DAY=d;slider.value=d;refresh();}
function geomTick(frac){updateDepth(frac);updateTerrain();updateOutline();updateLabels();updateModel();placeBox(selBox,SEL);placeBox(selFill,SEL);
  diggers.forEach(g=>{if(g.userData.k!==undefined)g.position.y=-cd(g.userData.k)*ZEX;});}
function refresh(geom){updateDepth(T-DAY);updateTerrain();updateOutline();updateLabels();updateModel();gantryVis();if(geom||!ghost.children.length)buildGhost();
  buildFlow();renderHud();placeBox(selBox,SEL);placeBox(selFill,SEL);drawChart();legend();
  if(PANEL==='zone')renderZone();else if(PANEL==='today')renderToday();else if(PANEL==='alert')renderAlerts();if(dash.classList.contains('show'))renderDash();}
// ================= 迴圈 =================
function resize(){const w=wrap.clientWidth,h=wrap.clientHeight,a=w/h;renderer.setSize(w,h);camera.left=-VIEW*a;camera.right=VIEW*a;camera.top=VIEW;camera.bottom=-VIEW;camera.updateProjectionMatrix();drawChart();}
window.addEventListener('resize',resize);
refresh(true);resize();
let last=performance.now();const tv=new THREE.Vector3(),tv2=new THREE.Vector3();
(function loop(now){const dt=Math.min(0.1,(now-last)/1000);last=now;const sec=now/1000;
  if(playing){T+=dt*(+$('speed').value);if(T>=ND-0.0001){T=ND-0.0001;}const d=Math.floor(T);if(d!==DAY)setDay(d,true);
    if((T-DAY)<0.95&&(frameN++&1)===0)geomTick(T-DAY);if(T>=ND-0.0001){geomTick(1);togglePlay();}}
  if(camGoal){camera.position.lerp(camGoal.p,0.09);controls.target.lerp(camGoal.t,0.09);camera.zoom+=(camGoal.z-camera.zoom)*0.09;camera.updateProjectionMatrix();
    if(camera.position.distanceTo(camGoal.p)<0.5&&Math.abs(camera.zoom-camGoal.z)<0.01)camGoal=null;}
  if(flowGrp.visible){const LOOP=34;
    flows.forEach(f=>f.dots.forEach(m=>{const q=ptAt(f.P,((m.userData.o+sec*6/f.P.tot)%1)*f.P.tot);m.position.set(q.x,q.y+0.3,q.z);}));
    trucks.forEach(t=>{if(!t.visible||!t.userData.P)return;const d=t.userData,r=posOn(d.P,(d.o+sec/LOOP)%1);t.position.set(r.x,r.y-0.55,r.z);if(Math.abs(r.dx)+Math.abs(r.dz)>1e-6)t.rotation.y=Math.atan2(-r.dz,r.dx);
      t.userData.soil.visible=r.sd>=d.P.loadS-0.1;});
    diggers.forEach(g=>{g.userData.arm.rotation.z=Math.sin(sec*1.6+g.userData.ph)*0.25;});
    if(screen){const dr=screen.userData.drum;dr.getWorldPosition(tv);
      const scr=arcs.length>0;falls.forEach(m=>{m.visible=scr;});dr.rotation.x=scr?sec*3:0;
      falls.forEach(m=>{const u=(m.userData.o+sec*0.6)%1;m.position.set(tv.x+0.6+u*2.2,tv.y-0.6-u*(tv.y-2.2),tv.z+Math.sin(m.userData.o*20)*0.6);m.rotation.set(u*3,u*5,0);});
      arcs.forEach(m=>{const f=m.userData.from;if(!f)return;const u=(m.userData.o+sec*0.35)%1,ax=f.position.x,ay=f.position.y+2,az=f.position.z,bx=tv.x,by=tv.y+0.8,bz=tv.z;
        const h=Math.max(ay,by)+8;m.position.set(ax+(bx-ax)*u,(1-u)*(1-u)*ay+2*u*(1-u)*h+u*u*by,az+(bz-az)*u);m.rotation.set(u*4,u*6,0);});}}
  pinGrp.children.forEach((p,i)=>{p.position.y=Math.sin(sec*2+i)*0.4;const r=p.userData.ring;if(r){const s=1+((sec*0.8+i*0.3)%1)*1.6;r.scale.set(s,s,s);r.material.opacity=0.6*(1-((sec*0.8+i*0.3)%1));}});
  selBox.material.opacity=0.6+0.4*Math.sin(now/260);
  controls.update();renderer.render(scene,camera);requestAnimationFrame(loop);})(last);
window.__ready=true;
}catch(e){$('err').textContent='3D 管理中心載入失敗：'+e;console.error(e);}
</script></body></html>
"""
