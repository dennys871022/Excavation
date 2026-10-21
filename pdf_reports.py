"""PDF 與後台地圖輸出：每日出土報表、週趨勢報表、聯單簽收單。"""

import numpy as np
import os
import pandas as pd
import tempfile
from datetime import datetime
from stage_calc import get_stage_index
from zones import filter_zones_for_stage, local_stage_index


def generate_backend_map(df_results, zone_grouped, stage_choice=None):
    """
    產出後端地圖圖片（給PDF報表嵌入用）。
    stage_choice 若給定且為第1~4挖，會跟畫面上的單階段地圖一致：
      - 第3、4挖自動排除滯洪池分區（滯洪池只有到第2挖）
      - 套用三色進度（尚未開始/進行中/已完成），只反映該階段自己的進度
    stage_choice 為 None 或「開挖前土方」時，維持原本總體4階累計完成度上色。
    """
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches
    from matplotlib.font_manager import FontProperties
    import gc

    fig, ax = plt.subplots(figsize=(10, 6))
    font_path = "font.ttf"
    my_font = FontProperties(fname=font_path) if os.path.exists(font_path) else None

    vol_dict = {}
    if not zone_grouped.empty:
        vol_dict = zone_grouped.set_index('出土分區')['累計實挖方量'].to_dict()
    stage_dict = df_results.set_index('分區代號')['各階累計方量'].to_dict() if not df_results.empty else {}

    stage_idx = get_stage_index(stage_choice) if stage_choice else None

    plot_df = df_results
    if stage_idx is not None:
        plot_df = filter_zones_for_stage(df_results, stage_idx)

    for idx, row in plot_df.iterrows():
        grid_id = row['分區代號']
        current_vol = vol_dict.get(grid_id, 0)
        thresholds = stage_dict.get(grid_id, [])
        fill_color = '#F0F0F0'

        if stage_idx is not None and thresholds:
            _local_idx = local_stage_index(row, stage_idx)
            if _local_idx >= 0 and _local_idx < len(thresholds):
                band_start = thresholds[_local_idx - 1] if _local_idx > 0 else 0
                band_end = thresholds[_local_idx]
                band_size = band_end - band_start
                pct = min(max((current_vol - band_start) / band_size * 100, 0), 100) if band_size > 0 else 100.0
                if pct >= 98:
                    fill_color = '#2ECC71'
                elif pct > 0:
                    fill_color = '#E67E22'
                else:
                    fill_color = '#F0F0F0'
        elif stage_idx is None:
            if pd.notnull(current_vol) and current_vol > 0 and len(thresholds) > 0:
                if current_vol >= thresholds[-1] * 0.98:
                    fill_color = '#2ECC71'
                else:
                    colors = ['#F1C40F', '#E67E22', '#3498DB', '#9B59B6']
                    for s_idx, t_vol in enumerate(thresholds):
                        if current_vol < t_vol * 0.98:
                            fill_color = colors[s_idx] if s_idx < len(colors) else colors[-1]
                            break

        xy = [[row['x_min'], row['y_min']], [row['x_max'], row['y_min']],
              [row['x_max'], row['y_max']], [row['x_min'], row['y_max']]]
        poly = patches.Polygon(xy, closed=True, facecolor=fill_color, edgecolor='gray', alpha=0.8)
        ax.add_patch(poly)

        if my_font:
            ax.text(row['x_center'], row['y_center'], grid_id, ha='center', va='center', fontsize=8, color='black', fontproperties=my_font)
        else:
            ax.text(row['x_center'], row['y_center'], grid_id, ha='center', va='center', fontsize=8, color='black')

    ax.autoscale_view()
    ax.set_aspect('equal')
    plt.axis('off')

    tmp_img = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    plt.savefig(tmp_img.name, bbox_inches='tight', dpi=150)
    fig.clf()
    plt.close('all')
    gc.collect()
    return tmp_img.name


def generate_daily_report_pdf(report_text, breakdown_text, display_df, map_img_path,
                               period_label, start_date, end_date, stage_overview=None,
                               daily_control_df=None, map_legend_items=None, stage_label=None,
                               fda_daily_df=None):
    """
    產出每日/區間出土統計 PDF 報表：
      - 標題（置中）
      - 左欄：階段總覽（stage_overview，對應圖1紫色表格；若為 None 則退回顯示 report_text 本日回報文字）
      - 右欄：聯單分類出土明細 (breakdown_text)
      - 圖例（真正的顏色色塊，map_legend_items = [(label, hex_color), ...]） + 各區開挖階段狀態地圖
      - 各分區挖掘進度總表（放在地圖後面，避免分區數增加時把地圖往下擠）
      - 每日出土管控明細 (daily_control_df, 放在PDF最後面；標題用 stage_label)
      - FDA土方交換每日明細 (fda_daily_df, 獨立一張表，接在每日出土管控明細後面)
    回傳暫存 PDF 檔案路徑。
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_path = "font.ttf"
    font_name = "CustomFont"
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont(font_name, font_path))
        except Exception:
            font_name = "Helvetica"
    else:
        font_name = "Helvetica"

    tmp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    c = canvas.Canvas(tmp_pdf.name, pagesize=A4)
    width, height = A4
    margin = 18 * mm
    y = height - margin

    def new_page():
        nonlocal y
        c.showPage()
        c.setFont(font_name, 10)
        y = height - margin

    # 標題（置中）
    c.setFont(font_name, 16)
    c.drawCentredString(width / 2, y, f"CDC土方開挖{period_label}回報")
    y -= 12 * mm

    # 左欄：階段總覽（若有帶入 stage_overview，取代原本的本日回報文字）；右欄：聯單分類出土
    col2_x_top = margin + (width - 2 * margin) * 0.58
    y_left_start = y
    y_right_start = y

    y_left = y_left_start
    if stage_overview is not None:
        so = stage_overview
        c.setFont(font_name, 12)
        c.drawString(margin, y_left, f"【{so['stage_choice']}】階段總覽")
        y_left -= 7 * mm
        c.setFont(font_name, 9.5)
        overview_lines = [
            f"今天日期：{so['today']}",
            f"目前作業工期：{so['current_work_days']} 天",
            f"預計開始時間：{so['est_start']}　實際開始時間：{so['actual_start_date']}",
            f"預計施作工期：{so['est_days']} 天　推估剩餘天數：{so['remain_days']} 天",
            f"預計完成日期：{so['est_end']}",
            f"推估完成日期：{so['est_completion_date']}　（差異：{so['diff_days']} 天）",
            f"預估土方量(鬆方)：{so['est_vol']:,.1f} m³",
            f"本日出土量：{so['today_vol']:,.1f} m³",
            f"預估每日出土量：{so['est_daily_vol']:,.1f} m³",
            f"累積出土數量：{so['cum_vol']:,.1f} m³",
            f"平均出土功率：{so['avg_vol_per_day']:,.1f} m³/天",
            f"剩餘土方量：{so['remain_vol']:,.1f} m³",
            f"完成百分比：{so['percent_done']}%",
        ]
        for line in overview_lines:
            if y_left < margin + 10 * mm:
                break
            c.drawString(margin, y_left, line)
            y_left -= 5.5 * mm
    else:
        # 未帶入 stage_overview 時，退回顯示原本的本日回報文字（向下相容）
        c.setFont(font_name, 10)
        for line in str(report_text).split("\n"):
            if y_left < margin + 10 * mm:
                break
            c.drawString(margin, y_left, line)
            y_left -= 5.5 * mm

    c.setFont(font_name, 11)
    y_right = y_right_start
    c.drawString(col2_x_top, y_right, "聯單分類出土：")
    y_right -= 6 * mm
    c.setFont(font_name, 10)
    for line in str(breakdown_text).split("\n"):
        if line.strip() == "":
            continue
        c.drawString(col2_x_top, y_right, line)
        y_right -= 5.5 * mm

    y = min(y_left, y_right) - 4 * mm
    if y < margin + 20 * mm:
        new_page()

    def draw_legend(legend_items, start_y):
        """畫真正的顏色色塊圖例（不是emoji文字，避免字型不支援顯示成方框），超出頁寬會自動換行。"""
        nonlocal y
        box_size = 3.5 * mm
        gap = 5 * mm
        line_h = 6 * mm
        x_pos = margin
        cur_y = start_y
        c.setFont(font_name, 9)
        for label, hex_color in legend_items:
            text_w = c.stringWidth(label, font_name, 9)
            item_w = box_size + 1.5 * mm + text_w
            if x_pos + item_w > width - margin and x_pos > margin:
                x_pos = margin
                cur_y -= line_h
            c.setFillColor(HexColor(hex_color))
            c.rect(x_pos, cur_y - box_size + 1, box_size, box_size, fill=1, stroke=0)
            c.setFillColor(HexColor("#000000"))
            c.drawString(x_pos + box_size + 1.5 * mm, cur_y - box_size + 1, label)
            x_pos += item_w + gap
        y = cur_y - box_size - 4 * mm

    # 各區開挖階段狀態地圖（放在分區表格前面，避免分區數日後增加時把地圖一直往下擠）
    if map_img_path and os.path.exists(map_img_path):
        y -= 4 * mm
        if y < margin + 95 * mm:
            new_page()
        c.setFont(font_name, 11)
        c.drawString(margin, y, "各區開挖階段狀態圖：")
        y -= 6 * mm
        legend_items = map_legend_items if map_legend_items else [
            ("尚未開始", "#F0F0F0"), ("進行中", "#E67E22"), ("已完成", "#2ECC71"),
        ]
        draw_legend(legend_items, y)
        img_w = width - 2 * margin
        img_h = img_w * 0.6
        if y - img_h < margin:
            new_page()
        c.drawImage(map_img_path, margin, y - img_h, width=img_w, height=img_h, preserveAspectRatio=True, mask='auto')
        y -= (img_h + 6 * mm)

    # 各分區進度表格
    if y < margin + 30 * mm:
        new_page()
    c.setFont(font_name, 11)
    c.drawString(margin, y, "各分區挖掘進度總表：")
    y -= 7 * mm

    if display_df is not None and not display_df.empty:
        col_names = list(display_df.columns)
        col_width = (width - 2 * margin) / max(len(col_names), 1)
        c.setFont(font_name, 9)
        for i, col in enumerate(col_names):
            c.drawString(margin + i * col_width, y, str(col))
        y -= 2 * mm
        c.line(margin, y, width - margin, y)
        y -= 5 * mm

        for _, row in display_df.iterrows():
            if y < margin + 12 * mm:
                new_page()
                c.setFont(font_name, 9)
                for i, col in enumerate(col_names):
                    c.drawString(margin + i * col_width, y, str(col))
                y -= 2 * mm
                c.line(margin, y, width - margin, y)
                y -= 5 * mm
            for i, col in enumerate(col_names):
                c.drawString(margin + i * col_width, y, str(row[col]))
            y -= 5.5 * mm
    else:
        c.setFont(font_name, 10)
        c.drawString(margin, y, "（尚無分區資料）")
        y -= 6 * mm

    # 每日出土管控明細（放在PDF最後面，根據目前選擇的階段從雲端資料顯示）
    if daily_control_df is not None and not daily_control_df.empty:
        new_page()
        c.setFont(font_name, 13)
        stage_label_for_table = stage_label or (stage_overview['stage_choice'] if stage_overview else "")
        c.drawString(margin, y, f"【{stage_label_for_table}】每日出土管控明細")
        y -= 9 * mm

        dc_cols = list(daily_control_df.columns)
        total_w = width - 2 * margin

        def _dc_cell_text(val):
            if isinstance(val, float) and pd.isna(val):
                return "－"  # 台北港排除日：差異/累積差異跳過不計算，顯示為破折號而不是難看的 nan
            if isinstance(val, float):
                return f"{val:,.1f}"
            return str(val)

        # 依「表頭文字」跟「每一列實際資料」量測每一欄需要多寬，而不是用寫死的百分比硬切，
        # 這樣像「內控預計車次」這種字比較多的表頭才不會被壓縮到跟旁邊的欄位重疊
        dc_font_size = 8
        col_padding = 2.5 * mm
        col_widths = []
        for col in dc_cols:
            needed = c.stringWidth(str(col), font_name, dc_font_size)
            for _, row in daily_control_df.iterrows():
                cell_w = c.stringWidth(_dc_cell_text(row[col]), font_name, dc_font_size)
                if cell_w > needed:
                    needed = cell_w
            col_widths.append(needed + col_padding)

        total_needed = sum(col_widths)
        if total_needed > total_w:
            # 量出來的總寬度超過版面，等比例縮小；縮太多的話字體也跟著縮小一點，避免文字被切斷
            scale = total_w / total_needed
            col_widths = [w * scale for w in col_widths]
            if scale < 0.8:
                dc_font_size = 7
        else:
            # 版面還有多餘空間，平均分給每一欄，表格不會擠成一團
            extra = (total_w - total_needed) / len(dc_cols)
            col_widths = [w + extra for w in col_widths]

        def draw_dc_header():
            nonlocal y
            c.setFont(font_name, dc_font_size)
            x_pos = margin
            for col, w in zip(dc_cols, col_widths):
                c.drawString(x_pos, y, str(col))
                x_pos += w
            y -= 2 * mm
            c.line(margin, y, width - margin, y)
            y -= 4.5 * mm

        red_cols = {'差異', '累積差異'}
        draw_dc_header()
        c.setFont(font_name, dc_font_size)
        for _, row in daily_control_df.iterrows():
            if y < margin + 10 * mm:
                new_page()
                c.setFont(font_name, 13)
                c.drawString(margin, y, f"【{stage_label_for_table}】每日出土管控明細（續）")
                y -= 9 * mm
                draw_dc_header()
            x_pos = margin
            for col, w in zip(dc_cols, col_widths):
                val = row[col]
                text_val = _dc_cell_text(val)
                try:
                    is_negative = col in red_cols and not pd.isna(val) and float(val) < 0
                except (ValueError, TypeError):
                    is_negative = False
                if is_negative:
                    c.setFillColor(HexColor("#e74c3c"))
                c.drawString(x_pos, y, text_val)
                if is_negative:
                    c.setFillColor(HexColor("#000000"))
                x_pos += w
            y -= 5 * mm

    # FDA土方交換每日明細（獨立一張表，車次與方量都不併進上面台北港的實際車次）
    if fda_daily_df is not None and not fda_daily_df.empty:
        if y < margin + 45 * mm:
            new_page()
        else:
            y -= 10 * mm
        c.setFont(font_name, 13)
        c.drawString(margin, y, "【FDA土方交換】每日明細（車次與方量獨立統計）")
        y -= 9 * mm

        fda_cols = list(fda_daily_df.columns)
        fda_total_w = width - 2 * margin
        fda_font_size = 8
        fda_text_cols = {'分區分配', '備註'}

        def _fda_cell_text(val):
            if isinstance(val, (int, np.integer)):
                return f"{int(val):,}"
            if isinstance(val, float):
                return "" if pd.isna(val) else f"{val:,.1f}"
            return "" if val is None else str(val)

        def _fda_fit_text(text, max_w):
            if c.stringWidth(text, font_name, fda_font_size) <= max_w:
                return text
            while text and c.stringWidth(text + "...", font_name, fda_font_size) > max_w:
                text = text[:-1]
            return text + "..."

        fda_pad = 2.5 * mm
        fda_widths = {}
        for col in fda_cols:
            if col in fda_text_cols:
                continue
            needed = c.stringWidth(str(col), font_name, fda_font_size)
            for _, r in fda_daily_df.iterrows():
                needed = max(needed, c.stringWidth(_fda_cell_text(r[col]), font_name, fda_font_size))
            fda_widths[col] = needed + fda_pad
        num_total = sum(fda_widths.values())
        min_text_w = 50 * mm
        remain = fda_total_w - num_total
        if remain < min_text_w and num_total > 0:
            shrink = max((fda_total_w - min_text_w) / num_total, 0.3)
            fda_widths = {k: v * shrink for k, v in fda_widths.items()}
            remain = fda_total_w - sum(fda_widths.values())
        fda_widths['分區分配'] = remain * 0.65
        fda_widths['備註'] = remain * 0.35

        def draw_fda_header():
            nonlocal y
            c.setFont(font_name, fda_font_size)
            x_pos = margin
            for col in fda_cols:
                c.drawString(x_pos, y, str(col))
                x_pos += fda_widths[col]
            y -= 2 * mm
            c.line(margin, y, width - margin, y)
            y -= 4.5 * mm

        draw_fda_header()
        c.setFont(font_name, fda_font_size)
        for _, r in fda_daily_df.iterrows():
            if y < margin + 10 * mm:
                new_page()
                c.setFont(font_name, 13)
                c.drawString(margin, y, "【FDA土方交換】每日明細（續）")
                y -= 9 * mm
                draw_fda_header()
                c.setFont(font_name, fda_font_size)
            x_pos = margin
            for col in fda_cols:
                text_val = _fda_cell_text(r[col])
                if col in fda_text_cols:
                    text_val = _fda_fit_text(text_val, fda_widths[col] - 2 * mm)
                c.drawString(x_pos, y, text_val)
                x_pos += fda_widths[col]
            y -= 5 * mm

    c.save()
    return tmp_pdf.name


def generate_weekly_trend_pdf(week_display_df, stage_choice, start_str, end_str):
    """
    產出週會用的簡易PDF：標題 + 日期區間 + 「日期/實際車次/備註」三欄表格。
    回傳暫存 PDF 檔案路徑。
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_path = "font.ttf"
    font_name = "CustomFont"
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont(font_name, font_path))
        except Exception:
            font_name = "Helvetica"
    else:
        font_name = "Helvetica"

    tmp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    c = canvas.Canvas(tmp_pdf.name, pagesize=A4)
    width, height = A4
    margin = 18 * mm
    y = height - margin

    def new_page():
        nonlocal y
        c.showPage()
        c.setFont(font_name, 10)
        y = height - margin

    c.setFont(font_name, 16)
    c.drawCentredString(width / 2, y, f"【{stage_choice}】週會車次資料")
    y -= 9 * mm
    c.setFont(font_name, 10)
    c.drawCentredString(width / 2, y, f"統計區間：{start_str} ～ {end_str}")
    y -= 12 * mm

    dc_cols = ['日期', '實際車次', '備註']
    total_w = width - 2 * margin
    col_widths = [total_w * 0.25, total_w * 0.2, total_w * 0.55]

    def draw_header():
        nonlocal y
        c.setFont(font_name, 10)
        x_pos = margin
        for col, w in zip(dc_cols, col_widths):
            c.drawString(x_pos, y, col)
            x_pos += w
        y -= 2 * mm
        c.line(margin, y, width - margin, y)
        y -= 6 * mm

    draw_header()
    c.setFont(font_name, 10)
    for _, row in week_display_df.iterrows():
        if y < margin + 10 * mm:
            new_page()
            draw_header()
        x_pos = margin
        for col, w in zip(dc_cols, col_widths):
            val = row[col]
            text_val = f"{val:,.0f}" if isinstance(val, float) and not pd.isna(val) else ("" if pd.isna(val) else str(val))
            c.drawString(x_pos, y, text_val)
            x_pos += w
        y -= 6 * mm

    c.save()
    return tmp_pdf.name


def generate_delivery_pdf(df, scope):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import base64
    import io

    font_path = "font.ttf"
    font_name = "CustomFont"
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont(font_name, font_path))
        except Exception:
            font_name = "Helvetica"
    else:
        font_name = "Helvetica"

    tmp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    c = canvas.Canvas(tmp_pdf.name, pagesize=A4)
    width, height = A4
    margin = 18 * mm

    c.setFont(font_name, 16)
    c.drawString(margin, height - margin, f"CDC土方聯單交付簽收報表 - {scope}")
    c.setFont(font_name, 9)
    c.drawString(margin, height - margin - 7 * mm, f"匯出時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    y = height - margin - 20 * mm
    row_block_h = 55 * mm

    for _, row in df.iterrows():
        if y < row_block_h:
            c.showPage()
            c.setFont(font_name, 9)
            y = height - margin

        c.setFont(font_name, 10)
        text_lines = [
            f"交付日期時間: {row.get('交付日期','')} {row.get('交付時間','')}",
            f"廠商名稱: {row.get('廠商名稱','')}",
            f"聯單類型: {row.get('聯單類型','')}",
            f"起始序號: {row.get('起始序號','')}",
            f"發放張數: {row.get('發放張數','')}",
            f"簽收人姓名: {row.get('簽收人姓名','')}",
        ]
        ty = y
        for line in text_lines:
            c.drawString(margin, ty, line)
            ty -= 5 * mm

        sign_data = row.get('簽名資料', '')
        if isinstance(sign_data, str) and sign_data:
            try:
                img_bytes = base64.b64decode(sign_data)
                tmp_sign = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                with open(tmp_sign.name, "wb") as sf:
                    sf.write(img_bytes)
                c.drawImage(tmp_sign.name, 120 * mm, y - 30 * mm, width=60 * mm, height=35 * mm,
                            preserveAspectRatio=True, mask='auto')
            except Exception:
                c.drawString(120 * mm, y - 15 * mm, "(簽名載入失敗)")
        else:
            c.drawString(120 * mm, y - 15 * mm, "(無簽名資料)")

        c.line(margin, y - 38 * mm, width - margin, y - 38 * mm)
        y -= row_block_h

    c.save()
    return tmp_pdf.name
