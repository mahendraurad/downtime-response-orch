"""Test-suite isolation from optional cloud infrastructure."""
import os
import pytest


# Unit and API tests must never depend on, mutate, or incur cost against Azure.
os.environ.setdefault("HITL_REPOSITORY", "sqlite")
os.environ["LANGSMITH_TRACING"] = "false"


@pytest.fixture(autouse=True)
def authenticated_api_test_identity(request):
    """Exercise protected API behavior with a trusted test identity.

    Tests marked ``real_auth`` bypass this override and exercise JWT handling.
    """
    if request.node.get_closest_marker("real_auth"):
        yield
        return
    from src.api.auth import get_current_user
    import src.api.main as api
    app=api.app
    prior_chat_llm=api._CHAT_LLM
    class DisabledChatLLM:
        @staticmethod
        def is_configured(): return False
    api._CHAT_LLM=DisabledChatLLM()
    app.dependency_overrides[get_current_user]=lambda: {
        "sub":"pytest-admin",
        "role":"admin",
        "display_name":"Pytest Admin",
        "allowed_personas":[
            "supervisor","engineer","maintenance","manager",
            "executive","md","ot","safety",
        ],
    }
    yield
    api._CHAT_LLM=prior_chat_llm
    app.dependency_overrides.pop(get_current_user,None)
