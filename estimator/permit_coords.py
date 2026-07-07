# Loveland Permit & Affidavit — field coordinate map.
#
# The city's PDF (static/permit_templates/loveland_permit_affidavit.pdf) has no
# fillable AcroForm fields — it's a flat 2-page scan. It is also a *pre-signed*
# template: page 1 already carries Bethany Durnbaugh's print name/signature,
# page 2 already carries Luke Durnbaugh's print name/signature plus the
# contractor company name and business address, and the fixed company info
# (license #9034, phones, co.permits@ email, Contact: Luke Durnbaugh) is
# printed into the form itself. We only draw the job-specific blanks (and
# today's date next to each signature) on top — never anything already on it.
# The city-only boxes on page 1 (Permit #, Received by, Date, Approvals,
# Total Fees Due) are likewise never touched.
#
# Coordinates are in PDF points with origin at the BOTTOM-LEFT (pypdf
# convention; the overlay generator converts to fpdf2's top-left origin).
# Calibrated by rendering each page at 4x with pypdfium2 under a labeled
# 10pt grid. If a generated PDF shows text drifting off a line or crowding
# a label, nudge the numbers here — nothing else needs to change.
#
# Field entry keys:
#   page  0 or 1
#   x, y  text baseline position (or "X" mark position for checkboxes)
#   size  font size in points
#   mark  True = checkbox; draw an "X" when the field's value is truthy
#   max_width / line_height / max_lines  multi-line wrapped text block

PAGE_SIZE = (612, 792)  # US Letter, confirmed via pypdf mediabox

FIELDS = {
    # ---- Page 1: Residential Fast Track Permit Application ----
    "job_site_address": {"page": 0, "x": 185, "y": 568, "size": 10},
    "valuation":        {"page": 0, "x": 150, "y": 505, "size": 10},
    "owner_name":       {"page": 0, "x": 155, "y": 487, "size": 10},
    "owner_phone":      {"page": 0, "x": 300, "y": 487, "size": 10},
    "owner_address":    {"page": 0, "x": 125, "y": 463, "size": 10},
    "owner_city":       {"page": 0, "x": 120, "y": 447, "size": 10},
    "owner_state":      {"page": 0, "x": 295, "y": 447, "size": 10},
    "owner_zip":        {"page": 0, "x": 425, "y": 447, "size": 10},
    "num_squares":      {"page": 0, "x": 368, "y": 392, "size": 10},
    "work_description": {"page": 0, "x": 90,  "y": 352, "size": 8,
                          "line_height": 11, "max_width": 505, "max_lines": 5},
    "date_p1":          {"page": 0, "x": 295, "y": 150, "size": 10},

    # ---- Page 2: Roofing Affidavit ----
    "affidavit_job_address":   {"page": 1, "x": 372, "y": 562, "size": 10},
    "roof_covering_type":      {"page": 1, "x": 215, "y": 514, "size": 9},
    "roof_covering_class":     {"page": 1, "x": 205, "y": 495, "size": 10},
    "replacing_sheathing_yes": {"page": 1, "x": 184, "y": 481, "size": 11, "mark": True},
    "replacing_sheathing_no":  {"page": 1, "x": 229, "y": 480, "size": 11, "mark": True},
    "metal_noncombustible":    {"page": 1, "x": 205, "y": 461, "size": 11, "mark": True},
    "astm_asphalt":            {"page": 1, "x": 49,  "y": 407, "size": 11, "mark": True},
    "astm_other":              {"page": 1, "x": 49,  "y": 393, "size": 11, "mark": True},
    "astm_other_text":         {"page": 1, "x": 320, "y": 392, "size": 8},
    "fastener_staples":        {"page": 1, "x": 235, "y": 348, "size": 9},
    "fastener_nails":          {"page": 1, "x": 220, "y": 333, "size": 9},
    "fastener_other":          {"page": 1, "x": 158, "y": 318, "size": 9},
    "underlayment_self_adhering": {"page": 1, "x": 47, "y": 273, "size": 11, "mark": True},
    "underlayment_ice_barrier":   {"page": 1, "x": 47, "y": 257, "size": 11, "mark": True},
    "date_p2":                 {"page": 1, "x": 435, "y": 73,  "size": 10},
}
