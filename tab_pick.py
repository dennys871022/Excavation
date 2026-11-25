"""📍 今日出土分區（手機選分區小頁面）。

網址：你的App網址/?tab=pick&k=通關碼&d=2026-11-25
由電腦上的自動腳本每天 16:30 用 Discord 傳給你；手機點開用地圖點選分區、按送出，
結果存到雲端（stage_settings 全域設定列的「全域_今日分區」），電腦上的腳本讀到後繼續指派分區、產 PDF。
連結裡的 k 是用 Secrets 的 pick_token＋日期算出來的「當天通關碼」：每天都不同、只在當天有效，
外流的舊連結隔天就失效，也推算不出別天的通關碼。
"""

import hashlib
import hmac
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from config import LOOSE_SOIL_FACTOR
from data_store import filter_active_dispatch_logs, load_sheet_data, save_global_pref
from stage_calc import get_stage_index
from zone_picker import zone_map_picker

PREF_FIELD = "全域_今日分區"


def day_key(secret, day_s):
    """主通關碼＋日期 → 當天通關碼（跟電腦上 auto_manifest.py 的算法一樣）"""
    return hmac.new(secret.encode(), day_s.encode(), hashlib.sha256).hexdigest()[:20]   # 存成「2026-11-25|C'7 C'8」；「|跳過」代表今天不指派


def render(df_results, global_stage_choice):
    secret = str(st.secrets.get("pick_token", "")).strip()
    tw_today = (datetime.utcnow() + timedelta(hours=8)).date()
    day = tw_today
    day_s = f"{day:%Y-%m-%d}"
    # 只接受「今天」的連結，而且通關碼要對得上今天
    if (not secret or st.query_params.get("d", "") != day_s
            or not hmac.compare_digest(st.query_params.get("k", ""), day_key(secret, day_s))):
        st.error("⛔ 這個連結無效或已過期（連結只在當天有效），請用 Discord 傳來的今天的連結開啟。")
        return
    st.session_state["demo_mode"] = False   # 有正確通關碼才能寫入
    # 每次自動執行都有自己的編號（r）：電腦只接受「這一次」連結送出的答案，同一天重跑不會誤用舊的回覆
    run = "".join(ch for ch in str(st.query_params.get("r", "")) if ch.isalnum())[:12]
    tag = f"{day_s}#{run}" if run else day_s

    st.markdown(f"### 📍 {day:%m/%d} 今日出土分區")
    st.caption(f"目前作業階段：{global_stage_choice}　｜　點一下分區＝選取，再點一下＝取消，選好按最下面「送出」。"
               "　地圖分成東區、西區，用上方按鈕切換，兩區選的會一起送出。")

    logs = filter_active_dispatch_logs(load_sheet_data("dispatch_logs"))
    excavated, n_today = {}, 0
    if logs is not None and not logs.empty and "出土分區" in logs.columns:
        dd = pd.to_datetime(logs["日期"], errors="coerce").dt.date
        dest = logs.get("運送目的地", pd.Series("台北港", index=logs.index)).fillna("台北港").astype(str)
        n_today = int(((dd == day) & (dest != "FDA")).sum())
        a = logs[logs["出土分區"].astype(str) != "未指定"].copy()
        a["載運方量(m³)"] = pd.to_numeric(a["載運方量(m³)"], errors="coerce")
        excavated = (a.groupby("出土分區")["載運方量(m³)"].sum() / LOOSE_SOIL_FACTOR).to_dict()
    st.info(f"🚚 {day:%m/%d} 目前系統裡台北港車次：{n_today} 車")

    # 手機上整張地圖太小，分成西區／東區兩塊各自放大顯示（兩區選的分區會合在一起）
    if not df_results.empty:
        x_split = df_results.loc[df_results["分區代號"] == "A7", "x_min"]
        x_split = float(x_split.iloc[0]) if len(x_split) else float(df_results["x_center"].median())
        west = df_results[df_results["x_center"] < x_split]
        east = df_results[df_results["x_center"] >= x_split]
        part = st.radio("選擇區域", ["東區（A7～F'16、滯洪池A、油槽）", "西區（A1～E3、滯洪池BC）"],
                        horizontal=True, key="pick_part", label_visibility="collapsed")
        sub = east if part.startswith("東") else west
        h = 380 if part.startswith("東") else 460
        zone_map_picker(sub, "pick_zones", get_stage_index(global_stage_choice), excavated,
                        chart_key="pick_map_east" if part.startswith("東") else "pick_map_west",
                        height=h, marker_size=26)
    codes = df_results["分區代號"].tolist() if not df_results.empty else []
    zones = st.multiselect("已選分區（也可以直接在這裡增減）", ["開挖前土方"] + codes, key="pick_zones")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("✅ 送出", type="primary", use_container_width=True, disabled=not zones):
            if save_global_pref(PREF_FIELD, f"{tag}|{' '.join(zones)}"):
                st.success(f"已送出：{'、'.join(zones)}。電腦會接著自動指派分區並產出報表，可以關掉這個頁面了。")
    with c2:
        if st.button("今天不指派分區", use_container_width=True):
            if save_global_pref(PREF_FIELD, f"{tag}|跳過"):
                st.success("已送出：今天不指派分區，報表照樣產出。")
