from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, datetime, timedelta
from functools import wraps
from sqlalchemy import func
from flask_socketio import SocketIO, emit
from flask import jsonify
from flask_login import current_user
import csv, io, os

# ==================== APP SETUP ====================

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "change-this-secret")
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sneakers.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ==================== CONSTANTS ====================

USD_TO_SLL = 23000
LOW_STOCK_THRESHOLD = 10
PER_PAGE = 15

OWNER_USERNAME = "MichaelJobieMusa"
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD")
# ==================== RBAC MODELS ====================

class Role(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'), nullable=False)
    role = db.relationship('Role')

# ==================== CORE MODELS ====================

class Shoe(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    brand = db.Column(db.String(100), nullable=False)
    model = db.Column(db.String(100), nullable=False)
    size = db.Column(db.String(10))
    cost_usd = db.Column(db.Float, nullable=False)
    sell_usd = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.Integer, default=0)

class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shoe_id = db.Column(db.Integer, db.ForeignKey('shoe.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    total_usd = db.Column(db.Float, nullable=False)
    profit_usd = db.Column(db.Float, nullable=False)
    sold_by = db.Column(db.String(50))
    date = db.Column(db.DateTime, default=datetime.utcnow)

    shoe = db.relationship('Shoe')

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100))
    amount_usd = db.Column(db.Float)
    date = db.Column(db.DateTime, default=datetime.utcnow)

    
 # ==================== PRODUCTS MODEL ====================

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)  # shoe / clothing
    cost_usd = db.Column(db.Float, nullable=False)
    price_usd = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.Integer, default=0)
   

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String(50))
    action = db.Column(db.String(200))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    message = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(50))

    # user specific notification
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    is_read = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")


class Restock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shoe_id = db.Column(db.Integer, db.ForeignKey('shoe.id'))
    quantity = db.Column(db.Integer)
    cost_usd = db.Column(db.Float)
    supplier = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    shoe = db.relationship("Shoe")


# ==================== HELPERS ====================

def usd_to_sll(value):
    """Convert USD to Sierra Leonean Leone using the configured rate."""
    return round((value or 0) * USD_TO_SLL, 2)


app.jinja_env.globals.update(usd_to_sll=usd_to_sll)


def log(action, commit=False):
    """
    Add an audit-log entry.

    By default, the caller controls the transaction and commits once
    all related database operations have succeeded.
    """
    try:
        db.session.add(
            AuditLog(
                user=session.get("username", "system"),
                action=action
            )
        )

        if commit:
            db.session.commit()

    except Exception:
        db.session.rollback()
        raise



# ==================== AUTH DECORATORS ====================

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))

        return f(*args, **kwargs)

    return wrapper


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))

            if session.get("role") not in roles:
                abort(403)

            return f(*args, **kwargs)

        return wrapper

    return decorator

# ==================== SEEDING ====================

DEFAULT_ROLES = ("owner", "admin", "staff")


def seed_roles():
    """Create the application's default roles if they don't exist."""

    for role_name in DEFAULT_ROLES:
        if not Role.query.filter_by(name=role_name).first():
            db.session.add(Role(name=role_name))

    db.session.commit()


def seed_owner():
    """
    Create the initial owner account if it does not already exist.

    OWNER_PASSWORD must be supplied through the environment.
    """

    owner = User.query.filter_by(
        username=OWNER_USERNAME
    ).first()

    if owner:
        return

    if not OWNER_PASSWORD:
        raise RuntimeError(
            "OWNER_PASSWORD environment variable is not configured. "
            "The initial owner account cannot be created."
        )

    owner_role = Role.query.filter_by(
        name="owner"
    ).first()

    if not owner_role:
        raise RuntimeError(
            "Owner role does not exist. Run seed_roles() first."
        )

    owner = User(
        username=OWNER_USERNAME,
        password_hash=generate_password_hash(OWNER_PASSWORD),
        role=owner_role
    )

    db.session.add(owner)
    db.session.commit()


def ensure_owner():
    db.create_all()
    """
    Ensure the default roles and owner account exist.
    """

    try:
        seed_roles()
        seed_owner()

    except Exception:
        db.session.rollback()
        raise


# ==================== AUTH ====================

@app.route("/", methods=["GET", "POST"])
def login():

    try:
        ensure_owner()
    except RuntimeError as error:
        flash(str(error))
        return render_template("login.html")

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Username and password are required.")
            return render_template("login.html")

        user = User.query.filter_by(
            username=username
        ).first()

        if user and check_password_hash(
            user.password_hash,
            password
        ):
            session.clear()

            session.update({
                "user_id": user.id,
                "username": user.username,
                "role": user.role.name
            })

            return redirect(url_for("dashboard"))

        flash("Invalid login credentials.")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ==================== DASHBOARD ====================

@app.route('/dashboard')
@login_required
def dashboard():

    inv_page = request.args.get('inv_page', 1, type=int)
    sales_page = request.args.get('sales_page', 1, type=int)

    shoes = Shoe.query.order_by(Shoe.id.desc()).paginate(
        page=inv_page,
        per_page=PER_PAGE,
        error_out=False
    )

    sales = Sale.query.order_by(Sale.date.desc()).paginate(
        page=sales_page,
        per_page=PER_PAGE,
        error_out=False
    )

    total_sales, total_profit = db.session.query(
        func.coalesce(func.sum(Sale.total_usd),0),
        func.coalesce(func.sum(Sale.profit_usd),0)
    ).first()

    total_expense = db.session.query(func.coalesce(func.sum(Expense.amount_usd),0)).scalar()
    net_profit = total_profit - total_expense

    daily_sales = dict(
        db.session.query(
            func.strftime('%Y-%m-%d', Sale.date),
            func.sum(Sale.total_usd)
        ).group_by(func.strftime('%Y-%m-%d', Sale.date))
    )

    # ----- BEST SELLERS GROUPED BY DATE -----
    from collections import defaultdict

    best_sellers_by_date = defaultdict(dict)

    sales_all = (
        db.session.query(
            func.strftime('%Y-%m-%d', Sale.date).label("sale_date"),
            (Shoe.brand + " " + Shoe.model).label("shoe_name"),
            func.coalesce(func.sum(Sale.quantity), 0).label("total_qty")
        )
        .join(Shoe, Sale.shoe_id == Shoe.id)
        .group_by(func.strftime('%Y-%m-%d', Sale.date), Shoe.id)
        .order_by(func.strftime('%Y-%m-%d', Sale.date))
        .all()
    )

    for row in sales_all:
        best_sellers_by_date[row.sale_date][row.shoe_name] = int(row.total_qty)

    best_sellers = dict(best_sellers_by_date)  # Convert to normal dict for Jinja

    low_stock = Shoe.query.filter(Shoe.quantity <= LOW_STOCK_THRESHOLD).all()

    # 🔔 High demand logic (last 7 days)
    week_ago = datetime.utcnow() - timedelta(days=7)

    high_demand = dict(
        db.session.query(
            (Shoe.brand + " " + Shoe.model),
            func.sum(Sale.quantity)
        )
        .join(Sale)
        .filter(Sale.date >= week_ago)
        .group_by(Shoe.id)
        .having(func.sum(Sale.quantity) >= 5)
    )

# ==================== REAL NOTIFICATIONS ====================

    notifications = Notification.query.filter(
    (Notification.user_id == session['user_id']) |
    (Notification.user_id == None)
    ).order_by(Notification.created_at.desc()).limit(10).all()

    # ==================== HEADER ACTIVITY ====================

    recent_sales = Sale.query.order_by(Sale.date.desc()).limit(5).all()
    recent_expenses = Expense.query.order_by(Expense.date.desc()).limit(5).all()
    recent_users = User.query.order_by(User.id.desc()).limit(5).all()

    # ==================== ALERT COUNTS ====================

    alerts_count = Notification.query.filter(
        ((Notification.user_id == session['user_id']) |
        (Notification.user_id == None)) &
        (Notification.is_read == False)
    ).count()

    unread_alerts = alerts_count

    users = User.query.join(Role).filter(
    Role.name != "owner"
).order_by(
    User.username.asc()
).all()

   # ==================== EXPENSE ACCOUNTABILITY ====================

    recent_expense_logs = AuditLog.query.filter(
        AuditLog.action.like("Added expense:%")
    ).order_by(
        AuditLog.timestamp.desc()
    ).limit(10).all()

    return render_template(
        'dashboard.html',
        shoes=shoes.items,
        sales=sales.items,
        inv_page=inv_page,
        sales_page=sales_page,
        total_inventory_pages=shoes.pages,
        total_sales_pages=sales.pages,
        total_sales_usd=total_sales,
        total_sales_sll=usd_to_sll(total_sales),
        total_profit_usd=total_profit,
        total_profit_sll=usd_to_sll(total_profit),
        total_expense_usd=total_expense,
        total_expense_sll=usd_to_sll(total_expense),
        net_profit_usd=net_profit,
        net_profit_sll=usd_to_sll(net_profit),
        daily_sales=daily_sales,
        best_sellers=best_sellers,
        low_stock=low_stock,
        high_demand=high_demand,
        alerts_count=alerts_count,
        notifications=notifications,
        unread_alerts=unread_alerts,
        LOW_STOCK_THRESHOLD=LOW_STOCK_THRESHOLD,
        recent_sales=recent_sales,
        recent_expenses=recent_expenses,
        recent_expense_logs=recent_expense_logs,
        recent_users=recent_users,
        users=users,
        role=session['role'],
        username=session['username']
        
    )

# ==================== INVENTORY ====================

@app.route('/add_shoe', methods=['POST'])
@login_required
@role_required('owner', 'admin')
def add_shoe():

    brand = request.form.get('brand', '').strip()
    model = request.form.get('model', '').strip()
    size = request.form.get('size', '').strip()

    try:
        cost_usd = float(request.form.get('cost_usd', 0))
        sell_usd = float(request.form.get('sell_usd', 0))
        quantity = int(request.form.get('quantity', 0))
    except (TypeError, ValueError):
        flash("Please enter valid price and quantity values.")
        return redirect(url_for('dashboard'))

    if not brand or not model:
        flash("Brand and model are required.")
        return redirect(url_for('dashboard'))

    if cost_usd < 0 or sell_usd < 0:
        flash("Prices cannot be negative.")
        return redirect(url_for('dashboard'))

    if quantity < 0:
        flash("Quantity cannot be negative.")
        return redirect(url_for('dashboard'))

    shoe = Shoe(
        brand=brand,
        model=model,
        size=size,
        cost_usd=cost_usd,
        sell_usd=sell_usd,
        quantity=quantity
    )

    try:
        db.session.add(shoe)
        log(f"Added shoe {brand} {model}")
        db.session.commit()

    except Exception:
        db.session.rollback()
        flash("Unable to add the shoe. Please try again.")
        return redirect(url_for('dashboard'))

    flash(f"{brand} {model} added successfully.")
    return redirect(url_for('dashboard'))

@app.route('/sell/<int:shoe_id>', methods=['POST'])
@login_required
@role_required('owner', 'admin', 'staff')
def sell(shoe_id):

    shoe = Shoe.query.get_or_404(shoe_id)

    try:
        qty = int(request.form.get('quantity', 0))
    except (TypeError, ValueError):
        flash("Invalid quantity.")
        return redirect(url_for('dashboard'))

    if qty <= 0:
        flash("Quantity must be greater than zero.")
        return redirect(url_for('dashboard'))

    if qty > shoe.quantity:
        flash(f"Only {shoe.quantity} unit(s) are available.")
        return redirect(url_for('dashboard'))

    try:
        # Reduce stock
        shoe.quantity -= qty

        total_usd = shoe.sell_usd * qty
        profit_usd = (shoe.sell_usd - shoe.cost_usd) * qty

        # Record sale
        sale = Sale(
            shoe=shoe,
            quantity=qty,
            total_usd=total_usd,
            profit_usd=profit_usd,
            sold_by=session['username']
        )

        db.session.add(sale)

        notifications_to_emit = []

        # Sale notification
        sale_msg = f"Sold {qty} - {shoe.brand} {shoe.model}"

        db.session.add(Notification(
            message=sale_msg,
            category="sale",
            user_id=session['user_id']
        ))

        notifications_to_emit.append(("sale", sale_msg))

        # Low-stock notification
        if shoe.quantity <= LOW_STOCK_THRESHOLD:

            low_msg = (
                f"Low stock: {shoe.brand} "
                f"{shoe.model} ({shoe.quantity} left)"
            )

            db.session.add(Notification(
                message=low_msg,
                category="low_stock",
                user_id=session['user_id']
            ))

            notifications_to_emit.append(("low_stock", low_msg))

        # Audit log participates in same transaction
        log(
            f"Sold {qty} of {shoe.brand} {shoe.model}"
        )

        # Single transaction
        db.session.commit()

    except Exception:
        db.session.rollback()
        flash("The sale could not be completed. No stock was changed.")
        return redirect(url_for('dashboard'))

    # Emit notifications only after successful commit
    unread_alerts = Notification.query.filter(
        (
            (Notification.user_id == session['user_id']) |
            (Notification.user_id == None)
        ) &
        (Notification.is_read == False)
    ).count()

    for notif_type, message in notifications_to_emit:

        socketio.emit("new_notification", {
            "type": notif_type,
            "message": message,
            "unread_count": unread_alerts
        })

    flash(
        f"Sale completed: {qty} × "
        f"{shoe.brand} {shoe.model}"
    )

    return redirect(url_for('dashboard'))

# ==================== EXPENSE ====================

@app.route('/add_expense', methods=['POST'])
@login_required
@role_required('owner', 'admin')
def add_expense():

    title = request.form.get('title', '').strip()

    try:
        amount_usd = float(
            request.form.get('amount_usd', 0)
        )
    except (TypeError, ValueError):
        flash("Please enter a valid expense amount.")
        return redirect(url_for('dashboard'))

    if not title:
        flash("Expense title is required.")
        return redirect(url_for('dashboard'))

    if amount_usd <= 0:
        flash("Expense amount must be greater than zero.")
        return redirect(url_for('dashboard'))

    try:

        db.session.add(Expense(
            title=title,
            amount_usd=amount_usd
        ))

        msg = f"Expense added: {title}"

        db.session.add(Notification(
            message=msg,
            category="expense",
            user_id=session['user_id']
        ))

        log(f"Added expense: {title} (${amount_usd:.2f})")

        db.session.commit()

    except Exception:
        db.session.rollback()
        flash("Unable to save the expense.")
        return redirect(url_for('dashboard'))

    unread_count = Notification.query.filter(
        (
            (Notification.user_id == session['user_id']) |
            (Notification.user_id == None)
        ) &
        (Notification.is_read == False)
    ).count()

    socketio.emit("new_notification", {
        "type": "expense",
        "message": msg,
        "unread_count": unread_count
    })

    flash("Expense added successfully.")
    return redirect(url_for('dashboard'))

# ==================== USERS ====================

@app.route('/add_staff', methods=['POST'])
@login_required
@role_required('owner')
def add_staff():

    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    role_name = request.form.get('role', '').strip().lower()

    if not username or not password:
        flash("Username and password are required.")
        return redirect(url_for('dashboard'))

    if username == OWNER_USERNAME:
        flash("Owner account cannot be modified.")
        return redirect(url_for('dashboard'))

    if len(password) < 6:
        flash("Password must be at least 6 characters.")
        return redirect(url_for('dashboard'))

    if role_name not in ('admin', 'staff'):
        flash("Invalid staff role.")
        return redirect(url_for('dashboard'))

    if User.query.filter_by(username=username).first():
        flash("User already exists.")
        return redirect(url_for('dashboard'))

    role = Role.query.filter_by(name=role_name).first()

    if not role:
        flash("Selected role does not exist.")
        return redirect(url_for('dashboard'))

    try:
        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            role=role
        )

        db.session.add(user)

        msg = f"New user created: {username}"

        db.session.add(Notification(
            message=msg,
            category="user",
            user_id=session['user_id']
        ))

        log(f"Created user {username}")

        db.session.commit()

    except Exception:
        db.session.rollback()
        flash("Unable to create the staff account.")
        return redirect(url_for('dashboard'))

    unread_count = Notification.query.filter(
        (
            (Notification.user_id == session['user_id']) |
            (Notification.user_id == None)
        ) &
        (Notification.is_read == False)
    ).count()

    socketio.emit("new_notification", {
        "type": "user",
        "message": msg,
        "unread_count": unread_count
    })

    flash(f"Staff account '{username}' created successfully.")
    return redirect(url_for('dashboard'))

@app.route("/staff_list")
@login_required
@role_required("owner")
def staff_list():

    users = User.query.join(Role).filter(Role.name != "owner").all()

    return render_template(
        "staff_list.html",
        users=users,
        role=session["role"],
        username=session["username"]
    )

@app.route("/update_role/<int:user_id>", methods=["POST"])
@login_required
@role_required("owner")
def update_role(user_id):

    user = User.query.get_or_404(user_id)

    if user.username == OWNER_USERNAME:
        return jsonify({
            "status": "error",
            "message": "Owner role cannot be changed."
        }), 403

    if user.id == session["user_id"]:
        return jsonify({
            "status": "error",
            "message": "You cannot change your own role."
        }), 400

    data = request.get_json(silent=True) or {}
    role_name = str(data.get("role", "")).strip().lower()

    if role_name not in ("admin", "staff"):
        return jsonify({
            "status": "error",
            "message": "Invalid role."
        }), 400

    new_role = Role.query.filter_by(name=role_name).first()

    if not new_role:
        return jsonify({
            "status": "error",
            "message": "Role not found."
        }), 404

    try:
        old_role = user.role.name

        user.role = new_role

        log(
            f"Changed role for {user.username}: "
            f"{old_role} -> {role_name}"
        )

        db.session.commit()

    except Exception:
        db.session.rollback()

        return jsonify({
            "status": "error",
            "message": "Unable to update role."
        }), 500

    return jsonify({
        "status": "success",
        "message": f"{user.username} is now {role_name}."
    })

@app.route("/reset_password/<int:user_id>", methods=["POST"])
@login_required
@role_required("owner")
def reset_password(user_id):

    user = User.query.get_or_404(user_id)

    if user.username == OWNER_USERNAME:
        return jsonify({
            "status": "error",
            "message": "Owner password must be changed separately."
        }), 403

    data = request.get_json(silent=True) or {}
    new_pass = str(data.get("password", "")).strip()

    if len(new_pass) < 6:
        return jsonify({
            "status": "error",
            "message": "Password must be at least 6 characters."
        }), 400

    try:
        user.password_hash = generate_password_hash(new_pass)

        log(
            f"Reset password for user {user.username}"
        )

        db.session.commit()

    except Exception:
        db.session.rollback()

        return jsonify({
            "status": "error",
            "message": "Unable to reset password."
        }), 500

    return jsonify({
        "status": "success",
        "message": "Password reset successfully."
    })

@app.route("/delete_staff/<int:user_id>", methods=["POST"])
@login_required
@role_required("owner")
def delete_staff(user_id):

    user = User.query.get_or_404(user_id)

    if user.username == OWNER_USERNAME:
        return jsonify({
            "status": "error",
            "message": "Owner account cannot be deleted."
        }), 403

    if user.id == session["user_id"]:
        return jsonify({
            "status": "error",
            "message": "You cannot delete your own account."
        }), 400

    username = user.username

    try:
        db.session.delete(user)

        log(f"Deleted user {username}")

        db.session.commit()

    except Exception:
        db.session.rollback()

        return jsonify({
            "status": "error",
            "message": "Unable to delete staff member."
        }), 500

    return jsonify({
        "status": "success",
        "message": f"{username} deleted successfully."
    })

    
@app.route("/restock", methods=["POST"])
@login_required
@role_required("owner", "admin")
def restock():

    try:
        shoe_id = int(request.form.get("shoe_id", 0))
        quantity = int(request.form.get("quantity", 0))
        cost = float(request.form.get("cost_usd", 0))
    except (TypeError, ValueError):
        flash("Invalid restock information.")
        return redirect(url_for("products"))

    supplier = request.form.get("supplier", "").strip()

    if quantity <= 0:
        flash("Restock quantity must be greater than zero.")
        return redirect(url_for("products"))

    if cost < 0:
        flash("Cost cannot be negative.")
        return redirect(url_for("products"))

    shoe = Shoe.query.get_or_404(shoe_id)

    try:
        shoe.quantity += quantity
        shoe.cost_usd = cost

        restock = Restock(
            shoe_id=shoe.id,
            quantity=quantity,
            cost_usd=cost,
            supplier=supplier
        )

        db.session.add(restock)

        log(
            f"Restocked {quantity} of "
            f"{shoe.brand} {shoe.model}"
        )

        db.session.commit()

    except Exception:
        db.session.rollback()
        flash("Unable to complete the restock.")
        return redirect(url_for("products"))

    flash(
        f"{shoe.brand} {shoe.model} restocked successfully."
    )

    return redirect(url_for("products"))

# ==================== EXPORT ====================

@app.route('/export_sales')
@login_required
@role_required('owner','admin')
def export_sales():

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['Date','Shoe','Qty','Total USD','Profit USD','Sold By'])

    for s in Sale.query.order_by(Sale.date.desc()).all():
        shoe_name = f"{s.shoe.brand} {s.shoe.model}" if s.shoe else "Deleted Shoe"
        writer.writerow([
            s.date.strftime('%Y-%m-%d %H:%M'),
            shoe_name,
            s.quantity,
            s.total_usd,
            s.profit_usd,
            s.sold_by
        ])

    return send_file(
        io.BytesIO(out.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name="sales_history.csv"
    )

@app.route('/receipt/<int:sale_id>')
@login_required
def receipt(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    return render_template('receipt.html', sale=sale)

# ==================== SALES HISTORY ====================

@app.route("/sales_history")
@login_required
def sales_history():

    from sqlalchemy import func
    from collections import defaultdict
    from datetime import datetime, timedelta
    import json

    page = request.args.get("page", 1, type=int)

    # ===== GET FILTER VALUES =====
    user_filter = request.args.get("user")
    search = request.args.get("search")
    start = request.args.get("start")
    end = request.args.get("end")
    quick = request.args.get("quick")

    # ===== BASE QUERY =====
    query = Sale.query

    # ===== QUICK DATE FILTERS =====
    today = datetime.today()

    if quick == "today":
        query = query.filter(func.date(Sale.date) == today.date())

    elif quick == "week":
        query = query.filter(Sale.date >= today - timedelta(days=7))

    elif quick == "month":
        query = query.filter(Sale.date >= today.replace(day=1))

    elif quick == "30days":
        query = query.filter(Sale.date >= today - timedelta(days=30))

        # Quick filters take priority over custom date fields.
    if quick in ["today", "week", "month", "30days"]:
        start = None
        end = None

    # ===== CUSTOM DATE FILTER =====

    if start:
        start_date = datetime.strptime(start, "%Y-%m-%d")
        query = query.filter(Sale.date >= start_date)

    if end:
        end_date = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
        query = query.filter(Sale.date < end_date)

    # ===== STAFF FILTER =====
    if user_filter:
        query = query.filter(Sale.sold_by == user_filter)

    # ===== SEARCH (Brand, Model or Size) =====
    if search:
        query = query.join(Shoe).filter(
        (Shoe.brand.ilike(f"%{search}%")) |
        (Shoe.model.ilike(f"%{search}%")) |
        (Shoe.size.ilike(f"%{search}%"))
    )

    # ===== ORDER =====
    query = query.order_by(Sale.date.desc())

    # ===== GET ALL FILTERED RESULTS FOR KPI + CHARTS =====
    filtered_sales = query.all()

    # ===== PAGINATION =====
    sales_pagination = query.paginate(
        page=page,
        per_page=PER_PAGE,
        error_out=False
    )

    sales = sales_pagination.items

    # ================= KPIs =================

    total_sales = sum(s.total_usd for s in filtered_sales)
    total_profit = sum(s.profit_usd for s in filtered_sales)
    total_items = sum(s.quantity for s in filtered_sales)

    total_transactions = len(filtered_sales)
    avg_sale = total_sales / total_transactions if total_transactions > 0 else 0

    # ===== TOP STAFF =====
    staff_totals = defaultdict(float)
    for s in filtered_sales:
        staff_totals[s.sold_by] += s.total_usd

    top_staff = max(staff_totals, key=staff_totals.get) if staff_totals else "-"

    # ===== TOP PRODUCT =====
    product_totals = defaultdict(int)
    for s in filtered_sales:
        product_totals[f"{s.shoe.brand} {s.shoe.model}"] += s.quantity

    top_product = max(product_totals, key=product_totals.get) if product_totals else "-"

    # ===== STAFF LIST FOR DROPDOWN =====
    staff_list = db.session.query(Sale.sold_by).distinct().all()
    staff_list = [s[0] for s in staff_list]

    # ================= CHART DATA =================

    trend_dict = defaultdict(float)
    for s in filtered_sales:
        day = s.date.strftime("%Y-%m-%d")
        trend_dict[day] += s.total_usd

    trend_labels = sorted(trend_dict.keys())
    trend_data = [trend_dict[d] for d in trend_labels]

    staff_labels = list(staff_totals.keys())
    staff_data = list(staff_totals.values())

    return render_template(
        "sales_history.html",
        sales=sales,
        total_sales=total_sales,
        total_profit=total_profit,
        total_items=total_items,
        avg_sale=avg_sale,
        top_staff=top_staff,
        top_product=top_product,
        staff_list=staff_list,
        user_filter=user_filter,
        search=search,
        start=start,
        end=end,
        current_page=page,
        total_pages=sales_pagination.pages,
        role=session["role"],
        username=session["username"],
        trend_labels=json.dumps(trend_labels),
        trend_data=json.dumps(trend_data),
        staff_labels=json.dumps(staff_labels),
        staff_data=json.dumps(staff_data)
    )
# ==================== PRODUCTS ====================
@app.route("/products")
@login_required
def products():

    shoes = Shoe.query.order_by(
        Shoe.id.desc()
    ).all()

    products = Product.query.order_by(
        Product.id.desc()
    ).all()

    restocks = Restock.query.order_by(
        Restock.created_at.desc()
    ).all()

    brands = sorted({
        shoe.brand
        for shoe in shoes
        if shoe.brand
    })

    sizes = sorted({
        shoe.size
        for shoe in shoes
        if shoe.size
    })

    return render_template(
        "products.html",
        shoes=shoes,
        products=products,
        restocks=restocks,
        brands=brands,
        sizes=sizes,
        role=session["role"],
        username=session["username"],
        LOW_STOCK_THRESHOLD=LOW_STOCK_THRESHOLD
    )

@app.route('/add_product', methods=['POST'])
@login_required
@role_required('owner', 'admin')
def add_product():

    name = request.form.get('name', '').strip()
    category = request.form.get('category', '').strip()

    try:
        cost_usd = float(request.form.get('cost_usd', 0))
        price_usd = float(request.form.get('price_usd', 0))
        quantity = int(request.form.get('quantity', 0))
    except (TypeError, ValueError):
        flash("Please enter valid product values.")
        return redirect(url_for('products'))

    if not name or not category:
        flash("Product name and category are required.")
        return redirect(url_for('products'))

    if cost_usd < 0 or price_usd < 0:
        flash("Product prices cannot be negative.")
        return redirect(url_for('products'))

    if quantity < 0:
        flash("Product quantity cannot be negative.")
        return redirect(url_for('products'))

    try:
        product = Product(
            name=name,
            category=category,
            cost_usd=cost_usd,
            price_usd=price_usd,
            quantity=quantity
        )

        db.session.add(product)

        log(f"Added product {name}")

        db.session.commit()

    except Exception:
        db.session.rollback()
        flash("Unable to add the product.")
        return redirect(url_for('products'))

    flash(f"{name} added successfully.")
    return redirect(url_for('products'))

@app.route("/kpi-data")
@login_required
def kpi_data():
    range_type = request.args.get("range", "daily")
    now = datetime.utcnow()

    # Determine start date
    if range_type == "daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif range_type == "monthly":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif range_type == "quarterly":
        quarter = (now.month - 1) // 3
        start_month = quarter * 3 + 1
        start = now.replace(month=start_month, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif range_type == "yearly":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Filtered sales
    sales = Sale.query.filter(Sale.date >= start).all()

    revenue = sum(s.total_usd for s in sales)
    profit = sum(s.profit_usd for s in sales)
    total_sales = len(sales)

    # Filtered expenses
    expenses = db.session.query(
        func.coalesce(func.sum(Expense.amount_usd), 0)
    ).filter(Expense.date >= start).scalar()

    return jsonify({
        "revenue": revenue,
        "profit": profit,
        "sales": total_sales,
        "expenses": expenses
    })

@app.route("/mark_all_read", methods=["POST"])
@login_required
def mark_all_read():

    Notification.query.filter_by(
        user_id=session['user_id'],
        is_read=False
    ).update({"is_read": True})

    db.session.commit()

    return jsonify({"status": "success", "unread_count": 0})

@app.route("/mark_notification_read/<int:notif_id>", methods=["POST"])
@login_required
def mark_notification_read(notif_id):

    notification = Notification.query.filter_by(
        id=notif_id,
        user_id=session['user_id']
    ).first_or_404()

    notification.is_read = True
    db.session.commit()

    unread_count = Notification.query.filter_by(
        user_id=session['user_id'],
        is_read=False
    ).count()

    return jsonify({
        "status": "success",
        "unread_count": unread_count
    })

    return jsonify({"status": "error"}), 404


@app.cli.command("reset-owner-password")
def reset_owner_password():

    owner = User.query.filter_by(username=OWNER_USERNAME).first()

    if not owner:
        print("Owner account not found")
        return

    new_password = input("Enter new owner password: ")

    owner.password_hash = generate_password_hash(new_password)

    db.session.commit()

    print("Owner password successfully reset")



with app.app_context():
    db.create_all()

# ==================== INIT ====================

if __name__ == "__main__":

    with app.app_context():

        # db.create_all()

        ensure_owner()

    socketio.run(
        app,
        debug=False
    )