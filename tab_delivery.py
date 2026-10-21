"""✍️ 現場廠商簽收 分頁。"""

import numpy as np
import pandas as pd
import re
import streamlit as st
from datetime import datetime
from datetime import timedelta
from data_store import load_sheet_data, save_sheet_data
from pdf_reports import generate_delivery_pdf


def render(df_results, global_stage_choice):
    st.write("### ✍️ 現場廠商交付簽收管理")

    df_delivery = load_sheet_data("manifest_delivery")
    if df_delivery.empty:
        df_delivery = pd.DataFrame(columns=["交付日期", "交付時間", "廠商名稱", "聯單類型", "起始序號", "發放張數", "簽收人姓名", "簽名資料"])

    if not df_delivery.empty:
        try:
            st.markdown("#### 📋 歷史交付簽收對帳看板")

            record_options = [f"[{r['交付日期']} {r['交付時間']}] {r['廠商名稱']} {r['簽收人姓名']} ({r['聯單類型']} 聯單 / {int(r['發放張數']) if pd.notnull(r['發放張數']) else 0}張)" for idx, r in df_delivery.iterrows()]
            selected_record_idx = st.selectbox("🔍 選擇一筆歷史紀錄：", options=range(len(record_options)), format_func=lambda x: record_options[x], key="select_history_delivery")

            chosen_row = df_delivery.iloc[selected_record_idx]

            clean_history_serial = str(chosen_row['起始序號']).strip()
            if clean_history_serial.endswith('.0'):
                clean_history_serial = clean_history_serial[:-2]

            st.info(f"""**詳細交付核對資訊：**
* 交付時間： `{chosen_row['交付日期']} {chosen_row['交付時間']}`
* 簽收廠商： `{chosen_row['廠商名稱']}`
* 聯單規格： `{chosen_row['聯單類型']}`
* 發放張數： `{int(chosen_row['發放張數']) if pd.notnull(chosen_row['發放張數']) else 0} 張`
* 起始序號： `{clean_history_serial}`
* 現場簽收人： `{chosen_row['簽收人姓名']}`

*(簽名影像已隱藏，請匯出 PDF 檢視)*""")

            st.divider()

            st.markdown("#### 📄 導出交付簽收 PDF 報表")
            col_pdf1, col_pdf2 = st.columns([2, 1])
            with col_pdf1:
                pdf_scope = st.selectbox("選擇要匯出的聯單範圍：", options=["全部聯單類型", "B1", "B2-3", "B4", "B5"], key="pdf_scope_select")
            with col_pdf2:
                st.write("")
                st.write("")
                if pdf_scope == "全部聯單類型":
                    df_pdf_target = df_delivery.copy()
                else:
                    df_pdf_target = df_delivery[df_delivery["聯單類型"] == pdf_scope].copy()

                if df_pdf_target.empty:
                    st.warning("⚠️ 該範圍內無任何交付紀錄，無法產生報表。")
                else:
                    if st.button("📥 下載 PDF 報表", key="btn_download_delivery_pdf"):
                        with st.spinner("產生中..."):
                            try:
                                delivery_pdf_path = generate_delivery_pdf(df_pdf_target, pdf_scope)
                                with open(delivery_pdf_path, "rb") as pdf_file:
                                    st.download_button(
                                        label=f"✅ 點此儲存 {pdf_scope} 簽收報表 (含影像)",
                                        data=pdf_file,
                                        file_name=f"delivery_report_{pdf_scope}_{datetime.now().strftime('%Y%m%d')}.pdf",
                                        mime="application/pdf",
                                        use_container_width=True
                                    )
                            except Exception as ex:
                                st.error(f"PDF 導出失敗：{ex}")
            st.divider()
        except Exception as e:
            st.error(f"⚠️ 歷史交付看板渲染發生錯誤（已略過，其餘功能不受影響）：{e}")

    st.markdown("#### 📥 新增聯單現場發放")

    input_type = st.selectbox("選擇本次發放的聯單類型", options=["B1", "B2-3", "B4", "B5"], key="delivery_type_select")

    df_manifest_check = load_sheet_data("manifest_settings")
    df_logs_check = load_sheet_data("dispatch_logs")

    available_stock = 0
    if not df_manifest_check.empty and "聯單類型" in df_manifest_check.columns:
        col_target = "聯單數量_已列印" if "聯單數量_已列印" in df_manifest_check.columns else "已列印數量"
        if col_target in df_manifest_check.columns:
            used_counts_check = {t: 0 for t in df_manifest_check["聯單類型"]}
            if not df_logs_check.empty and "聯單序號" in df_logs_check.columns:
                valid_serials_check = df_logs_check["聯單序號"].dropna().astype(str).str.strip().str.upper()
                valid_serials_check = valid_serials_check[(valid_serials_check != "") & (valid_serials_check != "NAN") & (valid_serials_check != "NONE")]
                for m_type in df_manifest_check["聯單類型"]:
                    count = valid_serials_check.str.contains(m_type.upper(), regex=False).sum()
                    used_counts_check[m_type] = count

            df_manifest_check["已使用數量"] = df_manifest_check["聯單類型"].map(used_counts_check).fillna(0).astype(int)
            df_manifest_check["現場剩餘可用"] = pd.to_numeric(df_manifest_check[col_target], errors='coerce').fillna(0).astype(int) - df_manifest_check["已使用數量"]

            match_stock_row = df_manifest_check[df_manifest_check["聯單類型"] == input_type]
            if not match_stock_row.empty and "現場剩餘可用" in match_stock_row.columns:
                available_stock = int(match_stock_row.iloc[0]["現場剩餘可用"])

    st.write(f"📊 該類型聯單目前雲端剩餘可交付數量： **{available_stock}** 張")

    auto_serial = ""
    if not df_delivery.empty and "聯單類型" in df_delivery.columns and "起始序號" in df_delivery.columns and "發放張數" in df_delivery.columns:
        df_type_last = df_delivery[df_delivery["聯單類型"] == input_type]
        if not df_type_last.empty:
            last_record = df_type_last.iloc[-1]
            last_serial = str(last_record["起始序號"]).strip()
            if last_serial.endswith('.0'):
                last_serial = last_serial[:-2]
            try:
                last_count = int(last_record["發放張數"]) if pd.notnull(last_record["發放張數"]) else 0
                match = re.search(r'\d+', last_serial)
                if match:
                    num_str = match.group()
                    prefix = last_serial[:match.start()]
                    suffix = last_serial[match.end():]
                    next_num = int(num_str) + last_count
                    auto_serial = f"{prefix}{next_num:0{len(num_str)}d}{suffix}"
            except Exception:
                auto_serial = ""

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        input_vendor = st.text_input("廠商名稱", value="力勤", disabled=True)
        input_count = st.number_input("發放張數", min_value=1, value=50, step=1, format="%d")
    with col_d2:
        input_serial = st.text_input("聯單起始序號 (可根據現場實物修改)", value=auto_serial)
        input_name = st.text_input("廠商簽收人姓名", value="")

    sign_active = st.checkbox("✍️ 填寫完畢後，點此展開簽名板")

    base64_sign = ""
    if sign_active:
        from streamlit_drawable_canvas import st_canvas
        from PIL import Image
        import io
        import base64

        canvas_sign = st_canvas(
            fill_color="rgba(255, 255, 255, 1)",
            stroke_width=3,
            stroke_color="#000000",
            background_color="#EEEEEE",
            height=200,
            width=340,
            drawing_mode="freedraw",
            update_streamlit=True,
            key=f"canvas_delivery_form_{st.session_state['canvas_key_counter']}",
        )

        if st.button("確認交付並永久儲存簽收紀錄", use_container_width=True):
            if not input_serial.strip():
                st.error("請填寫聯單起始序號")
            elif not input_name.strip():
                st.error("請填寫廠商簽收人姓名")
            elif canvas_sign.image_data is None or np.sum(canvas_sign.image_data[:, :, 3]) == 0:
                st.error("請完成手寫簽名後再提交")
            elif int(input_count) > available_stock:
                st.error(f"❌ 拒絕紀錄：庫存量不足！目前庫存僅剩 {available_stock} 張，無法超量發放 {int(input_count)} 張。")
            else:
                img_array = canvas_sign.image_data.astype('uint8')
                img = Image.fromarray(img_array, 'RGBA')
                buffered = io.BytesIO()
                img.save(buffered, format="PNG")
                base64_sign = base64.b64encode(buffered.getvalue()).decode()

                clean_input_serial = input_serial.strip()
                if clean_input_serial.endswith('.0'):
                    clean_input_serial = clean_input_serial[:-2]

                now_tw = datetime.utcnow() + timedelta(hours=8)
                new_record = {
                    "交付日期": now_tw.strftime("%Y-%m-%d"),
                    "交付時間": now_tw.strftime("%H:%M:%S"),
                    "廠商名稱": input_vendor.strip(),
                    "聯單類型": input_type,
                    "起始序號": clean_input_serial,
                    "發放張數": int(input_count),
                    "簽收人姓名": input_name.strip(),
                    "簽名資料": base64_sign
                }

                df_delivery = pd.concat([df_delivery, pd.DataFrame([new_record])], ignore_index=True)

                if save_sheet_data("manifest_delivery", df_delivery):
                    st.session_state['canvas_key_counter'] += 1
                    st.success("✅ 聯單現場交付成功！紀錄與簽名已即時寫入雲端。")
                    st.rerun()
