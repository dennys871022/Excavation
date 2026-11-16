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
        return pd.Series(dtype=object), pd.DataFrame(), 0, {}
    elig["_dt"] = pd.to_datetime(elig["日期"].astype(str).str[:10] + " " + elig.get("時間", "").fillna("").astype(str), errors="coerce")
    elig["_vol"] = pd.to_numeric(elig.get("載運方量(m³)", 12.0), errors="coerce").fillna(12.0) / LOOSE_SOIL_FACTOR
    elig = elig.sort_values(["_dt"], kind="stable")
    trips_col = pd.to_numeric(elig["車次數"], errors="coerce") if "車次數" in elig.columns else pd.Series(1, index=elig.index)
    elig["_trips"] = trips_col.fillna(1).clip(lower=1).round().astype(int)
    remaining = dict(caps)
    new = {}
    splits = {}            # 多車次的集計列（例如FDA）跨到兩個以上分區時：idx -> [(分區, 車次), ...]
    after_vol = {}
    p = 0
    left = 0
    for idx, r in elig.iterrows():
        n = int(r["_trips"])
        per = r["_vol"] / n
        alloc = []          # 依時間順序逐車填：集計列裡的每一車都跟同一天的車一樣分到當時正在挖的分區
        for _ in range(n):
            while p < len(order) and remaining[order[p]] <= 0:
                p += 1
            z = order[p] if p < len(order) else "未指定"
            if z == "未指定":
                left += 1
            else:
                remaining[z] -= per        # 最後一車允許些微超挖收尾，跟批量分配規則一致
                after_vol[z] = after_vol.get(z, 0.0) + per
            if alloc and alloc[-1][0] == z:
                alloc[-1][1] += 1
            else:
                alloc.append([z, 1])
        new[idx] = alloc[0][0]
        if len(alloc) > 1:
            splits[idx] = [(z, c) for z, c in alloc]
    new_s = pd.Series(new, dtype=object)
    before = elig["_vol"].groupby(zcol[elig.index]).sum()
    rows = []
    for z in order:
        rows.append({"分區": z, "本階段目標(實方)": round(caps[z]), "調整前(實方)": round(float(before.get(z, 0))),
                     "調整後(實方)": round(float(after_vol.get(z, 0)))})
    return new_s, pd.DataFrame(rows), left, splits


def apply_reflow(raw, new_s, splits):
    """把分配結果寫回：一般列直接改分區；跨分區的集計列拆成多列，車次與方量依車數比例分配。"""
    out = raw.copy()
    out.loc[new_s.index, "出土分區"] = new_s.values
    # 作廢的車不佔任何分區：一律改回「未指定」，表上一眼就看得出它沒被分配
    if "作廢" in out.columns:
        vmask = out["作廢"].apply(_is_voided) & (out["出土分區"].fillna("").astype(str).str.strip() != PRE_ZONE)
        out["出土分區"] = out["出土分區"].astype(object)
        out.loc[vmask, "出土分區"] = "未指定"
    if not splits:
        return out
    extra = []
    out = out.astype({c: object for c in ("出土分區", "車次數", "載運方量(m³)", "車頭車號") if c in out.columns})
    for idx, parts in splits.items():
        row = out.loc[idx].astype(object)
        n = sum(c for _, c in parts)
        vol = pd.to_numeric(row.get("載運方量(m³)", 0), errors="coerce")
        vol = 0.0 if pd.isna(vol) else float(vol)
        for z, c in parts:
            nr = row.copy()
            nr["出土分區"] = z
            nr["車次數"] = c
            nr["載運方量(m³)"] = round(vol * c / n, 1)
            car = str(row.get("車頭車號", ""))
            if car.startswith("FDA集計") and z != "未指定":
                nr["車頭車號"] = f"{car.split('-')[0]}-{car.split('-')[1]}-{z}" if car.count("-") >= 1 else car
            extra.append(nr)
    out = out.drop(index=list(splits))
    out = pd.concat([out, pd.DataFrame(extra)], ignore_index=True)
    key = pd.to_datetime(out["日期"].astype(str).str[:10] + " " + out.get("時間", "").fillna("").astype(str), errors="coerce")
    return out.assign(_k=key).sort_values("_k", kind="stable").drop(columns="_k").reset_index(drop=True)


def render_reflow(df_results, global_stage_choice):
    stage_idx = get_stage_index(global_stage_choice)
    with st.expander("♻️ 依實方重新分配出土分區（修正換算係數調整前的分配）", expanded=False):
        if stage_idx is None or df_results is None or df_results.empty:
            st.info("請先在左側選擇「第1～5挖」其中一個作業階段（開挖前土方沒有分區門檻，不適用）。")
            return
        st.caption(
            f"目前換算係數 {LOOSE_SOIL_FACTOR}（鬆方÷係數＝實方）。系統會把【{global_stage_choice}】範圍內已指定分區的車次"
            "（不含作廢、不含「開挖前土方」；FDA等多車次集計列會逐車分配，跨到兩區時自動拆成兩列）依日期時間順序，從挖掘順序的第一區開始逐區填到該區在本階段的目標，再換下一區。"
            "會直接改寫雲端 dispatch_logs 的「出土分區」欄，所以按確認前請先下載備份。")
        if not st.checkbox("開始計算並顯示預覽", value=False, key="reflow_open",
                           help="預覽需要逐筆模擬分配，會拖慢每次點選的速度，所以預設不計算；要用時再勾選。"):
            return
        if st.button("🔄 重新讀取雲端最新資料（剛在試算表改過作廢時先按這個）", key="reflow_refresh"):
            st.cache_data.clear()
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
        new_s, summ, left, splits = reflow_assignments(raw, df_results, stage_idx, order, inc)
        if new_s.empty:
            st.info("沒有符合條件的車次可以重新分配。")
            return
        changed = int((raw.loc[new_s.index, "出土分區"].fillna("").astype(str).str.strip() != new_s).sum())
        front = summ[summ["調整後(實方)"] > 0]
        frontier = front.iloc[-1]["分區"] if not front.empty else "—"
        st.write(f"共 **{len(new_s)}** 筆車次參與分配，其中 **{changed}** 筆分區會改變；推進到最前面的分區：**{frontier}**。"
                 + (f"　{len(splits)} 筆集計列會拆分到多個分區。" if splits else "")
                 + (f"　⚠️ {left} 車因所有分區目標都已填滿，會變成「未指定」。" if left else ""))
        show = summ[(summ["調整前(實方)"] > 0) | (summ["調整後(實方)"] > 0)]
        st.dataframe(show, use_container_width=True, hide_index=True)
        st.download_button("⬇️ 先下載目前 dispatch_logs 備份（CSV）", raw.to_csv(index=False).encode("utf-8-sig"),
                           file_name="dispatch_logs_backup.csv", mime="text/csv", key="reflow_backup")
        if st.button("✅ 確認重新分配並寫入雲端", type="primary", key="reflow_apply"):
            # 雲端讀取有5分鐘快取：如果剛剛直接在試算表（或另一個分頁）改過作廢、補登，預覽用的可能是舊資料，
            # 直接寫回會把剛改的作廢蓋掉。所以寫入前一定重新讀一次雲端最新資料，用最新資料重算分配再寫回。
            st.cache_data.clear()
            fresh = load_sheet_data("dispatch_logs")
            if fresh is None or fresh.empty or "出土分區" not in fresh.columns:
                st.error("寫入前重新讀取雲端失敗，這次不寫入，請重新整理後再試。")
                return
            new_s, summ, left, splits = reflow_assignments(fresh, df_results, stage_idx, order, inc)
            out = apply_reflow(fresh, new_s, splits)
            if save_sheet_data("dispatch_logs", out):
                sync_stage_daily_log(global_stage_choice, df_results)
                st.success(f"已重新分配 {len(new_s)} 筆（{changed} 筆有改變）。")
                st.rerun()
