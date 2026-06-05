import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from shapely.geometry import Polygon
import os
import tempfile
from datetime import datetime, timedelta, date
from streamlit_gsheets import GSheetsConnection
from fpdf import FPDF
import matplotlib.pyplot as plt
import matplotlib.patches as patches

st.set_page_config(page_title="後台管理端", layout="wide")
st.title("🚧 營建土方後台管理系統")

SHEET_URL = "https://docs.google.com/spreadsheets/d/1y3Qnlx9qFwV6S6pyFTsT4rlXP_Tb8qd9tNhRBTjBHao/edit"

try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"資料庫連線失敗，請檢查 Secrets 設定：{e}")
    st.stop()

def load_sheet_data(sheet_name):
    try:
        df = conn.read(spreadsheet=SHEET_URL, worksheet=sheet_name, ttl=0)
        return df.dropna(how='all')
    except Exception as e:
        st.warning(f"無法讀取分頁 `{sheet_name}`。錯誤：{e}")
        return pd.DataFrame()

def save_sheet_data(sheet_name, df):
    try:
        conn.update(spreadsheet=SHEET_URL, worksheet=sheet_name, data=df)
        return True
    except Exception as e:
        st.error(f"寫入分頁 `{sheet_name}` 失敗：{e}")
        return False

def generate_backend_map(df_results, zone_grouped):
    fig, ax = plt.subplots(figsize=(10, 6))
    
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
        ax.text(row['x_center'], row['y_center'], grid_id, ha='center', va='center', fontsize=8, color='black')
        
    ax.autoscale_view()
    ax.set_aspect('equal')
    plt.axis('off')
    
    tmp_img = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    plt.savefig(tmp_img.name, bbox_inches='tight', dpi=150)
    plt.close(fig)
    return tmp_img.name

def generate_pdf(report_text, df_stats, df_results, zone_grouped):
    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("CustomFont", fname="font.ttf")
    pdf.set_font("CustomFont", size=18)
    
    pdf.cell(0, 10, text="營建土方每日回報", align='C', new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    pdf.set_font("CustomFont", size=12)
    for line in report_text.split('\n'):
        pdf.multi_cell(0, 8, text=line.replace('•', '*'))
        
    pdf.ln(5)
    
    try:
        img_path = generate_backend_map(df_results, zone_grouped)
        pdf.image(img_path, x=15, w=180)
        os.unlink(img_path) 
    except Exception as e:
        pdf.set_font("CustomFont", size=10)
        pdf.cell(0, 10, text=f"(地圖生成失敗: {e})", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(5)
    pdf.set_font("CustomFont", size=10)
    pdf.cell(0, 8, text="圖例說明: 淺灰(未開挖) | 黃(1挖) | 橘(2挖) | 藍(3挖) | 紫(4挖) | 綠(完成)", new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(5)
    pdf.set_font("CustomFont", size=14)
    pdf.cell(0, 10, text="各分區挖掘進度總表", new_x="LMARGIN", new_y="NEXT")
    
    if not df_stats.empty:
        pdf.set_font("CustomFont", size=9)
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

st.sidebar.header("【圖資與各區開挖參數】")
base_x_input = st.sidebar.number_input("1軸與A軸交點 X", value=-274766.4, format="%.2f")
base_y_input = st.sidebar.number_input("1軸與A軸交點 Y", value=-24009.49, format="%.2f")
scale_option = st.sidebar.selectbox("CAD圖資單位", ["公分 (除以100)", "公尺 (不轉換)", "公釐 (除以1000)"])
scale_factor = 100 if "公分" in scale_option else (1000 if "公釐" in scale_option else 1)

st.sidebar.markdown("### 各區開挖 GL 高程設定")
current_gl = st.sidebar.number_input("現地 GL 高程增減 (m)", value=0.0, step=0.1, help="正值代表現地高，增加第一挖土方；負值代表現地低，減少第一挖土方")

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
    except:
        return []

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
            cum_vols = list(np.cumsum(vols))
            results.append({"分區代號": grid_id, "預估總土方": round(sum(vols), 2), "各階累計方量": cum_vols, "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2})
    
    for j in range(len(dy2)):
        for i in range(len(dx2)):
            grid_id = f"{y_labels2[j]}{i+7}" 
            x_min, x_max = x_coords2[i], x_coords2[i+1]
            y_max, y_min = y_coords2[j], y_coords2[j+1]
            poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
            vols = [poly.area * d for d in depths_lab]
            cum_vols = list(np.cumsum(vols))
            results.append({"分區代號": grid_id, "預估總土方": round(sum(vols), 2), "各階累計方量": cum_vols, "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2})
    
    bc_x = [-2764.56, -2758.41, -2749.46]
    bc_y = [-250.94, -256.69, -262.94, -270.04, -275.14]
    idx_l = 1
    for j in range(len(bc_y)-1):
        for i in range(len(bc_x)-1):
            x_min, x_max = bc_x[i], bc_x[i+1]
            y_max, y_min = bc_y[j], bc_y[j+1]
            if idx_l in [1, 3]:
                idx_l += 1; continue
            grid_id = f"滯洪池B.C{idx_l}"
            poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
            vols = [poly.area * d for d in depths_bc]
            cum_vols = list(np.cumsum(vols))
            results.append({"分區代號": grid_id, "預估總土方": round(sum(vols), 2), "各階累計方量": cum_vols, "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2})
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
            cum_vols = list(np.cumsum(vols))
            results.append({"分區代號": grid_id, "預估總土方": round(sum(vols), 2), "各階累計方量": cum_vols, "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2})
            idx_r += 1

    df_results = pd.DataFrame(results)
except Exception as e:
    st.sidebar.error(f"圖資運算錯誤: {e}")

tab_grid, tab_vehicle, tab_stats = st.tabs(["🗺️ 圖資與方量基準", "🚛 車籍資料庫管理", "📊 出土統計儀表板"])

with tab_grid:
    if st.button("🚀 推送分區資料至雲端試算表"):
        if save_sheet_data("grid_zones", df_results[['分區代號', '預估總土方']]):
            st.success("分區基準已成功上傳！")
            
    col1, col2 = st.columns([3, 2])
    with col2:
        st.write("### 基準方量總表")
        st.dataframe(df_results[['分區代號', '預估總土方']], height=600)
        st.success(f"全區預估總土方量： **{df_results['預估總土方'].sum():,.2f} m³**")
        
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

with tab_vehicle:
    st.write("### 📂 車籍資料庫管理")
    df_drivers = load_sheet_data("drivers")
    if df_drivers.empty:
        df_drivers = pd.DataFrame(columns=["姓名", "身分證", "車頭車號", "車斗車號"])

    uploaded_file = st.file_uploader("📥 匯入 Excel/CSV 檔案 (將覆蓋現有資料)", type=["csv", "xlsx", "xls"])
    if uploaded_file:
        try:
            if uploaded_file.name.endswith('.csv'):
                new_df = pd.read_csv(uploaded_file)
            else:
                new_df = pd.read_excel(uploaded_file)
            new_df.columns = new_df.columns.str.replace(r'\s+', '', regex=True)
            if save_sheet_data("drivers", new_df):
                st.success("資料庫已成功上傳覆蓋！請重新整理網頁。")
        except Exception as e:
            st.error(f"檔案讀取失敗：{e}")
            
    edited_drivers = st.data_editor(df_drivers, num_rows="dynamic", use_container_width=True, height=400)
    if st.button("💾 將變更儲存至雲端"):
        clean_df = edited_drivers.dropna(subset=["車頭車號"])
        if save_sheet_data("drivers", clean_df):
            st.success("車籍資料已同步更新！")

with tab_stats:
    st.write("### 📊 雲端出土統計儀表板")
    if st.button("🔄 重新抓取最新派車資料"):
        st.rerun()

    df_logs = load_sheet_data("dispatch_logs")
    
    if not df_logs.empty and "日期" in df_logs.columns:
        tw_today = (datetime.utcnow() + timedelta(hours=8)).date()
        today_str = tw_today.strftime("%Y-%m-%d")
        
        valid_logs = df_logs[df_logs['備註'] != '1分鐘內連續查詢'].copy()
        today_logs = valid_logs[valid_logs['日期'].astype(str) == today_str]
        
        today_trucks = today_logs['車頭車號'].nunique() if '車頭車號' in today_logs.columns else 0
        today_trips = len(today_logs)
        today_vol = pd.to_numeric(today_logs['載運方量(m³)'], errors='coerce').sum() if '載運方量(m³)' in today_logs.columns else 0
        
        st.markdown("#### 📅 今日有效出土概況")
        m1, m2, m3 = st.columns(3)
        m1.metric("今日派車數", f"{today_trucks} 輛")
        m2.metric("今日總車次", f"{today_trips} 趟")
        m3.metric("今日實挖方量", f"{today_vol:,.2f} m³")
        st.divider()

        zone_grouped = pd.DataFrame()
        total_est = df_results['預估總土方'].sum() if not df_results.empty else 0
        total_all_trips = len(valid_logs)
        
        display_df = pd.DataFrame()
        if '出土分區' in valid_logs.columns and '載運方量(m³)' in valid_logs.columns:
            df_assigned = valid_logs[valid_logs['出土分區'] != '未指定'].copy()
            if not df_assigned.empty:
                df_assigned['載運方量(m³)'] = pd.to_numeric(df_assigned['載運方量(m³)'], errors='coerce')
                zone_grouped = df_assigned.groupby('出土分區')['載運方量(m³)'].sum().reset_index()
                zone_grouped.rename(columns={'載運方量(m³)': '累計實挖方量'}, inplace=True)
                
                baseline_dict = df_results.set_index('分區代號')['預估總土方'].to_dict() if not df_results.empty else {}
                zone_grouped['預估基準方量'] = zone_grouped['出土分區'].map(baseline_dict)
                zone_grouped['完成率數值'] = (zone_grouped['累計實挖方量'] / zone_grouped['預估基準方量'] * 100).round(1)
                
                zone_grouped['預估基準方量_顯示'] = zone_grouped['預估基準方量'].fillna('無基準量')
                zone_grouped['完成率_顯示'] = zone_grouped['完成率數值'].fillna('不適用').astype(str)
                zone_grouped['完成率_顯示'] = zone_grouped['完成率_顯示'].apply(lambda x: f"{x}%" if x != '不適用' else x)
                
                display_df = zone_grouped[['出土分區', '累計實挖方量', '預估基準方量_顯示', '完成率_顯示']].rename(
                    columns={'預估基準方量_顯示': '預估基準方量', '完成率_顯示': '完成率(%)'}
                )

        st.markdown("#### 📱 每日回報與報表匯出")
        
        total_excavated = zone_grouped[zone_grouped['出土分區'] != '開挖前土方']['累計實挖方量'].sum() if not zone_grouped.empty else 0
        pre_excavated = zone_grouped[zone_grouped['出土分區'] == '開挖前土方']['累計實挖方量'].sum() if not zone_grouped.empty and '開挖前土方' in zone_grouped['出土分區'].values else 0
        overall_rate = round((total_excavated / total_est * 100), 1) if total_est > 0 else 0
        
        report_text = f"""【土方開挖每日回報】 {today_str}
• 本日車次： {today_trips} 趟 ({today_trucks} 輛)
• 本日出土方量： {today_vol:,.2f} m³
• 累計總車次： {total_all_trips} 趟
• 累計實挖方量： {total_excavated:,.2f} m³ (另計開挖前土方: {pre_excavated:,.2f} m³)
• 預估總土方量： {total_est:,.2f} m³
• 總體開挖進度： {overall_rate}%"""
        
        col_txt, col_fig = st.columns([1, 2])
        with col_txt:
            st.info("💡 點擊下方文字區塊右上角圖示，即可一鍵複製回報文字。")
            st.code(report_text, language="text")
            
            st.markdown("#### 📄 匯出 PDF 報表")
            if os.path.exists("font.ttf"):
                with st.spinner("正在繪製地圖與生成報表..."):
                    try:
                        pdf_path = generate_pdf(report_text, display_df, df_results, zone_grouped)
                        with open(pdf_path, "rb") as f:
                            st.download_button(
                                label="📥 下載完整 PDF 報表 (包含地圖)",
                                data=f,
                                file_name=f"excavation_report_{today_str}.pdf",
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
                        text=f"{grid_id}<br>{stage_text}<br>已挖: {current_vol:,.1f} m³"
                    ))
                    fig_map.add_annotation(x=row['x_center'], y=row['y_center'], text=grid_id, showarrow=False, font=dict(color="black", size=10))
                
                fig_map.update_layout(title="各區階數開挖狀態", dragmode='pan', xaxis_title="", yaxis_title="", yaxis=dict(scaleanchor="x", scaleratio=1), height=500, margin=dict(l=0, r=0, t=30, b=0))
                st.plotly_chart(fig_map, use_container_width=True, config={'displayModeBar': False})
        
        st.divider()

        st.markdown("#### ⚙️ 批量設定出土分區")
        df_unassigned = valid_logs[valid_logs['出土分區'] == '未指定'].copy()
        if not df_unassigned.empty:
            st.info(f"尚有 {len(df_unassigned)} 筆有效紀錄未指定分區，請勾選並套用。")
            df_unassigned.insert(0, '勾選', False)
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
                        checked_indices = edited_unassigned[edited_unassigned['勾選'] == True].index
                        if len(checked_indices) > 0:
                            original_indices = edited_unassigned.loc[checked_indices].index
                            df_logs.loc[original_indices, '出土分區'] = selected_zone
                            if save_sheet_data("dispatch_logs", df_logs):
                                st.success(f"成功更新 {len(checked_indices)} 筆紀錄！")
                                st.rerun()
        else:
            st.success("目前所有有效紀錄皆已分配分區。")

        st.divider()
        st.markdown("#### 📍 各分區挖掘進度總表")
        if not display_df.empty:
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            
        with st.expander("📂 檢視所有歷史紀錄 (可直接編輯儲存)"):
            edited_all = st.data_editor(df_logs.sort_values(['日期', '時間'], ascending=[False, False]), use_container_width=True)
            if st.button("💾 儲存歷史紀錄修改"):
                df_logs.update(edited_all)
                if save_sheet_data("dispatch_logs", df_logs):
                    st.success("歷史紀錄修改已儲存！")
                    st.rerun()
    else:
        st.info("尚無出土紀錄。")
