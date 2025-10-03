import os
import secrets
import requests
import json
import re
import hashlib
import uuid
import csv
from datetime import datetime
from flask import Flask, redirect, request, session, url_for, jsonify, send_from_directory

app = Flask(__name__)

# Supabase Configuration
SUPABASE_URL = "https://gqitmhktxmktggjlkjfa.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inh4Ynd6b2l4Z3hna2pnbWlncWhjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTkzOTQzNTUsImV4cCI6MjA3NDk3MDM1NX0.kWHi5lKpDxtZXFslHCGJ53jVDkc5e-0IKKHhJAswDDE"

# Flask Secret Key
app.secret_key = "4f7d9a2b8c1e6f3a9d2b5c8e1f7a3d6b9c2e5f8a1d4b7c0e3f6a9d2b5c8e1f7a"

GOOGLE_CLIENT_ID = "268307319121-04ckk21lv76bs27dgc6mokpdbh78apcp.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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" 
# ==============================================================

# Инициализируем Supabase только если переменные установлены
supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client, Client
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase Database: CONNECTED")
    except Exception as e:
        print(f"⚠️ Supabase connection failed: {e}")
        supabase = None
else:
    print("⚠️ Supabase: NOT CONFIGURED - running in demo mode")

# Константы для поиска
BASE_CSV = 'base.csv'
RATE_LIMITS = {}
MAX_REQUESTS_PER_MINUTE = 10
MAX_REQUESTS_PER_HOUR = 100

# Функции для поиска из onion.py
def sanitize_input(text):
    if not text:
        return ""
    text = str(text)
    text = re.sub(r'[<>"\']', '', text)
    text = re.sub(r'[\x00-\x1F\x7F]', '', text)
    text = text.strip()
    return text[:500]

def validate_phone_manual(phone_str):
    cleaned = re.sub(r'[^\d+]', '', phone_str)
    if not cleaned:
        return None, None, None
    patterns = [
        (r'^\+7(\d{10})$', '+7 {} {} {}', [0, 3, 3, 4]),
        (r'^8(\d{10})$', '+7 {} {} {}', [0, 3, 3, 4]),
        (r'^(\d{10})$', '+7 {} {} {}', [0, 3, 3, 4]),
    ]
    for pattern, format_template, groups in patterns:
        match = re.match(pattern, cleaned)
        if match:
            formatted = format_template.format(*[match.group(i) for i in groups if i < len(match.groups()) + 1])
            digits_only = re.sub(r'\D', '', formatted)
            if len(digits_only) not in [10, 11, 12]:
                continue
            russian_operators = {
                '79': 'МТС', '91': 'МТС', '98': 'МТС',
                '90': 'Билайн', '96': 'Билайн',
                '92': 'МегаФон', '93': 'МегаФон', '95': 'МегаФон',
                '98': 'ЮТел', '99': 'ЮТел',
            }
            operator = "Неизвестный оператор"
            for prefix, op in russian_operators.items():
                if digits_only.startswith(prefix):
                    operator = op
                    break
            return formatted, operator, "Россия"
    return None, None, None

def extract_phones_from_text(text):
    found_phones = []
    if not text or not isinstance(text, str):
        return found_phones
    
    try:
        text = text.encode().decode('unicode_escape')
    except:
        pass
    
    patterns = [
        r'\+7\s?[\(\-]?\d{3}[\)\-]?\s?\d{3}[\-]?\d{2}[\-]?\d{2}', 
        r'8\s?[\(\-]?\d{3}[\)\-]?\s?\d{3}[\-]?\d{2}[\-]?\d{2}',   
        r'\b\d{3}[\-]\d{3}[\-]\d{2}[\-]\d{2}\b',                  
        r'\b\d{11,15}\b',                                        
    ]
    
    for pattern in patterns:
        matches = re.finditer(pattern, text)
        for match in matches:
            phone_str = match.group()
            formatted, operator, region = validate_phone_manual(phone_str)
            if formatted:
                phone_info = {
                    'number': formatted,
                    'operator': operator,
                    'region': region,
                    'original': phone_str
                }
                if not any(p['number'] == formatted for p in found_phones):
                    found_phones.append(phone_info)
    
    return found_phones

def create_search_report(query, data, search_type="phone"):
    names_data = []
    phones_data = []
    emails_data = []
    formatted_results = []
    
    def safe_sanitize(text):
        """Безопасная sanitize функция для обработки Unicode суррогатов"""
        if not text:
            return ""
        try:
            text = str(text)
            # Обрабатываем Unicode суррогаты
            text = text.encode('utf-8', 'ignore').decode('utf-8')
            text = re.sub(r'[<>"\']', '', text)
            text = re.sub(r'[\x00-\x1F\x7F]', '', text)
            text = text.strip()
            return text[:500]
        except Exception as e:
            return f"Ошибка обработки данных: {str(e)}"

    try:
        # Декодируем Unicode escape последовательности безопасно
        if isinstance(data, str):
            try:
                # Пробуем декодировать Unicode escapes
                data = data.encode('latin-1').decode('unicode_escape')
            except:
                try:
                    data = data.encode('utf-8', 'ignore').decode('utf-8')
                except:
                    pass
        
        json_data = json.loads(data) if isinstance(data, str) else data
        
        if 'results' in json_data and json_data['results']:
            for result in json_data['results']:
                # Обрабатываем каждый результат
                source = "Неизвестный источник"
                formatted_result = '<div class="database-block">'
                
                # Находим источник
                for key, value in result.items():
                    key_str = safe_sanitize(key)
                    if '🏫' in key_str or 'Источник' in key_str:
                        source = safe_sanitize(value)
                        break
                
                formatted_result += f'<div class="database-header">📊 База: <span class="source-highlight">{source}</span></div>'
                
                # Обрабатываем все поля
                items_list = list(result.items())
                for j, (key, value) in enumerate(items_list):
                    key_str = safe_sanitize(key)
                    if '🏫' in key_str or 'Источник' in key_str:
                        continue  # Пропускаем поле источника, т.к. уже обработали
                    
                    value_str = safe_sanitize(value) if value is not None else ""
                    
                    if value_str:
                        prefix = "├" if j < len(items_list) - 2 else "└"
                        formatted_result += f'<div class="data-line">{prefix} <span class="key">{key_str}:</span> <span class="value">{value_str}</span></div>'
                        
                        # Собираем имена
                        name_fields = ['👤Фамилия', '👤Имя', '👤Отчество', '👤ФИО', '🔸Никнейм', 'Фамилия', 'Имя', 'Отчество', 'ФИО', 'Никнейм']
                        if any(field in key_str for field in name_fields) and value_str:
                            names_data.append(f"{key_str}: {value_str}")
                        
                        # Ищем телефоны в значениях
                        found_phones = extract_phones_from_text(value_str)
                        for phone_info in found_phones:
                            if not any(p['number'] == phone_info['number'] for p in phones_data):
                                if len(phones_data) < 5:
                                    phones_data.append(phone_info)
                        
                        # Ищем email в значениях
                        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
                        emails_found = re.findall(email_pattern, value_str)
                        for email in emails_found:
                            if len(emails_data) < 5 and email not in [e.split(': ')[-1] for e in emails_data]:
                                emails_data.append(f"{key_str}: {email}")
                
                formatted_result += '</div>'
                formatted_results.append(formatted_result)
                
    except Exception as e:
        # Если не удалось распарсить JSON, показываем raw данные в читаемом формате
        raw_data = str(data)
        try:
            # Безопасно обрабатываем raw данные
            raw_data = raw_data.encode('utf-8', 'ignore').decode('utf-8')
        except:
            pass
        
        # Разбиваем raw данные на отдельные строки для лучшего отображения
        lines = raw_data.split(',')
        formatted_result = '<div class="database-block"><div class="database-header">📊 Raw данные</div>'
        
        for i, line in enumerate(lines):
            line = safe_sanitize(line.strip(' {}[]"'))
            if line:
                prefix = "├" if i < len(lines) - 1 else "└"
                formatted_result += f'<div class="data-line">{prefix} <span class="value">{line}</span></div>'
        
        formatted_result += '</div>'
        formatted_results = [formatted_result]

    # Определяем заголовок и иконку в зависимости от типа поиска
    search_type_titles = {
        "phone": (f"Результаты поиска по номеру {safe_sanitize(query)}", "📱"),
        "email": (f"Результаты поиска по почте {safe_sanitize(query)}", "✉️"),
        "vk": (f"Результаты поиска по Вконтакте {safe_sanitize(query)}", "🔵"),
        "ok": (f"Результаты поиска по Одноклассникам {safe_sanitize(query)}", "🟠"),
        "fc": (f"Результаты поиска по Facebook {safe_sanitize(query)}", "🔷"),
        "inn": (f"Результаты поиска по ИНН {safe_sanitize(query)}", "🔢"),
        "snils": (f"Результаты поиска по СНИЛС {safe_sanitize(query)}", "🆔"),
        "nick": (f"Результаты поиска по нику {safe_sanitize(query)}", "🔸"),
        "ogrn": (f"Результаты поиска по ОГРН {safe_sanitize(query)}", "📊")
    }
    
    title, icon = search_type_titles.get(search_type, (f"Результаты поиска {safe_sanitize(query)}", "🔍"))

    # Генерируем HTML отчет с безопасной кодировкой
    html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OSINT Report - {safe_sanitize(query)}</title>
    <link rel="stylesheet" href="/onion.css">
    <script src="https://cdn.jsdelivr.net/npm/particles.js@2.0.0/particles.min.js"></script>
    <style>
        .report-container {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: transparent;
            z-index: 1000;
            overflow-y: auto;
        }}
        .report-header {{
            background: linear-gradient(145deg, rgba(40, 40, 40, 0.95), rgba(25, 25, 25, 0.98));
            padding: 20px;
            border-bottom: 1px solid rgba(0, 255, 136, 0.2);
            backdrop-filter: blur(10px);
            margin: 20px;
            border-radius: 16px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }}
        .report-content {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 20px;
        }}
        .back-btn {{
            background: linear-gradient(135deg, #00ff88, #00cc6a);
            color: #000;
            border: none;
            padding: 12px 24px;
            border-radius: 12px;
            font-weight: 600;
            cursor: pointer;
            margin-bottom: 20px;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .back-btn:hover {{
            background: linear-gradient(135deg, #00cc6a, #00aa55);
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0, 255, 136, 0.4);
        }}
        .stats-grid {{
            grid-column: 1 / -1;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }}
        .stat-card {{
            background: linear-gradient(145deg, rgba(40, 40, 40, 0.8), rgba(25, 25, 25, 0.9));
            border-radius: 16px;
            padding: 20px;
            text-align: center;
            border: 1px solid rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            transition: all 0.4s cubic-bezier(0.25, 0.46, 0.45, 0.94);
        }}
        .stat-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 15px 40px rgba(0, 255, 136, 0.2), 0 0 0 1px rgba(0, 255, 136, 0.1);
        }}
        .stat-number {{
            font-size: 2em;
            font-weight: 700;
            background: linear-gradient(135deg, #00ff88, #00cc6a);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin: 10px 0;
        }}
        .main-data-section {{
            background: linear-gradient(145deg, rgba(40, 40, 40, 0.8), rgba(25, 25, 25, 0.9));
            border-radius: 16px;
            padding: 20px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            margin-bottom: 20px;
            transition: all 0.4s cubic-bezier(0.25, 0.46, 0.45, 0.94);
            max-height: 600px;
            overflow-y: auto;
        }}
        .main-data-section:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        }}
        .database-block {{
            background: linear-gradient(145deg, rgba(50, 50, 50, 0.6), rgba(35, 35, 35, 0.7));
            border-radius: 12px;
            padding: 15px;
            border: 1px solid rgba(255, 255, 255, 0.05);
            margin-bottom: 15px;
        }}
        .database-header {{
            color: #00ff88;
            font-size: 1.1em;
            font-weight: 600;
            margin-bottom: 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            padding-bottom: 8px;
        }}
        .source-highlight {{
            color: #ff6b6b;
            font-weight: 600;
        }}
        .data-line {{
            margin: 6px 0;
            font-family: 'Courier New', monospace;
            font-size: 0.85em;
            line-height: 1.4;
            color: #ffffff;
            padding: 4px;
            border-radius: 4px;
            transition: background-color 0.3s ease;
        }}
        .data-line:hover {{
            background: rgba(255, 255, 255, 0.05);
        }}
        .key {{
            color: #00ff88;
            font-weight: 500;
        }}
        .value {{
            color: #ffffff;
        }}
        .phone-operator {{
            color: #00ff88;
            font-size: 0.8em;
            margin-left: 10px;
            background: rgba(0, 255, 136, 0.1);
            padding: 2px 6px;
            border-radius: 4px;
        }}
        .phone-region {{
            color: #0088ff;
            font-size: 0.8em;
            margin-left: 5px;
            background: rgba(0, 136, 255, 0.1);
            padding: 2px 6px;
            border-radius: 4px;
        }}
        .no-data {{
            text-align: center;
            color: rgba(255, 255, 255, 0.5);
            font-style: italic;
            padding: 20px;
        }}
        .data-section {{
            background: linear-gradient(145deg, rgba(40, 40, 40, 0.8), rgba(25, 25, 25, 0.9));
            border-radius: 16px;
            padding: 20px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            margin-bottom: 20px;
            transition: all 0.4s cubic-bezier(0.25, 0.46, 0.45, 0.94);
        }}
        .data-section:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        }}
        .section-title {{
            color: #00ff88;
            font-size: 1.2em;
            font-weight: 600;
            margin-bottom: 15px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            padding-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        @media (max-width: 1024px) {{
            .report-content {{
                grid-template-columns: 1fr;
            }}
            .main-data-section {{
                width: 100%;
                max-width: 100%;
            }}
        }}
    </style>
</head>
<body>
    <div id="particles-js"></div>
    
    <div class="report-container">
        <div class="report-header">
            <button class="back-btn" onclick="closeReport()">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                    <path d="M11.354 1.646a.5.5 0 0 1 0 .708L5.707 8l5.647 5.646a.5.5 0 0 1-.708.708l-6-6a.5.5 0 0 1 0-.708l6-6a.5.5 0 0 1 .708 0z"/>
                </svg>
                Назад к поиску
            </button>
            <h1 style="color: #00ff88; margin: 0; display: flex; align-items: center; gap: 10px;">
                <span>{icon}</span>
                <span>{title}</span>
            </h1>
        </div>
        
        <div class="report-content">
            <div class="stats-grid">
                <div class="stat-card">
                    <div style="color: rgba(255, 255, 255, 0.7);">📊 Всего записей</div>
                    <div class="stat-number" id="totalCount">{len(formatted_results)}</div>
                </div>
                <div class="stat-card">
                    <div style="color: rgba(255, 255, 255, 0.7);">👤 Имен</div>
                    <div class="stat-number" id="nameCount">{len(names_data)}</div>
                </div>
                <div class="stat-card">
                    <div style="color: rgba(255, 255, 255, 0.7);">📱 Телефонов</div>
                    <div class="stat-number" id="phoneCount">{len(phones_data)}</div>
                </div>
                <div class="stat-card">
                    <div style="color: rgba(255, 255, 255, 0.7);">📨 Email</div>
                    <div class="stat-number" id="emailCount">{len(emails_data)}</div>
                </div>
            </div>
            
            <div class="left-column">
                <div class="main-data-section">
                    <div class="section-title">
                        <span>📋</span>
                        <span>Основные данные</span>
                    </div>
                    {"".join(formatted_results) if formatted_results else '<div class="no-data">Данные не найдены</div>'}
                </div>
            </div>
            
            <div class="right-column">
                <div class="data-section">
                    <div class="section-title">
                        <span>ℹ️</span>
                        <span>Информация</span>
                    </div>
                    <div class="data-line">• Запрос: {safe_sanitize(query)}</div>
                    <div class="data-line">• Время: <span id="currentTime"></span></div>
                    <div class="data-line">• Найдено баз: {len(formatted_results)}</div>
                </div>
                
                <div class="data-section">
                    <div class="section-title">
                        <span>📞</span>
                        <span>Телефоны</span>
                    </div>
                    {"".join([f'<div class="data-line">• {phone["number"]}<span class="phone-operator">{phone["operator"]}</span><span class="phone-region">{phone["region"]}</span></div>' for phone in phones_data]) if phones_data else '<div class="no-data">Телефоны не найдены</div>'}
                </div>
                
                <div class="data-section">
                    <div class="section-title">
                        <span>📧</span>
                        <span>Email адреса</span>
                    </div>
                    {"".join([f'<div class="data-line">• {email}</div>' for email in emails_data[:5]]) if emails_data else '<div class="no-data">Email не найдены</div>'}
                </div>
            </div>
        </div>
    </div>

    <script>
        particlesJS('particles-js', {{
            particles: {{
                number: {{ value: 80, density: {{ enable: true, value_area: 800 }} }},
                color: {{ value: '#00ff88' }},
                opacity: {{ value: 0.5, random: true, anim: {{ enable: true, speed: 1 }} }},
                size: {{ value: 3, random: true, anim: {{ enable: true, speed: 2 }} }},
                line_linked: {{ enable: true, distance: 150, color: '#00ff88', opacity: 0.2, width: 1 }},
                move: {{ enable: true, speed: 1, direction: 'none', random: true }}
            }},
            interactivity: {{
                detect_on: 'canvas',
                events: {{ onhover: {{ enable: true, mode: 'grab' }}, onclick: {{ enable: true, mode: 'push' }} }},
                modes: {{ grab: {{ distance: 200, line_linked: {{ opacity: 0.3 }} }}, push: {{ particles_nb: 4 }} }}
            }},
            retina_detect: true
        }});
        
        document.getElementById('currentTime').textContent = new Date().toLocaleString();
        
        function closeReport() {{
            window.close();
        }}
        
        function animateCounter(elementId, finalValue, duration = 1000) {{
            let element = document.getElementById(elementId);
            let start = 0;
            let increment = finalValue / (duration / 16);
            let current = 0;
            
            function update() {{
                current += increment;
                if (current < finalValue) {{
                    element.textContent = Math.floor(current);
                    requestAnimationFrame(update);
                }} else {{
                    element.textContent = finalValue;
                }}
            }}
            update();
        }}
        
        setTimeout(() => {{
            animateCounter('totalCount', {len(formatted_results)});
            animateCounter('nameCount', {len(names_data)});
            animateCounter('phoneCount', {len(phones_data)});
            animateCounter('emailCount', {len(emails_data)});
        }}, 500);
        
        document.addEventListener('DOMContentLoaded', function() {{
            const elements = document.querySelectorAll('.database-block, .data-section, .stat-card, .main-data-section');
            elements.forEach((element, index) => {{
                element.style.opacity = '0';
                element.style.transform = 'translateY(20px)';
                setTimeout(() => {{
                    element.style.transition = 'all 0.5s ease';
                    element.style.opacity = '1';
                    element.style.transform = 'translateY(0)';
                }}, index * 100);
            }});
        }});
    </script>
</body>
</html>"""
    
    # Безопасно кодируем ответ
    try:
        return html_content.encode('utf-8')
    except Exception as e:
        # Если все еще есть ошибки, возвращаем простой текст
        error_html = f"<html><body><h1>Ошибка отображения данных</h1><p>{str(e)}</p></body></html>"
        return error_html.encode('utf-8')

def rate_limit(func):
    import time
    def wrapper(*args, **kwargs):
        ip = request.remote_addr
        now = time.time()
        
        if ip not in RATE_LIMITS:
            RATE_LIMITS[ip] = {'minute': [], 'hour': []}
        
        minute_requests = [req_time for req_time in RATE_LIMITS[ip]['minute'] if now - req_time < 60]
        hour_requests = [req_time for req_time in RATE_LIMITS[ip]['hour'] if now - req_time < 3600]
        
        if len(minute_requests) >= MAX_REQUESTS_PER_MINUTE:
            return jsonify({'error': 'Слишком много запросов. Попробуйте позже.'}), 429
        if len(hour_requests) >= MAX_REQUESTS_PER_HOUR:
            return jsonify({'error': 'Превышен лимит запросов в час.'}), 429
        
        RATE_LIMITS[ip]['minute'].append(now)
        RATE_LIMITS[ip]['hour'].append(now)
        
        return func(*args, **kwargs)
    return wrapper

def get_google_auth_url(state):
    # Всегда используем Render домен для продакшена
    redirect_uri = "https://onion-web-root.onrender.com/auth/google/callback"
    
    base_url = "https://accounts.google.com/o/oauth2/v2/auth"
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": redirect_uri,
        "state": state,
        "access_type": "offline",
        "prompt": "consent"
    }
    
    param_string = "&".join([f"{k}={v}" for k, v in params.items()])
    return f"{base_url}?{param_string}"
    
def save_user_to_supabase(user_info, request):
    try:
        user_ip = request.remote_addr
        user_agent = request.headers.get('User-Agent', '')
        
        response = supabase.table('users').select('*').eq('google_id', user_info['sub']).execute()
        
        user_data = {
            'google_id': user_info['sub'],
            'email': user_info['email'],
            'name': user_info.get('name', ''),
            'avatar_url': user_info.get('picture', ''),
            'last_login': datetime.now().isoformat(),
            'last_ip': user_ip,
            'last_user_agent': user_agent
        }
        
        if response.data:
            user_data['login_count'] = response.data[0].get('login_count', 0) + 1
            result = supabase.table('users').update(user_data).eq('google_id', user_info['sub']).execute()
        else:
            user_data['login_count'] = 1
            user_data['registration_ip'] = user_ip
            user_data['registration_user_agent'] = user_agent
            result = supabase.table('users').insert(user_data).execute()
        
        return result.data[0] if result.data else None
        
    except Exception as e:
        print(f"Error saving user to Supabase: {e}")
        return None

@app.route('/')
def index():
    try:
        return send_from_directory('.', 'onion.html')
    except:
        return "Главная страница не найдена", 404

@app.route('/onion.css')
def serve_css():
    try:
        return send_from_directory('.', 'onion.css')
    except:
        return "CSS файл не найден", 404

@app.route('/onion.js')
def serve_js():
    try:
        return send_from_directory('.', 'onion.js')
    except:
        return "JS файл не найден", 404

@app.route('/auth/google')
def google_auth():
    state = secrets.token_urlsafe(16)
    session['oauth_state'] = state
    session['next_url'] = request.args.get('next', '/dashboard')
    
    auth_url = get_google_auth_url(state)
    return redirect(auth_url)

@app.route('/auth/google/callback')
def google_callback():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return "Google OAuth не настроен", 500
        
    if request.args.get('state') != session.get('oauth_state'):
        return "Неверный state параметр", 400
    
    code = request.args.get('code')
    if not code:
        return "Authorization code не получен", 400
    
    try:
        # Всегда используем Render домен
        redirect_uri = "https://onion-web-root.onrender.com/auth/google/callback"
        
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri
        }
        
        token_response = requests.post(token_url, data=token_data)
        if token_response.status_code != 200:
            return f"Ошибка получения токена: {token_response.text}", 400
        
        tokens = token_response.json()
        access_token = tokens.get('access_token')
        
        if not access_token:
            return "Access token не получен", 400
        
        userinfo_url = "https://www.googleapis.com/oauth2/v3/userinfo"
        userinfo_response = requests.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        if userinfo_response.status_code != 200:
            return "Ошибка получения данных пользователя", 400
        
        user_info = userinfo_response.json()
        
        db_user = save_user_to_supabase(user_info, request)
        
        if not db_user:
            return "Ошибка сохранения пользователя", 500
        
        session['user'] = {
            'id': db_user['id'],
            'google_id': db_user['google_id'],
            'email': db_user['email'],
            'name': db_user.get('name', ''),
            'picture': db_user.get('avatar_url', ''),
            'login_count': db_user.get('login_count', 1),
            'login_time': datetime.now().isoformat(),
            'ip': db_user.get('last_ip', ''),
            'user_agent': db_user.get('last_user_agent', '')[:100]
        }
        
        session['license_accepted'] = True
        
        session.pop('oauth_state', None)
        next_url = session.get('next_url', '/dashboard')
        return redirect(next_url)
        
    except Exception as e:
        return f"Внутренняя ошибка сервера: {str(e)}", 500

@app.route('/license')
def license():
    if 'user' not in session:
        return redirect('/')
    
    return '''
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Лицензионное соглашение</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                background: linear-gradient(135deg, #0c0c0c, #1a1a2e);
                color: #fff;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }
            .license-container {
                background: rgba(255, 255, 255, 0.05);
                backdrop-filter: blur(20px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 20px;
                padding: 40px;
                max-width: 800px;
                max-height: 80vh;
                overflow-y: auto;
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            }
            .license-header {
                text-align: center;
                margin-bottom: 30px;
                background: linear-gradient(45deg, #00ff88, #00ccff);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
            }
            .license-text {
                line-height: 1.6;
                margin-bottom: 30px;
                max-height: 400px;
                overflow-y: auto;
                padding: 20px;
                background: rgba(0, 0, 0, 0.3);
                border-radius: 10px;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            .license-actions {
                display: flex;
                gap: 15px;
                justify-content: center;
            }
            .accept-btn, .decline-btn {
                padding: 15px 30px;
                border: none;
                border-radius: 10px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s ease;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            .accept-btn {
                background: linear-gradient(45deg, #00ff88, #00ccff);
                color: #000;
            }
            .accept-btn:hover {
                transform: translateY(-2px);
                box-shadow: 0 10px 25px rgba(0, 255, 136, 0.3);
            }
            .decline-btn {
                background: linear-gradient(45deg, #ff4444, #ff0066);
                color: #fff;
            }
            .decline-btn:hover {
                transform: translateY(-2px);
                box-shadow: 0 10px 25px rgba(255, 0, 102, 0.3);
            }
        </style>
    </head>
    <body>
        <div class="license-container">
            <div class="license-header">
                <h1>🔐 ЛИЦЕНЗИОННОЕ СОГЛАШЕНИЕ</h1>
            </div>
            
            <div class="license-text">
                <p><strong>Настоящее Лицензионное Соглашение с Конечным Пользователем («Соглашение») представляет собой юридически обязывающий договор между вами («Пользователь», «Вы») и Web Security Research («Правообладатель», «Мы») относительно доступа и использования веб-сайта web.onion.xss, его контента, инструментов, программного обеспечения, данных, аналитики и услуг (совместно именуемые «Сервис»).</strong></p>

                <p><strong>ВНИМАТЕЛЬНО ПРОЧИТАЙТЕ УСЛОВИЯ ДАННОГО СОГЛАШЕНИЕ. ЛЮБОЙ ДОСТУП ИЛИ ИСПОЛЬЗОВАНИЕ СЕРВИСА ПОДТВЕРЖДАЕТ, ЧТО ВЫ ПРОЧЛИ, ПОНЯЛИ И БЕЗОГОВОРОЧНО ПРИНИМАЕТЕ ВСЕ УСЛОВИЯ НАСТОЯЩЕГО СОГЛАШЕНИЯ. ЕСЛИ ВЫ НЕ СОГЛАСНЫ С КАКИМИ-ЛИБО УСЛОВИЯМИ, ВАМ ЗАПРЕЩЕНО ДОСТУПАТЬ К СЕРВИСУ ИЛИ ИСПОЛЬЗОВАТЬ ЕГО.</strong></p>

                <h3>1. ПРЕДМЕТ СОГЛАШЕНИЯ И ПРЕДОСТАВЛЯЕМАЯ ЛИЦЕНЗИЯ</h3>
                <p>1.1. Правообладатель предоставляет Пользователю ограниченную, непередаваемую, непредусматривающую сублицензирование, отзывную, неисключительную лицензию на доступ к Сервису исключительно для вашего личного и внутреннего коммерческого использования в соответствии с условиями настоящего Соглашения.</p>

                <h3>2. ОГРАНИЧЕНИЯ И ЗАПРЕТЫ</h3>
                <p>Пользователю в строгом порядке ЗАПРЕЩАЕТСЯ:</p>
                <ul>
                    <li>2.1. Копировать, модифицировать, создавать производные работы, декомпилировать, дизассемблировать, осуществлять обратный инжиниринг или пытаться извлечь исходный код любого программного компонента Сервиса.</li>
                    <li>2.2. Перепродавать, лицензировать, сдавать в аренду, передавать, распространять, публично демонстрировать или иным образом коммерчески использовать Сервис или любой его Контент без явного письменного согласия Правообладателя.</li>
                </ul>

                <h3>3. ОТКАЗ ОТ ГАРАНТИЙ</h3>
                <p>СЕРВИС И ВЕСЬ ЕГО КОНТЕНТ ПРЕДОСТАВЛЯЮТСЯ НА УСЛОВИЯХ «КАК ЕСТЬ» И «КАК ДОСТУПНО». ПРАВООБЛАДАТЕЛЬ В ПОЛНОЙ МЕРЕ, РАЗРЕШЕННОЙ ЗАКОНОМ, ОТКАЗЫВАЕТСЯ ОТ ЛЮБЫХ ГАРАНТИЙ, ЯВНЫХ ИЛИ ПОДРАЗУМЕВАЕМЫХ.</p>

                <p style="text-align: center; margin-top: 20px; font-weight: bold;">Дата последнего обновления: ''' + datetime.now().strftime('%d.%m.%Y') + '''</p>
            </div>
            
            <div class="license-actions">
                <button class="decline-btn" onclick="declineLicense()">ОТКЛОНИТЬ</button>
                <button class="accept-btn" onclick="acceptLicense()">ПРИНЯТЬ</button>
            </div>
        </div>

        <script>
            function acceptLicense() {
                fetch('/accept-license', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    }
                }).then(() => {
                    window.location.href = '/dashboard';
                });
            }

            function declineLicense() {
                window.location.href = '/logout';
            }
        </script>
    </body>
    </html>
    '''

@app.route('/accept-license', methods=['POST'])
def accept_license():
    session['license_accepted'] = True
    return jsonify({'status': 'accepted'})

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect('/')
    
    if not session.get('license_accepted'):
        return redirect('/license')
    
    user = session['user']
    
    dashboard_html = f'''
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Web Security Search</title>
        <link rel="stylesheet" href="/onion.css">
        <script src="https://cdn.jsdelivr.net/particles.js/2.0.0/particles.min.js"></script>
    </head>
    <body>
        <div id="particles-js"></div>
        
        <div class="container">
            <div class="header" style="opacity: 0; transform: translateY(-20px); transition: all 0.5s ease;">
                <img src="{user.get('picture', '')}" alt="Avatar" class="user-avatar" onerror="this.src='data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNDAiIGhlaWdodD0iNDAiIHZpZXdCb3g9IjAgMCA0MCA0MCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPGNpcmNsZSBjeD0iMjAiIGN5PSIyMCIgcj0iMjAiIGZpbGw9IiMwMGZmODgiLz4KPHN2ZyB4PSIxMCIgeT0iMTAiIHdpZHRoPSIyMCIgaGVpZ2h0PSIyMCIgdmlld0JveD0iMCAwIDIwIDIwIiBmaWxsPSJub25lIj4KPHBhdGggZD0iTTEwIDBDMTEuMzIzIDAgMTIuNjQ2IDAgMTMuOTY5IDBDMTMuOTY5IDMuMjU0IDEzLjk2OSA2LjUwOCAxMy45NjkgOS43NjJDMTYuMTU0IDkuNzYyIDE4LjMzOSA5Ljc2MiAyMC41MjQgOS43NjJDMjAuNTI0IDExLjA4NSAyMC41MjQgMTIuNDA4IDIwLjUyNCAxMy43MzFDMTguMzM5IDEzLjczMSAxNi4xNTQgMTMuNzMxIDEzLjk2OSAxMy43MzFDMTMuOTY5IDE2LjkxNSAxMy45NjkgMjAuMSAxMy45NjkgMjMuMjg0QzEyLjY0NiAyMy4yODQgMTEuMzIzIDIzLjI4NCA5Ljk5OTkgMjMuMjg0QzkuOTk5OSAyMC4xIDkuOTk5OSAxNi45MTUgOS45OTk5IDEzLjczMUM3LjgxNDkgMTMuNzMxIDUuNjI5OSAxMy43MzEgMy40NDQ5IDEzLjczMUMzLjQ0NDkgMTIuNDA4IDMuNDQ0OSAxMS4wODUgMy40NDQ5IDkuNzYyQzUuNjI5OSA5Ljc2MiA3LjgxNDkgOS43NjIgOS45OTk5IDkuNzYyQzkuOTk5OSA2LjUwOCA5Ljk5OTkgMy4yNTQgOS45OTk5IDBaIiBmaWxsPSJibGFjayIvPgo8L3N2Zz4KPC9zdmc+'">
                <div class="user-info">
                    <div class="user-name glow-text">{user.get('name', 'User')}</div>
                    <div class="user-email">{user.get('email', '')}</div>
                </div>
                <a href="/logout" class="logout-btn">ВЫЙТИ</a>
            </div>
            
            <div class="main-content">
                <div class="search-container" style="opacity: 0; transform: translateY(30px); transition: all 0.8s ease;">
                    <h1 class="search-title floating">WEB SECURITY SEARCH</h1>
                    <div class="search-box">
                        <input type="text" class="search-input" placeholder="Введите запрос для поиска уязвимостей...">
                        <button class="search-btn pulse">🔍 ПОИСК</button>
                    </div>
                </div>
            </div>
        </div>
        
        <script src="/onion.js"></script>
        <script>
            document.addEventListener('DOMContentLoaded', function() {{
                const searchInput = document.querySelector('.search-input');
                const searchBtn = document.querySelector('.search-btn');
                
                function performSearch() {{
                    const query = searchInput.value.trim();
                    if (query) {{
                        const osintKeywords = ['osint', 'докс', 'сват', 'пробив', 'os', 'osi', 'osin'];
                        const hasOsintKeyword = osintKeywords.some(keyword => 
                            query.toLowerCase().includes(keyword.toLowerCase())
                        );
                        
                        if (hasOsintKeyword) {{
                            window.location.href = '/search';
                        }} else {{
                            searchBtn.innerHTML = '🔍 ПОИСК...';
                            searchBtn.style.background = 'linear-gradient(45deg, #ff00cc, #ff4444)';
                            
                            setTimeout(() => {{
                                searchBtn.innerHTML = '🔍 ПОИСК';
                                searchBtn.style.background = 'linear-gradient(45deg, #00ff88, #00ccff)';
                                searchInput.style.borderColor = '#00ff88';
                                setTimeout(() => {{
                                    searchInput.style.borderColor = 'rgba(255, 255, 255, 0.1)';
                                }}, 2000);
                            }}, 1500);
                        }}
                    }}
                }}
                
                searchBtn.addEventListener('click', performSearch);
                searchInput.addEventListener('keypress', (e) => {{
                    if (e.key === 'Enter') {{
                        performSearch();
                    }}
                }});
            }});
        </script>
    </body>
    </html>
    '''
    
    return dashboard_html

@app.route('/search')
def search_page():
    if 'user' not in session:
        return redirect('/')
    
    if not session.get('license_accepted'):
        return redirect('/license')
    
    try:
        return send_from_directory('search', 'onion.html')
    except:
        return "Страница поиска не найдена", 404

@app.route('/search/<path:filename>')
def serve_search_files(filename):
    if 'user' not in session:
        return redirect('/')
    
    try:
        return send_from_directory('search', filename)
    except:
        return f"Файл {filename} не найден", 404

# Маршруты для поиска OSINT
@app.route('/search_<search_type>')
@rate_limit
def search(search_type):
    if 'user' not in session:
        return jsonify({'error': 'Требуется авторизация'}), 403
    
    query = request.args.get(search_type, '')
    if not query:
        return jsonify({'error': 'Пустой запрос'}), 400
    
    query = sanitize_input(query)
    
    try:
        if search_type == "nick":
            github_data = requests.get(f"https://api.github.com/users/{query}", timeout=10)
            social_links = {
                "ВКонтакте": f"https://vk.com/{query}",
                "GitHub": f"https://github.com/{query}",
                "Twitch": f"https://twitch.tv/{query}",
                "Steam": f"https://steamcommunity.com/id/{query}",
                "Pinterest": f"https://pinterest.com/{query}",
                "DevTo": f"https://dev.to/{query}",
                "Producthunt": f"https://www.producthunt.com/@{query}"
            }
            
            results = []
            if github_data.status_code == 200:
                github_json = github_data.json()
                results.append({
                    "🏫Источник": "GitHub",
                    "👤Логин": github_json.get('login'),
                    "🏢Компания": github_json.get('company'),
                    "📍Местоположение": github_json.get('location'),
                    "🌐Веб-сайт": github_json.get('blog'),
                    "📂Публичные репозитории": github_json.get('public_repos'),
                    "🎁Подарки": github_json.get('public_gists'),
                    "👥Подписчики": github_json.get('followers'),
                    "🔔Подписки": github_json.get('following'),
                    "📅Создан": github_json.get('created_at'),
                    "🔄Обновлен": github_json.get('updated_at'),
                    "🔧Тип": github_json.get('type'),
                    "🔗Профиль": github_json.get('html_url')
                })
            
            for platform, url in social_links.items():
                try:
                    response = requests.head(url, timeout=5, allow_redirects=True)
                    if response.status_code == 200:
                        results.append({
                            "🏫Источник": platform,
                            "👤Профиль": url,
                            "🔗Ссылка": url
                        })
                except:
                    continue
            
            response_data = {"results": results}
            
        elif search_type == "ogrn":
            ofdata_response = requests.get(
                f"https://api.ofdata.ru/v2/company?key=DiC9ALodH5T12BfR&ogrn={query}",
                timeout=10
            )
            if ofdata_response.status_code == 200:
                ofdata_json = ofdata_response.json()
                results = [{
                    "🏫Источник": "OFDATA",
                    "📊ОГРН": ofdata_json.get('data', {}).get('ОГРН'),
                    "🔢ИНН": ofdata_json.get('data', {}).get('ИНН'),
                    "🏢Наименование": ofdata_json.get('data', {}).get('НаимПолн'),
                    "📍Адрес": ofdata_json.get('data', {}).get('ЮрАдрес', {}).get('АдресРФ'),
                    "📅Дата регистрации": ofdata_json.get('data', {}).get('ДатаРег'),
                    "👤Руководитель": ofdata_json.get('data', {}).get('Руковод', [{}])[0].get('ФИО') if ofdata_json.get('data', {}).get('Руковод') else None,
                    "💼Статус": ofdata_json.get('data', {}).get('Статус', {}).get('Наим'),
                    "📞Телефоны": ", ".join(ofdata_json.get('data', {}).get('Контакты', {}).get('Тел', [])),
                    "📧Email": ", ".join(ofdata_json.get('data', {}).get('Контакты', {}).get('Емэйл', []))
                }]
                response_data = {"results": results}
            else:
                response_data = {"results": []}
        else:
            response = requests.get(
                f"https://api.depsearch.digital/quest={query}?token=30L5ZJxVhQjNnMynqSYvGND80Gj3Xx7x&lang=ru",
                timeout=10
            )
            response_data = response.json()
        
        report_html = create_search_report(query, json.dumps(response_data), search_type)
        return report_html
        
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Ошибка API: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Внутренняя ошибка: {str(e)}'}), 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

@app.route('/api/user')
def api_user():
    if 'user' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    return jsonify(session['user'])

@app.route('/admin/users')
def admin_users():
    if supabase is None:
        return jsonify({'error': 'Supabase не настроен'}), 500
        
    try:
        response = supabase.table('users').select('*').execute()
        return jsonify({
            'total_users': len(response.data),
            'users': response.data
        })
    except Exception as e:
        return f"Error: {e}", 500

@app.route('/<folder>/<path:filename>')
def serve_section_files(folder, filename):
    try:
        return send_from_directory(folder, filename)
    except:
        return f"Файл {filename} в разделе {folder} не найден", 404

if __name__ == '__main__':
    print("=" * 60)
    print("🚀 Web Security Research Server")
    
    if supabase:
        print("✅ Supabase Database: CONNECTED")
    else:
        print("⚠️ Supabase: DEMO MODE")
    
    if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
        print("✅ Google OAuth: CONFIGURED")
    else:
        print("⚠️ Google OAuth: NOT CONFIGURED")
    
    print("✅ OSINT Search: INTEGRATED")
    print("🌐 Сервер запущен...")
    print("=" * 60)
    
    # Получаем порт из переменной окружения Render, или используем 5000 по умолчанию
    port = int(os.environ.get('PORT', 5000))
    
    # Запускаем на всех интерфейсах (0.0.0.0) для Render
    app.run(debug=False, host='0.0.0.0', port=port)
    

    app.run(debug=True, host='0.0.0.0', port=5000)








