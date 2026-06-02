import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from shapely.geometry import Polygon

st.set_page_config(page_title="土方網格與方量計算", layout="wide")

st.title("第一階段：參數化網格與土方基準量建置")

st.sidebar.header("1. 匯入排樁底圖")
pile_file = st.sidebar.file_uploader("上傳排樁座標 CSV (需含 X, Y 欄位)", type=["csv"])

st.sidebar.header("2. 網格基準與跨距")
x0 = st.sidebar.number_input("起始基準點 X 座標", value=0.0)
y0 = st.sidebar.number_input("起始基準點 Y 座標", value=0.0)
x_spacing_input = st.sidebar.text_input("X 向跨距 (以逗號分隔，例如 8,8,10)", "10, 10, 10")
y_spacing_input = st.sidebar.text_input("Y 向跨距 (以逗號分隔，例如 8,8,10)", "10, 10, 10")

st.sidebar.header("3. 開挖深度設定")
depth_input = st.sidebar.text_input("各階開挖深度 (以逗號分隔，共4階)", "2.5, 3.0, 3.5, 2.0")

if st.sidebar.button("生成網格與運算方量"):
    try:
        # 解析輸入參數
        dx_list = [float(x.strip()) for x in x_spacing_input.split(",")]
        dy_list = [float(y.strip()) for y in y_spacing_input.split(",")]
        depths = [float(d.strip()) for d in depth_input.split(",")]

        # 產生網格座標陣列
        x_coords = [x0] + list(x0 + np.cumsum(dx_list))
        y_coords = [y0] + list(y0 + np.cumsum(dy_list))

        fig = go.Figure()

        # 讀取並繪製排樁點位
        if pile_file is not None:
            df_piles = pd.read_csv(pile_file)
            if 'X' in df_piles.columns and 'Y' in df_piles.columns:
                fig.add_trace(go.Scatter(
                    x=df_piles['X'], y=df_piles['Y'],
                    mode='markers', name='排樁',
                    marker=dict(size=6, color='black')
                ))

        results = []
        # 以 A, B, C... 作為 Y 軸向命名，1, 2, 3... 作為 X 軸向命名
        y_labels = [chr(65 + i) for i in range(len(dy_list))] 

        for i in range(len(dx_list)):
            for j in range(len(dy_list)):
                grid_id = f"{y_labels[j]}{i+1}"
                
                x_min, x_max = x_coords[i], x_coords[i+1]
                y_min, y_max = y_coords[j], y_coords[j+1]
                
                poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
                area = poly.area
                
                vols = [area * d for d in depths]
                
                results.append({
                    "分區": grid_id,
                    "面積 (m²)": round(area, 2),
                    "一階土方 (m³)": round(vols[0], 2),
                    "二階土方 (m³)": round(vols[1], 2),
                    "三階土方 (m³)": round(vols[2], 2),
                    "四階土方 (m³)": round(vols[3], 2),
                    "預估總土方 (m³)": round(sum(vols), 2)
                })

                # 在圖表上繪製多邊形網格
                fig.add_trace(go.Scatter(
                    x=[x_min, x_max, x_max, x_min, x_min],
                    y=[y_min, y_min, y_max, y_max, y_min],
                    mode='lines',
                    line=dict(color='blue', width=1),
                    showlegend=False
                ))
                # 標註網格代號
                fig.add_annotation(
                    x=(x_min + x_max)/2, y=(y_min + y_max)/2,
                    text=grid_id, showarrow=False,
                    font=dict(color="red", size=14)
                )

        # 設定圖表比例 1:1，確保網格與排樁不變形
        fig.update_layout(
            title="開挖網格與排樁底圖疊合",
            xaxis_title="X 座標",
            yaxis_title="Y 座標",
            yaxis=dict(scaleanchor="x", scaleratio=1),
            height=650,
            template="plotly_white"
        )

        df_results = pd.DataFrame(results)

        # 畫面排版
        col1, col2 = st.columns([3, 2])
        with col1:
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            st.write("### 預估開挖數量基準表")
            st.dataframe(df_results, height=600)

    except Exception as e:
        st.error(f"參數輸入格式錯誤或執行失敗：{e}")
