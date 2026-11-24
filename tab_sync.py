"""🧾 官方聯單對帳 分頁。"""

import pandas as pd
import streamlit as st
from datetime import datetime
from datetime import timedelta
from data_store import apply_official_fields, load_sheet_data, save_sheet_data
from reconcile import missing_columns, reconcile_day


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

            missing_cols = missing_columns(official_df)

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
                        # 對帳規則統一放在 reconcile.py（本機自動下載腳本 auto_manifest.py 也用同一份）
                        df_logs, stats = reconcile_day(load_sheet_data("dispatch_logs"), official_df, sync_date, final_csv_of_day)
                        merged, leftover_df = stats["merged"], stats["leftover"]
                        extra_cleaned = stats["extra_cleaned"]
                        sync_off_empty = stats["official_rows"] == 0
                        save_ok = save_sheet_data("dispatch_logs", df_logs)
                    if save_ok:
                        st.success(
                            f"✅ 比對完成並已直接寫入雲端！更新 {stats['updated']} 筆、新增 {stats['added']} 筆、剔除 {stats['deleted']} 筆。"
                            f"另有 {stats['already']} 筆官方聯單先前已對過帳（序號已存在）維持不動，"
                            f"{stats['protected']} 筆已有官方聯單序號的紀錄因受保護而沒有被刪除。"
                            + (f"另外清除了 {extra_cleaned} 筆不在任何CSV裡、也沒有官方聯單序號的殘留紀錄。" if extra_cleaned else "")
                            + f"（同時依聯單序號補上 {stats['enriched']} 筆的官方補登／司機／土質／進場時間欄位。）"
                        )
                        if final_csv_of_day and sync_off_empty:
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
