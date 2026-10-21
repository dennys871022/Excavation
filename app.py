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

t_grid, t_stats, t_stage, t_sync, t_manifest, t_delivery = st.tabs([
    "🗺️ 圖資與方量基準", "📊 出土統計儀表板", "📈 階段開挖管控", "🧾 官方聯單對帳", "🎫 聯單庫存管理", "✍️ 現場廠商簽收"
])

with t_grid:
    tab_grid.render(df_results, global_stage_choice)
with t_stats:
    tab_stats.render(df_results, global_stage_choice)
with t_stage:
    tab_stage.render(df_results, global_stage_choice)
with t_sync:
    tab_sync.render(df_results, global_stage_choice)
with t_manifest:
    tab_manifest.render(df_results, global_stage_choice)
with t_delivery:
    tab_delivery.render(df_results, global_stage_choice)
