"""🔬 聯單進階分析 分頁：由雲端 dispatch_logs 直接分析班別、車輛趟次與運載時間、週度趨勢、月度結算。

原本是獨立的 HTML 儀表板（要手動上傳官方CSV），現在改成直接讀雲端 dispatch_logs，不需要上傳任何檔案。
對照原本欄位：dispatch_logs 沒有「司機姓名／實際進土量／土質／土資場名稱／補登旗標」，因此做了以下對應——
  進土量 → 移除；土資場 → 運送目的地(台北港/FDA)與出土分區。
  司機、土質、出場/進場補登：來自官方CSV，由「官方聯單對帳」分頁（或其中的「補充官方欄位」）依聯單序號寫進 dispatch_logs；
  沒有官方土質時，聯單類型改由聯單序號判斷。
  補登異常 → 只看官方的「出場補登／進場補登」旗標，加上已作廢的紀錄。備註裡的「官方聯單補登」只是系統自動補資料的註記，不算補登。
車次/趟次一律只算台北港（FDA不併入車次，跟全站規則一致）；已作廢的紀錄不計入車次與出土量，只列入異常分析。
"""
import html as _html

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from data_store import _is_voided, load_sheet_data

SHIFTS = [("早班 (05-08)", 5, 8), ("午班 (08-12)", 8, 12), ("晚班 (12-18)", 12, 18)]
SHIFT_COLORS = ["#3498db", "#f1c40f", "#2c3e50", "#95a5a6"]


TRANSIT_MAX_MIN = 720   # 單趟運送時間超過 12 小時視為時間異常（例如隔天才進場）


def _shift_of(h):
    if pd.isna(h):
        return "其他時段"
    for name, a, b in SHIFTS:
        if a <= h < b:
            return name
    return "其他時段"


def _serial_type(serial, types):
    s = "" if pd.isna(serial) else str(serial).strip().upper()
    if s in ("", "NAN", "NONE"):
        return "無聯單序號"
    for t in types:
        if str(t).upper() in s:
            return str(t)
    return "其他"


def prepare_tickets(df_logs, manifest_types):
    """把 dispatch_logs 整理成分析用表格（含已作廢列，作廢以 is_void 標記）。"""
    if df_logs is None or df_logs.empty or "日期" not in df_logs.columns:
        return pd.DataFrame()
    d = df_logs.copy()
    for c, default in [("時間", ""), ("車頭車號", ""), ("出土分區", ""), ("載運方量(m³)", 0), ("備註", ""),
                       ("聯單序號", ""), ("作廢", False), ("作廢原因", ""), ("運送目的地", "台北港"), ("車次數", 1)]:
        if c not in d.columns:
            d[c] = default
    d["日期"] = d["日期"].astype(str).str.strip().str[:10]
    d = d[d["日期"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)].copy()
    if d.empty:
        return pd.DataFrame()
    d["dest"] = d["運送目的地"].fillna("台北港").astype(str).replace({"": "台北港", "nan": "台北港"})
    d["is_void"] = d["作廢"].apply(_is_voided)
    d["void_reason"] = d["作廢原因"].fillna("").astype(str)
    d["note"] = d["備註"].fillna("").astype(str)
    # 補登一律以官方CSV的旗標為準（出場補登／進場補登），不再用備註文字判斷
    _out = pd.to_numeric(d["出場補登"], errors="coerce").fillna(0) if "出場補登" in d.columns else pd.Series(0, index=d.index)
    _in = pd.to_numeric(d["進場補登"], errors="coerce").fillna(0) if "進場補登" in d.columns else pd.Series(0, index=d.index)
    d["is_backfill"] = (_out == 1) | (_in == 1)
    _ro = d["出場補登原因"].fillna("").astype(str).str.strip() if "出場補登原因" in d.columns else pd.Series("", index=d.index)
    _ri = d["進場補登原因"].fillna("").astype(str).str.strip() if "進場補登原因" in d.columns else pd.Series("", index=d.index)
    d["backfill_label"] = ""
    d.loc[_in == 1, "backfill_label"] = "進場補登：" + _ri[_in == 1].replace("", "（未填原因）")
    d.loc[_out == 1, "backfill_label"] = "出場補登：" + _ro[_out == 1].replace("", "（未填原因）")
    d["driver"] = d["司機姓名"].fillna("").astype(str).str.strip().replace({"nan": ""}) if "司機姓名" in d.columns else ""
    _soil = d["土質"].fillna("").astype(str).str.strip().replace({"nan": ""}) if "土質" in d.columns else pd.Series("", index=d.index)
    d["vol"] = pd.to_numeric(d["載運方量(m³)"], errors="coerce").fillna(0.0)
    d["trips"] = pd.to_numeric(d["車次數"], errors="coerce").fillna(1).clip(lower=1)
    d["truck"] = d["車頭車號"].fillna("").astype(str).str.strip()
    d["zone"] = d["出土分區"].fillna("").astype(str).replace({"": "未指定", "nan": "未指定"})
    d["ticket_type"] = d["聯單序號"].apply(lambda v: _serial_type(v, manifest_types))
    d.loc[_soil != "", "ticket_type"] = _soil[_soil != ""]
    t = d["時間"].fillna("").astype(str).str.strip()
    d["dt"] = pd.to_datetime(d["日期"] + " " + t, errors="coerce")
    # 進場時間（官方CSV的進場日期，土車抵達台北港）→ 單趟運送時間＝進場時間 − 出場時間（分鐘）
    if "進場時間" in d.columns:
        _arr = d["進場時間"].fillna("").astype(str).str.strip()
        d["arr"] = pd.to_datetime(_arr.where(_arr != ""), errors="coerce")
    else:
        d["arr"] = pd.NaT
    _mins = (d["arr"] - d["dt"]).dt.total_seconds() / 60
    d["transit_raw"] = _mins
    d["transit_state"] = "ok"
    d.loc[d["arr"].isna(), "transit_state"] = "缺進場時間"
    d.loc[d["arr"].notna() & ((_mins <= 0) | (_mins > TRANSIT_MAX_MIN)), "transit_state"] = "時間異常"
    d.loc[(d["transit_state"] == "ok") & d["is_backfill"], "transit_state"] = "官方補登"
    d["transit"] = _mins.where(d["transit_state"] == "ok")
    d["hour"] = d["dt"].dt.hour
    d["shift"] = d["hour"].apply(_shift_of)
    dd = pd.to_datetime(d["日期"])
    iso = dd.dt.isocalendar()
    d["week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    d["month"] = d["日期"].str[:7]
    d = d.sort_values(["dt", "日期"], na_position="last").reset_index(drop=True)
    # 單趟完成時間：同一台車同一天，相鄰兩趟的出場時間差（分鐘）；跟原本HTML的算法一樣
    d["cycle"] = float("nan")
    ok = (d["dest"] == "台北港") & (~d["is_void"]) & d["dt"].notna() & (d["truck"] != "")
    sub = d[ok]
    diff = sub.groupby(["truck", "日期"])["dt"].diff().dt.total_seconds() / 60
    d.loc[sub.index, "cycle"] = diff
    return d


def _show_table(df, height=380):
    """用深色表頭、白色粗體字的HTML表格顯示（st.dataframe 的表頭樣式無法自訂，這裡改用HTML讓表頭一眼看得到）。"""
    if df is None or df.empty:
        st.caption("（沒有資料）")
        return
    head = "".join(f"<th>{_html.escape(str(c))}</th>" for c in df.columns)
    body = []
    for _, r in df.iterrows():
        cells = []
        for v in r.values:
            if isinstance(v, float):
                txt = "-" if pd.isna(v) else (f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}")
            else:
                txt = "-" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v)
            cells.append(f"<td>{_html.escape(txt)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    st.markdown(
        """<style>
.ana-wrap{overflow:auto;border:1px solid #d5dbe1;border-radius:6px;}
.ana-wrap table{border-collapse:collapse;width:100%;font-size:0.92rem;}
.ana-wrap th{position:sticky;top:0;background:#1f3a5f;color:#fff;font-weight:700;font-size:0.95rem;padding:10px 8px;text-align:center;border:1px solid #16293f;white-space:nowrap;}
.ana-wrap td{padding:7px 8px;text-align:center;border:1px solid #e3e7eb;color:inherit;}
.ana-wrap tbody tr:nth-child(even){background:rgba(31,58,95,0.06);}
.ana-wrap tbody tr:hover{background:rgba(243,156,18,0.18);}
</style>"""
        f'<div class="ana-wrap" style="max-height:{height}px"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>',
        unsafe_allow_html=True,
    )


def _driver_of(g):
    ds = g.loc[g["driver"] != "", "driver"] if "driver" in g.columns else []
    return ds.mode().iloc[0] if len(ds) else ""


def _fmt_min(x):
    return "-" if pd.isna(x) else f"{x:.1f} 分"


def _fmt_dur(x):
    """分鐘 → 「2 時 49 分」；不到 1 小時就只顯示分鐘。"""
    if pd.isna(x):
        return "-"
    m = int(round(x))
    return f"{m // 60} 時 {m % 60} 分" if m >= 60 else f"{m} 分"


def _transit_section(rng, key):
    """單趟運送時間（出場時間 → 進場時間）：平均、中位數、最短、最長、逐日趨勢、時段與車輛比較。"""
    st.markdown("##### 單趟運送時間（出場時間 → 進場時間）")
    n_all = len(rng)
    ok = rng[rng["transit"].notna()]
    if "進場時間" not in rng.columns or rng["arr"].isna().all():
        st.info("📎 雲端 dispatch_logs 還沒有「進場時間」。請到「🧾 官方聯單對帳」分頁最上面的「補充官方欄位」上傳官方CSV一次，"
                "系統會依聯單序號把官方的進場日期寫進雲端新欄位「進場時間」；之後每次對帳也會自動帶入。")
        return
    c = st.columns(5)
    c[0].metric("平均單趟運送時間", _fmt_dur(ok["transit"].mean()), help="進場時間（抵達台北港）− 出場時間（離開工地），單位分鐘。")
    c[1].metric("中位數", _fmt_dur(ok["transit"].median()))
    c[2].metric("最短", _fmt_dur(ok["transit"].min()))
    c[3].metric("最長", _fmt_dur(ok["transit"].max()))
    c[4].metric("有效趟次", f"{len(ok):,} / {n_all:,}")
    st_cnt = rng["transit_state"].value_counts()
    skip = [f"{k} {int(st_cnt[k])} 筆" for k in ["缺進場時間", "官方補登", "時間異常"] if k in st_cnt.index]
    st.caption("單趟運送時間＝進場時間 − 出場時間。不納入平均的紀錄：" + ("、".join(skip) if skip else "無")
               + f"（官方補登是人工補打的時間、時間異常是進場早於出場或超過 {TRANSIT_MAX_MIN // 60} 小時，都會讓平均失真）。")
    if ok.empty:
        return
    a, b = st.columns(2)
    with a:
        dm = ok.groupby("日期")["transit"].mean().reset_index()
        fig = go.Figure(go.Scatter(x=dm["日期"], y=dm["transit"], mode="lines+markers", line=dict(color="#8e44ad")))
        fig.update_layout(title="每日平均單趟運送時間（分）", height=290, margin=dict(l=10, r=10, t=40, b=10), xaxis=dict(type="category"))
        st.plotly_chart(fig, use_container_width=True, key=f"{key}_daily")
    with b:
        hm = ok.dropna(subset=["hour"]).groupby("hour")["transit"].mean().reset_index()
        fig = go.Figure(go.Bar(x=hm["hour"].astype(int).astype(str) + " 時", y=hm["transit"], marker_color="#16a085"))
        fig.update_layout(title="各出場時段平均運送時間（分）", height=290, margin=dict(l=10, r=10, t=40, b=10), xaxis=dict(type="category"))
        st.plotly_chart(fig, use_container_width=True, key=f"{key}_hour")
    rows = []
    for truck, g in ok[ok["truck"] != ""].groupby("truck"):
        rows.append({"車號": truck, "司機": _driver_of(g), "有效趟次": len(g), "平均": g["transit"].mean(),
                     "最短": g["transit"].min(), "最長": g["transit"].max()})
    if rows:
        tdf = pd.DataFrame(rows).sort_values("平均").reset_index(drop=True)
        for col in ["平均", "最短", "最長"]:
            tdf[col] = tdf[col].apply(_fmt_dur)
        if (tdf["司機"] == "").all():
            tdf = tdf.drop(columns=["司機"])
        tdf = tdf.rename(columns={"平均": "平均單趟運送", "最短": "最短", "最長": "最長"})
        _show_table(tdf, 360)


def _range_picker(label, dates, key):
    lo, hi = min(dates), max(dates)
    v = st.date_input(label, value=(lo, hi), min_value=lo, max_value=hi, key=key)
    if isinstance(v, (tuple, list)):
        if len(v) == 2:
            return v[0], v[1]
        return (v[0], v[0]) if len(v) == 1 else (lo, hi)
    return v, v


def _view_daily(port):
    total_trips = int(port["trips"].sum())
    days = port.groupby("日期")["trips"].sum().sort_index()
    all_dates = sorted(pd.to_datetime(port["日期"]).dt.date.unique())
    # 班別圖表與車輛分析共用同一個區間
    s, e = _range_picker("選取分析區間（班別圖表與車輛分析共用）", all_dates, "ana_daily_range")
    s_str, e_str = s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d")
    c1, c2 = st.columns([1, 2])
    with c1:
        st.metric("累計出土總車次", f"{total_trips:,}")
        if len(days) >= 1:
            last_d, last_n = days.index[-1], int(days.iloc[-1])
            if len(days) >= 2:
                prev_d, prev_n = days.index[-2], int(days.iloc[-2])
                st.metric(f"最新進度比對（{last_d}）", f"{last_n} 車次", delta=f"{last_n - prev_n:+d}（對比 {prev_d} 的 {prev_n} 車次）")
            else:
                st.metric(f"最新進度（{last_d}）", f"{last_n} 車次")
    with c2:
        st.markdown("##### 出車班別區間分析")
        rng = port[(port["日期"] >= s_str) & (port["日期"] <= e_str)]
        shift_names = [n for n, _, _ in SHIFTS] + ["其他時段"]
        by = rng.groupby(["日期", "shift"])["trips"].sum().unstack(fill_value=0).reindex(columns=shift_names, fill_value=0).sort_index()
        p1, p2 = st.columns(2)
        with p1:
            tot = by.sum()
            fig = go.Figure(go.Pie(labels=shift_names, values=[tot[n] for n in shift_names], hole=0.5,
                                   marker=dict(colors=SHIFT_COLORS), sort=False))
            fig.update_layout(title="區間班別比例", height=300, margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(fig, use_container_width=True, key="ana_pie")
        with p2:
            fig = go.Figure()
            for n, col in zip(shift_names, SHIFT_COLORS):
                fig.add_bar(x=by.index, y=by[n], name=n, marker_color=col)
            fig.update_layout(barmode="stack", title="每日出車趨勢", height=300, margin=dict(l=10, r=10, t=40, b=10),
                              xaxis=dict(type="category"))
            st.plotly_chart(fig, use_container_width=True, key="ana_bar")

    st.divider()
    st.markdown("##### 區間車輛趟次比較與運載時間分析")
    st.caption(f"分析區間：{s_str} ～ {e_str}（跟上方班別圖表同一個區間）")
    tr = port[(port["日期"] >= s_str) & (port["日期"] <= e_str) & (port["truck"] != "")]
    if tr.empty:
        st.info("此區間無資料")
    else:
        rows = []
        for truck, g in tr.groupby("truck"):
            r = {"車號": truck, "司機": _driver_of(g), "區間總趟次": int(g["trips"].sum()), "平均完成時間(單趟)": g["cycle"].mean(),
                 "平均運送時間(出場→進場)": g["transit"].mean()}
            for n, _, _ in SHIFTS:
                r[n.split(" ")[0] + "平均"] = g.loc[g["shift"] == n, "cycle"].mean()
            rows.append(r)
        tdf = pd.DataFrame(rows).sort_values("區間總趟次", ascending=False).reset_index(drop=True)
        top = tdf.head(15)
        fig = go.Figure(go.Bar(x=top["車號"], y=top["區間總趟次"], marker_color="#e67e22"))
        fig.update_layout(title="區間車輛趟次排行 (前15名)", height=280, margin=dict(l=10, r=10, t=40, b=10), xaxis=dict(type="category"))
        st.plotly_chart(fig, use_container_width=True, key="ana_truck_rank")
        show = tdf.copy()
        show.insert(0, "排名", range(1, len(show) + 1))
        for c in ["平均完成時間(單趟)", "早班平均", "午班平均", "晚班平均"]:
            show[c] = show[c].apply(_fmt_min)
        show["平均運送時間(出場→進場)"] = show["平均運送時間(出場→進場)"].apply(_fmt_dur)
        if "進場時間" not in tr.columns or tr["arr"].isna().all():
            show = show.drop(columns=["平均運送時間(出場→進場)"])
        if (show["司機"] == "").all():
            show = show.drop(columns=["司機"])
        _show_table(show, 420)
        st.caption("平均完成時間＝同一台車同一天相鄰兩趟的出場時間差（含回工地裝土的整個循環）；每台車每天第一趟沒有前一趟可比，不納入平均。"
                   "平均運送時間＝進場時間 − 出場時間（單程，工地到台北港）。")

    st.divider()
    _transit_section(port[(port["日期"] >= s_str) & (port["日期"] <= e_str)], "ana_transit")

    st.divider()
    st.markdown("##### 每日歷史總表")
    hist = port.groupby(["日期", "shift"])["trips"].sum().unstack(fill_value=0).reindex(columns=shift_names, fill_value=0)
    hist["單日總計"] = hist.sum(axis=1)
    hist = hist.sort_index(ascending=False).reset_index()
    _show_table(hist, 420)


def _abnormal_table(alltp):
    reasons = {}
    for _, r in alltp.iterrows():
        if r["is_void"]:
            k = "作廢：" + (r["void_reason"].strip() or "（未填原因）")
            reasons[k] = reasons.get(k, 0) + 1
        elif r["is_backfill"]:
            reasons[r["backfill_label"]] = reasons.get(r["backfill_label"], 0) + 1
    return reasons


def _view_weekly(port, alltp):
    weeks = sorted(port["week"].unique())
    c1, c2 = st.columns(2)
    ws = c1.selectbox("起始週次", weeks, index=0, key="ana_w_start")
    we = c2.selectbox("結束週次", weeks, index=len(weeks) - 1, key="ana_w_end")
    if ws > we:
        st.warning("起始週次不能晚於結束週次。")
        return
    w = port[(port["week"] >= ws) & (port["week"] <= we)]
    wa = alltp[(alltp["week"] >= ws) & (alltp["week"] <= we)]
    trend = w.groupby("week").agg(vol=("vol", "sum"), trips=("trips", "sum")).reset_index()
    fig = go.Figure()
    fig.add_bar(x=trend["week"], y=trend["vol"], name="實際出土量(m³)", marker_color="#3498db", yaxis="y")
    fig.add_trace(go.Scatter(x=trend["week"], y=trend["trips"], name="總趟次", mode="lines+markers", line=dict(color="#e74c3c"), yaxis="y2"))
    fig.update_layout(title="週度出土量與趟次趨勢比較", height=340, margin=dict(l=10, r=10, t=40, b=10),
                      yaxis=dict(title="出土量 (m³)"), yaxis2=dict(title="趟次", overlaying="y", side="right", showgrid=False),
                      xaxis=dict(type="category"))
    st.plotly_chart(fig, use_container_width=True, key="ana_weekly_trend")

    abn = int((wa["is_void"] | wa["is_backfill"]).sum())
    m1, m2, m2b, m3 = st.columns(4)
    m1.metric("區間實際出土量 (台北港)", f"{w['vol'].sum():,.1f} m³")
    m2.metric("區間平均完成時間", _fmt_min(w["cycle"].mean()), help="同一台車同一天相鄰兩趟的出場時間差。")
    m2b.metric("區間平均單趟運送時間", _fmt_dur(w["transit"].mean()), help="進場時間 − 出場時間（單程）。需要先在「補充官方欄位」補進場時間。")
    m3.metric("區間異常與補登率", f"{(abn / len(wa) * 100 if len(wa) else 0):.1f}%", help="（已作廢 + 官方出場/進場補登的紀錄）÷ 區間全部台北港紀錄。補登只看官方CSV的旗標。")

    l, r = st.columns(2)
    with l:
        st.markdown("##### 車隊運能排行（區間累計）")
        wt = w[w["truck"] != ""]
        rk = pd.DataFrame([{"車號": t, "司機": _driver_of(g), "趟次": int(g["trips"].sum())} for t, g in wt.groupby("truck")])
        if not rk.empty:
            rk = rk.sort_values("趟次", ascending=False).reset_index(drop=True)
            rk.insert(0, "排名", range(1, len(rk) + 1))
            if (rk["司機"] == "").all():
                rk = rk.drop(columns=["司機"])
        _show_table(rk, 320)
    with r:
        st.markdown("##### 補登／作廢原因分析（區間累計）")
        reasons = _abnormal_table(wa)
        if reasons:
            rdf = pd.DataFrame([{"原因": k, "發生次數": v, "佔比": f"{v / sum(reasons.values()) * 100:.1f}%"} for k, v in sorted(reasons.items(), key=lambda x: -x[1])])
            _show_table(rdf, 320)
        else:
            st.caption("此區間無補登或作廢紀錄。")


def _view_monthly(port, alltp, fda):
    months = sorted(port["month"].unique())
    c1, c2 = st.columns(2)
    ms = c1.selectbox("起始月份", months, index=0, key="ana_m_start")
    me = c2.selectbox("結束月份", months, index=len(months) - 1, key="ana_m_end")
    if ms > me:
        st.warning("起始月份不能晚於結束月份。")
        return
    m = port[(port["month"] >= ms) & (port["month"] <= me)]
    ma = alltp[(alltp["month"] >= ms) & (alltp["month"] <= me)]
    mf = fda[(fda["month"] >= ms) & (fda["month"] <= me)] if not fda.empty else fda
    tm = ma.groupby("month").apply(lambda g: pd.Series({"abn": (g["is_void"] | g["is_backfill"]).sum(), "n": len(g)}), include_groups=False) if len(ma) else pd.DataFrame()
    vol_m = m.groupby("month")["vol"].sum()
    months_x = sorted(set(vol_m.index) | set(tm.index if len(tm) else []))
    fig = go.Figure()
    fig.add_bar(x=months_x, y=[vol_m.get(x, 0) for x in months_x], name="實際出土量(m³)", marker_color="#2980b9", yaxis="y")
    fig.add_trace(go.Scatter(x=months_x, y=[(tm.loc[x, "abn"] / tm.loc[x, "n"] * 100) if x in tm.index else 0 for x in months_x],
                             name="異常補登率(%)", mode="lines+markers", line=dict(color="#e74c3c"), yaxis="y2"))
    fig.update_layout(title="月度出土量與異常率趨勢比較", height=340, margin=dict(l=10, r=10, t=40, b=10),
                      yaxis=dict(title="出土量 (m³)"), yaxis2=dict(title="異常率 (%)", overlaying="y", side="right", showgrid=False),
                      xaxis=dict(type="category"))
    st.plotly_chart(fig, use_container_width=True, key="ana_monthly_trend")

    abn = int((ma["is_void"] | ma["is_backfill"]).sum())
    a, b, c, c2, d, e = st.columns(6)
    a.metric("區間總車次 (台北港)", f"{int(m['trips'].sum()):,}")
    b.metric("區間出土量 (台北港)", f"{m['vol'].sum():,.1f} m³")
    c.metric("區間平均完成時間", _fmt_min(m["cycle"].mean()))
    c2.metric("區間平均單趟運送時間", _fmt_dur(m["transit"].mean()), help="進場時間 − 出場時間（單程）。")
    d.metric("區間總異常率", f"{(abn / len(ma) * 100 if len(ma) else 0):.1f}%")
    e.metric("FDA土方交換方量", f"{(mf['vol'].sum() if len(mf) else 0):,.1f} m³", help="FDA不計入車次，另列方量供參考")

    l, r = st.columns(2)
    with l:
        by_t = m.groupby("ticket_type")["vol"].sum()
        fig = go.Figure(go.Pie(labels=list(by_t.index), values=list(by_t.values), hole=0.4))
        fig.update_layout(title="聯單類型出土量分佈（原土質分佈）", height=300, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, use_container_width=True, key="ana_pie_type")
    with r:
        by_d = {"台北港": float(m["vol"].sum())}
        if len(mf):
            by_d["FDA"] = float(mf["vol"].sum())
        fig = go.Figure(go.Pie(labels=list(by_d), values=list(by_d.values()), hole=0.4))
        fig.update_layout(title="運送目的地方量比例（原土資場比例）", height=300, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, use_container_width=True, key="ana_pie_dest")

    by_z = m.groupby("zone")["vol"].sum().sort_values(ascending=False)
    fig = go.Figure(go.Bar(x=list(by_z.index), y=list(by_z.values), marker_color="#16a085"))
    fig.update_layout(title="各出土分區出土量（區間累計，台北港）", height=280, margin=dict(l=10, r=10, t=40, b=10), xaxis=dict(type="category"))
    st.plotly_chart(fig, use_container_width=True, key="ana_bar_zone")

    st.markdown("##### 月度車輛績效結算明細（區間累計）")
    perf = m[m["truck"] != ""].groupby("truck").agg(總趟次=("trips", "sum"), 實際出土量=("vol", "sum")).sort_values("總趟次", ascending=False).reset_index()
    perf = perf.rename(columns={"truck": "車號"})
    perf["實際出土量"] = perf["實際出土量"].round(1)
    _drv = {t: _driver_of(g) for t, g in m.groupby("truck")}
    perf.insert(1, "司機", perf["車號"].map(_drv).fillna(""))
    if (perf["司機"] == "").all():
        perf = perf.drop(columns=["司機"])
    _show_table(perf, 420)


def render(df_results=None, global_stage_choice=None):
    st.write("### 🔬 聯單進階分析")
    st.caption("直接讀取雲端 dispatch_logs，不需要上傳CSV。車次只算台北港（FDA不併入車次）；已作廢的紀錄不計入車次與出土量，只列入異常分析。")
    df_logs = load_sheet_data("dispatch_logs")
    mdf = load_sheet_data("manifest_settings")
    types = mdf["聯單類型"].dropna().astype(str).tolist() if (mdf is not None and not mdf.empty and "聯單類型" in mdf.columns) else ["B1", "B2-3", "B4", "B5"]
    d = prepare_tickets(df_logs, types)
    if d.empty:
        st.info("雲端 dispatch_logs 目前沒有可分析的資料。")
        return
    if "出場補登" not in df_logs.columns:
        st.info("📎 雲端 dispatch_logs 還沒有官方補登資料，所以目前「補登」只會統計到已作廢的紀錄。"
                "請到「🧾 官方聯單對帳」分頁最上面的「補充官方欄位」上傳官方CSV一次（可一次選多份），之後就會用官方真實的補登數字；日常對帳也會自動帶入。")
    alltp = d[d["dest"] == "台北港"]                       # 含作廢（異常分析用）
    port = alltp[~alltp["is_void"]]                        # 正式台北港紀錄（車次/出土量）
    fda = d[(d["dest"] == "FDA") & (~d["is_void"])]
    if port.empty:
        st.info("目前沒有有效的台北港紀錄可分析。")
        return

    view = st.radio("分析項目", ["日報表與車輛分析", "週度趨勢與分析", "月度結算與比較"], horizontal=True, key="analytics_view")
    if view == "日報表與車輛分析":
        _view_daily(port)
    elif view == "週度趨勢與分析":
        _view_weekly(port, alltp)
    else:
        _view_monthly(port, alltp, fda)
