import logging
from io import StringIO
from unittest import TestCase

from app.utils.logger import get_logger, logger


class LoggerTests(TestCase):
    def test_module_names_share_app_configuration(self):
        self.assertIs(logger, logging.getLogger("app"))
        self.assertIs(get_logger("knowledge.tasks"), logging.getLogger("app.knowledge.tasks"))
        self.assertIs(get_logger("app.services.example"), logging.getLogger("app.services.example"))

    def test_output_filtering_and_tracebacks(self):
        parent = logging.getLogger("app")
        original = (parent.handlers, parent.level, parent.propagate, parent.disabled)
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        parent.handlers = [handler]
        parent.setLevel(logging.INFO)
        parent.propagate = False
        parent.disabled = False
        try:
            module_logger = get_logger("logger_test.module")
            self.assertIs(module_logger, get_logger("logger_test.module"))
            module_logger.debug("hidden")
            module_logger.info("Started %s", "job")
            module_logger.warning("Retrying")
            module_logger.error("Failed")
            try:
                raise ValueError("example failure")
            except ValueError:
                module_logger.exception("Processing failed")
            output = stream.getvalue()
            self.assertNotIn("hidden", output)
            self.assertEqual(output.count("INFO Started job"), 1)
            self.assertIn("WARNING Retrying", output)
            self.assertIn("ERROR Failed", output)
            self.assertIn("Traceback (most recent call last)", output)
            self.assertIn("ValueError: example failure", output)
        finally:
            parent.handlers, level, parent.propagate, parent.disabled = original
            parent.setLevel(level)
            handler.close()
