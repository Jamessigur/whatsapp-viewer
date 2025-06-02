from flask import Flask, render_template, request, redirect, url_for, send_from_directory, Response
import os
import zipfile
import re
from datetime import datetime
import shutil
from werkzeug.utils import secure_filename
import base64
import mimetypes

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['EXTRACTED_FOLDER'] = 'extracted'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size

# Создаем необходимые папки
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['EXTRACTED_FOLDER'], exist_ok=True)
os.makedirs('static/chats', exist_ok=True)

def parse_whatsapp_chat(file_path):
    """Парсит файл чата WhatsApp и возвращает список сообщений"""
    messages = []
    
    try:
        # Пробуем разные кодировки
        content = None
        for encoding in ['utf-8', 'utf-8-sig', 'cp1251', 'latin1']:
            try:
                with open(file_path, 'r', encoding=encoding) as file:
                    content = file.read()
                    print(f"Файл успешно прочитан с кодировкой: {encoding}")
                    break
            except UnicodeDecodeError:
                continue
        
        if not content:
            raise ValueError("Не удалось прочитать файл с поддерживаемыми кодировками")
        
        # Множественные паттерны для разных форматов экспорта WhatsApp
        patterns = [
            # Формат с квадратными скобками
            r'\[(\d{1,2}\.\d{1,2}\.\d{4}, \d{1,2}:\d{2}:\d{2})\] ([^:]+): (.+)',  # [3.04.2019, 9:28:07] Name: Message
            r'\[(\d{1,2}/\d{1,2}/\d{2,4}, \d{1,2}:\d{2}:\d{2})\] ([^:]+): (.+)',  # [4/3/2019, 9:28:07] Name: Message
            
            # Формат без квадратных скобок  
            r'(\d{1,2}\.\d{1,2}\.\d{4}, \d{1,2}:\d{2}:\d{2}): ([^:]+): (.+)',      # 22.10.2017, 9:27:50: Name: Message
            r'(\d{1,2}\.\d{1,2}\.\d{4}, \d{1,2}:\d{2}): ([^:]+): (.+)',            # 22.10.2017, 9:27 - Name: Message
            r'(\d{1,2}/\d{1,2}/\d{2,4}, \d{1,2}:\d{2}:\d{2}): ([^:]+): (.+)',     # 10/22/17, 9:27:50: Name: Message
            r'(\d{1,2}/\d{1,2}/\d{2,4}, \d{1,2}:\d{2}): ([^:]+): (.+)',           # 10/22/17, 9:27: Name: Message
            
            # Формат для системных сообщений (без имени отправителя)
            r'(\d{1,2}\.\d{1,2}\.\d{4}, \d{1,2}:\d{2}:\d{2}): (.+)',               # 22.10.2017, 9:27:50: System message
            r'(\d{1,2}\.\d{1,2}\.\d{4}, \d{1,2}:\d{2}): (.+)',                     # 22.10.2017, 9:27: System message
        ]
        
        # Разбиваем на строки
        lines = content.split('\n')
        current_message = None
        
        print(f"Обрабатываем {len(lines)} строк")
        
        for line_num, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            
            # Пробуем каждый паттерн
            match = None
            timestamp_str = None
            sender = None
            message_text = None
            is_system_message = False
            
            # Сначала пробуем паттерны с именем отправителя
            for i, pattern in enumerate(patterns[:6]):  # Первые 6 паттернов с отправителем
                match = re.match(pattern, line)
                if match:
                    timestamp_str, sender, message_text = match.groups()
                    break
            
            # Если не найдено, пробуем системные сообщения
            if not match:
                for pattern in patterns[6:]:  # Последние 2 паттерна для системных сообщений
                    match = re.match(pattern, line)
                    if match:
                        timestamp_str, message_text = match.groups()
                        sender = "Система"
                        is_system_message = True
                        break
            
            if match:
                # Сохраняем предыдущее сообщение
                if current_message:
                    messages.append(current_message)
                
                # Парсим timestamp с разными форматами
                timestamp = None
                timestamp_formats = [
                    '%d.%m.%Y, %H:%M:%S',
                    '%d.%m.%Y, %H:%M',
                    '%m/%d/%y, %H:%M:%S',
                    '%m/%d/%Y, %H:%M:%S',
                    '%d/%m/%y, %H:%M:%S',
                    '%d/%m/%Y, %H:%M:%S',
                    '%m/%d/%y, %H:%M',
                    '%m/%d/%Y, %H:%M',
                    '%d/%m/%y, %H:%M',
                    '%d/%m/%Y, %H:%M',
                    # Форматы с однозначными числами
                    '%d.%m.%Y, %H:%M:%S',
                    '%d.%m.%Y, %H:%M'
                ]
                
                for fmt in timestamp_formats:
                    try:
                        timestamp = datetime.strptime(timestamp_str, fmt)
                        break
                    except ValueError:
                        continue
                
                if not timestamp:
                    print(f"Не удалось распарсить время: {timestamp_str}")
                    continue
                
                # Проверяем вложения с разными форматами
                attachment_patterns = [
                    r'‎<прикреплено: (.+?)>',
                    r'<attached: (.+?)>',
                    r'(.+?\.(jpg|jpeg|png|gif|webp|bmp|mp4|avi|mov|wmv|flv|webm|mkv|mp3|wav|ogg|opus|m4a|aac|pdf|doc|docx)) <‎в приложении>',  # Ваш формат
                    r'(.+?\.(jpg|jpeg|png|gif|webp|bmp|mp4|avi|mov|wmv|flv|webm|mkv|mp3|wav|ogg|opus|m4a|aac|pdf|doc|docx)) \(в приложении\)',
                    r'<Media omitted>',
                    r'<Медиафайл не включён>',
                    r'\(файл прикреплён\)',
                    r'\(file attached\)',
                    r'audio omitted',
                    r'video omitted',
                    r'image omitted'
                ]
                
                attachment_match = None
                attachment_file = None
                
                for att_pattern in attachment_patterns:
                    attachment_match = re.search(att_pattern, message_text, re.IGNORECASE)
                    if attachment_match:
                        groups = attachment_match.groups()
                        if len(groups) >= 1 and groups[0]:
                            attachment_file = groups[0]
                            # Удаляем информацию о вложении из текста сообщения
                            message_text = re.sub(att_pattern, '', message_text, flags=re.IGNORECASE).strip()
                        else:
                            attachment_file = "media_omitted"
                        break
                
                current_message = {
                    'timestamp': timestamp,
                    'sender': sender.strip(),
                    'text': message_text.strip(),
                    'attachment': attachment_file,
                    'is_attachment': bool(attachment_match),
                    'is_system': is_system_message
                }
                
                if line_num < 5:  # Выводим первые несколько сообщений для отладки
                    print(f"Сообщение {line_num}: {current_message}")
                    
            else:
                # Продолжение предыдущего сообщения (многострочное)
                if current_message:
                    current_message['text'] += '\n' + line
        
        # Добавляем последнее сообщение
        if current_message:
            messages.append(current_message)
        
        print(f"Всего обработано сообщений: {len(messages)}")
        
        if not messages:
            # Попробуем показать первые строки файла для диагностики
            sample_lines = lines[:10]
            print("Первые 10 строк файла:")
            for i, line in enumerate(sample_lines):
                print(f"{i+1}: {repr(line)}")
            raise ValueError(f"Не найдено ни одного сообщения. Возможно, формат файла не поддерживается.")
        
    except Exception as e:
        print(f"Ошибка парсинга: {str(e)}")
        raise
    
    return messages

def extract_and_process_archive(zip_path):
    """Извлекает архив и обрабатывает чат"""
    extract_path = os.path.join(app.config['EXTRACTED_FOLDER'], 'current_chat')
    
    try:
        # Очищаем предыдущий извлеченный контент
        if os.path.exists(extract_path):
            shutil.rmtree(extract_path)
        os.makedirs(extract_path)
        
        # Извлекаем архив
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_path)
            print(f"Архив извлечен в: {extract_path}")
        
        # Показываем содержимое архива
        print("Содержимое архива:")
        for root, dirs, files in os.walk(extract_path):
            for file in files:
                file_path = os.path.join(root, file)
                print(f"  {file} ({os.path.getsize(file_path)} байт)")
        
        # Ищем файл чата (поддерживаем разные названия)
        chat_file = None
        chat_patterns = ['*chat.txt', '*Chat.txt', '*.txt']
        
        for root, dirs, files in os.walk(extract_path):
            for file in files:
                file_lower = file.lower()
                if (file_lower.endswith('chat.txt') or 
                    (file_lower.endswith('.txt') and 'chat' in file_lower) or
                    (file_lower.endswith('.txt') and len(files) == 1)):  # Если только один txt файл
                    chat_file = os.path.join(root, file)
                    print(f"Найден файл чата: {chat_file}")
                    break
            if chat_file:
                break
        
        if not chat_file:
            # Показываем все txt файлы
            txt_files = []
            for root, dirs, files in os.walk(extract_path):
                for file in files:
                    if file.lower().endswith('.txt'):
                        txt_files.append(file)
            
            if txt_files:
                raise ValueError(f"Файл чата не найден. Найденные текстовые файлы: {', '.join(txt_files)}. Убедитесь, что файл называется *chat.txt")
            else:
                raise ValueError("В архиве не найдено текстовых файлов чата (.txt)")
        
        # Парсим чат
        messages = parse_whatsapp_chat(chat_file)
        
        if not messages:
            raise ValueError("Файл чата пуст или имеет неподдерживаемый формат")
        
        # Копируем медиафайлы в статическую папку
        media_folder = os.path.join('static', 'chats', 'current')
        if os.path.exists(media_folder):
            shutil.rmtree(media_folder)
        os.makedirs(media_folder, exist_ok=True)
        
        media_files_copied = 0
        for root, dirs, files in os.walk(extract_path):
            for file in files:
                if not file.lower().endswith('.txt'):
                    src = os.path.join(root, file)
                    dst = os.path.join(media_folder, file)
                    try:
                        shutil.copy2(src, dst)
                        media_files_copied += 1
                    except Exception as e:
                        print(f"Ошибка копирования файла {file}: {e}")
        
        print(f"Скопировано медиафайлов: {media_files_copied}")
        print(f"Обработано сообщений: {len(messages)}")
        
        return messages
        
    except Exception as e:
        print(f"Ошибка обработки архива: {str(e)}")
        raise

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return "Файл не выбран", 400
    
    file = request.files['file']
    if file.filename == '':
        return "Файл не выбран", 400
    
    if not file or not file.filename.lower().endswith('.zip'):
        return "Пожалуйста, загрузите ZIP файл", 400
    
    try:
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        print(f"Файл сохранен: {file_path} ({os.path.getsize(file_path)} байт)")
        
        messages = extract_and_process_archive(file_path)
        
        if not messages:
            return "Файл чата пуст или имеет неподдерживаемый формат", 400
            
        return render_template('chat.html', messages=messages)
        
    except zipfile.BadZipFile:
        return "Файл поврежден или не является ZIP архивом", 400
    except ValueError as e:
        return f"Ошибка обработки: {str(e)}", 400
    except Exception as e:
        print(f"Неожиданная ошибка: {str(e)}")
        return f"Произошла ошибка при обработке файла: {str(e)}", 500

@app.route('/media/<filename>')
def media_file(filename):
    """Отдает медиафайлы с правильными заголовками"""
    media_path = os.path.join('static', 'chats', 'current')
    file_path = os.path.join(media_path, filename)
    
    print(f"Запрос медиафайла: {filename}")
    print(f"Полный путь: {file_path}")
    print(f"Файл существует: {os.path.exists(file_path)}")
    
    if os.path.exists(file_path):
        # Определяем MIME тип
        ext = filename.split('.')[-1].lower()
        
        # Для OPUS файлов возвращаем как audio/ogg
        if ext == 'opus':
            mime_type = 'audio/ogg; codecs=opus'
        elif ext == 'mp3':
            mime_type = 'audio/mpeg'
        elif ext == 'wav':
            mime_type = 'audio/wav'
        elif ext == 'ogg':
            mime_type = 'audio/ogg'
        elif ext in ['m4a', 'aac']:
            mime_type = 'audio/mp4'
        else:
            mime_type = 'application/octet-stream'
        
        print(f"MIME тип: {mime_type}")
        
        try:
            def generate():
                with open(file_path, 'rb') as f:
                    data = f.read(1024)
                    while data:
                        yield data
                        data = f.read(1024)
            
            file_size = os.path.getsize(file_path)
            
            response = Response(
                generate(),
                mimetype=mime_type,
                headers={
                    'Content-Length': str(file_size),
                    'Accept-Ranges': 'bytes',
                    'Content-Disposition': f'inline; filename="{filename}"',
                    'Cache-Control': 'public, max-age=3600'
                }
            )
            
            return response
            
        except Exception as e:
            print(f"Ошибка отправки файла: {e}")
            return f"Ошибка: {e}", 404
    else:
        print(f"Файл не найден: {file_path}")
        return "Файл не найден", 404

@app.route('/debug/media')
def debug_media():
    """Диагностическая страница для проверки медиафайлов"""
    media_folder = os.path.join('static', 'chats', 'current')
    files_info = []
    
    if os.path.exists(media_folder):
        for filename in os.listdir(media_folder):
            file_path = os.path.join(media_folder, filename)
            if os.path.isfile(file_path):
                file_size = os.path.getsize(file_path)
                file_ext = filename.split('.')[-1].lower()
                files_info.append({
                    'name': filename,
                    'size': file_size,
                    'ext': file_ext,
                    'is_audio': file_ext in ['mp3', 'wav', 'ogg', 'opus', 'm4a', 'aac']
                })
    
    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Диагностика медиафайлов</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
            .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; }}
            table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
            th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
            th {{ background-color: #25D366; color: white; }}
            .audio-test {{ margin: 10px 0; }}
            .status {{ padding: 5px 10px; border-radius: 15px; font-size: 12px; }}
            .status.success {{ background: #d4edda; color: #155724; }}
            .status.error {{ background: #f8d7da; color: #721c24; }}
            .browser-info {{ background: #e9ecef; padding: 15px; border-radius: 5px; margin: 20px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔍 Диагностика медиафайлов</h1>
            
            <div class="browser-info">
                <h3>Информация о браузере:</h3>
                <script>
                    document.write('<p><strong>User Agent:</strong> ' + navigator.userAgent + '</p>');
                    
                    // Проверяем поддержку аудиоформатов
                    const audio = document.createElement('audio');
                    const formats = {{
                        'MP3': 'audio/mpeg',
                        'WAV': 'audio/wav',
                        'OGG': 'audio/ogg',
                        'OPUS': 'audio/ogg; codecs=opus',
                        'WEBM OPUS': 'audio/webm; codecs=opus',
                        'MP4/AAC': 'audio/mp4'
                    }};
                    
                    document.write('<h4>Поддержка аудиоформатов:</h4><ul>');
                    for (const [name, mime] of Object.entries(formats)) {{
                        const support = audio.canPlayType(mime);
                        const status = support === 'probably' ? '✅ Отлично' : 
                                      support === 'maybe' ? '⚠️ Возможно' : '❌ Не поддерживается';
                        document.write(`<li><strong>${{name}}:</strong> ${{status}} (${{support}})</li>`);
                    }}
                    document.write('</ul>');
                </script>
            </div>
            
            <p><strong>Всего файлов:</strong> {len(files_info)}</p>
            <p><strong>Аудиофайлов:</strong> {len([f for f in files_info if f['is_audio']])}</p>
            
            <table>
                <tr>
                    <th>Имя файла</th>
                    <th>Размер</th>
                    <th>Формат</th>
                    <th>Статус</th>
                    <th>Тестирование</th>
                </tr>
    '''
    
    for file_info in files_info:
        status_class = 'success' if file_info['is_audio'] else 'error'
        status_text = 'Аудиофайл' if file_info['is_audio'] else 'Не аудио'
        
        html += f'''
            <tr>
                <td>{file_info['name']}</td>
                <td>{file_info['size']:,} байт</td>
                <td><strong>{file_info['ext'].upper()}</strong></td>
                <td><span class="status {status_class}">{status_text}</span></td>
                <td>
        '''
        
        if file_info['is_audio']:
            html += f'''
                    <div class="audio-test">
                        <p><a href="/media/{file_info['name']}" target="_blank">🔗 Открыть файл</a></p>
                        <p><a href="/test/audio/{file_info['name']}">🧪 Детальный тест</a></p>
                        <audio controls preload="none" style="width: 100%; max-width: 300px;">
            '''
            
            # Добавляем правильные source теги в зависимости от формата
            if file_info['ext'] == 'opus':
                html += f'''
                            <source src="/media/{file_info['name']}" type="audio/ogg; codecs=opus">
                            <source src="/media/{file_info['name']}" type="audio/webm; codecs=opus">
                '''
            elif file_info['ext'] == 'mp3':
                html += f'<source src="/media/{file_info["name"]}" type="audio/mpeg">'
            elif file_info['ext'] == 'wav':
                html += f'<source src="/media/{file_info["name"]}" type="audio/wav">'
            elif file_info['ext'] == 'ogg':
                html += f'<source src="/media/{file_info["name"]}" type="audio/ogg">'
            elif file_info['ext'] in ['m4a', 'aac']:
                html += f'<source src="/media/{file_info["name"]}" type="audio/mp4">'
            
            html += '''
                            Ваш браузер не поддерживает этот формат.
                        </audio>
                    </div>
            '''
        else:
            html += '<em>Не аудиофайл</em>'
        
        html += '</td></tr>'
    
    html += '''
        </table>
        
        <div style="margin-top: 30px; padding: 15px; background: #fff3cd; border-radius: 5px;">
            <h3>💡 Советы по устранению проблем:</h3>
            <ul>
                <li>Если OPUS не воспроизводится, попробуйте Chrome или Firefox</li>
                <li>Проверьте, что файлы не повреждены (скачайте и проверьте локально)</li>
                <li>Убедитесь, что в браузере включен звук</li>
                <li>Попробуйте обновить браузер до последней версии</li>
                <li>В некоторых браузерах нужно сначала взаимодействовать со страницей</li>
            </ul>
        </div>
        
        <br>
        <a href="/" style="background: #25D366; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">← Назад к чату</a>
        </div>
    </body>
    </html>
    '''
    
    return html

@app.route('/download')
def download_chat():
    """Создает автономный HTML файл для скачивания"""
    try:
        # Получаем сообщения из последней обработки
        extract_path = os.path.join(app.config['EXTRACTED_FOLDER'], 'current_chat')
        chat_file = None
        
        for root, dirs, files in os.walk(extract_path):
            for file in files:
                file_lower = file.lower()
                if (file_lower.endswith('chat.txt') or 
                    (file_lower.endswith('.txt') and 'chat' in file_lower)):
                    chat_file = os.path.join(root, file)
                    break
            if chat_file:
                break
        
        if not chat_file:
            return "Чат не найден. Сначала загрузите архив.", 400
        
        messages = parse_whatsapp_chat(chat_file)
        
        # Создаем автономный HTML
        standalone_html = create_standalone_html(messages)
        
        # Возвращаем файл для скачивания
        return Response(
            standalone_html,
            mimetype='text/html',
            headers={
                'Content-Disposition': 'attachment; filename=whatsapp_chat.html',
                'Content-Type': 'text/html; charset=utf-8'
            }
        )
    
    except Exception as e:
        print(f"Ошибка создания файла: {str(e)}")
        return f"Ошибка создания файла: {str(e)}", 500

def create_standalone_html(messages):
    """Создает автономный HTML файл со встроенными медиафайлами"""
    # Генерируем контент чата
    chat_content = generate_chat_content_standalone(messages)
    
    # Подсчитываем статистику
    participants = set(msg['sender'] for msg in messages if not msg.get('is_system', False))
    date_range = ""
    if messages:
        start_date = messages[0]['timestamp'].strftime('%d.%m.%Y')
        end_date = messages[-1]['timestamp'].strftime('%d.%m.%Y')
        date_range = f"{start_date} - {end_date}" if start_date != end_date else start_date
    
    # Базовый HTML шаблон
    html_template = f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WhatsApp Chat Export</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #ece5dd; min-height: 100vh; display: flex; flex-direction: column; }}
        .header {{ background: #25D366; color: white; padding: 15px 20px; display: flex; align-items: center; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
        .header-info {{ flex: 1; }}
        .chat-title {{ font-size: 18px; font-weight: 600; margin-bottom: 2px; }}
        .chat-subtitle {{ font-size: 14px; opacity: 0.8; }}
        .chat-container {{ flex: 1; overflow-y: auto; padding: 20px; }}
        .message {{ margin-bottom: 12px; display: flex; flex-direction: column; }}
        .message-bubble {{ max-width: 45%; min-width: 120px; padding: 8px 12px; border-radius: 18px; position: relative; word-wrap: break-word; box-shadow: 0 1px 2px rgba(0,0,0,0.1); }}
        .message.sent {{ align-items: flex-end; }}
        .message.sent .message-bubble {{ background: #dcf8c6; align-self: flex-end; border-bottom-right-radius: 5px; }}
        .message.received {{ align-items: flex-start; }}
        .message.received .message-bubble {{ background: white; align-self: flex-start; border-bottom-left-radius: 5px; }}
        .message.system {{ align-items: center; }}
        .message.system .message-bubble {{ background: #ffeaa7; color: #2d3436; font-style: italic; text-align: center; align-self: center; max-width: 80%; }}
        .sender-name {{ font-size: 13px; font-weight: 600; margin-bottom: 3px; color: #25D366; }}
        .message-text {{ font-size: 14px; line-height: 1.4; margin-bottom: 5px; white-space: pre-wrap; }}
        .message-time {{ font-size: 11px; color: #999; text-align: right; margin-top: 5px; }}
        .message.received .message-time {{ text-align: left; }}
        .attachment {{ margin-bottom: 8px; }}
        .attachment img {{ max-width: 250px; max-height: 250px; border-radius: 8px; cursor: pointer; transition: transform 0.2s ease; display: block; }}
        .attachment img:hover {{ transform: scale(1.02); }}
        .attachment video {{ max-width: 250px; max-height: 250px; border-radius: 8px; }}
        .attachment audio {{ width: 200px; margin: 5px 0; }}
        .custom-audio-player {{ background: #f5f5f5; border-radius: 20px; padding: 12px 16px; display: flex; align-items: center; gap: 12px; max-width: 280px; min-width: 220px; }}
        .audio-play-btn {{ width: 32px; height: 32px; border-radius: 50%; background: #25D366; border: none; color: white; display: flex; align-items: center; justify-content: center; cursor: pointer; font-size: 14px; }}
        .audio-play-btn:hover {{ background: #20b358; }}
        .audio-info {{ display: flex; flex-direction: column; gap: 5px; flex: 1; }}
        .audio-duration {{ font-size: 12px; color: #666; font-weight: 500; }}
        .audio-progress {{ width: 100%; height: 4px; background: #ddd; border-radius: 2px; overflow: hidden; }}
        .audio-progress-bar {{ height: 100%; background: #25D366; width: 0%; transition: width 0.1s; }}
        .video-player {{ position: relative; max-width: 250px; }}
        .video-player video {{ width: 100%; border-radius: 8px; }}
        .attachment-placeholder {{ background: #f0f0f0; padding: 15px; border-radius: 8px; text-align: center; color: #666; font-size: 14px; display: flex; align-items: center; justify-content: center; gap: 10px; max-width: 200px; }}
        .media-icon {{ font-size: 24px; }}
        .date-separator {{ text-align: center; margin: 20px 0; }}
        .date-separator span {{ background: rgba(255,255,255,0.8); padding: 6px 12px; border-radius: 15px; font-size: 12px; color: #666; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .stats {{ background: white; margin: 20px; padding: 15px; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); font-size: 14px; color: #666; }}
        .modal {{ display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.9); }}
        .modal-content {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); max-width: 90%; max-height: 90%; }}
        .modal img {{ width: 100%; height: auto; border-radius: 8px; }}
        .close {{ position: absolute; top: 15px; right: 35px; color: #f1f1f1; font-size: 40px; font-weight: bold; cursor: pointer; }}
        .close:hover {{ color: white; }}
        @media (max-width: 768px) {{ .message-bubble {{ max-width: 85%; }} .chat-container {{ padding: 10px; }} }}
    </style>
</head>
<body>
    <div class="header">
        <div class="header-info">
            <div class="chat-title">WhatsApp Chat Export</div>
            <div class="chat-subtitle">{len(messages)} сообщений</div>
        </div>
    </div>
    <div class="stats">📊 Всего сообщений: {len(messages)} | 👥 Участники: {len(participants)} | 📅 Период: {date_range}</div>
    <div class="chat-container">{chat_content}</div>
    <div id="imageModal" class="modal"><span class="close" onclick="closeModal()">&times;</span><div class="modal-content"><img id="modalImage" src="" alt=""></div></div>
    <script>
        function initAudioPlayers() {{
            const audioPlayers = document.querySelectorAll('.custom-audio-player');
            audioPlayers.forEach(player => {{
                const audio = player.querySelector('audio');
                const durationEl = player.querySelector('.audio-duration');
                
                audio.addEventListener('loadedmetadata', function() {{
                    const duration = formatTime(audio.duration);
                    durationEl.textContent = `🎵 ${{duration}}`;
                }});
                
                audio.addEventListener('timeupdate', function() {{
                    const progressBar = player.querySelector('.audio-progress-bar');
                    const progress = (audio.currentTime / audio.duration) * 100;
                    progressBar.style.width = progress + '%';
                    
                    const remaining = audio.duration - audio.currentTime;
                    durationEl.textContent = `🎵 ${{formatTime(remaining)}}`;
                }});
                
                audio.addEventListener('ended', function() {{
                    const playBtn = player.querySelector('.audio-play-btn');
                    playBtn.textContent = '▶';
                    playBtn.dataset.playing = 'false';
                    const progressBar = player.querySelector('.audio-progress-bar');
                    progressBar.style.width = '0%';
                    durationEl.textContent = `🎵 ${{formatTime(audio.duration)}}`;
                }});
            }});
        }}
        
        function toggleAudio(button) {{
            const player = button.closest('.custom-audio-player');
            const audio = player.querySelector('audio');
            
            if (button.dataset.playing === 'true') {{
                audio.pause();
                button.textContent = '▶';
                button.dataset.playing = 'false';
            }} else {{
                document.querySelectorAll('.custom-audio-player audio').forEach(otherAudio => {{
                    if (otherAudio !== audio) {{
                        otherAudio.pause();
                        const otherBtn = otherAudio.closest('.custom-audio-player').querySelector('.audio-play-btn');
                        otherBtn.textContent = '▶';
                        otherBtn.dataset.playing = 'false';
                    }}
                }});
                
                audio.play();
                button.textContent = '⏸';
                button.dataset.playing = 'true';
            }}
        }}
        
        function formatTime(seconds) {{
            if (isNaN(seconds)) return '0:00';
            const mins = Math.floor(seconds / 60);
            const secs = Math.floor(seconds % 60);
            return `${{mins}}:${{secs.toString().padStart(2, '0')}}`;
        }}
        
        document.addEventListener('DOMContentLoaded', function() {{ initAudioPlayers(); }});
        
        function openModal(src) {{ document.getElementById('imageModal').style.display = 'block'; document.getElementById('modalImage').src = src; }}
        function closeModal() {{ document.getElementById('imageModal').style.display = 'none'; }}
        window.onclick = function(event) {{ if (event.target == document.getElementById('imageModal')) {{ closeModal(); }} }}
        document.addEventListener('keydown', function(event) {{ if (event.key === 'Escape') {{ closeModal(); }} }});
    </script>
</body>
</html>'''

    return html_template

def generate_chat_content_standalone(messages):
    """Генерирует HTML контент чата с встроенными медиафайлами"""
    content = ""
    current_date = None
    media_folder = os.path.join('static', 'chats', 'current')
    
    for message in messages:
        message_date = message['timestamp'].strftime('%d.%m.%Y')
        
        # Показываем дату только если она изменилась
        if current_date != message_date:
            current_date = message_date
            content += f'<div class="date-separator"><span>{message_date}</span></div>'
        
        # Определяем класс сообщения
        if message.get('is_system', False):
            msg_class = 'system'
        elif message['sender'] in ['Igor', 'S', 'Ss']:
            msg_class = 'sent'
        else:
            msg_class = 'received'
        
        content += f'<div class="message {msg_class}"><div class="message-bubble">'
        
        # Имя отправителя (только для входящих сообщений)
        if msg_class == 'received':
            sender_escaped = message["sender"].replace('<', '&lt;').replace('>', '&gt;')
            content += f'<div class="sender-name">{sender_escaped}</div>'
        
        # Вложения
        if message.get('is_attachment') and message.get('attachment') and message['attachment'] != 'media_omitted':
            attachment_file = message['attachment']
            file_path = os.path.join(media_folder, attachment_file)
            
            if os.path.exists(file_path):
                file_ext = attachment_file.split('.')[-1].lower()
                
                try:
                    with open(file_path, 'rb') as f:
                        file_data = f.read()
                        file_base64 = base64.b64encode(file_data).decode('utf-8')
                        mime_type = mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
                        data_url = f"data:{mime_type};base64,{file_base64}"
                    
                    content += '<div class="attachment">'
                    
                    if file_ext in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp']:
                        content += f'<img src="{data_url}" alt="Изображение" onclick="openModal(this.src)">'
                    elif file_ext in ['mp4', 'avi', 'mov', 'wmv', 'flv', 'webm', 'mkv']:
                        content += f'<div class="video-player"><video controls preload="metadata"><source src="{data_url}" type="{mime_type}">Ваш браузер не поддерживает воспроизведение видео.</video></div>'
                    elif file_ext in ['mp3', 'wav', 'ogg', 'opus', 'm4a', 'aac']:
                        content += f'''<div class="custom-audio-player">
                            <span class="media-icon">🎵</span>
                            <div class="audio-info">
                                <div class="audio-duration">Аудио сообщение</div>
                                <div class="audio-progress"><div class="audio-progress-bar"></div></div>
                            </div>
                            <button class="audio-play-btn" onclick="toggleAudio(this)">▶</button>
                            <audio preload="metadata" style="display: none;">
                                <source src="{data_url}" type="audio/mpeg">
                                <source src="{data_url}" type="audio/ogg">
                                <source src="{data_url}" type="audio/wav">
                            </audio>
                        </div>
                        <div style="margin-top: 10px;">
                            <audio controls style="width: 100%; max-width: 250px;">
                                <source src="{data_url}" type="audio/mpeg">
                                <source src="{data_url}" type="audio/ogg">
                                <source src="{data_url}" type="audio/wav">
                                Ваш браузер не поддерживает воспроизведение аудио.
                            </audio>
                        </div>'''
                    else:
                        escaped_filename = attachment_file.replace('<', '&lt;').replace('>', '&gt;')
                        content += f'<div class="attachment-placeholder"><span class="media-icon">📎</span><div><strong>Вложение</strong><br><small>{escaped_filename}</small></div></div>'
                    
                    content += '</div>'
                
                except Exception as e:
                    escaped_filename = attachment_file.replace('<', '&lt;').replace('>', '&gt;')
                    content += f'<div class="attachment-placeholder"><span class="media-icon">❌</span><div>Ошибка загрузки<br><small>{escaped_filename}</small></div></div>'
            else:
                escaped_filename = attachment_file.replace('<', '&lt;').replace('>', '&gt;')
                content += f'<div class="attachment-placeholder"><span class="media-icon">📎</span><div>Файл недоступен<br><small>{escaped_filename}</small></div></div>'
        elif message.get('attachment') == 'media_omitted':
            content += '<div class="attachment-placeholder"><span class="media-icon">📷</span><div>Медиафайл не включён</div></div>'
        
        # Текст сообщения
        if message.get('text') and message['text'].strip():
            escaped_text = message['text'].replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br>')
            content += f'<div class="message-text">{escaped_text}</div>'
        
        # Время
        time_str = message['timestamp'].strftime('%H:%M')
        content += f'<div class="message-time">{time_str}</div>'
        
        content += '</div></div>'
    
    return content

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
