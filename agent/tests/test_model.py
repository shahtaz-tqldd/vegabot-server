import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, override_settings

from agent.helpers.model import ChatGemini, vertex_client, vertex_client_scope


class LoopBoundClient:
    """A transport that fails if it is reused or closed on another loop."""

    def __init__(self, **kwargs):
        self.loop = asyncio.get_running_loop()
        self.options = kwargs
        self.async_closed = False
        self.sync_closed = False
        self.aio = SimpleNamespace(aclose=self.aclose)

    def request(self):
        if self.loop is not asyncio.get_running_loop() or self.loop.is_closed():
            raise RuntimeError("Event loop is closed")
        if self.async_closed:
            raise RuntimeError("Client is closed")

    async def aclose(self):
        self.request()
        self.async_closed = True

    def close(self):
        self.sync_closed = True


@override_settings(GOOGLE_CLOUD_PROJECT_ID="test-project", GOOGLE_CLOUD_LOCATION="test-location")
class VertexClientLifetimeTests(SimpleTestCase):
    def setUp(self):
        self.clients = []

        def make_client(**kwargs):
            client = LoopBoundClient(**kwargs)
            self.clients.append(client)
            return client

        self.factory = self.enterContext(patch("agent.helpers.model.Client", side_effect=make_client))

    async def turn(self):
        async with vertex_client_scope():
            coordinator = ChatGemini(model="gemini-2.5-flash")
            specialist = ChatGemini(model="gemini-2.5-flash")
            client = coordinator.api_client
            client.request()
            self.assertIs(specialist.api_client, client)
            self.assertIs(vertex_client(), client)
        return client

    def test_consecutive_sync_turns_use_separate_clients_and_close_before_loop_exit(self):
        # Celery calls async_to_sync for each message; both loops are short lived.
        first = async_to_sync(self.turn)()
        second = async_to_sync(self.turn)()
        self.assertIsNot(first, second)
        self.assertIsNot(first.loop, second.loop)
        self.assertEqual(self.factory.call_count, 2)
        for client in (first, second):
            self.assertTrue(client.loop.is_closed())
            self.assertTrue(client.async_closed)
            self.assertTrue(client.sync_closed)
        self.factory.assert_called_with(vertexai=True, project="test-project", location="test-location")
        with self.assertRaisesRegex(RuntimeError, "active vertex_client_scope"):
            vertex_client()

    async def test_concurrent_turns_do_not_share_clients(self):
        async def concurrent_turn():
            async with vertex_client_scope():
                client = vertex_client()
                await asyncio.sleep(0)
                self.assertIs(vertex_client(), client)
                client.request()
                return client

        first, second = await asyncio.gather(concurrent_turn(), concurrent_turn())
        self.assertIsNot(first, second)
        self.assertTrue(first.async_closed and second.async_closed)

    async def test_model_failure_still_closes_client_and_resets_scope(self):
        with self.assertRaisesRegex(ValueError, "model failed"):
            async with vertex_client_scope():
                client = vertex_client()
                raise ValueError("model failed")
        self.assertTrue(client.async_closed)
        self.assertTrue(client.sync_closed)
        with self.assertRaisesRegex(RuntimeError, "active vertex_client_scope"):
            vertex_client()
        await self.turn()
        self.assertEqual(len(self.clients), 2)

    async def test_turn_without_vertex_calls_does_not_create_client(self):
        async with vertex_client_scope():
            pass
        self.factory.assert_not_called()
