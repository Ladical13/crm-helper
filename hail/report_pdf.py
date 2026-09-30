"""Printable, self-contained property hail evidence; no live tiles or mutable data."""
from pathlib import Path
from fpdf import FPDF
from datetime import date, datetime, timedelta


def date_ranges(dates):
    ranges = []
    start = end = None
    for value in sorted(set(dates)):
        day = date.fromisoformat(value)
        if end is not None and day == end + timedelta(days=1):
            end = day
            continue
        if start is not None:
            ranges.append(str(start) if start == end else f'{start} through {end}')
        start = end = day
    if start is not None:
        ranges.append(str(start) if start == end else f'{start} through {end}')
    return ', '.join(ranges)


def build(report):
    fonts = Path(__file__).resolve().parents[1] / 'estimator' / 'static' / 'fonts'
    class PDF(FPDF):
        def header(self):
            self.set_xy(15, 12)
            self.set_font('Inter', 'B', 10)
            self.set_text_color(20, 55, 70)
            self.cell(0, 6, 'PROJECT ONE ROOFING  |  COLORADO', new_x='LMARGIN', new_y='NEXT')
            self.set_draw_color(35, 166, 145)
            self.line(15, 23, 195, 23)
            self.set_y(28)

        def footer(self):
            self.set_y(-14)
            self.set_font('Inter', '', 7)
            self.set_text_color(95, 105, 115)
            self.cell(0, 5, f"Report {report['id'][:8]}  |  NOAA radar estimates  |  Page {self.page_no()}", align='C')

    pdf = PDF()
    pdf.set_creation_date(datetime.fromisoformat(report['created_at']))
    pdf.set_margins(15, 28, 15)
    pdf.set_auto_page_break(True, 20)
    pdf.add_font('Inter', '', str(fonts / 'Inter-Regular.ttf'))
    pdf.add_font('Inter', 'B', str(fonts / 'Inter-SemiBold.ttf'))
    pdf.add_page()

    def text(value, size=9, bold=False, height=4.8):
        pdf.set_font('Inter', 'B' if bold else '', size)
        pdf.set_text_color(30, 45, 60)
        pdf.multi_cell(0, height, str(value), new_x='LMARGIN', new_y='NEXT')

    text('Property Hail History', 23, True, 12)
    text(report['label'], 11, True, 6)
    text(f"Confirmed point: {report['lat']:.5f}, {report['lng']:.5f}")
    text(f"Period: {report['since']} through {report['until']}  |  Prepared: {report['created_at'][:10]}")
    cov = report['coverage']
    text(f"{len(report['storms'])} qualifying radar windows  |  Largest estimate: {report['max_size']:.2f} inches", 11, True, 7)
    text(f"Valid radar data at this point: {cov['days_verified']} of {cov['days_requested']} days. "
         f"{cov['days_missing']} missing or unchecked. Stored threshold: {cov['threshold_in']:g} inches.")
    text(report['disclaimer'], 8)
    pdf.ln(3)
    text('Radar grid at the confirmed property', 12, True, 7)
    m = report['map']
    text(('Largest recorded window: ' + m['date']) if m['date'] else 'No qualifying radar window in the available data.', 8)
    x, y, width, height = 15, pdf.get_y() + 2, 180, 65
    pdf.set_fill_color(237, 243, 246)
    pdf.rect(x, y, width, height, style='F')
    south, west, north, east = m['bounds']
    def xy(lat, lng):
        return x + (lng - west) / (east - west) * width, y + (north - lat) / (north - south) * height
    for s, w, n, e, size in m['cells']:
        if e < west or w > east or n < south or s > north:
            continue
        left, top = xy(min(n, north), max(w, west))
        right, bottom = xy(max(s, south), min(e, east))
        pdf.set_fill_color(*((210, 72, 65) if size >= 2 else (232, 139, 52) if size >= 1.5 else (222, 183, 63)))
        pdf.rect(left, top, right - left, bottom - top, style='F')
    px, py = xy(report['lat'], report['lng'])
    pdf.set_draw_color(15, 37, 61)
    pdf.set_line_width(.7)
    pdf.line(px - 3, py, px + 3, py)
    pdf.line(px, py - 3, px, py + 3)
    pdf.circle(px, py, 2)
    pdf.set_xy(x + 3, y + 2)
    text('N ↑', 8, True)
    pdf.set_xy(15, y + height + 3)
    text('Yellow: 1-1.49 in  |  Orange: 1.5-1.99 in  |  Red: 2+ in  |  Crosshair: confirmed point', 7)
    text('Grid locator, not a street basemap. Unshaded areas are not proof of no hail. Approx. 1 km radar cells.', 7)
    if m['truncated']:
        text('Map is truncated; additional cells exist outside this displayed sample.', 8, True)
    pdf.ln(3)
    text('Recorded radar windows', 12, True, 7)
    if not report['storms']:
        text('No archived estimates meet the stored threshold for this point. Review the coverage limits above.')
    for event in report['storms']:
        if pdf.will_page_break(24):
            pdf.add_page()
        text(f"{event['date']}  |  {event['size']:.2f} inches (estimated)", 10, True, 6)
        text(event['local_window'], 8)
        if event['note']:
            text(event['note'], 8)
        pdf.set_font('Inter', '', 8)
        pdf.set_text_color(15, 110, 120)
        pdf.cell(0, 5, 'NOAA source file', link=event['source_url'], new_x='LMARGIN', new_y='NEXT')
    pdf.ln(3)
    text('Source and coverage', 12, True, 7)
    text('NOAA Multi-Radar/Multi-Sensor (MRMS), MESH_Max_1440min. Public archive: noaa-mrms-pds.s3.amazonaws.com.', 8)
    text('This dated report is a saved snapshot. It does not certify roof condition, damage, or an exact date of loss.', 8)
    missing = report['missing_archive_dates']
    if missing:
        text('Dates without a decoded archive file', 10, True, 6)
        text(date_ranges(missing), 7, height=4)
    return bytes(pdf.output())
