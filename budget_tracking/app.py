from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import urllib
from datetime import datetime
import calendar
from sqlalchemy import extract, func

# Flask setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'supersecretkey123'

# MsSQL connection
params = urllib.parse.quote_plus(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=SINEM\\SQLEXPRESS01;"
    "DATABASE=BudgetDB;"
    "Trusted_Connection=yes;"
)
app.config['SQLALCHEMY_DATABASE_URI'] = f"mssql+pyodbc:///?odbc_connect={params}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Database and login manager
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = "info"

# User model
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    expenses = db.relationship('Expense', backref='user', lazy=True)

# Expense model
class Expense(db.Model):
    __tablename__ = 'expenses'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, nullable=False)
    note = db.Column(db.String(200))

# Create tables if not exist
with app.app_context():
    db.create_all()

# User loader
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Home route (/)
@app.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return redirect(url_for('login'))

# Register route
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form['confirmPassword']

        if password != confirm_password:
            flash("Passwords do not match!", "danger")
            return redirect(url_for('register'))

        if User.query.filter_by(username=username).first():
            flash("This username is already taken!", "danger")
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(password)
        new_user = User(username=username, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        flash("Registration successful! You can log in now.", "success")
        return redirect(url_for('login'))

    return render_template('register.html')

# Login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index')) 

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            flash("Incorrect username or password!", "danger")
            return redirect(url_for('login'))

    return render_template('login.html')

# Index route (Ana Sayfa)
@app.route('/index')
@login_required
def index():
    # Buraya Ana Sayfa (Dashboard) mantığınız gelecek.
    return render_template('index.html', username=current_user.username)

# Logout route
@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash("Successfully logged out.", "info")
    return redirect(url_for('login'))

@app.route('/forecast')
@login_required
def forecast():
    today = datetime.today()
    current_month = today.month
    current_month_name = calendar.month_name[current_month]

    # Bir sonraki ay
    next_month = current_month + 1 if current_month < 12 else 1
    next_month_name = calendar.month_name[next_month]

    # --- 1. Kullanıcının kategorilerini DB'den çek ---
    categories = (
        db.session.query(Expense.category)
        .filter_by(user_id=current_user.id)
        .distinct()
        .all()
    )
    categories = [c[0] for c in categories]

    # --- 2. Mevcut ay harcamalarını kategori bazlı çek ---
    current_month_expenses = {}
    for cat in categories:
        total = (
            db.session.query(func.sum(Expense.amount))
            .filter_by(user_id=current_user.id, category=cat)
            .filter(extract('month', Expense.date) == current_month)
            .scalar()
        )
        current_month_expenses[cat] = total or 0

    total_current = sum(current_month_expenses.values())

    # --- 3. Basit tahmin modeli: %5 artış ---
    predicted_expenses = {cat: amt * 1.05 for cat, amt in current_month_expenses.items()}
    total_predicted = sum(predicted_expenses.values())

    return render_template(
        'forecast.html',
        username=current_user.username,
        current_month_name=current_month_name,
        current_month_expenses=current_month_expenses,
        total_current=total_current,
        next_month_name=next_month_name,
        predicted_expenses=predicted_expenses,
        total_predicted=total_predicted
    )

# Analysis & Tips Route
@app.route('/analysis')
@login_required
def analysis():
    # Buraya harcama analizleri ve tasarruf ipuçları mantığı gelecek.
    return render_template('analysis.html', username=current_user.username)
# --- YENİ EKLENEN SİDEBAR ROTALARI SONU ---

# ==========================
# Expenses CRUD routes
# ==========================

@app.route('/expenses', methods=['GET', 'POST'])
@login_required
def get_expenses():
    # Tüm kategorileri çek (distinct)
    categories = (
        db.session.query(Expense.category)
        .filter_by(user_id=current_user.id)
        .distinct()
        .all()
    )
    categories = [c[0] for c in categories]  # ('Food',) → "Food"

    # Filtre parametreleri
    filter_category = request.args.get('category', '').strip()
    min_amount = request.args.get('min_amount', '').strip()
    max_amount = request.args.get('max_amount', '').strip()
    filter_month = request.args.get('month', '').strip()  # "YYYY-MM" formatında

    # Temel query
    query = Expense.query.filter_by(user_id=current_user.id)

    # Kategori filtresi
    if filter_category:
        query = query.filter(Expense.category == filter_category)

    # Min Amount filtresi
    if min_amount:
        try:
            min_val = float(min_amount)
            query = query.filter(Expense.amount >= min_val)
        except ValueError:
            pass  # Geçersizse filtreleme yapılmasın

    # Max Amount filtresi
    if max_amount:
        try:
            max_val = float(max_amount)
            query = query.filter(Expense.amount <= max_val)
        except ValueError:
            pass

    # Month filtresi
    if filter_month:
        try:
            year, month = map(int, filter_month.split('-'))
            query = query.filter(
                db.extract('year', Expense.date) == year,
                db.extract('month', Expense.date) == month
            )
        except ValueError:
            pass

    expenses = query.order_by(Expense.date.desc()).all()

    return render_template(
        'expenses.html',
        expenses=expenses,
        categories=categories,
        filter_category=filter_category,
        min_amount=min_amount,
        max_amount=max_amount,
        filter_month=filter_month
    )

# Add new expense
@app.route('/expenses/add', methods=['POST'])
@login_required
def add_expense():
    category = request.form['category']
    amount = float(request.form['amount'])
    date_str = request.form['date'] 
    note = request.form.get('note', '')

    try:
        date_obj = datetime.strptime(date_str, "%d.%m.%Y").date()
    except ValueError:
        flash("Incorrect date format! Use DD.MM.YYYY", "danger")
        return redirect(url_for('get_expenses'))

    new_expense = Expense(
        user_id=current_user.id,
        category=category,
        amount=amount,
        date=date_obj,
        note=note
    )
    db.session.add(new_expense)
    db.session.commit()
    flash("Expense added successfully!", "success")
    # Gider eklendikten sonra mevcut sayfaya (get_expenses) yönlendiriyor
    return redirect(url_for('get_expenses')) 

# Delete expense
@app.route('/expenses/delete/<int:expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    if expense.user_id != current_user.id:
        flash("You are not authorized to delete this expense.", "danger")
        return redirect(url_for('get_expenses'))
    db.session.delete(expense)
    db.session.commit()
    flash("Expense deleted successfully!", "success")
    return redirect(url_for('get_expenses'))

# Edit expense
@app.route('/expenses/edit/<int:expense_id>', methods=['POST'])
@login_required
def edit_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    if expense.user_id != current_user.id:
        flash("You are not authorized to edit this expense.", "danger")
        return redirect(url_for('get_expenses'))

    expense.category = request.form['category']
    expense.amount = float(request.form['amount'])
    
    date_str = request.form['date']
    try:
        expense.date = datetime.strptime(date_str, "%d.%m.%Y").date()
    except ValueError:
        flash("Incorrect date format! Use DD.MM.YYYY", "danger")
        return redirect(url_for('get_expenses'))

    expense.note = request.form.get('note', '')
    db.session.commit()
    flash("Expense updated successfully!", "success")
    return redirect(url_for('get_expenses'))

if __name__ == '__main__':
    app.run(debug=True)