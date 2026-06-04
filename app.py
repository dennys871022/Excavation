import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from shapely.geometry import Polygon
import os
from datetime import datetime, timedelta, date
from streamlit_gsheets import GSheetsConnection

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

# 側邊欄統一運算圖資
st.sidebar.header("【圖資與開挖基準設定】")
base_x_input = st.sidebar.number_input("1軸與A軸交點 X", value=-274766.4, format="%.2f")
base_y_input = st.sidebar.number_input("1軸與A軸交點 Y", value=-24009.49, format="%.2f")
scale_option = st.sidebar.selectbox("CAD圖資單位", ["公分 (除以100)", "公尺 (不轉換)", "公釐 (除以1000)"])
scale_factor = 100 if "公分" in scale_option else (1000 if "公釐" in scale_option else 1)
current_gl = st.sidebar.number_input("現地GL高程增減 (m)", value=0.0, step=0.1)

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

    results = []
    depths_admin = [max(0, 2.5 + current_gl), 1.95, 3.4, 2.05]
    for j in range(len(dy1)):
        for i in range(len(dx1)):
            if j >= 2 and i >= 3: continue 
            grid_id = f"{y_labels1[j]}{i+1}"
            x_min, x_max = x_coords1[i], x_coords1[i+1]
            y_max, y_min = y_coords1[j], y_coords1[j+1]
            if grid_id in ["E1", "E2", "E3"]: y_min -= e_ext
            poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
            results.append({"分區代號": grid_id, "預估總土方": round(sum([poly.area * d for d in depths_admin]), 2), "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2})
    
    depths_lab = [max(0, 2.5 + current_gl), 1.95, 3.4, 3.55]
    for j in range(len(dy2)):
        for i in range(len(dx2)):
            grid_id = f"{y_labels2[j]}{i+7}" 
            x_min, x_max = x_coords2[i], x_coords2[i+1]
            y_max, y_min = y_coords2[j], y_coords2[j+1]
            poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
            results.append({"分區代號": grid_id, "預估總土方": round(sum([poly.area * d for d in depths_lab]), 2), "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2})
    
    depths_bc = [max(0, 1.5 + current_gl), 6.1]
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
            results.append({"分區代號": grid_id, "預估總土方": round(sum([poly.area * d for d in depths_bc]), 2), "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2})
            idx_l += 1

    depths_a = [max(0, 2.0 + current_gl), 5.85]
    a_x = [-2606.06, -2592.82]
    a_y = [-276.14, -284.44, -290.24, -296.04]
    idx_r = 1
    for j in range(len(a_y)-1):
        for i in range(len(a_x)-1):
            x_min, x_max = a_x[i], a_x[i+1]
            y_max, y_min = a_y[j], a_y[j+1]
            grid_id = f"滯洪池A{idx_r}"
            poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
            results.append({"分區代號": grid_id, "預估總土方": round(sum([poly.area * d for d in depths_a]), 2), "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2})
            idx_r += 1

    df_results = pd.DataFrame(results)
    st.session_state['grid_df'] = df_results
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

        st.markdown("#### 📱 LINE 每日回報預覽")
        
        total_excavated = zone_grouped[zone_grouped['出土分區'] != '開挖前土方']['累計實挖方量'].sum() if not zone_grouped.empty else 0
        pre_excavated = zone_grouped[zone_grouped['出土分區'] == '開挖前土方']['累計實挖方量'].sum() if not zone_grouped.empty and '開挖前土方' in zone_grouped['出土分區'].values else 0
        overall_rate = round((total_excavated / total_est * 100), 1) if total_est > 0 else 0
        
        report_text = f"""
**【土方開挖每日回報】** {today_str}
* **本日車次**： {today_trips} 趟 ({today_trucks} 輛)
* **本日出土方量**： {today_vol:,.2f} m³
* **累計實挖方量**： {total_excavated:,.2f} m³ (另計開挖前土方: {pre_excavated:,.2f} m³)
* **預估總土方量**： {total_est:,.2f} m³
* **總體開挖進度**： {overall_rate}%
        """
        
        col_txt, col_fig = st.columns([1, 2])
        with col_txt:
            st.info(report_text)
            st.markdown("**進度圖例說明：**")
            st.markdown("⬜ 0% (尚未開挖)\n\n🟥 1% ~ 25%\n\n🟨 26% ~ 50%\n\n🟦 51% ~ 75%\n\n🟩 76% 以上")
            
        with col_fig:
            if not df_results.empty:
                fig_map = go.Figure()
                
                rate_dict = {}
                if not zone_grouped.empty:
                    rate_dict = zone_grouped.set_index('出土分區')['完成率數值'].to_dict()
                
                for idx, row in df_results.iterrows():
                    grid_id = row['分區代號']
                    rate = rate_dict.get(grid_id, 0)
                    
                    fill_color = 'rgba(240, 240, 240, 0.5)' 
                    if pd.notnull(rate) and rate > 0:
                        if rate <= 25: fill_color = 'rgba(255, 102, 102, 0.7)'
                        elif rate <= 50: fill_color = 'rgba(255, 204, 51, 0.7)'
                        elif rate <= 75: fill_color = 'rgba(51, 153, 255, 0.7)'
                        else: fill_color = 'rgba(102, 204, 102, 0.7)'
                    
                    fig_map.add_trace(go.Scatter(
                        x=[row['x_min'], row['x_max'], row['x_max'], row['x_min'], row['x_min']],
                        y=[row['y_min'], row['y_min'], row['y_max'], row['y_max'], row['y_min']],
                        mode='lines', line=dict(color='gray', width=1),
                        fill='toself', fillcolor=fill_color, showlegend=False, hoverinfo='text',
                        text=f"{grid_id}<br>進度: {rate}%"
                    ))
                    fig_map.add_annotation(x=row['x_center'], y=row['y_center'], text=grid_id, showarrow=False, font=dict(color="black", size=10))
                
                fig_map.update_layout(title="各區開挖完成度視覺化", dragmode='pan', xaxis_title="", yaxis_title="", yaxis=dict(scaleanchor="x", scaleratio=1), height=500, margin=dict(l=0, r=0, t=30, b=0))
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
        if not zone_grouped.empty:
            display_df = zone_grouped[['出土分區', '累計實挖方量', '預估基準方量_顯示', '完成率_顯示']].rename(
                columns={'預估基準方量_顯示': '預估基準方量', '完成率_顯示': '完成率(%)'}
            )
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
