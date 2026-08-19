import os
import requests

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID = "-1004424116637"

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

data = {
    "chat_id": CHANNEL_ID,
    "text": "🤖 Bahadır İndirim Botu başarıyla bağlandı!\n\nPC kapalı olsa bile çalışacak sistem hazır. 🚀"
}

response = requests.post(url, data=data)

print(response.json())
