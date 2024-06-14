import requests
from telebot import TeleBot, types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import json
import bcrypt
import psycopg2

TOKEN = '7312237819:AAE50V0ZEVyATIyE53BPNZnkMpZP6GmDc9U'
bot = TeleBot(TOKEN)
jira_token = "pTogIaHA7YRm6cNDuEWoJVC4Omj373namjqDKh"
JIRA_URL = 'https://jira.zyfra.com'

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

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Пожалуйста, авторизуйтесь для начала работы. Введите вашу почту:")

@bot.message_handler(func=lambda message: message.chat.id not in user_sessions)
def ask_for_email(message):
    user_sessions[message.chat.id] = {'email': message.text}
    bot.reply_to(message, "Теперь введите ваш пароль:")

@bot.message_handler(func=lambda message: message.chat.id in user_sessions and 'email' in user_sessions[message.chat.id] and 'password' not in user_sessions[message.chat.id])
def ask_for_password(message):
    email = user_sessions[message.chat.id]['email']
    password = message.text

    if check_password(email, password):
        user_sessions[message.chat.id]['password'] = password
        bot.reply_to(message, "Авторизация успешна! Теперь вы можете использовать команду /get_all для получения всех задач.")
    else:
        user_sessions.pop(message.chat.id, None)
        bot.reply_to(message, "Неверная почта или пароль. Попробуйте снова, введите вашу почту:")

@bot.message_handler(commands=['get_all'])
def get_all_issues(message):
    if message.chat.id not in user_sessions or 'email' not in user_sessions[message.chat.id] or 'password' not in user_sessions[message.chat.id]:
        bot.reply_to(message, "Пожалуйста, сначала авторизуйтесь. Введите вашу почту:")
        return

    user_email = user_sessions[message.chat.id]['email']
    url = f"{JIRA_URL}/rest/api/2/search"
    jql_query = 'issuetype=Epic AND project="DP00001" ORDER BY duedate'
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
        project_issues = [
            issue['key'] for issue in issues
            if 'fields' in issue and 'customfield_12911' in issue['fields'] and
               issue['fields']['customfield_12911'] and
               issue['fields']['customfield_12911']['emailAddress'] == user_email
        ][:9]

        if project_issues:
            markup = InlineKeyboardMarkup()
            for issue_key in project_issues:
                markup.add(InlineKeyboardButton(issue_key, callback_data=f'issue_{issue_key}'))
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
        assignee = fields.get('assignee', {}).get('displayName', 'Unassigned') if fields.get('assignee') else 'Unassigned'
        reporter = fields.get('reporter', {}).get('displayName', 'Unknown Reporter') if fields.get('reporter') else 'Unknown Reporter'
        manager = fields.get('customfield_12911', {}).get('displayName', 'Unknown Manager') if fields.get('customfield_12911') else 'Unknown Manager'

        created_date = fields.get('created')
        updated_date = fields.get('updated')
        due_date = fields.get('duedate', 'No Due Date')

        response_text = (
            f"Issue Key: {json_data['key']}\n"
            f"Project: {project_name}\n"
            f"Assignee: {assignee}\n"
            f"Reporter: {reporter}\n"
            f"Project Manager: {manager}\n"
            f"Created Date: {created_date}\n"
            f"Updated Date: {updated_date}\n"
            f"Due Date: {due_date}\n"
        )

        bot.send_message(message.chat.id, response_text)
    except requests.exceptions.RequestException as e:
        bot.send_message(message.chat.id, f"Ошибка при выполнении запроса: {e}")
    except json.JSONDecodeError:
        bot.send_message(message.chat.id, "Ошибка при декодировании JSON.")

bot.polling()
