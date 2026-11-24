from urllib.parse import parse_qs, urlparse

import pytest

from dakarbot.about import INVITE_PERMISSIONS, build_invite_url, build_user_install_url
from dakarbot.polls import PollInputError, parse_answers


def test_parse_answers_one_per_line():
    assert parse_answers("Pizza\n  Pasta \n\nSalad") == ["Pizza", "Pasta", "Salad"]


def test_parse_answers_drops_case_insensitive_duplicates():
    assert parse_answers("Yes\nyes\nNo") == ["Yes", "No"]


@pytest.mark.parametrize("text", ["", "Only one", "\n".join(f"Answer {i}" for i in range(11))])
def test_parse_answers_needs_two_to_ten(text):
    with pytest.raises(PollInputError):
        parse_answers(text)


def test_parse_answers_rejects_long_answers():
    with pytest.raises(PollInputError):
        parse_answers("Short\n" + "x" * 56)


def test_invite_url_has_bot_scopes_and_permissions():
    query = parse_qs(urlparse(build_invite_url(1234)).query)
    assert query["client_id"] == ["1234"]
    assert set(query["scope"][0].split()) == {"bot", "applications.commands"}
    assert query["permissions"] == [str(INVITE_PERMISSIONS.value)]


def test_invite_permissions_cover_moderation_without_admin():
    perms = INVITE_PERMISSIONS
    assert not perms.administrator
    for name in ("ban_members", "kick_members", "moderate_members", "manage_roles",
                 "manage_channels", "manage_messages", "view_audit_log", "send_messages"):
        assert getattr(perms, name), name


def test_user_install_url_installs_to_account_with_commands_only():
    query = parse_qs(urlparse(build_user_install_url(1234)).query)
    assert query["client_id"] == ["1234"]
    assert query["integration_type"] == ["1"]
    assert query["scope"] == ["applications.commands"]
    assert "permissions" not in query
