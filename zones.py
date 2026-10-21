"""分區（棟別）階段狀態、剩餘容量、車次分配，以及由圖資算出各區方量。"""

import numpy as np
import pandas as pd
import streamlit as st
from shapely.geometry import Polygon
from config import STAGE_VOLUME_COLS, dx1, dx2, dy1, dy2, e_ext, y_labels1, y_labels2


def zone_active_in_stage(row, stage_idx):
    """
    判斷一個分區在「第 stage_idx+1 挖」(0-indexed) 是否有實際開挖方量(>0)。
    取代原本寫死「滯洪池不出現在第3、4挖」的規則，改成完全依照每個分區實際算出來的
    各階方量動態判斷，這樣不管以後哪個分區變幾挖、或新增像油槽這種只在特定單一階段
    （例如第5挖）開挖的特殊分區，地圖跟統計都會自動正確反映，不用再改寫死的規則。
    """
    if stage_idx is None or stage_idx >= len(STAGE_VOLUME_COLS):
        return False
    col = STAGE_VOLUME_COLS[stage_idx]
    if col not in row:
        return False
    val = row[col]
    return pd.notna(val) and val > 0


def filter_zones_for_stage(df, stage_idx):
    """回傳 df 中「在該階段有實際開挖方量」的分區子集；stage_idx 為 None 時（開挖前土方）回傳全部分區。"""
    if df is None or df.empty or stage_idx is None:
        return df
    mask = df.apply(lambda r: zone_active_in_stage(r, stage_idx), axis=1)
    return df[mask]


def local_stage_index(row, stage_idx):
    """
    把「全域階段索引」(0~4，對應第1~5挖) 換算成「這個分區自己的門檻清單(各階累計方量)裡的位置」。
    大多數分區（例如行政棟4挖、滯洪池3挖）是從第1挖開始連續參與，全域索引跟自己清單的位置是1:1對應。
    但油槽這種只在單一後段階段（第5挖）才開挖的分區，它的門檻清單只有1筆資料，
    如果直接拿全域索引4去對應清單第4個位置就會抓錯（清單根本沒有第4個），
    所以要先算「這個分區在 0~stage_idx 這些階段裡，總共實際參與了幾次」，
    再減1，才是它自己清單裡正確的位置。
    """
    count_active = sum(1 for s in range(stage_idx + 1) if zone_active_in_stage(row, s))
    return count_active - 1


def get_stage_remaining_capacity(zone_code, df_results, stage_idx, excavated_vol_dict):
    """
    算出某個分區在「目前階段」還剩多少方量可以挖（m³）。
    = 該區這一挖的累計門檻 − 已經挖掉的累計量，最少為0（已超挖就回傳0）。
    excavated_vol_dict: {分區代號: 目前累計實挖方量}
    回傳 None 代表這個分區在這個階段沒有開挖目標（例如滯洪池的第4挖、或非階段性的「開挖前土方」），
    此時視為沒有容量上限，不參與「挖滿就跳過」的判斷。
    """
    if stage_idx is None or df_results is None or df_results.empty:
        return None
    match = df_results[df_results['分區代號'] == zone_code]
    if match.empty:
        return None
    row = match.iloc[0]
    if not zone_active_in_stage(row, stage_idx):
        return None
    thresholds = row.get('各階累計方量', [])
    local_idx = local_stage_index(row, stage_idx)
    if not thresholds or local_idx < 0 or local_idx >= len(thresholds):
        return None
    band_end = thresholds[local_idx]
    current_vol = excavated_vol_dict.get(zone_code, 0) or 0
    return max(0.0, band_end - current_vol)


def allocate_trucks_by_capacity(truck_volumes, selected_zones, df_results, stage_idx, excavated_vol_dict):
    """
    依「各分區在目前階段的剩餘容量」分配車次，而不是單純平均輪流。
    規則：輪流分派，只要分區還有剩餘需求（>0）就會分給它，優先讓它完成；
    最後一車如果剩餘量小於一整車，允許些微超挖把它收尾完成（不會卡在差一點點卻永遠補不滿的狀態），
    完成後（剩餘量歸零）才會跳過、把後續車次轉去分給還有空間的其他分區；
    全部分區都完成後，多出來的車次回報為未分配。
    """
    # 每個分區的剩餘容量；None 代表沒有上限（不受填滿限制）
    remaining = {}
    for z in selected_zones:
        cap = get_stage_remaining_capacity(z, df_results, stage_idx, excavated_vol_dict)
        remaining[z] = cap

    assigned = []
    cursor = 0
    n = len(selected_zones)
    for vol in truck_volumes:
        placed = None
        # 從上次停的位置開始找，最多繞一圈，找到第一個「還有剩餘需求」的分區（>0即可，不用整車都裝得下）
        for step in range(n):
            z = selected_zones[(cursor + step) % n]
            cap = remaining[z]
            if cap is None or cap > 0:
                placed = z
                if cap is not None:
                    remaining[z] = max(0.0, cap - vol)  # 允許最後一車些微超挖，完成後歸零不會變負的
                cursor = (cursor + step + 1) % n  # 下一車從下一個分區開始找，維持輪流的公平性
                break
        assigned.append(placed)

    unassigned_count = sum(1 for a in assigned if a is None)
    return assigned, unassigned_count


def get_thickness_from_gl(gl_str, gl_offset):
    try:
        # 取絕對值：不管你輸入正數(2.5, 4.45...)還是負數高程(-2.5, -4.45...)，算出來的厚度完全一樣，
        # 純粹是輸入格式讓你可以照工程慣例用負值表示高程，不影響任何土方量計算結果
        gl_list = [abs(float(x.strip())) for x in gl_str.split(",")]
        thickness = []
        for i in range(len(gl_list)):
            if i == 0:
                thickness.append(max(0.0, gl_list[i] + gl_offset))
            else:
                thickness.append(max(0.0, gl_list[i] - gl_list[i-1]))
        return thickness
    except Exception:
        return []


def build_zone_results(base_x_input, base_y_input, scale_factor, current_gl,
                       gl_admin_input, gl_lab_input, gl_bc_input, gl_a_input, gl_oil_input):
    """由側邊欄的座標/比例/GL高程設定，算出各分區面積與各挖方量 (df_results)。"""
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
        depths_oil = get_thickness_from_gl(gl_oil_input, current_gl)

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
                    "第3挖方量(m³)": round(v3, 0), "第4挖方量(m³)": round(v4, 0), "第5挖方量(m³)": 0,
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
                    "第3挖方量(m³)": round(v3, 0), "第4挖方量(m³)": round(v4, 0), "第5挖方量(m³)": 0,
                    "預估總土方": round(sum(vols), 0), "各階累計方量": cum_vols,
                    "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max,
                    "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2
                })

        # F'8 座標記一下，油槽區塊要用
        f8_x_min = x_coords2[1]
        f8_y_min = y_coords2[6]

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
                    "第3挖方量(m³)": round(v3, 0), "第4挖方量(m³)": round(v4, 0), "第5挖方量(m³)": 0,
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
                    "第3挖方量(m³)": round(v3, 0), "第4挖方量(m³)": round(v4, 0), "第5挖方量(m³)": 0,
                    "預估總土方": round(sum(vols), 0), "各階累計方量": cum_vols,
                    "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max,
                    "x_center": (x_min + x_max)/2, "y_center": (y_min + y_max)/2
                })
                idx_r += 1

        # 油槽：獨立第5挖，等其他區域都挖完、結構做到B1版才會單獨開挖。
        # 位置在 F'8 正下方，長度6.4m（從F'8左邊界起算），面積28.4㎡反推高度 ≈ 4.4m。
        # 假設是矩形貼齊F'8下緣；如果實際形狀/座標不是這樣，跟我說正確座標我再調整。
        OIL_TANK_LENGTH = 6.4
        OIL_TANK_AREA = 28.4
        oil_height = OIL_TANK_AREA / OIL_TANK_LENGTH
        oil_x_min = f8_x_min
        oil_x_max = f8_x_min + OIL_TANK_LENGTH
        oil_y_max = f8_y_min
        oil_y_min = oil_y_max - oil_height
        oil_poly = Polygon([(oil_x_min, oil_y_min), (oil_x_max, oil_y_min), (oil_x_max, oil_y_max), (oil_x_min, oil_y_max)])
        vols_oil = [oil_poly.area * d for d in depths_oil]
        cum_vols_oil = [round(v, 0) for v in list(np.cumsum(vols_oil))]
        v5_oil = vols_oil[0] if len(vols_oil) > 0 else 0
        results.append({
            "分區代號": "油槽", "區域面積(㎡)": round(oil_poly.area, 0),
            "第1挖方量(m³)": 0, "第2挖方量(m³)": 0,
            "第3挖方量(m³)": 0, "第4挖方量(m³)": 0, "第5挖方量(m³)": round(v5_oil, 0),
            "預估總土方": round(sum(vols_oil), 0), "各階累計方量": cum_vols_oil,
            "x_min": oil_x_min, "x_max": oil_x_max, "y_min": oil_y_min, "y_max": oil_y_max,
            "x_center": (oil_x_min + oil_x_max)/2, "y_center": (oil_y_min + oil_y_max)/2
        })

        df_results = pd.DataFrame(results)
    except Exception as e:
        st.sidebar.error(f"圖資運算錯誤: {e}")
    return df_results
