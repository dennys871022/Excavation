"""側邊欄：管理員權限、強制同步、作業階段、座標/GL 高程設定。"""

import streamlit as st
from config import STAGE_OPTIONS
from data_store import load_global_prefs, save_global_pref


def render_auth():
    """密碼驗證；結果存在 st.session_state["demo_mode"]（True=訪客唯讀）。"""
    st.sidebar.markdown("### 🔒 系統權限")
    pwd = st.sidebar.text_input("輸入管理員密碼解鎖編輯模式", type="password")
    if pwd == "34561297":
        st.session_state["demo_mode"] = False
        st.sidebar.success("🔓 已解鎖管理員模式")
    else:
        st.session_state["demo_mode"] = True
        if pwd:
            st.sidebar.error("❌ 密碼錯誤")
        else:
            st.sidebar.caption("👀 目前為訪客模式 (唯讀沙盒)")


def render_sidebar():
    """畫出側邊欄設定，回傳其他模組要用的設定值。"""
    if st.sidebar.button("🔄 強制同步雲端最新資料", use_container_width=True):
        for sheet in ["grid_zones", "dispatch_logs", "manifest_settings", "manifest_delivery", "stage_settings", "stage_daily_notes"]:
            if f"cache_{sheet}" in st.session_state:
                del st.session_state[f"cache_{sheet}"]
        st.cache_data.clear()
        st.rerun()

    _global_prefs = load_global_prefs()

    st.sidebar.markdown("### 🎯 目前作業階段")
    if "global_stage_choice" not in st.session_state:
        _saved_stage = _global_prefs.get("全域_目前作業階段")
        st.session_state["global_stage_choice"] = _saved_stage if _saved_stage in STAGE_OPTIONS else STAGE_OPTIONS[0]


    def _on_stage_choice_change():
        save_global_pref("全域_目前作業階段", st.session_state["global_stage_choice"])


    global_stage_choice = st.sidebar.selectbox(
        "影響：階段管控頁 / 出土儀表板PDF / 單階段地圖　（重新整理後會記住上次選的階段）",
        STAGE_OPTIONS,
        key="global_stage_choice",
        on_change=_on_stage_choice_change,
    )

    st.sidebar.markdown("### 各區開挖 GL 高程設定")
    base_x_input = st.sidebar.number_input("1軸與A軸交點 X", value=-274766.4, format="%.2f")
    base_y_input = st.sidebar.number_input("1軸與A軸交點 Y", value=-24009.49, format="%.2f")
    scale_option = st.sidebar.selectbox("CAD圖資單位", ["公分 (除以100)", "公尺 (不轉換)", "公釐 (除以1000)"])
    scale_factor = 100 if "公分" in scale_option else (1000 if "公釐" in scale_option else 1)

    if "current_gl_offset" not in st.session_state:
        try:
            st.session_state["current_gl_offset"] = float(_global_prefs.get("全域_現地GL高程增減", 0.0))
        except (ValueError, TypeError):
            st.session_state["current_gl_offset"] = 0.0


    def _on_gl_offset_change():
        save_global_pref("全域_現地GL高程增減", st.session_state["current_gl_offset"])


    current_gl = st.sidebar.number_input(
        "現地 GL 高程增減 (m)　（重新整理後會記住上次的數字）",
        step=0.1,
        key="current_gl_offset",
        on_change=_on_gl_offset_change,
    )

    def _make_persistent_gl_input(label, pref_field, default_value, session_key):
        """建立一個會存到雲端、重新整理後也記得住的GL高程文字輸入框。"""
        if session_key not in st.session_state:
            st.session_state[session_key] = _global_prefs.get(pref_field, default_value)

        def _on_change():
            save_global_pref(pref_field, st.session_state[session_key])

        return st.sidebar.text_input(label, key=session_key, on_change=_on_change)


    gl_admin_input = _make_persistent_gl_input(
        "行政棟區域 GL高程 (4挖，可用負值)", "全域_GL_行政棟", "-2.5, -4.45, -7.85, -9.9", "gl_admin_input_key")
    gl_lab_input = _make_persistent_gl_input(
        "實驗棟區域 GL高程 (4挖，可用負值)", "全域_GL_實驗棟", "-2.5, -4.45, -7.85, -11.4", "gl_lab_input_key")
    gl_bc_input = _make_persistent_gl_input(
        "滯洪池BC區 GL高程 (3挖，可用負值)", "全域_GL_滯洪池BC", "-1.5, -4.5, -7.6", "gl_bc_input_key")
    gl_a_input = _make_persistent_gl_input(
        "滯洪池A區 GL高程 (3挖，可用負值)", "全域_GL_滯洪池A", "-2.0, -5.0, -7.85", "gl_a_input_key")
    gl_oil_input = _make_persistent_gl_input(
        "油槽區域 GL高程 (獨立第5挖，可用負值)", "全域_GL_油槽", "-4.25", "gl_oil_input_key")
    st.sidebar.caption("💡 滯洪池BC區的第2個數字（中間那個）是我暫時估的，不是實際數字，改成正確的之後會自動存起來，下次重整不會跑掉。GL高程可以直接輸入負值（例如 -2.5），跟輸入正值算出來的方量完全一樣，純粹方便你照工程慣例讀。")

    return {
        "global_stage_choice": global_stage_choice,
        "base_x_input": base_x_input,
        "base_y_input": base_y_input,
        "scale_factor": scale_factor,
        "current_gl": current_gl,
        "gl_admin_input": gl_admin_input,
        "gl_lab_input": gl_lab_input,
        "gl_bc_input": gl_bc_input,
        "gl_a_input": gl_a_input,
        "gl_oil_input": gl_oil_input,
    }
