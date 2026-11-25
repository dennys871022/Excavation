"""
本機彈出視窗：用分區地圖「點選」今日出土分區（跟網頁 zone_picker 一樣：點一下選取、再點一下取消）。
由 auto_manifest.py 在每天 16:30 呼叫。顏色規則同網頁：
  🟦 本階段還有可挖量　🟩 本階段已達標　⬜ 本階段無開挖目標　🟧 已選取
"""

import pandas as pd

FILL_OPEN = "#cfe1fb"    # 本階段還有可挖量（淡藍）
FILL_FULL = "#cdeedb"    # 本階段已達標（淡綠）
FILL_IDLE = "#e6e8ec"    # 本階段無開挖目標（淡灰）
FILL_PICK = "#ff8c00"    # 已選取（橘）
EDGE = "#8a93a3"
OUTSIDE_MAP = ["開挖前土方"]   # 不在地圖上的選項，用按鈕切換

DEFAULT_GL = {
    "全域_GL_行政棟": "-2.5, -4.45, -7.85, -9.9",
    "全域_GL_實驗棟": "-2.5, -4.45, -7.85, -11.4",
    "全域_GL_滯洪池BC": "-1.5, -4.5, -7.6",
    "全域_GL_滯洪池A": "-2.0, -5.0, -7.85",
    "全域_GL_油槽": "-4.25",
}


def load_zone_status(sh, read_logs):
    """從雲端讀目前作業階段、GL 設定、累計出土，算出每個分區的顏色與說明。回傳 (df_results, {代號: (顏色, 說明)}, 階段名稱)"""
    from config import GLOBAL_PREFS_KEY, LOOSE_SOIL_FACTOR, STAGE_OPTIONS
    from data_store import filter_active_dispatch_logs
    from stage_calc import get_stage_index
    from zones import build_zone_results, get_stage_remaining_capacity
    from gspread_dataframe import get_as_dataframe

    prefs = {}
    try:
        ss = get_as_dataframe(sh.worksheet("stage_settings"), evaluate_formulas=True).dropna(how="all")
        row = ss[ss["階段名稱"] == GLOBAL_PREFS_KEY]
        if not row.empty:
            prefs = {k: v for k, v in row.iloc[0].items() if str(k).startswith("全域_") and pd.notna(v)}
    except Exception:
        pass
    g = lambda k: str(prefs.get(k, DEFAULT_GL[k]))
    try:
        gl = float(prefs.get("全域_現地GL高程增減", 0.0))
    except (TypeError, ValueError):
        gl = 0.0
    df_results = build_zone_results(-274766.4, -24009.49, 100, gl,
                                     g("全域_GL_行政棟"), g("全域_GL_實驗棟"), g("全域_GL_滯洪池BC"),
                                     g("全域_GL_滯洪池A"), g("全域_GL_油槽"))
    stage = prefs.get("全域_目前作業階段")
    stage = stage if stage in STAGE_OPTIONS else STAGE_OPTIONS[0]
    stage_idx = get_stage_index(stage)

    excavated = {}
    try:
        logs = filter_active_dispatch_logs(read_logs(sh))
        logs = logs[logs["出土分區"].astype(str) != "未指定"].copy()
        logs["載運方量(m³)"] = pd.to_numeric(logs["載運方量(m³)"], errors="coerce")
        excavated = (logs.groupby("出土分區")["載運方量(m³)"].sum() / LOOSE_SOIL_FACTOR).to_dict()
    except Exception:
        pass

    status = {}
    for code in df_results["分區代號"]:
        if stage_idx is None:
            status[code] = (FILL_OPEN, "不限階段")
            continue
        cap = get_stage_remaining_capacity(code, df_results, stage_idx, excavated)
        if cap is None:
            status[code] = (FILL_IDLE, "本階段無開挖目標")
        elif cap <= 0:
            status[code] = (FILL_FULL, "本階段已達標")
        else:
            status[code] = (FILL_OPEN, f"本階段剩餘 {cap:,.0f} m³")
    return df_results, status, stage


def pick_zones(df_results, status, stage, title, preselect=(), minutes=30):
    """跳出地圖視窗讓你點選。回傳選到的分區 list；按「跳過」、關閉或逾時回傳 None。"""
    import tkinter as tk

    codes = df_results["分區代號"].tolist()
    chosen = [z for z in preselect if z in codes or z in OUTSIDE_MAP]
    result = {"v": None}

    root = tk.Tk()
    root.title("CDC土方 今日出土分區")
    root.attributes("-topmost", True)
    root.after(1500, lambda: root.attributes("-topmost", False))   # 先跳到最上層，之後不擋其他視窗

    tk.Label(root, text=title, font=("Microsoft JhengHei", 13, "bold")).pack(padx=14, pady=(12, 2), anchor="w")
    tk.Label(root, text=f"目前作業階段：{stage}　　點一下分區＝選取，再點一下＝取消",
             font=("Microsoft JhengHei", 10)).pack(padx=14, anchor="w")

    W, H, PAD = 1100, 560, 20
    cv = tk.Canvas(root, width=W, height=H, bg="white", highlightthickness=1, highlightbackground="#ccc")
    cv.pack(padx=14, pady=8)
    x0, x1 = df_results["x_min"].min(), df_results["x_max"].max()
    y0, y1 = df_results["y_min"].min(), df_results["y_max"].max()
    s = min((W - 2 * PAD) / (x1 - x0), (H - 2 * PAD) / (y1 - y0))
    ox = (W - (x1 - x0) * s) / 2
    oy = (H - (y1 - y0) * s) / 2
    X = lambda x: ox + (x - x0) * s
    Y = lambda y: oy + (y1 - y) * s           # 北在上

    rect_of = {}
    for _, r in df_results.iterrows():
        c = r["分區代號"]
        rid = cv.create_rectangle(X(r["x_min"]), Y(r["y_max"]), X(r["x_max"]), Y(r["y_min"]),
                                  fill=status.get(c, (FILL_IDLE,))[0], outline=EDGE, tags=("zone", c))
        cv.create_text(X(r["x_center"]), Y(r["y_center"]), text=c, font=("Microsoft JhengHei", 9),
                       tags=("zone", c))
        rect_of[c] = rid

    sel_var = tk.StringVar()
    out_btns = {}

    def refresh():
        for c, rid in rect_of.items():
            cv.itemconfigure(rid, fill=FILL_PICK if c in chosen else status.get(c, (FILL_IDLE,))[0])
        for z, b in out_btns.items():
            b.configure(relief="sunken" if z in chosen else "raised", bg=FILL_PICK if z in chosen else b.default_bg)
        sel_var.set("已選：" + ("、".join(chosen) if chosen else "（尚未選擇）"))

    def toggle(code):
        if code in chosen:
            chosen.remove(code)
        else:
            chosen.append(code)
        refresh()

    def on_click(ev):
        hit = cv.find_withtag("current")
        if not hit:
            return
        tags = [t for t in cv.gettags(hit[0]) if t not in ("zone", "current")]
        if tags:
            toggle(tags[0])

    tip = tk.Label(root, text="", fg="#555", font=("Microsoft JhengHei", 9))

    def on_move(ev):
        hit = cv.find_withtag("current")
        tags = [t for t in cv.gettags(hit[0]) if t not in ("zone", "current")] if hit else []
        tip.configure(text=f"{tags[0]}：{status.get(tags[0], ('', ''))[1]}" if tags else "")

    cv.tag_bind("zone", "<Button-1>", on_click)
    cv.bind("<Motion>", on_move)

    bar1 = tk.Frame(root)
    bar1.pack(padx=14, fill="x")
    for z in OUTSIDE_MAP:
        out_btns[z] = tk.Button(bar1, text=z, width=12, command=lambda z=z: toggle(z))
        out_btns[z].default_bg = out_btns[z].cget("bg")
        out_btns[z].pack(side="left")
    tk.Label(bar1, text="　").pack(side="left")
    for color, text in [(FILL_OPEN, "還有可挖量"), (FILL_FULL, "已達標"), (FILL_IDLE, "無開挖目標"), (FILL_PICK, "已選取")]:
        tk.Label(bar1, text="　　", bg=color, relief="solid", bd=1).pack(side="left", padx=(8, 3))
        tk.Label(bar1, text=text, font=("Microsoft JhengHei", 9)).pack(side="left")
    tip.pack(padx=14, anchor="w")
    tk.Label(root, textvariable=sel_var, font=("Microsoft JhengHei", 11, "bold"), fg="#c25e00",
             wraplength=W, justify="left").pack(padx=14, pady=(4, 2), anchor="w")

    def ok(_=None):
        # 跟網頁一樣的順序：地圖外選項在前，地圖分區依分區表順序（套用時照這個順序輪流分配）
        result["v"] = [z for z in OUTSIDE_MAP if z in chosen] + [c for c in codes if c in chosen]
        root.destroy()

    def clear():
        chosen.clear()
        refresh()

    bar2 = tk.Frame(root)
    bar2.pack(pady=(4, 6))
    tk.Button(bar2, text="確定", width=12, command=ok, font=("Microsoft JhengHei", 11)).pack(side="left", padx=6)
    tk.Button(bar2, text="清除", width=8, command=clear).pack(side="left", padx=6)
    tk.Button(bar2, text="跳過（不指派分區）", width=16, command=root.destroy).pack(side="left", padx=6)
    tk.Label(root, text=f"（{minutes} 分鐘沒回應會自動跳過，報表照樣產出）", fg="gray").pack(pady=(0, 10))
    root.bind("<Return>", ok)
    root.after(minutes * 60 * 1000, root.destroy)
    refresh()
    root.mainloop()
    return result["v"] or None
