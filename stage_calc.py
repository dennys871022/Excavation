"""階段總覽計算、每日出土紀錄同步與總覽 HTML。"""

import numpy as np
import pandas as pd
from datetime import datetime
from datetime import timedelta
from config import LOOSE_SOIL_FACTOR, PORT_REJECTION_KEYWORDS, STAGE_VOLUME_COLS
from data_store import _save_daily_notes_sorted, filter_active_dispatch_logs, load_sheet_data
from zones import filter_zones_for_stage


def _note_indicates_port_rejection(note):
    """
    判斷「備註」是否含有代表台北港無收土/停止收容之類的關鍵字。
    這種日期即使被勾選為特例要顯示，也不應該算進工期／平均出土功率的計算
    （因為當天雖然出車去現場了，但港口不收，本質上跟沒出土一樣，不能墊高平均值）。
    """
    if pd.isna(note):
        return False
    note_str = str(note)
    return any(kw in note_str for kw in PORT_REJECTION_KEYWORDS)


def _migrate_stage_settings_schema(df):
    """
    相容舊版雲端資料：stage_settings 分頁舊欄位是「預計施作工期」（天數），
    新版改成「預計結束日期」。如果讀到的資料還是舊欄位，這裡自動換算補上新欄位，
    避免舊資料在新版程式裡直接 KeyError。
    """
    df = df.copy()
    if "預計結束日期" not in df.columns:
        if "預計施作工期" in df.columns and "預計開始時間" in df.columns:
            def _calc_end(row):
                try:
                    start = pd.to_datetime(row["預計開始時間"])
                    days = pd.to_numeric(row["預計施作工期"], errors='coerce')
                    if pd.isna(days):
                        days = 20
                    return (start + pd.Timedelta(days=int(days))).strftime("%Y-%m-%d")
                except Exception:
                    return None
            df["預計結束日期"] = df.apply(_calc_end, axis=1)
        else:
            df["預計結束日期"] = None
    if "手動指定每日預計車次" not in df.columns:
        df["手動指定每日預計車次"] = np.nan
    return df


def compute_stage_overview(stage_choice, df_results, override_settings_row=None, as_of_date=None):
    """
    計算單一階段（開挖前土方 / 第1~4挖）的總覽數據，回傳 dict。
    給 tab_stage（畫面顯示）與 tab_stats 的 PDF 匯出共用，確保兩邊數字一致。
    override_settings_row: 若提供（例如畫面上尚未儲存的 data_editor 編輯值），
    優先使用它來計算，取代從雲端讀回的已儲存設定，維持「編輯即時預覽」的效果。
    as_of_date: 若提供，代表「查詢過往報表」——把所有「今天」相關的計算都改成以這個日期為準
    （該日的出土量、累積量、剩餘量、完成百分比等），呈現「截至那一天」的歷史快照。
    預設為 None，等同今天（維持原本即時總覽的行為）。
    """
    tw_today_ = (datetime.utcnow() + timedelta(hours=8)).date()
    query_date_ = as_of_date if as_of_date is not None else tw_today_

    df_stage_map_ = df_results.copy() if df_results is not None and not df_results.empty else pd.DataFrame()
    _stage_idx_for_target = get_stage_index(stage_choice)
    if _stage_idx_for_target is not None:
        target_col_ = STAGE_VOLUME_COLS[_stage_idx_for_target]
        df_stage_map_ = filter_zones_for_stage(df_stage_map_, _stage_idx_for_target)
    else:
        target_col_ = None

    # 圖資與方量基準算出來的是「實方」，這裡要乘上膨脹係數換算成「鬆方」，
    # 這個值會隨圖資頁的現地GL高程調整即時重新計算（只是預設值，實際存進 stage_settings 的數字要按「套用最新圖資計算值」才會更新）
    default_est_vol_real_ = df_stage_map_[target_col_].sum() if target_col_ and not df_stage_map_.empty else 0
    default_est_vol_ = default_est_vol_real_ * LOOSE_SOIL_FACTOR

    df_stage_set_ = load_sheet_data("stage_settings")
    if df_stage_set_.empty or "階段名稱" not in df_stage_set_.columns:
        df_stage_set_ = pd.DataFrame(columns=["階段名稱", "預計開始時間", "預計結束日期", "預估土方量(鬆方)", "單車預設實方", "手動指定每日預計車次"])
    else:
        df_stage_set_ = _migrate_stage_settings_schema(df_stage_set_)

    current_set_ = df_stage_set_[df_stage_set_["階段名稱"] == stage_choice]
    if override_settings_row is not None:
        s_row_ = override_settings_row
    elif current_set_.empty:
        s_row_ = pd.Series({
            "階段名稱": stage_choice,
            "預計開始時間": tw_today_.strftime("%Y-%m-%d"),
            "預計結束日期": (tw_today_ + timedelta(days=20)).strftime("%Y-%m-%d"),
            "預估土方量(鬆方)": default_est_vol_,
            "單車預設實方": 12.0,
            "手動指定每日預計車次": np.nan,
        })
    else:
        s_row_ = current_set_.iloc[0]

    est_start_str_ = s_row_.get("預計開始時間", str(tw_today_))
    est_end_str_ = s_row_.get("預計結束日期", None)
    est_vol_ = pd.to_numeric(s_row_.get("預估土方量(鬆方)", 0), errors='coerce')
    manual_daily_trips_ = pd.to_numeric(s_row_.get("手動指定每日預計車次", np.nan), errors='coerce')
    vol_per_truck_ = pd.to_numeric(s_row_.get("單車預設實方", 12.0), errors='coerce')

    try:
        est_start_ = pd.to_datetime(est_start_str_).date()
    except Exception:
        est_start_ = tw_today_

    try:
        est_end_ = pd.to_datetime(est_end_str_).date() if est_end_str_ not in (None, "", "NaT") else est_start_ + timedelta(days=20)
    except Exception:
        est_end_ = est_start_ + timedelta(days=20)

    # 預計施作工期改由系統自動算：結束日期 - 開始日期（至少算1天，避免除以0）
    est_days_ = max((est_end_ - est_start_).days, 1)
    est_daily_vol_ = est_vol_ / est_days_ if est_days_ > 0 else 0

    df_logs_ = filter_active_dispatch_logs(load_sheet_data("dispatch_logs"))
    if not df_logs_.empty and "日期" in df_logs_.columns:
        valid_logs_ = df_logs_.copy()
        valid_logs_['載運方量(m³)'] = pd.to_numeric(valid_logs_.get('載運方量(m³)', vol_per_truck_), errors='coerce').fillna(vol_per_truck_)
        # 車次數：正常逐台登記的紀錄固定是1；FDA簡化集計紀錄一列代表好幾台車，數字會大於1，
        # 所以車次要加總「車次數」而不是數「列數」
        if '車次數' in valid_logs_.columns:
            valid_logs_['車次數'] = pd.to_numeric(valid_logs_['車次數'], errors='coerce').fillna(1)
        else:
            valid_logs_['車次數'] = 1
        if '運送目的地' in valid_logs_.columns:
            _is_fda_ = valid_logs_['運送目的地'].fillna('台北港').astype(str).eq('FDA')
        else:
            _is_fda_ = pd.Series(False, index=valid_logs_.index)

        # 「實際車次」只算台北港正式聯單的車次，FDA車次不併進來（FDA另外在 df_fda_daily 獨立一張表）。
        # 「當日運棄量」則仍含FDA方量，因為FDA挖出來的土一樣算開挖進度。
        valid_logs_['_台北港車次'] = valid_logs_['車次數'].where(~_is_fda_, 0)
        daily_stats_ = valid_logs_.groupby('日期').agg(
            實際車次=('_台北港車次', 'sum'),
            當日運棄量=('載運方量(m³)', 'sum')
        ).reset_index()
        daily_stats_ = daily_stats_.sort_values('日期')

        # FDA 專用每日明細（獨立一張表，不跟台北港的車次混在一起）
        fda_logs_ = valid_logs_[_is_fda_]
        if not fda_logs_.empty:
            _fda_rows = []
            for _d, _sub in fda_logs_.groupby('日期'):
                _zone_parts = [
                    f"{_z}（{int(_zsub['車次數'].sum())}台/{_zsub['載運方量(m³)'].sum():,.0f}m³）"
                    for _z, _zsub in _sub.groupby('出土分區')
                ]
                _notes = []
                if '備註' in _sub.columns:
                    for _n in _sub['備註'].dropna().unique():
                        _n = str(_n).replace("FDA土方交換：", "").replace("FDA土方交換", "").strip()
                        if _n and _n not in _notes:
                            _notes.append(_n)
                _fda_rows.append({
                    '日期': _d,
                    'FDA車次': int(_sub['車次數'].sum()),
                    'FDA方量(m³)': float(_sub['載運方量(m³)'].sum()),
                    '分區分配': '、'.join(_zone_parts),
                    '備註': '；'.join(_notes),
                })
            df_fda_daily_ = pd.DataFrame(_fda_rows).sort_values('日期').reset_index(drop=True)
            df_fda_daily_['累計FDA車次'] = df_fda_daily_['FDA車次'].cumsum()
            df_fda_daily_['累計FDA方量(m³)'] = df_fda_daily_['FDA方量(m³)'].cumsum()
            df_fda_daily_ = df_fda_daily_[['日期', 'FDA車次', 'FDA方量(m³)', '累計FDA車次', '累計FDA方量(m³)', '分區分配', '備註']]
        else:
            df_fda_daily_ = pd.DataFrame(columns=['日期', 'FDA車次', 'FDA方量(m³)', '累計FDA車次', '累計FDA方量(m³)', '分區分配', '備註'])
    else:
        daily_stats_ = pd.DataFrame(columns=['日期', '實際車次', '當日運棄量'])
        df_fda_daily_ = pd.DataFrame(columns=['日期', 'FDA車次', 'FDA方量(m³)', '累計FDA車次', '累計FDA方量(m³)', '分區分配', '備註'])

    df_daily_notes_ = load_sheet_data("stage_daily_notes")
    if df_daily_notes_.empty:
        df_daily_notes_ = pd.DataFrame(columns=['階段名稱', '日期', '內控預計車次', '實際車次', '差異', '當日運棄量', '累計運棄量', '剩餘土方量', '備註', '計入工期'])
    else:
        if '階段名稱' not in df_daily_notes_.columns:
            df_daily_notes_.insert(0, '階段名稱', stage_choice)
        if '剩餘土方量' not in df_daily_notes_.columns:
            df_daily_notes_['剩餘土方量'] = np.nan
        if '計入工期' not in df_daily_notes_.columns:
            df_daily_notes_['計入工期'] = np.nan

    curr_notes_ = df_daily_notes_[df_daily_notes_['階段名稱'] == stage_choice].copy() if not df_daily_notes_.empty else pd.DataFrame()

    max_log_date_ = pd.to_datetime(daily_stats_['日期'].max()).date() if not daily_stats_.empty else tw_today_
    end_date_for_range_ = max(tw_today_, max_log_date_)
    date_range_ = [d.strftime("%Y-%m-%d") for d in pd.date_range(est_start_, end_date_for_range_)]
    df_range_ = pd.DataFrame({"日期": date_range_})

    if not daily_stats_.empty:
        df_range_ = pd.merge(df_range_, daily_stats_, on="日期", how="left").fillna({"實際車次": 0, "當日運棄量": 0})
    else:
        df_range_['實際車次'] = 0
        df_range_['當日運棄量'] = 0

    df_range_['累計車次'] = df_range_['實際車次'].cumsum()
    df_range_['累計運棄量'] = df_range_['當日運棄量'].cumsum()

    if not curr_notes_.empty and '備註' in curr_notes_.columns:
        merge_cols_ = ['日期', '內控預計車次', '備註']
        if '計入工期' in curr_notes_.columns:
            merge_cols_.append('計入工期')
        merge_notes_ = curr_notes_[merge_cols_].drop_duplicates('日期')
        if '計入工期' in merge_notes_.columns:
            merge_notes_ = merge_notes_.rename(columns={'計入工期': '計入工期_saved'})
        df_range_ = pd.merge(df_range_, merge_notes_, on="日期", how="left")
    else:
        df_range_['內控預計車次'] = np.nan
        df_range_['備註'] = ""
        df_range_['計入工期_saved'] = np.nan

    if pd.notna(manual_daily_trips_) and manual_daily_trips_ > 0:
        default_daily_trips_ = manual_daily_trips_
    else:
        default_daily_trips_ = round((est_vol_ / vol_per_truck_) / est_days_) if est_days_ > 0 and vol_per_truck_ > 0 else 0
    df_range_['內控預計車次'] = pd.to_numeric(df_range_['內控預計車次'], errors='coerce').fillna(default_daily_trips_)
    df_range_['備註'] = df_range_['備註'].fillna("")
    df_range_['剩餘土方量'] = (est_vol_ - df_range_['累計運棄量']).clip(lower=0)

    # 計入工期：有出土(>0)一律算，沒出土(=0)預設不算，除非該日被手動勾選為特例（存在 stage_daily_notes 裡）
    if '計入工期_saved' not in df_range_.columns:
        df_range_['計入工期_saved'] = np.nan
    df_range_['計入工期'] = df_range_['當日運棄量'] > 0
    _saved_mask = df_range_['計入工期_saved'].notna()
    df_range_.loc[_saved_mask, '計入工期'] = df_range_.loc[_saved_mask, '計入工期_saved'].astype(bool)
    df_range_.loc[df_range_['當日運棄量'] > 0, '計入工期'] = True  # 有出土一律算，不受任何手動設定影響
    df_range_ = df_range_.drop(columns=['計入工期_saved'])

    # 備註含「台北港」字樣且當天沒出土 → 這天的差異／累積差異直接跳過不計算（差異留空、累積差異維持前一天數字不變）
    # 判斷「台北港有沒有出土」要看台北港自己的車次（實際車次，已排除FDA）；
    # 當日運棄量含FDA方量，如果當天只有FDA土方交換、台北港暫停收土，運棄量>0但台北港車次=0，一樣要跳過差異。
    _port_reject_mask = df_range_['備註'].apply(_note_indicates_port_rejection) & (df_range_['實際車次'] == 0)

    df_range_['差異'] = df_range_['實際車次'] - df_range_['內控預計車次']
    df_range_.loc[_port_reject_mask, '差異'] = np.nan

    # 累積差異：只累加「有顯示且沒被台北港註記跳過」的日期的差異，被隱藏的0出土日跟台北港排除日都不會貢獻差異值，
    # 這樣畫面上兩個相鄰可見日期之間的累積差異變化，才會等於中間那筆「差異」的值，數字對得起來
    _cumsum_contribution = df_range_['差異'].where(df_range_['計入工期'] & ~_port_reject_mask, 0).fillna(0)
    df_range_['累積差異'] = _cumsum_contribution.cumsum()

    actual_start_date_ = est_start_
    # 目前作業工期改用「有計入工期的天數」，不是單純日曆天數，
    # 這樣沒出土又沒被標記為特例的日期就不會拖累平均出土功率/剩餘天數的計算。
    # 另外：即使被勾選為特例要顯示，只要備註寫了「台北港無收土/停止收容」之類關鍵字，
    # 那天雖然會顯示在明細表上，但不會被算進工期與平均功率（除非那天其實有出土量，就一律照算）。
    today_str_ = query_date_.strftime("%Y-%m-%d")
    _work_days_mask = (
        (df_range_['日期'] <= today_str_)
        & (df_range_['計入工期'] == True)
        & (~_port_reject_mask)
    )
    current_work_days_ = int(_work_days_mask.sum())
    today_row_ = df_range_[df_range_['日期'] == today_str_]
    today_vol_ = today_row_['當日運棄量'].sum() if not today_row_.empty else 0

    # cum_vol / cum_trips 用「截至查詢日那一列」已經算好的累計欄位，
    # 而不是整個 df_range_ 加總，這樣查詢過去日期時才不會把查詢日之後的資料也算進去
    if not today_row_.empty:
        cum_vol_ = today_row_['累計運棄量'].iloc[0]
        cum_trips_ = today_row_['累計車次'].iloc[0]
    else:
        past_rows_ = df_range_[df_range_['日期'] <= today_str_]
        cum_vol_ = past_rows_['當日運棄量'].sum() if not past_rows_.empty else 0
        cum_trips_ = past_rows_['實際車次'].sum() if not past_rows_.empty else 0

    avg_vol_per_day_ = cum_vol_ / current_work_days_ if current_work_days_ > 0 else 0
    remain_vol_ = max(0, est_vol_ - cum_vol_)
    remain_days_ = round(remain_vol_ / avg_vol_per_day_) if avg_vol_per_day_ > 0 else 0
    est_completion_date_ = query_date_ + timedelta(days=remain_days_)
    diff_days_ = (est_completion_date_ - est_end_).days
    percent_done_ = round((cum_vol_ / est_vol_) * 100) if est_vol_ > 0 else 0

    avg_trips_per_day_ = cum_trips_ / current_work_days_ if current_work_days_ > 0 else 0

    return {
        "stage_choice": stage_choice,
        "today": query_date_,
        "is_historical": as_of_date is not None,
        "current_work_days": current_work_days_,
        "est_start": est_start_,
        "actual_start_date": actual_start_date_,
        "est_days": est_days_,
        "remain_days": remain_days_,
        "est_end": est_end_,
        "est_completion_date": est_completion_date_,
        "diff_days": diff_days_,
        "est_vol": est_vol_,
        "today_vol": today_vol_,
        "est_daily_vol": est_daily_vol_,
        "cum_vol": cum_vol_,
        "avg_vol_per_day": avg_vol_per_day_,
        "cum_trips": cum_trips_,
        "avg_trips_per_day": avg_trips_per_day_,
        "remain_vol": remain_vol_,
        "percent_done": percent_done_,
        "df_range": df_range_,
        "df_daily_notes": df_daily_notes_,
        "df_stage_map": df_stage_map_,
        "target_col": target_col_,
        "vol_per_truck": vol_per_truck_,
        "est_vol_default": default_est_vol_,
        "est_vol_default_real": default_est_vol_real_,
        "default_daily_trips": default_daily_trips_,
        "df_fda_daily": df_fda_daily_,
        "manual_daily_trips": manual_daily_trips_ if pd.notna(manual_daily_trips_) else None,
    }


def build_stage_overview_html(overview):
    """把 compute_stage_overview() 回傳的 dict 畫成紫色階段總覽表格的HTML字串（給tab_stage即時總覽與歷史查詢共用）。"""
    html_table = f"""
    <table style="width: 100%; border-collapse: collapse; font-family: sans-serif; text-align: right;">
        <tr>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">今天日期</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{overview['today']}</td>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">目前作業工期</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{overview['current_work_days']}</td>
        </tr>
        <tr>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">預計開始時間</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{overview['est_start']}</td>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">實際開始時間</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{overview['actual_start_date']}</td>
        </tr>
        <tr>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">預計施作工期</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{overview['est_days']}</td>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">推估剩餘天數</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{overview['remain_days']}</td>
        </tr>
        <tr>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">預計完成日期</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{overview['est_end']}</td>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">推估完成日期</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{overview['est_completion_date']}</td>
        </tr>
        <tr>
            <td style="border: 1px solid #ccc; padding: 8px;"></td>
            <td style="border: 1px solid #ccc; padding: 8px;"></td>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">差異 (天)</td>
            <td style="border: 1px solid #ccc; padding: 8px; background-color: #f39c12; color: white;">{overview['diff_days']}</td>
        </tr>
        <tr>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">預估土方量(鬆方)</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{overview['est_vol']:,.1f}</td>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">本日出土量(m³)</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{overview['today_vol']:,.1f}</td>
        </tr>
        <tr>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">預估每日出土量(m³)</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{overview['est_daily_vol']:,.1f}</td>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">累積出土數量(m³)</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{overview['cum_vol']:,.1f}</td>
        </tr>
        <tr>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">平均出土功率(m³/天)</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{overview['avg_vol_per_day']:,.1f}</td>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">剩餘土方量(m³)</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{overview['remain_vol']:,.1f}</td>
        </tr>
        <tr>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">平均出土功率(台/天)</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{overview['avg_trips_per_day']:,.1f}</td>
            <td style="border: 1px solid #ccc; padding: 8px;"></td>
            <td style="border: 1px solid #ccc; padding: 8px;"></td>
        </tr>
        <tr>
            <td style="border: 1px solid #ccc; padding: 8px;"></td>
            <td style="border: 1px solid #ccc; padding: 8px;"></td>
            <td style="border: 1px solid #ccc; padding: 8px; text-align: left; background-color: #f9f9f9;">完成百分比</td>
            <td style="border: 1px solid #ccc; padding: 8px;">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <div style="flex: 1; background-color: #e8e0ee; border-radius: 4px; overflow: hidden; height: 14px;">
                        <div style="width: {min(max(overview['percent_done'], 0), 100)}%; background-color: #8e44ad; height: 100%;"></div>
                    </div>
                    <span style="color: #8e44ad; font-weight: bold; white-space: nowrap;">{overview['percent_done']}%</span>
                </div>
            </td>
        </tr>
    </table>
    """

    return html_table


def get_stage_index(stage_choice):
    """回傳 0~4 代表第1~5挖（第5挖是油槽獨立開挖）；開挖前土方回傳 None（因為它不是以分區門檻定義的階段）。"""
    if "第1挖" in stage_choice:
        return 0
    if "第2挖" in stage_choice:
        return 1
    if "第3挖" in stage_choice:
        return 2
    if "第4挖" in stage_choice:
        return 3
    if "第5挖" in stage_choice or "油槽" in stage_choice:
        return 4
    return None


def sync_stage_daily_log(stage_choice, df_results):
    """
    把「目前作業階段」今天這一列的統計數字，即時 upsert 寫入 stage_daily_notes 雲端分頁。
    設計給「批量設定出土分區」按下去之後自動呼叫，不需要使用者再手動去階段管控頁按儲存。

    注意：只同步「事實」欄位（實際車次/差異/當日運棄量/累計運棄量/剩餘土方量，這些是從派車
    紀錄算出來的真實數字）。「內控預計車次」跟「備註」是使用者的排程規劃值，這裡刻意不覆寫，
    只能透過階段管控頁的「💾 儲存每日車次目標與備註」手動編輯儲存。這樣做是為了讓「內控預計
    車次」在使用者調整上面的階段參數（開始/結束日期、預估土方量等）時，只要那天沒被手動改過，
    包含過去日期在內都能持續套用最新算出來的預設值，而不會被自動同步寫入的舊數字卡住。
    """
    overview = compute_stage_overview(stage_choice, df_results)
    df_range = overview["df_range"]
    df_daily_notes = overview["df_daily_notes"]

    today_str = overview["today"].strftime("%Y-%m-%d")
    today_rows = df_range[df_range['日期'] == today_str]
    if today_rows.empty:
        return False

    row = today_rows.iloc[0].to_dict()
    fact_cols = ['實際車次', '差異', '當日運棄量', '累計運棄量', '剩餘土方量']

    mask = (df_daily_notes['階段名稱'] == stage_choice) & (df_daily_notes['日期'].astype(str) == today_str)
    if mask.any():
        # 該日已有既存紀錄（可能含使用者手動設定的內控預計車次/備註），只更新事實欄位，不動那兩欄
        for col in fact_cols:
            df_daily_notes.loc[mask, col] = row.get(col, "")
        final_notes = df_daily_notes
    else:
        # 該日還沒有任何紀錄，新增一筆；內控預計車次留空，讓它之後持續套用系統預設值，不鎖住
        new_row = {col: row.get(col, "") for col in fact_cols}
        new_row['階段名稱'] = stage_choice
        new_row['日期'] = today_str
        new_row['內控預計車次'] = np.nan
        new_row['備註'] = ""
        new_row_df = pd.DataFrame([new_row])
        final_notes = pd.concat([df_daily_notes, new_row_df], ignore_index=True)

    return _save_daily_notes_sorted(final_notes)
