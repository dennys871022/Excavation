"""🗺️ 圖資與方量基準 分頁。"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from config import LOOSE_SOIL_FACTOR
from data_store import filter_active_dispatch_logs, load_sheet_data, save_sheet_data
from stage_calc import compute_stage_overview, get_stage_index
from zones import local_stage_index, zone_active_in_stage


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
        if True:  # 3D 檢視已移到「🏗️ 3D 工地管理中心」分頁
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

        # 抓目前累計出土量（排除已作廢紀錄）。派車紀錄的載運方量是鬆方，÷ 膨脹係數換成實方，才能跟圖資方量（實方）相減
        _df_logs_for_est = filter_active_dispatch_logs(load_sheet_data("dispatch_logs"))
        _dug_solid = {}
        if not _df_logs_for_est.empty and '出土分區' in _df_logs_for_est.columns and '載運方量(m³)' in _df_logs_for_est.columns:
            _tmp_est = _df_logs_for_est[_df_logs_for_est['出土分區'] != '未指定'].copy()
            _tmp_est['載運方量(m³)'] = pd.to_numeric(_tmp_est['載運方量(m³)'], errors='coerce')
            _dug_solid = (_tmp_est.groupby('出土分區')['載運方量(m³)'].sum() / LOOSE_SOIL_FACTOR).to_dict()

        st.markdown("##### ⏱️ 依出土功率預估這個範圍要挖幾天（含每日車次情境比較）")
        _est_stage_defs = [
            ("第1挖", "第1階段 (第1挖)", "第1挖方量(m³)"),
            ("第2挖", "第2階段 (第2挖)", "第2挖方量(m³)"),
            ("第3挖", "第3階段 (第3挖)", "第3挖方量(m³)"),
            ("第4挖", "第4階段 (第4挖)", "第4挖方量(m³)"),
        ]
        # 各階段實際功率（鬆方/天 → 換成實方/天）。還沒開始的階段沒有自己的功率，改用目前施工中那一挖的功率推估
        _cur_idx = get_stage_index(global_stage_choice)
        _cur_idx = 0 if _cur_idx is None or _cur_idx > 3 else _cur_idx
        _own_rate = {}
        for _i, (_label, _stage_name, _vk) in enumerate(_est_stage_defs):
            _r = 0.0
            if _i <= _cur_idx:
                try:
                    _ov = compute_stage_overview(_stage_name, df_results)
                    if _ov.get('cum_vol', 0) > 0:
                        _r = float(_ov.get('avg_vol_per_day', 0) or 0)
                except Exception:
                    _r = 0.0
            _own_rate[_i] = _r
        _base_loose = _own_rate.get(_cur_idx, 0) or next((r for r in _own_rate.values() if r > 0), 0.0)
        _base_label = _est_stage_defs[_cur_idx][0] if _own_rate.get(_cur_idx, 0) else "已施作階段"

        _left, _right = st.columns([3, 1.1])
        with _right:
            st.markdown("**🚚 每日車次情境**")
            _per_truck = st.number_input("每台載運（鬆方 m³）", min_value=1.0, max_value=30.0, value=12.0, step=0.5, key="grid_est_per_truck")
            _default_trips = max(1, int(round(_base_loose / _per_truck))) if _base_loose > 0 else 30
            _trips = st.number_input("假設之後每天幾台車", min_value=1, max_value=200, value=_default_trips, step=1, key="grid_est_trips",
                                     help=f"預設＝目前平均功率 {_base_loose:,.0f} m³/天（鬆方）÷ 每台 {_per_truck:g} m³，四捨五入")
            _scn_loose = _trips * _per_truck
            st.caption(f"每天 **{_trips} 台** ＝ {_scn_loose:,.0f} m³ 鬆方 ＝ **{_scn_loose / LOOSE_SOIL_FACTOR:,.0f} m³ 實方**")
            st.caption(f"目前平均功率：{_base_loose:,.0f} m³/天 鬆方（約 {_base_loose / _per_truck:,.1f} 台/天，取自{_base_label}）")

        _rows = []
        for _i, (_label, _stage_name, _vk) in enumerate(_est_stage_defs):
            _stage_idx_est = get_stage_index(_stage_name)
            _total_vol = float(subset[_vk].sum())
            # 剩餘只算「這一挖本身那一層」：上一挖目標 ～ 這一挖目標之間還沒挖的量，不重複算前面幾挖
            _rem_vol = 0.0
            for _, _zrow in subset.iterrows():
                if not zone_active_in_stage(_zrow, _stage_idx_est):
                    continue
                _th = _zrow.get('各階累計方量', []) or []
                _li = local_stage_index(_zrow, _stage_idx_est)
                if _li < 0 or _li >= len(_th):
                    continue
                _b0 = _th[_li - 1] if _li > 0 else 0.0
                _b1 = _th[_li]
                _dug = _dug_solid.get(_zrow['分區代號'], 0.0) or 0.0
                _rem_vol += min(max(_b1 - max(_dug, _b0), 0.0), _b1 - _b0)
            _rate_loose = _own_rate.get(_i, 0) or _base_loose
            _rate_solid = _rate_loose / LOOSE_SOIL_FACTOR if _rate_loose > 0 else 0
            _src = "本階段實際" if _own_rate.get(_i, 0) else (f"以{_base_label}推估" if _base_loose > 0 else "無工率資料")
            _scn_solid = _scn_loose / LOOSE_SOIL_FACTOR
            _rows.append({
                "階段": _label,
                "方量(實方)": _total_vol,
                "剩餘方量(實方)": _rem_vol,
                "目前功率(實方/天)": _rate_solid,
                "功率來源": _src,
                "預估總天數": _total_vol / _rate_solid if _rate_solid else None,
                "剩餘天數(目前功率)": _rem_vol / _rate_solid if _rate_solid else None,
                f"剩餘天數(每天{_trips}台)": _rem_vol / _scn_solid if _scn_solid else None,
            })
        _df_est = pd.DataFrame(_rows)
        _scn_col = f"剩餘天數(每天{_trips}台)"
        _df_est["可提早天數"] = _df_est["剩餘天數(目前功率)"] - _df_est[_scn_col]
        _sum = {"階段": "1~4挖 合計", "方量(實方)": _df_est["方量(實方)"].sum(), "剩餘方量(實方)": _df_est["剩餘方量(實方)"].sum(),
                "目前功率(實方/天)": None, "功率來源": ""}
        for _c in ["預估總天數", "剩餘天數(目前功率)", _scn_col, "可提早天數"]:
            _sum[_c] = _df_est[_c].sum() if _df_est[_c].notna().all() else None
        _df_est = pd.concat([_df_est, pd.DataFrame([_sum])], ignore_index=True)

        with _left:
            _tot = _df_est.iloc[-1]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("剩餘方量（實方）", f"{_tot['剩餘方量(實方)']:,.0f} m³")
            m2.metric("目前功率 剩餘天數", "資料不全" if pd.isna(_tot["剩餘天數(目前功率)"]) else f"{_tot['剩餘天數(目前功率)']:.1f} 天")
            m3.metric(f"每天 {_trips} 台 剩餘天數", "資料不全" if pd.isna(_tot[_scn_col]) else f"{_tot[_scn_col]:.1f} 天")
            if pd.notna(_tot["可提早天數"]):
                _d = _tot["可提早天數"]
                if abs(_d) < 0.05:
                    m4.metric("跟目前相比", "差不多")
                else:
                    m4.metric("可提早" if _d > 0 else "會延後", f"{abs(_d):.1f} 天", delta=f"{_d:+.1f} 天", delta_color="normal")
            else:
                m4.metric("可提早天數", "資料不全")
            _fmt = {c: "{:,.0f}" for c in ["方量(實方)", "剩餘方量(實方)", "目前功率(實方/天)"]}
            _fmt.update({c: "{:,.1f}" for c in ["預估總天數", "剩餘天數(目前功率)", _scn_col, "可提早天數"]})
            st.dataframe(_df_est.style.format(_fmt, na_rep="—"), use_container_width=True, hide_index=True)

            # 比較圖：各挖剩餘天數，目前功率 vs 假設車次
            _plot = _df_est.iloc[:-1]
            _fig = go.Figure()
            _fig.add_bar(x=_plot["階段"], y=_plot["剩餘天數(目前功率)"], name="目前功率", marker_color="#95A5A6",
                         text=[f"{v:.1f}" if pd.notna(v) else "" for v in _plot["剩餘天數(目前功率)"]], textposition="outside")
            _fig.add_bar(x=_plot["階段"], y=_plot[_scn_col], name=f"每天 {_trips} 台", marker_color="#3498DB",
                         text=[f"{v:.1f}" if pd.notna(v) else "" for v in _plot[_scn_col]], textposition="outside")
            _fig.update_layout(barmode="group", height=320, margin=dict(l=10, r=10, t=30, b=10), yaxis_title="剩餘天數（工作天）",
                               legend=dict(orientation="h", y=1.12))
            st.plotly_chart(_fig, use_container_width=True, key="grid_est_compare")

        st.caption(f"算法：全部用實方計算。功率＝該階段「預計開始時間」到今天的累計出土量（鬆方，含FDA）÷ 有計入工期的天數，再 ÷ {LOOSE_SOIL_FACTOR} 換成實方；"
                   "還沒開始出土的階段用目前施工中那一挖的功率推估（見「功率來源」）。「剩餘方量」只算每一挖自己那一層還沒挖的量（上一挖目標～這一挖目標之間），不會重複算前面幾挖。"
                   "天數都是「工作天」（不含沒出土的日子），假設各挖依序施作、不重疊。")
    else:
        st.info("尚未選取任何分區。請用滑鼠在上方地圖拖曳框選，或在下方清單勾選分區代號。")
