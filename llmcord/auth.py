from __future__ import annotations

from typing import Any

import discord


def is_authorized(
    *, new_msg: discord.Message, config: dict[str, Any], is_dm: bool
) -> bool:
    """Check if user is authorized to use the bot."""

    role_ids = set(role.id for role in getattr(new_msg.author, "roles", ()))
    channel_ids = set(
        filter(
            None,
            (
                new_msg.channel.id,
                getattr(new_msg.channel, "parent_id", None),
                getattr(new_msg.channel, "category_id", None),
            ),
        )
    )

    allow_dms = config.get("allow_dms", True)
    permissions = config["permissions"]
    user_is_admin = new_msg.author.id in permissions["users"]["admin_ids"]

    (
        (allowed_user_ids, blocked_user_ids),
        (allowed_role_ids, blocked_role_ids),
        (allowed_channel_ids, blocked_channel_ids),
    ) = (
        (perm["allowed_ids"], perm["blocked_ids"])
        for perm in (
            permissions["users"],
            permissions["roles"],
            permissions["channels"],
        )
    )

    allow_all_users = (
        not allowed_user_ids if is_dm else not allowed_user_ids and not allowed_role_ids
    )
    is_good_user = (
        user_is_admin
        or allow_all_users
        or new_msg.author.id in allowed_user_ids
        or any(id in allowed_role_ids for id in role_ids)
    )
    is_bad_user = (
        not is_good_user
        or new_msg.author.id in blocked_user_ids
        or any(id in blocked_role_ids for id in role_ids)
    )

    allow_all_channels = not allowed_channel_ids
    is_good_channel = (
        user_is_admin or allow_dms
        if is_dm
        else allow_all_channels or any(id in allowed_channel_ids for id in channel_ids)
    )
    is_bad_channel = not is_good_channel or any(
        id in blocked_channel_ids for id in channel_ids
    )

    return not (is_bad_user or is_bad_channel)


def format_system_prompt(
    system_prompt: str,
    *,
    accept_usernames: bool,
    users_listing: str | None = None,
) -> str:
    """Format system prompt with username support if needed."""
    from datetime import datetime

    if not system_prompt:
        return ""

    now = datetime.now().astimezone()
    formatted = (
        system_prompt.replace("{date}", now.strftime("%B %d %Y"))
        .replace("{time}", now.strftime("%H:%M:%S %Z%z"))
        .strip()
    )

    # Replace {users} by default if present; empty string if no listing provided
    if "{users}" in formatted:
        formatted = formatted.replace("{users}", users_listing or "")

    if accept_usernames:
        formatted += (
            "\nUser's names are their Discord IDs and should be typed as '<@ID>'."
        )

    return formatted
