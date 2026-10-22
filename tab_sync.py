"""🧾 官方聯單對帳 分頁。"""

import pandas as pd
import streamlit as st
from datetime import datetime
from datetime import timedelta
from data_store import _clean_serial, _is_voided, apply_official_fields, load_sheet_data, save_sheet_data


def render(df_results, global_stage_choice):
    st.write("### 🧾 官方聯單時間序列精準對帳與校正")
    st.info("💡 演算法說明：系統會自動尋找時間最接近的紀錄綁定並寫入聯單序號（保留分區），多出的自動剔除，少按的會依官方時序自動補齊。上傳CSV後按一次按鈕即可完成比對並直接寫入雲端，不需要再分兩步驟。")

    with st.expander("📎 補充官方欄位（補登／司機／土質／進場時間）— 只填欄位，不動車次、不新增、不刪除", expanded=False):
        st.caption("官方CSV裡的「是否為出場補登」才是真正的補登；雲端舊備註的「官方聯單補登」只是系統自動補上資料的註記，不是官方補登。"
                   "把CSV（可一次選多份、整個月的都可以）上傳一次，系統會依「聯單序號」把官方的補登旗標與原因、司機姓名、土質、進場時間填進雲端 dispatch_logs 對應的紀錄，"
                   "「🔬 聯單進階分析」的補登異常率就會用這個真實數字。以後每次做日常對帳也會自動帶入，不必每個月重傳。")
        enrich_files = st.file_uploader("上傳官方CSV（可多選）", type=["csv"], accept_multiple_files=True, key="enrich_csv_files")
        if enrich_files and st.button("📎 只補充官方欄位（不改動車次）", key="btn_enrich_official"):
            frames, bad = [], []
            for f in enrich_files:
                try:
                    try:
                        frames.append(pd.read_csv(f, encoding='utf-8'))
                    except UnicodeDecodeError:
                        f.seek(0)
                        frames.append(pd.read_csv(f, encoding='big5'))
                except Exception as e:
                    bad.append(f"{f.name}：{e}")
            if bad:
                st.error("以下檔案讀取失敗：" + "；".join(bad))
            if frames:
                big = pd.concat(frames, ignore_index=True)
                if "聯單序號" not in big.columns:
                    st.error("CSV 缺少「聯單序號」欄位，無法對應。")
                else:
                    cur_logs = load_sheet_data("dispatch_logs")
                    new_logs, n_match, unmatched = apply_official_fields(cur_logs, big)
                    flag_col = "是否為出場補登 (0=正常, 1=補登)"
                    n_real = int(pd.to_numeric(big.get(flag_col, 0), errors="coerce").fillna(0).astype(int).sum()) if flag_col in big.columns else 0
                    if n_match and save_sheet_data("dispatch_logs", new_logs):
                        st.success(f"✅ 已補充 {n_match} 筆紀錄的官方欄位（CSV 共 {len(big)} 筆，其中官方出場補登 {n_real} 筆）。"
                                   + (f"另有 {len(unmatched)} 張CSV聯單在雲端找不到對應序號（可能還沒對帳過）。" if unmatched else ""))
                    elif not n_match:
                        st.warning("沒有任何CSV聯單序號對得上雲端紀錄，所以沒有寫入。請先用下方的「官方對帳」把該日期的序號對上之後再補充。")

    sync_date = st.date_input("選擇對帳日期：", value=(datetime.utcnow() + timedelta(hours=8)).date())

    uploaded_csv = st.file_uploader("上傳官方電子聯單 CSV 檔案", type=["csv"])

    if uploaded_csv:
        try:
            try:
                official_df = pd.read_csv(uploaded_csv, encoding='utf-8')
            except UnicodeDecodeError:
                uploaded_csv.seek(0)
                official_df = pd.read_csv(uploaded_csv, encoding='big5')

            plate_col = "出場車頭車號"
            datetime_col = "出場日期"
            serial_col = "聯單序號"

            missing_cols = [c for c in [plate_col, datetime_col, serial_col] if c not in official_df.columns]

            if missing_cols:
                st.error(f"⚠️ CSV 檔案格式不符！缺少必要欄位：{', '.join(missing_cols)}。請確認官方匯出的檔案是否包含這些欄位。")
            else:
                final_csv_of_day = st.checkbox(
                    "📌 這是這一天「最後一份」CSV（所有土質都已經上傳完了）→ 同時清除當天不在任何CSV裡、也沒有官方聯單序號的殘留紀錄",
                    value=False, key="sync_final_csv_of_day",
                    help="平常（沒勾選）對帳只會處理「這份CSV裡有出現的車牌」，這是為了讓你分批上傳不同土質的CSV時，不會把另一份CSV的車誤刪。"
                         "但這樣一來，車牌打錯、或多按到不屬於任何CSV的車，就不會被清掉。"
                         "當天所有CSV都傳完之後，最後一份勾選這個，就會把這種殘留紀錄一併清掉。已有官方聯單序號的紀錄與已作廢的紀錄一律受保護，不會被刪。",
                )
                if st.button("🚀 開始比對並直接寫入雲端", use_container_width=True):
                    with st.spinner("比對中並同步寫入雲端，請稍候..."):
                        official_df['FullTime'] = pd.to_datetime(official_df[datetime_col], errors='coerce')
                        official_df['ParsedDate'] = official_df['FullTime'].dt.date
                        official_df['正規化車號'] = official_df[plate_col].astype(str).str.replace(r'\W+', '', regex=True).str.upper()

                        sync_off_df = official_df[official_df['ParsedDate'] == sync_date].copy()

                        df_logs = load_sheet_data("dispatch_logs")
                        if df_logs.empty:
                            df_logs = pd.DataFrame(columns=["日期", "時間", "車頭車號", "出土分區", "載運方量(m³)", "備註", "聯單序號"])
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

                        df_logs['ParsedDate'] = pd.to_datetime(df_logs['日期']).dt.date
                        df_logs['FullTime'] = pd.to_datetime(df_logs['日期'].astype(str) + ' ' + df_logs['時間'].astype(str), errors='coerce')
                        df_logs['正規化車號'] = df_logs['車頭車號'].astype(str).str.replace(r'\W+', '', regex=True).str.upper()
                        # 這筆紀錄有沒有「官方聯單序號」：有的話代表已經對過帳，這次對帳絕對不能刪、也不能被拿去重新配對覆蓋
                        df_logs['_serial_clean'] = df_logs['聯單序號'].apply(_clean_serial)
                        # 官方對帳只處理「台北港」的正式聯單車輛，FDA簡化集計紀錄完全不是這套系統的東西，
                        # 一定要排除掉，不然會被誤判成「系統多出來、官方聯單沒有對應」而被刪除
                        sync_sys_df = df_logs[(df_logs['ParsedDate'] == sync_date) & (df_logs['運送目的地'] != 'FDA')].copy()

                        # 先算出差異摘要，等下比對完一起顯示給你看
                        off_counts = sync_off_df['正規化車號'].value_counts().reset_index()
                        off_counts.columns = ['車頭車號', '官方台數']
                        sys_counts = sync_sys_df['正規化車號'].value_counts().reset_index()
                        sys_counts.columns = ['車頭車號', '系統台數']
                        merged = pd.merge(off_counts, sys_counts, on='車頭車號', how='outer').fillna(0)
                        merged['差異 (多按或漏按)'] = merged['系統台數'] - merged['官方台數']

                        # 系統裡已經登記過的官方聯單序號 → 對應的紀錄索引（不限日期、不含FDA），用來判斷「這張官方聯單先前是不是已經對過帳」
                        serial_to_idx_all = {}
                        for _idx, _s in df_logs.loc[df_logs['運送目的地'] != 'FDA', '_serial_clean'].items():
                            if _s:
                                serial_to_idx_all.setdefault(_s, _idx)

                        # 直接執行時間序列精準比對覆蓋與序號寫入
                        to_delete_indices = []
                        to_add_records = []
                        updates = {}
                        already_reconciled = 0   # 官方序號系統裡早就有了、原封不動的筆數
                        protected_count = 0      # 這次沒配到官方資料、但因為已有官方序號而受保護沒被刪的筆數

                        # 只處理「這次上傳的CSV裡有提到的車號」，不要去動系統裡其他車號的紀錄
                        # （例如同一天另外上傳的不同土質CSV已經對過帳的資料）。
                        plates = set(sync_off_df['正規化車號'])
                        for plate in plates:
                            o_subset = sync_off_df[sync_off_df['正規化車號'] == plate].sort_values('FullTime')
                            s_subset = df_logs[(df_logs['ParsedDate'] == sync_date) & (df_logs['正規化車號'] == plate) & (df_logs['運送目的地'] != 'FDA')].sort_values('FullTime')

                            s_indices = s_subset.index.tolist()
                            used_s = set()

                            for _, o_row in o_subset.iterrows():
                                o_t = o_row['FullTime']
                                o_plate_raw = o_row[plate_col]
                                o_serial_clean = _clean_serial(o_row[serial_col])

                                # (1) 這張官方聯單序號系統裡已經有了 → 先前（例如上一份不同土質的CSV）已經對過帳，原封不動
                                if o_serial_clean and o_serial_clean in serial_to_idx_all:
                                    _existing_idx = serial_to_idx_all[o_serial_clean]
                                    if _existing_idx is not None:
                                        used_s.add(_existing_idx)
                                    already_reconciled += 1
                                    continue

                                # (2) 只在「還沒有官方序號」的紀錄裡，找時間最接近的配對；
                                #     已經有別張官方序號的紀錄不能被拿來配對，不然會把人家的序號蓋掉
                                best_s_idx = None
                                best_diff = float('inf')

                                for idx in s_indices:
                                    if idx in used_s: continue
                                    if s_subset.loc[idx, '_serial_clean']: continue
                                    st_t = s_subset.loc[idx, 'FullTime']
                                    if pd.isna(o_t) or pd.isna(st_t): continue
                                    diff = abs((o_t - st_t).total_seconds())
                                    if diff < best_diff:
                                        best_diff = diff
                                        best_s_idx = idx

                                if best_s_idx is not None and best_diff < 7200:
                                    used_s.add(best_s_idx)
                                    updates[best_s_idx] = {
                                        "時間": o_t.strftime("%H:%M:%S"),
                                        "聯單序號": o_serial_clean
                                    }
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
                                        serial_to_idx_all[o_serial_clean] = None  # 同一份CSV裡序號重複出現時不要重複補登

                            for idx in s_indices:
                                if idx in used_s:
                                    continue
                                if s_subset.loc[idx, '_serial_clean']:
                                    protected_count += 1  # 已有官方聯單序號 → 絕對不刪
                                    continue
                                if _is_voided(s_subset.loc[idx, '作廢']):
                                    continue  # 你手動標記作廢的紀錄是稽核軌跡，也不能被對帳刪掉
                                to_delete_indices.append(idx)

                        # 勾選「最後一份CSV」時：當天所有土質都傳完了，順便清掉「車牌根本不在任何CSV裡」、又沒有官方聯單序號的殘留紀錄
                        # （通常是車牌輸入錯誤、或多按的）。已有官方序號、已作廢的紀錄一樣完全受保護。
                        extra_cleaned = 0
                        if final_csv_of_day and not sync_off_df.empty:
                            _already_marked = set(to_delete_indices)
                            _day_rows = df_logs[(df_logs['ParsedDate'] == sync_date) & (df_logs['運送目的地'] != 'FDA')]
                            for _idx, _r in _day_rows.iterrows():
                                if _idx in _already_marked or _r['正規化車號'] in plates:
                                    continue  # 有出現在CSV的車牌上面已經處理過了
                                if _r['_serial_clean']:
                                    protected_count += 1
                                    continue
                                if _is_voided(_r['作廢']):
                                    continue
                                to_delete_indices.append(_idx)
                                extra_cleaned += 1

                        if updates:
                            for idx, vals in updates.items():
                                for k, v in vals.items():
                                    df_logs.loc[idx, k] = v

                        if to_delete_indices:
                            df_logs = df_logs.drop(index=to_delete_indices)

                        if to_add_records:
                            df_logs = pd.concat([df_logs, pd.DataFrame(to_add_records)], ignore_index=True)

                        # 順便把官方CSV裡真正的補登旗標/原因、司機、土質依聯單序號填進去（只填欄位，不增刪）
                        df_logs, enriched_n, _unmatched_serials = apply_official_fields(df_logs, official_df)

                        # 對帳完成後，這一天還留在系統裡、卻沒有官方聯單序號的台北港紀錄（多按、車牌打錯，或屬於還沒上傳的其他土質CSV）
                        _chk_date = pd.to_datetime(df_logs['日期'], errors='coerce').dt.date
                        _chk_mask = (
                            (_chk_date == sync_date)
                            & (df_logs['聯單序號'].apply(_clean_serial) == '')
                            & (df_logs['運送目的地'].fillna('台北港').astype(str) != 'FDA')
                            & (~df_logs['作廢'].apply(_is_voided))
                        )
                        leftover_df = df_logs.loc[_chk_mask, ['車頭車號', '時間', '出土分區']].reset_index(drop=True)

                        df_logs['SortTime'] = pd.to_datetime(df_logs['日期'].astype(str) + ' ' + df_logs['時間'].astype(str), errors='coerce')
                        df_logs = df_logs.sort_values('SortTime').drop(columns=['ParsedDate', 'FullTime', '正規化車號', 'SortTime', '_serial_clean'])

                        save_ok = save_sheet_data("dispatch_logs", df_logs)

                    if save_ok:
                        st.success(
                            f"✅ 比對完成並已直接寫入雲端！更新 {len(updates)} 筆、新增 {len(to_add_records)} 筆、剔除 {len(to_delete_indices)} 筆。"
                            f"另有 {already_reconciled} 筆官方聯單先前已對過帳（序號已存在）維持不動，"
                            f"{protected_count} 筆已有官方聯單序號的紀錄因受保護而沒有被刪除。"
                            + (f"另外清除了 {extra_cleaned} 筆不在任何CSV裡、也沒有官方聯單序號的殘留紀錄。" if extra_cleaned else "")
                            + f"（同時依聯單序號補上 {enriched_n} 筆的官方補登／司機／土質／進場時間欄位。）"
                        )
                        if final_csv_of_day and sync_off_df.empty:
                            st.warning("⚠️ 這份CSV裡沒有你選的這一天的資料，所以已略過「清除殘留紀錄」，避免誤刪。請確認對帳日期是否選對。")
                        if not leftover_df.empty:
                            st.warning(
                                f"⚠️ 這一天系統裡還有 **{len(leftover_df)} 筆**沒有官方聯單序號的紀錄（可能是多按、車牌輸入錯誤，"
                                f"或屬於還沒上傳的其他土質CSV）。如果這是這一天最後一份CSV，勾選上方「最後一份CSV」後再按一次對帳，就會一併清掉："
                            )
                            st.dataframe(leftover_df, use_container_width=True, hide_index=True)
                        st.markdown("##### 比對差異摘要（僅供參考，資料已寫入雲端）")
                        st.dataframe(merged, use_container_width=True)
                    else:
                        st.error("寫入雲端失敗，請檢查連線或稍後再試。")

        except Exception as e:
            st.error(f"檔案解析或比對失敗：{e}")
