from __future__ import annotations

import unittest

from app.debug import _should_print_label


class DebugFilterTests(unittest.TestCase):
    def test_allows_only_llm_send_receive_labels(self) -> None:
        self.assertTrue(_should_print_label("planner.generate_operations.system_prompt"))
        self.assertTrue(_should_print_label("planner.generate_operations.user_context"))
        self.assertTrue(_should_print_label("planner.generate_operations.llm_payload"))
        self.assertTrue(_should_print_label("planner.generate_operations.llm_response"))
        self.assertTrue(_should_print_label("planner.generate_operations.llm_raw_content"))
        self.assertTrue(_should_print_label("planner.generate_query_answer.llm_payload"))

        self.assertFalse(_should_print_label("planner.generate_operations.received"))
        self.assertFalse(_should_print_label("planner.generate_operations.parsed_json"))
        self.assertFalse(_should_print_label("planner.generate_operations.compiled_operations"))
        self.assertFalse(_should_print_label("routes.run_agent.storage"))
        self.assertFalse(_should_print_label("routes.autocomplete.generated"))


if __name__ == "__main__":
    unittest.main()
