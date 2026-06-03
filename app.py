import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from shapely.geometry import Polygon
import os
from datetime import date

st.set_page_config(page_title="土方開挖與出土管理系統", layout="wide")

st.title("🚧 營建土方開挖與出土管理系統 (全功能整合版)")

# ==========================================
# 輔助函式：確保資料庫檔案存在
# ==========================================
def init_db():
    if not os.path.exists("drivers.csv"):
        pd.DataFrame(columns=["姓名", "身分證", "車頭車號", "車斗車號", "標準載重(m³)"]).to_csv("drivers.csv", index=False, encoding="utf-8-sig")
    if not os.path.exists("dispatch_logs.csv"):
        pd.DataFrame(columns=["日期", "車頭車號", "出土分區", "載運方量(m³)", "備註"]).to_csv("dispatch_logs.csv", index=False, encoding="utf-8-sig")

init_db()

# ==========================================
# 建立頁籤介面
# ==========================================
tab_grid, tab_vehicle, tab_dispatch = st.tabs([
    "🗺️ 第一步：網格與基準方量", 
    "🚛 第二步：車籍資料庫快速查詢", 
    "📝 第三步：出土派車與統計"
])

# ==========================================
# 頁籤 1：網格與基準方量 (定稿版邏輯)
# ==========================================
with tab_grid:
    st.sidebar.header("【圖資基準設定】")
    base_x_input = st.sidebar.number_input("1軸與A軸交點 X", value=-274766.4, format="%.2f")
    base_y_input = st.sidebar.number_input("1軸與A軸交點 Y", value=-24009.49, format="%.2f")
    scale_option = st.sidebar.selectbox("CAD圖資單位", ["公分 (除以100)", "公尺 (不轉換)", "公釐 (除以1000)"])
    scale_factor = 100 if "公分" in scale_option else (1000 if "公釐" in scale_option else 1)
    
    e_ext = st.sidebar.number_input("E1至3 底部延伸納入量 (m)", value=3.25, step=0.25)
    depth_input = st.sidebar.text_input("各階開挖深度 (逗號分隔)", "2.5, 3.0, 3.5, 2.0")

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
        grid_index = 0
        
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
        st.session_state['zone_list'] = df_results['分區代號'].tolist()
        st.session_state['baseline_vols'] = df_results.set_index('分區代號')['預估總土方'].to_dict()

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


# ==========================================
# 頁籤 2：車籍資料庫快速查詢 (整合自訂搜尋功能)
# ==========================================
with tab_vehicle:
    df_drivers = pd.read_csv("drivers.csv")
    
    col_search, col_db = st.columns([1, 1])
    
    with col_search:
        st.write("### 🔍 快速查詢與複製")
        search_term = st.text_input("輸入車號數字即可搜尋 (支援車頭或車斗)：", key="search_input")
        
        if search_term:
            # 篩選符合的資料 (轉大寫並去除空白確保比對精準)
            mask = df_drivers.apply(lambda row: row.astype(str).str.replace(r'\s+', '', regex=True).str.upper().str.contains(search_term.upper().replace(" ", "")), axis=1).any(axis=1)
            search_results = df_drivers[mask]
            
            if search_results.empty:
                st.warning("查無符合資料")
            else:
                if len(search_results) > 1:
                    st.info(f"找到 {len(search_results)} 筆資料，請選擇：")
                    # 將選項格式化為易讀字串
                    options = search_results.apply(lambda x: f"{x['車頭車號']} ({x['姓名']})", axis=1).tolist()
                    selected_option = st.selectbox("選擇要檢視的車輛", options=options)
                    # 反查選中的列
                    selected_idx = options.index(selected_option)
                    target_data = search_results.iloc[selected_idx]
                else:
                    target_data = search_results.iloc[0]

                st.divider()
                st.write("#### 📋 點擊灰色區塊即可一鍵複製")
                
                # 自訂顯示與複製順序 (取代網頁版的拖曳排序)
                if 'field_order' not in st.session_state:
                    st.session_state['field_order'] = ["姓名", "身分證", "車頭車號", "車斗車號"]
                
                selected_order = st.multiselect(
                    "🛠 自訂顯示與複製順序", 
                    options=["姓名", "身分證", "車頭車號", "車斗車號", "標準載重(m³)"],
                    default=st.session_state['field_order']
                )
                st.session_state['field_order'] = selected_order

                # 依據自訂順序顯示複製區塊
                for field in selected_order:
                    val = str(target_data.get(field, "無資料"))
                    st.caption(field)
                    st.code(val, language="text")

    with col_db:
        st.write("### 📂 資料庫管理")
        st.info("直接編輯下方表格或上傳 Excel/CSV 更新資料庫。")
        
        uploaded_file = st.file_uploader("📥 匯入 Excel/CSV 檔案 (需包含 姓名, 身分證, 車頭車號, 車斗車號)", type=["csv", "xlsx", "xls"])
        
        if uploaded_file:
            try:
                if uploaded_file.name.endswith('.csv'):
                    new_df = pd.read_csv(uploaded_file)
                else:
                    new_df = pd.read_excel(uploaded_file)
                
                # 簡單清理欄位名稱的空白
                new_df.columns = new_df.columns.str.replace(r'\s+', '', regex=True)
                new_df.to_csv("drivers.csv", index=False, encoding="utf-8-sig")
                st.success("資料庫已成功更新！請重新整理網頁。")
            except Exception as e:
                st.error(f"檔案讀取失敗：{e}")
                
        edited_drivers = st.data_editor(
            df_drivers,
            num_rows="dynamic",
            use_container_width=True,
            height=400
        )
        
        if st.button("💾 儲存表格變更"):
            edited_drivers.dropna(subset=["車頭車號"]).to_csv("drivers.csv", index=False, encoding="utf-8-sig")
            st.success("車籍資料已更新！")


# ==========================================
# 頁籤 3：出土派車與統計
# ==========================================
with tab_dispatch:
    df_drivers = pd.read_csv("drivers.csv")
    vehicle_list = df_drivers["車頭車號"].dropna().unique().tolist()
    zone_list = st.session_state.get('zone_list', [])
    baseline_dict = st.session_state.get('baseline_vols', {})
    
    col_form, col_stats = st.columns([1, 2])
    
    with col_form:
        st.write("### 📝 新增出土紀錄")
        with st.form("dispatch_form"):
            t_date = st.date_input("派車日期", date.today())
            t_plate = st.selectbox("載運車頭車號", options=["請選擇"] + vehicle_list)
            t_zone = st.selectbox("來源分區", options=["請選擇"] + zone_list)
            
            default_vol = 0.0
            if t_plate != "請選擇":
                match_row = df_drivers[df_drivers["車頭車號"] == t_plate]
                if not match_row.empty and "標準載重(m³)" in match_row.columns:
                    try:
                        default_vol = float(match_row["標準載重(m³)"].iloc[0])
                    except:
                        pass
                
            t_vol = st.number_input("實際載運方量 (m³)", value=default_vol, min_value=0.0, step=1.0)
            t_note = st.text_input("備註")
            
            submit_btn = st.form_submit_button("➕ 登錄紀錄")
            
            if submit_btn:
                if t_plate == "請選擇" or t_zone == "請選擇":
                    st.error("請完整選擇車號與來源分區！")
                else:
                    new_log = pd.DataFrame([{
                        "日期": t_date, "車頭車號": t_plate, "出土分區": t_zone, 
                        "載運方量(m³)": t_vol, "備註": t_note
                    }])
                    new_log.to_csv("dispatch_logs.csv", mode='a', header=False, index=False, encoding="utf-8-sig")
                    st.success(f"已成功紀錄：{t_plate} 從 {t_zone} 載運 {t_vol} m³")

    with col_stats:
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
            zone_grouped['預估基準方量'] = zone_grouped['出土分區'].map(baseline_dict)
            zone_grouped['完成率(%)'] = (zone_grouped['累計實挖方量'] / zone_grouped['預估基準方量'] * 100).fillna(0).round(1)
            
            st.dataframe(zone_grouped, use_container_width=True, hide_index=True)
            
            with st.expander("📂 檢視所有歷史紀錄"):
                st.dataframe(df_logs.sort_values('日期', ascending=False), use_container_width=True)
        else:
            st.info("尚無出土紀錄。")
