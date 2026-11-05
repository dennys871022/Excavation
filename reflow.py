"""♻️ 依實方重新分配出土分區：修正「換算係數調整前」造成的分區指派偏差。

車次載運方量（每車12m³）是鬆方，分區門檻是實方（鬆方 ÷ LOOSE_SOIL_FACTOR）。
換算係數調整後，過去自動分配出去的車次在地圖上的位置會跟現場對不上，這裡把車次依日期時間順序，
按「分區挖掘順序」逐區填滿（填到該區在目前階段的目標就換下一區），重新寫回出土分區。
"""
import pandas as pd
import streamlit as st

from config import LOOSE_SOIL_FACTOR
from data_store import _is_voided, load_sheet_data, save_sheet_data
from stage_calc import get_stage_index, sync_stage_daily_log
from zones import filter_zones_for_stage, get_stage_remaining_capacity

PRE_ZONE = "開挖前土方"


def default_zone_order(df_results, stage_idx):
    """預設挖掘順序：西側（行政棟）整塊先挖；東側先挖 A、B' 兩排（由西往東一欄一欄，同欄 A 在 B' 前），
    再依序 C'、D'、E'、F' 排，最後是滯洪池。實際順序跟這個不同時，在畫面上自己重選即可。"""
    z = filter_zones_for_stage(df_results, stage_idx).copy()

    def key(r):
        code = str(r["分區代號"])
        col = round(float(r["x_min"]), 0)
        if float(r["x_min"]) < -2694:
            return (0, 0, col, -float(r["y_max"]))
        if float(r["x_min"]) > -2607:
            return (2, 0, col, -float(r["y_max"]))
        letter = code[0]
        grp = 0 if letter in ("A", "B") else 1 + max(0, ord(letter) - ord("C"))
        return (1, grp, col, -float(r["y_max"]))

    z["_k"] = z.apply(key, axis=1)
    return z.sort_values("_k")["分區代號"].astype(str).tolist()


def reflow_assignments(logs, df_results, stage_idx, zone_order, include_unassigned=True):
    """純函式：回傳 (new_zone_series[index=logs.index, 只含被重新分配的列], summary_df, leftover_count)。"""
    caps = {}
    for z in zone_order:
        cap = get_stage_remaining_capacity(z, df_results, stage_idx, {})
        if cap is not None and cap > 0:
            caps[z] = float(cap)
    order = [z for z in zone_order if z in caps]
    zcol = logs["出土分區"].fillna("").astype(str).str.strip()
    voided = logs["作廢"].apply(_is_voided) if "作廢" in logs.columns else pd.Series(False, index=logs.index)
    in_order = zcol.isin(order)
    unassigned = zcol.isin(["未指定", "", "nan"]) if include_unassigned else pd.Series(False, index=logs.index)
    elig = logs[(~voided) & (in_order | unassigned)].copy()
    if elig.empty:
        return pd.Series(dtype=object), pd.DataFrame(), 0
    elig["_dt"] = pd.to_datetime(elig["日期"].astype(str).str[:10] + " " + elig.get("時間", "").fillna("").astype(str), errors="coerce")
    elig["_vol"] = pd.to_numeric(elig.get("載運方量(m³)", 12.0), errors="coerce").fillna(12.0) / LOOSE_SOIL_FACTOR
    elig = elig.sort_values(["_dt"], kind="stable")
    remaining = dict(caps)
    new = {}
    p = 0
    left = 0
    for idx, r in elig.iterrows():
        while p < len(order) and remaining[order[p]] <= 0:
            p += 1
        if p >= len(order):
            new[idx] = "未指定"
            left += 1
            continue
        z = order[p]
        new[idx] = z
        remaining[z] -= r["_vol"]          # 最後一車允許些微超挖收尾，跟批量分配規則一致
    new_s = pd.Series(new)
    before = elig["_vol"].groupby(zcol[elig.index]).sum()
    after = elig["_vol"].groupby(new_s).sum()
    rows = []
    for z in order:
        rows.append({"分區": z, "本階段目標(實方)": round(caps[z]), "調整前(實方)": round(float(before.get(z, 0))),
                     "調整後(實方)": round(float(after.get(z, 0)))})
    return new_s, pd.DataFrame(rows), left


def render_reflow(df_results, global_stage_choice):
    stage_idx = get_stage_index(global_stage_choice)
    with st.expander("♻️ 依實方重新分配出土分區（修正換算係數調整前的分配）", expanded=False):
        if stage_idx is None or df_results is None or df_results.empty:
            st.info("請先在左側選擇「第1～5挖」其中一個作業階段（開挖前土方沒有分區門檻，不適用）。")
            return
        st.caption(
            f"目前換算係數 {LOOSE_SOIL_FACTOR}（鬆方÷係數＝實方）。系統會把【{global_stage_choice}】範圍內已指定分區的車次"
            "（不含作廢、不含「開挖前土方」）依日期時間順序，從挖掘順序的第一區開始逐區填到該區在本階段的目標，再換下一區。"
            "會直接改寫雲端 dispatch_logs 的「出土分區」欄，所以按確認前請先下載備份。")
        if not st.checkbox("開始計算並顯示預覽", value=False, key="reflow_open",
                           help="預覽需要逐筆模擬分配，會拖慢每次點選的速度，所以預設不計算；要用時再勾選。"):
            return
        raw = load_sheet_data("dispatch_logs")
        if raw is None or raw.empty or "出土分區" not in raw.columns:
            st.info("目前沒有派車紀錄。")
            return
        default_order = default_zone_order(df_results, stage_idx)
        order = st.multiselect("分區挖掘順序（依選取先後排序，可增減或重選順序；預設由西往東一欄一欄推）",
                               options=default_order, default=default_order, key="reflow_order")
        inc = st.checkbox("連同目前「未指定」的車次一起分配", value=True, key="reflow_inc")
        if not order:
            st.warning("請至少選一個分區。")
            return
        new_s, summ, left = reflow_assignments(raw, df_results, stage_idx, order, inc)
        if new_s.empty:
            st.info("沒有符合條件的車次可以重新分配。")
            return
        changed = int((raw.loc[new_s.index, "出土分區"].fillna("").astype(str).str.strip() != new_s).sum())
        front = summ[summ["調整後(實方)"] > 0]
        frontier = front.iloc[-1]["分區"] if not front.empty else "—"
        st.write(f"共 **{len(new_s)}** 筆車次參與分配，其中 **{changed}** 筆分區會改變；推進到最前面的分區：**{frontier}**。"
                 + (f"　⚠️ {left} 筆因所有分區目標都已填滿，會變成「未指定」。" if left else ""))
        show = summ[(summ["調整前(實方)"] > 0) | (summ["調整後(實方)"] > 0)]
        st.dataframe(show, use_container_width=True, hide_index=True)
        st.download_button("⬇️ 先下載目前 dispatch_logs 備份（CSV）", raw.to_csv(index=False).encode("utf-8-sig"),
                           file_name="dispatch_logs_backup.csv", mime="text/csv", key="reflow_backup")
        if st.button("✅ 確認重新分配並寫入雲端", type="primary", key="reflow_apply"):
            out = raw.copy()
            out.loc[new_s.index, "出土分區"] = new_s.values
            if save_sheet_data("dispatch_logs", out):
                sync_stage_daily_log(global_stage_choice, df_results)
                st.success(f"已重新分配 {len(new_s)} 筆（{changed} 筆有改變）。")
                st.rerun()
