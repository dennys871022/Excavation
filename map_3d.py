"""3D 地圖：把各分區畫成立方體（頂面＝地表，往下＝開挖深度）。只有選到 3D 時才會執行，不影響 2D 的速度。

- render_progress_3d：出土統計儀表板用。實心方塊＝目前已挖深度（顏色跟 2D 地圖的進度規則完全相同），
  灰色細框＝這個分區的預估總開挖深度。
- render_layers_3d：圖資與方量基準頁用。每個分區依各挖階疊成分層，看得出每一挖挖多深。
3D 圖只能旋轉縮放檢視；要點選分區請用 2D 地圖。
"""
import re

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from zones import local_stage_index, zone_active_in_stage, zone_progress_style

_TRIS = [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
         (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
STAGE_LAYER_COLORS = ["rgb(241,196,15)", "rgb(230,126,34)", "rgb(52,152,219)", "rgb(155,89,182)", "rgb(22,160,133)"]


VIEW_2D, VIEW_BLOCK, VIEW_PIT = "2D", "3D 方塊", "3D 挖掘實景"


def view_toggle(key):
    """地圖呈現方式切換：2D／3D 方塊／3D 挖掘實景。"""
    return st.radio("地圖呈現方式", [VIEW_2D, VIEW_BLOCK, VIEW_PIT], horizontal=True, key=key)


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


def _layout(fig, z_exag, title, height, eye=(1.3, -1.6, 1.0)):
    fig.update_layout(
        title=title, height=height, margin=dict(l=0, r=0, t=40, b=0), showlegend=False,
        scene=dict(
            aspectmode="data",
            xaxis=dict(visible=False), yaxis=dict(visible=False),
            zaxis=dict(title=f"深度（放大 {z_exag:g} 倍）", showticklabels=False),
            camera=dict(eye=dict(x=eye[0], y=eye[1], z=eye[2])),
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


def render_pit_3d(df_zones, depth_m, color_of, hover_of, title, caption, height=600, key="map3d_pit", z_default=3.0):
    """
    3D 挖掘實景：地面是平的，各分區依實際深度「往下挖」成坑，坑壁自然形成斜面。
    做法是把整個工地取樣成高程網格（地表=0，坑底=-深度），用一張連續的地形曲面畫出來。
    depth_m：{分區代號: 深度(公尺)}；color_of：{分區代號: 'rgb(...)'}；hover_of：{分區代號: 滑鼠提示文字}。
    """
    if df_zones is None or df_zones.empty:
        st.info("目前沒有可顯示的分區。")
        return
    z_exag = st.slider("深度放大倍率（深度比平面小很多，放大才看得清楚）", 1.0, 10.0, z_default, 0.5, key=f"{key}_zoom")
    x0, x1 = float(df_zones["x_min"].min()), float(df_zones["x_max"].max())
    y0, y1 = float(df_zones["y_min"].min()), float(df_zones["y_max"].max())
    step = max(x1 - x0, y1 - y0) / 220.0
    pad = step * 8
    xs = np.arange(x0 - pad, x1 + pad + step, step)
    ys = np.arange(y0 - pad, y1 + pad + step, step)
    X, Y = np.meshgrid(xs, ys)
    Z = np.zeros_like(X)
    ground = "rgb(196,170,128)"
    palette = [ground]
    cidx = np.zeros_like(X)
    texts = np.full(X.shape, "地表（尚未開挖）", dtype=object)
    cmap = {ground: 0}
    for _, r in df_zones.iterrows():
        code = r["分區代號"]
        d = float(depth_m.get(code, 0) or 0)
        m = (X >= r["x_min"]) & (X < r["x_max"]) & (Y >= r["y_min"]) & (Y < r["y_max"])
        if d > 0:
            Z[m] = -d * z_exag
        col = color_of.get(code, ground) if d > 0 else ground
        if col not in cmap:
            cmap[col] = len(palette)
            palette.append(col)
        cidx[m] = cmap[col]
        texts[m] = hover_of.get(code, code)
    n = len(palette)
    colorscale = []
    for i, c in enumerate(palette):
        colorscale += [[i / n, c], [(i + 1) / n, c]]
    fig = go.Figure(go.Surface(
        x=xs, y=ys, z=Z, surfacecolor=cidx + 0.5, cmin=0, cmax=n, colorscale=colorscale, showscale=False,
        text=texts, hoverinfo="text",
        lighting=dict(ambient=0.7, diffuse=0.7, specular=0.05, roughness=0.9),
        contours=dict(x=dict(show=False), y=dict(show=False), z=dict(show=False)),
    ))
    # 坑底分區邊線與分區代號
    lx, ly, lz = [], [], []
    for _, r in df_zones.iterrows():
        d = float(depth_m.get(r["分區代號"], 0) or 0)
        zz = -d * z_exag + 0.02 if d > 0 else 0.02
        for px, py in [(r["x_min"], r["y_min"]), (r["x_max"], r["y_min"]), (r["x_max"], r["y_max"]), (r["x_min"], r["y_max"]), (r["x_min"], r["y_min"])]:
            lx.append(px); ly.append(py); lz.append(zz)
        lx.append(None); ly.append(None); lz.append(None)
    fig.add_trace(go.Scatter3d(x=lx, y=ly, z=lz, mode="lines", line=dict(color="rgba(60,60,60,0.55)", width=2), hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter3d(
        x=list(df_zones["x_center"]), y=list(df_zones["y_center"]),
        z=[(-float(depth_m.get(c, 0) or 0) * z_exag + 0.4) for c in df_zones["分區代號"]],
        mode="text", text=list(df_zones["分區代號"]), textfont=dict(size=9, color="black"), hoverinfo="skip", showlegend=False))
    _layout(fig, z_exag, title, height, eye=(0.9, -1.5, 2.1))   # 從較高處斜看，才看得到坑底
    st.plotly_chart(fig, use_container_width=True, key=key, config={"displayModeBar": False})
    st.caption(caption)


def render_progress_pit(df_map_zones, vol_dict, stage_idx, stage_label, end_date, key="map3d_pit_progress"):
    """出土統計儀表板：依目前實挖深度挖出來的工地實景。"""
    depth, color, hover = {}, {}, {}
    for _, row in df_map_zones.iterrows():
        code = row["分區代號"]
        area, total = _depth_cap(row)
        if area <= 0 or total <= 0:
            continue
        cur = float(vol_dict.get(code, 0) or 0)
        text, rgba = zone_progress_style(row, cur, row.get("各階累計方量", []), stage_idx, stage_label)
        depth[code] = min(cur, total) / area
        color[code] = _opaque(rgba)
        hover[code] = f"{code}<br>{text}<br>已挖 {cur:,.0f} m³　目前深度約 {depth[code]:.2f} m"
    render_pit_3d(
        df_map_zones, depth, color, hover,
        f"3D 挖掘實景 (截至 {end_date})　【{stage_label}】",
        "地面是平的，各區依目前累計實挖方量往下挖成坑（深度＝累計實挖方量 ÷ 分區面積），坑壁的斜面是自然連接，不代表實際邊坡。顏色跟 2D 地圖相同。可拖曳旋轉、滾輪縮放。",
        key=key,
    )


def render_planned_pit(df_results, key="map3d_pit_plan"):
    """圖資與方量基準頁：選「完成到第幾挖」，看全場挖到那一階段的樣子。"""
    labels = ["第1挖完成", "第2挖完成", "第3挖完成", "第4挖完成", "第5挖完成（含油槽）"]
    pick = st.select_slider("看哪個階段完成後的樣子", options=labels, value=labels[3], key=f"{key}_stage")
    upto = labels.index(pick)
    depth, color, hover = {}, {}, {}
    for _, row in df_results.iterrows():
        code = row["分區代號"]
        area = float(row.get("區域面積(㎡)", 0) or 0)
        th = row.get("各階累計方量", []) or []
        if area <= 0 or not len(th):
            continue
        last_s = None
        for s_i in range(upto + 1):
            if zone_active_in_stage(row, s_i) and 0 <= local_stage_index(row, s_i) < len(th):
                last_s = s_i
        if last_s is None:
            continue
        depth[code] = th[local_stage_index(row, last_s)] / area
        color[code] = STAGE_LAYER_COLORS[last_s]
        hover[code] = f"{code}<br>挖到第{last_s + 1}挖　深度 {depth[code]:.2f} m"
    render_pit_3d(
        df_results, depth, color, hover, f"3D 挖掘實景（規劃：{pick}）",
        "🟨 最深到第1挖　🟧 第2挖　🟦 第3挖　🟪 第4挖　🟩 第5挖(油槽)。顏色＝該區目前挖到的最後一挖。深度由各階累計方量 ÷ 分區面積算出。",
        key=key,
    )
