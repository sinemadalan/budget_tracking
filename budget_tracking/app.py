from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import urllib
from datetime import datetime
import calendar
from sqlalchemy import extract, func
import pandas as pd
import numpy as np

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
    return render_template('index.html', username=current_user.username)

# Logout route
@app.route('/logout', methods=['GET', 'POST']) 
@login_required
def logout():
    session.pop('_flashes', None)
    logout_user()
    flash("Successfully logged out.", "info")
    
    return redirect(url_for('login'))

from prophet import Prophet
import pandas as pd

# ==========================
# Forecast Route 
# ==========================
@app.route('/forecast')
@login_required
def forecast():
    # KULLANICININ SEÇTİĞİ TARİHİ AL (Yoksa Bugünü Al)
    selected_month_str = request.args.get('selected_month')

    if selected_month_str:
        try:
            year, month = map(int, selected_month_str.split('-'))
            current_date = datetime(year, month, 1)
        except ValueError:
            # Hata olursa bugüne dön
            today = datetime.today()
            current_date = datetime(today.year, today.month, 1)
    else:
        # Seçim yoksa varsayılan olarak bugünün tarihini al
        today = datetime.today()
        current_date = datetime(today.year, today.month, 1)

    # GELECEK AYI HESAPLA (Tahmin yapılacak hedef ay)
    if current_date.month == 12:
        next_month_date = datetime(current_date.year + 1, 1, 1)
    else:
        next_month_date = datetime(current_date.year, current_date.month + 1, 1)

    # HTML'de göstermek için isimler
    selected_month_name = current_date.strftime("%B") 
    next_month_name = next_month_date.strftime("%B")
    
    # Input alanında seçili kalsın diye value formatı
    selected_month_val = current_date.strftime("%Y-%m")

    # VERİTABANINDAN TÜM GİDERLERİ ÇEK
    expenses = (
        db.session.query(Expense)
        .filter_by(user_id=current_user.id)
        .order_by(Expense.date)
        .all()
    )

    # Veri yoksa boş döndür
    if not expenses:
        flash("There is not enough data for the forecast!", "warning")
        return render_template('forecast.html', 
                               next_month_name=next_month_name, 
                               selected_month=selected_month_val,
                               selected_month_name=selected_month_name,
                               total_current=0,
                               total_predicted=0,
                               categories=[],
                               current_month_expenses={},
                               predicted_expenses={})

    categories = list({e.category for e in expenses})

    df = pd.DataFrame([
        {"ds": e.date, "y": e.amount, "category": e.category}
        for e in expenses
    ])
    df['ds'] = pd.to_datetime(df['ds'])

    # TAHMİN MANTIĞI 
    # Modelin overfittingini engellemek için veriyi seçilen tarihe göre kesiyoruz.
    cutoff_date = pd.Timestamp(next_month_date) 
    df_history = df[df['ds'] < cutoff_date].copy()

    predicted_expenses = {}

    # --- Kategori Bazlı Tahmin ---
    for cat in categories:
        # Sadece geçmiş veriyi (df_history) kullan
        df_cat = df_history[df_history["category"] == cat][["ds", "y"]]

        if df_cat.empty:
            predicted_expenses[cat] = 0
            continue

        # Aylık toplama (Resample)
        df_cat = df_cat.set_index('ds').resample('M').sum().dropna().reset_index()

        # Prophet için en az 2 veri noktası gerekli
        if len(df_cat) < 2:
            last_val = float(df_cat['y'].iloc[-1]) if not df_cat.empty else 0
            predicted_expenses[cat] = round(last_val, 2)
            continue

        try:
            # Log transformation (daha kararlı tahmin için)
            df_cat['y_log'] = np.log1p(df_cat['y'])
            df_prophet = df_cat[['ds', 'y_log']].rename(columns={'y_log': 'y'})

            model = Prophet(yearly_seasonality=False, daily_seasonality=False, weekly_seasonality=False)
            model.fit(df_prophet)
            
            # Gelecek 1 ayı tahmin et
            future = model.make_future_dataframe(periods=1, freq='M')
            forecast_df = model.predict(future)
            
            # Log'u geri çevir (Inverse log transform)
            next_month_estimate = np.expm1(forecast_df.iloc[-1]['yhat'])
            predicted_expenses[cat] = round(next_month_estimate, 2)
        except Exception:
            # Model hata verirse son değeri kullan
            predicted_expenses[cat] = round(float(df_cat['y'].iloc[-1]), 2)

    # --- Toplam Tahmin (Total Forecast) ---
    # Yine sadece df_history kullanıyoruz
    df_total = df_history.groupby('ds')['y'].sum().reset_index()
    df_total['ds'] = pd.to_datetime(df_total['ds'])
    df_total = df_total.set_index('ds').resample('M').sum().dropna().reset_index()

    if len(df_total) >= 2:
        try:
            df_total['y_log'] = np.log1p(df_total['y'])
            df_prophet_total = df_total[['ds', 'y_log']].rename(columns={'y_log': 'y'})

            model_total = Prophet(yearly_seasonality=False, daily_seasonality=False, weekly_seasonality=False)
            model_total.fit(df_prophet_total)
            future_total = model_total.make_future_dataframe(periods=1, freq='M')
            forecast_total = model_total.predict(future_total)
            total_predicted = round(np.expm1(forecast_total.iloc[-1]['yhat']), 2)
        except Exception:
            total_predicted = round(float(df_total['y'].iloc[-1]), 2)
    else:
        total_predicted = round(float(df_total['y'].iloc[-1]) if not df_total.empty else 0, 2)

    # MEVCUT AY HARCAMALARI (Actuals)
    # Kullanıcının SEÇTİĞİ ayın gerçek harcamalarını çekiyoruz
    current_month_expenses = {}
    for cat in categories:
        total = (
            db.session.query(func.sum(Expense.amount))
            .filter_by(user_id=current_user.id, category=cat)
            .filter(extract('year', Expense.date) == current_date.year)  # Dinamik Yıl
            .filter(extract('month', Expense.date) == current_date.month) # Dinamik Ay
            .scalar()
        )
        current_month_expenses[cat] = total or 0

    total_current = sum(current_month_expenses.values())

    return render_template(
        'forecast.html',
        username=current_user.username,
        categories=categories,
        current_month_expenses=current_month_expenses,
        total_current=total_current,
        predicted_expenses=predicted_expenses,
        total_predicted=total_predicted,
        next_month_name=next_month_name,
        selected_month=selected_month_val,      
        selected_month_name=selected_month_name 
    )

# ==========================
# Analysis & Tips Route 
# ==========================
@app.route('/analysis')
@login_required
def analysis():
    # TARİH SEÇİMİ
    selected_month_str = request.args.get('selected_month')

    if selected_month_str:
        try:
            year, month = map(int, selected_month_str.split('-'))
            current_date = datetime(year, month, 1)
        except ValueError:
            today = datetime.today()
            current_date = datetime(today.year, today.month, 1)
    else:
        today = datetime.today()
        current_date = datetime(today.year, today.month, 1)

    selected_month_name = current_date.strftime("%B")
    selected_month_val = current_date.strftime("%Y-%m")

    # SEÇİLEN AYIN VERİLERİ (KATEGORİ BAZLI)
    category_totals = (
        db.session.query(Expense.category, func.sum(Expense.amount))
        .filter_by(user_id=current_user.id)
        .filter(extract('year', Expense.date) == current_date.year)
        .filter(extract('month', Expense.date) == current_date.month)
        .group_by(Expense.category)
        .all()
    )
    
    # Veriyi dictionary formatına çevir: {'Food': 500, 'Rent': 2000}
    data = {cat: amount for cat, amount in category_totals}
    total_spent = sum(data.values())

    # --- GEÇEN AY İLE KIYASLAMA (Comparison) ---
    # Önceki ayın tarihini hesapla
    if current_date.month == 1:
        prev_month_date = datetime(current_date.year - 1, 12, 1)
    else:
        prev_month_date = datetime(current_date.year, current_date.month - 1, 1)
    
    # Önceki ayın toplam harcamasını çek
    prev_total = (
        db.session.query(func.sum(Expense.amount))
        .filter_by(user_id=current_user.id)
        .filter(extract('year', Expense.date) == prev_month_date.year)
        .filter(extract('month', Expense.date) == prev_month_date.month)
        .scalar()
    ) or 0

    # Kıyaslama Mantığı
    comparison_text = "No previous data"
    comparison_class = "text-muted"
    comparison_icon = "fa-minus"

    if prev_total > 0:
        diff = total_spent - prev_total
        percent = (diff / prev_total) * 100
        
        if diff > 0:
            # Harcama artmış (Kötü)
            comparison_text = f"+{round(percent)}% vs last month"
            comparison_class = "text-danger" 
            comparison_icon = "fa-arrow-up"
        else:
            # Harcama azalmış (İyi)
            comparison_text = f"{round(percent)}% vs last month"
            comparison_class = "text-success"
            comparison_icon = "fa-arrow-down"

    # --- GÜNLÜK HARCAMA TRENDİ (Line Chart) ---
    daily_expenses_query = (
        db.session.query(extract('day', Expense.date), func.sum(Expense.amount))
        .filter_by(user_id=current_user.id)
        .filter(extract('year', Expense.date) == current_date.year)
        .filter(extract('month', Expense.date) == current_date.month)
        .group_by(extract('day', Expense.date))
        .all()
    )
    
    # Ayın kaç çektiğini bul (28, 30, 31?)
    days_in_month = calendar.monthrange(current_date.year, current_date.month)[1]
    
    # Sorgudan gelen veriyi sözlüğe çevir: {1: 100, 5: 250...}
    daily_dict = {int(day): amt for day, amt in daily_expenses_query}
    
    # 1'den ay sonuna kadar tüm günleri doldur (Harcama yoksa 0)
    daily_labels = [str(i) for i in range(1, days_in_month + 1)]
    daily_values = [daily_dict.get(i, 0) for i in range(1, days_in_month + 1)]

    # İPUÇLARI OLUŞTURMA
    tips = []
    highest_category = "-"
    highest_amount = 0
    potential_savings = 0

    if data:
        highest_category = max(data, key=data.get)
        highest_amount = data[highest_category]

        # Tip 1: En yüksek harcama
        tips.append({
            'icon': 'fa-exclamation-triangle',
            'color': '#e74c3c', # Kırmızı
            'title': 'Highest Spending Alert',
            'desc': f"Your highest spending is in <strong>{highest_category}</strong> with <strong>{highest_amount}₺</strong>."
        })

        # Tip 2: Tasarruf Planı (En yüksek 2 kalemi %10 kısma)
        sorted_cats = sorted(data.items(), key=lambda item: item[1], reverse=True)
        top_2_savings = 0
        for i in range(min(2, len(sorted_cats))):
            top_2_savings += sorted_cats[i][1] * 0.10
        
        potential_savings = top_2_savings

        tips.append({
            'icon': 'fa-piggy-bank',
            'color': '#27ae60', # Yeşil
            'title': 'Smart Saving Opportunity',
            'desc': f"If you reduce your top 2 expenses by just 10%, you could save approximately <strong>{round(top_2_savings)}₺</strong>."
        })
        
        # Tip 3: Günlük Ortalama
        daily_avg = total_spent / days_in_month
        tips.append({
            'icon': 'fa-chart-area',
            'color': '#2980b9', # Mavi
            'title': 'Daily Average',
            'desc': f"You are spending an average of <strong>{round(daily_avg)}₺</strong> per day in {selected_month_name}."
        })

    else:
        tips.append({
            'icon': 'fa-info-circle',
            'color': '#7f8c8d',
            'title': 'No Data',
            'desc': f"No expenses found for {selected_month_name}."
        })

    return render_template(
        'analysis.html',
        username=current_user.username,
        selected_month=selected_month_val,
        selected_month_name=selected_month_name,
        
        # Genel İstatistikler
        total_spent=total_spent,
        highest_category=highest_category,
        highest_amount=highest_amount,
        potential_savings=round(potential_savings),
        
        # Özellik 2: Kıyaslama Verileri
        comparison_text=comparison_text,
        comparison_class=comparison_class,
        comparison_icon=comparison_icon,
        
        # Özellik 1: Günlük Grafik Verileri
        daily_labels=daily_labels,
        daily_values=daily_values,
        
        # Özellik 3 & Pasta Grafik: Kategori Verileri
        category_data=data, # Progress barlar için sözlük
        chart_labels=list(data.keys()), # Pasta grafik etiketleri
        chart_values=list(data.values()), # Pasta grafik değerleri
        
        # İpuçları
        tips=tips
    )

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