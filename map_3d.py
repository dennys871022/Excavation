"""3D 地圖：把各分區畫成立方體（頂面＝地表，往下＝開挖深度）。只有選到 3D 時才會執行，不影響 2D 的速度。

- render_progress_3d：出土統計儀表板用。實心方塊＝目前已挖深度（顏色跟 2D 地圖的進度規則完全相同），
  灰色細框＝這個分區的預估總開挖深度。
- render_layers_3d：圖資與方量基準頁用。每個分區依各挖階疊成分層，看得出每一挖挖多深。
3D 圖只能旋轉縮放檢視；要點選分區請用 2D 地圖。
"""
import re

import plotly.graph_objects as go
import streamlit as st

from zones import local_stage_index, zone_active_in_stage, zone_progress_style

_TRIS = [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
         (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
STAGE_LAYER_COLORS = ["rgb(241,196,15)", "rgb(230,126,34)", "rgb(52,152,219)", "rgb(155,89,182)", "rgb(22,160,133)"]


def view_toggle(key):
    """2D / 3D 切換。回傳 '2D' 或 '3D'。"""
    return st.radio("地圖呈現方式", ["2D", "3D"], horizontal=True, key=key)


def _opaque(rgba):
    m = re.findall(r"[\d.]+", rgba)
    return f"rgb({m[0]},{m[1]},{m[2]})" if len(m) >= 3 else rgba


class _Boxes:
    def __init__(self):
        self.x, self.y, self.z, self.i, self.j, self.k, self.face, self.text = [], [], [], [], [], [], [], []

    def add(self, x0, x1, y0, y1, ztop, zbot, color, hover):
        base = len(self.x)
        pts = [(x0, y0, ztop), (x1, y0, ztop), (x1, y1, ztop), (x0, y1, ztop),
               (x0, y0, zbot), (x1, y0, zbot), (x1, y1, zbot), (x0, y1, zbot)]
        for px, py, pz in pts:
            self.x.append(px); self.y.append(py); self.z.append(pz); self.text.append(hover)
        for a, b, c in _TRIS:
            self.i.append(base + a); self.j.append(base + b); self.k.append(base + c); self.face.append(color)

    def trace(self):
        return go.Mesh3d(x=self.x, y=self.y, z=self.z, i=self.i, j=self.j, k=self.k, facecolor=self.face,
                         text=self.text, hoverinfo="text", flatshading=True, showscale=False,
                         lighting=dict(ambient=0.75, diffuse=0.6, specular=0.05), opacity=1.0)


def _frame_trace(frames):
    xs, ys, zs = [], [], []
    for x0, x1, y0, y1, ztop, zbot in frames:
        ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        for z in (ztop, zbot):
            for px, py in ring:
                xs.append(px); ys.append(py); zs.append(z)
            xs.append(None); ys.append(None); zs.append(None)
        for px, py in ring[:4]:
            xs += [px, px, None]; ys += [py, py, None]; zs += [ztop, zbot, None]
    return go.Scatter3d(x=xs, y=ys, z=zs, mode="lines", line=dict(color="rgba(120,120,120,0.7)", width=2), hoverinfo="skip", showlegend=False)


def _layout(fig, z_exag, title, height):
    fig.update_layout(
        title=title, height=height, margin=dict(l=0, r=0, t=40, b=0), showlegend=False,
        scene=dict(
            aspectmode="data",
            xaxis=dict(visible=False), yaxis=dict(visible=False),
            zaxis=dict(title=f"深度（放大 {z_exag:g} 倍）", showticklabels=False),
            camera=dict(eye=dict(x=1.3, y=-1.6, z=1.0)),
        ),
    )


def _zoom_slider(key):
    return st.slider("高度放大倍率（深度比平面小很多，放大才看得清楚）", 1.0, 10.0, 4.0, 0.5, key=key)


def _depth_cap(row):
    area = float(row.get("區域面積(㎡)", 0) or 0)
    th = row.get("各階累計方量", []) or []
    total = float(th[-1]) if len(th) else 0.0
    return area, total


def render_progress_3d(df_map_zones, vol_dict, stage_idx, stage_label, end_date, height=520, key="map3d_progress"):
    """3D 實挖進度：實心＝已挖深度，灰框＝預估總深度。"""
    if df_map_zones is None or df_map_zones.empty:
        st.info("目前階段沒有可顯示的分區。")
        return
    z_exag = _zoom_slider(f"{key}_zoom")
    boxes, frames, labels = _Boxes(), [], ([], [], [])
    for _, row in df_map_zones.iterrows():
        code = row["分區代號"]
        area, total = _depth_cap(row)
        if area <= 0 or total <= 0:
            continue
        cur = float(vol_dict.get(code, 0) or 0)
        thresholds = row.get("各階累計方量", [])
        text, rgba = zone_progress_style(row, cur, thresholds, stage_idx, stage_label)
        dep_now = min(cur, total) / area
        dep_tot = total / area
        frames.append((row["x_min"], row["x_max"], row["y_min"], row["y_max"], 0, -dep_tot * z_exag))
        if dep_now > 0:
            hover = f"{code}<br>{text}<br>已挖 {cur:,.0f} m³　深度約 {dep_now:.2f} m / 預估總深 {dep_tot:.2f} m"
            boxes.add(row["x_min"], row["x_max"], row["y_min"], row["y_max"], 0, -dep_now * z_exag, _opaque(rgba), hover)
        else:   # 還沒挖：畫一片薄薄的地表，才看得到位置與顏色
            boxes.add(row["x_min"], row["x_max"], row["y_min"], row["y_max"], 0, -0.15 * z_exag * 0.2, _opaque(rgba),
                      f"{code}<br>{text}<br>已挖 0 m³　預估總深 {dep_tot:.2f} m")
        labels[0].append(row["x_center"]); labels[1].append(row["y_center"]); labels[2].append(code)
    fig = go.Figure([boxes.trace(), _frame_trace(frames)])
    fig.add_trace(go.Scatter3d(x=labels[0], y=labels[1], z=[0.3] * len(labels[2]), mode="text", text=labels[2],
                               textfont=dict(size=10, color="black"), hoverinfo="skip", showlegend=False))
    _layout(fig, z_exag, f"3D 各區開挖深度 (截至 {end_date})　【{stage_label}】", height)
    st.plotly_chart(fig, use_container_width=True, key=key, config={"displayModeBar": False})
    st.caption("實心方塊＝目前已挖深度（深度＝累計實挖方量 ÷ 分區面積），灰色細框＝預估總開挖深度。可拖曳旋轉、滾輪縮放。")


def render_layers_3d(df_results, height=600, key="map3d_layers"):
    """3D 規劃分層：每個分區依各挖階疊成不同顏色的層。"""
    if df_results is None or df_results.empty:
        return
    z_exag = _zoom_slider(f"{key}_zoom")
    boxes, frames, labels = _Boxes(), [], ([], [], [])
    for _, row in df_results.iterrows():
        area = float(row.get("區域面積(㎡)", 0) or 0)
        th = row.get("各階累計方量", []) or []
        if area <= 0 or not len(th):
            continue
        code = row["分區代號"]
        for s in range(5):
            if not zone_active_in_stage(row, s):
                continue
            li = local_stage_index(row, s)
            if li < 0 or li >= len(th):
                continue
            lo = (th[li - 1] if li > 0 else 0) / area
            hi = th[li] / area
            if hi <= lo:
                continue
            boxes.add(row["x_min"], row["x_max"], row["y_min"], row["y_max"], -lo * z_exag, -hi * z_exag,
                      STAGE_LAYER_COLORS[s], f"{code}<br>第{s + 1}挖：深度 {lo:.2f}～{hi:.2f} m（厚 {hi - lo:.2f} m）")
        frames.append((row["x_min"], row["x_max"], row["y_min"], row["y_max"], 0, -th[-1] / area * z_exag))
        labels[0].append(row["x_center"]); labels[1].append(row["y_center"]); labels[2].append(code)
    fig = go.Figure([boxes.trace(), _frame_trace(frames)])
    fig.add_trace(go.Scatter3d(x=labels[0], y=labels[1], z=[0.3] * len(labels[2]), mode="text", text=labels[2],
                               textfont=dict(size=10, color="black"), hoverinfo="skip", showlegend=False))
    _layout(fig, z_exag, "3D 各區開挖分層（由上往下：第1挖→第5挖）", height)
    st.plotly_chart(fig, use_container_width=True, key=key, config={"displayModeBar": False})
    st.caption("🟨 第1挖　🟧 第2挖　🟦 第3挖　🟪 第4挖　🟩 第5挖(油槽)。可拖曳旋轉、滾輪縮放，滑鼠移到方塊上看各挖深度。")
