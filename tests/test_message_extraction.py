from __future__ import annotations

from agent_rt import ContentPart

from openbox_agentrt.message_extraction import (
    apply_redaction,
    extract_last_user_text,
)
from tests.fakes import final, user


def test_extract_last_user_text() -> None:
    assert extract_last_user_text([user("a"), final("b").message, user("c")]) == "c"
    assert extract_last_user_text([final("b").message]) == ""


def test_redaction_replaces_text_keeps_non_text_parts_and_history() -> None:
    img = ContentPart(type="image", data="x")
    msg = user("secret")
    original = (msg.__class__(role="user", content=(*msg.content, img)),)
    out = apply_redaction(original, [{"prompt": "[R]"}])
    assert [p.type for p in out[0].content] == ["text", "image"]
    assert out[0].content[0].text == "[R]"
    assert original[0].content[0].text == "secret"  # frozen original untouched


def test_redaction_noop_without_text() -> None:
    msgs = (user("a"),)
    assert apply_redaction(msgs, None) == msgs
