"""📈 階段開挖管控 分頁。"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from datetime import timedelta
from config import LOOSE_SOIL_FACTOR
from data_store import _clear_daily_notes_field_or_drop, _row_has_meaningful_override, _save_daily_notes_sorted, _upsert_daily_notes_field, filter_active_dispatch_logs, load_sheet_data, save_sheet_data
from pdf_reports import generate_weekly_trend_pdf
from stage_calc import _migrate_stage_settings_schema, build_stage_overview_html, compute_stage_overview, get_stage_index
from zones import local_stage_index


def render(df_results, global_stage_choice):
    st.write("### 📈 階段開挖管控")
    st.info("此分頁用於單一開挖階段的進度追蹤。左側側邊欄切換「目前作業階段」，此頁與出土儀表板的PDF報表會同步套用該階段。")

    stage_choice = global_stage_choice
    st.markdown(f"目前檢視階段：**{stage_choice}**　（可至左側側邊欄「🎯 目前作業階段」切換）")

    df_stage_set = load_sheet_data("stage_settings")
    if df_stage_set.empty or "階段名稱" not in df_stage_set.columns:
        df_stage_set = pd.DataFrame(columns=["階段名稱", "預計開始時間", "預計結束日期", "預估土方量(鬆方)", "單車預設實方", "手動指定每日預計車次"])
    else:
        df_stage_set = _migrate_stage_settings_schema(df_stage_set)

    overview = compute_stage_overview(stage_choice, df_results)
    target_col = overview["target_col"]
    df_stage_map = overview["df_stage_map"]

    current_set = df_stage_set[df_stage_set["階段名稱"] == stage_choice]
    if current_set.empty:
        new_row = pd.DataFrame([{
            "階段名稱": stage_choice,
            "預計開始時間": overview["today"].strftime("%Y-%m-%d"),
            "預計結束日期": (overview["today"] + timedelta(days=20)).strftime("%Y-%m-%d"),
            "預估土方量(鬆方)": overview["est_vol_default"],
            "單車預設實方": 12.0,
            "手動指定每日預計車次": np.nan,
        }])
        display_stage_set = new_row
    else:
        display_stage_set = current_set.copy()

    display_stage_set = display_stage_set.copy()
    display_stage_set["預計開始時間"] = pd.to_datetime(display_stage_set["預計開始時間"], errors='coerce')
    display_stage_set["預計結束日期"] = pd.to_datetime(display_stage_set["預計結束日期"], errors='coerce')

    st.markdown(f"#### ⚙️ 【{stage_choice}】參數設定")
    st.caption("💡 「預計施作工期」改由系統自動算（= 預計結束日期 − 預計開始時間），你只要設定開始與結束日期即可。下方「每日出土管控明細」表格中的「內控預計車次」預設值，正常情況下 = 預估土方量(鬆方) ÷ 單車預設實方 ÷ 預計施作工期，所有日期會統一帶入這個算出來的數字；但如果你在「手動指定每日預計車次」欄位填了數字，會直接改用你填的數字當作每日預設值，不再套公式（留空則照舊用公式算）。不管用哪種方式算出來的預設值，都還是可以在下方表格個別對單一日期覆寫。")
    edited_stage_set = st.data_editor(
        display_stage_set,
        hide_index=True,
        use_container_width=True,
        column_config={
            "預計開始時間": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "預計結束日期": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "手動指定每日預計車次": st.column_config.NumberColumn(help="填數字＝每天預設車次直接用這個數字，不再套公式計算；留空＝照舊用公式自動算。", min_value=0, step=1),
        },
    )
    _preview_start = pd.to_datetime(edited_stage_set.iloc[0]["預計開始時間"]).date()
    _preview_end = pd.to_datetime(edited_stage_set.iloc[0]["預計結束日期"]).date()
    st.caption(f"📐 系統自動算出的預計施作工期：**{max((_preview_end - _preview_start).days, 1)} 天**")
    _preview_manual_trips = edited_stage_set.iloc[0].get("手動指定每日預計車次")
    if pd.notna(_preview_manual_trips) and _preview_manual_trips not in (None, ""):
        st.caption(f"🎯 目前每日預計車次採用**手動指定值：{_preview_manual_trips} 台/天**（不套公式）")
    else:
        st.caption("🧮 目前每日預計車次採用**公式自動計算**（留空「手動指定每日預計車次」欄位即為此模式）")

    if overview["target_col"] is not None:
        _current_est_vol = pd.to_numeric(edited_stage_set.iloc[0].get("預估土方量(鬆方)"), errors='coerce')
        _live_real = overview["est_vol_default_real"]
        _live_loose = overview["est_vol_default"]
        st.markdown("###### 📊 預估土方量對照（圖資與方量基準即時計算 vs 目前設定值）")
        col_cmp1, col_cmp2, col_cmp3 = st.columns(3)
        col_cmp1.metric("圖資即時算出的實方", f"{_live_real:,.0f} m³")
        col_cmp2.metric(f"換算鬆方（×{LOOSE_SOIL_FACTOR}）", f"{_live_loose:,.0f} m³")
        col_cmp3.metric("目前設定值", f"{_current_est_vol:,.0f} m³" if pd.notna(_current_est_vol) else "尚未設定")

        if pd.isna(_current_est_vol) or abs(_current_est_vol - _live_loose) > 1:
            st.warning(f"⚠️ 目前設定值跟圖資即時計算的鬆方量對不起來（可能是你調整過現地GL高程，或這個數字是之前手動輸入的），如果要以圖資最新計算為準，按下面的按鈕同步。")
            if st.button("🔄 套用圖資最新計算值", key=f"sync_est_vol_{stage_choice}"):
                sync_set = df_stage_set[df_stage_set["階段名稱"] != stage_choice].copy()
                new_setting_row = edited_stage_set.iloc[0].copy()
                new_setting_row["預計開始時間"] = pd.to_datetime(new_setting_row["預計開始時間"]).strftime("%Y-%m-%d")
                new_setting_row["預計結束日期"] = pd.to_datetime(new_setting_row["預計結束日期"]).strftime("%Y-%m-%d")
                new_setting_row["預估土方量(鬆方)"] = _live_loose
                sync_set = pd.concat([sync_set, pd.DataFrame([new_setting_row])], ignore_index=True)
                save_sheet_data("stage_settings", sync_set)
                st.success(f"已套用最新圖資計算值：{_live_loose:,.0f} m³（實方 {_live_real:,.0f} × {LOOSE_SOIL_FACTOR}）")
                st.rerun()
        else:
            st.caption("✅ 目前設定值與圖資即時計算一致。")

    if st.button("💾 儲存本階段設定"):
        save_set = edited_stage_set.copy()
        save_set["預計開始時間"] = pd.to_datetime(save_set["預計開始時間"]).dt.strftime("%Y-%m-%d")
        save_set["預計結束日期"] = pd.to_datetime(save_set["預計結束日期"]).dt.strftime("%Y-%m-%d")
        other_sets = df_stage_set[df_stage_set["階段名稱"] != stage_choice]
        final_sets = pd.concat([other_sets, save_set], ignore_index=True)
        save_sheet_data("stage_settings", final_sets)
        st.rerun()

    # 使用畫面上剛編輯的（尚未儲存的）參數即時重算總覽，維持「編輯即時預覽」效果
    _override_row = edited_stage_set.iloc[0].copy()
    _override_row["預計開始時間"] = pd.to_datetime(_override_row["預計開始時間"]).strftime("%Y-%m-%d")
    _override_row["預計結束日期"] = pd.to_datetime(_override_row["預計結束日期"]).strftime("%Y-%m-%d")
    overview = compute_stage_overview(stage_choice, df_results, override_settings_row=_override_row)
    df_range = overview["df_range"]
    df_daily_notes = overview["df_daily_notes"]

    st.markdown(f"#### 🟣 【{stage_choice}】總覽")

    html_table = build_stage_overview_html(overview)
    st.markdown(html_table, unsafe_allow_html=True)

    st.divider()
    st.markdown("#### 📜 查詢過往報表")
    st.caption("選一個過去的日期，呈現「截至那一天」的歷史快照（該日出土量、累積量、剩餘量、完成百分比等），不影響上面即時總覽或下面的編輯表格。")
    query_col1, query_col2 = st.columns([1, 3])
    with query_col1:
        query_date = st.date_input(
            "查詢日期",
            value=overview["today"],
            min_value=overview["est_start"],
            max_value=overview["today"],
            key=f"history_query_date_{stage_choice}",
        )
    if query_date != overview["today"]:
        historical_overview = compute_stage_overview(stage_choice, df_results, as_of_date=query_date)
        st.markdown(f"##### 🟣 【{stage_choice}】截至 {query_date} 的歷史快照")
        st.markdown(build_stage_overview_html(historical_overview), unsafe_allow_html=True)
    else:
        st.caption("目前選的是今天，跟上面的即時總覽相同；選其他日期即可查詢過往快照。")

    st.divider()
    st.markdown("#### 📅 每日出土管控明細")
    st.caption("💡 「備註」欄可填寫無法出土原因（例如：台北港無收土），供後續對帳查閱；「剩餘土方量」為該日累計後推算之剩餘量；「累積差異」為「差異」逐日累加。「差異」與「累積差異」為負數時，下方預覽表會以紅色標示。下方表格只顯示「有出土」或「被標記為特例」的日期；沒出土又沒被標記的日期不會出現、也不算進「目前作業工期」；備註含「台北港」字樣且當天沒出土的日期，會顯示但同樣不算進工期與平均功率，且「差異」「累積差異」當天直接跳過不計算（差異留空、累積差異維持前一天數字），PDF匯出時這種日期的差異/累積差異會顯示「－」。 另外：「實際車次」只計台北港正式聯單的車次，FDA車次不併入，改列在下方「FDA土方交換每日明細」獨立一張表；「當日運棄量／累計運棄量／剩餘土方量」則含FDA方量（FDA挖出來的土仍算開挖進度）。")

    # 台北港沒出車（實際車次=0）的日期都列在這裡，包含「台北港暫停收土、但有FDA土方交換」的日子（運棄量>0，但台北港車次是0）
    zero_vol_df = df_range[df_range['實際車次'] == 0][['日期', '計入工期', '當日運棄量', '備註']].copy()
    zero_vol_df['當日運棄量'] = zero_vol_df['當日運棄量'].astype(float)
    with st.expander(f"⚙️ 管理台北港沒出土的日期（共 {len(zero_vol_df)} 天；沒有任何出土量的日期預設不顯示、不算工期，勾選才會顯示）", expanded=False):
        st.caption("勾選「顯示這天」＝這天會出現在下方明細表。至於算不算進工期／平均功率，規則是：①有出土的日子一律算，不受這裡影響；②沒出土但備註寫了「台北港」相關字樣（例如：台北港無收土、台北港停止收容）→ 會顯示但不算進工期與功率；③沒出土且備註沒有台北港字樣 → 顯示且算入工期。另外，「當日運棄量」大於0但台北港沒車次的日子（只有FDA土方交換），一定會顯示；備註寫了「台北港」字樣時，差異與累積差異同樣跳過不計算。")
        if zero_vol_df.empty:
            st.caption("目前沒有出土量為0的日期。")
        else:
            edited_zero = st.data_editor(
                zero_vol_df.rename(columns={'計入工期': '顯示這天'}),
                column_config={
                    "日期": st.column_config.TextColumn(disabled=True),
                    "當日運棄量": st.column_config.NumberColumn("當日運棄量(含FDA)", format="%.0f", disabled=True),
                    "顯示這天": st.column_config.CheckboxColumn(default=False),
                    "備註": st.column_config.TextColumn(help="填寫原因，例如：台北港無收土。含「台北港」字樣的日期即使顯示也不會算進工期與功率。"),
                },
                hide_index=True,
                use_container_width=True,
                key=f"zero_vol_editor_{stage_choice}",
            )
            if st.button("💾 儲存特例設定", key=f"save_zero_vol_{stage_choice}"):
                updated_notes = df_daily_notes.copy()
                for _, r in edited_zero.iterrows():
                    d = str(r['日期'])
                    if bool(r['顯示這天']):
                        updated_notes = _upsert_daily_notes_field(updated_notes, stage_choice, d, '計入工期', True)
                        updated_notes = _upsert_daily_notes_field(updated_notes, stage_choice, d, '備註', str(r['備註']) if pd.notna(r['備註']) else "")
                    else:
                        # 沒勾 = 恢復預設行為，不需要特地存一筆 False，
                        # 直接清掉這個欄位；如果那天已經沒有其他值得保留的手動設定，整列一併移除，避免留下空列
                        updated_notes = _clear_daily_notes_field_or_drop(updated_notes, stage_choice, d, '計入工期')
                _save_daily_notes_sorted(updated_notes)
                st.success("已儲存特例設定！打勾的日期會顯示在下方明細表；若備註含「台北港」字樣，會顯示但不算進工期與功率，其餘打勾的日期則正常算入。沒勾的日期不會在雲端留下多餘紀錄。")
                st.rerun()

    with st.expander("🧹 清理雲端舊資料（一次性；清掉沒有實際內容的空白紀錄列）", expanded=False):
        st.caption("如果你的雲端 stage_daily_notes 分頁裡有很多內控預計車次、備註都空白，計入工期也是空的舊紀錄列（例如舊版程式曾經誤寫入的資料），可以用這顆按鈕一次清掉。只會刪除「內控預計車次沒填、備註沒寫、計入工期沒被勾選為True」的列；只要三者有一個有內容，那一列就會被保留。")
        if st.button("🧹 清理本階段沒有意義的舊紀錄列", key=f"cleanup_notes_{stage_choice}"):
            cleaned_notes = df_daily_notes.copy()
            if not cleaned_notes.empty:
                mask_stage_clean = cleaned_notes['階段名稱'] == stage_choice
                keep_mask = (~mask_stage_clean) | cleaned_notes.apply(_row_has_meaningful_override, axis=1)
                removed_count = int((~keep_mask).sum())
                cleaned_notes = cleaned_notes[keep_mask]
            else:
                removed_count = 0
            _save_daily_notes_sorted(cleaned_notes)
            st.success(f"已清理【{stage_choice}】{removed_count} 筆沒有實際內容的舊紀錄列。")
            st.rerun()

    display_cols = ['日期', '內控預計車次', '實際車次', '差異', '累積差異', '當日運棄量', '累計運棄量', '剩餘土方量', '備註']
    df_range_display = df_range[(df_range['當日運棄量'] > 0) | (df_range['計入工期'] == True)]

    def _highlight_negative_diff(val):
        try:
            return 'color: #e74c3c; font-weight: bold;' if float(val) < 0 else ''
        except (ValueError, TypeError):
            return ''

    preview_styler = df_range_display[display_cols].style.applymap(_highlight_negative_diff, subset=['差異', '累積差異'])
    st.dataframe(preview_styler, use_container_width=True, hide_index=True)

    col_reset1, col_reset2 = st.columns([3, 1])
    with col_reset2:
        if st.button("🔄 重設內控預計車次為系統預設值", use_container_width=True):
            mask_stage = df_daily_notes['階段名稱'] == stage_choice
            if '內控預計車次' in df_daily_notes.columns:
                df_daily_notes.loc[mask_stage, '內控預計車次'] = np.nan
            # 清空後，把已經沒有任何值得保留設定（備註/計入工期）的空列一併移除，避免留下垃圾列
            if not df_daily_notes.empty:
                keep_mask = (~mask_stage) | df_daily_notes.apply(_row_has_meaningful_override, axis=1)
                df_daily_notes = df_daily_notes[keep_mask]
            _save_daily_notes_sorted(df_daily_notes)
            st.success(f"已清空【{stage_choice}】所有日期的內控預計車次，全部改回套用目前參數算出的系統預設值。")
            st.rerun()
    with col_reset1:
        st.caption("⚠️ 若過去曾手動調整過某幾天的內控預計車次，按下這顆按鈕會把「這個階段」所有日期都清空重算，手動設定的數字也會一併被清掉，請注意。")

    st.markdown("##### ✏️ 編輯內控預計車次 / 備註")
    edited_daily = st.data_editor(
        df_range_display[display_cols],
        column_config={
            "內控預計車次": st.column_config.NumberColumn(required=True),
            "實際車次": st.column_config.NumberColumn(disabled=True),
            "差異": st.column_config.NumberColumn(disabled=True),
            "累積差異": st.column_config.NumberColumn(disabled=True),
            "當日運棄量": st.column_config.NumberColumn(disabled=True),
            "累計運棄量": st.column_config.NumberColumn(disabled=True),
            "剩餘土方量": st.column_config.NumberColumn(disabled=True),
            "備註": st.column_config.TextColumn()
        },
        hide_index=True,
        use_container_width=True
    )

    if st.button("💾 儲存每日車次目標與備註 (全欄位寫入雲端)"):
        save_df = edited_daily.copy()
        save_df['階段名稱'] = stage_choice
        save_df['計入工期'] = True  # 這裡看得到的日期一定是「有出土」或「已標記特例」，都算工期
        # 只替換「這次編輯到的日期」，其餘（含被隱藏的0出土日期特例設定）完全不動
        edited_dates = set(save_df['日期'])
        other_notes = df_daily_notes[~((df_daily_notes['階段名稱'] == stage_choice) & (df_daily_notes['日期'].astype(str).isin(edited_dates)))]
        final_notes = pd.concat([other_notes, save_df], ignore_index=True)
        _save_daily_notes_sorted(final_notes)
        st.rerun()

    st.divider()
    st.markdown("#### 🔄 FDA土方交換每日明細（獨立一張表，不併入上方實際車次）")
    st.caption("隔壁工地FDA的土方交換另外列表：車次與方量獨立呈現，「分區分配」顯示當天各分區各分到幾台、多少方量。FDA的方量仍然計入各分區累計實挖方量與開挖進度，只是車次不跟台北港正式聯單的車次混在一起算。")
    df_fda_view = overview.get("df_fda_daily")
    if df_fda_view is None or df_fda_view.empty:
        st.info("目前還沒有任何FDA土方交換紀錄。可以到「出土統計儀表板」的「🔄 FDA土方交換」表單新增。")
    else:
        st.dataframe(df_fda_view, use_container_width=True, hide_index=True)
        st.caption(f"📌 FDA累計：**{int(df_fda_view['FDA車次'].sum())} 台** / **{df_fda_view['FDA方量(m³)'].sum():,.0f} m³**")

    st.divider()
    st.markdown("#### 📈 週會資料：區間車次趨勢")
    st.caption("選一段日期區間，只顯示「日期、實際車次、備註」三欄，並畫出車次折線圖（X軸日期、Y軸車次），方便週會報告直接用。這裡會列出區間內每一天（包含沒出土的日子），讓趨勢圖看得出完整狀況。")

    col_wk1, col_wk2 = st.columns(2)
    with col_wk1:
        week_start = st.date_input(
            "起始日期", value=max(overview["est_start"], overview["today"] - timedelta(days=7)),
            key=f"week_start_{stage_choice}"
        )
    with col_wk2:
        week_end = st.date_input("結束日期", value=overview["today"], key=f"week_end_{stage_choice}")

    if week_start > week_end:
        st.error("起始日期不能晚於結束日期。")
    else:
        week_start_str = week_start.strftime("%Y-%m-%d")
        week_end_str = week_end.strftime("%Y-%m-%d")
        week_df = df_range[(df_range['日期'] >= week_start_str) & (df_range['日期'] <= week_end_str)].copy()

        if week_df.empty:
            st.info("這個區間內沒有資料。")
        else:
            week_display = week_df[['日期', '實際車次', '備註']]
            st.dataframe(week_display, use_container_width=True, hide_index=True)

            week_df['_date_obj'] = pd.to_datetime(week_df['日期'])
            week_df['_date_label'] = week_df['_date_obj'].apply(lambda d: f"{d.month}/{d.day}")

            fig_week = go.Figure()
            fig_week.add_trace(go.Scatter(
                x=week_df['_date_obj'], y=week_df['實際車次'],
                mode='lines+markers+text',
                text=week_df['實際車次'].astype(int).astype(str),
                textposition='top center',
                line=dict(color='#3498DB', width=2),
                marker=dict(size=7),
                name='實際車次',
            ))
            fig_week.update_layout(
                title=f"【{stage_choice}】{week_start_str} ~ {week_end_str} 車次趨勢",
                xaxis=dict(
                    title="日期",
                    type='date',
                    tickmode='array',
                    tickvals=week_df['_date_obj'],
                    ticktext=week_df['_date_label'],  # 直接用算好的「9/13」格式文字，不靠Plotly解析日期格式字串
                    tickangle=-45,
                ),
                yaxis_title="車次 (台)",
                height=460, margin=dict(l=20, r=20, t=50, b=60),
            )
            st.plotly_chart(fig_week, use_container_width=True)

            if st.button("📥 匯出週會表格 PDF", key=f"export_week_pdf_{stage_choice}"):
                with st.spinner("PDF 產生中..."):
                    week_pdf_path = generate_weekly_trend_pdf(
                        week_display, stage_choice, week_start_str, week_end_str
                    )
                with open(week_pdf_path, "rb") as f:
                    st.download_button(
                        label=f"✅ 點此下載 週會車次表格 ({week_start_str}~{week_end_str}).pdf",
                        data=f,
                        file_name=f"weekly_trend_{stage_choice}_{week_start_str}_{week_end_str}.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                        key=f"download_week_pdf_{stage_choice}",
                    )

    st.divider()
    st.markdown(f"#### 🗺️ 【{stage_choice}】單階段專用地圖（僅顯示本階段挖掘進度）")
    st.markdown("⬜ 尚未開始 🟧 進行中 🟩 已完成")

    df_logs_for_map = filter_active_dispatch_logs(load_sheet_data("dispatch_logs"))
    zone_vol_dict = {}
    if not df_logs_for_map.empty and '出土分區' in df_logs_for_map.columns and '載運方量(m³)' in df_logs_for_map.columns:
        tmp_map_logs = df_logs_for_map[df_logs_for_map['出土分區'] != '未指定'].copy()
        tmp_map_logs['載運方量(m³)'] = pd.to_numeric(tmp_map_logs['載運方量(m³)'], errors='coerce')
        zone_vol_dict = tmp_map_logs.groupby('出土分區')['載運方量(m³)'].sum().to_dict()

    stage_idx = get_stage_index(stage_choice)
    stage_thresholds_dict = df_results.set_index('分區代號')['各階累計方量'].to_dict() if not df_results.empty else {}

    fig_stage = go.Figure()
    for idx, row in df_stage_map.iterrows():
        grid_id = row['分區代號']
        current_vol = zone_vol_dict.get(grid_id, 0)
        thresholds = stage_thresholds_dict.get(grid_id, [])
        t_vol = row[target_col] if target_col else 0

        _local_idx = local_stage_index(row, stage_idx) if stage_idx is not None else -1
        if stage_idx is not None and thresholds and 0 <= _local_idx < len(thresholds):
            band_start = thresholds[_local_idx - 1] if _local_idx > 0 else 0
            band_end = thresholds[_local_idx]
            band_size = band_end - band_start
            pct = min(max((current_vol - band_start) / band_size * 100, 0), 100) if band_size > 0 else 100.0

            if pct >= 98:
                fill_color = 'rgba(46, 204, 113, 0.8)'
                status_label = "已完成"
            elif pct > 0:
                fill_color = 'rgba(230, 126, 34, 0.7)'
                status_label = f"進行中 {pct:.0f}%"
            else:
                fill_color = 'rgba(240, 240, 240, 0.5)'
                status_label = "尚未開始"
            hover_text = f"{grid_id}<br>本階段目標: {t_vol:,.0f} m³<br>本階段狀態: {status_label}"
        else:
            fill_color = 'rgba(0, 100, 255, 0.1)'
            hover_text = f"{grid_id}<br>階段基準方量: {t_vol:,.0f} m³<br>（開挖前土方無分區進度）"

        fig_stage.add_trace(go.Scatter(
            x=[row['x_min'], row['x_max'], row['x_max'], row['x_min'], row['x_min']],
            y=[row['y_min'], row['y_min'], row['y_max'], row['y_max'], row['y_min']],
            mode='lines', line=dict(color='gray', width=1),
            fill='toself', fillcolor=fill_color, showlegend=False, hoverinfo='text',
            text=hover_text
        ))
        fig_stage.add_annotation(x=row['x_center'], y=row['y_center'], text=grid_id, showarrow=False, font=dict(color="black", size=10))

    fig_stage.update_layout(title=f"【{stage_choice}】單階段進度地圖", dragmode='pan', xaxis_title="", yaxis_title="", yaxis=dict(scaleanchor="x", scaleratio=1), height=500, margin=dict(l=0, r=0, t=30, b=0))
    st.plotly_chart(fig_stage, use_container_width=True, config={'displayModeBar': False})
