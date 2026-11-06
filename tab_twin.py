"""🏗️ 3D 工地管理中心 分頁：以 3D 場景為主畫面的工地數位孿生。"""

import pandas as pd
import streamlit as st

from data_store import filter_active_dispatch_logs, load_sheet_data
from stage_calc import get_stage_index
from twin_3d import build_twin_payload, render_twin


def _prepare_logs():
    df = load_sheet_data("dispatch_logs")
    if df is None or df.empty or "日期" not in df.columns:
        return pd.DataFrame()
    df = df.copy()
    if "運送目的地" not in df.columns:
        df["運送目的地"] = "台北港"
    else:
        df["運送目的地"] = df["運送目的地"].fillna("台北港").replace("", "台北港")
    if "車次數" not in df.columns:
        df["車次數"] = 1
    return filter_active_dispatch_logs(df)


def render(df_results, global_stage_choice):
    if df_results is None or df_results.empty:
        st.warning("圖資尚未算出分區，請先確認側邊欄的座標與 GL 設定。")
        return

    c1, c2 = st.columns([3, 1])
    with c1:
        st.write("### 🏗️ 3D 工地管理中心")
    with c2:
        height = st.select_slider("畫面高度", options=[700, 780, 860, 960, 1080], value=860, key="twin_height")

    try:
        gl_off = float(st.session_state.get("current_gl_offset", 0.0) or 0.0)
    except (TypeError, ValueError):
        gl_off = 0.0

    payload = build_twin_payload(_prepare_logs(), df_results, get_stage_index(global_stage_choice), gl_off)
    render_twin(payload, height=height)
    st.caption(
        "所有操作都在 3D 畫面裡完成：左側切換上色模式與圖層、點分區看詳細資料、下方時間軸拖曳或按 ▶ 一天一天回放（空白鍵播放、←→ 換日）。"
        "分區深度＝累計實挖方量（鬆方 ÷ 1.2）÷ 分區面積，屬估算；安全支撐依所在分區挖到該層支撐底下 0.5m 才出現。"
        "歷建、大門、洗車台、地磅、構台位置沿用開挖動畫的設定。第一次載入需要連網下載 three.js。"
    )
