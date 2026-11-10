"""📋 週會簡報 分頁：剩餘土石方出土紀錄（FDA／台北港兩張表）＋週會車次折線圖，
按「儲存」自動存到指定的雲端試算表（每週一個分頁，例如「土方出土紀錄1109」）。"""

import io
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data_store import filter_active_dispatch_logs, get_conn, is_demo_mode, load_global_prefs, load_sheet_data, save_global_pref
from stage_calc import _note_indicates_port_rejection

PREF_URL = "全域_週會試算表網址"
STAGE_AREA = {
    "開挖前土方": "開挖前土方",
    "第1階段 (第1挖)": "第一階開挖",
    "第2階段 (第2挖)": "第二階開挖",
    "第3階段 (第3挖)": "第三階開挖",
    "第4階段 (第4挖)": "第四階開挖",
    "第5階段 (油槽開挖)": "油槽開挖",
}
HEAD_BG = "#2D3E50"


# ----------------------------------------------------------------- 計算
def _prep_logs():
    df = load_sheet_data("dispatch_logs")
    if df is None or df.empty or "日期" not in df.columns:
        return pd.DataFrame(columns=["_d", "_trips", "_vol", "_fda"])
    df = filter_active_dispatch_logs(df.copy())
    df["_d"] = pd.to_datetime(df["日期"], errors="coerce").dt.date
    df = df[df["_d"].notna()]
    df["_trips"] = pd.to_numeric(df["車次數"], errors="coerce").fillna(1) if "車次數" in df.columns else 1
    df["_vol"] = pd.to_numeric(df["載運方量(m³)"], errors="coerce").fillna(0) if "載運方量(m³)" in df.columns else 0
    dest = df["運送目的地"].fillna("台北港").astype(str) if "運送目的地" in df.columns else pd.Series("台北港", index=df.index)
    df["_fda"] = dest.eq("FDA")
    return df


def _daily(df):
    """每天的車次、方量（只算有紀錄的日子）。"""
    if df.empty:
        return pd.DataFrame(columns=["_d", "trips", "vol"])
    g = df.groupby("_d").agg(trips=("_trips", "sum"), vol=("_vol", "sum")).reset_index()
    return g[g["trips"] > 0].sort_values("_d")


def _ratio(trips, vol):
    return round(vol / trips) if trips else None


def _fmt_ratio(r):
    return f"{r:g}m3/台" if r else ""


def _dstr(d):
    return d.strftime("%Y/%m/%d")


def _section_rows(daily, start, end):
    """
    「前期累積」依每台方量（m3/台）分段：方量一變（例如 FDA 7m3/台 → 13m3/台）就另起一列；
    接著「本週」一列、「累積」一列。回傳 list of dict。
    """
    prev = daily[daily["_d"] < start]
    week = daily[(daily["_d"] >= start) & (daily["_d"] <= end)]
    rows, segs = [], []
    for _, r in prev.iterrows():
        rt = _ratio(r["trips"], r["vol"])
        if segs and segs[-1]["ratio"] == rt:
            segs[-1]["trips"] += r["trips"]; segs[-1]["vol"] += r["vol"]
        else:
            segs.append({"first": r["_d"], "ratio": rt, "trips": r["trips"], "vol": r["vol"]})
    for i, sg in enumerate(segs):
        s = sg["first"] if i == 0 else segs[i - 1]["end"] + timedelta(days=1)
        e = (segs[i + 1]["first"] - timedelta(days=1)) if i + 1 < len(segs) else start - timedelta(days=1)
        sg["end"] = e
        rows.append({"項目": "前期累積", "統計日期": f"{_dstr(s)}~{e.strftime('%m/%d')}", "車次": int(sg["trips"]),
                     "數量 (m³)": float(sg["vol"]), "備註": _fmt_ratio(sg["ratio"])})
    wt, wv = int(week["trips"].sum()), float(week["vol"].sum())
    last_ratio = _ratio(wt, wv) or (segs[-1]["ratio"] if segs else None)
    rows.append({"項目": "本週", "統計日期": f"{_dstr(start)}~{end.strftime('%m/%d')}", "車次": wt,
                 "數量 (m³)": wv, "備註": _fmt_ratio(last_ratio)})
    rows.append({"項目": "累積", "統計日期": "", "車次": int(sum(r["車次"] for r in rows)),
                 "數量 (m³)": float(sum(r["數量 (m³)"] for r in rows)), "備註": ""})
    return rows


def _port_stop_days(start, end):
    """本週備註寫了台北港（暫停收容／不收土）的日子。"""
    notes = load_sheet_data("stage_daily_notes")
    if notes is None or notes.empty or "備註" not in notes.columns or "日期" not in notes.columns:
        return set(), {}
    n = notes.copy()
    n["_d"] = pd.to_datetime(n["日期"], errors="coerce").dt.date
    n = n[(n["_d"] >= start) & (n["_d"] <= end)]
    by_day = {}
    for _, r in n.iterrows():
        t = str(r["備註"]).strip() if pd.notna(r["備註"]) else ""
        if t and t != "nan":
            by_day.setdefault(r["_d"], [])
            if t not in by_day[r["_d"]]:
                by_day[r["_d"]].append(t)
    stop = {d for d, ts in by_day.items() if any(_note_indicates_port_rejection(t) for t in ts)}
    return stop, by_day


def build_report(start, end):
    df = _prep_logs()
    fda = _section_rows(_daily(df[df["_fda"]]), start, end)
    tp = _section_rows(_daily(df[~df["_fda"]]), start, end)
    stop, notes = _port_stop_days(start, end)
    if stop:
        tp[-2]["備註"] = f"台北港暫停收容作業{len(stop)}天"
    days = []
    d = start
    dd = df[(df["_d"] >= start) & (df["_d"] <= end)]
    while d <= end:
        x = dd[dd["_d"] == d]
        days.append({"日期": d, "台北港車次": int(x.loc[~x["_fda"], "_trips"].sum()),
                     "FDA車次": int(x.loc[x["_fda"], "_trips"].sum()), "備註": "；".join(notes.get(d, []))})
        d += timedelta(days=1)
    return {"fda": fda, "tp": tp, "daily": pd.DataFrame(days), "stop_days": len(stop)}


# ----------------------------------------------------------------- 匯出
def sheet_title(end):
    return f"「土方出土紀錄{end.strftime('%m%d')}」"


def _grid(rep, area):
    """把報表排成試算表的格子（list of rows），並回傳各區塊所在列號（0-based）。"""
    rows = [["土方工程"], ["剩餘土石方出土紀錄"], [f"施作區域:{area}"]]
    pos = {}
    for key, title in (("fda", "土方平衡-FDA"), ("tp", "土方交換-臺北港")):
        pos[key + "_head"] = len(rows)
        rows.append([title, "統計日期", "車次", "數量 (m³)", "備註"])
        pos[key + "_first"] = len(rows)
        for r in rep[key]:
            rows.append([r["項目"], r["統計日期"], r["車次"], r["數量 (m³)"], r["備註"]])
        pos[key + "_last"] = len(rows) - 1
        rows += [[], []]
    return rows, pos


def build_xlsx(rep, area, end):
    from openpyxl import Workbook
    from openpyxl.chart import LineChart, Reference
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title(end)[:31]
    rows, pos = _grid(rep, area)
    for r in rows:
        ws.append(r)
    ws["A1"].font = Font(size=16, bold=True)
    ws["A2"].font = Font(size=12, bold=True, color="8A8A8A")
    ws["A3"].font = Font(size=11)
    head_fill, sum_fill = PatternFill("solid", fgColor=HEAD_BG[1:]), PatternFill("solid", fgColor="E8EAED")
    thin, dbl = Side(style="thin", color="BFBFBF"), Side(style="double", color="2D3E50")
    for key in ("fda", "tp"):
        h = pos[key + "_head"] + 1
        for c in range(1, 6):
            cell = ws.cell(h, c)
            cell.fill, cell.font = head_fill, Font(bold=True, color="FFFFFF", size=12)
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for r in range(pos[key + "_first"] + 1, pos[key + "_last"] + 2):
            for c in range(1, 6):
                cell = ws.cell(r, c)
                cell.border = Border(bottom=thin)
                if c in (1, 2):
                    cell.alignment = Alignment(horizontal="center")
                if c in (3, 4):
                    cell.number_format = "#,##0"
            if "暫停" in str(ws.cell(r, 5).value or ""):
                ws.cell(r, 5).font = Font(color="FF0000")
        last = pos[key + "_last"] + 1
        for c in range(1, 6):
            cell = ws.cell(last, c)
            cell.fill, cell.font, cell.border = sum_fill, Font(bold=True), Border(bottom=dbl)
    for col, w in zip("ABCDE", (16, 22, 12, 12, 24)):
        ws.column_dimensions[col].width = w

    # 每日車次表＋折線圖（放在右邊 G 欄起）
    daily = rep["daily"]
    ws["G4"], ws["H4"], ws["I4"], ws["J4"] = "日期", "台北港車次", "FDA車次", "備註"
    for c in "GHIJ":
        ws[f"{c}4"].fill, ws[f"{c}4"].font = head_fill, Font(bold=True, color="FFFFFF")
        ws[f"{c}4"].alignment = Alignment(horizontal="center")
    for i, r in enumerate(daily.itertuples(index=False)):
        ws.cell(5 + i, 7, f"{r.日期.month}/{r.日期.day}").alignment = Alignment(horizontal="center")
        ws.cell(5 + i, 8, r.台北港車次)
        ws.cell(5 + i, 9, r.FDA車次)
        ws.cell(5 + i, 10, r.備註)
    for col, w in zip("GHIJ", (10, 12, 10, 30)):
        ws.column_dimensions[col].width = w
    if len(daily):
        ch = LineChart()
        ch.title, ch.y_axis.title, ch.x_axis.title = "本週每日車次", "車次 (台)", "日期"
        ch.height, ch.width = 7.5, 16
        data = Reference(ws, min_col=8, max_col=9, min_row=4, max_row=4 + len(daily))
        ch.add_data(data, titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=7, min_row=5, max_row=4 + len(daily)))
        ws.add_chart(ch, f"G{6 + len(daily)}")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _rgb(hexs):
    h = hexs.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


def save_to_cloud(url, rep, area, end):
    """在指定的雲端試算表新增（或覆蓋）本週分頁，排版跟範本一樣，右邊附每日車次表與折線圖。"""
    client = get_conn().client._client
    sh = client.open_by_url(url)
    title = sheet_title(end)
    try:
        sh.del_worksheet(sh.worksheet(title))          # 同一週重新產生就整頁覆蓋
    except Exception:
        pass
    ws = sh.add_worksheet(title=title, rows=60, cols=12, index=0)
    rows, pos = _grid(rep, area)
    daily = rep["daily"]
    grid = [list(r) + [""] * (5 - len(r)) for r in rows]
    ws.update("A1", grid, value_input_option="USER_ENTERED")
    dvals = [["日期", "台北港車次", "FDA車次", "備註"]] + [
        [f"{r.日期.month}/{r.日期.day}", int(r.台北港車次), int(r.FDA車次), r.備註] for r in daily.itertuples(index=False)]
    ws.update("G4", dvals, value_input_option="USER_ENTERED")

    sid = ws.id
    rng = lambda r0, r1, c0, c1: {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}
    req = []
    fmt = lambda r, f, fields: req.append({"repeatCell": {"range": r, "cell": {"userEnteredFormat": f}, "fields": "userEnteredFormat(" + fields + ")"}})
    fmt(rng(0, 1, 0, 1), {"textFormat": {"bold": True, "fontSize": 16}}, "textFormat")
    fmt(rng(1, 2, 0, 1), {"textFormat": {"bold": True, "fontSize": 12, "foregroundColor": _rgb("#8A8A8A")}}, "textFormat")
    head = {"backgroundColor": _rgb(HEAD_BG), "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            "textFormat": {"bold": True, "fontSize": 12, "foregroundColor": _rgb("#FFFFFF")}}
    for key in ("fda", "tp"):
        h, f, l = pos[key + "_head"], pos[key + "_first"], pos[key + "_last"]
        fmt(rng(h, h + 1, 0, 5), head, "backgroundColor,horizontalAlignment,verticalAlignment,textFormat")
        fmt(rng(f, l + 1, 0, 2), {"horizontalAlignment": "CENTER"}, "horizontalAlignment")
        fmt(rng(f, l + 1, 2, 4), {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}, "numberFormat")
        fmt(rng(l, l + 1, 0, 5), {"backgroundColor": _rgb("#E8EAED"), "textFormat": {"bold": True}}, "backgroundColor,textFormat")
        req.append({"updateBorders": {"range": rng(f, l + 1, 0, 5), "innerHorizontal": {"style": "SOLID", "color": _rgb("#BFBFBF")},
                                      "bottom": {"style": "DOUBLE", "color": _rgb(HEAD_BG)}}})
        for r in range(f, l + 1):
            if "暫停" in str(grid[r][4]):
                fmt(rng(r, r + 1, 4, 5), {"textFormat": {"foregroundColor": _rgb("#FF0000")}}, "textFormat")
    fmt(rng(3, 4, 6, 10), head, "backgroundColor,horizontalAlignment,verticalAlignment,textFormat")
    fmt(rng(4, 4 + len(daily), 6, 7), {"horizontalAlignment": "CENTER"}, "horizontalAlignment")
    for i, px in enumerate((120, 165, 105, 105, 175, 30, 70, 90, 80, 220)):
        req.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                                                  "properties": {"pixelSize": px}, "fields": "pixelSize"}})
    if len(daily):
        n = len(daily)
        req.append({"addChart": {"chart": {
            "spec": {"title": "本週每日車次", "basicChart": {
                "chartType": "LINE", "legendPosition": "BOTTOM_LEGEND", "headerCount": 1,
                "axis": [{"position": "BOTTOM_AXIS", "title": "日期"}, {"position": "LEFT_AXIS", "title": "車次 (台)"}],
                "domains": [{"domain": {"sourceRange": {"sources": [rng(3, 4 + n, 6, 7)]}}}],
                "series": [{"series": {"sourceRange": {"sources": [rng(3, 4 + n, 7, 8)]}}, "targetAxis": "LEFT_AXIS",
                            "dataLabel": {"type": "DATA", "placement": "ABOVE"}},
                           {"series": {"sourceRange": {"sources": [rng(3, 4 + n, 8, 9)]}}, "targetAxis": "LEFT_AXIS"}]}},
            "position": {"overlayPosition": {"anchorCell": {"sheetId": sid, "rowIndex": 6 + n, "columnIndex": 6},
                                             "widthPixels": 560, "heightPixels": 300}}}}})
    sh.batch_update({"requests": req})
    return title


# ----------------------------------------------------------------- 畫面
def _default_week(today):
    """週會統計到最近一個「已過完的星期一」：例如 11/10（二）開會 → 11/03（二）～ 11/09（一）。"""
    end = today - timedelta(days=today.weekday() or 7)
    return end - timedelta(days=6), end


def _show_table(rows, title):
    df = pd.DataFrame(rows).rename(columns={"項目": title})
    sty = (df.style.format({"車次": "{:,.0f}", "數量 (m³)": "{:,.0f}"})
           .set_table_styles([{"selector": "th", "props": f"background:{HEAD_BG};color:#fff;font-weight:700;text-align:center"}])
           .apply(lambda r: ["font-weight:700;background:#E8EAED" if r.iloc[0] == "累積" else "" for _ in r], axis=1)
           .apply(lambda r: ["color:#FF0000" if (c == "備註" and "暫停" in str(v)) else "" for c, v in r.items()], axis=1))
    st.dataframe(sty, use_container_width=True, hide_index=True)


def render(df_results, global_stage_choice):
    st.write("### 📋 週會簡報：剩餘土石方出土紀錄")
    st.caption("數字全部由雲端派車紀錄自動算出：FDA／台北港分開統計，「前期累積」會依每台方量（例如 7m3/台、13m3/台）自動分段；"
               "本週有「台北港」相關備註的日子會自動算成「台北港暫停收容作業N天」。按「儲存」會把這一週存到下面指定的雲端試算表，每週一個分頁；畫面可直接截圖用於週會。")

    s0, e0 = _default_week(date.today())
    c1, c2, c3 = st.columns([1, 1, 1.4])
    with c1:
        start = st.date_input("本週開始", value=s0, key="wk_start")
    with c2:
        end = st.date_input("本週結束", value=e0, key="wk_end")
    with c3:
        area = st.text_input("施作區域", value=STAGE_AREA.get(global_stage_choice, global_stage_choice), key="wk_area")
    if start > end:
        st.error("開始日期不能晚於結束日期。")
        return

    rep = build_report(start, end)
    st.markdown(f"**土方工程**　｜　剩餘土石方出土紀錄　｜　施作區域：{area}")
    _show_table(rep["fda"], "土方平衡-FDA")
    _show_table(rep["tp"], "土方交換-臺北港")

    st.markdown("#### 📈 週會車次折線圖")
    daily = rep["daily"].copy()
    daily["_x"] = pd.to_datetime(daily["日期"])
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=daily["_x"], y=daily["台北港車次"], mode="lines+markers+text", name="台北港車次",
                             text=daily["台北港車次"].astype(str), textposition="top center", line=dict(color="#3498DB", width=2)))
    if daily["FDA車次"].sum() > 0:
        fig.add_trace(go.Scatter(x=daily["_x"], y=daily["FDA車次"], mode="lines+markers", name="FDA車次", line=dict(color="#E67E22", width=2, dash="dot")))
    fig.update_layout(title=f"{start:%Y/%m/%d} ~ {end:%m/%d} 每日車次", height=420, margin=dict(l=20, r=20, t=50, b=50),
                      xaxis=dict(title="日期", tickmode="array", tickvals=daily["_x"], ticktext=[f"{d.month}/{d.day}" for d in daily["日期"]]),
                      yaxis_title="車次 (台)", legend=dict(orientation="h", y=-0.2))
    st.plotly_chart(fig, use_container_width=True, key="wk_chart")
    st.dataframe(rep["daily"].assign(日期=[f"{d.month}/{d.day}" for d in rep["daily"]["日期"]]), use_container_width=True, hide_index=True)

    st.markdown("#### 💾 儲存到雲端試算表")
    prefs = load_global_prefs()
    url = st.text_input("週會資料雲端試算表網址（每週會在這份試算表新增一個分頁）", value=str(prefs.get(PREF_URL, "") or ""), key="wk_url",
                        placeholder="https://docs.google.com/spreadsheets/d/…")
    if url and url != str(prefs.get(PREF_URL, "") or "") and st.button("記住這個網址", key="wk_url_save"):
        save_global_pref(PREF_URL, url)
        st.success("已記住，之後打開會自動帶入。")
    try:
        sa = st.secrets["connections"]["gsheets"]["client_email"]
        st.caption(f"⚠️ 第一次使用請先把這份試算表「共用」給系統帳號並設為編輯者：`{sa}`")
    except Exception:
        pass

    title = sheet_title(end)
    if st.button(f"💾 儲存到雲端試算表（分頁 {title}）", use_container_width=True, type="primary", key="wk_save"):
        if not url:
            st.session_state["wk_msg"] = ("warning", "請先填雲端試算表網址。")
        elif is_demo_mode():
            st.session_state["wk_msg"] = ("warning", "訪客模式不會寫入雲端。")
        else:
            try:
                with st.spinner("儲存中…"):
                    t = save_to_cloud(url, rep, area, end)
                if url != str(prefs.get(PREF_URL, "") or ""):
                    save_global_pref(PREF_URL, url)
                st.session_state["wk_msg"] = ("success", f"已存到雲端試算表的分頁 {t}（同一週重存會整頁覆蓋）。")
            except Exception as e:
                st.session_state["wk_msg"] = ("error", f"存到雲端失敗：{e}。請確認網址正確、試算表已共用給系統帳號（編輯者）。")
    msg = st.session_state.pop("wk_msg", None)
    if msg:
        getattr(st, msg[0])(msg[1])
