"""📊 出土統計儀表板 分頁。"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from datetime import datetime
from datetime import timedelta
from data_store import _is_voided, filter_active_dispatch_logs, load_sheet_data, save_sheet_data
from pdf_reports import generate_backend_map, generate_daily_report_pdf
from stage_calc import compute_stage_overview, get_stage_index, sync_stage_daily_log
from zone_picker import zone_map_picker
from map_3d import VIEW_ANIM, VIEW_BLOCK, VIEW_PIT, VIEW_RENDER, render_dig_animation, render_progress_3d, render_progress_pit, view_toggle
from zones import allocate_trucks_by_capacity, filter_zones_for_stage, get_stage_remaining_capacity, zone_progress_style


def render(df_results, global_stage_choice):
    st.write("### 📊 雲端出土統計儀表板")

    df_logs = load_sheet_data("dispatch_logs")
    if not df_logs.empty:
        df_logs['orig_index'] = df_logs.index

    tw_today = (datetime.utcnow() + timedelta(hours=8)).date()
    st.markdown("#### 📅 篩選統計時間區間")
    date_selection = st.date_input("選擇區間 (單選一日或拖曳選擇範圍)：", value=(tw_today, tw_today))

    if isinstance(date_selection, tuple) and len(date_selection) == 2:
        start_date, end_date = date_selection
    elif isinstance(date_selection, tuple) and len(date_selection) == 1:
        start_date = end_date = date_selection[0]
    else:
        start_date = end_date = date_selection

    delta_days = (end_date - start_date).days
    if delta_days == 0:
        period_label = "本日"
    elif start_date.weekday() == 0 and end_date.weekday() == 6 and delta_days == 6:
        period_label = "本週"
    elif start_date.day == 1 and (end_date + timedelta(days=1)).day == 1 and start_date.month == end_date.month:
        period_label = "本月"
    else:
        period_label = "本區間"

    # PDF 產出用暫存變數
    pdf_report_text = ""
    pdf_breakdown_text = ""
    pdf_display_df = pd.DataFrame()

    if not df_logs.empty and "日期" in df_logs.columns:
        if "聯單序號" not in df_logs.columns:
            df_logs["聯單序號"] = ""
        if "作廢" not in df_logs.columns:
            df_logs["作廢"] = False
        if "作廢原因" not in df_logs.columns:
            df_logs["作廢原因"] = ""
        if "運送目的地" not in df_logs.columns:
            df_logs["運送目的地"] = "台北港"
        else:
            df_logs["運送目的地"] = df_logs["運送目的地"].fillna("台北港").replace("", "台北港")
        if "車次數" not in df_logs.columns:
            df_logs["車次數"] = 1
        else:
            df_logs["車次數"] = pd.to_numeric(df_logs["車次數"], errors='coerce').fillna(1)

        df_logs['ParsedDate'] = pd.to_datetime(df_logs['日期']).dt.date
        # valid_logs 是統計計算用的資料，會排除已標記「作廢」的紀錄（例如爆胎超時被拒收、原車返回的那種）
        # df_logs 本身維持完整原始資料（含作廢列），供下面「檢視所有歷史紀錄」查閱與編輯
        valid_logs = filter_active_dispatch_logs(df_logs)

        if not valid_logs.empty and '時間' in valid_logs.columns:
            valid_logs = valid_logs.sort_values(['車頭車號', '日期', '時間'])

        range_logs = valid_logs[(valid_logs['ParsedDate'] >= start_date) & (valid_logs['ParsedDate'] <= end_date)]
        cumul_logs = valid_logs[valid_logs['ParsedDate'] <= end_date].copy()

        # 車次/車頭數只算台北港正式聯單的車，FDA的車次不併進來（FDA另外在「FDA土方交換」那兩行獨立統計）。
        # 方量（本期出土方量、累計實挖方量）則仍含FDA，因為FDA挖出來的土一樣算開挖進度。
        def _exclude_fda(_df):
            if '運送目的地' in _df.columns:
                return _df[_df['運送目的地'].fillna('台北港').astype(str) != 'FDA']
            return _df
        range_logs_official = _exclude_fda(range_logs)
        cumul_logs_official = _exclude_fda(cumul_logs)

        range_trucks = range_logs_official['車頭車號'].nunique() if '車頭車號' in range_logs_official.columns else 0
        # 車次用加總「車次數」欄位，不是單純數列數（一列不一定只代表一台車）
        range_trips = pd.to_numeric(range_logs_official['車次數'], errors='coerce').fillna(1).sum() if '車次數' in range_logs_official.columns else len(range_logs_official)
        range_vol = pd.to_numeric(range_logs['載運方量(m³)'], errors='coerce').sum() if '載運方量(m³)' in range_logs.columns else 0
        total_all_trips = pd.to_numeric(cumul_logs_official['車次數'], errors='coerce').fillna(1).sum() if '車次數' in cumul_logs_official.columns else len(cumul_logs_official)

        period_days = range_logs['ParsedDate'].nunique() if not range_logs.empty and 'ParsedDate' in range_logs.columns else 0
        period_rate = round(range_trips / period_days, 1) if period_days > 0 else 0

        # 累計總天數：改用日曆天數（從第一筆出土紀錄那天算到統計結束日，含中間沒出土的日期），
        # 跟「階段總覽」的「目前作業工期」算法一致，不再只算「有出土紀錄」的天數
        if not cumul_logs.empty and 'ParsedDate' in cumul_logs.columns:
            earliest_log_date = cumul_logs['ParsedDate'].min()
            total_days = (end_date - earliest_log_date).days + 1
        else:
            total_days = 0
        total_rate = round(total_all_trips / total_days, 1) if total_days > 0 else 0

        st.markdown(f"#### 📊 {period_label}統計結果 ({start_date} 至 {end_date})")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric(f"{period_label}出車車頭數", f"{range_trucks} 輛")
        m2.metric(f"{period_label}總車次", f"{range_trips} 台")
        m3.metric(f"{period_label}實挖方量", f"{range_vol:,.0f} m³")
        m4.metric(f"{period_label}出土功率", f"{period_rate} 台/天")
        m5.metric("總出土功率", f"{total_rate} 台/天")
        st.divider()

        zone_grouped = pd.DataFrame()

        display_df = pd.DataFrame()
        if '出土分區' in cumul_logs.columns and '載運方量(m³)' in cumul_logs.columns:
            df_assigned = cumul_logs[cumul_logs['出土分區'] != '未指定'].copy()
            if not df_assigned.empty:
                df_assigned['載運方量(m³)'] = pd.to_numeric(df_assigned['載運方量(m³)'], errors='coerce')
                zone_grouped = df_assigned.groupby('出土分區')['載運方量(m³)'].sum().reset_index()
                zone_grouped.rename(columns={'載運方量(m³)': '累計實挖方量'}, inplace=True)

                baseline_dict = df_results.set_index('分區代號')['預估總土方'].to_dict() if not df_results.empty else {}
                zone_grouped['預估基準方量'] = zone_grouped['出土分區'].map(baseline_dict)
                zone_grouped['完成率數值'] = (zone_grouped['累計實挖方量'] / zone_grouped['預估基準方量'] * 100).round(1)

                zone_grouped['累計實挖方量_顯示'] = zone_grouped['累計實挖方量'].apply(lambda x: f"{x:,.0f}")
                zone_grouped['預估基準方量_顯示'] = zone_grouped['預估基準方量'].apply(lambda x: f"{x:,.0f}" if pd.notnull(x) else '無基準量')
                zone_grouped['完成率_顯示'] = zone_grouped['完成率數值'].fillna('不適用').astype(str)
                zone_grouped['完成率_顯示'] = zone_grouped['完成率_顯示'].apply(lambda x: f"{x}%" if x != '不適用' else x)

                display_df = zone_grouped[['出土分區', '累計實挖方量_顯示', '預估基準方量_顯示', '完成率_顯示']].rename(
                    columns={'累計實挖方量_顯示': '累計實挖方量', '預估基準方量_顯示': '預估基準方量', '完成率_顯示': '完成率(%)'}
                )

        st.markdown(f"#### 📱 {period_label}回報")

        total_excavated = zone_grouped[zone_grouped['出土分區'] != '開挖前土方']['累計實挖方量'].sum() if not zone_grouped.empty else 0
        pre_excavated = zone_grouped[zone_grouped['出土分區'] == '開挖前土方']['累計實挖方量'].sum() if not zone_grouped.empty and '開挖前土方' in zone_grouped['出土分區'].values else 0

        manifest_breakdown_str = ""
        if '聯單序號' in range_logs.columns and '載運方量(m³)' in range_logs.columns:
            range_serials = range_logs['聯單序號'].fillna('').astype(str).str.strip().str.upper()
            range_vols = pd.to_numeric(range_logs['載運方量(m³)'], errors='coerce').fillna(0)

            m_types = ["B1", "B2-3", "B4", "B5"]
            breakdown_lines = []
            for m_type in m_types:
                mask = range_serials.str.contains(m_type.upper(), regex=False)
                m_trips = mask.sum()
                m_vol = range_vols[mask].sum()
                breakdown_lines.append(f"* {m_type} 聯單： {m_trips} 台 / {m_vol:,.0f} m³")
            manifest_breakdown_str = "\n".join(breakdown_lines)

        manifest_total = 79692.0
        overall_rate = round((total_excavated / manifest_total * 100), 1) if manifest_total > 0 else 0

        # FDA土方交換統計：本期（跟上面車次/方量同一個日期區間）與累積兩種，開挖前土方另計（跟一般的累計實挖方量同樣的呈現方式）
        _fda_col = '運送目的地'
        fda_range_logs = range_logs[range_logs[_fda_col] == 'FDA'] if _fda_col in range_logs.columns else range_logs.iloc[0:0]
        period_fda_trips = pd.to_numeric(fda_range_logs['車次數'], errors='coerce').fillna(1).sum() if not fda_range_logs.empty else 0
        period_fda_vol = pd.to_numeric(fda_range_logs['載運方量(m³)'], errors='coerce').fillna(0).sum() if not fda_range_logs.empty else 0

        fda_cumul_logs = cumul_logs[cumul_logs[_fda_col] == 'FDA'] if _fda_col in cumul_logs.columns else cumul_logs.iloc[0:0]
        cumul_fda_trips = pd.to_numeric(fda_cumul_logs['車次數'], errors='coerce').fillna(1).sum() if not fda_cumul_logs.empty else 0
        cumul_fda_vol = pd.to_numeric(fda_cumul_logs['載運方量(m³)'], errors='coerce').fillna(0).sum() if not fda_cumul_logs.empty else 0
        cumul_fda_pre_vol = pd.to_numeric(
            fda_cumul_logs[fda_cumul_logs['出土分區'] == '開挖前土方']['載運方量(m³)'], errors='coerce'
        ).fillna(0).sum() if not fda_cumul_logs.empty else 0

        report_text_left = f"""【CDC土方開挖{period_label}回報】 區間: {start_date} 至 {end_date}
{period_label}出土天數： {period_days} 天
{period_label}車次： {range_trips} 台
{period_label}出土功率： {period_rate} 台/天
{period_label}出土方量： {range_vol:,.0f} m³
累計總天數： {total_days} 天
累計總車次： {total_all_trips} 台
總出土功率： {total_rate} 台/天
累計實挖方量： {total_excavated:,.0f} m³ (另計開挖前土方: {pre_excavated:,.0f} m³)
聯單預估總出土： {manifest_total:,.0f} m³
總體開挖進度： {overall_rate}%
{period_label}FDA土方交換： {period_fda_trips:.0f} 台 / {period_fda_vol:,.0f} m³
累積FDA土方交換： {cumul_fda_trips:.0f} 台 / {cumul_fda_vol:,.0f} m³ (另計開挖前土方: {cumul_fda_pre_vol:,.0f} m³)"""

        report_text_right = f"區間聯單分類出土：\n{manifest_breakdown_str}"
        ui_display_text = f"{report_text_left}\n\n{report_text_right}"

        # 保留給 PDF 匯出使用
        pdf_report_text = report_text_left
        pdf_breakdown_text = manifest_breakdown_str
        pdf_display_df = display_df

        col_txt, col_fig = st.columns([1, 2])
        with col_txt:
            st.info(ui_display_text.replace("\n", "\n\n"))

        with col_fig:
            _stats_stage_idx = get_stage_index(global_stage_choice)
            if _stats_stage_idx is not None:
                st.markdown(f"**進度圖例說明：（依目前作業階段【{global_stage_choice}】上色）**")
                st.markdown("⬜ 尚未開始 🟧 進行中 🟩 已完成")
            else:
                st.markdown("**進度圖例說明：（開挖前土方無分區門檻，顯示總體4階累計完成度）**")
                st.markdown("⬜ 尚未開挖 🟨 1挖進行中 🟧 1挖完成/2挖進行中 🟦 2挖完成/3挖進行中 🟪 3挖完成/4挖進行中 🟩 開挖完成")
            fig_map = go.Figure()
            if not df_results.empty:
                vol_dict = {}
                if not zone_grouped.empty:
                    vol_dict = zone_grouped.set_index('出土分區')['累計實挖方量'].to_dict()

                stage_dict = df_results.set_index('分區代號')['各階累計方量'].to_dict()

                # 依每個分區實際算出來的各階方量動態判斷，該階段沒有開挖的分區（例如滯洪池只到第3挖、
                # 油槽只有第5挖）地圖上會自動被濾掉，不用再寫死判斷是不是滯洪池
                if _stats_stage_idx is not None:
                    df_map_zones = filter_zones_for_stage(df_results, _stats_stage_idx)
                else:
                    df_map_zones = df_results

                _map_view = view_toggle("map_view_stats", with_anim=True)
                if _map_view == VIEW_BLOCK:
                    render_progress_3d(df_map_zones, vol_dict, _stats_stage_idx, global_stage_choice, end_date)
                elif _map_view == VIEW_ANIM:
                    render_dig_animation(cumul_logs, df_map_zones, _stats_stage_idx, global_stage_choice, end_date)
                elif _map_view in (VIEW_PIT, VIEW_RENDER):
                    render_progress_pit(df_map_zones, vol_dict, _stats_stage_idx, global_stage_choice, end_date, realistic=(_map_view == VIEW_RENDER))
                else:
                    for idx, row in df_map_zones.iterrows():
                        grid_id = row['分區代號']
                        current_vol = vol_dict.get(grid_id, 0)
                        thresholds = stage_dict.get(grid_id, [])

                        stage_text, fill_color = zone_progress_style(row, current_vol, thresholds, _stats_stage_idx, global_stage_choice)

                        fig_map.add_trace(go.Scatter(
                            x=[row['x_min'], row['x_max'], row['x_max'], row['x_min'], row['x_min']],
                            y=[row['y_min'], row['y_min'], row['y_max'], row['y_max'], row['y_min']],
                            mode='lines', line=dict(color='gray', width=1),
                            fill='toself', fillcolor=fill_color, showlegend=False, hoverinfo='text',
                            text=f"{grid_id}<br>{stage_text}<br>已挖: {current_vol:,.0f} m³"
                        ))
                        fig_map.add_annotation(x=row['x_center'], y=row['y_center'], text=grid_id, showarrow=False, font=dict(color="black", size=10))

                    fig_map.update_layout(title=f"各區階數開挖狀態 (截至 {end_date})　【{global_stage_choice}】", dragmode='pan', xaxis_title="", yaxis_title="", yaxis=dict(scaleanchor="x", scaleratio=1), height=500, margin=dict(l=0, r=0, t=30, b=0))
                    st.plotly_chart(fig_map, use_container_width=True, config={'displayModeBar': False})

        st.divider()

        # ====================================================================
        # 新增：每日出土 PDF 報表匯出按鍵
        # ====================================================================
        st.markdown(f"#### 📄 匯出每日出土 PDF 報表　（目前作業階段：**{global_stage_choice}**，可於左側側邊欄切換）")
        if st.button("📥 一鍵產出PDF報表", key="btn_daily_pdf", use_container_width=True):
            try:
                with st.spinner("PDF 產生中，請稍候..."):
                    map_img_path = generate_backend_map(df_results, zone_grouped, stage_choice=global_stage_choice) if not df_results.empty else None
                    stage_overview_for_pdf = compute_stage_overview(global_stage_choice, df_results)
                    daily_control_cols = ['日期', '內控預計車次', '實際車次', '差異', '累積差異', '當日運棄量', '累計運棄量', '剩餘土方量', '備註']
                    _pdf_df_range = stage_overview_for_pdf["df_range"]
                    _pdf_df_range_display = _pdf_df_range[(_pdf_df_range['當日運棄量'] > 0) | (_pdf_df_range['計入工期'] == True)]
                    daily_control_for_pdf = _pdf_df_range_display[daily_control_cols].copy()

                    if get_stage_index(global_stage_choice) is not None:
                        pdf_legend_items = [
                            ("尚未開始", "#F0F0F0"),
                            ("進行中", "#E67E22"),
                            ("已完成", "#2ECC71"),
                        ]
                    else:
                        pdf_legend_items = [
                            ("尚未開挖", "#F0F0F0"),
                            ("1挖進行中", "#F1C40F"),
                            ("1挖完成/2挖進行中", "#E67E22"),
                            ("2挖完成/3挖進行中", "#3498DB"),
                            ("3挖完成/4挖進行中", "#9B59B6"),
                            ("開挖完成", "#2ECC71"),
                        ]

                    pdf_path = generate_daily_report_pdf(
                        report_text=pdf_report_text,
                        breakdown_text=pdf_breakdown_text,
                        display_df=pdf_display_df,
                        map_img_path=map_img_path,
                        period_label=period_label,
                        start_date=start_date,
                        end_date=end_date,
                        stage_overview=None,
                        daily_control_df=daily_control_for_pdf,
                        fda_daily_df=stage_overview_for_pdf.get("df_fda_daily"),
                        map_legend_items=pdf_legend_items,
                        stage_label=global_stage_choice,
                    )
                with open(pdf_path, "rb") as f:
                    st.download_button(
                        label=f"✅ 點此下載 {period_label}出土報表 ({start_date}~{end_date}).pdf",
                        data=f,
                        file_name=f"daily_excavation_report_{start_date}_{end_date}.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                    )
            except Exception as ex:
                st.error(f"PDF 匯出失敗：{ex}")

        st.divider()

        st.markdown("#### 🔄 FDA土方交換（無電子聯單，簡化輸入）")
        st.caption("隔壁工地FDA沒有電子聯單，這裡用簡化方式登記：填當天總台數、總方量，分配到的分區可以複選。按新增後會直接併入派車紀錄，自動算進累計實挖方量、地圖進度、完成率（視同跟台北港的量合併計算），也會同步顯示在下面「本日回報」的FDA土方交換統計裡。")

        fda_col1, fda_col2, fda_col3 = st.columns([1, 1, 1])
        with fda_col1:
            fda_date = st.date_input("交換日期", value=tw_today, key="fda_entry_date")
        with fda_col2:
            fda_trucks = st.number_input("總台數", min_value=1, value=1, step=1, key="fda_entry_trucks")
        with fda_col3:
            fda_vol = st.number_input("總方量 (m³)", min_value=0.0, value=12.0, step=1.0, key="fda_entry_vol")

        fda_zone_options = ["開挖前土方"] + (df_results["分區代號"].tolist() if not df_results.empty else [])
        with st.expander("🗺️ 用地圖點選分區", expanded=True):
            zone_map_picker(
                df_results, "fda_entry_zones", get_stage_index(global_stage_choice),
                zone_grouped.set_index('出土分區')['累計實挖方量'].to_dict() if not zone_grouped.empty else {},
                chart_key="fda_zone_map",
            )
        fda_zones = st.multiselect(
            "分配到分區（可複選；選多個時依剩餘容量自動分配，某區達到本階段目標後自動跳過、改分給其他區，邏輯跟下面「批量設定出土分區」一樣）",
            options=fda_zone_options, key="fda_entry_zones",
        )
        fda_note = st.text_input("備註（選填）", value="", key="fda_entry_note", placeholder="例如：FDA工地土方交換")

        # 顯示選定分區在本階段的剩餘容量，跟批量設定出土分區那邊共用同一套參考資訊
        _fda_cap_stage_idx = get_stage_index(global_stage_choice)
        _fda_excavated_dict = zone_grouped.set_index('出土分區')['累計實挖方量'].to_dict() if not zone_grouped.empty else {}
        if fda_zones and _fda_cap_stage_idx is not None:
            fda_cap_rows = []
            for z in fda_zones:
                cap = get_stage_remaining_capacity(z, df_results, _fda_cap_stage_idx, _fda_excavated_dict)
                fda_cap_rows.append({
                    "分區": z,
                    f"【{global_stage_choice}】剩餘可挖 (m³)": f"{cap:,.0f}" if cap is not None else "無上限（本階段無目標）",
                })
            st.caption(f"📏 目前選定分區在【{global_stage_choice}】的剩餘容量：")
            st.dataframe(pd.DataFrame(fda_cap_rows), use_container_width=True, hide_index=True)

        if st.button("➕ 新增這筆FDA土方交換紀錄", key="fda_entry_submit"):
            if not fda_zones:
                st.error("請至少選擇一個分區")
            else:
                fda_raw_logs = load_sheet_data("dispatch_logs")
                if fda_raw_logs.empty:
                    fda_raw_logs = pd.DataFrame(columns=["日期", "時間", "車頭車號", "出土分區", "載運方量(m³)", "備註", "聯單序號", "作廢", "作廢原因", "運送目的地", "車次數"])
                for _col, _default in [("作廢", False), ("作廢原因", ""), ("運送目的地", "台北港"), ("車次數", 1), ("聯單序號", "")]:
                    if _col not in fda_raw_logs.columns:
                        fda_raw_logs[_col] = _default

                # 把「總台數+總方量」拆成一台一台的方量清單，交給跟批量設定出土分區同一套容量感知分配演算法，
                # 完成的分區會自動跳過，再把分配結果依分區彙總回集計列（維持FDA簡化輸入的精神，不逐台存成一列）
                per_truck_vol = fda_vol / fda_trucks if fda_trucks > 0 else 0
                truck_vol_list = [per_truck_vol] * int(fda_trucks)

                if len(fda_zones) == 1:
                    assigned_list = [fda_zones[0]] * len(truck_vol_list)
                else:
                    assigned_list, _ = allocate_trucks_by_capacity(
                        truck_vol_list, fda_zones, df_results, _fda_cap_stage_idx, _fda_excavated_dict
                    )

                from collections import defaultdict
                zone_agg = defaultdict(lambda: [0, 0.0])
                unassigned_trucks = 0
                unassigned_vol = 0.0
                for z, v in zip(assigned_list, truck_vol_list):
                    if z is None:
                        unassigned_trucks += 1
                        unassigned_vol += v
                    else:
                        zone_agg[z][0] += 1
                        zone_agg[z][1] += v

                new_fda_rows = []
                for z, (trucks, vol) in zone_agg.items():
                    new_fda_rows.append({
                        "日期": fda_date.strftime("%Y-%m-%d"),
                        "時間": "00:00:00",
                        "車頭車號": f"FDA集計-{fda_date.strftime('%Y%m%d')}-{z}",
                        "出土分區": z,
                        "載運方量(m³)": round(vol, 1),
                        "備註": (f"FDA土方交換" + (f"：{fda_note}" if fda_note else "")),
                        "聯單序號": "",
                        "作廢": False,
                        "作廢原因": "",
                        "運送目的地": "FDA",
                        "車次數": trucks,
                    })

                if new_fda_rows:
                    fda_raw_logs = pd.concat([fda_raw_logs, pd.DataFrame(new_fda_rows)], ignore_index=True)
                    if save_sheet_data("dispatch_logs", fda_raw_logs):
                        sync_stage_daily_log(global_stage_choice, df_results)
                        breakdown_str = "、".join([f"{z}：{int(t)}台/{v:,.0f}m³" for z, (t, v) in zone_agg.items()])
                        st.success(f"已新增：{fda_date.strftime('%Y-%m-%d')} FDA土方交換，{breakdown_str}")
                        if unassigned_trucks > 0:
                            st.warning(f"⚠️ 有 **{unassigned_trucks} 台**（約 {unassigned_vol:,.0f} m³）沒有分配出去，因為所選分區在【{global_stage_choice}】的目標都已經填滿了，請自行調整分區或接受超挖後重新輸入。")
                        st.rerun()
                else:
                    st.error("所選分區在這個階段的目標都已經完成，沒有任何容量可以分配，請改選其他分區。")

        st.divider()

        st.markdown("#### ⚙️ 批量設定出土分區")
        df_unassigned = valid_logs[valid_logs['出土分區'] == '未指定'].copy()
        if not df_unassigned.empty:
            st.info(f"尚有 {len(df_unassigned)} 筆有效紀錄未指定分區。")

            unassigned_dates = sorted(df_unassigned['日期'].unique(), reverse=True) if '日期' in df_unassigned.columns else []
            selected_dates = st.multiselect(
                "依日期篩選要處理的紀錄（不選＝顯示全部日期）",
                options=unassigned_dates,
            )
            if selected_dates:
                df_unassigned = df_unassigned[df_unassigned['日期'].isin(selected_dates)]

            select_all = st.checkbox(f"☑️ 一鍵全選（{'篩選出的' if selected_dates else '全部'} {len(df_unassigned)} 筆）", value=False)
            df_unassigned.insert(0, '勾選', select_all)

            edited_unassigned = st.data_editor(df_unassigned, hide_index=True, column_config={"勾選": st.column_config.CheckboxColumn(required=True)})

            checked_count = int((edited_unassigned['勾選'] == True).sum())
            checked_vol = pd.to_numeric(
                edited_unassigned.loc[edited_unassigned['勾選'] == True, '載運方量(m³)'], errors='coerce'
            ).fillna(0).sum() if '載運方量(m³)' in edited_unassigned.columns else 0
            st.caption(f"✅ 目前已勾選 **{checked_count}** 筆，合計約 **{checked_vol:,.0f} m³**")

            zone_list = df_results["分區代號"].tolist() if not df_results.empty else []
            with st.expander("🗺️ 用地圖點選分區", expanded=True):
                zone_map_picker(
                    df_results, "bulk_zone_select", get_stage_index(global_stage_choice),
                    zone_grouped.set_index('出土分區')['累計實挖方量'].to_dict() if not zone_grouped.empty else {},
                    chart_key="bulk_zone_map",
                )
            col_z1, col_z2 = st.columns([2, 1])
            with col_z1:
                selected_zones = st.multiselect(
                    "選擇要套用的分區（可複選；選多個時會輪流分配，但某一區達到本階段開挖目標後就自動跳過，剩下的車次繼續分給還有空間的分區）",
                    options=["開挖前土方"] + zone_list,
                    key="bulk_zone_select",
                )
            with col_z2:
                st.write("")
                st.write("")
                apply_clicked = st.button("套用到勾選的紀錄", use_container_width=True)

            # 顯示目前選定分區在本階段還剩多少可挖，方便你先評估夠不夠分
            _cap_stage_idx = get_stage_index(global_stage_choice)
            _excavated_dict = {}
            if not zone_grouped.empty:
                _excavated_dict = zone_grouped.set_index('出土分區')['累計實挖方量'].to_dict()
            if selected_zones and _cap_stage_idx is not None:
                cap_rows = []
                for z in selected_zones:
                    cap = get_stage_remaining_capacity(z, df_results, _cap_stage_idx, _excavated_dict)
                    cap_rows.append({
                        "分區": z,
                        f"【{global_stage_choice}】剩餘可挖 (m³)": f"{cap:,.0f}" if cap is not None else "無上限（本階段無目標）",
                    })
                st.caption(f"📏 目前選定分區在【{global_stage_choice}】的剩餘容量（依此自動判斷何時跳過）：")
                st.dataframe(pd.DataFrame(cap_rows), use_container_width=True, hide_index=True)

            if apply_clicked:
                    if not selected_zones:
                        st.error("請至少選擇一個分區")
                    else:
                        checked_rows = edited_unassigned[edited_unassigned['勾選'] == True]
                        if len(checked_rows) > 0:
                            original_indices = checked_rows['orig_index'].tolist()
                            n_zones = len(selected_zones)

                            if n_zones == 1:
                                # 只選1個分區＝全部指定同一區（維持原本行為，不做容量判斷）
                                assigned_zones = [selected_zones[0]] * len(original_indices)
                                unassigned_count = 0
                            else:
                                # 依各分區在目前階段的剩餘容量分配：挖滿的自動跳過，多的維持未指定
                                truck_vols = pd.to_numeric(
                                    checked_rows.get('載運方量(m³)', pd.Series([12.0] * len(checked_rows))),
                                    errors='coerce'
                                ).fillna(12.0).tolist()
                                assigned_zones, unassigned_count = allocate_trucks_by_capacity(
                                    truck_vols, selected_zones, df_results, _cap_stage_idx, _excavated_dict
                                )

                            # 只寫入有分配到分區的紀錄，沒分配到的維持「未指定」不動
                            applied_count = 0
                            for idx, zone in zip(original_indices, assigned_zones):
                                if zone is not None:
                                    df_logs.loc[idx, '出土分區'] = zone
                                    applied_count += 1

                            if 'orig_index' in df_logs.columns:
                                df_logs = df_logs.drop(columns=['orig_index'])
                            if 'ParsedDate' in df_logs.columns:
                                df_logs = df_logs.drop(columns=['ParsedDate'])

                            if save_sheet_data("dispatch_logs", df_logs):
                                sync_stage_daily_log(global_stage_choice, df_results)
                                if n_zones == 1:
                                    st.success(f"成功更新 {applied_count} 筆紀錄，全部指定至【{selected_zones[0]}】！本日「{global_stage_choice}」逐日紀錄已同步寫入雲端。")
                                else:
                                    from collections import Counter
                                    zone_counts = Counter([z for z in assigned_zones if z is not None])
                                    breakdown = "、".join([f"{z}：{c}筆" for z, c in zone_counts.items()])
                                    st.success(f"成功更新 {applied_count} 筆紀錄（{breakdown}）！本日「{global_stage_choice}」逐日紀錄已同步寫入雲端。")
                                if unassigned_count > 0:
                                    st.warning(f"⚠️ 有 **{unassigned_count} 筆**車次沒有分配出去，因為所選分區在【{global_stage_choice}】的開挖目標都已經填滿了。這些紀錄維持「未指定」狀態，請你自行決定要讓哪些區域超挖、或改指定到其他分區。")
                                st.rerun()
                        else:
                            st.warning("⚠️ 請至少勾選一筆要套用的紀錄。")
        else:
            st.success("目前所有有效紀錄皆已分配分區。")

        st.divider()
        st.markdown("#### 📍 各分區挖掘進度總表")
        if not display_df.empty:
            st.dataframe(display_df, use_container_width=True, hide_index=True)

        st.divider()
        st.markdown("#### 🚫 標記異常作廢聯單")
        st.caption("例如：司機爆胎導致超過4小時、台北港拒收原車返回工地，這種聯單要作廢。選日期、勾選要作廢的紀錄並填寫原因後儲存，作廢的紀錄不會再計入車次與出土量統計（地圖、報表、進度、PDF全部都會排除），但原始紀錄仍完整保留在雲端可查，不會被刪除。")
        void_date = st.date_input("選擇要處理的日期", value=tw_today, key="void_search_date")
        day_logs = df_logs[df_logs['ParsedDate'] == void_date] if not df_logs.empty else pd.DataFrame()
        if day_logs.empty:
            st.info("這天沒有派車紀錄。")
        else:
            void_display_cols = [c for c in ['車頭車號', '時間', '出土分區', '載運方量(m³)', '聯單序號', '作廢', '作廢原因'] if c in day_logs.columns]
            day_logs_display = day_logs[void_display_cols].copy()
            # CheckboxColumn 要求欄位一定要是「真正的布林值」dtype，
            # 但這欄剛從雲端讀回來（或是全新欄位）型態可能是空值/字串，要先強制轉型，不然 data_editor 會直接報錯
            day_logs_display['作廢'] = day_logs_display['作廢'].apply(_is_voided)
            day_logs_display['作廢原因'] = day_logs_display['作廢原因'].fillna("").astype(str)
            edited_void = st.data_editor(
                day_logs_display,
                column_config={
                    "車頭車號": st.column_config.TextColumn(disabled=True),
                    "時間": st.column_config.TextColumn(disabled=True),
                    "出土分區": st.column_config.TextColumn(disabled=True),
                    "載運方量(m³)": st.column_config.NumberColumn(disabled=True),
                    "聯單序號": st.column_config.TextColumn(disabled=True),
                    "作廢": st.column_config.CheckboxColumn(help="勾選代表這筆聯單作廢，不計入任何統計"),
                    "作廢原因": st.column_config.TextColumn(help="例如：爆胎超時遭台北港拒收，原車返回工地"),
                },
                hide_index=True,
                use_container_width=True,
                key=f"void_editor_{void_date}",
            )
            if st.button("💾 儲存作廢設定", key=f"save_void_{void_date}"):
                full_logs = df_logs.copy()
                for idx in edited_void.index:
                    full_logs.loc[idx, '作廢'] = bool(edited_void.loc[idx, '作廢'])
                    full_logs.loc[idx, '作廢原因'] = edited_void.loc[idx, '作廢原因']
                save_cols = [c for c in full_logs.columns if c not in ('orig_index', 'ParsedDate')]
                if save_sheet_data("dispatch_logs", full_logs[save_cols]):
                    void_count = int(edited_void['作廢'].apply(_is_voided).sum())
                    st.success(f"已儲存！{void_date} 目前有 {void_count} 筆作廢紀錄，統計數字會自動排除。")
                    st.rerun()

        with st.expander("📂 檢視所有歷史紀錄"):
            clean_show_logs = df_logs.copy()
            if 'orig_index' in clean_show_logs.columns:
                clean_show_logs = clean_show_logs.drop(columns=['orig_index'])
            if 'ParsedDate' in clean_show_logs.columns:
                clean_show_logs = clean_show_logs.drop(columns=['ParsedDate'])

            show_logs = clean_show_logs.sort_values(['日期', '時間'], ascending=[False, False])
            edited_all = st.data_editor(show_logs, use_container_width=True)
            if st.button("💾 儲存歷史紀錄修改"):
                if save_sheet_data("dispatch_logs", edited_all):
                    st.success("歷史紀錄修改已儲存！")
                    st.rerun()
    else:
        st.info("尚無出土紀錄。")
