"""🗺️ 圖資與方量基準 分頁。"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from config import LOOSE_SOIL_FACTOR
from data_store import filter_active_dispatch_logs, load_sheet_data, save_sheet_data
from map_3d import render_layers_3d, view_toggle
from stage_calc import compute_stage_overview, get_stage_index
from zones import get_stage_remaining_capacity


def render(df_results, global_stage_choice):
    export_columns = ['分區代號', '區域面積(㎡)', '第1挖方量(m³)', '第2挖方量(m³)', '第3挖方量(m³)', '第4挖方量(m³)', '第5挖方量(m³)', '預估總土方']
    if st.button("🚀 推送分區資料至雲端試算表"):
        if save_sheet_data("grid_zones", df_results[export_columns]):
            st.success("分區基準已成功上傳！")

    col1, col2 = st.columns([3, 2])
    with col2:
        st.write("### 基準方量總表")
        st.dataframe(df_results[export_columns], height=600)
        st.success(f"全區預估總土方量： **{df_results['預估總土方'].sum():,.0f} m³**")

    with col1:
        st.write("### 精準網格地圖")
        _grid_view = view_toggle("map_view_grid")
        if _grid_view == "3D":
            render_layers_3d(df_results)
        else:
            fig = go.Figure()
            for idx, row in df_results.iterrows():
                fig.add_trace(go.Scatter(
                    x=[row['x_min'], row['x_max'], row['x_max'], row['x_min'], row['x_min']],
                    y=[row['y_min'], row['y_min'], row['y_max'], row['y_max'], row['y_min']],
                    mode='lines', line=dict(color='blue', width=1),
                    fill='toself', fillcolor='rgba(0, 100, 255, 0.1)', showlegend=False, hoverinfo='skip'
                ))
                fig.add_annotation(x=row['x_center'], y=row['y_center'], text=row['分區代號'], showarrow=False, font=dict(color="red", size=12))
            fig.update_layout(dragmode='pan', xaxis_title="X (m)", yaxis_title="Y (m)", yaxis=dict(scaleanchor="x", scaleratio=1), height=700, margin=dict(l=20, r=20, t=30, b=20))
            st.plotly_chart(fig, use_container_width=True, config={'scrollZoom': True, 'displayModeBar': False})

    st.divider()
    st.markdown("### 🖱️ 框選查詢範圍內各階段土方量")
    st.caption("在下方地圖用滑鼠拖曳框選想查詢的範圍（地圖右上角工具列可切換框選/套索選取），下方會即時加總範圍內所有分區的各挖階土方量。如果你部署的 Streamlit 版本不支援框選，可以改用下面的分區勾選清單。")

    select_fig = go.Figure()
    for idx, row in df_results.iterrows():
        select_fig.add_trace(go.Scatter(
            x=[row['x_min'], row['x_max'], row['x_max'], row['x_min'], row['x_min']],
            y=[row['y_min'], row['y_min'], row['y_max'], row['y_max'], row['y_min']],
            mode='lines', line=dict(color='lightblue', width=1),
            fill='toself', fillcolor='rgba(0, 100, 255, 0.08)', showlegend=False, hoverinfo='skip'
        ))
    marker_trace_index = len(df_results)  # 矩形共 len(df_results) 條 trace（索引 0 ~ N-1），中心點marker是接下來加的第 N 條
    select_fig.add_trace(go.Scatter(
        x=df_results['x_center'], y=df_results['y_center'],
        mode='markers+text',
        text=df_results['分區代號'], textposition='middle center', textfont=dict(size=9, color='black'),
        marker=dict(size=20, color='rgba(0,100,255,0.35)', line=dict(width=1, color='blue')),
        showlegend=False,
        hovertext=[f"{r['分區代號']}<br>預估總土方: {r['預估總土方']:,.0f} m³" for _, r in df_results.iterrows()],
        hoverinfo='text',
    ))
    select_fig.update_layout(
        dragmode='select',
        xaxis_title="X (m)", yaxis_title="Y (m)",
        yaxis=dict(scaleanchor="x", scaleratio=1),
        height=650, margin=dict(l=20, r=20, t=30, b=20),
    )

    box_selected_codes = []
    select_supported = True
    try:
        select_event = st.plotly_chart(
            select_fig, use_container_width=True, key="grid_box_select_chart",
            on_select="rerun", selection_mode=("box", "lasso"),
            config={'scrollZoom': True},
        )
        if select_event and select_event.get("selection", {}).get("points"):
            for p in select_event["selection"]["points"]:
                if p.get("curve_number") == marker_trace_index:
                    pi = p.get("point_index")
                    if pi is not None and pi < len(df_results):
                        box_selected_codes.append(df_results.iloc[pi]['分區代號'])
    except TypeError:
        select_supported = False
        st.plotly_chart(select_fig, use_container_width=True, config={'scrollZoom': True})
        st.warning("⚠️ 目前部署的 Streamlit 版本不支援地圖互動框選（需要較新版本），請改用下方的分區勾選清單來查詢範圍。")

    manual_codes = st.multiselect(
        "或者，直接勾選要查詢的分區（框選後這裡會自動帶入，也可以手動增減）",
        options=df_results['分區代號'].tolist(),
        default=box_selected_codes,
        key="grid_manual_select",
    )

    final_codes = manual_codes if manual_codes else box_selected_codes

    if final_codes:
        subset = df_results[df_results['分區代號'].isin(final_codes)]
        st.success(f"已選取 **{len(subset)}** 個分區：{'、'.join(final_codes)}")

        st.caption("實方（圖資算出來的原地體積）")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("第1挖方量", f"{subset['第1挖方量(m³)'].sum():,.0f} m³")
        m2.metric("第2挖方量", f"{subset['第2挖方量(m³)'].sum():,.0f} m³")
        m3.metric("第3挖方量", f"{subset['第3挖方量(m³)'].sum():,.0f} m³")
        m4.metric("第4挖方量", f"{subset['第4挖方量(m³)'].sum():,.0f} m³")
        m5.metric("預估總土方", f"{subset['預估總土方'].sum():,.0f} m³")

        st.caption(f"鬆方（實方 × {LOOSE_SOIL_FACTOR}，開挖後蓬鬆的體積）")
        l1, l2, l3, l4, l5 = st.columns(5)
        l1.metric("第1挖方量(鬆方)", f"{subset['第1挖方量(m³)'].sum() * LOOSE_SOIL_FACTOR:,.0f} m³")
        l2.metric("第2挖方量(鬆方)", f"{subset['第2挖方量(m³)'].sum() * LOOSE_SOIL_FACTOR:,.0f} m³")
        l3.metric("第3挖方量(鬆方)", f"{subset['第3挖方量(m³)'].sum() * LOOSE_SOIL_FACTOR:,.0f} m³")
        l4.metric("第4挖方量(鬆方)", f"{subset['第4挖方量(m³)'].sum() * LOOSE_SOIL_FACTOR:,.0f} m³")
        l5.metric("預估總土方(鬆方)", f"{subset['預估總土方'].sum() * LOOSE_SOIL_FACTOR:,.0f} m³")

        st.dataframe(subset[export_columns], use_container_width=True, hide_index=True)

        # 抓目前累計實挖量（排除已作廢紀錄），用來算「這個範圍扣掉已經挖的之後，還剩多少天」
        _df_logs_for_est = filter_active_dispatch_logs(load_sheet_data("dispatch_logs"))
        _excavated_dict_grid = {}
        if not _df_logs_for_est.empty and '出土分區' in _df_logs_for_est.columns and '載運方量(m³)' in _df_logs_for_est.columns:
            _tmp_est = _df_logs_for_est[_df_logs_for_est['出土分區'] != '未指定'].copy()
            _tmp_est['載運方量(m³)'] = pd.to_numeric(_tmp_est['載運方量(m³)'], errors='coerce')
            _excavated_dict_grid = _tmp_est.groupby('出土分區')['載運方量(m³)'].sum().to_dict()

        st.markdown("##### ⏱️ 依各階段目前的平均出土功率，預估這個範圍要挖幾天")
        st.caption("平均出土功率抓自「階段管控頁」各階段的即時總覽（累積出土量 ÷ 目前作業工期），會隨每天的派車紀錄自動更新。某階段還沒有任何出土紀錄時無法估算天數，會顯示「尚無工率資料」。「預估總天數」是從0開始算的完整工期；「剩餘天數」則是扣掉這個範圍目前已經挖掉的量之後，還要多久才能完工。")

        _est_stage_defs = [
            ("第1挖", "第1階段 (第1挖)", "第1挖方量(m³)"),
            ("第2挖", "第2階段 (第2挖)", "第2挖方量(m³)"),
            ("第3挖", "第3階段 (第3挖)", "第3挖方量(m³)"),
            ("第4挖", "第4階段 (第4挖)", "第4挖方量(m³)"),
        ]

        st.caption("預估總天數（從0開始，未扣除已出土量）")
        _est_cols = st.columns(5)
        _total_days = 0.0
        _total_has_estimate = True
        _rates_by_stage = {}
        for _col, (_label, _stage_name, _vol_key) in zip(_est_cols[:4], _est_stage_defs):
            _stage_vol = subset[_vol_key].sum()
            try:
                _ov = compute_stage_overview(_stage_name, df_results)
                _rate = _ov.get('avg_vol_per_day', 0)
            except Exception:
                _rate = 0
            _rates_by_stage[_stage_name] = _rate
            if _rate and _rate > 0:
                _days = _stage_vol / _rate
                _total_days += _days
                _col.metric(f"{_label} 預估天數", f"{_days:.1f} 天", help=f"{_stage_vol:,.0f} m³ ÷ 目前平均功率 {_rate:,.1f} m³/天")
            else:
                _total_has_estimate = False
                _col.metric(f"{_label} 預估天數", "尚無工率資料")
        with _est_cols[4]:
            if _total_has_estimate:
                st.metric("1~4挖 總計預估天數", f"{_total_days:.1f} 天", help="假設各階段依序施作（不重疊），四階段天數加總")
            else:
                st.metric("1~4挖 總計預估天數", "資料不全")

        st.caption("剩餘天數（扣除這個範圍已出土的量，只計算還沒挖到目標的部分）")
        _rem_cols = st.columns(5)
        _total_rem_days = 0.0
        _total_rem_has_estimate = True
        for _col, (_label, _stage_name, _vol_key) in zip(_rem_cols[:4], _est_stage_defs):
            _stage_idx_est = get_stage_index(_stage_name)
            _remaining_vol = 0.0
            for _, _zrow in subset.iterrows():
                _cap = get_stage_remaining_capacity(_zrow['分區代號'], df_results, _stage_idx_est, _excavated_dict_grid)
                if _cap is not None:
                    _remaining_vol += _cap
            _rate = _rates_by_stage.get(_stage_name, 0)
            if _remaining_vol <= 0:
                _col.metric(f"{_label} 剩餘天數", "已完成", help="這個範圍在此階段的目標已經全部挖完")
            elif _rate and _rate > 0:
                _rem_days = _remaining_vol / _rate
                _total_rem_days += _rem_days
                _col.metric(f"{_label} 剩餘天數", f"{_rem_days:.1f} 天", help=f"剩餘 {_remaining_vol:,.0f} m³ ÷ 目前平均功率 {_rate:,.1f} m³/天")
            else:
                _total_rem_has_estimate = False
                _col.metric(f"{_label} 剩餘天數", "尚無工率資料")
        with _rem_cols[4]:
            if _total_rem_has_estimate:
                st.metric("1~4挖 總計剩餘天數", f"{_total_rem_days:.1f} 天", help="扣除已出土量後，假設各階段依序施作，四階段剩餘天數加總")
            else:
                st.metric("1~4挖 總計剩餘天數", "資料不全")
    else:
        st.info("尚未選取任何分區。請用滑鼠在上方地圖拖曳框選，或在下方清單勾選分區代號。")
