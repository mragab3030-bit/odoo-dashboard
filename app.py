from flask import Flask, jsonify, send_file, render_template
from flask_cors import CORS
import xmlrpc.client
from datetime import date, datetime
from io import BytesIO
import os
import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

app = Flask(__name__)
CORS(app)

ODOO_URL = "https://success-creator.odoo.com"
ODOO_DB  = "sdaaaecho-success-creator-main-25773216"
ODOO_USER = "admin"
ODOO_API_KEY = "edcc5250f3a70524b7de7d4860d7eedd5b21739f"

FONT_PATH = os.path.join(os.path.dirname(__file__), 'fonts', 'Amiri-1.000', 'Amiri-Regular.ttf')

def ar(text):
    try:
        reshaped = arabic_reshaper.reshape(str(text))
        return get_display(reshaped)
    except:
        return str(text)

def get_uid():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_API_KEY, {})
    print("UID:", uid)
    return uid

def odoo_call(uid, model, method, args=[], kwargs={}):
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
    return models.execute_kw(ODOO_DB, uid, ODOO_API_KEY, model, method, args, kwargs)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/summary")
def get_summary():
    uid = get_uid()
    if not uid:
        return jsonify({"error": "Auth failed"}), 401
    today = date.today().isoformat()
    all_inv = odoo_call(uid, "account.move", "search_read",
        [[["move_type","=","out_invoice"],["state","=","posted"]]],
        {"fields":["amount_total","amount_residual","payment_state","invoice_date_due"]}
    )
    total        = sum(i["amount_total"] for i in all_inv)
    paid         = sum(i["amount_total"] for i in all_inv if i["payment_state"]=="paid")
    outstanding  = sum(i["amount_residual"] for i in all_inv)
    overdue_list = [i for i in all_inv if i["amount_residual"]>0 and i["invoice_date_due"] and i["invoice_date_due"]<today]
    overdue      = sum(i["amount_residual"] for i in overdue_list)
    return jsonify({
        "total_invoiced": round(total,2),
        "total_invoices": len(all_inv),
        "total_paid":     round(paid,2),
        "outstanding":    round(outstanding,2),
        "overdue":        round(overdue,2),
        "overdue_count":  len(overdue_list)
    })

@app.route("/api/invoices")
def get_invoices():
    uid = get_uid()
    if not uid:
        return jsonify({"error": "Auth failed"}), 401
    invoices = odoo_call(uid, "account.move", "search_read",
        [[["move_type","=","out_invoice"],["state","=","posted"]]],
        {"fields":["name","partner_id","invoice_date","invoice_date_due",
                   "amount_total","amount_residual","payment_state","invoice_user_id"],
         "limit":1000, "order":"invoice_date desc"}
    )
    return jsonify(invoices or [])

@app.route("/api/aging")
def get_aging():
    uid = get_uid()
    if not uid:
        return jsonify({"error": "Auth failed"}), 401
    today = date.today().isoformat()
    overdue = odoo_call(uid, "account.move", "search_read",
        [[["move_type","=","out_invoice"],["state","=","posted"],
          ["amount_residual",">",0],["invoice_date_due","<",today]]],
        {"fields":["amount_residual","invoice_date_due"]}
    )
    b1=b2=b3=c1=c2=c3=0
    for i in overdue:
        due  = datetime.strptime(i["invoice_date_due"],"%Y-%m-%d").date()
        days = (date.today()-due).days
        if days<=30:   b1+=i["amount_residual"]; c1+=1
        elif days<=60: b2+=i["amount_residual"]; c2+=1
        else:          b3+=i["amount_residual"]; c3+=1
    return jsonify([
        {"label":"1-30 days",  "amount":round(b1,2),"count":c1},
        {"label":"31-60 days", "amount":round(b2,2),"count":c2},
        {"label":"61+ days",   "amount":round(b3,2),"count":c3},
    ])

@app.route("/api/export/pdf")
def export_pdf():
    uid = get_uid()
    if not uid:
        return jsonify({"error": "Auth failed"}), 401

    today = date.today().isoformat()

    # Register Arabic font
    try:
        pdfmetrics.registerFont(TTFont('Amiri', FONT_PATH))
        arabic_font = 'Amiri'
    except:
        arabic_font = 'Helvetica'

    invoices = odoo_call(uid, "account.move", "search_read",
        [[["move_type","=","out_invoice"],["state","=","posted"]]],
        {"fields":["name","partner_id","invoice_date","invoice_date_due",
                   "amount_total","amount_residual","payment_state"],
         "limit":1000, "order":"invoice_date desc"}
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            rightMargin=1*cm, leftMargin=1*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)

    elements = []

    title_style = ParagraphStyle('title', fontSize=16, fontName='Helvetica-Bold',
                                  spaceAfter=4, textColor=colors.HexColor('#1a1d23'))
    sub_style = ParagraphStyle('sub', fontSize=9, fontName='Helvetica',
                                spaceAfter=16, textColor=colors.HexColor('#6b7280'))

    elements.append(Paragraph("Invoice Dashboard", title_style))
    elements.append(Paragraph("Generated on " + today + " | Live data from Odoo", sub_style))

    header = ['Invoice #', 'Customer', 'Issue Date', 'Due Date', 'Amount (SAR)', 'Outstanding (SAR)', 'Status']
    data = [header]

    for i in invoices:
        customer_raw = i['partner_id'][1] if i['partner_id'] else '-'
        customer = ar(customer_raw)

        payment_state = i.get('payment_state','')
        if payment_state == 'paid':
            status = 'Paid'
        elif payment_state == 'partial':
            status = 'Partial'
        elif i.get('invoice_date_due') and i['invoice_date_due'] < today and i['amount_residual'] > 0:
            status = 'Overdue'
        else:
            status = 'Outstanding'

        customer_para = Paragraph(customer, ParagraphStyle('ar', fontName=arabic_font, fontSize=7.5, alignment=TA_RIGHT))

        data.append([
            i['name'],
            customer_para,
            i.get('invoice_date','') or '-',
            i.get('invoice_date_due','') or '-',
            '{:,.0f}'.format(i['amount_total']),
            '{:,.0f}'.format(i['amount_residual']) if i['amount_residual'] > 0 else '-',
            status
        ])

    col_widths = [3.5*cm, 7.5*cm, 2.8*cm, 2.8*cm, 3.2*cm, 3.8*cm, 2.8*cm]

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,0),  colors.HexColor('#1e40af')),
        ('TEXTCOLOR',     (0,0), (-1,0),  colors.white),
        ('FONTNAME',      (0,0), (-1,0),  'Helvetica-Bold'),
        ('FONTSIZE',      (0,0), (-1,0),  8),
        ('ALIGN',         (0,0), (-1,0),  'CENTER'),
        ('FONTNAME',      (0,1), (-1,-1), 'Helvetica'),
        ('FONTSIZE',      (0,1), (-1,-1), 7.5),
        ('ROWBACKGROUNDS',(0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')]),
        ('GRID',          (0,0), (-1,-1), 0.4, colors.HexColor('#e8eaed')),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING',    (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('ALIGN',         (4,1), (5,-1),  'RIGHT'),
        ('ALIGN',         (0,1), (0,-1),  'LEFT'),
        ('ALIGN',         (2,1), (3,-1),  'CENTER'),
    ]))

    # Color status column
    for row_idx, row in enumerate(data[1:], start=1):
        status = row[6]
        if status == 'Overdue':
            table.setStyle(TableStyle([
                ('TEXTCOLOR', (6,row_idx), (6,row_idx), colors.HexColor('#dc2626')),
                ('FONTNAME',  (6,row_idx), (6,row_idx), 'Helvetica-Bold')]))
        elif status == 'Paid':
            table.setStyle(TableStyle([
                ('TEXTCOLOR', (6,row_idx), (6,row_idx), colors.HexColor('#16a34a')),
                ('FONTNAME',  (6,row_idx), (6,row_idx), 'Helvetica-Bold')]))
        elif status == 'Partial':
            table.setStyle(TableStyle([
                ('TEXTCOLOR', (6,row_idx), (6,row_idx), colors.HexColor('#d97706')),
                ('FONTNAME',  (6,row_idx), (6,row_idx), 'Helvetica-Bold')]))

    elements.append(table)
    doc.build(elements)
    buffer.seek(0)

    return send_file(buffer, mimetype='application/pdf',
                     as_attachment=True,
                     download_name='invoices_' + today + '.pdf')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
app.run(host="0.0.0.0", port=port, debug=False)
