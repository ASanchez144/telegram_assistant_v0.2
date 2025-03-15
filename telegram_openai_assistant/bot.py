import asyncio
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, PicklePersistence
from telegram import Update
from openai import OpenAI

from .conversation_manager import ConversationManager
from .assistant_handler import AssistantHandler
from .config import telegram_token_bots, assistant_id_bots
from .handlers import BotHandlers

from .config import client_api_key

import threading
import subprocess
import os

def start_keep_alive():
    """Ejecuta keep_alive.py en segundo plano"""
    try:
        subprocess.Popen(["python", "keep_alive.py"])
        print("Keep-alive ejecutado correctamente")
    except Exception as e:
        print(f"No se pudo iniciar keep_alive: {e}")


# Inicia el servidor Flask antes de lanzar el bot
try:
    start_keep_alive()
except Exception as e:
    print(f"No se pudo iniciar keep_alive: {e}")


client = OpenAI(api_key=client_api_key)



class Bot:
    def __init__(self, bot_name: str, token: str, assistant_id: str, manager: ConversationManager):
        """Initialize the bot application with a token and assistant_id"""
        self.token = token
        self.bot_name = bot_name
        self.assistant_id = assistant_id
        self.manager = manager
        self.assistant_handler = AssistantHandler(client, assistant_id)
        
        # Configurar persistencia de datos para guardar información de usuario
        persistence_directory = os.path.join("data", f"{bot_name}_data")
        os.makedirs(persistence_directory, exist_ok=True)
        persistence_path = os.path.join(persistence_directory, "bot_data.pickle")
        
        # Crear el objeto de persistencia - compatible con python-telegram-bot 21.x
        # Verificar versión y ajustar parámetros
        try:
            # Intentar con la API más reciente
            persistence = PicklePersistence(
                filepath=persistence_path,
                chat_data_json=True,  # En lugar de store_chat_data
                user_data_json=True   # Guardar también datos de usuario
            )
        except TypeError:
            try:
                # Intentar con la API alternativa
                persistence = PicklePersistence(
                    filepath=persistence_path
                )
                print(f"Usando PicklePersistence básico para {bot_name}")
            except Exception as e:
                print(f"Error al crear persistencia, continuando sin ella: {e}")
                persistence = None
        
        self.handlers = BotHandlers(bot_name, assistant_id, token, manager)
        
        # Construir la aplicación con o sin persistencia
        if persistence:
            self.application = ApplicationBuilder().token(token).persistence(persistence).pool_timeout(60.0).build()
        else:
            self.application = ApplicationBuilder().token(token).pool_timeout(60.0).build()
            
        self.setup_handlers()

    def setup_handlers(self):
        """Sets up the command and message handlers."""
        self.application.add_handler(CommandHandler("start", self.handlers.start))
        self.application.add_handler(CommandHandler("help", self.handlers.help_command))
        self.application.add_handler(CommandHandler("end", self.end_conversation))
        
        # Añadir manejador para fotos
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handlers.process_photo))
        
        # Manejador para mensajes de texto (debe ir después de los comandos y fotos)
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.process_message))

    async def end_conversation(self, update: Update, context):
        """End the current conversation."""
        await self.handlers.end_conversation(update, context)

    async def send_message(self, message: str):
        """Send a message to the specified chat_id"""
        await self.application.bot.send_message(chat_id=self.chat_id, text=message)

    async def start(self):
        """Start the bot."""
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()

    async def stop(self):
        """Stop the bot."""
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()


async def start_bots(manager: ConversationManager):
    """Runs all bot applications concurrently."""
    print("Iniciando aplicación de bots...")
    names = ["Regen", "Degen"]
    bots = {}
    
    # Intentar crear cada bot
    for name, token, assistant_id in zip(names, telegram_token_bots, assistant_id_bots):
        try:
            print(f"Creando bot {name}...")
            bot = Bot(name, token, assistant_id, manager)
            bots[name] = bot
        except Exception as e:
            print(f"Error al crear bot {name}: {e}")
    
    if not bots:
        print("No se pudo crear ningún bot. Saliendo.")
        return
        
    manager.register_bots(bots)
    
    # Start all bots concurrently
    print("Iniciando todos los bots...")
    await asyncio.gather(*(bot.start() for bot in bots.values()))

    try:
        # Keep the event loop running until interrupted
        print("Bots en funcionamiento. Presiona Ctrl+C para detener.")
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("Bots apagándose...")
    except Exception as e:
        print(f"Error inesperado: {e}")
    finally:
        print("Limpiando recursos...")
        # Stop polling for all bots
        stop_tasks = [bot.application.updater.stop() for bot in bots.values()]
        await asyncio.gather(*stop_tasks)

        # Stop all applications
        stop_tasks = [bot.application.stop() for bot in bots.values()]
        await asyncio.gather(*stop_tasks)

        # Shutdown all applications
        shutdown_tasks = [bot.application.shutdown() for bot in bots.values()]
        await asyncio.gather(*shutdown_tasks)


def main():
    """Main function to run the bots."""
    print("Iniciando bots de telegram...")
    
    # Asegurar carpetas necesarias
    os.makedirs("data", exist_ok=True)
    temp_image_folder = os.path.join("data", "temp_images")
    os.makedirs(temp_image_folder, exist_ok=True)
    
    manager = ConversationManager()
    
    try:
        asyncio.run(start_bots(manager))
    except Exception as e:
        print(f"Error en la ejecución principal: {e}")


if __name__ == "__main__":
    main()

# Asegurar que main es exportada correctamente
__all__ = ['main']