import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from shapely.geometry import Polygon

st.set_page_config(page_title="網格生成與方量計算", layout="wide")

st.title("土方開挖分區與方量基準建置 (客製化滯洪池版)")

st.sidebar.header("1. 絕對座標與圖資")
base_x_input = st.sidebar.number_input("起點 X 座標", value=-274766.4, format="%.2f")
base_y_input = st.sidebar.number_input("起點 Y 座標", value=-24009.49, format="%.2f")

st.sidebar.info("系統將自動讀取同目錄下的「柱心座標.csv」")
scale_option = st.sidebar.selectbox("CAD圖資單位", ["公分 (除以100)", "公尺 (不轉換)", "公釐 (除以1000)"])
scale_factor = 100 if "公分" in scale_option else (1000 if "公釐" in scale_option else 1)

st.sidebar.header("2. 邊界微調參數")
e_ext = st.sidebar.number_input("E1-3 底部延伸納入量 (m)", value=3.0, step=0.5)
dx_l_input = st.sidebar.text_input("左側滯洪池跨距 (2跨)", "5.0, 5.0")
dx_r_input = st.sidebar.number_input("右側滯洪池跨距 (1跨)", value=6.0, step=0.5)

st.sidebar.header("3. 開挖深度設定")
depth_input = st.sidebar.text_input("各階開挖深度 (逗號分隔)", "2.5, 3.0, 3.5, 2.0")

# 左區：保留 A、B 軸全區，C、D、E 軸第4跨後挖空
dx1 = [8.7, 8.7, 8.7, 8.7, 8.7, 10.2]
dy1 = [-9.6, -8.4, -7.5, -7.5, -7.5]
y_labels1 = ["A", "B", "C", "D", "E"]

# 右區：合併 D' 與 E 形成 9.3m 跨距
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
    
    # 1. 生成左區網格 (自動剔除無效區塊)
    for j in range(len(dy1)):
        for i in range(len(dx1)):
            if i >= 3 and j >= 2: 
                continue 
                
            grid_id = f"{y_labels1[j]}{i+1}"
            x_min, x_max = x_coords1[i], x_coords1[i+1]
            y_max, y_min = y_coords1[j], y_coords1[j+1]
            
            # E1-3 底下小區域一同納入
            if grid_id in ["E1", "E2", "E3"]:
                y_min -= e_ext
            
            poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
            area = poly.area
            vols = [area * d for d in depths]
            
            results.append({
                "系統編號": grid_index, "分區代號": grid_id, "面積 (m²)": round(area, 2),
                "一階土方": round(vols[0], 2), "二階土方": round(vols[1], 2),
                "三階土方": round(vols[2], 2), "四階土方": round(vols[3], 2),
                "預估總土方": round(sum(vols), 2),
                "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max,
                "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2
            })
            grid_index += 1

    # 2. 生成右區網格
    for j in range(len(dy2)):
        for i in range(len(dx2)):
            grid_id = f"{y_labels2[j]}{i+7}" 
            x_min, x_max = x_coords2[i], x_coords2[i+1]
            y_max, y_min = y_coords2[j], y_coords2[j+1]
            
            poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
            area = poly.area
            vols = [area * d for d in depths]
            
            results.append({
                "系統編號": grid_index, "分區代號": grid_id, "面積 (m²)": round(area, 2),
                "一階土方": round(vols[0], 2), "二階土方": round(vols[1], 2),
                "三階土方": round(vols[2], 2), "四階土方": round(vols[3], 2),
                "預估總土方": round(sum(vols), 2),
                "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max,
                "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2
            })
            grid_index += 1

    # 3. 生成左側滯洪池 B.C1-6
    dx_l_list = [float(x.strip()) for x in dx_l_input.split(",")]
    xl = [base_x - sum(dx_l_list), base_x - dx_l_list[1], base_x]
    yl = [y_coords1[2], y_coords1[3], y_coords1[4], y_coords1[5] - e_ext]
    
    basin_l_names = ["滯洪池B.C1", "滯洪池B.C2", "滯洪池B.C3", "滯洪池B.C4", "滯洪池B.C5", "滯洪池B.C6"]
    idx_l = 0
    for j in range(3):
        for i in range(2):
            x_min, x_max = xl[i], xl[i+1]
            y_max, y_min = yl[j], yl[j+1]
            poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
            area = poly.area
            vols = [area * d for d in depths]
            results.append({
                "系統編號": grid_index, "分區代號": basin_l_names[idx_l], "面積 (m²)": round(area, 2),
                "一階土方": round(vols[0], 2), "二階土方": round(vols[1], 2),
                "三階土方": round(vols[2], 2), "四階土方": round(vols[3], 2),
                "預估總土方": round(sum(vols), 2),
                "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max,
                "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2
            })
            grid_index += 1
            idx_l += 1

    # 4. 生成右側滯洪池 A1-3
    xr = [x_coords2[-1], x_coords2[-1] + dx_r_input]
    yr = [y_coords2[3], y_coords2[4], y_coords2[5], y_coords2[6]]
    
    basin_r_names = ["滯洪池A1", "滯洪池A2", "滯洪池A3"]
    for j in range(3):
        x_min, x_max = xr[0], xr[1]
        y_max, y_min = yr[j], yr[j+1]
        poly = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])
        area = poly.area
        vols = [area * d for d in depths]
        results.append({
            "系統編號": grid_index, "分區代號": basin_r_names[j], "面積 (m²)": round(area, 2),
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
        st.write("### 步驟一：確認總方量")
        edited_df = st.data_editor(
            df_results.drop(columns=['x_min', 'x_max', 'y_min', 'y_max', 'x_center', 'y_center']),
            column_config={
                "系統編號": st.column_config.NumberColumn(disabled=True),
                "分區代號": st.column_config.TextColumn("分區代號", required=True),
                "面積 (m²)": st.column_config.NumberColumn(disabled=True),
            },
            hide_index=True,
            height=700
        )
        
        total_vol = edited_df["預估總土方"].sum()
        st.success(f"全區預估總土方量： **{total_vol:,.2f} m³**")

        st.download_button(
            label="💾 下載定稿資料庫 (CSV)",
            data=edited_df.to_csv(index=False).encode('utf-8-sig'),
            file_name='土方開挖分區總表_定稿.csv',
            mime='text/csv'
        )

    with col1:
        st.write("### 步驟二：精準網格地圖")
        fig = go.Figure()
        
        for idx, row in edited_df.iterrows():
            r_data = df_results.iloc[idx]
            x_min, x_max = r_data['x_min'], r_data['x_max']
            y_min, y_max = r_data['y_min'], r_data['y_max']
            
            fig.add_trace(go.Scatter(
                x=[x_min, x_max, x_max, x_min, x_min],
                y=[y_min, y_min, y_max, y_max, y_min],
                mode='lines', line=dict(color='blue', width=1),
                fill='toself', fillcolor='rgba(0, 100, 255, 0.15)',
                showlegend=False, hoverinfo='skip'
            ))
            
            fig.add_annotation(
                x=r_data['x_center'], y=r_data['y_center'],
                text=row['分區代號'], showarrow=False, font=dict(color="red", size=12)
            )

        # 讀取柱心座標
        try:
            df_cols = pd.read_csv("柱心座標.csv")
            x_col = next((c for c in df_cols.columns if 'X' in c.upper()), None)
            y_col = next((c for c in df_cols.columns if 'Y' in c.upper()), None)
            
            if x_col and y_col:
                fig.add_trace(go.Scatter(
                    x=df_cols[x_col] / scale_factor, 
                    y=df_cols[y_col] / scale_factor,
                    mode='markers', name='實體柱心點位',
                    marker=dict(size=6, color='black', symbol='square'),
                    showlegend=True
                ))
            else:
                st.warning("「柱心座標.csv」中找不到 X 或 Y 欄位。")
        except FileNotFoundError:
            st.warning("找不到「柱心座標.csv」，請確認檔案已存在 GitHub 同目錄中。")
        except Exception as e:
            st.warning(f"無法讀取柱心資料。錯誤：{e}")

        fig.update_layout(
            dragmode='pan', 
            xaxis_title="絕對 X 座標 (m)", yaxis_title="絕對 Y 座標 (m)",
            yaxis=dict(scaleanchor="x", scaleratio=1),
            height=750, template="plotly_white",
            margin=dict(l=20, r=20, t=30, b=20)
        )
        
        st.plotly_chart(fig, use_container_width=True, config={
            'scrollZoom': True,
            'displayModeBar': True,
            'modeBarButtonsToRemove': ['lasso2d', 'select2d', 'autoScale2d'],
            'displaylogo': False
        })

except Exception as e:
    st.error(f"執行失敗：{e}")
