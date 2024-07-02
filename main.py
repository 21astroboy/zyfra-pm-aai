import requests
from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import bcrypt
import psycopg2
import schedule
import time
from threading import Thread

TOKEN = '7312237819:AAE50V0ZEVyATIyE53BPNZnkMpZP6GmDc9U'
bot = TeleBot(TOKEN)
jira_token = "HFjVoGesbxaIP8JoERE81afFfy7yqb1NsT3CCL"
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
    SELECT password FROM project_managers WHERE mail = %s
    """
    cursor.execute(select_query, (mail,))
    result = cursor.fetchone()
    cursor.close()
    conn.close()

    if result is None:
        return False

    stored_hashed_password = result[0]
    return bcrypt.checkpw(provided_password.encode('utf-8'), stored_hashed_password.encode('utf-8'))


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
    bot.reply_to(message, "Привет! Пожалуйста, авторизуйтесь для начала работы. Введите вашу почту:")


@bot.message_handler(func=lambda message: message.chat.id not in user_sessions)
def ask_for_email(message):
    user_sessions[message.chat.id] = {'email': message.text}
    bot.reply_to(message, "Теперь введите ваш пароль:")


@bot.message_handler(func=lambda message: message.chat.id in user_sessions and 'email' in user_sessions[
    message.chat.id] and 'password' not in user_sessions[message.chat.id])
def ask_for_password(message):
    email = user_sessions[message.chat.id]['email']
    password = message.text

    if check_password(email, password):
        user_sessions[message.chat.id]['password'] = password
        bot.reply_to(message, "Авторизация успешна! Введите почту проектного менеджера, чьи проекты вы хотите увидеть:")
        # Удаляем сообщение с паролем
        bot.delete_message(message.chat.id, message.message_id)
    else:
        user_sessions.pop(message.chat.id, None)
        bot.reply_to(message, "Неверная почта или пароль. Попробуйте снова, введите вашу почту:")


@bot.message_handler(func=lambda message: message.chat.id in user_sessions and 'password' in user_sessions[
    message.chat.id] and 'manager_email' not in user_sessions[message.chat.id])
def ask_for_manager_email(message):
    user_sessions[message.chat.id]['manager_email'] = message.text
    bot.reply_to(message, "Теперь вы можете использовать команду /get_projects для получения всех задач.")


@bot.message_handler(commands=['get_projects'])
def get_all_issues(message):
    if message.chat.id not in user_sessions or 'email' not in user_sessions[message.chat.id] or 'password' not in \
            user_sessions[message.chat.id]:
        bot.reply_to(message, "Пожалуйста, сначала авторизуйтесь. Введите вашу почту:")
        return

    if 'manager_email' not in user_sessions[message.chat.id]:
        bot.reply_to(message, "Пожалуйста, введите почту проектного менеджера, чьи проекты вы хотите увидеть.")
        return

    manager_email = user_sessions[message.chat.id]['manager_email']
    url = f"{JIRA_URL}/rest/api/2/search"
    jql_query = f'issuetype=Epic AND project="DP00001" ORDER BY duedate'
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
                                 and issue['fields']['customfield_12911'] and issue['fields']['customfield_12911'][
                                     'emailAddress'] == manager_email
                          ][:9]

        if filtered_issues:
            markup = InlineKeyboardMarkup()
            for summary, issue_key in filtered_issues:
                project_name, project_number = summary.split(' - ')[0], issue_key.split('-')[-1]
                button_text = f"{project_name} ({project_number})"
                markup.add(InlineKeyboardButton(button_text, callback_data=f'issue_{issue_key}'))
            bot.send_message(message.chat.id, "Выберите задачу:", reply_markup=markup)
        else:
            bot.send_message(message.chat.id, "Задачи не найдены.")
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
        assignee = fields.get('assignee', {}).get('displayName', 'Unassigned') if fields.get(
            'assignee') else 'Unassigned'
        reporter = fields.get('reporter', {}).get('displayName', 'Unknown Reporter') if fields.get(
            'reporter') else 'Unknown Reporter'
        manager = fields.get('customfield_12911', {}).get('displayName', 'Unknown Manager') if fields.get(
            'customfield_12911') else 'Unknown Manager'

        created_date = fields.get('created')
        updated_date = fields.get('updated')
        due_date = fields.get('duedate', 'No Due Date')

        response_text = (
            f"Issue Key: {json_data['key']}\n"
            f"Project: {project_name}\n"
            f"Assignee: {assignee}\n"
            f"Reporter: {reporter}\n"
            f"Manager: {manager}\n"
            f"Created Date: {created_date}\n"
            f"Updated Date: {updated_date}\n"
            f"Due Date: {due_date}\n"
        )

        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("Получить индикатор", callback_data=f'indicator_{json_data["key"]}'))

        bot.send_message(message.chat.id, response_text, reply_markup=markup)

    except requests.exceptions.HTTPError as err:
        bot.send_message(message.chat.id, f"Ошибка HTTP: {err}")
    except requests.exceptions.RequestException as err:
        bot.send_message(message.chat.id, f"Ошибка запроса: {err}")


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
        f'AND reporter="{manager_email}" ORDER BY created DESC'
    )

    params = {
        "jql": jql_query,
        "maxResults": 10000
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
            issue = issues[0]  # предположим, что берем первый найденный индикатор
            fields = issue['fields']

            content_status = fields.get('customfield_14121', {}).get('value', 'N/A')
            deadline_status = fields.get('customfield_14122', {}).get('value', 'N/A')
            resource_status = fields.get('customfield_14200', {}).get('value', 'N/A')
            budget_status = fields.get('customfield_14123', {}).get('value', 'N/A')

            response_text = (
                f"Содержание: {get_color_value(content_status)} {content_status}\n"
                f"Сроки: {get_color_value(deadline_status)} {deadline_status}\n"
                f"Ресурсы: {get_color_value(resource_status)} {resource_status}\n"
                f"Бюджет: {get_color_value(budget_status)} {budget_status}\n"
            )
        else:
            response_text = "Индикаторы не найдены."

        bot.send_message(call.message.chat.id, response_text)
    except requests.exceptions.HTTPError as err:
        bot.send_message(call.message.chat.id, f"Ошибка HTTP: {err}")
    except requests.exceptions.RequestException as err:
        bot.send_message(call.message.chat.id, f"Ошибка запроса: {err}")


bot.infinity_polling()
