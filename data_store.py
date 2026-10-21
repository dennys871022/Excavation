"""Google Sheets 讀寫、快取、全域設定與每日備註等資料存取函式。"""

import numpy as np
import pandas as pd
import streamlit as st
from streamlit_gsheets import GSheetsConnection
from config import GLOBAL_PREFS_KEY, SHEET_URL


def get_conn():
    """取得 Google Sheets 連線（Streamlit 內部已快取，重複呼叫不會重連）。"""
    return st.connection("gsheets", type=GSheetsConnection)


def is_demo_mode():
    """訪客（唯讀沙盒）模式旗標，由 sidebar.render_auth() 於每次執行時設定。"""
    return st.session_state.get("demo_mode", True)


@st.cache_data(ttl=300)
def fetch_gsheet_data(sheet_name):
    return get_conn().read(spreadsheet=SHEET_URL, worksheet=sheet_name, ttl=0)


def load_sheet_data(sheet_name):
    try:
        df = fetch_gsheet_data(sheet_name)
        df = df.dropna(how='all')
        if not df.empty:
            st.session_state[f"cache_{sheet_name}"] = df.copy()
        return df
    except Exception:
        if f"cache_{sheet_name}" in st.session_state:
            return st.session_state[f"cache_{sheet_name}"]
        return pd.DataFrame()


def save_sheet_data(sheet_name, df):
    if is_demo_mode():
        st.session_state[f"cache_{sheet_name}"] = df.copy()
        st.toast(f"👀 訪客模式：已模擬儲存【{sheet_name}】至網頁暫存，雲端資料庫未變動。")
        return True

    try:
        get_conn().update(spreadsheet=SHEET_URL, worksheet=sheet_name, data=df)
        st.cache_data.clear()
        st.session_state[f"cache_{sheet_name}"] = df.copy()
        return True
    except Exception as e:
        st.error(f"寫入分頁 `{sheet_name}` 失敗：{e}")
        return False


def _is_voided(val):
    """
    穩健判斷「作廢」欄位是否為 True。因為資料存到 Google 試算表再讀回來，
    布林值常常會變成字串 'TRUE'/'FALSE' 或數字 1/0，不能直接用 `if val:` 判斷。
    """
    if pd.isna(val):
        return False
    if isinstance(val, bool):
        return val
    s = str(val).strip().upper()
    return s in ('TRUE', '1', 'YES', 'V', '是')


def filter_active_dispatch_logs(df_logs):
    """
    回傳排除「已作廢」紀錄後的派車紀錄，給所有車次/方量統計計算使用
    （例如爆胎超時被台北港拒收、原車返回工地的那種聯單，作廢後不應計入車次與出土量）。
    原始資料（含作廢列）完全不受影響，仍完整保留在雲端可供查核，只是統計時被排除。
    """
    if df_logs is None or df_logs.empty:
        return df_logs
    if '作廢' not in df_logs.columns:
        return df_logs
    return df_logs[~df_logs['作廢'].apply(_is_voided)].copy()


def _clean_serial(v):
    """
    把聯單序號統一整理成乾淨字串，用來判斷「這筆紀錄有沒有官方聯單序號」：
    空白、NaN、None、'nan' 一律視為沒有序號；數字被雲端讀成 123.0 的，去掉尾巴的 .0。
    """
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    s = str(v).strip()
    if s.upper() in ("NAN", "NONE", "NAT"):
        return ""
    if s.endswith(".0") and s[:-2].replace("-", "").isdigit():
        s = s[:-2]
    return s


def load_global_prefs():
    """
    讀取存在 stage_settings 分頁裡一筆特殊的「全域設定」列，用來記住側邊欄的
    「目前作業階段」「現地GL高程增減」跟各區GL高程輸入，這樣重新整理瀏覽器時才不會跳回預設值。
    借用既有的 stage_settings 分頁存放，不需要額外去雲端新增分頁。

    這裡改成「自動讀取所有『全域_』開頭的欄位」，回傳的 dict 直接用欄位名稱當 key。
    這樣以後不管再加幾個新的全域設定欄位，只要用 save_global_pref() 存的時候欄位名稱
    是「全域_」開頭，這裡就會自動讀得到，不用每次新增欄位都要回來手動加一行——
    上次就是漏了改這裡，才會發生「存進雲端了、但重整後讀不回來」的問題。
    """
    prefs = {}
    df = load_sheet_data("stage_settings")
    if df.empty or "階段名稱" not in df.columns:
        return prefs
    row = df[df["階段名稱"] == GLOBAL_PREFS_KEY]
    if row.empty:
        return prefs
    r = row.iloc[0]
    for col in df.columns:
        if col.startswith("全域_") and pd.notna(r.get(col)):
            prefs[col] = r.get(col)
    return prefs


def save_global_pref(field, value):
    """把單一全域設定值寫回 stage_settings 分頁的「全域設定」列，其餘欄位/其他階段的設定完全不動。"""
    df = load_sheet_data("stage_settings")
    if df.empty or "階段名稱" not in df.columns:
        df = pd.DataFrame(columns=["階段名稱"])
    if field not in df.columns:
        # 用 object dtype 建立新欄位（可以同時放文字或數字），不要用 np.nan 直接指派，
        # 那樣 pandas 會把欄位型態推斷成 float64，之後這裡要存文字（例如GL高程公式）就會直接報錯
        df[field] = pd.Series([None] * len(df), dtype="object", index=df.index)
    else:
        # 既有欄位也強制轉成 object，避免它原本被推斷成 float64（例如全都是數字的欄位）導致後面寫入字串失敗
        df[field] = df[field].astype("object")
    mask = df["階段名稱"] == GLOBAL_PREFS_KEY
    if mask.any():
        df.loc[mask, field] = value
    else:
        new_row = {c: np.nan for c in df.columns}
        new_row["階段名稱"] = GLOBAL_PREFS_KEY
        new_row[field] = value
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    return save_sheet_data("stage_settings", df)


def _upsert_daily_notes_field(df_daily_notes, stage_choice, date_str, field, value):
    """
    只更新 stage_daily_notes 裡「某一天、某一個欄位」的值，其餘欄位（包含其他已存的手動設定）
    完全不動。給「0出土日期管理」跟其他只想改單一欄位的地方用，避免整列/整段覆寫。
    """
    df_daily_notes = df_daily_notes.copy()
    mask = (df_daily_notes['階段名稱'] == stage_choice) & (df_daily_notes['日期'].astype(str) == str(date_str))
    if mask.any():
        df_daily_notes.loc[mask, field] = value
    else:
        new_row = {c: np.nan for c in df_daily_notes.columns}
        new_row['階段名稱'] = stage_choice
        new_row['日期'] = date_str
        new_row[field] = value
        df_daily_notes = pd.concat([df_daily_notes, pd.DataFrame([new_row])], ignore_index=True)
    return df_daily_notes


def _is_blank_value(v):
    """判斷一個值是否算「空」：NaN、None，或去除空白後的空字串、或 False。"""
    if pd.isna(v):
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    if isinstance(v, bool) and v is False:
        return True
    return False


def _row_has_meaningful_override(row):
    """
    判斷一列是否還有「值得保留」的手動設定：
    內控預計車次有填真的數字、備註有寫字、或計入工期被設成True，這三個才算數。
    差異/當日運棄量/累計運棄量/剩餘土方量/實際車次這些「事實」欄位不列入判斷，
    因為它們本來就只是同步時的快照，不影響任何計算（計算永遠會重新從派車紀錄算），
    舊快照留著沒有意義，可以放心清掉。
    """
    if not _is_blank_value(row.get('內控預計車次')):
        return True
    if not _is_blank_value(row.get('備註')):
        return True
    if row.get('計入工期') is True:
        return True
    return False


def _clear_daily_notes_field_or_drop(df_daily_notes, stage_choice, date_str, field):
    """
    把某一天某個欄位清空（設回 NaN，代表恢復系統預設行為）。
    清空後如果這一列已經沒有任何值得保留的手動設定，就把整列刪掉，避免留下空列造成雲端資料肥大、雜亂。
    """
    df_daily_notes = df_daily_notes.copy()
    mask = (df_daily_notes['階段名稱'] == stage_choice) & (df_daily_notes['日期'].astype(str) == str(date_str))
    if not mask.any():
        return df_daily_notes
    df_daily_notes.loc[mask, field] = np.nan
    row = df_daily_notes.loc[mask].iloc[0]
    if not _row_has_meaningful_override(row):
        df_daily_notes = df_daily_notes[~mask]
    return df_daily_notes


def _save_daily_notes_sorted(df_daily_notes):
    """存檔前依「階段名稱、日期」排序，避免雲端表格日期跳來跳去、難以閱讀核對。"""
    if not df_daily_notes.empty and '日期' in df_daily_notes.columns:
        sort_cols = ['階段名稱', '日期'] if '階段名稱' in df_daily_notes.columns else ['日期']
        df_daily_notes = df_daily_notes.sort_values(sort_cols).reset_index(drop=True)
    return save_sheet_data("stage_daily_notes", df_daily_notes)


OFFICIAL_FIELD_COLS = ["出場補登", "出場補登原因", "進場補登", "進場補登原因", "司機姓名", "土質"]


def apply_official_fields(df_logs, official_df):
    """
    把官方CSV裡「真正的」補登資訊與司機、土質，依「聯單序號」對應寫進 dispatch_logs 的新欄位
    （出場補登 0/1、出場補登原因、進場補登 0/1、進場補登原因、司機姓名、土質）。
    只會填補欄位，不新增、不刪除、不改動車次/時間/序號/分區，所以任何時候重複執行都是安全的。
    備註裡舊的「官方聯單補登」只是系統自動補上資料的註記，不是官方的補登，統計一律以這裡的「出場補登」欄位為準。
    回傳 (更新後的df_logs, 對應到的筆數, 官方CSV裡找不到對應紀錄的序號清單)。
    """
    if df_logs is None or df_logs.empty or official_df is None or official_df.empty or "聯單序號" not in official_df.columns:
        return df_logs, 0, []
    off = official_df.copy()
    off["_s"] = off["聯單序號"].apply(_clean_serial)
    off = off[off["_s"] != ""].drop_duplicates("_s", keep="last")

    def _col(*names):
        for n in names:
            if n in off.columns:
                return off[n]
        return pd.Series([None] * len(off), index=off.index)

    def _flag(series):
        return pd.to_numeric(series, errors="coerce").fillna(0).astype(int)

    def _text(series):
        return series.fillna("").astype(str).str.strip().replace({"nan": "", "NaN": ""})

    rec = pd.DataFrame({
        "出場補登": _flag(_col("是否為出場補登 (0=正常, 1=補登)")).values,
        "出場補登原因": _text(_col("出場補登原因")).values,
        "進場補登": _flag(_col("是否為進場補登 (0=正常, 1=補登)")).values,
        "進場補登原因": _text(_col("進場補登原因")).values,
        "司機姓名": _text(_col("司機姓名")).values,
        "土質": _text(_col("土質")).values,
    }, index=off["_s"].values)   # 用 .values 避免跟 off 的索引對齊成全空

    out = df_logs.copy()
    cur = out["聯單序號"].apply(_clean_serial) if "聯單序號" in out.columns else pd.Series([""] * len(out), index=out.index)
    mask = cur.isin(rec.index)
    for c in OFFICIAL_FIELD_COLS:
        if c not in out.columns:
            out[c] = ""
        out[c] = out[c].astype(object)
        out.loc[mask, c] = cur[mask].map(rec[c]).values
    unmatched = sorted(set(rec.index) - set(cur[cur != ""]))
    return out, int(mask.sum()), unmatched
