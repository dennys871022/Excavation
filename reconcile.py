"""
官方電子聯單 CSV 對帳核心邏輯（不含任何 Streamlit 畫面）。
「🧾 官方聯單對帳」分頁與本機自動腳本 auto_manifest.py 共用這一份，改對帳規則只要改這裡。

規則（與原本分頁內的邏輯完全相同）：
- 只處理台北港紀錄，FDA 一律排除。
- 官方序號系統裡已存在 → 原封不動。
- 同車號、時間最接近（2 小時內）且還沒有序號的系統紀錄 → 寫入官方時間與序號（保留分區）。
- 找不到可配對的 → 依官方資料補一筆（備註「系統依官方CSV補齊（非官方補登）」）。
- 系統多出來的 → 刪除；但已有官方聯單序號、或已作廢的紀錄絕不刪。
- final_of_day=True（當天所有土質CSV都到齊）→ 再清掉車牌不在任何CSV裡、又沒有序號的殘留紀錄。
- 最後依聯單序號補上官方補登／司機／土質／進場時間欄位。
"""

import pandas as pd
from data_store import _clean_serial, _is_voided, apply_official_fields

PLATE_COL = "出場車頭車號"
DATETIME_COL = "出場日期"
SERIAL_COL = "聯單序號"
REQUIRED_COLS = [PLATE_COL, DATETIME_COL, SERIAL_COL]


def missing_columns(official_df):
    return [c for c in REQUIRED_COLS if c not in official_df.columns]


def reconcile_day(df_logs, official_df, sync_date, final_of_day=False):
    """
    回傳 (新的 df_logs, 統計 dict)。統計 dict 內含：
    updated, added, deleted, already, protected, extra_cleaned, enriched,
    official_rows（官方當天筆數）, merged（差異摘要 DataFrame）, leftover（殘留紀錄 DataFrame）
    """
    official_df = official_df.copy()
    official_df['FullTime'] = pd.to_datetime(official_df[DATETIME_COL], errors='coerce')
    official_df['ParsedDate'] = official_df['FullTime'].dt.date
    official_df['正規化車號'] = official_df[PLATE_COL].astype(str).str.replace(r'\W+', '', regex=True).str.upper()

    sync_off_df = official_df[official_df['ParsedDate'] == sync_date].copy()

    if df_logs is None or df_logs.empty:
        df_logs = pd.DataFrame(columns=["日期", "時間", "車頭車號", "出土分區", "載運方量(m³)", "備註", "聯單序號"])
    df_logs = df_logs.copy()
    if "聯單序號" not in df_logs.columns:
        df_logs["聯單序號"] = ""
    if "運送目的地" not in df_logs.columns:
        df_logs["運送目的地"] = "台北港"
    else:
        df_logs["運送目的地"] = df_logs["運送目的地"].fillna("台北港").replace("", "台北港")
    for _c, _d in [("作廢", False), ("作廢原因", ""), ("車次數", 1)]:
        if _c not in df_logs.columns:
            df_logs[_c] = _d
    # 序號欄位轉成可以放文字的型態，避免整欄原本被判成數字時寫入字串報錯
    df_logs["聯單序號"] = df_logs["聯單序號"].astype(object)

    df_logs['ParsedDate'] = pd.to_datetime(df_logs['日期'], errors='coerce').dt.date
    df_logs['FullTime'] = pd.to_datetime(df_logs['日期'].astype(str) + ' ' + df_logs['時間'].astype(str), errors='coerce')
    df_logs['正規化車號'] = df_logs['車頭車號'].astype(str).str.replace(r'\W+', '', regex=True).str.upper()
    # 有官方聯單序號 = 已經對過帳，絕對不能刪、也不能被拿去重新配對覆蓋
    df_logs['_serial_clean'] = df_logs['聯單序號'].apply(_clean_serial)
    # 官方對帳只處理台北港；FDA 簡化集計紀錄要排除，不然會被誤判成多出來而被刪除
    sync_sys_df = df_logs[(df_logs['ParsedDate'] == sync_date) & (df_logs['運送目的地'] != 'FDA')].copy()

    off_counts = sync_off_df['正規化車號'].value_counts().reset_index()
    off_counts.columns = ['車頭車號', '官方台數']
    sys_counts = sync_sys_df['正規化車號'].value_counts().reset_index()
    sys_counts.columns = ['車頭車號', '系統台數']
    merged = pd.merge(off_counts, sys_counts, on='車頭車號', how='outer').fillna(0)
    merged['差異 (多按或漏按)'] = merged['系統台數'] - merged['官方台數']

    # 系統裡已登記過的官方聯單序號 → 對應紀錄索引（不限日期、不含FDA）
    serial_to_idx_all = {}
    for _idx, _s in df_logs.loc[df_logs['運送目的地'] != 'FDA', '_serial_clean'].items():
        if _s:
            serial_to_idx_all.setdefault(_s, _idx)

    to_delete_indices = []
    to_add_records = []
    updates = {}
    already_reconciled = 0
    protected_count = 0

    # 只處理「這次CSV裡有提到的車號」，不動系統裡其他車號的紀錄
    plates = set(sync_off_df['正規化車號'])
    for plate in plates:
        o_subset = sync_off_df[sync_off_df['正規化車號'] == plate].sort_values('FullTime')
        s_subset = df_logs[(df_logs['ParsedDate'] == sync_date) & (df_logs['正規化車號'] == plate) & (df_logs['運送目的地'] != 'FDA')].sort_values('FullTime')

        s_indices = s_subset.index.tolist()
        used_s = set()

        for _, o_row in o_subset.iterrows():
            o_t = o_row['FullTime']
            o_plate_raw = o_row[PLATE_COL]
            o_serial_clean = _clean_serial(o_row[SERIAL_COL])

            # (1) 序號系統裡已經有了 → 先前已對過帳，原封不動
            if o_serial_clean and o_serial_clean in serial_to_idx_all:
                _existing_idx = serial_to_idx_all[o_serial_clean]
                if _existing_idx is not None:
                    used_s.add(_existing_idx)
                already_reconciled += 1
                continue

            # (2) 只在還沒有官方序號的紀錄裡，找時間最接近的配對
            best_s_idx = None
            best_diff = float('inf')
            for idx in s_indices:
                if idx in used_s:
                    continue
                if s_subset.loc[idx, '_serial_clean']:
                    continue
                st_t = s_subset.loc[idx, 'FullTime']
                if pd.isna(o_t) or pd.isna(st_t):
                    continue
                diff = abs((o_t - st_t).total_seconds())
                if diff < best_diff:
                    best_diff = diff
                    best_s_idx = idx

            if best_s_idx is not None and best_diff < 7200:
                used_s.add(best_s_idx)
                updates[best_s_idx] = {"時間": o_t.strftime("%H:%M:%S"), "聯單序號": o_serial_clean}
                if o_serial_clean:
                    serial_to_idx_all[o_serial_clean] = best_s_idx
            else:
                to_add_records.append({
                    "日期": o_t.strftime("%Y-%m-%d") if pd.notnull(o_t) else sync_date.strftime("%Y-%m-%d"),
                    "時間": o_t.strftime("%H:%M:%S") if pd.notnull(o_t) else "00:00:00",
                    "車頭車號": o_plate_raw,
                    "出土分區": "未指定",
                    "載運方量(m³)": 12.0,
                    "備註": "系統依官方CSV補齊（非官方補登）",
                    "聯單序號": o_serial_clean,
                    "作廢": False,
                    "作廢原因": "",
                    "運送目的地": "台北港",
                    "車次數": 1,
                })
                if o_serial_clean:
                    serial_to_idx_all[o_serial_clean] = None  # 同一份CSV裡序號重複出現時不要重複補

        for idx in s_indices:
            if idx in used_s:
                continue
            if s_subset.loc[idx, '_serial_clean']:
                protected_count += 1  # 已有官方聯單序號 → 絕對不刪
                continue
            if _is_voided(s_subset.loc[idx, '作廢']):
                continue  # 手動作廢的紀錄是稽核軌跡，不刪
            to_delete_indices.append(idx)

    # 當天所有土質都到齊：清掉車牌不在任何CSV裡、又沒有官方序號的殘留紀錄
    extra_cleaned = 0
    if final_of_day and not sync_off_df.empty:
        _already_marked = set(to_delete_indices)
        _day_rows = df_logs[(df_logs['ParsedDate'] == sync_date) & (df_logs['運送目的地'] != 'FDA')]
        for _idx, _r in _day_rows.iterrows():
            if _idx in _already_marked or _r['正規化車號'] in plates:
                continue
            if _r['_serial_clean']:
                protected_count += 1
                continue
            if _is_voided(_r['作廢']):
                continue
            to_delete_indices.append(_idx)
            extra_cleaned += 1

    for idx, vals in updates.items():
        for k, v in vals.items():
            df_logs.loc[idx, k] = v
    if to_delete_indices:
        df_logs = df_logs.drop(index=to_delete_indices)
    if to_add_records:
        df_logs = pd.concat([df_logs, pd.DataFrame(to_add_records)], ignore_index=True)

    # 依聯單序號補上官方補登／司機／土質／進場時間（只填欄位，不增刪）
    df_logs, enriched_n, _unmatched = apply_official_fields(df_logs, official_df)

    _chk_date = pd.to_datetime(df_logs['日期'], errors='coerce').dt.date
    _chk_mask = (
        (_chk_date == sync_date)
        & (df_logs['聯單序號'].apply(_clean_serial) == '')
        & (df_logs['運送目的地'].fillna('台北港').astype(str) != 'FDA')
        & (~df_logs['作廢'].apply(_is_voided))
    )
    leftover_df = df_logs.loc[_chk_mask, ['車頭車號', '時間', '出土分區']].reset_index(drop=True)

    df_logs['SortTime'] = pd.to_datetime(df_logs['日期'].astype(str) + ' ' + df_logs['時間'].astype(str), errors='coerce')
    df_logs = df_logs.sort_values('SortTime').drop(
        columns=['ParsedDate', 'FullTime', '正規化車號', 'SortTime', '_serial_clean'], errors='ignore')

    stats = {
        "updated": len(updates),
        "added": len(to_add_records),
        "deleted": len(to_delete_indices),
        "already": already_reconciled,
        "protected": protected_count,
        "extra_cleaned": extra_cleaned,
        "enriched": enriched_n,
        "official_rows": len(sync_off_df),
        "merged": merged,
        "leftover": leftover_df,
    }
    return df_logs, stats
