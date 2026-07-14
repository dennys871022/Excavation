import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from shapely.geometry import Polygon
import os
import tempfile
from datetime import datetime, timedelta, date
from streamlit_gsheets import GSheetsConnection
import re

st.set_page_config(page_title="後台管理端", layout="wide")
st.title("🚧 CDC土方管理系統 ")

SHEET_URL = "https://docs.google.com/spreadsheets/d/1y3Qnlx9qFwV6S6pyFTsT4rlXP_Tb8qd9tNhRBTjBHao/edit"

if 'sync_data_summary' not in st.session_state:
    st.session_state['sync_data_summary'] = None
if 'sync_date' not in st.session_state:
    st.session_state['sync_date'] = None
if 'official_ready_df' not in st.session_state:
    st.session_state['official_ready_df'] = None
if 'canvas_key_counter' not in st.session_state:
    st.session_state['canvas_key_counter'] = 0

try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"資料庫連線失敗：{e}")
    st.stop()

@st.cache_data(ttl=300)
def fetch_gsheet_data(sheet_name):
    return conn.read(spreadsheet=SHEET_URL, worksheet=sheet_name, ttl=0)

def load_sheet_data(sheet_name):
    try:
        df = fetch_gsheet_data(sheet_name)
        df = df.dropna(how='all')
        if not df.empty:
            st.session_state[f"cache_{sheet_name}"] = df.copy()
        return df
    except Exception:
        if f"cache_{sheet_name}" in st.session_state:
            return st.session_state[f"cache_{sheet_name}"]
        return pd.DataFrame()

def save_sheet_data(sheet_name, df):
    try:
        conn.update(spreadsheet=SHEET_URL, worksheet=sheet_name, data=df)
        st.cache_data.clear()
        st.session_state[f"cache_{sheet_name}"] = df.copy()
        return True
    except Exception as e:
        st.error(f"寫入分頁 `{sheet_name}` 失敗：{e}")
        return False

if st.sidebar.button("🔄 強制同步雲端最新資料", use_container_width=True):
    for sheet in ["grid_zones", "dispatch_logs", "manifest_settings", "manifest_delivery"]:
        if f"cache_{sheet}" in st.session_state:
            del st.session_state[f"cache_{sheet}"]
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("### 各區開挖 GL 高程設定")
base_x_input = st.sidebar.number_input("1軸與A軸交點 X", value=-274766.4, format="%.2f")
base_y_input = st.sidebar.number_input("1軸與A軸交點 Y", value=-24009.49, format="%.2f")
scale_option = st.sidebar.selectbox("CAD圖資單位", ["公分 (除以100)", "公尺 (不轉換)", "公釐 (除以1000)"])
scale_factor = 100 if "公分" in scale_option else (1000 if "公釐" in scale_option else 1)
current_gl = st.sidebar.number_input("現地 GL 高程增減 (m)", value=0.0, step=0.1)

gl_admin_input = st.sidebar.text_input("行政棟區域 GL高程 (4挖)", "2.5, 4.45, 7.85, 9.9")
gl_lab_input = st.sidebar.text_input("實驗棟區域 GL高程 (4挖)", "2.5, 4.45, 7.85, 11.4")
gl_bc_input = st.sidebar.text_input("滯洪池BC區 GL高程 (2挖)", "1.5, 7.6")
gl_a_input = st.sidebar.text_input("滯洪池A區 GL高程 (2挖)", "2.0, 7.85")

def get_thickness_from_gl(gl_str, gl_offset):
    try:
        gl_list = [float(x.strip()) for x in gl_str.split(",")]
        thickness = []
        for i in range(len(gl_list)):
            if i == 0:
                thickness.append(max(0.0, gl_list[i] + gl_offset))
            else:
                thickness.append(max(0.0, gl_list[i] - gl_list[i-1]))
        return thickness
    except Exception:
        return []

def generate_backend_map(df_results, zone_grouped):
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches
    from matplotlib.font_manager import FontProperties
    import gc
    
    fig, ax = plt.subplots(figsize=(10, 6))
    font_path = "font.ttf"
    my_font = FontProperties(fname=font_path) if os.path.exists(font_path) else None
    
    vol_dict = {}
    if not zone_grouped.empty:
        vol_dict = zone_grouped.set_index('出土分區')['累計實挖方量'].to_dict()
    stage_dict = df_results.set_index('分區代號')['各階累計方量'].to_dict() if not df_results.empty else {}
    
    for idx, row in df_results.iterrows():
        grid_id = row['分區代號']
        current_vol = vol_dict.get(grid_id, 0)
        thresholds = stage_dict.get(grid_id, [])
        fill_color = '#F0F0F0' 
        
        if pd.notnull(current_vol) and current_vol > 0 and len(thresholds) > 0:
            if current_vol >= thresholds[-1] * 0.98:
                fill_color = '#2ECC71' 
            else:
                colors = ['#F1C40F', '#E67E22', '#3498DB', '#9B59B6']
                for s_idx, t_vol in enumerate(thresholds):
                    if current_vol < t_vol * 0.98:
                        fill_color = colors[s_idx] if s_idx < len(colors) else colors[-1]
                        break
                        
        xy = [[row['x_min'], row['y_min']], [row['x_max'], row['y_min']], 
              [row['x_max'], row['y_max']], [row['x_min'], row['y_max']]]
        poly = patches.Polygon(xy, closed=True, facecolor=fill_color, edgecolor='gray', alpha=0.8)
        ax.add_patch(poly)
        
        if my_font:
            ax.text(row['x_center'], row['y_center'], grid_id, ha='center', va='center', fontsize=8, color='black', fontproperties=my_font)
        else:
            ax.text(row['x_center'], row['y_center'], grid_id, ha='center', va='center', fontsize=8, color='black')
        
    ax.autoscale_view()
    ax.set_aspect('equal')
    plt.axis('off')
    
    tmp_img = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    plt.savefig(tmp_img.name, bbox_inches='tight', dpi=150)
    fig.clf()
    plt.close('all')
    gc.collect()
    return tmp_img.name

def generate_pdf(report_text_left, report_text_right, df_stats, df_results, zone_grouped, period_label="本日"):
    from fpdf import FPDF
    
    pdf = FPDF()
    pdf.add_page()
    
    if os.path.exists("font.ttf"):
        pdf.add_font("CustomFont", fname="font.ttf")
        pdf.set_font("CustomFont", size=18)
    else:
        pdf.set_font("Helvetica", size=18)
        
    title_text = f"CDC土方{period_label}回報"
    pdf.cell(0, 10, text=title_text, align='C', new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    if os.path.exists("font.ttf"):
        pdf.set_font("CustomFont", size=12)
    else:
        pdf.set_font("Helvetica", size=12)
        
    start_y = pdf.get_y()
    pdf.set_xy(15, start_y)
    for line in report_text_left.split('\n'):
        if line.strip():
            pdf.set_x(15)
            pdf.cell(90, 8, text=line.strip().replace('•', '*'), new_x="LMARGIN", new_y="NEXT")
    left_end_y = pdf.get_y()

    pdf.set_xy(110, start_y + 8) 
    for line in report_text_right.split('\n'):
        if line.strip():
            pdf.set_x(110)
            pdf.cell(85, 8, text=line.strip().replace('•', '*'), new_x="LMARGIN", new_y="NEXT")
    right_end_y = pdf.get_y()

    pdf.set_y(max(left_end_y, right_end_y) + 5)
    
    try:
        img_path = generate_backend_map(df_results, zone_grouped)
        pdf.image(img_path, x=15, w=180)
        os.unlink(img_path) 
    except Exception as e:
        if os.path.exists("font.ttf"):
            pdf.set_font("CustomFont", size=10)
        else:
            pdf.set_font("Helvetica", size=10)
        pdf.cell(0, 10, text=f"(地圖生成失敗: {e})", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(5)
    if os.path.exists("font.ttf"):
        pdf.set_font("CustomFont", size=10)
    else:
        pdf.set_font("Helvetica", size=10)
        
    pdf.cell(0, 6, text="進度圖例說明：", new_x="LMARGIN", new_y="NEXT")
    
    legend_items = [
        ("尚未開挖", 240, 240, 240),
        ("1挖進行中", 241, 196, 15),
        ("1挖完成/2挖中", 230, 126, 34),
        ("2挖完成/3挖中", 52, 152, 219),
        ("3挖完成/4挖中", 155, 89, 182),
        ("開挖完成", 46, 204, 113)
    ]
    
    for i, (label, r, g, b) in enumerate(legend_items):
        pdf.set_fill_color(r, g, b)
        pdf.cell(4, 4, text="", border=1, fill=True)
        pdf.cell(2, 4, text="")
        pdf.cell(32, 4, text=label)
        if i == 2:
            pdf.ln(6)
    pdf.ln(6)
    
    if os.path.exists("font.ttf"):
        pdf.set_font("CustomFont", size=14)
    else:
        pdf.set_font("Helvetica", size=14)
        
    pdf.cell(0, 10, text="各分區挖掘進度總表", new_x="LMARGIN", new_y="NEXT")
    
    if not df_stats.empty:
        if os.path.exists("font.ttf"):
            pdf.set_font("CustomFont", size=9)
        else:
            pdf.set_font("Helvetica", size=9)
        col_widths = [45, 45, 45, 45]
        headers = df_stats.columns.tolist()
        
        for i, header in enumerate(headers):
            pdf.cell(col_widths[i], 8, text=str(header), border=1, align='C')
        pdf.ln()
        
        for idx, row in df_stats.iterrows():
            for i, col in enumerate(headers):
                pdf.cell(col_widths[i], 8, text=str(row[col]), border=1, align='C')
            pdf.ln()
            
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf.output(tmp_file.name)
    return tmp_file.name

def generate_delivery_pdf(df_target, scope_label):
    from fpdf import FPDF
    import base64
    import gc
    
    pdf = FPDF()
    pdf.add_page()
    
    if os.path.exists("font.ttf"):
        pdf.add_font("CustomFont", fname="font.ttf")
        pdf.set_font("CustomFont", size=16)
    else:
        pdf.set_font("Helvetica", size=16)
        
    pdf.cell(0, 10, text=f"聯單交付簽收歷史報表 ({scope_label})", align='C', new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    if os.path.exists("font.ttf"):
        pdf.set_font("CustomFont", size=10)
    else:
        pdf.set_font("Helvetica", size=10)
        
    for idx, row in df_target.iterrows():
        curr_serial = str(row['起始序號']).strip()
        if curr_serial.endswith('.0'):
            curr_serial = curr_serial[:-2]
            
        info_text = f"日期: {row['交付日期']}  時間: {row['交付時間']}  廠商: {row['廠商名稱']}  類型: {row['聯單類型']}  張數: {int(row['發放張數'])}  起始序號: {curr_serial}  簽收人: {row['簽收人姓名']}"
        pdf.cell(0, 8, text=info_text, new_x="LMARGIN", new_y="NEXT")
        
        if pd.notnull(row.get('簽名資料')) and str(row['簽名資料']).strip() != "":
            try:
                img_bytes = base64.b64decode(row['簽名資料'])
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_img:
                    tmp_img.write(img_bytes)
                    tmp_img_path = tmp_img.name
                
                if pdf.get_y() > 240:
                    pdf.add_page()
                    
                pdf.cell(25, 15, text="簽名影像: ")
                pdf.image(tmp_img_path, x=35, w=40, h=15)
                pdf.ln(16)
                os.unlink(tmp_img_path)
            except Exception as e:
                pdf.cell(0, 6, text=f"(簽名影像解碼失敗: {e})", new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.cell(0, 6, text="簽名影像: 無簽名資料", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
            
        pdf.cell(0, 4, text="==========================================================================================", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf.output(tmp_file.name)
    gc.collect()
    return tmp_file.name

e_ext = 3.25
dx1 = [8.7, 8.7, 8.7, 8.7, 8.7, 10.2]
dy1 = [-9.6, -8.4, -7.5, -7.5, -7.5]
y_labels1 = ["A", "B", "C", "D", "E"]
dx2 = [6.9, 9.0, 9.0, 9.3, 9.3, 9.3, 9.3, 9.0, 9.0, 6.0]
dy2 = [-11.25, -9.0, -9.3, -9.3, -9.3, -7.5] 
y_labels2 = ["A", "B'", "C'", "D'", "E'", "F'"]

df_results = pd.DataFrame()
try:
    base_x = base_x_input / scale_factor
    base_y = base_y_input / scale_factor
    x_coords1 = [base_x] + list(base_x + np.cumsum(dx1))
    y_coords1 = [base_y] + list(base_y + np.cumsum(dy1))
    x_offset = x_coords1[-1]
    x_coords2 = [x_offset] + list(x_offset + np.cumsum(dx2))
    y_coords2 = [base_y] + list(base_y + np.cumsum(dy2))

    depths_admin = get_thickness_from_gl(gl_admin_input, current_gl)
    depths_lab = get_thickness_from_gl(gl_lab_input, current_gl)
    depths_bc = get_thickness_from_gl(gl_bc_input, current_gl)
    depths_a = get_thickness_from_gl(gl_a_input, current_gl)

    results = []
    
    for j in range(len(dy1)):
        for i in range(len(dx1)):
            if j >= 2 and i >= 3: continue 
            grid_id = f"{y_labels1[j]}{i+1}"
            x_min, x_max = x_coords1[i], x_coords1[i+1]
            y_max, y_min = y_coords1[j], y_coords1[j+1]
            if grid_id in ["E1", "E2", "E3"]: y_min -= e_ext
            poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
            vols = [poly.area * d for d in depths_admin]
            cum_vols = [round(v, 0) for v in list(np.cumsum(vols))]
            v1 = vols[0] if len(vols) > 0 else 0
            v2 = vols[1] if len(vols) > 1 else 0
            v3 = vols[2] if len(vols) > 2 else 0
            v4 = vols[3] if len(vols) > 3 else 0
            results.append({
                "分區代號": grid_id, "區域面積(㎡)": round(poly.area, 0),
                "第1挖方量(m³)": round(v1, 0), "第2挖方量(m³)": round(v2, 0),
                "第3挖方量(m³)": round(v3, 0), "第4挖方量(m³)": round(v4, 0),
                "預估總土方": round(sum(vols), 0), "各階累計方量": cum_vols, 
                "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, 
                "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2
            })
    
    for j in range(len(dy2)):
        for i in range(len(dx2)):
            grid_id = f"{y_labels2[j]}{i+7}" 
            x_min, x_max = x_coords2[i], x_coords2[i+1]
            y_max, y_min = y_coords2[j], y_coords2[j+1]
            poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
            vols = [poly.area * d for d in depths_lab]
            cum_vols = [round(v, 0) for v in list(np.cumsum(vols))]
            v1 = vols[0] if len(vols) > 0 else 0
            v2 = vols[1] if len(vols) > 1 else 0
            v3 = vols[2] if len(vols) > 2 else 0
            v4 = vols[3] if len(vols) > 3 else 0
            results.append({
                "分區代號": grid_id, "區域面積(㎡)": round(poly.area, 0),
                "第1挖方量(m³)": round(v1, 0), "第2挖方量(m³)": round(v2, 0),
                "第3挖方量(m³)": round(v3, 0), "第4挖方量(m³)": round(v4, 0),
                "預估總土方": round(sum(vols), 0), "各階累計方量": cum_vols, 
                "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, 
                "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2
            })
    
    bc_x = [-2764.56, -2758.41, -2749.46]
    bc_y = [-250.94, -256.69, -262.94, -270.04, -275.14]
    idx_l = 1
    for i in range(len(bc_x)-1):
        for j in range(len(bc_y)-1):
            old_idx = j * 2 + i + 1
            if old_idx in [1, 3]: continue
            grid_id = f"滯BC{idx_l}"
            x_min, x_max = bc_x[i], bc_x[i+1]
            y_max, y_min = bc_y[j], bc_y[j+1]
            poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
            vols = [poly.area * d for d in depths_bc]
            cum_vols = [round(v, 0) for v in list(np.cumsum(vols))]
            v1 = vols[0] if len(vols) > 0 else 0
            v2 = vols[1] if len(vols) > 1 else 0
            v3 = vols[2] if len(vols) > 2 else 0
            v4 = vols[3] if len(vols) > 3 else 0
            results.append({
                "分區代號": grid_id, "區域面積(㎡)": round(poly.area, 0),
                "第1挖方量(m³)": round(v1, 0), "第2挖方量(m³)": round(v2, 0),
                "第3挖方量(m³)": round(v3, 0), "第4挖方量(m³)": round(v4, 0),
                "預估總土方": round(sum(vols), 0), "各階累計方量": cum_vols, 
                "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, 
                "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2
            })
            idx_l += 1

    a_x = [-2606.06, -2592.82]
    a_y = [-276.14, -284.44, -290.24, -296.04]
    idx_r = 1
    for j in range(len(a_y)-1):
        for i in range(len(a_x)-1):
            x_min, x_max = a_x[i], a_x[i+1]
            y_max, y_min = a_y[j], a_y[j+1]
            grid_id = f"滯洪池A{idx_r}"
            poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
            vols = [poly.area * d for d in depths_a]
            cum_vols = [round(v, 0) for v in list(np.cumsum(vols))]
            v1 = vols[0] if len(vols) > 0 else 0
            v2 = vols[1] if len(vols) > 1 else 0
            v3 = vols[2] if len(vols) > 2 else 0
            v4 = vols[3] if len(vols) > 3 else 0
            results.append({
                "分區代號": grid_id, "區域面積(㎡)": round(poly.area, 0),
                "第1挖方量(m³)": round(v1, 0), "第2挖方量(m³)": round(v2, 0),
                "第3挖方量(m³)": round(v3, 0), "第4挖方量(m³)": round(v4, 0),
                "預估總土方": round(sum(vols), 0), "各階累計方量": cum_vols, 
                "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, 
                "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2
            })
            idx_r += 1

    df_results = pd.DataFrame(results)
except Exception as e:
    st.sidebar.error(f"圖資運算錯誤: {e}")

tab_grid, tab_stats, tab_sync, tab_manifest, tab_delivery = st.tabs([
    "🗺️ 圖資與方量基準", "📊 出土統計儀表板", "🧾 官方聯單對帳", "🎫 聯單庫存管理", "✍️ 現場廠商簽收"
])

with tab_grid:
    export_columns = ['分區代號', '區域面積(㎡)', '第1挖方量(m³)', '第2挖方量(m³)', '第3挖方量(m³)', '第4挖方量(m³)', '預估總土方']
    if st.button("🚀 推送分區資料至雲端試算表"):
        if save_sheet_data("grid_zones", df_results[export_columns]):
            st.success("分區基準已成功上傳！")
            
    col1, col2 = st.columns([3, 2])
    with col2:
        st.write("### 基準方量總表")
        st.dataframe(df_results[export_columns], height=600)
        st.success(f"全區預估總土方量： **{df_results['預估總土方'].sum():,.0f} m³**")
        
    with col1:
        st.write("### 精準網格地圖")
        fig = go.Figure()
        for idx, row in df_results.iterrows():
            fig.add_trace(go.Scatter(
                x=[row['x_min'], row['x_max'], row['x_max'], row['x_min'], row['x_min']],
                y=[row['y_min'], row['y_min'], row['y_max'], row['y_max'], row['y_min']],
                mode='lines', line=dict(color='blue', width=1),
                fill='toself', fillcolor='rgba(0, 100, 255, 0.1)', showlegend=False, hoverinfo='skip'
            ))
            fig.add_annotation(x=row['x_center'], y=row['y_center'], text=row['分區代號'], showarrow=False, font=dict(color="red", size=12))
        fig.update_layout(dragmode='pan', xaxis_title="X (m)", yaxis_title="Y (m)", yaxis=dict(scaleanchor="x", scaleratio=1), height=700, margin=dict(l=20, r=20, t=30, b=20))
        st.plotly_chart(fig, use_container_width=True, config={'scrollZoom': True, 'displayModeBar': False})

with tab_stats:
    st.write("### 📊 雲端出土統計儀表板")
    
    df_logs = load_sheet_data("dispatch_logs")
    if not df_logs.empty:
        df_logs['orig_index'] = df_logs.index
        
    tw_today = (datetime.utcnow() + timedelta(hours=8)).date()
    st.markdown("#### 📅 篩選統計時間區間")
    date_selection = st.date_input("選擇區間 (單選一日或拖曳選擇範圍)：", value=(tw_today, tw_today))
    
    if isinstance(date_selection, tuple) and len(date_selection) == 2:
        start_date, end_date = date_selection
    elif isinstance(date_selection, tuple) and len(date_selection) == 1:
        start_date = end_date = date_selection[0]
    else:
        start_date = end_date = date_selection

    delta_days = (end_date - start_date).days
    if delta_days == 0:
        period_label = "本日"
    elif start_date.weekday() == 0 and end_date.weekday() == 6 and delta_days == 6:
        period_label = "本週"
    elif start_date.day == 1 and (end_date + timedelta(days=1)).day == 1 and start_date.month == end_date.month:
        period_label = "本月"
    else:
        period_label = "本區間"

    if not df_logs.empty and "日期" in df_logs.columns:
        if "聯單序號" not in df_logs.columns:
            df_logs["聯單序號"] = ""
            
        df_logs['ParsedDate'] = pd.to_datetime(df_logs['日期']).dt.date
        valid_logs = df_logs[df_logs['備註'].astype(str) != '1分鐘內連續點擊'].copy()
        
        if not valid_logs.empty and '時間' in valid_logs.columns:
            valid_logs = valid_logs.sort_values(['車頭車號', '日期', '時間'])

        range_logs = valid_logs[(valid_logs['ParsedDate'] >= start_date) & (valid_logs['ParsedDate'] <= end_date)]
        cumul_logs = valid_logs[valid_logs['ParsedDate'] <= end_date].copy()
        
        range_trucks = range_logs['車頭車號'].nunique() if '車頭車號' in range_logs.columns else 0
        range_trips = len(range_logs)
        range_vol = pd.to_numeric(range_logs['載運方量(m³)'], errors='coerce').sum() if '載運方量(m³)' in range_logs.columns else 0
        
        excavation_days = range_logs['ParsedDate'].nunique() if not range_logs.empty and 'ParsedDate' in range_logs.columns else 0
        excavation_rate = round(range_trips / excavation_days, 1) if excavation_days > 0 else 0
        
        st.markdown(f"#### 📊 {period_label}統計結果 ({start_date} 至 {end_date})")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(f"{period_label}出車車頭數", f"{range_trucks} 輛")
        m2.metric(f"{period_label}總車次", f"{range_trips} 趟")
        m3.metric(f"{period_label}實挖方量", f"{range_vol:,.0f} m³")
        m4.metric("出土功率", f"{excavation_rate} 趟/天")
        st.divider()

        zone_grouped = pd.DataFrame()
        total_est = df_results['預估總土方'].sum() if not df_results.empty else 0
        total_all_trips = len(cumul_logs)
        
        display_df = pd.DataFrame()
        if '出土分區' in cumul_logs.columns and '載運方量(m³)' in cumul_logs.columns:
            df_assigned = cumul_logs[cumul_logs['出土分區'] != '未指定'].copy()
            if not df_assigned.empty:
                df_assigned['載運方量(m³)'] = pd.to_numeric(df_assigned['載運方量(m³)'], errors='coerce')
                zone_grouped = df_assigned.groupby('出土分區')['載運方量(m³)'].sum().reset_index()
                zone_grouped.rename(columns={'載運方量(m³)': '累計實挖方量'}, inplace=True)
                
                baseline_dict = df_results.set_index('分區代號')['預估總土方'].to_dict() if not df_results.empty else {}
                zone_grouped['預估基準方量'] = zone_grouped['出土分區'].map(baseline_dict)
                zone_grouped['完成率數值'] = (zone_grouped['累計實挖方量'] / zone_grouped['預估基準方量'] * 100).round(1)
                
                zone_grouped['累計實挖方量_顯示'] = zone_grouped['累計實挖方量'].apply(lambda x: f"{x:,.0f}")
                zone_grouped['預估基準方量_顯示'] = zone_grouped['預估基準方量'].apply(lambda x: f"{x:,.0f}" if pd.notnull(x) else '無基準量')
                zone_grouped['完成率_顯示'] = zone_grouped['完成率數值'].fillna('不適用').astype(str)
                zone_grouped['完成率_顯示'] = zone_grouped['完成率_顯示'].apply(lambda x: f"{x}%" if x != '不適用' else x)
                
                display_df = zone_grouped[['出土分區', '累計實挖方量_顯示', '預估基準方量_顯示', '完成率_顯示']].rename(
                    columns={'累計實挖方量_顯示': '累計實挖方量', '預估基準方量_顯示': '預估基準方量', '完成率_顯示': '完成率(%)'}
                )

        st.markdown(f"#### 📱 {period_label}回報與報表匯出")
        
        total_excavated = zone_grouped[zone_grouped['出土分區'] != '開挖前土方']['累計實挖方量'].sum() if not zone_grouped.empty else 0
        pre_excavated = zone_grouped[zone_grouped['出土分區'] == '開挖前土方']['累計實挖方量'].sum() if not zone_grouped.empty and '開挖前土方' in zone_grouped['出土分區'].values else 0
        
        manifest_breakdown_str = ""
        if '聯單序號' in range_logs.columns and '載運方量(m³)' in range_logs.columns:
            range_serials = range_logs['聯單序號'].fillna('').astype(str).str.strip().str.upper()
            range_vols = pd.to_numeric(range_logs['載運方量(m³)'], errors='coerce').fillna(0)
            
            m_types = ["B1", "B2-3", "B4", "B5"]
            breakdown_lines = []
            for m_type in m_types:
                mask = range_serials.str.contains(m_type.upper(), regex=False)
                m_trips = mask.sum()
                m_vol = range_vols[mask].sum()
                breakdown_lines.append(f"* {m_type} 聯單： {m_trips} 趟 / {m_vol:,.0f} m³")
            manifest_breakdown_str = "\n".join(breakdown_lines)

        manifest_total = 79692.0
        combined_excavated = total_excavated + pre_excavated
        overall_rate = round((combined_excavated / manifest_total * 100), 1)
        
        report_text_left = f"""【CDC土方開挖{period_label}回報】 區間: {start_date} 至 {end_date}
{period_label}出土天數： {excavation_days} 天
{period_label}車次： {range_trips} 台
出土功率： {excavation_rate} 趟/天
{period_label}出土方量： {range_vol:,.0f} m³
累計總車次： {total_all_trips} 台
累計實挖方量： {total_excavated:,.0f} m³ (另計開挖前土方: {pre_excavated:,.0f} m³)
聯單預估總出土： {manifest_total:,.0f} m³
總體開挖進度： {overall_rate}%"""

        report_text_right = f"區間聯單分類出土：\n{manifest_breakdown_str}"
        ui_display_text = f"{report_text_left}\n\n{report_text_right}"
        
        col_txt, col_fig = st.columns([1, 2])
        with col_txt:
            st.info(ui_display_text.replace("\n", "\n\n"))
            
            st.markdown("#### 📄 匯出 PDF 報表")
            if os.path.exists("font.ttf"):
                if st.button("📥 下載完整 PDF 報表 (點擊後產生，需稍候)"):
                    with st.spinner("正在繪製地圖與生成報表，這需要一些時間..."):
                        try:
                            pdf_path = generate_pdf(report_text_left, report_text_right, display_df, df_results, zone_grouped, period_label)
                            with open(pdf_path, "rb") as f:
                                st.download_button(
                                    label="✅ 點此儲存 PDF 檔案",
                                    data=f,
                                    file_name=f"excavation_report_{start_date}_{end_date}.pdf",
                                    mime="application/pdf",
                                    use_container_width=True
                                )
                        except Exception as e:
                            st.error(f"PDF 產生失敗：{e}")
            else:
                st.warning("⚠️ 找不到字型檔 `font.ttf`，無法產生 PDF。請先將檔案上傳至專案根目錄。")

        with col_fig:
            st.markdown("**進度圖例說明：**")
            st.markdown("⬜ 尚未開挖 🟨 1挖進行中 🟧 1挖完成/2挖進行中 🟦 2挖完成/3挖進行中 🟪 3挖完成/4挖進行中 🟩 開挖完成")
            fig_map = go.Figure()
            if not df_results.empty:
                vol_dict = {}
                if not zone_grouped.empty:
                    vol_dict = zone_grouped.set_index('出土分區')['累計實挖方量'].to_dict()
                
                stage_dict = df_results.set_index('分區代號')['各階累計方量'].to_dict()
                
                for idx, row in df_results.iterrows():
                    grid_id = row['分區代號']
                    current_vol = vol_dict.get(grid_id, 0)
                    thresholds = stage_dict.get(grid_id, [])
                    
                    stage_text = "尚未開挖"
                    fill_color = 'rgba(240, 240, 240, 0.5)' 
                    
                    if pd.notnull(current_vol) and current_vol > 0 and len(thresholds) > 0:
                        if current_vol >= thresholds[-1] * 0.98:
                            stage_text = "開挖完成"
                            fill_color = 'rgba(46, 204, 113, 0.8)' 
                        else:
                            colors = ['rgba(241, 196, 15, 0.7)', 'rgba(230, 126, 34, 0.7)', 'rgba(52, 152, 219, 0.7)', 'rgba(155, 89, 182, 0.7)']
                            for s_idx, t_vol in enumerate(thresholds):
                                if current_vol < t_vol * 0.98:
                                    if s_idx == 0:
                                        stage_text = "1挖進行中"
                                    else:
                                        stage_text = f"{s_idx}挖完成 / {s_idx+1}挖進行中"
                                    fill_color = colors[s_idx] if s_idx < len(colors) else colors[-1]
                                    break
                    
                    fig_map.add_trace(go.Scatter(
                        x=[row['x_min'], row['x_max'], row['x_max'], row['x_min'], row['x_min']],
                        y=[row['y_min'], row['y_min'], row['y_max'], row['y_max'], row['y_min']],
                        mode='lines', line=dict(color='gray', width=1),
                        fill='toself', fillcolor=fill_color, showlegend=False, hoverinfo='text',
                        text=f"{grid_id}<br>{stage_text}<br>已挖: {current_vol:,.0f} m³"
                    ))
                    fig_map.add_annotation(x=row['x_center'], y=row['y_center'], text=grid_id, showarrow=False, font=dict(color="black", size=10))
                
                fig_map.update_layout(title=f"各區階數開挖狀態 (截至 {end_date})", dragmode='pan', xaxis_title="", yaxis_title="", yaxis=dict(scaleanchor="x", scaleratio=1), height=500, margin=dict(l=0, r=0, t=30, b=0))
                st.plotly_chart(fig_map, use_container_width=True, config={'displayModeBar': False})
        
        st.divider()

        st.markdown("#### ⚙️ 批量設定出土分區")
        df_unassigned = valid_logs[valid_logs['出土分區'] == '未指定'].copy()
        if not df_unassigned.empty:
            st.info(f"尚有 {len(df_unassigned)} 筆有效紀錄未指定分區，請勾選並套用。")
            
            select_all = st.checkbox("☑️ 一鍵全選所有未指定紀錄", value=False)
            df_unassigned.insert(0, '勾選', select_all)
            
            edited_unassigned = st.data_editor(df_unassigned, hide_index=True, column_config={"勾選": st.column_config.CheckboxColumn(required=True)})
            
            zone_list = df_results["分區代號"].tolist() if not df_results.empty else []
            col_z1, col_z2 = st.columns([2, 1])
            with col_z1:
                selected_zone = st.selectbox("選擇要套用的分區", options=["請選擇", "開挖前土方"] + zone_list)
            with col_z2:
                if st.button("套用到勾選的紀錄"):
                    if selected_zone == "請選擇":
                        st.error("請先選擇分區")
                    else:
                        checked_rows = edited_unassigned[edited_unassigned['勾選'] == True]
                        if len(checked_rows) > 0:
                            original_indices = checked_rows['orig_index'].tolist()
                            df_logs.loc[original_indices, '出土分區'] = selected_zone
                            
                            if 'orig_index' in df_logs.columns:
                                df_logs = df_logs.drop(columns=['orig_index'])
                            if 'ParsedDate' in df_logs.columns:
                                df_logs = df_logs.drop(columns=['ParsedDate'])
                                
                            if save_sheet_data("dispatch_logs", df_logs):
                                st.success(f"成功更新 {len(checked_rows)} 筆紀錄！")
                                st.rerun()
                        else:
                            st.warning("⚠️ 請至少勾選一筆要套用的紀錄。")
        else:
            st.success("目前所有有效紀錄皆已分配分區。")

        st.divider()
        st.markdown("#### 📍 各分區挖掘進度總表")
        if not display_df.empty:
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            
        with st.expander("📂 檢視所有歷史紀錄"):
            clean_show_logs = df_logs.copy()
            if 'orig_index' in clean_show_logs.columns:
                clean_show_logs = clean_show_logs.drop(columns=['orig_index'])
            if 'ParsedDate' in clean_show_logs.columns:
                clean_show_logs = clean_show_logs.drop(columns=['ParsedDate'])
                
            show_logs = clean_show_logs.sort_values(['日期', '時間'], ascending=[False, False])
            edited_all = st.data_editor(show_logs, use_container_width=True)
            if st.button("💾 儲存歷史紀錄修改"):
                if save_sheet_data("dispatch_logs", edited_all):
                    st.success("歷史紀錄修改已儲存！")
                    st.rerun()
    else:
        st.info("尚無出土紀錄。")

with tab_sync:
    st.write("### 🧾 官方聯單時間序列精準對帳與校正")
    st.info("💡 演算法說明：系統會自動尋找時間最接近的紀錄綁定並寫入聯單序號（保留分區），多出的自動剔除，少按的會依官方時序自動補齊。")
    
    sync_date = st.date_input("選擇對帳日期：", value=(datetime.utcnow() + timedelta(hours=8)).date())

    uploaded_csv = st.file_uploader("上傳官方電子聯單 CSV 檔案", type=["csv"])
    
    if uploaded_csv:
        try:
            try:
                official_df = pd.read_csv(uploaded_csv, encoding='utf-8')
            except UnicodeDecodeError:
                uploaded_csv.seek(0)
                official_df = pd.read_csv(uploaded_csv, encoding='big5')

            plate_col = "出場車頭車號"
            datetime_col = "出場日期"
            serial_col = "聯單序號"

            missing_cols = [c for c in [plate_col, datetime_col, serial_col] if c not in official_df.columns]
            
            if missing_cols:
                st.error(f"⚠️ CSV 檔案格式不符！缺少必要欄位：{', '.join(missing_cols)}。請確認官方匯出的檔案是否包含這些欄位。")
            else:
                if st.button("開始進行時間序列精準比對", use_container_width=True):
                    official_df['FullTime'] = pd.to_datetime(official_df[datetime_col], errors='coerce')
                    official_df['ParsedDate'] = official_df['FullTime'].dt.date
                    official_df['正規化車號'] = official_df[plate_col].astype(str).str.replace(r'\W+', '', regex=True).str.upper()
                    
                    sync_off_df = official_df[official_df['ParsedDate'] == sync_date].copy()

                    df_logs_sync = load_sheet_data("dispatch_logs")
                    if not df_logs_sync.empty and '日期' in df_logs_sync.columns:
                        if "聯單序號" not in df_logs_sync.columns:
                            df_logs_sync["聯單序號"] = ""
                        df_logs_sync['ParsedDate'] = pd.to_datetime(df_logs_sync['日期']).dt.date
                        df_logs_sync['FullTime'] = pd.to_datetime(df_logs_sync['日期'].astype(str) + ' ' + df_logs_sync['時間'].astype(str), errors='coerce')
                        df_logs_sync['正規化車號'] = df_logs_sync['車頭車號'].astype(str).str.replace(r'\W+', '', regex=True).str.upper()
                        sync_sys_df = df_logs_sync[df_logs_sync['ParsedDate'] == sync_date].copy()
                    else:
                        sync_sys_df = pd.DataFrame(columns=['正規化車號', 'FullTime'])

                    off_counts = sync_off_df['正規化車號'].value_counts().reset_index()
                    off_counts.columns = ['車頭車號', '官方趟數']

                    sys_counts = sync_sys_df['正規化車號'].value_counts().reset_index()
                    sys_counts.columns = ['車頭車號', '系統趟數']

                    merged = pd.merge(off_counts, sys_counts, on='車頭車號', how='outer').fillna(0)
                    merged['差異 (多按或漏按)'] = merged['系統趟數'] - merged['官方趟數']
                    
                    st.session_state['sync_data_summary'] = merged
                    st.session_state['sync_date'] = sync_date
                    st.session_state['official_ready_df'] = sync_off_df
                    st.success("時間序列比對運算完成！請檢視下方差異並決定是否同步。")

        except Exception as e:
            st.error(f"檔案解析或比對失敗：{e}")

    if st.session_state.get('sync_data_summary') is not None and st.session_state.get('sync_date') == sync_date:
        merged_data = st.session_state['sync_data_summary']
        st.dataframe(merged_data, use_container_width=True)
        
        st.warning("點擊下方按鈕，系統將依照官方時序重新整理資料庫，並將 CSV 的「聯單序號」永久寫入雲端紀錄中。")
        
        if st.button("以官方聯單時間軸為主，執行精準覆蓋與寫入序號", use_container_width=True):
            df_logs = load_sheet_data("dispatch_logs")
            if df_logs.empty:
                 df_logs = pd.DataFrame(columns=["日期", "時間", "車頭車號", "出土分區", "載運方量(m³)", "備註", "聯單序號"])
            if "聯單序號" not in df_logs.columns:
                df_logs["聯單序號"] = ""

            df_logs['ParsedDate'] = pd.to_datetime(df_logs['日期']).dt.date
            df_logs['FullTime'] = pd.to_datetime(df_logs['日期'].astype(str) + ' ' + df_logs['時間'].astype(str), errors='coerce')
            df_logs['正規化車號'] = df_logs['車頭車號'].astype(str).str.replace(r'\W+', '', regex=True).str.upper()

            sync_off_df = st.session_state['official_ready_df']

            to_delete_indices = []
            to_add_records = []
            updates = {}

            plates = set(sync_off_df['正規化車號']).union(set(df_logs[df_logs['ParsedDate'] == sync_date]['正規化車號']))

            plate_col = "出場車頭車號"
            serial_col = "聯單序號"

            for plate in plates:
                o_subset = sync_off_df[sync_off_df['正規化車號'] == plate].sort_values('FullTime')
                s_subset = df_logs[(df_logs['ParsedDate'] == sync_date) & (df_logs['正規化車號'] == plate)].sort_values('FullTime')

                s_indices = s_subset.index.tolist()
                used_s = set()

                for _, o_row in o_subset.iterrows():
                    o_t = o_row['FullTime']
                    o_plate_raw = o_row[plate_col]
                    o_serial_raw = str(o_row[serial_col])
                    
                    best_s_idx = None
                    best_diff = float('inf')

                    for idx in s_indices:
                        if idx in used_s: continue
                        st_t = s_subset.loc[idx, 'FullTime']
                        if pd.isna(o_t) or pd.isna(st_t): continue
                        diff = abs((o_t - st_t).total_seconds())
                        if diff < best_diff:
                            best_diff = diff
                            best_s_idx = idx

                    if best_s_idx is not None and best_diff < 7200:
                        used_s.add(best_s_idx)
                        updates[best_s_idx] = {
                            "時間": o_t.strftime("%H:%M:%S"),
                            "聯單序號": o_serial_raw
                        }
                    else:
                        to_add_records.append({
                            "日期": o_t.strftime("%Y-%m-%d") if pd.notnull(o_t) else sync_date.strftime("%Y-%m-%d"),
                            "時間": o_t.strftime("%H:%M:%S") if pd.notnull(o_t) else "00:00:00",
                            "車頭車號": o_plate_raw,
                            "出土分區": "未指定",
                            "載運方量(m³)": 12.0,
                            "備註": "官方聯單補登",
                            "聯單序號": o_serial_raw
                        })

                for idx in s_indices:
                    if idx not in used_s:
                        to_delete_indices.append(idx)

            if updates:
                for idx, vals in updates.items():
                    for k, v in vals.items():
                        df_logs.loc[idx, k] = v

            if to_delete_indices:
                df_logs = df_logs.drop(index=to_delete_indices)

            if to_add_records:
                df_logs = pd.concat([df_logs, pd.DataFrame(to_add_records)], ignore_index=True)

            df_logs['SortTime'] = pd.to_datetime(df_logs['日期'].astype(str) + ' ' + df_logs['時間'].astype(str), errors='coerce')
            df_logs = df_logs.sort_values('SortTime').drop(columns=['ParsedDate', 'FullTime', '正規化車號', 'SortTime'])

            if save_sheet_data("dispatch_logs", df_logs):
                st.success("✅ 同步校正與聯單綁定完成！")
                st.session_state['sync_data_summary'] = None

with tab_manifest:
    st.write("### 🎫 聯單庫存與發放管理")
    
    df_manifest = load_sheet_data("manifest_settings")
    if df_manifest.empty:
        df_manifest = pd.DataFrame({
            "聯單類型": ["B1", "B2-3", "B4", "B5"],
            "總配額": [1000, 2790, 2821, 30],
            "已列印數量": [0, 0, 0, 0]
        })
    
    df_logs = load_sheet_data("dispatch_logs")
    
    used_counts = {t: 0 for t in df_manifest["聯單類型"]}
    if not df_logs.empty and "聯單序號" in df_logs.columns:
        valid_serials = df_logs["聯單序號"].dropna().astype(str).str.strip().str.upper()
        valid_serials = valid_serials[(valid_serials != "") & (valid_serials != "NAN") & (valid_serials != "NONE")]
        
        for m_type in df_manifest["聯單類型"]:
            count = valid_serials.str.contains(m_type.upper(), regex=False).sum()
            used_counts[m_type] = count

    df_manifest["總配額"] = pd.to_numeric(df_manifest["總配額"], errors='coerce').fillna(0).astype(int)
    col_print_name = "聯單數量_已列印" if "聯單數量_已列印" in df_manifest.columns else "已列印數量"
    df_manifest["已列印數量"] = pd.to_numeric(df_manifest[col_print_name], errors='coerce').fillna(0).astype(int)
    df_manifest["已使用數量"] = df_manifest["聯單類型"].map(used_counts).fillna(0).astype(int)
    df_manifest["現場剩餘可用"] = df_manifest["已列印數量"] - df_manifest["已使用數量"]
    df_manifest["雲端未列印配額"] = df_manifest["總配額"] - df_manifest["已列印數量"]
    
    st.info("請於下方表格直接修改「已列印數量」，系統會根據對帳結果自動計算現場剩餘的可用張數。")
    edited_manifest = st.data_editor(
        df_manifest, 
        column_config={
            "總配額": st.column_config.NumberColumn(format="%d"),
            "已列印數量": st.column_config.NumberColumn(format="%d", min_value=0),
            "已使用數量": st.column_config.NumberColumn(format="%d"),
            "現場剩餘可用": st.column_config.NumberColumn(format="%d"),
            "雲端未列印配額": st.column_config.NumberColumn(format="%d"),
        },
        disabled=["聯單類型", "總配額", "已使用數量", "現場剩餘可用", "雲端未列印配額"],
        hide_index=True,
        use_container_width=True
    )
    
    if st.button("💾 儲存列印數量更新"):
        if save_sheet_data("manifest_settings", edited_manifest[['聯單類型', '總配額', '已列印數量']]):
            st.success("已更新列印數量！")
            st.rerun()

    st.markdown("#### 🚨 現場庫存狀態警報")
    
    alert_triggered = False
    for idx, row in df_manifest.iterrows():
        if pd.notnull(row["現場剩餘可用"]) and pd.notnull(row["雲端未列印配額"]):
            if row["現場剩餘可用"] < 100 and row["雲端未列印配額"] > 0:
                st.error(f"⚠️ **【警告】{row['聯單類型']}** 聯單現場僅剩 **{int(row['現場剩餘可用'])}** 張！請盡速列印補充備用。 (尚有雲端配額 {int(row['雲端未列印配額'])} 張)")
                alert_triggered = True
            
    if not alert_triggered:
        st.success("✅ 目前所有類型的聯單現場庫存皆十分充足，或已全數列印完畢。")

with tab_delivery:
    st.write("### ✍️ 現場廠商交付簽收管理")
    
    df_delivery = load_sheet_data("manifest_delivery")
    if df_delivery.empty:
        df_delivery = pd.DataFrame(columns=["交付日期", "交付時間", "廠商名稱", "聯單類型", "起始序號", "發放張數", "簽收人姓名", "簽名資料"])

    if not df_delivery.empty:
        st.markdown("#### 📋 歷史交付簽收對帳看板")
        
        record_options = [f"[{r['交付日期']} {r['交付時間']}] {r['廠商名稱']}-{r['簽收人姓名']} ({r['聯單類型']} 聯單 / {int(r['發放張數']) if pd.notnull(r['發放張數']) else 0}張)" for idx, r in df_delivery.iterrows()]
        selected_record_idx = st.selectbox("🔍 選擇一筆歷史紀錄：", options=range(len(record_options)), format_func=lambda x: record_options[x], key="select_history_delivery")
        
        chosen_row = df_delivery.iloc[selected_record_idx]
        
        clean_history_serial = str(chosen_row['起始序號']).strip()
        if clean_history_serial.endswith('.0'):
            clean_history_serial = clean_history_serial[:-2]
            
        st.info(f"""**詳細交付核對資訊：**
* 交付時間： `{chosen_row['交付日期']} {chosen_row['交付時間']}`
* 簽收廠商： `{chosen_row['廠商名稱']}`
* 聯單規格： `{chosen_row['聯單類型']}`
* 發放張數： `{int(chosen_row['發放張數']) if pd.notnull(chosen_row['發放張數']) else 0} 張`
* 起始序號： `{clean_history_serial}`
* 現場簽收人： `{chosen_row['簽收人姓名']}`

*(簽名影像已隱藏，請匯出 PDF 檢視)*""")
        
        st.divider()
        
        st.markdown("#### 📄 導出交付簽收 PDF 報表")
        col_pdf1, col_pdf2 = st.columns([2, 1])
        with col_pdf1:
            pdf_scope = st.selectbox("選擇要匯出的聯單範圍：", options=["全部聯單類型", "B1", "B2-3", "B4", "B5"], key="pdf_scope_select")
        with col_pdf2:
            st.write("")
            st.write("")
            if pdf_scope == "全部聯單類型":
                df_pdf_target = df_delivery.copy()
            else:
                df_pdf_target = df_delivery[df_delivery["聯單類型"] == pdf_scope].copy()
                
            if df_pdf_target.empty:
                st.warning("⚠️ 該範圍內無任何交付紀錄，無法產生報表。")
            else:
                if st.button("📥 下載 PDF 報表", key="btn_download_delivery_pdf"):
                    with st.spinner("產生中..."):
                        try:
                            delivery_pdf_path = generate_delivery_pdf(df_pdf_target, pdf_scope)
                            with open(delivery_pdf_path, "rb") as pdf_file:
                                st.download_button(
                                    label=f"✅ 點此儲存 {pdf_scope} 簽收報表 (含影像)",
                                    data=pdf_file,
                                    file_name=f"delivery_report_{pdf_scope}_{datetime.now().strftime('%Y%m%d')}.pdf",
                                    mime="application/pdf",
                                    use_container_width=True
                                )
                        except Exception as ex:
                            st.error(f"PDF 導出失敗：{ex}")
        st.divider()

    st.markdown("#### 📥 新增聯單現場發放")
    
    input_type = st.selectbox("選擇本次發放的聯單類型", options=["B1", "B2-3", "B4", "B5"], key="delivery_type_select")
    
    df_manifest_check = load_sheet_data("manifest_settings")
    df_logs_check = load_sheet_data("dispatch_logs")
    
    available_stock = 0
    if not df_manifest_check.empty and "聯單類型" in df_manifest_check.columns:
        col_target = "聯單數量_已列印" if "聯單數量_已列印" in df_manifest_check.columns else "已列印數量"
        if col_target in df_manifest_check.columns:
            used_counts_check = {t: 0 for t in df_manifest_check["聯單類型"]}
            if not df_logs_check.empty and "聯單序號" in df_logs_check.columns:
                valid_serials_check = df_logs_check["聯單序號"].dropna().astype(str).str.strip().str.upper()
                valid_serials_check = valid_serials_check[(valid_serials_check != "") & (valid_serials_check != "NAN") & (valid_serials_check != "NONE")]
                for m_type in df_manifest_check["聯單類型"]:
                    count = valid_serials_check.str.contains(m_type.upper(), regex=False).sum()
                    used_counts_check[m_type] = count
            
            df_manifest_check["已使用數量"] = df_manifest_check["聯單類型"].map(used_counts_check).fillna(0).astype(int)
            df_manifest_check["現場剩餘可用"] = pd.to_numeric(df_manifest_check[col_target], errors='coerce').fillna(0).astype(int) - df_manifest_check["已使用數量"]
            
            match_stock_row = df_manifest_check[df_manifest_check["聯單類型"] == input_type]
            if not match_stock_row.empty and "現場剩餘可用" in match_stock_row.columns:
                available_stock = int(match_stock_row.iloc[0]["現場剩餘可用"])

    st.write(f"📊 該類型聯單目前雲端剩餘可交付數量： **{available_stock}** 張")
    
    auto_serial = ""
    if not df_delivery.empty and "聯單類型" in df_delivery.columns and "起始序號" in df_delivery.columns and "發放張數" in df_delivery.columns:
        df_type_last = df_delivery[df_delivery["聯單類型"] == input_type]
        if not df_type_last.empty:
            last_record = df_type_last.iloc[-1]
            last_serial = str(last_record["起始序號"]).strip()
            if last_serial.endswith('.0'):
                last_serial = last_serial[:-2]
            try:
                last_count = int(last_record["發放張數"]) if pd.notnull(last_record["發放張數"]) else 0
                match = re.search(r'\d+', last_serial)
                if match:
                    num_str = match.group()
                    prefix = last_serial[:match.start()]
                    suffix = last_serial[match.end():]
                    next_num = int(num_str) + last_count
                    auto_serial = f"{prefix}{next_num:0{len(num_str)}d}{suffix}"
            except Exception:
                auto_serial = ""

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        input_vendor = st.text_input("廠商名稱", value="力勤", disabled=True)
        input_count = st.number_input("發放張數", min_value=1, value=50, step=1, format="%d")
    with col_d2:
        input_serial = st.text_input("聯單起始序號 (可根據現場實物修改)", value=auto_serial)
        input_name = st.text_input("廠商簽收人姓名", value="")
        
    sign_active = st.checkbox("✍️ 填寫完畢後，點此展開簽名板")
    
    base64_sign = ""
    if sign_active:
        from streamlit_drawable_canvas import st_canvas
        from PIL import Image
        import io
        import base64
        
        canvas_sign = st_canvas(
            fill_color="rgba(255, 255, 255, 1)",
            stroke_width=3,
            stroke_color="#000000",
            background_color="#EEEEEE",
            height=200,
            width=340,
            drawing_mode="freedraw",
            update_streamlit=True,
            key=f"canvas_delivery_form_{st.session_state['canvas_key_counter']}",
        )
        
        if st.button("確認交付並永久儲存簽收紀錄", use_container_width=True):
            if not input_serial.strip():
                st.error("請填寫聯單起始序號")
            elif not input_name.strip():
                st.error("請填寫廠商簽收人姓名")
            elif canvas_sign.image_data is None or np.sum(canvas_sign.image_data[:, :, 3]) == 0:
                st.error("請完成手寫簽名後再提交")
            elif int(input_count) > available_stock:
                st.error(f"❌ 拒絕紀錄：庫存量不足！目前庫存僅剩 {available_stock} 張，無法超量發放 {int(input_count)} 張。")
            else:
                img_array = canvas_sign.image_data.astype('uint8')
                img = Image.fromarray(img_array, 'RGBA')
                buffered = io.BytesIO()
                img.save(buffered, format="PNG")
                base64_sign = base64.b64encode(buffered.getvalue()).decode()
                
                clean_input_serial = input_serial.strip()
                if clean_input_serial.endswith('.0'):
                    clean_input_serial = clean_input_serial[:-2]
                    
                now_tw = datetime.utcnow() + timedelta(hours=8)
                new_record = {
                    "交付日期": now_tw.strftime("%Y-%m-%d"),
                    "交付時間": now_tw.strftime("%H:%M:%S"),
                    "廠商名稱": input_vendor.strip(),
                    "聯單類型": input_type,
                    "起始序號": clean_input_serial,
                    "發放張數": int(input_count),
                    "簽收人姓名": input_name.strip(),
                    "簽名資料": base64_sign
                }
                
                df_delivery = pd.concat([df_delivery, pd.DataFrame([new_record])], ignore_index=True)
                
                if save_sheet_data("manifest_delivery", df_delivery):
                    st.session_state['canvas_key_counter'] += 1
                    st.success("✅ 聯單現場交付成功！紀錄與簽名已即時寫入雲端。")
                    st.rerun()
