from telegram.ext import CallbackContext
from telegram import Update
from telegram.constants import ParseMode
import os
import asyncio


class BotHandlers:
    def __init__(self, bot_name: str, assistant_id: str, telegram_id: str, manager):
        self.assistant_id = assistant_id
        self.telegram_id = telegram_id
        self.bot_name = bot_name
        self.manager = manager
        self.temp_image_folder = os.path.join("data", "temp_images")
        # Crear carpeta para imágenes temporales si no existe
        os.makedirs(self.temp_image_folder, exist_ok=True)

    async def start(self, update: Update, context: CallbackContext) -> None:
        """Envía un mensaje de bienvenida e inicia preguntas para conocer al usuario."""
        # Guardar el nombre del usuario en los datos del chat
        user_name = update.message.from_user.first_name
        if not context.chat_data.get('user_info'):
            context.chat_data['user_info'] = {}
        context.chat_data['user_info']['name'] = user_name
        
        welcome_message = (
            f"👶✨ ¡Bienvenido/a {user_name} a tu Asistente Familiar! 🤰🤱\n\n"
            f"Hola {user_name}, soy tu asistente virtual diseñado para ayudarte en cada etapa del embarazo y la crianza de tu bebé. 💙\n\n"
            "📌 ¿Tienes dudas sobre el embarazo, el parto o el cuidado de tu peque? Estoy aquí para responderlas.\n"
            "📌 ¿Necesitas consejos sobre alimentación, sueño o desarrollo infantil? ¡Pregúntame!\n"
            "📌 <b>¡Ahora también puedes enviarme fotos!</b> Puedo analizar ecografías, resultados médicos o cualquier otra imagen relacionada.\n\n"
            "Antes de empezar, me gustaría conocerte mejor para ofrecerte la mejor ayuda posible. 😊\n\n"
            "📋 <b>Por favor, responde a estas preguntas:</b>"
        )

        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text=welcome_message,
            parse_mode=ParseMode.HTML
        )

        # Preguntas personalizadas para el usuario
        questions = [
            "1️⃣ ¿Cuál es tu edad? 🎂",
            "2️⃣ ¿Eres hombre o mujer? ⚤",
            "3️⃣ ¿Estás embarazada? 🤰 (Sí/No)",
            "4️⃣ ¿Tienes hijos? 👶 (Sí/No)",
            "5️⃣ Si tienes hijos, ¿cuántos tienes y qué edades tienen? 🧒👧"
        ]

        for question in questions:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text=question,
                parse_mode=ParseMode.HTML
            )

    async def help_command(self, update: Update, context: CallbackContext) -> None:
        """Sends a help message to the user."""
        user_name = update.message.from_user.first_name
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                f"Hola {user_name}, aquí tienes algunas formas de utilizar este asistente:\n\n"
                "• Envíame preguntas sobre embarazo, bebés, o crianza\n"
                "• <b>Puedes enviarme fotos</b> para que las analice (ecografías, síntomas, etc.)\n"
                "• Cuando envíes una foto, puedes añadir una descripción o pregunta\n"
                "• Usa /start para iniciar una nueva conversación\n"
                "• Usa /end para finalizar la conversación actual"
            ),
            parse_mode=ParseMode.HTML
        )

    async def download_photo(self, update: Update, context: CallbackContext):
        """Descarga la foto de mayor resolución enviada al bot."""
        # Obtener la mejor calidad de foto disponible (el último elemento de la lista)
        photo_file = await context.bot.get_file(update.message.photo[-1].file_id)
        
        # Crear un nombre único para el archivo
        chat_id = update.effective_chat.id
        user_id = update.message.from_user.id
        file_id = update.message.photo[-1].file_id
        timestamp = update.message.date.timestamp()
        file_name = f"{chat_id}_{user_id}_{timestamp}_{file_id[-10:]}.jpg"
        file_path = os.path.join(self.temp_image_folder, file_name)
        
        # Descargar la foto
        await photo_file.download_to_drive(file_path)
        print(f"[DEBUG] Imagen descargada en: {file_path}")
        
        return file_path

    async def process_photo(self, update: Update, context: CallbackContext) -> None:
        """Maneja fotos enviadas por el usuario."""
        if update.message is None or update.message.photo is None:
            return
        
        chat_id = update.effective_chat.id
        user_name = update.message.from_user.first_name
        
        # Guardar nombre de usuario
        if not context.chat_data.get('user_info'):
            context.chat_data['user_info'] = {}
        context.chat_data['user_info']['name'] = user_name
        
        # Informar al usuario que estamos procesando
        processing_message = await context.bot.send_message(
            chat_id=chat_id,
            text=f"📸 Procesando tu imagen, {user_name}... Un momento por favor.",
            parse_mode=ParseMode.HTML
        )
        
        try:
            # Descargar la foto
            image_path = await self.download_photo(update, context)
            
            # Obtener la descripción o pregunta del usuario (caption)
            caption = update.message.caption or "Analiza esta imagen y describe lo que ves."
            
            # Preparar contexto para el asistente con información del usuario
            user_context = f"[INFORMACIÓN DEL USUARIO: Nombre={user_name}]\n\n"
            enhanced_message = user_context + caption
            
            # Enviar mensaje de que estamos procesando pero sin la codificación
            print(f"[DEBUG] Procesando imagen: {image_path}")
            
            # Enviar la imagen y el mensaje al asistente - sin pre-codificar la imagen
            await self.manager.handle_image(chat_id, enhanced_message, None, image_path)
            
            # Eliminar mensaje de procesamiento
            await context.bot.delete_message(chat_id=chat_id, message_id=processing_message.message_id)
            
        except Exception as e:
            print(f"[ERROR] Error procesando foto: {e}")
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=processing_message.message_id,
                text=f"❌ Lo siento {user_name}, hubo un problema al procesar tu imagen. Por favor, intenta de nuevo.",
                parse_mode=ParseMode.HTML
            )

    async def process_message(self, update: Update, context: CallbackContext) -> None:
        """Handles incoming messages and delegates to ConversationManager."""
        if update.message is None:
            return  # No message to process

        if update.message.from_user.is_bot:
            print("El mensaje proviene de un bot, ignorando...")
            return  # Ignora mensajes enviados por bots
        
        # Obtener y guardar el nombre del usuario
        user_name = update.message.from_user.first_name
        if not context.chat_data.get('user_info'):
            context.chat_data['user_info'] = {}
        context.chat_data['user_info']['name'] = user_name
        
        chat_type = update.effective_chat.type
        group_id = update.effective_chat.id
        message_text = update.message.text
        print(f"Mensaje recibido de {update.message.from_user.username} ({user_name}): {message_text}")

        # Preparar contexto para el asistente con información del usuario
        user_context = f"[INFORMACIÓN DEL USUARIO: Nombre={user_name}]\n\n"
        enhanced_message = user_context + message_text

        if chat_type == "private":
            # No verificar menciones en chats privados
            if not self.manager.is_active(group_id):
                if group_id in self.manager.active_conversation:
                    await context.bot.send_message(
                        chat_id=group_id,
                        text=f"Estoy procesando tu solicitud {user_name}, un momento por favor...",
                        parse_mode=ParseMode.HTML
                    )
            await self.manager.handle_turn(group_id, enhanced_message)
        else:
            # Solo procesar mensajes en grupos si el bot está mencionado
            if update.message.entities:
                for entity in update.message.entities:
                    if entity.type == 'mention' and '@' + context.bot.username in message_text[entity.offset:entity.offset + entity.length]:
                        if not self.manager.is_active(group_id):
                            if self.manager.active_conversation(group_id, self.bot_name):
                                await context.bot.send_message(
                                    chat_id=group_id,
                                    text=f"Conversación iniciada por {self.bot_name} en el grupo {group_id}. Usa /end para terminar.",
                                    parse_mode=ParseMode.HTML
                                )
                        await self.manager.handle_turn(group_id, enhanced_message)

    async def end_conversation(self, update: Update, context: CallbackContext) -> None:
        """Ends the active conversation."""
        group_id = update.effective_chat.id
        user_name = update.message.from_user.first_name
        
        if self.manager.end_conversation(group_id):
            await context.bot.send_message(
                chat_id=group_id,
                text=f"Conversación finalizada por {self.bot_name}. ¡Hasta pronto {user_name}!",
                parse_mode=ParseMode.HTML
            )
        else:
            await context.bot.send_message(
                chat_id=group_id,
                text=f"No hay una conversación activa en este grupo para finalizar, {user_name}.",
                parse_mode=ParseMode.HTML
            )