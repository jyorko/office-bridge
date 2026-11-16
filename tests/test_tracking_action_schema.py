"""Exercise the Actions Core schema boundary that supplies the MCP tool catalog."""

from actions._action import Action
from actions._customization._plugin_manager import PluginManager

from teams_actions import TrackingChats, teams_work_tracking_configure


def test_chat_array_keeps_nested_identity_schema():
    action = Action(
        PluginManager(), "teams_actions", "teams_actions.py", teams_work_tracking_configure
    )
    schema = action.input_schema
    chats = schema["properties"]["chats"]
    assert chats["type"] == "array"
    assert chats["maxItems"] == 50
    assert set(chats["items"]["required"]) == {"chat_id", "title"}
    assert "$ref" not in str(chats)
    parsed = TrackingChats.model_validate(
        [{"chat_id": "19:fixture@thread.v2", "title": "Fixture Group"}]
    )
    assert parsed.root[0].chat_id == "19:fixture@thread.v2"
