import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from shapely.geometry import Polygon
import os
from datetime import date

st.set_page_config(page_title="後台管理端", layout="wide")
st.title("🚧 營建土方後台管理系統")

def init_db():
    if not os.path.exists("drivers.csv"):
        pd.DataFrame(columns=["姓名", "身分證", "車頭車號", "車斗車號", "標準載重(m³)"]).to_csv("drivers.csv", index=False, encoding="utf-8-sig")
    if not os.path.exists("dispatch_logs.csv"):
        pd.DataFrame(columns=["日期", "車頭車號", "出土分區", "載運方量(m³)", "備註"]).to_csv("dispatch_logs.csv", index=False, encoding="utf-8-sig")

init_db()

tab_grid, tab_vehicle, tab_stats = st.tabs(["🗺️ 圖資與方量基準", "🚛 車籍資料庫管理", "📊 出土統計儀表板"])

with tab_grid:
    st.sidebar.header("【圖資基準設定】")
    base_x_input = st.sidebar.number_input("1軸與A軸交點 X", value=-274766.4, format="%.2f")
    base_y_input = st.sidebar.number_input("1軸與A軸交點 Y", value=-24009.49, format="%.2f")
    scale_option = st.sidebar.selectbox("CAD圖資單位", ["公分 (除以100)", "公尺 (不轉換)", "公釐 (除以1000)"])
    scale_factor = 100 if "公分" in scale_option else (1000 if "公釐" in scale_option else 1)
    
    depth_input = st.sidebar.text_input("各階開挖深度 (逗號分隔)", "2.5, 3.0, 3.5, 2.0")
    e_ext = 3.25

    dx1 = [8.7, 8.7, 8.7, 8.7, 8.7, 10.2]
    dy1 = [-9.6, -8.4, -7.5, -7.5, -7.5]
    y_labels1 = ["A", "B", "C", "D", "E"]
    dx2 = [6.9, 9.0, 9.0, 9.3, 9.3, 9.3, 9.3, 9.0, 9.0, 6.0]
    dy2 = [-11.25, -9.0, -9.3, -9.3, -9.3, -7.5] 
    y_labels2 = ["A", "B'", "C'", "D'", "E'", "F'"]

    try:
        depths = [float(d.strip()) for d in depth_input.split(",")]
        base_x = base_x_input / scale_factor
        base_y = base_y_input / scale_factor

        x_coords1 = [base_x] + list(base_x + np.cumsum(dx1))
        y_coords1 = [base_y] + list(base_y + np.cumsum(dy1))
        x_offset = x_coords1[-1]
        x_coords2 = [x_offset] + list(x_offset + np.cumsum(dx2))
        y_coords2 = [base_y] + list(base_y + np.cumsum(dy2))

        results = []
        for j in range(len(dy1)):
            for i in range(len(dx1)):
                if j >= 2 and i >= 3: continue 
                grid_id = f"{y_labels1[j]}{i+1}"
                x_min, x_max = x_coords1[i], x_coords1[i+1]
                y_max, y_min = y_coords1[j], y_coords1[j+1]
                if grid_id in ["E1", "E2", "E3"]: y_min -= e_ext
                poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
                area = poly.area
                vols = [area * d for d in depths]
                results.append({"分區代號": grid_id, "面積 (m²)": round(area, 2), "預估總土方": round(sum(vols), 2), "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2})
        
        for j in range(len(dy2)):
            for i in range(len(dx2)):
                grid_id = f"{y_labels2[j]}{i+7}" 
                x_min, x_max = x_coords2[i], x_coords2[i+1]
                y_max, y_min = y_coords2[j], y_coords2[j+1]
                poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
                area = poly.area
                vols = [area * d for d in depths]
                results.append({"分區代號": grid_id, "面積 (m²)": round(area, 2), "預估總土方": round(sum(vols), 2), "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2})
        
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
                area = poly.area
                vols = [area * d for d in depths]
                results.append({"分區代號": grid_id, "面積 (m²)": round(area, 2), "預估總土方": round(sum(vols), 2), "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2})
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
                area = poly.area
                vols = [area * d for d in depths]
                results.append({"分區代號": grid_id, "面積 (m²)": round(area, 2), "預估總土方": round(sum(vols), 2), "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max, "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2})
                idx_r += 1

        df_results = pd.DataFrame(results)
        
        # 匯出分區資料供現場端使用
        df_results[['分區代號', '預估總土方']].to_csv("grid_zones.csv", index=False, encoding="utf-8-sig")

        col1, col2 = st.columns([3, 2])
        with col2:
            st.write("### 基準方量總表")
            st.dataframe(df_results.drop(columns=['x_min', 'x_max', 'y_min', 'y_max', 'x_center', 'y_center']), height=600)
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

            if os.path.exists("柱心座標.csv"):
                try:
                    df_cols = pd.read_csv("柱心座標.csv")
                    x_col = next((c for c in df_cols.columns if 'X' in c.upper()), None)
                    y_col = next((c for c in df_cols.columns if 'Y' in c.upper()), None)
                    if x_col and y_col:
                        fig.add_trace(go.Scatter(x=df_cols[x_col]/scale_factor, y=df_cols[y_col]/scale_factor, mode='markers', name='柱心', marker=dict(size=5, color='black'), showlegend=False))
                except Exception: pass
            
            fig.update_layout(dragmode='pan', xaxis_title="X (m)", yaxis_title="Y (m)", yaxis=dict(scaleanchor="x", scaleratio=1), height=700, margin=dict(l=20, r=20, t=30, b=20))
            st.plotly_chart(fig, use_container_width=True, config={'scrollZoom': True, 'displayModeBar': False})
            
    except Exception as e:
        st.error(f"圖資生成失敗：{e}")

with tab_vehicle:
    st.write("### 📂 車籍資料庫管理")
    df_drivers = pd.read_csv("drivers.csv")
    uploaded_file = st.file_uploader("📥 匯入 Excel/CSV 檔案", type=["csv", "xlsx", "xls"])
    if uploaded_file:
        try:
            if uploaded_file.name.endswith('.csv'):
                new_df = pd.read_csv(uploaded_file)
            else:
                new_df = pd.read_excel(uploaded_file)
            new_df.columns = new_df.columns.str.replace(r'\s+', '', regex=True)
            new_df.to_csv("drivers.csv", index=False, encoding="utf-8-sig")
            st.success("資料庫已成功更新！請重新整理網頁。")
        except Exception as e:
            st.error(f"檔案讀取失敗：{e}")
            
    edited_drivers = st.data_editor(df_drivers, num_rows="dynamic", use_container_width=True, height=400)
    if st.button("💾 儲存表格變更"):
        edited_drivers.dropna(subset=["車頭車號"]).to_csv("drivers.csv", index=False, encoding="utf-8-sig")
        st.success("車籍資料已更新！")

with tab_stats:
    st.write("### 📊 統計儀表板")
    df_logs = pd.read_csv("dispatch_logs.csv")
    if not df_logs.empty:
        df_logs['日期'] = pd.to_datetime(df_logs['日期']).dt.date
        today_logs = df_logs[df_logs['日期'] == date.today()]
        today_trucks = today_logs['車頭車號'].nunique()
        today_trips = len(today_logs)
        today_vol = today_logs['載運方量(m³)'].sum()
        
        st.markdown("#### 📅 今日出土概況")
        m1, m2, m3 = st.columns(3)
        m1.metric("今日派車數", f"{today_trucks} 輛")
        m2.metric("今日總車次", f"{today_trips} 趟")
        m3.metric("今日實挖方量", f"{today_vol:,.2f} m³")
        st.divider()
        
        st.markdown("#### 📍 各分區挖掘進度 (累計)")
        zone_grouped = df_logs.groupby('出土分區')['載運方量(m³)'].sum().reset_index()
        zone_grouped.rename(columns={'載運方量(m³)': '累計實挖方量'}, inplace=True)
        
        if os.path.exists("grid_zones.csv"):
            df_zones = pd.read_csv("grid_zones.csv")
            baseline_dict = df_zones.set_index('分區代號')['預估總土方'].to_dict()
            zone_grouped['預估基準方量'] = zone_grouped['出土分區'].map(baseline_dict)
            zone_grouped['完成率(%)'] = (zone_grouped['累計實挖方量'] / zone_grouped['預估基準方量'] * 100).fillna(0).round(1)
        
        st.dataframe(zone_grouped, use_container_width=True, hide_index=True)
        with st.expander("📂 檢視所有歷史紀錄"):
            st.dataframe(df_logs.sort_values('日期', ascending=False), use_container_width=True)
    else:
        st.info("尚無出土紀錄。")
