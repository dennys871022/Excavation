import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from shapely.geometry import Polygon

st.set_page_config(page_title="網格生成與方量計算", layout="wide")

st.title("土方開挖分區與方量基準建置 (雙網格+柱位檢核版)")

st.sidebar.header("1. 匯入檢核圖資 (選填)")
column_file = st.sidebar.file_uploader("上傳柱位座標 CSV (需含 X, Y 欄位)", type=["csv"])

st.sidebar.header("2. 開挖深度設定")
depth_input = st.sidebar.text_input("各階開挖深度 (以逗號分隔，共4階)", "2.5, 3.0, 3.5, 2.0")

# 系統後台直接定義圖面參數：拆分左右兩大區域
dx1 = [8.7, 8.7, 8.7, 8.7, 8.7, 10.2]
dy1 = [-9.6, -8.4, -7.5, -7.5, -7.5]
y_labels1 = ["A", "B", "C", "D", "E", "F"]

dx2 = [6.9, 9.0, 9.0, 9.3, 9.3, 9.3, 9.3, 9.0, 9.0, 6.0]
dy2 = [-11.25, -9.0, -9.3, -3.45, -5.85, -9.3, -7.5]
y_labels2 = ["A", "B'", "C'", "D'", "E", "E'", "F'", "G"]

try:
    depths = [float(d.strip()) for d in depth_input.split(",")]

    x_coords1 = [0.0] + list(np.cumsum(dx1))
    y_coords1 = [0.0] + list(np.cumsum(dy1))

    x_offset = x_coords1[-1]
    x_coords2 = [x_offset] + list(x_offset + np.cumsum(dx2))
    y_coords2 = [0.0] + list(np.cumsum(dy2))

    results = []
    grid_index = 0
    
    # 建立左區網格
    for j in range(len(dy1)):
        for i in range(len(dx1)):
            grid_id = f"{y_labels1[j]}{i+1}"
            x_min, x_max = x_coords1[i], x_coords1[i+1]
            y_max, y_min = y_coords1[j], y_coords1[j+1]
            
            poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
            area = poly.area
            vols = [area * d for d in depths]
            
            is_excavated = not (i >= 3 and j >= 3)
            
            results.append({
                "系統編號": grid_index, "保留開挖區": is_excavated, "分區代號": grid_id,
                "面積 (m²)": round(area, 2),
                "一階土方": round(vols[0], 2), "二階土方": round(vols[1], 2),
                "三階土方": round(vols[2], 2), "四階土方": round(vols[3], 2),
                "預估總土方": round(sum(vols), 2),
                "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max,
                "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2
            })
            grid_index += 1

    # 建立右區網格
    for j in range(len(dy2)):
        for i in range(len(dx2)):
            grid_id = f"{y_labels2[j]}{i+7}" 
            x_min, x_max = x_coords2[i], x_coords2[i+1]
            y_max, y_min = y_coords2[j], y_coords2[j+1]
            
            poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
            area = poly.area
            vols = [area * d for d in depths]
            
            is_excavated = True
            
            results.append({
                "系統編號": grid_index, "保留開挖區": is_excavated, "分區代號": grid_id,
                "面積 (m²)": round(area, 2),
                "一階土方": round(vols[0], 2), "二階土方": round(vols[1], 2),
                "三階土方": round(vols[2], 2), "四階土方": round(vols[3], 2),
                "預估總土方": round(sum(vols), 2),
                "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max,
                "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2
            })
            grid_index += 1

    df_results = pd.DataFrame(results)

    col1, col2 = st.columns([3, 2])
    
    with col2:
        st.write("### 步驟一：微調開挖邊界")
        edited_df = st.data_editor(
            df_results.drop(columns=['x_min', 'x_max', 'y_min', 'y_max', 'x_center', 'y_center']),
            column_config={
                "系統編號": st.column_config.NumberColumn(disabled=True),
                "保留開挖區": st.column_config.CheckboxColumn("保留開挖區", required=True),
                "分區代號": st.column_config.TextColumn("分區代號", required=True),
                "面積 (m²)": st.column_config.NumberColumn(disabled=True),
            },
            hide_index=True,
            height=650
        )
        
        active_df = edited_df[edited_df["保留開挖區"] == True]
        total_vol = active_df["預估總土方"].sum()
        st.success(f"目前有效開挖區預估總土方量： **{total_vol:,.2f} m³**")

        st.download_button(
            label="💾 下載定稿資料庫 (CSV)",
            data=active_df.to_csv(index=False).encode('utf-8-sig'),
            file_name='土方開挖分區總表_定稿.csv',
            mime='text/csv'
        )

    with col1:
        st.write("### 步驟二：精準網格地圖")
        fig = go.Figure()
        
        # 繪製網格
        for idx, row in edited_df.iterrows():
            if row['保留開挖區']:
                r_data = df_results.iloc[idx]
                x_min, x_max = r_data['x_min'], r_data['x_max']
                y_min, y_max = r_data['y_min'], r_data['y_max']
                
                fig.add_trace(go.Scatter(
                    x=[x_min, x_max, x_max, x_min, x_min],
                    y=[y_min, y_min, y_max, y_max, y_min],
                    mode='lines', line=dict(color='blue', width=1),
                    fill='toself', fillcolor='rgba(0, 100, 255, 0.15)',
                    showlegend=False
                ))
                
                fig.add_annotation(
                    x=r_data['x_center'], y=r_data['y_center'],
                    text=row['分區代號'], showarrow=False, font=dict(color="red", size=12)
                )

        # 繪製匯入的柱位座標
        if column_file is not None:
            df_cols = pd.read_csv(column_file)
            # 自動尋找包含 X 或 Y 的欄位名稱
            x_col = next((c for c in df_cols.columns if 'X' in c.upper()), None)
            y_col = next((c for c in df_cols.columns if 'Y' in c.upper()), None)
            
            if x_col and y_col:
                fig.add_trace(go.Scatter(
                    x=df_cols[x_col], y=df_cols[y_col],
                    mode='markers', name='實體柱位',
                    marker=dict(size=8, color='black', symbol='square'),
                    showlegend=True
                ))
            else:
                st.warning("CSV 檔案中找不到 X 或 Y 欄位，無法繪製柱位。")

        fig.update_layout(
            xaxis_title="X 座標 (m)", yaxis_title="Y 座標 (m)",
            yaxis=dict(scaleanchor="x", scaleratio=1),
            height=700, template="plotly_white",
            margin=dict(l=20, r=20, t=30, b=20)
        )
        st.plotly_chart(fig, use_container_width=True)

except Exception as e:
    st.error(f"執行失敗：{e}")
