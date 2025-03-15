import time
import asyncio
import re
import os
from typing import Optional, Dict
from telegram.constants import ParseMode
import threading

# Semáforo global para la creación de threads
thread_creation_lock = threading.Lock()

class ConversationManager:
    """Manages global state and orchestrates bot-to-bot conversations."""
    def __init__(self):
        self.all_bots: Dict[str, 'Bot'] = {}  # Dictionary to hold all bots by name
        self.active_conversation: Dict[int, dict] = {}  # {group_id: conversation_state}
        self.bot_order: list[str] = []  # List of bot names in fixed order
        self.current_bot_index: int = 0  # Tracks the current bot in the rotation
        self.last_assistant_response = None
        self.threads: Dict[int, str] = {}  # {group_id: thread_id} almacena los hilos en memoria
        self.user_data: Dict[int, Dict[str, str]] = {}  # Almacena información de usuarios {group_id: {name: nombre, ...}}
        self._thread_locks: Dict[int, asyncio.Lock] = {}  # Locks para cada group_id
    
    def register_bots(self, bots: Dict[str, 'Bot']):
        """Registra los bots disponibles en la instancia de ConversationManager."""
        self.all_bots = bots
        print("[DEBUG] Bots registrados correctamente.")
        
        # Sincronizar threads hacia los handlers
        for bot_name, bot in self.all_bots.items():
            bot.assistant_handler.threads = self.threads

    def is_active(self, group_id: int) -> bool:
        """Verifica si un group_id tiene una conversación activa."""
        return group_id in self.threads

    def get_thread_id(self, group_id: int) -> Optional[str]:
        """Obtiene el thread_id asociado a un group_id si existe."""
        return self.threads.get(group_id)

    async def get_thread_lock(self, group_id: int) -> asyncio.Lock:
        """Obtiene o crea un lock para el group_id específico."""
        if group_id not in self._thread_locks:
            self._thread_locks[group_id] = asyncio.Lock()
        return self._thread_locks[group_id]

    async def set_thread_id(self, group_id: int, thread_id: str = None) -> Optional[str]:
        """
        Asocia un thread_id a un group_id en la memoria del proyecto.
        Devuelve el thread_id resultante.
        """
        # Adquirir lock específico para este group_id
        lock = await self.get_thread_lock(group_id)
        async with lock:
            # Verificar de nuevo si ya existe un thread_id (podría haberse creado mientras esperábamos el lock)
            existing_thread_id = self.get_thread_id(group_id)
            if existing_thread_id:
                print(f"[DEBUG] Se encontró thread_id existente mientras esperaba lock: {existing_thread_id}")
                return existing_thread_id
            
            if thread_id and isinstance(thread_id, str):  # Asegurar que el thread_id es válido antes de asignarlo
                self.threads[group_id] = thread_id
                print(f"[DEBUG] Se asoció thread_id: {thread_id} al group_id: {group_id}")
                
                # Sincronizar con los handlers
                for bot_name, bot in self.all_bots.items():
                    bot.assistant_handler.threads[group_id] = thread_id
                
                return thread_id
            else:
                print(f"[DEBUG] No se encontró un thread_id para group_id: {group_id}, creando uno nuevo.")
                try:
                    next_bot = next(iter(self.all_bots.values()))
                    thread = next_bot.assistant_handler.client.beta.threads.create()
                    if thread and hasattr(thread, 'id') and thread.id:
                        new_thread_id = thread.id
                        self.threads[group_id] = new_thread_id
                        
                        # Sincronizar con los handlers
                        for bot_name, bot in self.all_bots.items():
                            bot.assistant_handler.threads[group_id] = new_thread_id
                        
                        print(f"[DEBUG] Nuevo thread_id creado: {new_thread_id} para group_id: {group_id}")
                        return new_thread_id
                    else:
                        print(f"[ERROR] No se pudo crear un thread válido para group_id: {group_id}")
                        return None
                except Exception as e:
                    print(f"[ERROR] Error al crear thread: {e}")
                    return None

    def get_next_bot(self) -> Optional[str]:
        """Obtiene el siguiente bot disponible en la rotación para responder."""
        if not self.all_bots:
            return None
        return next(iter(self.all_bots.keys()))

    def prepare_text_for_html(self, text):
        """
        Prepara el texto para ser enviado con formato HTML a Telegram.
        Enfoque optimizado para formatear correctamente títulos y listas.
        """
        # Paso 1: Eliminar los marcadores ### de encabezados
        text = re.sub(r'###\s+', '', text)
        
        # Paso 2: Convertir **texto** a <b>texto</b> (negrita)
        # Primero capturamos los casos especiales con asteriscos y espacios
        text = re.sub(r'\*\*\s+([^*]+)\s+\*\*', r'<b>\1</b>', text)
        # Luego los casos normales
        text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
        
        # Paso 3: Eliminar espacios antes de los dos puntos
        text = re.sub(r'\s+:', r':', text)
        
        # Paso 4: Formatear listas con viñetas
        # Primero convertimos listas con • que ya existan
        text = re.sub(r'•\s+', '• ', text)
        # Convertir guiones en viñetas
        text = re.sub(r'\n\s*-\s+', '\n• ', text)
        
        # Paso 5: Manejar elementos numerados
        text = re.sub(r'(\d+)\.\s+<b>([^<]+)</b>:', r'\1. <b>\2</b>:', text)
        
        # Paso 6: Aplicar espaciado consistente después de las viñetas
        text = re.sub(r'•\s+', '• ', text)
        
        # Paso 7: Asegurar que no haya etiquetas HTML escapadas
        text = text.replace('&lt;b&gt;', '<b>').replace('&lt;/b&gt;', '</b>')
        text = text.replace('&lt;i&gt;', '<i>').replace('&lt;/i&gt;', '</i>')
        
        return text
    
    def save_user_info(self, group_id: int, name: str):
        """Guarda información del usuario para personalizar respuestas."""
        if group_id not in self.user_data:
            self.user_data[group_id] = {}
        self.user_data[group_id]['name'] = name
        print(f"[DEBUG] Guardada información del usuario {name} para group_id: {group_id}")

    def get_user_name(self, group_id: int) -> str:
        """Obtiene el nombre del usuario si está disponible."""
        if group_id in self.user_data and 'name' in self.user_data[group_id]:
            return self.user_data[group_id]['name']
        return ""

    async def handle_turn(self, group_id: int, message: str) -> None:
        """Procesa un mensaje para el grupo correspondiente."""
        print(f"[DEBUG] Procesando mensaje para group_id: {group_id}")

        # Extraer información del usuario si está en el formato esperado
        user_name_match = re.search(r'\[INFORMACIÓN DEL USUARIO: Nombre=([^\]]+)\]', message)
        if user_name_match:
            user_name = user_name_match.group(1)
            self.save_user_info(group_id, user_name)
            # Eliminar la etiqueta de información del usuario del mensaje
            message = re.sub(r'\[INFORMACIÓN DEL USUARIO: Nombre=[^\]]+\]\s*\n*', '', message)
            print(f"[DEBUG] Mensaje procesado para {user_name}: {message}")

        # Obtener o crear thread_id de manera segura
        thread_id = await self.set_thread_id(group_id, self.get_thread_id(group_id))
        if not thread_id:
            print(f"[ERROR] No se pudo obtener o crear un thread_id válido para group_id: {group_id}")
            return

        print(f"[DEBUG] Usando thread_id: {thread_id} para group_id: {group_id}")

        next_bot_name = self.get_next_bot()
        if not next_bot_name:
            print("[ERROR] No hay bots disponibles para responder.")
            return

        next_bot = self.all_bots[next_bot_name]
        user_name = self.get_user_name(group_id)

        # Forzar la actualización del thread_id en el handler
        if group_id not in next_bot.assistant_handler.threads or next_bot.assistant_handler.threads.get(group_id) != thread_id:
            print(f"[DEBUG] Sincronizando thread_id con el handler: {thread_id}")
            next_bot.assistant_handler.threads[group_id] = thread_id

        async def send_to_telegram(chunk):
            """Envía la respuesta del bot al usuario/grupo correcto con formato HTML."""
            try:
                # Personalizar la respuesta con el nombre del usuario si está disponible
                if user_name:
                    # Añadir personalización inteligente - solo si la respuesta parece apropiada
                    if re.search(r'^(hola|buenos días|buenas tardes|buenas noches)', chunk.lower()):
                        chunk = chunk.replace('Hola', f'Hola {user_name}', 1)
                    elif 'espero' in chunk.lower() and '!' in chunk:
                        chunk = chunk.replace('!', f", {user_name}!", 1)
                
                # Convertir texto a formato HTML para mejor compatibilidad
                html_text = self.prepare_text_for_html(chunk)
                await next_bot.application.bot.send_message(
                    chat_id=group_id, 
                    text=html_text,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                # Si falla con HTML, intentar con Markdown
                print(f"[WARN] Error enviando mensaje con HTML: {e}")
                try:
                    await next_bot.application.bot.send_message(
                        chat_id=group_id, 
                        text=chunk,
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    # Si falla con Markdown, intentar sin formato
                    print(f"[WARN] Error enviando mensaje con Markdown: {e}")
                    try:
                        await next_bot.application.bot.send_message(
                            chat_id=group_id, 
                            text=chunk
                        )
                    except Exception as e:
                        print(f"[ERROR] Error enviando mensaje a Telegram: {e}")

        try:
            await next_bot.assistant_handler.stream_response(group_id, message, send_to_telegram)
        except Exception as e:
            print(f"[ERROR] Error durante el procesamiento del mensaje: {e}")
    
    async def handle_image(self, group_id: int, message: str, image_base64: str, image_path: str) -> None:
        """Procesa un mensaje que contiene una imagen."""
        print(f"[DEBUG] Procesando imagen para group_id: {group_id}")
        
        # Extraer información del usuario
        user_name_match = re.search(r'\[INFORMACIÓN DEL USUARIO: Nombre=([^\]]+)\]', message)
        if user_name_match:
            user_name = user_name_match.group(1)
            self.save_user_info(group_id, user_name)
            # Eliminar la etiqueta de información del usuario del mensaje
            message = re.sub(r'\[INFORMACIÓN DEL USUARIO: Nombre=[^\]]+\]\s*\n*', '', message)
        
        # Obtener o crear thread_id de manera segura
        thread_id = await self.set_thread_id(group_id, self.get_thread_id(group_id))
        if not thread_id:
            print(f"[ERROR] No se pudo obtener o crear un thread_id válido para group_id: {group_id}")
            return
        
        print(f"[DEBUG] Usando thread_id: {thread_id} para group_id: {group_id}")
            
        next_bot_name = self.get_next_bot()
        if not next_bot_name:
            print("[ERROR] No hay bots disponibles para responder.")
            return
            
        next_bot = self.all_bots[next_bot_name]
        user_name = self.get_user_name(group_id)
        
        # Forzar la actualización del thread_id en el handler
        if group_id not in next_bot.assistant_handler.threads or next_bot.assistant_handler.threads.get(group_id) != thread_id:
            print(f"[DEBUG] Sincronizando thread_id con el handler: {thread_id}")
            next_bot.assistant_handler.threads[group_id] = thread_id
        
        # Mismo callback de envío que en handle_turn
        async def send_to_telegram(chunk):
            try:
                # Personalizar la respuesta con el nombre del usuario si está disponible
                if user_name:
                    # Añadir personalización inteligente - solo si la respuesta parece apropiada
                    if re.search(r'^(hola|buenos días|buenas tardes|buenas noches)', chunk.lower()):
                        chunk = chunk.replace('Hola', f'Hola {user_name}', 1)
                    elif 'espero' in chunk.lower() and '!' in chunk:
                        chunk = chunk.replace('!', f", {user_name}!", 1)
                
                # Convertir texto a formato HTML para mejor compatibilidad
                html_text = self.prepare_text_for_html(chunk)
                await next_bot.application.bot.send_message(
                    chat_id=group_id, 
                    text=html_text,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                print(f"[WARN] Error enviando mensaje con HTML: {e}")
                try:
                    await next_bot.application.bot.send_message(
                        chat_id=group_id, 
                        text=chunk,
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    try:
                        await next_bot.application.bot.send_message(
                            chat_id=group_id, 
                            text=chunk
                        )
                    except Exception as e:
                        print(f"[ERROR] Error enviando mensaje a Telegram: {e}")
        
        try:
            # Llamar al método específico para imágenes en AssistantHandler
            await next_bot.assistant_handler.stream_image_response(
                group_id, message, image_base64, image_path, send_to_telegram
            )
        except Exception as e:
            print(f"[ERROR] Error durante el procesamiento de la imagen: {e}")
            # Intentar enviar un mensaje de error
            try:
                await next_bot.application.bot.send_message(
                    chat_id=group_id,
                    text=f"⚠️ Lo siento, hubo un problema al procesar la imagen. Error: {str(e)}",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass  # Si esto también falla, lo dejamos pasar
            
    def end_conversation(self, group_id: int) -> bool:
        """Finaliza una conversación activa."""
        if group_id in self.threads:
            thread_id = self.threads.pop(group_id)
            
            # Limpiar el thread_id de todos los handlers
            for bot_name, bot in self.all_bots.items():
                if group_id in bot.assistant_handler.threads:
                    del bot.assistant_handler.threads[group_id]
            
            # No eliminamos los datos del usuario para mantener la personalización
            return True
        return False