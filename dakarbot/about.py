"""Install links for /about. The server invite asks for the permissions DakarBot's
commands actually use, rather than Administrator."""
from __future__ import annotations

from urllib.parse import urlencode

import discord

INVITE_PERMISSIONS = discord.Permissions(
    view_channel=True,
    send_messages=True,
    send_messages_in_threads=True,
    embed_links=True,
    attach_files=True,
    read_message_history=True,
    add_reactions=True,
    use_external_emojis=True,
    manage_messages=True,
    manage_channels=True,
    manage_roles=True,
    manage_threads=True,
    kick_members=True,
    ban_members=True,
    moderate_members=True,
    view_audit_log=True,
    send_polls=True,
)


def build_invite_url(application_id: int) -> str:
    return discord.utils.oauth_url(
        application_id, permissions=INVITE_PERMISSIONS, scopes=("bot", "applications.commands")
    )


def build_user_install_url(application_id: int) -> str:
    """Adds the bot to the user's own account; pycord's oauth_url can't set integration_type."""
    query = urlencode({"client_id": application_id, "integration_type": 1, "scope": "applications.commands"})
    return f"https://discord.com/oauth2/authorize?{query}"
