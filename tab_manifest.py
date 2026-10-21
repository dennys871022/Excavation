"""🎫 聯單庫存管理 分頁。"""

import pandas as pd
import streamlit as st
from data_store import load_sheet_data, save_sheet_data


def render(df_results, global_stage_choice):
    st.write("### 🎫 聯單庫存與發放管理")

    df_manifest = load_sheet_data("manifest_settings")
    if df_manifest.empty:
        df_manifest = pd.DataFrame({
            "聯單類型": ["B1", "B2-3", "B4", "B5"],
            "總配額": [1000, 2790, 2821, 30],
            "已列印數量": [0, 0, 0, 0]
        })

    df_logs = load_sheet_data("dispatch_logs")

    used_counts = {t: 0 for t in df_manifest["聯單類型"]}
    if not df_logs.empty and "聯單序號" in df_logs.columns:
        valid_serials = df_logs["聯單序號"].dropna().astype(str).str.strip().str.upper()
        valid_serials = valid_serials[(valid_serials != "") & (valid_serials != "NAN") & (valid_serials != "NONE")]

        for m_type in df_manifest["聯單類型"]:
            count = valid_serials.str.contains(m_type.upper(), regex=False).sum()
            used_counts[m_type] = count

    df_manifest["總配額"] = pd.to_numeric(df_manifest["總配額"], errors='coerce').fillna(0).astype(int)
    col_print_name = "聯單數量_已列印" if "聯單數量_已列印" in df_manifest.columns else "已列印數量"
    df_manifest["已列印數量"] = pd.to_numeric(df_manifest[col_print_name], errors='coerce').fillna(0).astype(int)
    df_manifest["已使用數量"] = df_manifest["聯單類型"].map(used_counts).fillna(0).astype(int)
    df_manifest["現場剩餘可用"] = df_manifest["已列印數量"] - df_manifest["已使用數量"]
    df_manifest["雲端未列印配額"] = df_manifest["總配額"] - df_manifest["已列印數量"]

    st.info("請於下方表格直接修改「已列印數量」，系統會根據對帳結果自動計算現場剩餘的可用張數。")
    edited_manifest = st.data_editor(
        df_manifest,
        column_config={
            "總配額": st.column_config.NumberColumn(format="%d"),
            "已列印數量": st.column_config.NumberColumn(format="%d", min_value=0),
            "已使用數量": st.column_config.NumberColumn(format="%d"),
            "現場剩餘可用": st.column_config.NumberColumn(format="%d"),
            "雲端未列印配額": st.column_config.NumberColumn(format="%d"),
        },
        disabled=["聯單類型", "總配額", "已使用數量", "現場剩餘可用", "雲端未列印配額"],
        hide_index=True,
        use_container_width=True
    )

    if st.button("💾 儲存列印數量更新"):
        if save_sheet_data("manifest_settings", edited_manifest[['聯單類型', '總配額', '已列印數量']]):
            st.success("已更新列印數量！")
            st.rerun()

    st.markdown("#### 🚨 現場庫存狀態警報")

    alert_triggered = False
    for idx, row in df_manifest.iterrows():
        if pd.notnull(row["現場剩餘可用"]) and pd.notnull(row["雲端未列印配額"]):
            if row["現場剩餘可用"] < 100 and row["雲端未列印配額"] > 0:
                st.error(f"⚠️ **【警告】{row['聯單類型']}** 聯單現場僅剩 **{int(row['現場剩餘可用'])}** 張！請盡速列印補充備用。 (尚有雲端配額 {int(row['雲端未列印配額'])} 張)")
                alert_triggered = True

    if not alert_triggered:
        st.success("✅ 目前所有類型的聯單現場庫存皆十分充足，或已全數列印完畢。")
