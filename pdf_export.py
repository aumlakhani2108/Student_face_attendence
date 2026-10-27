import io
import datetime

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False


def generate_class_attendance_pdf(class_info, records, summary=None):
    """
    Generates a PDF bytes buffer containing the class attendance record.
    """
    if not HAS_REPORTLAB:
        raise RuntimeError("ReportLab is not installed. Run `pip install reportlab` to enable PDF export.")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0071e3"),
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#6e6e73"),
        spaceAfter=14,
    )
    meta_style = ParagraphStyle(
        "MetaText",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#1d1d1f"),
    )

    story = []

    # Title & Header
    story.append(Paragraph("BioBytes · Digital Facial Recognition Attendance System", title_style))
    class_name = class_info.get("name", "Class Session") if class_info else "All Classes"
    story.append(Paragraph(f"Official Attendance Report: <b>{class_name}</b>", subtitle_style))

    # Metadata Block
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M local")
    date_val = class_info.get("class_date", "N/A") if class_info else "All dates"
    timing_val = f"{class_info.get('class_time', 'N/A')} - {class_info.get('end_time', 'N/A')}" if class_info else "All Sessions"
    faculty_val = class_info.get("faculty", "N/A") if class_info else "All Faculty"
    status_val = class_info.get("status", "Completed").capitalize() if class_info else "Summary"

    meta_data = [
        [
            Paragraph(f"<b>Date:</b> {date_val}", meta_style),
            Paragraph(f"<b>Timing:</b> {timing_val}", meta_style),
        ],
        [
            Paragraph(f"<b>Faculty:</b> {faculty_val}", meta_style),
            Paragraph(f"<b>Generated At:</b> {now_str}", meta_style),
        ],
        [
            Paragraph(f"<b>Total Present:</b> {len(records)}", meta_style),
            Paragraph(f"<b>Status:</b> {status_val}", meta_style),
        ],
    ]
    meta_table = Table(meta_data, colWidths=[270, 270])
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f5f7")),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e5ea")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 16))

    # Attendance Records Table
    table_headers = ["#", "Student ID", "Name", "Roll", "Class / Section", "Marked Time (UTC)"]
    table_rows = [table_headers]

    for idx, r in enumerate(records, start=1):
        table_rows.append(
            [
                str(idx),
                str(r.get("student_id", "")),
                str(r.get("name", "")),
                str(r.get("roll", "") or "—"),
                f"{r.get('class', '')} {r.get('section', '')}".strip() or "—",
                str(r.get("timestamp", ""))[:19],
            ]
        )

    if len(table_rows) == 1:
        table_rows.append(["—", "—", "No attendance records found.", "—", "—", "—"])

    col_widths = [30, 60, 150, 60, 110, 130]
    rec_table = Table(table_rows, colWidths=col_widths, repeatRows=1)
    rec_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d1d1f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
                ("TOPPADDING", (0, 0), (-1, 0), 7),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 8.5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbfbfd")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e5ea")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(rec_table)
    story.append(Spacer(1, 20))

    footer_style = ParagraphStyle(
        "FooterNote",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#86868b"),
        alignment=1,
    )
    story.append(Paragraph("This is a system-generated document verified by facial recognition. No physical signature required.", footer_style))

    doc.build(story)
    buffer.seek(0)
    return buffer


def generate_student_attendance_pdf(student_info, attendance_records, stats):
    """
    Generates a personalized PDF report for a student.
    """
    if not HAS_REPORTLAB:
        raise RuntimeError("ReportLab is not installed. Run `pip install reportlab` to enable PDF export.")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0071e3"),
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#6e6e73"),
        spaceAfter=14,
    )
    meta_style = ParagraphStyle(
        "MetaText",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#1d1d1f"),
    )

    story = []
    story.append(Paragraph("Student Attendance Transcript", title_style))
    story.append(Paragraph(f"Official Record for <b>{student_info.get('name', 'Student')}</b>", subtitle_style))

    # Student Summary Box
    pct = stats.get("percentage", 0.0)
    pct_color = "#2e7d32" if pct >= 75 else ("#e65100" if pct >= 60 else "#c62828")

    meta_data = [
        [
            Paragraph(f"<b>Student Name:</b> {student_info.get('name')}", meta_style),
            Paragraph(f"<b>Roll No:</b> {student_info.get('roll') or '—'}", meta_style),
        ],
        [
            Paragraph(f"<b>Class / Section:</b> {student_info.get('class') or '—'} / {student_info.get('section') or '—'}", meta_style),
            Paragraph(f"<b>Reg No:</b> {student_info.get('reg_no') or '—'}", meta_style),
        ],
        [
            Paragraph(f"<b>Total Sessions Attended:</b> {stats.get('attended_sessions', 0)} / {stats.get('total_sessions', 0)}", meta_style),
            Paragraph(f"<b>Attendance Rate:</b> <font color='{pct_color}'><b>{pct:.1f}%</b></font>", meta_style),
        ],
    ]
    meta_table = Table(meta_data, colWidths=[270, 270])
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f5f7")),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e5ea")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 16))

    # Attendance Records
    headers = ["#", "Class / Subject", "Class Date", "Faculty", "Marked Time (UTC)", "Status"]
    table_rows = [headers]

    for idx, r in enumerate(attendance_records, start=1):
        table_rows.append(
            [
                str(idx),
                str(r.get("class_name", "General Session")),
                str(r.get("class_date", "—")),
                str(r.get("faculty", "—")),
                str(r.get("timestamp", ""))[:19],
                "PRESENT",
            ]
        )

    if len(table_rows) == 1:
        table_rows.append(["—", "No attended classes recorded yet.", "—", "—", "—", "—"])

    col_widths = [30, 160, 80, 100, 110, 60]
    rec_table = Table(table_rows, colWidths=col_widths, repeatRows=1)
    rec_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0071e3")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
                ("TOPPADDING", (0, 0), (-1, 0), 7),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 8.5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbfbfd")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e5ea")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(rec_table)
    story.append(Spacer(1, 20))

    footer_style = ParagraphStyle(
        "FooterNote",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#86868b"),
        alignment=1,
    )
    story.append(Paragraph("Official student attendance transcript generated by BioBytes Attendance System.", footer_style))

    doc.build(story)
    buffer.seek(0)
    return buffer
