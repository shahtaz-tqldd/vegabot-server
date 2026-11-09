from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from agent.helpers.citations import CitationStreamFilter
from agent.helpers.recommendations import build_recommendations
from agent.helpers.stream import TurnStream


class CitationStreamFilterTests(SimpleTestCase):
    def run_filter(self, chunks):
        stream_filter = CitationStreamFilter()
        return "".join(stream_filter.feed(chunk) for chunk in chunks) + stream_filter.flush()

    def test_markers_split_across_chunks_are_removed(self):
        self.assertEqual(
            self.run_filter(["Shoes [K", "1] and hats [K2, K", "3]!"]),
            "Shoes  and hats !",
        )

    def test_ordinary_brackets_survive(self):
        self.assertEqual(
            self.run_filter(["Size [EU", " 42] or [", "see chart]"]),
            "Size [EU 42] or [see chart]",
        )

    def test_unterminated_tail_is_released_on_flush(self):
        self.assertEqual(self.run_filter(["Pick one [K"]), "Pick one [K")


class RecommendationTests(SimpleTestCase):
    def test_only_cited_catalog_items_in_citation_order(self):
        known = {
            "p1": {"space": "product", "name": "A"},
            "kb": {"space": "document", "name": "FAQ"},
            "s1": {"space": "service", "name": "B"},
        }
        items = build_recommendations(["s1", "kb", "p1", "s1", "missing"], known)
        self.assertEqual([(i["type"], i["id"]) for i in items], [("service", "s1"), ("product", "p1")])


class TurnStreamTests(SimpleTestCase):
    def test_no_recommendation_event_without_items(self):
        events = []

        async def scenario():
            stream = TurnStream(events.append, mode="text", turn_id="t1")
            await stream.start()
            await stream.delta("Hi")
            await stream.delta("")
            await stream.finish(message={"id": "m"})

        async_to_sync(scenario)()

        self.assertEqual(
            [(e["type"], e["data"].get("sequence")) for e in events],
            [
                ("agent.response.started", None),
                ("agent.response.delta", 1),
                ("agent.response.completed", None),
            ],
        )
        self.assertTrue(all(e["data"]["turn_id"] == "t1" for e in events))

    def test_emit_failures_never_break_the_turn(self):
        def broken(payload):
            raise ConnectionError

        async def scenario():
            stream = TurnStream(broken, mode="audio")
            await stream.start()
            await stream.finish(message=None)

        async_to_sync(scenario)()
