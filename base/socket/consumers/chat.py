import asyncio

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from agent.client import AgentClient, AgentUnavailableError
from agent.helpers.lock import (
    TEXT_TURN_TTL_SECONDS,
    AgentBusyError,
    aacquire_session_lock,
    arelease_session_lock,
)
from agent.helpers.runtime import build_session_service
from agent.voice import VoiceSession
from app.utils.logger import logger
from base.socket.events import MODE_CHANGED, NOTIFICATION_CREATED
from base.socket.services.groups import (
    chat_session_group,
    chatbot_dashboard_group,
)
from chatbot.models import Chatbot
from chat.services.messages import create_user_message
from chat.services.sessions import session_for_user
from chat.utils.choices import ChatMode

BUSY_MESSAGE = "The assistant is still answering. Wait for agent.response.completed."


class ChatSessionConsumer(AsyncJsonWebsocketConsumer):
    """Realtime chat for one session, in text or audio mode.

    The frontend connects with a JWT (header or ``?token=``) and receives
    every new message in the session plus the notifications of the
    chatbots it owns. The session owner chats here:

    - ``{"type": "mode.set", "mode": "text" | "audio"}`` switches the
      agent configuration (text: chat model; audio: live model). Text is
      the default; audio opens the live voice session.
    - ``{"type": "message.send", "content": "..."}`` sends a typed
      message, answered in the current mode.
    - Binary frames carry microphone PCM audio in audio mode.

    Replies stream as ``agent.response.*`` events (plus binary audio
    frames in audio mode).
    """

    async def connect(self):
        self.mode = ChatMode.TEXT
        self.voice = None
        self.turn_task = None
        self._session_service = None

        user = self.scope["user"]
        if not user.is_authenticated or not user.is_active:
            await self.close(code=4401)
            return

        session_id = self.scope["url_route"]["kwargs"]["session_id"]
        self.session = await database_sync_to_async(session_for_user)(
            user, session_id,
        )
        if self.session is None:
            await self.close(code=4403)
            return

        owned_chatbot_ids = await database_sync_to_async(
            self.get_owned_chatbot_ids,
        )(user)
        self.groups = {
            chat_session_group(self.session.pk),
            *(chatbot_dashboard_group(chatbot_id) for chatbot_id in owned_chatbot_ids),
        }
        await self.accept()
        for group in self.groups:
            await self.channel_layer.group_add(group, self.channel_name)
        await self.send_json({
            "type": "connection.ready",
            "data": {"session_id": str(self.session.pk), "mode": self.mode},
        })

    @staticmethod
    def get_owned_chatbot_ids(user):
        return list(
            Chatbot.objects.filter(
                is_deleted=False,
                created_by=user,
            ).values_list("pk", flat=True),
        )

    async def disconnect(self, close_code):
        # A running text turn is left to finish: its reply is persisted
        # and still reaches the session's other sockets.
        await self._stop_voice()
        for group in getattr(self, "groups", ()):
            await self.channel_layer.group_discard(group, self.channel_name)

    # ------------------------------------------------------------- inbound
    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        if bytes_data is not None:
            await self.handle_audio(bytes_data)
            return
        await super().receive(text_data=text_data, bytes_data=bytes_data, **kwargs)

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            await self.send_error("Expected a JSON object.")
            return
        event_type = content.get("type")

        if event_type == "ping":
            await self.send_json({"type": "pong"})
            return
        if event_type == "mode.set":
            await self.handle_mode_set(content)
            return
        if event_type == "message.send":
            await self.handle_message_send(content)
            return
        await self.send_error("Unsupported event type.")

    def _is_owner(self):
        return self.session.created_by_id == self.scope["user"].pk

    def _turn_running(self):
        return self.turn_task is not None and not self.turn_task.done()

    async def handle_mode_set(self, content):
        mode = content.get("mode")
        if mode not in ChatMode.values:
            await self.send_error("mode must be 'text' or 'audio'.")
            return
        if not self._is_owner():
            await self.send_error("Only the session owner can chat here.")
            return
        if mode == self.mode:
            await self._send_mode()
            return
        if mode == ChatMode.AUDIO:
            await self._start_voice()
        else:
            await self._stop_voice()
            self.mode = ChatMode.TEXT
            await self._send_mode()

    async def handle_message_send(self, content):
        if not self._is_owner():
            await self.send_error("Only the session owner can send messages here.")
            return
        if self.mode == ChatMode.AUDIO:
            await self._send_voice_text(content)
            return

        if self._turn_running():
            await self.send_error(BUSY_MESSAGE, code="busy")
            return
        owner = f"text:{self.channel_name}"
        if not await aacquire_session_lock(
            self.session.pk, owner, TEXT_TURN_TTL_SECONDS,
        ):
            await self.send_error(BUSY_MESSAGE, code="busy")
            return
        try:
            message = await database_sync_to_async(create_user_message)(
                self.session,
                content.get("content"),
                mode=ChatMode.TEXT,
                dispatch_reply=False,
            )
        except ValueError as exc:
            await arelease_session_lock(self.session.pk, owner)
            await self.send_error(str(exc))
            return
        await self._send_accepted(message)
        self.turn_task = asyncio.create_task(self._run_text_turn(message, owner))

    async def handle_audio(self, chunk):
        if self.voice is None:
            await self.send_error("Switch to audio mode before sending audio.")
            return
        try:
            await self.voice.send_audio(chunk)
        except Exception:
            await self.send_error("The voice session is not running.")

    # ----------------------------------------------------------- text mode
    def _get_session_service(self):
        if self._session_service is None:
            self._session_service = build_session_service()
        return self._session_service

    async def _run_text_turn(self, message, owner):
        try:
            client = await database_sync_to_async(AgentClient.for_session)(
                self.session.pk,
                session_service=self._get_session_service(),
            )
            await client.generate_reply(message, emit=self.safe_send_json)
        except AgentUnavailableError as exc:
            await self.safe_send_json({"type": "error", "message": str(exc)})
        except Exception:
            logger.exception("Text turn failed for message %s", message.pk)
        finally:
            await arelease_session_lock(self.session.pk, owner)

    # ---------------------------------------------------------- audio mode
    async def _start_voice(self):
        if self._turn_running():
            await self.send_error(BUSY_MESSAGE, code="busy")
            return
        voice = VoiceSession(
            session=self.session,
            send_json=self.safe_send_json,
            send_bytes=self.safe_send_bytes,
            owner=self.channel_name,
            on_failure=self._on_voice_failure,
            session_service=self._get_session_service(),
        )
        try:
            await voice.start()
        except AgentBusyError:
            await self.send_error(
                "Voice mode is already active for this session elsewhere, "
                "or a reply is in progress.",
                code="busy",
            )
            return
        except AgentUnavailableError as exc:
            await self.send_error(str(exc))
            return
        except Exception:
            logger.exception("Voice mode failed to start for session %s", self.session.pk)
            await self.send_error("Could not start voice mode.", code="voice_unavailable")
            return
        self.voice = voice
        self.mode = ChatMode.AUDIO
        await self._send_mode()

    async def _stop_voice(self):
        voice, self.voice = getattr(self, "voice", None), None
        if voice is not None:
            await voice.close()

    async def _on_voice_failure(self, message):
        await self._stop_voice()
        self.mode = ChatMode.TEXT
        await self.safe_send_json({"type": "error", "code": "voice_failed", "message": message})
        await self._send_mode()

    async def _send_voice_text(self, content):
        try:
            message = await database_sync_to_async(create_user_message)(
                self.session,
                content.get("content"),
                mode=ChatMode.AUDIO,
                dispatch_reply=False,
            )
        except ValueError as exc:
            await self.send_error(str(exc))
            return
        await self._send_accepted(message)
        try:
            await self.voice.send_text(message.content)
        except Exception:
            await self.send_error("The voice session is not running.")

    # ------------------------------------------------------------ outbound
    async def _send_mode(self):
        await self.safe_send_json({"type": MODE_CHANGED, "data": {"mode": self.mode}})

    async def _send_accepted(self, message):
        await self.send_json({
            "type": "message.accepted",
            "session_id": str(self.session.pk),
            "message_id": str(message.pk),
            "mode": message.mode,
        })

    async def safe_send_json(self, payload):
        try:
            await self.send_json(payload)
        except Exception:
            logger.debug("Socket closed; dropped %s", payload.get("type"))

    async def safe_send_bytes(self, data):
        try:
            await self.send(bytes_data=data)
        except Exception:
            logger.debug("Socket closed; dropped audio frame")

    async def chat_message_event(self, event):
        await self.send_json(event["event"])

    async def notification_created(self, event):
        await self.send_json({
            "type": NOTIFICATION_CREATED,
            "data": event["notification"],
        })

    async def send_error(self, message, *, code=None):
        payload = {"type": "error", "message": message}
        if code:
            payload["code"] = code
        await self.send_json(payload)
