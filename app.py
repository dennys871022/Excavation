import streamlit as st

# set_page_config 必須是第一個 Streamlit 指令，所以放在其他模組 import 之前
st.set_page_config(page_title="後台管理端", layout="wide")
st.title("🚧 CDC土方管理系統 ")

from data_store import get_conn
from sidebar import render_auth, render_sidebar
from zones import build_zone_results
import tab_grid
import tab_stats
import tab_stage
import tab_sync
import tab_manifest
import tab_delivery
import tab_analytics
import tab_twin
import tab_weekly

render_auth()

if 'canvas_key_counter' not in st.session_state:
    st.session_state['canvas_key_counter'] = 0

try:
    get_conn()
except Exception as e:
    st.error(f"資料庫連線失敗：{e}")
    st.stop()

settings = render_sidebar()
global_stage_choice = settings["global_stage_choice"]

df_results = build_zone_results(
    settings["base_x_input"], settings["base_y_input"], settings["scale_factor"],
    settings["current_gl"], settings["gl_admin_input"], settings["gl_lab_input"],
    settings["gl_bc_input"], settings["gl_a_input"], settings["gl_oil_input"],
)

# 用單選列取代 st.tabs：st.tabs 每次互動都會把「所有分頁」的程式碼全部跑一遍，
# 改成只執行目前選到的分頁，其他分頁完全不運算，網頁會快很多。
TAB_RENDERERS = {
    "🏗️ 3D 工地管理中心": tab_twin.render,
    "🗺️ 圖資與方量基準": tab_grid.render,
    "📊 出土統計儀表板": tab_stats.render,
    "📈 階段開挖管控": tab_stage.render,
    "📋 週會簡報": tab_weekly.render,
    "🔬 聯單進階分析": tab_analytics.render,
    "🧾 官方聯單對帳": tab_sync.render,
    "🎫 聯單庫存管理": tab_manifest.render,
    "✍️ 現場廠商簽收": tab_delivery.render,
}
# 目前分頁記在網址上（?tab=stats）：重新整理/重新連線時才會停在同一個分頁，
# 不會出現「上方選項顯示出土儀表板、內容卻是 3D」的情況；也可以直接用網址開指定分頁。
TAB_KEYS = {
    "🏗️ 3D 工地管理中心": "twin", "🗺️ 圖資與方量基準": "grid", "📊 出土統計儀表板": "stats",
    "📈 階段開挖管控": "stage", "📋 週會簡報": "weekly", "🔬 聯單進階分析": "analytics",
    "🧾 官方聯單對帳": "sync", "🎫 聯單庫存管理": "manifest", "✍️ 現場廠商簽收": "delivery",
}
_KEY_TO_TAB = {v: k for k, v in TAB_KEYS.items()}
if st.session_state.get("active_tab") not in TAB_RENDERERS:
    st.session_state["active_tab"] = _KEY_TO_TAB.get(st.query_params.get("tab"), list(TAB_RENDERERS)[0])


def _on_tab_change():
    st.query_params["tab"] = TAB_KEYS[st.session_state["active_tab"]]


active_tab = st.radio(
    "目前分頁", list(TAB_RENDERERS), horizontal=True, key="active_tab", label_visibility="collapsed",
    on_change=_on_tab_change,
)
if st.query_params.get("tab") != TAB_KEYS[active_tab]:
    st.query_params["tab"] = TAB_KEYS[active_tab]
st.divider()
TAB_RENDERERS[active_tab](df_results, global_stage_choice)
