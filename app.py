import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from shapely.geometry import Polygon

st.set_page_config(page_title="網格生成與方量計算", layout="wide")

st.title("第一階段：網格線萃取與手動命名")

# 1. 側邊欄設定
st.sidebar.header("1. 匯入網格線")
st.sidebar.markdown("請上傳包含 `起點 X`, `起點 Y`, `終點 X`, `終點 Y` 的 CSV 檔")
grid_file = st.sidebar.file_uploader("上傳 CAD 網格線 CSV", type=["csv"])

st.sidebar.header("2. 開挖深度設定")
depth_input = st.sidebar.text_input("各階開挖深度 (以逗號分隔，共4階)", "2.5, 3.0, 3.5, 2.0")

# 2. 核心運算
if grid_file is not None:
    try:
        df_lines = pd.read_csv(grid_file)
        
        # 提取所有獨立的 X 與 Y 軸線座標
        x_vals = np.concatenate([df_lines['起點 X'].values, df_lines['終點 X'].values])
        y_vals = np.concatenate([df_lines['起點 Y'].values, df_lines['終點 Y'].values])
        
        # 四捨五入至小數點後兩位並排序，消除 CAD 微小誤差
        x_coords = sorted(list(set(np.round(x_vals, 2))))
        y_coords = sorted(list(set(np.round(y_vals, 2))))
        
        depths = [float(d.strip()) for d in depth_input.split(",")]

        results = []
        fig = go.Figure()
        
        # 建立網格多邊形
        grid_index = 0
        for i in range(len(x_coords)-1):
            for j in range(len(y_coords)-1):
                x_min, x_max = x_coords[i], x_coords[i+1]
                y_min, y_max = y_coords[j], y_coords[j+1]
                
                # 計算面積與體積
                poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
                area = poly.area
                
                # 過濾掉面積過小的畸零地 (可能因誤差產生)
                if area > 1.0: 
                    grid_id = f"未命名_{grid_index}"
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
                    
                    # 繪製網格框線
                    fig.add_trace(go.Scatter(
                        x=[x_min, x_max, x_max, x_min, x_min],
                        y=[y_min, y_min, y_max, y_max, y_min],
                        mode='lines',
                        line=dict(color='blue', width=1),
                        showlegend=False
                    ))
                    grid_index += 1

        df_results = pd.DataFrame(results)

        # 3. 畫面排版與互動
        col1, col2 = st.columns([3, 2])
        
        with col2:
            st.write("### 步驟一：手動輸入分區編號")
            st.info("請點擊下方表格的「自訂分區編號」欄位直接修改名稱")
            
            # 使用 st.data_editor 讓使用者直接在網頁上編輯表格
            edited_df = st.data_editor(
                df_results.drop(columns=['x_center', 'y_center']),
                column_config={
                    "系統編號": st.column_config.NumberColumn(disabled=True),
                    "自訂分區編號": st.column_config.TextColumn("自訂分區編號", required=True),
                    "面積 (m²)": st.column_config.NumberColumn(disabled=True),
                },
                hide_index=True,
                height=500
            )

        with col1:
            st.write("### 步驟二：網格疊合檢視")
            # 將使用者編輯後的新名稱標註到圖表上
            for idx, row in edited_df.iterrows():
                fig.add_annotation(
                    x=df_results.loc[idx, 'x_center'], 
                    y=df_results.loc[idx, 'y_center'],
                    text=row['自訂分區編號'], 
                    showarrow=False,
                    font=dict(color="red", size=14)
                )

            fig.update_layout(
                xaxis_title="X 座標",
                yaxis_title="Y 座標",
                yaxis=dict(scaleanchor="x", scaleratio=1),
                height=650,
                template="plotly_white"
            )
            st.plotly_chart(fig, use_container_width=True)
            
        # 提供匯出功能，讓命名後的資料庫可以存下來給下一個階段使用
        st.download_button(
            label="💾 下載已命名分區資料庫 (CSV)",
            data=edited_df.to_csv(index=False).encode('utf-8-sig'),
            file_name='土方開挖分區總表.csv',
            mime='text/csv'
        )

    except Exception as e:
        st.error(f"資料讀取或運算失敗，請確認 CSV 欄位名稱是否正確。錯誤訊息：{e}")
else:
    st.info("請從左側上傳網格線 CSV 檔案")
