"""
Petrol Station Management System (PSMS)
Flask Backend - app.py
CS 2005: Database Systems
"""

from flask import Flask, request, jsonify, session, render_template, redirect, url_for
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
import pg8000
import pg8000.native
from datetime import datetime
import uuid
import os
import ssl
from functools import wraps

app = Flask(__name__, template_folder='frontend/templates', static_folder='frontend/static')
app.secret_key = os.environ.get('SECRET_KEY', 'psms-secret-dev-key-change-in-prod')
CORS(app, supports_credentials=True)

def get_db():
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return pg8000.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        port=int(os.environ.get('DB_PORT', 5432)),
        database=os.environ.get('DB_NAME', 'psms_db'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASS', 'postgres'),
        ssl_context=ssl_context
    )

def query(conn, sql, params=None):
    cur = conn.cursor()
    cur.execute(sql, params or [])
    if cur.description is None:
        return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]

def query_one(conn, sql, params=None):
    results = query(conn, sql, params)
    return results[0] if results else None

def execute(conn, sql, params=None):
    cur = conn.cursor()
    cur.execute(sql, params or [])
    if cur.description:
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        return dict(zip(cols, row)) if row else None
    return None

def serialize_row(r):
    row = {}
    for k, v in r.items():
        if hasattr(v, 'isoformat'):
            row[k] = v.isoformat()
        elif hasattr(v, '__float__') and not isinstance(v, (int, bool, str)):
            row[k] = float(v)
        else:
            row[k] = v
    return row

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        if session.get('role') != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    return render_template('dashboard.html', role=session.get('role'), name=session.get('name'))

@app.route('/pos')
def pos():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    return render_template('pos.html', role=session.get('role'), name=session.get('name'))

@app.route('/inventory')
def inventory():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    return render_template('inventory.html', role=session.get('role'), name=session.get('name'))

@app.route('/shifts')
def shifts_page():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    return render_template('shifts.html', role=session.get('role'), name=session.get('name'))

@app.route('/reports')
def reports_page():
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('dashboard'))
    return render_template('reports.html', role=session.get('role'), name=session.get('name'))

@app.route('/employees')
def employees_page():
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('dashboard'))
    return render_template('employees.html', role=session.get('role'), name=session.get('name'))

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    conn = get_db()
    user = query_one(conn, "SELECT * FROM employees WHERE username = %s AND is_active = TRUE", [username])
    conn.close()
    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': 'Invalid credentials'}), 401
    session['user_id'] = user['employee_id']
    session['username'] = user['username']
    session['name'] = user['full_name']
    session['role'] = user['role']
    return jsonify({'message': 'Login successful', 'role': user['role'], 'name': user['full_name']})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'message': 'Logged out'})

@app.route('/api/me', methods=['GET'])
@login_required
def api_me():
    return jsonify({'user_id': session['user_id'], 'name': session['name'], 'role': session['role']})

@app.route('/api/dashboard/stats', methods=['GET'])
@login_required
def api_dashboard_stats():
    conn = get_db()
    today = query_one(conn, "SELECT COALESCE(SUM(total_amount), 0) AS today_revenue, COUNT(*) AS today_transactions FROM transactions WHERE DATE(created_at) = CURRENT_DATE")
    active_shift = query_one(conn, "SELECT shift_id, start_time, total_sales FROM shifts WHERE employee_id = %s AND status = 'active' ORDER BY start_time DESC LIMIT 1", [session['user_id']])
    low_stock = query_one(conn, "SELECT COUNT(*) AS cnt FROM v_low_stock_tanks")
    emp_count = query_one(conn, "SELECT COUNT(*) AS cnt FROM employees WHERE is_active = TRUE")
    weekly = query(conn, "SELECT DATE(created_at) AS day, COALESCE(SUM(total_amount), 0) AS revenue FROM transactions WHERE created_at >= CURRENT_DATE - INTERVAL '6 days' GROUP BY DATE(created_at) ORDER BY day")
    conn.close()
    return jsonify({
        'today_revenue': float(today['today_revenue']) if today else 0,
        'today_transactions': today['today_transactions'] if today else 0,
        'active_shift': serialize_row(active_shift) if active_shift else None,
        'low_stock_alerts': low_stock['cnt'] if low_stock else 0,
        'active_employees': emp_count['cnt'] if emp_count else 0,
        'weekly_revenue': [serialize_row(r) for r in weekly]
    })

@app.route('/api/fuel-types', methods=['GET'])
@login_required
def api_fuel_types():
    conn = get_db()
    rows = query(conn, "SELECT * FROM fuel_types ORDER BY fuel_type_id")
    conn.close()
    return jsonify([serialize_row(r) for r in rows])

@app.route('/api/fuel-types/<int:fid>', methods=['PUT'])
@admin_required
def api_update_fuel_price(fid):
    data = request.get_json()
    conn = get_db()
    execute(conn, "UPDATE fuel_types SET price_per_liter = %s, updated_at = NOW() WHERE fuel_type_id = %s", [data.get('price_per_liter'), fid])
    conn.commit()
    conn.close()
    return jsonify({'message': 'Price updated'})

@app.route('/api/tanks', methods=['GET'])
@login_required
def api_tanks():
    conn = get_db()
    rows = query(conn, """
        SELECT tk.tank_id, tk.tank_name, tk.fuel_type_id, tk.capacity_liters,
               tk.current_level, tk.low_stock_alert, tk.last_refilled,
               ft.name AS fuel_name, ft.price_per_liter,
               ROUND((tk.current_level / tk.capacity_liters) * 100, 1) AS fill_pct
        FROM tanks tk JOIN fuel_types ft ON tk.fuel_type_id = ft.fuel_type_id ORDER BY tk.tank_id
    """)
    conn.close()
    return jsonify([serialize_row(r) for r in rows])

@app.route('/api/tanks/<int:tid>/refill', methods=['POST'])
@admin_required
def api_refill_tank(tid):
    liters = float(request.get_json().get('liters', 0))
    conn = get_db()
    row = execute(conn, "UPDATE tanks SET current_level = LEAST(current_level + %s, capacity_liters), last_refilled = NOW() WHERE tank_id = %s RETURNING tank_name, current_level, capacity_liters", [liters, tid])
    conn.commit()
    conn.close()
    return jsonify({'message': 'Tank refilled', 'tank': serialize_row(row)})

@app.route('/api/pumps', methods=['GET'])
@login_required
def api_pumps():
    conn = get_db()
    pumps = query(conn, "SELECT pump_id, pump_number, location_label, is_active FROM pumps WHERE is_active = TRUE ORDER BY pump_number")
    result = []
    for p in pumps:
        fuels = query(conn, """
            SELECT ft.fuel_type_id, ft.name AS fuel_name, ft.price_per_liter AS price, pft.tank_id, tk.current_level AS tank_level
            FROM pump_fuel_types pft JOIN fuel_types ft ON pft.fuel_type_id = ft.fuel_type_id JOIN tanks tk ON pft.tank_id = tk.tank_id WHERE pft.pump_id = %s
        """, [p['pump_id']])
        p['fuels'] = [serialize_row(f) for f in fuels]
        result.append(p)
    conn.close()
    return jsonify(result)

@app.route('/api/items', methods=['GET'])
@login_required
def api_items():
    conn = get_db()
    rows = query(conn, "SELECT * FROM convenience_items WHERE is_available = TRUE ORDER BY category, item_name")
    conn.close()
    return jsonify([serialize_row(r) for r in rows])

@app.route('/api/items', methods=['POST'])
@admin_required
def api_add_item():
    data = request.get_json()
    conn = get_db()
    row = execute(conn, "INSERT INTO convenience_items (item_name, price, stock_quantity, category) VALUES (%s, %s, %s, %s) RETURNING item_id", [data['item_name'], data['price'], data.get('stock_quantity', 0), data.get('category', '')])
    conn.commit()
    conn.close()
    return jsonify({'item_id': row['item_id'], 'message': 'Item added'})

@app.route('/api/shifts/start', methods=['POST'])
@login_required
def api_start_shift():
    conn = get_db()
    if query_one(conn, "SELECT shift_id FROM shifts WHERE employee_id = %s AND status = 'active'", [session['user_id']]):
        conn.close()
        return jsonify({'error': 'You already have an active shift'}), 400
    row = execute(conn, "INSERT INTO shifts (employee_id) VALUES (%s) RETURNING shift_id, start_time", [session['user_id']])
    conn.commit()
    conn.close()
    return jsonify({'shift_id': row['shift_id'], 'start_time': str(row['start_time'])})

@app.route('/api/shifts/end', methods=['POST'])
@login_required
def api_end_shift():
    conn = get_db()
    row = execute(conn, "UPDATE shifts SET status = 'closed', end_time = NOW() WHERE employee_id = %s AND status = 'active' RETURNING shift_id, total_sales, cash_collected, card_collected", [session['user_id']])
    conn.commit()
    conn.close()
    if not row:
        return jsonify({'error': 'No active shift found'}), 404
    return jsonify(serialize_row(row))

@app.route('/api/shifts', methods=['GET'])
@login_required
def api_shifts():
    conn = get_db()
    if session['role'] == 'admin':
        rows = query(conn, "SELECT * FROM v_shift_summary ORDER BY shift_id DESC LIMIT 50")
    else:
        rows = query(conn, "SELECT * FROM v_shift_summary WHERE full_name = %s ORDER BY shift_id DESC LIMIT 20", [session['name']])
    conn.close()
    return jsonify([serialize_row(r) for r in rows])

@app.route('/api/shifts/active', methods=['GET'])
@login_required
def api_active_shift():
    conn = get_db()
    row = query_one(conn, "SELECT shift_id, start_time, total_sales, cash_collected, card_collected FROM shifts WHERE employee_id = %s AND status = 'active' ORDER BY start_time DESC LIMIT 1", [session['user_id']])
    conn.close()
    return jsonify(serialize_row(row) if row else None)

@app.route('/api/transactions', methods=['POST'])
@login_required
def api_create_transaction():
    data = request.get_json()
    conn = get_db()
    shift = query_one(conn, "SELECT shift_id FROM shifts WHERE employee_id = %s AND status = 'active'", [session['user_id']])
    if not shift:
        conn.close()
        return jsonify({'error': 'No active shift. Please start a shift first.'}), 400
    receipt_no = 'RCP-' + datetime.now().strftime('%Y%m%d%H%M%S') + '-' + str(uuid.uuid4())[:4].upper()
    try:
        txn = execute(conn, """
            INSERT INTO transactions (shift_id, employee_id, pump_id, fuel_type_id, liters_dispensed, fuel_amount, convenience_amount, total_amount, payment_method, receipt_number)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING transaction_id
        """, [shift['shift_id'], session['user_id'], data.get('pump_id'), data.get('fuel_type_id'), data.get('liters_dispensed'), float(data.get('fuel_amount', 0)), float(data.get('convenience_amount', 0)), float(data.get('total_amount', 0)), data.get('payment_method', 'cash'), receipt_no])
        txn_id = txn['transaction_id']
        for item in data.get('items', []):
            execute(conn, "INSERT INTO transaction_items (transaction_id, item_id, quantity, unit_price, subtotal) VALUES (%s, %s, %s, %s, %s)", [txn_id, item['item_id'], item['quantity'], item['unit_price'], item['quantity'] * item['unit_price']])
        conn.commit()
        return jsonify({'transaction_id': txn_id, 'receipt_number': receipt_no, 'message': 'Transaction recorded'})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/transactions', methods=['GET'])
@login_required
def api_list_transactions():
    conn = get_db()
    sql = """SELECT t.transaction_id, t.total_amount, t.payment_method, t.receipt_number, t.created_at, e.full_name, p.pump_number, ft.name AS fuel_name
             FROM transactions t JOIN employees e ON t.employee_id = e.employee_id
             LEFT JOIN pumps p ON t.pump_id = p.pump_id LEFT JOIN fuel_types ft ON t.fuel_type_id = ft.fuel_type_id"""
    if session['role'] == 'admin':
        rows = query(conn, sql + " ORDER BY t.created_at DESC LIMIT 100")
    else:
        rows = query(conn, sql + " WHERE t.employee_id = %s ORDER BY t.created_at DESC LIMIT 50", [session['user_id']])
    conn.close()
    return jsonify([serialize_row(r) for r in rows])

@app.route('/api/reports/daily', methods=['GET'])
@admin_required
def api_report_daily():
    conn = get_db()
    rows = query(conn, "SELECT * FROM v_daily_revenue LIMIT 30")
    conn.close()
    return jsonify([serialize_row(r) for r in rows])

@app.route('/api/reports/fuel', methods=['GET'])
@admin_required
def api_report_fuel():
    conn = get_db()
    rows = query(conn, "SELECT * FROM v_fuel_revenue")
    conn.close()
    return jsonify([serialize_row(r) for r in rows])

@app.route('/api/reports/low-stock', methods=['GET'])
@login_required
def api_report_low_stock():
    conn = get_db()
    rows = query(conn, "SELECT * FROM v_low_stock_tanks")
    conn.close()
    return jsonify([serialize_row(r) for r in rows])

@app.route('/api/employees', methods=['GET'])
@admin_required
def api_employees():
    conn = get_db()
    rows = query(conn, "SELECT employee_id, full_name, username, role, phone, hire_date, is_active FROM employees ORDER BY employee_id")
    conn.close()
    return jsonify([serialize_row(r) for r in rows])

@app.route('/api/employees', methods=['POST'])
@admin_required
def api_add_employee():
    data = request.get_json()
    conn = get_db()
    row = execute(conn, "INSERT INTO employees (full_name, username, password_hash, role, phone, hire_date) VALUES (%s, %s, %s, %s, %s, CURRENT_DATE) RETURNING employee_id", [data['full_name'], data['username'], generate_password_hash(data['password']), data['role'], data.get('phone', '')])
    conn.commit()
    conn.close()
    return jsonify({'employee_id': row['employee_id'], 'message': 'Employee added'})

@app.route('/api/employees/<int:eid>', methods=['PUT'])
@admin_required
def api_update_employee(eid):
    data = request.get_json()
    conn = get_db()
    execute(conn, "UPDATE employees SET full_name = %s, phone = %s, role = %s, is_active = %s WHERE employee_id = %s", [data['full_name'], data.get('phone', ''), data['role'], data.get('is_active', True), eid])
    conn.commit()
    conn.close()
    return jsonify({'message': 'Employee updated'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
