"""
tests/test_knowledge_agent.py  —  Phase 6

TODO: implement when building the Knowledge Agent.

Test cases to write:
  - outer_race query retrieves SOP_001 (motor bearing replacement)
  - inner_race query retrieves SOP_001 (NDE section)
  - lubrication_issue query retrieves SOP_003
  - every fault_mode returns at least one source document
  - no guidance returned without a cited source (no hallucination test)
  - retrieval works for all 7 demo scenario fault modes
"""
import unittest


class TestKnowledgeAgent(unittest.TestCase):
    def test_placeholder(self):
        self.skipTest("KnowledgeAgent not yet implemented — Phase 6")


if __name__ == "__main__":
    unittest.main()
