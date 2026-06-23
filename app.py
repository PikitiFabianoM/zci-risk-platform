import os

import io

import sqlite3

from flask import Flask, render_template, request, jsonify, send_file

from reportlab.lib.pagesizes import letter

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from reportlab.lib import colors



BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))



VALID_OFFICER_TOKEN = "officer_alpha"

VALID_SECRET_PASSCODE = "credit2026"



def get_db():

    conn = sqlite3.connect(os.path.join(BASE_DIR, 'zci_registry.db'))

    conn.row_factory = sqlite3.Row

    return conn



def init_db():

    conn = get_db()

    conn.execute('''CREATE TABLE IF NOT EXISTS assessments (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        borrower_name TEXT, loan_amount REAL, monthly_income REAL,

        monthly_installment REAL, employment TEXT, sector TEXT,

        has_collateral TEXT, momo_proxy TEXT, credit_history TEXT,

        dti REAL, score INTEGER, decision TEXT, rec TEXT, officer TEXT,

        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')

    conn.commit()

    conn.close()



init_db()



@app.after_request

def cors(r):

    r.headers["Access-Control-Allow-Origin"] = "*"

    r.headers["Access-Control-Allow-Headers"] = "Content-Type"

    r.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"

    return r



@app.route('/')

def home():

    return render_template('index.html')



@app.route('/api/login', methods=['POST', 'OPTIONS'])

def api_login():

    if request.method == 'OPTIONS':

        return '', 200

    data = request.get_json() or {}

    if data.get('username', '').strip() == VALID_OFFICER_TOKEN and data.get('password', '').strip() == VALID_SECRET_PASSCODE:

        return jsonify({"status": "authenticated", "node": VALID_OFFICER_TOKEN, "user": VALID_OFFICER_TOKEN}), 200

    return jsonify({"status": "denied", "message": "Invalid credentials"}), 401



@app.route('/api/assessments', methods=['POST', 'OPTIONS', 'GET'])

def api_assessments():

    if request.method == 'OPTIONS':

        return '', 200



    if request.method == 'GET':

        search = request.args.get('search', '').strip()

        conn = get_db()

        if search:

            rows = conn.execute("SELECT * FROM assessments WHERE borrower_name LIKE ? ORDER BY id DESC", (f"%{search}%",)).fetchall()

        else:

            rows = conn.execute("SELECT * FROM assessments ORDER BY id DESC").fetchall()

        conn.close()

        return jsonify([dict(r) for r in rows]), 200



    data = request.get_json() or {}

    try:

        borrower_name = data.get('borrower_name', 'Unknown Entity').strip()

        loan_amount = float(str(data.get('loan_amount', 0) or 0).replace(',', ''))

        monthly_income = float(str(data.get('monthly_income', 1) or 1).replace(',', ''))

        if monthly_income <= 0:

            monthly_income = 1.0

        monthly_installment = float(str(data.get('monthly_installment', 0) or 0).replace(',', ''))

        employment = data.get('employment', '')

        sector = data.get('sector', '')

        has_collateral = data.get('has_collateral', 'no')

        momo_proxy = data.get('momo_proxy', 'no')

        credit_history = data.get('credit_history', 'none')

        officer = data.get('officer', VALID_OFFICER_TOKEN)



        dti = round((monthly_installment / monthly_income) * 100, 1)



        pts = 0

        if dti <= 15: pts += 30

        elif dti <= 30: pts += 20

        elif dti <= 45: pts += 10

        elif dti <= 60: pts += 5



        if employment == 'formal_employed': pts += 25

        elif employment == 'self_employed': pts += 18

        else: pts += 8



        if sector == 'trade': pts += 25

        elif sector == 'services': pts += 20

        else: pts += 12



        if has_collateral == 'yes': pts += 20

        if momo_proxy == 'yes': pts += 10



        if credit_history == '3_paid': pts += 15

        elif credit_history == '1-2_paid': pts += 10

        elif credit_history == 'none': pts += 5

        elif credit_history == 'defaulted': pts -= 10



        score = max(0, min(100, round((pts / 125) * 100)))



        if score >= 70:

            decision = "APPROVE"

            rec = "Proceed with standard facility parameters. Recommended rate: 18-22% p.a."

        elif score >= 50:

            decision = "CONDITIONAL"

            rec = "Conditional approval. Require guarantor or secondary asset charge."

        elif score >= 35:

            decision = "REFER"

            rec = "Refer to Credit Risk Committee for deep cash-flow audit."

        else:

            decision = "DECLINE"

            rec = "Risk thresholds breached. Application does not meet lending criteria."



        conn = get_db()

        cur = conn.execute('''INSERT INTO assessments

            (borrower_name, loan_amount, monthly_income, monthly_installment,

             employment, sector, has_collateral, momo_proxy, credit_history,

             dti, score, decision, rec, officer)

            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',

            (borrower_name, loan_amount, monthly_income, monthly_installment,

             employment, sector, has_collateral, momo_proxy, credit_history,

             dti, score, decision, rec, officer))

        record_id = cur.lastrowid

        conn.commit()

        conn.close()



        return jsonify({

            "id": record_id,

            "score": score,

            "dti": dti,

            "decision": decision,

            "rec": rec,

            "node": officer

        }), 201



    except Exception as e:

        return jsonify({"error": str(e)}), 400



@app.route('/api/download_report/<int:record_id>', methods=['GET'])

def download_report(record_id):

    conn = get_db()

    row = conn.execute("SELECT * FROM assessments WHERE id=?", (record_id,)).fetchone()

    conn.close()

    if not row:

        return "Record not found", 404

    row = dict(row)



    buffer = io.BytesIO()

    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)

    styles = getSampleStyleSheet()



    dec_color = '#10B981' if row['decision'] == 'APPROVE' else ('#F59E0B' if row['decision'] == 'CONDITIONAL' else '#EF4444')



    title_s = ParagraphStyle('T', parent=styles['Heading1'], fontSize=20, textColor=colors.HexColor('#0F172A'), alignment=1)

    sub_s = ParagraphStyle('S', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#64748B'), alignment=1)

    dec_s = ParagraphStyle('D', parent=styles['Normal'], fontSize=18, fontName='Helvetica-Bold', textColor=colors.HexColor(dec_color), alignment=1)

    body_s = ParagraphStyle('B', parent=styles['Normal'], fontSize=10, textColor=colors.HexColor('#334155'))

    bold_s = ParagraphStyle('Bo', parent=body_s, fontName='Helvetica-Bold')



    story = [

        Paragraph("ZCI ENTERPRISE RISK PLATFORM", title_s),

        Paragraph("Credit Risk Assessment Memorandum", sub_s),

        HRFlowable(width="100%", thickness=1, color=colors.HexColor('#CBD5E1'), spaceAfter=15),

        Paragraph(row['decision'], dec_s),

        Spacer(1, 6),

        Paragraph(row['rec'], ParagraphStyle('R', parent=body_s, alignment=1, fontName='Helvetica-Oblique')),

        Spacer(1, 10),

        Paragraph(str(row['score']), ParagraphStyle('Sc', parent=styles['Normal'], fontSize=48, fontName='Helvetica-Bold', textColor=colors.HexColor('#1E293B'), alignment=1)),

        Paragraph("SCORE INDEX / 100", ParagraphStyle('SL', parent=body_s, alignment=1, fontSize=8, textColor=colors.HexColor('#64748B'))),

        Spacer(1, 20),

        HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#E2E8F0'), spaceAfter=10),

    ]



    details = [

        ["BORROWER ENTITY", str(row['borrower_name'])],

        ["PRINCIPAL ADVANCE", f"ZMW {row['loan_amount']:,.2f}"],

        ["MONTHLY INCOME", f"ZMW {row['monthly_income']:,.2f}"],

        ["DEBT-TO-INCOME RATIO", f"{row['dti']}%"],

        ["EMPLOYMENT", str(row['employment'])],

        ["SECTOR", str(row['sector'])],

        ["COLLATERAL", str(row['has_collateral'])],

        ["OFFICER SIGN-OFF", str(row['officer'])],

        ["TIMESTAMP", str(row['timestamp'])],

    ]

    tdata = [[Paragraph(r[0], bold_s), Paragraph(r[1], body_s)] for r in details]

    t = Table(tdata, colWidths=[200, 310])

    t.setStyle(TableStyle([

        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')),

        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),

        ('TOPPADDING', (0,0), (-1,-1), 7),

        ('BOTTOMPADDING', (0,0), (-1,-1), 7),

        ('LEFTPADDING', (0,0), (-1,-1), 10),

    ]))

    story.append(t)



    doc.build(story)

    buffer.seek(0)

    name = "".join(c for c in row['borrower_name'] if c.isalnum() or c in " _-").strip()

    return send_file(buffer, as_attachment=True, download_name=f"ZCI_Report_{name.replace(' ','_')}.pdf", mimetype='application/pdf')



if __name__ == '__main__':

    app.run(debug=True) 

