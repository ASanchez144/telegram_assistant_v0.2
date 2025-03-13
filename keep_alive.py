from flask import Flask
import threading
import os

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!", 200

def run_server():
    port = int(os.getenv("PORT", 8080))  # Render espera un puerto abierto
    app.run(host="0.0.0.0", port=port)

# Ejecutar el servidor en un hilo separado para no interferir con `chatbot`
threading.Thread(target=run_server, daemon=True).start()
