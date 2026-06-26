import io
import sqlite3
from flask import Flask, request, jsonify, render_template, send_file
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.platypus.flowables import HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

app = Flask(__name__)
DATABASE = 'zci_registry.db'
VALID_OFFICER_TOKEN = 'officer_alpha'

# Sourced baseline CHIRPS data for Zambian agricultural operational hubs
DISTRICT_RAINFALL_LOOKUP = {
    "Kabwe": {"annual_mm": 920, "zone": "Region IIa"},
    "Chibombo": {"annual_mm": 850, "zone": "Region IIa"},
    "Mkushi": {"annual_mm": 950, "zone": "Region IIa"},
    "Petauke": {"annual_mm": 820, "zone": "Region IIa"},
    "Choma": {"annual_mm": 780, "zone": "Region IIb"},
    "Monze": {"annual_mm": 740, "zone": "Region IIb"},
    "Mazabuka": {"annual_mm": 760, "zone": "Region IIb"},
    "Mpika": {"annual_mm": 1050, "zone": "Region III"},
    "Kasama": {"annual_mm": 1200, "zone": "Region III"},
    "Solwezi": {"annual_mm": 1300, "zone": "Region III"},
    "Siavonga": {"annual_mm": 650, "zone": "Region I"},
    "Shang'ombo": {"annual_mm": 600, "zone": "Region I"}
}

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        borrower_name TEXT,
        loan_amount REAL,
        monthly_income REAL,
        monthly_installment REAL,
        employment TEXT,
        sector TEXT,
        has_collateral TEXT,
        momo_proxy TEXT,
        credit_history TEXT,
        district TEXT,
        dti REAL,
        score INTEGER,
        decision TEXT,
        rec TEXT,
        officer TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/debug-db')
def debug_db():
    token = request.args.get('token')
    if token != VALID_OFFICER_TOKEN:
        return jsonify({"error": "Unauthorized access restriction active"}), 401
        
    conn = get_db()
    rows = conn.execute("SELECT * FROM assessments").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

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
        district = data.get('district', 'Kabwe')
        officer = data.get('officer', VALID_OFFICER_TOKEN)

        dti = round((monthly_installment / monthly_income) * 100, 1)

        # 🌧️ CRITICAL FIX: Extract and calculate rainfall metrics BEFORE running final score algorithm
        weather_info = DISTRICT_RAINFALL_LOOKUP.get(district, {"annual_mm": 920, "zone": "Region IIa"})
        annual_mm = weather_info['annual_mm']
        
        if annual_mm >= 900: rainfall_pts = 100
        elif annual_mm >= 700: rainfall_pts = 80
        elif annual_mm >= 500: rainfall_pts = 60
        elif annual_mm >= 300: rainfall_pts = 40
        else: rainfall_pts = 20

        # Run Core Credit Point Allocations (Base Sub-total max points = 140)
        pts = 0
        if dti <= 15: pts += 30
        elif dti <= 30: pts += 20
        elif dti <= 45: pts += 10
        elif dti <= 60: pts += 5

        annual_income = monthly_income * 12
        loan_to_income_ratio = (loan_amount / annual_income) if annual_income > 0 else 0
        if loan_to_income_ratio <= 0.5: pts += 15
        elif loan_to_income_ratio <= 1.0: pts += 10
        elif loan_to_income_ratio <= 2.0: pts += 5
        else: pts -= 15

        if employment == 'formal_employed': pts += 25
        elif employment == 'self_employed': pts += 18
        else: pts += 8

        if sector == 'trade': pts += 25
        elif sector == 'agriculture': pts += 22  
        elif sector == 'services': pts += 15
        else: pts += 10

        if has_collateral == 'yes': pts += 20
        if momo_proxy == 'yes': pts += 10

        if credit_history == '3_paid': pts += 15
        elif credit_history == '1-2_paid': pts += 10
        elif credit_history == 'none': pts += 5
        elif credit_history == 'defaulted': pts -= 10

        # 🌧️ CRITICAL FIX: Combine standard points with the newly verified rainfall risk parameters
        # Total maximum available scale points = 140 base + 100 rainfall = 240
        total_pts = pts + rainfall_pts
        score = max(0, min(100, round((total_pts / 240) * 100)))

        # Absolute hard safety limits
        if credit_history == 'defaulted' and score > 35:
            score = 35

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
             employment, sector, has_collateral, momo_proxy, credit_history, district,
             dti, score, decision, rec, officer)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (borrower_name, loan_amount, monthly_income, monthly_installment,
             employment, sector, has_collateral, momo_proxy, credit_history, district,
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

    district_name = row.get('district', 'Kabwe')
    weather_info = DISTRICT_RAINFALL_LOOKUP.get(district_name, {"annual_mm": 920, "zone": "Region IIa"})
    annual_mm = weather_info['annual_mm']
    
    if annual_mm >= 900: r_score = 100
    elif annual_mm >= 700: r_score = 80
    elif annual_mm >= 500: r_score = 60
    elif annual_mm >= 300: r_score = 40
    else: r_score = 20

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()

    if row['decision'] == 'APPROVE': dec_color = '#10B981'
    elif row['decision'] == 'CONDITIONAL': dec_color = '#F59E0B'
    elif row['decision'] == 'REFER': dec_color = '#3B82F6'
    else: dec_color = '#EF4444'

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
        ["EMPLOYMENT MATRIX", str(row['employment']).replace('_', ' ').title()],
        ["MARKET SECTOR", str(row['sector']).upper()],
        ["RISK PROVINCE / DISTRICT", f"{district_name} ({weather_info['zone']})"],
        ["CHIRPS ANNUAL RAINFALL", f"{annual_mm} mm (Index: {r_score}/100)"],
        ["COLLATERAL PROFILE", str(row['has_collateral']).upper()],
        ["MOMO VELOCITY PROXY", "YES / STRONG VELOCITY" if row['momo_proxy'] == 'yes' else "STANDARD ACTIVITY"],
        ["CREDIT BUREAU RECORD", str(row['credit_history']).replace('_', ' ').upper()],
        ["OFFICER SIGN-OFF", str(row['officer'])],
        ["TIMESTAMP", str(row['timestamp'])],
    ]
    tdata = [[Paragraph(r[0], bold_s), Paragraph(r[1], body_s)] for r in details]
    t = Table(tdata, colWidths=[200, 310])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(t)

    doc.build(story)
    buffer.seek(0)
    name = "".join(c for c in row['borrower_name'] if c.isalnum() or c in " _-").strip()
    return send_file(buffer, as_attachment=True, download_name=f"ZCI_Report_{name.replace(' ','_')}.pdf", mimetype='application/pdf')

if __name__ == '__main__':
    init_db()
    app.run(debug=True)