import time
import asyncio
import re
import os
import base64
from typing import Optional, Dict, Callable, Any


def clean_text_and_split(text):
    """
    Divide el texto en párrafos formateados preservando los espacios originales.
    Respeta markdown y formatos de Telegram como **negrita**.
    """
    # Pre-procesamiento para asegurar que ciertos patrones se preservan intactos
    
    # Conservar intactos los patrones de títulos con 📌
    processed_parts = []
    parts = re.split(r'(📌\s+[^:]+\s*:)', text)
    for i, part in enumerate(parts):
        if i % 2 == 1:  # Es un título con 📌
            processed_parts.append(part)
        else:
            processed_parts.append(part)
    
    text = ''.join(processed_parts)
    
    # Dividir el texto por saltos de línea que sean significativos
    paragraphs = re.split(r'\n\n+', text)
    
    # Filtrar párrafos vacíos
    paragraphs = [p for p in paragraphs if p.strip()]
    
    return paragraphs


def process_markdown(text):
    """
    Procesa el texto para asegurar que el formato markdown de Telegram es correcto.
    Maneja cuidadosamente el formato para evitar problemas con asteriscos y espacios.
    """
    # Corregir múltiples espacios en todo el texto
    text = re.sub(r' +', ' ', text)
    
    # Eliminar espacios antes de los dos puntos
    text = re.sub(r'\s+:', r':', text)
    
    # Corregir el patrón problemático: "1. **  Texto  **:" -> "1. *Texto*:"
    text = re.sub(r'(\d+)\.\s+\*\*\s+([^*]+)\s+\*\*\s*:', r'\1. *\2*:', text)
    
    # Corregir el formato de encabezados
    text = re.sub(r'###\s+([^#\n]+)', r'*\1*', text)
    
    # Corregir el formato de elementos numerados
    text = re.sub(r'(\d+)\.\s+([^:]+):', r'\1. *\2*:', text)
    
    # Asegurarse de que los asteriscos para negrita estén correctamente formateados
    # Convertir **texto** a *texto* para Telegram (uno solo para negrita)
    text = re.sub(r'\*\*([^*]+)\*\*', r'*\1*', text)
    
    # Eliminar cualquier asterisco duplicado que pueda quedar
    text = text.replace('**', '*').replace('* *', '*')
    
    return text


class AssistantHandler:
    def __init__(self, client, assistant_id):
        self.client = client
        self.assistant_id = assistant_id
        self.thread_id = None
        self.message_history = []
        self.threads: Dict[int, str] = {}  # Almacenar {group_id: thread_id} en memoria
        self._in_progress_runs: Dict[str, bool] = {}  # Seguimiento de runs en progreso por thread_id

    async def stream_response(self, group_id: int, message_str: str, send_to_telegram):
        """Envía un mensaje al asistente y transmite la respuesta al usuario."""
        thread_id = self.threads.get(group_id)

        if not thread_id:
            print(f"[DEBUG] No se encontró un thread_id para group_id: {group_id}, no se creará aquí")
            return

        print(f"[DEBUG] Usando thread_id: {thread_id} para group_id: {group_id}")

        # Verificar si ya hay un run en progreso para este thread
        if self._in_progress_runs.get(thread_id, False):
            print(f"[WARN] Ya hay un run en progreso para thread_id: {thread_id}")
            await send_to_telegram("⏳ Estoy procesando una solicitud anterior. Por favor, espera un momento.")
            return

        try:
            # Marcar este thread como en progreso
            self._in_progress_runs[thread_id] = True
            
            # Enviar el mensaje al asistente
            try:
                self.client.beta.threads.messages.create(
                    thread_id=thread_id,
                    role="user",
                    content=message_str
                )
                self.message_history.append({"role": "user", "content": message_str})
            except Exception as e:
                print(f"[ERROR] Error enviando mensaje al asistente: {e}")
                self._in_progress_runs[thread_id] = False
                return

            try:
                with self.client.beta.threads.runs.create_and_stream(
                    thread_id=thread_id,
                    assistant_id=self.assistant_id,
                ) as event_handler:
                    buffer = ""
                    accumulated_text = ""
                    
                    for partial_text in event_handler.text_deltas:
                        buffer += partial_text
                        
                        # Cuando detectamos un doble salto de línea, enviamos el párrafo
                        if '\n\n' in buffer:
                            parts = buffer.split('\n\n', 1)
                            accumulated_text += parts[0]
                            
                            # Simplemente enviamos el texto sin procesar markdown
                            processed_text = accumulated_text.strip()
                            
                            if processed_text:
                                await send_to_telegram(processed_text)
                                self.message_history.append({"role": "assistant", "content": processed_text})
                            
                            buffer = parts[1]
                            accumulated_text = ""
                    
                    # Enviar cualquier texto restante en el buffer
                    if buffer.strip():
                        processed_text = buffer.strip()
                        await send_to_telegram(processed_text)
                        self.message_history.append({"role": "assistant", "content": processed_text})
                        
            except Exception as e:
                print(f"[ERROR] Error durante el streaming: {e}")
        finally:
            # Asegurarse de que el thread se marque como no en progreso, incluso si hay error
            self._in_progress_runs[thread_id] = False

    async def stream_image_response(self, group_id: int, message_str: str, image_base64: str, image_path: str, send_to_telegram):
        """Método utilizando Files API con timeout y manejo mejorado de queued"""
        thread_id = self.threads.get(group_id)

        if not thread_id:
            print(f"[DEBUG] No se encontró un thread_id para group_id: {group_id}, no se creará aquí")
            return

        print(f"[DEBUG] Usando thread_id: {thread_id} para group_id: {group_id} con imagen")

        # Verificar si ya hay un run en progreso para este thread
        if self._in_progress_runs.get(thread_id, False):
            print(f"[WARN] Ya hay un run en progreso para thread_id: {thread_id}")
            await send_to_telegram("⏳ Estoy procesando una solicitud anterior. Por favor, espera un momento.")
            return

        try:
            # Marcar este thread como en progreso
            self._in_progress_runs[thread_id] = True
            
            # Primero creamos un mensaje informativo para el usuario
            await send_to_telegram("🔍 Estoy analizando la imagen. Esto puede tardar unos momentos...")

            # Guardar información del modelo que usa el asistente para diagnóstico
            try:
                assistant_info = self.client.beta.assistants.retrieve(self.assistant_id)
                model_name = getattr(assistant_info, 'model', 'desconocido')
                print(f"[DEBUG] Modelo del asistente: {model_name}")
            except Exception as e:
                model_name = "desconocido"
                print(f"[DEBUG] No se pudo obtener información del modelo: {e}")

            # Intentar subir el archivo a OpenAI para obtener el file_id
            try:
                print(f"[DEBUG] Subiendo imagen a OpenAI Files API...")
                
                # Crear el mensaje del usuario con el texto
                self.client.beta.threads.messages.create(
                    thread_id=thread_id,
                    role="user",
                    content=message_str
                )
                print("[DEBUG] Mensaje de texto enviado correctamente")
                
                # Subir la imagen
                with open(image_path, 'rb') as f:
                    file_upload = self.client.files.create(
                        file=f,
                        purpose="assistants"
                    )
                
                file_id = file_upload.id
                print(f"[DEBUG] Archivo subido exitosamente, ID: {file_id}")
                
                # Intentar con el formato image_file directamente (el que funcionó antes)
                try:
                    self.client.beta.threads.messages.create(
                        thread_id=thread_id,
                        role="user",
                        content=[
                            {
                                "type": "image_file",
                                "image_file": {
                                    "file_id": file_id
                                }
                            }
                        ]
                    )
                    print("[DEBUG] Mensaje con imagen enviado correctamente")
                except Exception as e:
                    print(f"[ERROR] Error enviando mensaje con image_file: {e}")
                    # Intentar con formato alternativo como último recurso
                    try:
                        print("[DEBUG] Intentando formato alternativo...")
                        with open(image_path, "rb") as img_file:
                            b64_image = base64.b64encode(img_file.read()).decode()
                        
                        self.client.beta.threads.messages.create(
                            thread_id=thread_id,
                            role="user",
                            content=[
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{b64_image}"
                                    }
                                }
                            ]
                        )
                        print("[DEBUG] Formato alternativo tuvo éxito")
                    except Exception as e2:
                        print(f"[ERROR] Error con el formato alternativo: {e2}")
                        await send_to_telegram("❌ No pude procesar la imagen. Por favor, intenta con otra imagen o consulta sin imagen.")
                        self._in_progress_runs[thread_id] = False
                        return
                
            except Exception as e:
                print(f"[ERROR] Error subiendo archivo a OpenAI: {e}")
                await send_to_telegram(f"⚠️ No se pudo subir la imagen. Error: {str(e)}")
                self._in_progress_runs[thread_id] = False
                return

            # Registramos en el historial
            self.message_history.append({
                "role": "user", 
                "content": f"{message_str} [IMAGEN adjuntada como archivo: {file_id}]"
            })

            # Ejecutar el asistente para procesar el mensaje y la imagen
            try:
                print("[DEBUG] Iniciando análisis de la imagen...")
                
                # Crear un run para el thread
                run = self.client.beta.threads.runs.create(
                    thread_id=thread_id,
                    assistant_id=self.assistant_id
                )
                
                # Esperar a que termine la ejecución
                run_status = run.status
                run_id = run.id
                
                # Variables para controlar el timeout
                max_wait_time = 120  # Segundos máximos de espera (2 minutos)
                start_time = time.time()
                queued_message_sent = False
                queued_time_start = None
                
                while run_status not in ["completed", "failed", "cancelled", "expired"]:
                    elapsed_time = time.time() - start_time
                    print(f"[DEBUG] Estado actual del run: {run_status}, tiempo transcurrido: {elapsed_time:.1f}s")
                    
                    # Manejar el estado "queued" específicamente
                    if run_status == "queued":
                        if not queued_message_sent:
                            await send_to_telegram("🔄 Tu solicitud está en cola. Esto puede tardar un momento debido a alta demanda...")
                            queued_message_sent = True
                            queued_time_start = time.time()
                        
                        # Si ha estado en cola por más de 30 segundos, informar al usuario
                        if queued_time_start and (time.time() - queued_time_start > 30):
                            await send_to_telegram("⏳ Tu solicitud sigue en cola. A veces el procesamiento de imágenes puede tardar un poco más. Gracias por tu paciencia.")
                            # Reset para no enviar este mensaje de nuevo
                            queued_time_start = None
                    
                    # Verificar si excedimos el tiempo máximo de espera
                    if elapsed_time > max_wait_time:
                        print(f"[WARN] Excedido tiempo máximo de espera ({max_wait_time}s)")
                        await send_to_telegram("⚠️ El análisis está tomando más tiempo del esperado. Puedo continuar esperando o puedes cancelar e intentarlo nuevamente. ¿Quieres continuar esperando?")
                        
                        # Extender el tiempo de espera
                        max_wait_time += 60  # Añadir un minuto más
                    
                    # Esperar un poco antes de verificar de nuevo (con backoff exponencial)
                    base_wait = min(5, 1 + (elapsed_time / 20))  # Incrementar el tiempo de espera gradualmente
                    await asyncio.sleep(base_wait)
                    
                    # Obtener estado actualizado
                    try:
                        run = self.client.beta.threads.runs.retrieve(
                            thread_id=thread_id,
                            run_id=run_id
                        )
                        run_status = run.status
                    except Exception as e:
                        print(f"[ERROR] Error al obtener estado del run: {e}")
                        # Si hay error al obtener estado, incrementar el tiempo de espera
                        await asyncio.sleep(5)
                
                # Informar sobre estado final
                print(f"[DEBUG] Run completado con estado: {run_status}, tiempo total: {time.time() - start_time:.1f}s")
                
                if run_status == "completed":
                    # Obtener los mensajes de respuesta
                    messages = self.client.beta.threads.messages.list(
                        thread_id=thread_id
                    )
                    
                    # Extraer el último mensaje del asistente
                    assistant_messages = []
                    for msg in messages.data:
                        if msg.role == "assistant" and msg.run_id == run_id:
                            # Extraer el contenido del mensaje
                            for content_item in msg.content:
                                if content_item.type == "text":
                                    assistant_messages.append(content_item.text.value)
                    
                    # Enviar las respuestas del asistente
                    if assistant_messages:
                        for msg_text in assistant_messages:
                            # Sin procesar markdown, simplemente enviar el texto tal cual
                            await send_to_telegram(msg_text)
                            self.message_history.append({"role": "assistant", "content": msg_text})
                    else:
                        await send_to_telegram(f"⚠️ El asistente no generó una respuesta para la imagen. Modelo usado: {model_name}")
                else:
                    await send_to_telegram(f"❌ El análisis falló con estado: {run_status}. Modelo usado: {model_name}")
                    
            except Exception as e:
                print(f"[ERROR] Error durante el análisis de la imagen: {e}")
                await send_to_telegram(f"Lo siento, ocurrió un error al analizar la imagen: {str(e)}")
        finally:
            # Asegurarse de que el thread se marque como no en progreso, incluso si hay error
            self._in_progress_runs[thread_id] = False

    def trim_message_history(self):
        """Mantiene el historial de mensajes limitado a los últimos 20 mensajes."""
        max_messages = 20
        if len(self.message_history) > max_messages:
            self.message_history = self.message_history[-max_messages:]