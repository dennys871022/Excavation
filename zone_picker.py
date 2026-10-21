"""用地圖點選出土分區（台北港批量指派、FDA 登記共用）。

用法：在 multiselect 之前呼叫 zone_map_picker(...)，multiselect 用同一個 key。
地圖上的選取會同步到 multiselect；multiselect 手動增減也會反映回地圖的高亮。
"""
import plotly.graph_objects as go
import streamlit as st

from zones import get_stage_remaining_capacity

COLOR_OPEN = "#2b7de9"      # 本階段還有可挖量
COLOR_FULL = "#2e9e5b"      # 本階段已達標（剩餘 0）
COLOR_IDLE = "#b0b7c3"      # 本階段無開挖目標
COLOR_PICK = "#ff8c00"      # 已選取


def _status(code, df_results, stage_idx, excavated_dict):
    cap = get_stage_remaining_capacity(code, df_results, stage_idx, excavated_dict) if stage_idx is not None else None
    if stage_idx is None:
        return COLOR_OPEN, "不限階段"
    if cap is None:
        return COLOR_IDLE, "本階段無開挖目標"
    if cap <= 0:
        return COLOR_FULL, "本階段已達標"
    return COLOR_OPEN, f"本階段剩餘 {cap:,.0f} m³"


def zone_map_picker(df_results, target_key, stage_idx=None, excavated_dict=None, chart_key=None, height=420):
    """畫出可點選的分區地圖，並把選取結果寫入 st.session_state[target_key]（需在對應 multiselect 之前呼叫）。"""
    if df_results is None or df_results.empty:
        return
    excavated_dict = excavated_dict or {}
    chart_key = chart_key or f"{target_key}_map"
    ver_key = f"{chart_key}_ver"
    sig_key = f"{chart_key}_sig"
    st.session_state.setdefault(ver_key, 0)

    codes = df_results["分區代號"].tolist()
    current = [c for c in st.session_state.get(target_key, []) if c in codes]
    stat = [_status(c, df_results, stage_idx, excavated_dict) for c in codes]

    fig = go.Figure()
    for (_, r), (color, _txt) in zip(df_results.iterrows(), stat):
        fig.add_shape(type="rect", x0=r["x_min"], x1=r["x_max"], y0=r["y_min"], y1=r["y_max"],
                      line=dict(color=color, width=1), fillcolor=color, opacity=0.25, layer="below")
    fig.add_trace(go.Scatter(
        x=df_results["x_center"], y=df_results["y_center"], mode="markers+text",
        text=codes, textposition="middle center", textfont=dict(size=9, color="black"),
        marker=dict(size=24, color=[s[0] for s in stat], opacity=0.55, line=dict(width=1, color="white")),
        selected=dict(marker=dict(color=COLOR_PICK, opacity=1.0, size=26)),
        unselected=dict(marker=dict(opacity=0.45)),
        selectedpoints=[codes.index(c) for c in current] if current else None,
        hovertext=[f"{c}<br>{s[1]}" for c, s in zip(codes, stat)], hoverinfo="text", showlegend=False,
    ))
    fig.update_layout(
        dragmode="select", height=height, margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(visible=False), yaxis=dict(visible=False, scaleanchor="x", scaleratio=1),
    )

    picked = None
    try:
        ev = st.plotly_chart(
            fig, use_container_width=True, key=f"{chart_key}_{st.session_state[ver_key]}",
            on_select="rerun", selection_mode=("points", "box", "lasso"),
            config={"scrollZoom": True, "displayModeBar": False},
        )
        pts = (ev or {}).get("selection", {}).get("points", []) if ev else []
        picked = [codes[p["point_index"]] for p in pts
                  if p.get("curve_number") == 0 and p.get("point_index") is not None and p["point_index"] < len(codes)]
    except TypeError:
        st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})
        st.caption("⚠️ 目前的 Streamlit 版本不支援地圖點選，請改用下方清單選擇分區。")

    # 只有「地圖選取內容真的變了」才覆蓋清單，避免蓋掉使用者在清單上的手動調整
    sig = tuple(sorted(picked)) if picked is not None else None
    if picked is not None and sig != st.session_state.get(sig_key):
        st.session_state[sig_key] = sig
        keep = [c for c in st.session_state.get(target_key, []) if c not in codes]   # 例如「開挖前土方」
        st.session_state[target_key] = keep + [c for c in codes if c in picked]
        # 不需要 st.rerun()：這裡在 multiselect 建立之前就改好 session_state，同一輪畫面就會帶入

    c1, c2 = st.columns([3, 1])
    with c1:
        st.caption("🖱️ 點一下分區選取，按住 Shift 再點可加選，也可拖曳框選。"
                   "　🟦 本階段還有可挖量　🟩 本階段已達標　⬜ 本階段無開挖目標　🟧 已選取")
    with c2:
        if st.button("清除地圖選取", key=f"{chart_key}_clear", use_container_width=True):
            st.session_state[target_key] = [c for c in st.session_state.get(target_key, []) if c not in codes]
            st.session_state[sig_key] = ()
            st.session_state[ver_key] += 1
            st.rerun()
