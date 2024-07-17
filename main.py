import requests
from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import bcrypt
import psycopg2
import schedule
import time
from threading import Thread
from datetime import datetime

TOKEN = '7312237819:AAE50V0ZEVyATIyE53BPNZnkMpZP6GmDc9U'
bot = TeleBot(TOKEN)
jira_token = "foenELVXDA6eo1eI7NqHIjCCp671hiiKTbuDSd"
JIRA_URL = 'https://jira.zyfra.com'
DEFAULT_PASSWORD = "#tSfoNtyQa$r"

# Хранение сессий пользователей
user_sessions = {}

def get_db_connection():
    return psycopg2.connect(
        dbname="Zyfra-PM-AAI",
        user="zyfra-bot",
        password="#tSfoNtyQa$r"
    )

def check_password(mail, provided_password):
    conn = get_db_connection()
    cursor = conn.cursor()

    select_query = """
    SELECT password, admin FROM project_managers WHERE mail = %s
    """
    cursor.execute(select_query, (mail,))
    result = cursor.fetchone()
    cursor.close()
    conn.close()

    if result is None:
        return False, None

    stored_hashed_password, is_admin = result
    return bcrypt.checkpw(provided_password.encode('utf-8'), stored_hashed_password.encode('utf-8')), is_admin

def get_all_project_managers():
    url = f"{JIRA_URL}/rest/api/2/search"
    jql_query = 'issuetype=Epic AND project="DP00001" ORDER BY duedate'
    params = {
        "jql": jql_query,
        "maxResults": 20000
    }
    headers = {
        "Authorization": f"Bearer {jira_token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        json_data = response.json()

        issues = json_data.get('issues', [])
        project_managers = {
            issue['fields']['customfield_12911']['emailAddress']
            for issue in issues
            if 'fields' in issue and 'customfield_12911' in issue['fields'] and issue['fields']['customfield_12911']
        }
        return project_managers
    except requests.exceptions.RequestException as err:
        print(f"Ошибка запроса: {err}")
        return set()

def update_project_managers():
    project_managers = get_all_project_managers()
    if not project_managers:
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    for email in project_managers:
        select_query = """
        SELECT COUNT(*) FROM project_managers WHERE mail = %s
        """
        cursor.execute(select_query, (email,))
        count = cursor.fetchone()[0]

        if count == 0:
            hashed_password = bcrypt.hashpw(DEFAULT_PASSWORD.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            insert_query = """
            INSERT INTO project_managers (mail, password) VALUES (%s, %s)
            """
            cursor.execute(insert_query, (email, hashed_password))
            conn.commit()

    cursor.close()
    conn.close()

schedule.every().hour.do(update_project_managers)

def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(1)

schedule_thread = Thread(target=run_schedule)
schedule_thread.daemon = True
schedule_thread.start()

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Авторизоваться", callback_data="auth"))
    bot.send_message(message.chat.id, "Привет! Пожалуйста, авторизуйтесь для начала работы.", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "auth")
def auth_callback(call):
    bot.send_message(call.message.chat.id, "Введите вашу почту:")
    bot.register_next_step_handler(call.message, ask_for_email)

def ask_for_email(message):
    user_sessions[message.chat.id] = {'email': message.text}
    bot.send_message(message.chat.id, "Теперь введите ваш пароль:")
    bot.register_next_step_handler(message, ask_for_password)

def ask_for_password(message):
    email = user_sessions[message.chat.id]['email']
    password = message.text

    success, is_admin = check_password(email, password)
    if success:
        user_sessions[message.chat.id]['password'] = password
        user_sessions[message.chat.id]['is_admin'] = is_admin
        markup = InlineKeyboardMarkup()

        if is_admin:
            markup.add(
                InlineKeyboardButton("Получить проекты", callback_data="get_projects"),
                InlineKeyboardButton("Выйти из системы", callback_data="logout")
            )
        else:
            markup.add(
                InlineKeyboardButton("Получить мои проекты", callback_data="get_my_projects"),
                InlineKeyboardButton("Выйти из системы", callback_data="logout")
            )

        bot.send_message(message.chat.id, "Авторизация успешна!", reply_markup=markup)
        bot.delete_message(message.chat.id, message.message_id)  # Удаляем сообщение с паролем
    else:
        user_sessions.pop(message.chat.id, None)
        bot.send_message(message.chat.id, "Неверная почта или пароль. Попробуйте снова, введите вашу почту:")
        bot.register_next_step_handler(message, ask_for_email)

@bot.callback_query_handler(func=lambda call: call.data == "logout")
def logout_callback(call):
    user_sessions.pop(call.message.chat.id, None)
    bot.send_message(call.message.chat.id, "Вы успешно вышли из системы.")
    send_welcome(call.message)

@bot.callback_query_handler(func=lambda call: call.data == "get_projects")
def get_projects_callback(call):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Выйти из системы", callback_data="logout"))
    bot.send_message(call.message.chat.id, "Введите почту проектного менеджера, чьи проекты вы хотите увидеть:", reply_markup=markup)
    bot.register_next_step_handler(call.message, ask_for_manager_email)

def ask_for_manager_email(message):
    user_sessions[message.chat.id]['manager_email'] = message.text
    get_all_issues(message)

@bot.callback_query_handler(func=lambda call: call.data == "get_my_projects")
def get_my_projects_callback(call):
    user_sessions[call.message.chat.id]['manager_email'] = user_sessions[call.message.chat.id]['email']
    get_all_issues(call.message)

@bot.message_handler(commands=['get_projects'])
def get_all_issues(message):
    if message.chat.id not in user_sessions or 'email' not in user_sessions[message.chat.id] or 'password' not in user_sessions[message.chat.id]:
        bot.reply_to(message, "Пожалуйста, сначала авторизуйтесь. Введите вашу почту:")
        return

    manager_email = user_sessions[message.chat.id]['manager_email']
    is_admin = user_sessions[message.chat.id].get('is_admin', False)

    if not is_admin and manager_email != user_sessions[message.chat.id]['email']:
        bot.reply_to(message, "Вы не можете просматривать проекты других менеджеров.")
        return

    url = f"{JIRA_URL}/rest/api/2/search"
    jql_query = f'issuetype=Epic AND project="DP00001" AND cf[14712]="Выполняется" ORDER BY duedate'
    params = {
        "jql": jql_query,
        "maxResults": 5000
    }
    headers = {
        "Authorization": f"Bearer {jira_token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        json_data = response.json()

        issues = json_data.get('issues', [])
        filtered_issues = [
            (issue['fields']['summary'], issue['key']) for issue in issues
            if 'fields' in issue and 'customfield_12911' in issue['fields']
               and issue['fields']['customfield_12911'] and issue['fields']['customfield_12911']['emailAddress'] == manager_email
        ]

        filtered_issues.sort(key=lambda x: int(x[1].split('-')[1]))  # Сортировка по возрастанию номера задачи

        if filtered_issues:
            markup = InlineKeyboardMarkup()
            for summary, issue_key in filtered_issues[:9]:  # Отображение только первых 9 задач
                project_name, project_number = summary.split(' - ')[0], issue_key.split('-')[-1]
                button_text = f"({project_number}) {project_name}"
                markup.add(InlineKeyboardButton(button_text, callback_data=f'issue_{issue_key}'))
            markup.add(InlineKeyboardButton("Выйти из системы", callback_data="logout"))
            bot.send_message(message.chat.id, "Выберите задачу:", reply_markup=markup)
        else:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("Выйти из системы", callback_data="logout"))
            bot.send_message(message.chat.id, "Задачи не найдены.", reply_markup=markup)
    except requests.exceptions.HTTPError as err:
        bot.send_message(message.chat.id, f"Ошибка HTTP: {err}")
    except requests.exceptions.RequestException as err:
        bot.send_message(message.chat.id, f"Ошибка запроса: {err}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('issue_'))
def callback_inline(call):
    issue_key = call.data.split('issue_')[1]
    get_issue_by_key(call.message, issue_key)

def get_issue_by_key(message, issue_key):
    issue_url = f"{JIRA_URL}/rest/api/2/issue/{issue_key}"

    headers = {
        "Authorization": f"Bearer {jira_token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(issue_url, headers=headers)
        response.raise_for_status()
        json_data = response.json()
        fields = json_data.get('fields', {})

        project_name = fields.get('summary', 'Unknown Project')
        assignee = fields.get('assignee', {}).get('displayName', 'Unassigned') if fields.get('assignee') else 'Unassigned'
        reporter = fields.get('reporter', {}).get('displayName', 'Unknown Reporter') if fields.get('reporter') else 'Unknown Reporter'
        manager = fields.get('customfield_12911', {}).get('displayName', 'Unknown Manager') if fields.get('customfield_12911') else 'Unknown Manager'

        created_date = fields.get('created')
        updated_date = fields.get('updated')
        due_date = fields.get('duedate', 'No Due Date')

        response_text = (
            f"Ключ проекта: {json_data['key']}\n"
            f"Название проекта: {project_name}\n"
           # f"Assignee: {assignee}\n"
           # f"Создатель: {reporter}\n"
            f"РП: {manager}\n"
            f"Дата создания: {created_date}\n"
            f"Обновлено: {updated_date}\n"
            #f"Due Date: {due_date}\n"
        )

        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("Выбрать другой проект", callback_data="choose_another_project"),
            InlineKeyboardButton("Получить индикатор", callback_data=f'indicator_{json_data["key"]}'),
            InlineKeyboardButton("Выйти из системы", callback_data="logout")
        )

        bot.send_message(message.chat.id, response_text, reply_markup=markup)

    except requests.exceptions.HTTPError as err:
        bot.send_message(message.chat.id, f"Ошибка HTTP: {err}")
    except requests.exceptions.RequestException as err:
        bot.send_message(message.chat.id, f"Ошибка запроса: {err}")

# Function to get color value
# Function to get color value
def get_color_value(field):
    if field == "yellow":
        return "🟡"
    elif field == "red":
        return "🔴"
    elif field == "green":
        return "🟢"
    else:
        return "⚪"

@bot.callback_query_handler(func=lambda call: call.data.startswith('indicator_'))
def get_indicators(call):
    issue_key = call.data.split('indicator_')[1]
    manager_email = user_sessions[call.message.chat.id]['manager_email']

    url = f"{JIRA_URL}/rest/api/2/search"
    jql_query = (
        f'issuetype="Индикатор" AND cf[10002]="{issue_key}" '
        f'ORDER BY updated DESC'
    )

    params = {
        "jql": jql_query,
        "maxResults": 1
    }

    headers = {
        "Authorization": f"Bearer {jira_token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        json_data = response.json()
        issues = json_data.get('issues', [])

        if issues:
            issue = issues[0]
            fields = issue['fields']

            content_status = fields.get('customfield_14121', {}).get('value', 'N/A')
            deadline_status = fields.get('customfield_14122', {}).get('value', 'N/A')
            resource_status = fields.get('customfield_14200', {}).get('value', 'N/A')
            budget_status = fields.get('customfield_14123', {}).get('value', 'N/A')

            content_comment = fields.get('customfield_15103', 'Нет комментариев')
            deadline_comment = fields.get('customfield_15100', 'Нет комментариев')
            resource_comment = fields.get('customfield_15101', 'Нет комментариев')
            budget_comment = fields.get('customfield_15102', 'Нет комментариев')

            user_sessions[call.message.chat.id]['previous_indicator'] = {
                'content': content_status,
                'deadline': deadline_status,
                'resource': resource_status,
                'budget': budget_status,
                'content_comment': content_comment,
                'deadline_comment': deadline_comment,
                'resource_comment': resource_comment,
                'budget_comment': budget_comment,
                'issue_key': issue_key
            }

            response_text = (
                f"Содержание: {get_color_value(content_status)}\n"
                f"{content_comment}\n"
                f"Сроки: {get_color_value(deadline_status)}\n"
                f"{deadline_comment}\n"
                f"Ресурсы: {get_color_value(resource_status)}\n"
                f"{resource_comment}\n"
                f"Бюджет: {get_color_value(budget_status)}\n"
                f"{budget_comment}\n"
            )
        else:
            response_text = "Индикаторы не найдены."

        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("Выбрать другой проект", callback_data="choose_another_project"),
            InlineKeyboardButton("Создать новый индикатор", callback_data=f'create_new_indicator_{issue_key}'),
            InlineKeyboardButton("Выйти из системы", callback_data="logout")
        )

        bot.send_message(call.message.chat.id, response_text, reply_markup=markup)
    except requests.exceptions.HTTPError as err:
        bot.send_message(call.message.chat.id, f"Ошибка HTTP: {err}")
        print(f"HTTP Error: {err}")
    except requests.exceptions.RequestException as err:
        bot.send_message(call.message.chat.id, f"Ошибка запроса: {err}")
        print(f"Request Exception: {err}")

def create_new_indicator(message, issue_key):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("Изменить сроки", callback_data=f'change_indicator_deadline_{issue_key}'),
        InlineKeyboardButton("Изменить бюджет", callback_data=f'change_indicator_budget_{issue_key}'),
        InlineKeyboardButton("Изменить ресурсы", callback_data=f'change_indicator_resources_{issue_key}'),
        InlineKeyboardButton("Изменить содержание", callback_data=f'change_indicator_content_{issue_key}'),
        InlineKeyboardButton("Сохранить изменения", callback_data=f'save_new_indicator_{issue_key}')
    )
    bot.send_message(message.chat.id, "Выберите, что изменить:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('create_new_indicator_'))
def handle_create_new_indicator(call):
    issue_key = call.data.split('create_new_indicator_')[1]
    user_sessions[call.message.chat.id]['new_indicator'] = {
        'issue_key': issue_key,
        'content': None,
        'deadline': None,
        'resource': None,
        'budget': None,
        'content_comment': None,
        'deadline_comment': None,
        'resource_comment': None,
        'budget_comment': None,
    }
    create_new_indicator(call.message, issue_key)

@bot.callback_query_handler(func=lambda call: call.data.startswith('change_indicator_'))
def handle_change_indicator(call):
    parts = call.data.split('_')
    indicator_type = parts[2]
    issue_key = parts[3]

    user_sessions[call.message.chat.id]['current_indicator'] = indicator_type

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🟢 Зеленый", callback_data=f'color_green_{issue_key}'),
        InlineKeyboardButton("🟡 Желтый", callback_data=f'color_yellow_{issue_key}'),
        InlineKeyboardButton("🔴 Красный", callback_data=f'color_red_{issue_key}')
    )
    bot.send_message(call.message.chat.id, "Выберите цвет индикатора:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('color_'))
def handle_color_choice(call):
    parts = call.data.split('_')
    color = parts[1]
    issue_key = parts[2]

    indicator_type = user_sessions[call.message.chat.id]['current_indicator']
    user_sessions[call.message.chat.id]['new_indicator'][indicator_type] = color

    msg = bot.send_message(call.message.chat.id, "Введите комментарий (или оставьте пустым):")
    bot.register_next_step_handler(msg, handle_comment, issue_key)

def handle_comment(message, issue_key):
    comment = message.text if message.text else "Нет комментариев"

    indicator_type = user_sessions[message.chat.id]['current_indicator']
    user_sessions[message.chat.id]['new_indicator'][f'{indicator_type}_comment'] = comment

    create_new_indicator(message, issue_key)

# Обновленный код для более точной отладки
@bot.callback_query_handler(func=lambda call: call.data.startswith('save_new_indicator_'))
def save_new_indicator(call):
    issue_key = call.data.split('save_new_indicator_')[1]
    chat_id = call.message.chat.id

    new_indicator = user_sessions[chat_id].get('new_indicator')
    previous_indicator = user_sessions[chat_id].get('previous_indicator')

    if new_indicator:
        if previous_indicator:
            for key in previous_indicator:
                if new_indicator.get(key) is None:
                    new_indicator[key] = previous_indicator[key]

        print("Final new_indicator before sending to Jira:", new_indicator)  # Добавляем логирование перед отправкой
        create_issue_in_jira(call.message, new_indicator)
    else:
        bot.send_message(call.message.chat.id, "Вы не выбрали все необходимые параметры.")

def create_issue_in_jira(message, new_indicator):
    url = f"{JIRA_URL}/rest/api/2/issue"

    headers = {
        "Authorization": f"Bearer {jira_token}",
        "Content-Type": "application/json"
    }

    today_date = datetime.now().strftime("%d.%m.%Y")

    fields = {
        "project": {
            "key": new_indicator['issue_key'].split('-')[0]
        },
        "summary": f"{today_date}",
        "issuetype": {
            "name": "Индикатор"
        },
        "customfield_14121": {"value": new_indicator['content']} if new_indicator['content'] else None,
        "customfield_14122": {"value": new_indicator['deadline']} if new_indicator['deadline'] else None,
        "customfield_14200": {"value": new_indicator['resource']} if new_indicator['resource'] else None,
        "customfield_14123": {"value": new_indicator['budget']} if new_indicator['budget'] else None,
        "customfield_15103": new_indicator['content_comment'] if new_indicator['content_comment'] else None,
        "customfield_15100": new_indicator['deadline_comment'] if new_indicator['deadline_comment'] else None,
        "customfield_15101": new_indicator['resource_comment'] if new_indicator['resource_comment'] else None,
        "customfield_15102": new_indicator['budget_comment'] if new_indicator['budget_comment'] else None,
        "customfield_10002": new_indicator['issue_key']
    }

    fields = {k: v for k, v in fields.items() if v is not None}

    data = {
        "fields": fields
    }

    print("URL:", url)
    print("Headers:", headers)
    print("Data:", data)  # Добавляем логирование данных перед отправкой

    try:
        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status()
        json_data = response.json()
        new_issue_key = json_data.get('key')
        bot.send_message(message.chat.id, f"Новый индикатор успешно создан: {new_issue_key}")
        print(f"Created issue: {new_issue_key}")
    except requests.exceptions.HTTPError as err:
        bot.send_message(message.chat.id, f"Ошибка HTTP: {err}")
        print(f"HTTP Error: {err}")
    except requests.exceptions.RequestException as err:
        bot.send_message(message.chat.id, f"Ошибка запроса: {err}")
        print(f"Request Exception: {err}")

bot.infinity_polling()