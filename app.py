import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from shapely.geometry import Polygon

st.set_page_config(page_title="網格生成與方量計算", layout="wide")

st.title("土方開挖分區與方量基準建置")

st.sidebar.header("1. 預設網格跨距 (公尺)")
st.sidebar.info("已自動載入地下結構圖之跨距數據")
# 根據圖面尺寸轉換為公尺
default_x = "8.7, 8.7, 8.7, 8.7, 8.7, 10.2, 6.9, 9.0, 9.0, 8.3, 8.3, 9.3, 9.3, 9.0, 9.0, 6.0"
default_y = "11.25, 9.0, 9.0, 9.3, 3.45, 5.85, 9.3, 7.5"

x_spacing_input = st.sidebar.text_input("X 向跨距 (1至17軸)", default_x)
y_spacing_input = st.sidebar.text_input("Y 向跨距 (A至G軸)", default_y)

st.sidebar.header("2. 開挖深度設定")
depth_input = st.sidebar.text_input("各階開挖深度 (以逗號分隔，共4階)", "2.5, 3.0, 3.5, 2.0")

try:
    dx_list = [float(x.strip()) for x in x_spacing_input.split(",")]
    # Y軸由上往下繪製，數值取負以符合平面圖視覺習慣
    dy_list = [-float(y.strip()) for y in y_spacing_input.split(",")]
    depths = [float(d.strip()) for d in depth_input.split(",")]

    x_coords = [0.0] + list(np.cumsum(dx_list))
    y_coords = [0.0] + list(np.cumsum(dy_list))

    results = []
    fig = go.Figure()

    # 建立網格多邊形並命名
    y_labels = [chr(65 + i) for i in range(len(dy_list) + 1)] # A, B, C...
    
    grid_index = 0
    for j in range(len(dy_list)):
        for i in range(len(dx_list)):
            grid_id = f"{y_labels[j]}{i+1}"
            
            x_min, x_max = x_coords[i], x_coords[i+1]
            y_max, y_min = y_coords[j], y_coords[j+1] # Y軸反轉
            
            poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
            area = poly.area
            
            vols = [area * d for d in depths]
            
            results.append({
                "系統編號": grid_index,
                "自訂分區編號": grid_id,
                "面積 (m²)": round(area, 2),
                "一階土方": round(vols[0], 2),
                "二階土方": round(vols[1], 2),
                "三階土方": round(vols[2], 2),
                "四階土方": round(vols[3], 2),
                "預估總土方": round(sum(vols), 2),
                "x_center": (x_min + x_max)/2,
                "y_center": (y_min + y_max)/2
            })
            
            fig.add_trace(go.Scatter(
                x=[x_min, x_max, x_max, x_min, x_min],
                y=[y_min, y_min, y_max, y_max, y_min],
                mode='lines',
                line=dict(color='blue', width=1),
                showlegend=False
            ))
            grid_index += 1

    df_results = pd.DataFrame(results)

    col1, col2 = st.columns([3, 2])
    
    with col2:
        st.write("### 步驟一：檢視與修改分區編號")
        st.info("網格已依據圖面尺寸自動生成，你可以點擊表格修改名稱，系統會自動同步至左方圖面。")
        
        edited_df = st.data_editor(
            df_results.drop(columns=['x_center', 'y_center']),
            column_config={
                "系統編號": st.column_config.NumberColumn(disabled=True),
                "自訂分區編號": st.column_config.TextColumn("自訂分區編號", required=True),
                "面積 (m²)": st.column_config.NumberColumn(disabled=True),
            },
            hide_index=True,
            height=600
        )

        st.download_button(
            label="💾 下載已命名分區資料庫 (CSV)",
            data=edited_df.to_csv(index=False).encode('utf-8-sig'),
            file_name='土方開挖分區總表.csv',
            mime='text/csv'
        )

    with col1:
        st.write("### 步驟二：網格疊合檢視")
        for idx, row in edited_df.iterrows():
            fig.add_annotation(
                x=df_results.loc[idx, 'x_center'], 
                y=df_results.loc[idx, 'y_center'],
                text=row['自訂分區編號'], 
                showarrow=False,
                font=dict(color="red", size=12)
            )

        fig.update_layout(
            xaxis_title="X 座標 (m)",
            yaxis_title="Y 座標 (m)",
            yaxis=dict(scaleanchor="x", scaleratio=1),
            height=700,
            template="plotly_white"
        )
        st.plotly_chart(fig, use_container_width=True)

except Exception as e:
    st.error(f"參數輸入格式錯誤或執行失敗：{e}")
