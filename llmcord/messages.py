from __future__ import annotations

import asyncio
from base64 import b64encode
from dataclasses import dataclass, field
from typing import Any, Literal
import httpx
import discord
from .constants import (
    WARNING_MAX_TEXT_TEMPLATE,
    WARNING_MAX_IMAGES_TEMPLATE,
    WARNING_CANT_SEE_IMAGES,
    WARNING_UNSUPPORTED_ATTACHMENTS,
    WARNING_ONLY_USING_LAST_TEMPLATE,
)


@dataclass
class MsgNode:
    text: str | None = None
    images: list[dict[str, Any]] = field(default_factory=list)

    role: Literal["user", "assistant"] = "assistant"
    user_id: int | None = None

    has_bad_attachments: bool = False
    fetch_parent_failed: bool = False

    parent_msg: discord.Message | None = None

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


async def build_conversation_context(
    *,
    new_msg: discord.Message,
    bot_user: discord.ClientUser,
    accept_images: bool,
    accept_usernames: bool,
    experimental_message_formatting: bool,
    max_text: int,
    max_images: int,
    max_messages: int,
    msg_nodes: dict[int, "MsgNode"],
    httpx_client: httpx.AsyncClient,
) -> tuple[list[dict[str, Any]], set[str]]:
    messages: list[dict[str, Any]] = []
    user_warnings: set[str] = set()
    curr_msg: discord.Message | None = new_msg

    while curr_msg is not None and len(messages) < max_messages:
        curr_node = msg_nodes.setdefault(curr_msg.id, MsgNode())

        async with curr_node.lock:
            if curr_node.text is None:
                cleaned_content = curr_msg.content.removeprefix(
                    bot_user.mention
                ).lstrip()

                good_attachments = [
                    att
                    for att in curr_msg.attachments
                    if att.content_type
                    and any(att.content_type.startswith(x) for x in ("text", "image"))
                ]

                attachment_responses = await asyncio.gather(
                    *[httpx_client.get(att.url) for att in good_attachments]
                )

                curr_node.text = "\n".join(
                    ([cleaned_content] if cleaned_content else [])
                    + [
                        "\n".join(
                            filter(
                                None,
                                (embed.title, embed.description, embed.footer.text),
                            )
                        )
                        for embed in curr_msg.embeds
                    ]
                    + [
                        resp.text
                        for att, resp in zip(good_attachments, attachment_responses)
                        if (att.content_type or "").startswith("text")
                    ]
                )

                curr_node.images = [
                    dict(
                        type="image_url",
                        image_url=dict(
                            url=f"data:{att.content_type};base64,{b64encode(resp.content).decode('utf-8')}"
                        ),
                    )
                    for att, resp in zip(good_attachments, attachment_responses)
                    if (att.content_type or "").startswith("image")
                ]

                curr_node.role = "assistant" if curr_msg.author == bot_user else "user"

                curr_node.user_id = (
                    curr_msg.author.id if curr_node.role == "user" else None
                )

                curr_node.has_bad_attachments = len(curr_msg.attachments) > len(
                    good_attachments
                )

                try:
                    if (
                        curr_msg.reference is None
                        and bot_user.mention not in curr_msg.content
                        and (
                            prev_msg_in_channel := (
                                [
                                    m
                                    async for m in curr_msg.channel.history(
                                        before=curr_msg, limit=1
                                    )
                                ]
                                or [None]
                            )[0]
                        )
                        and prev_msg_in_channel.type
                        in (discord.MessageType.default, discord.MessageType.reply)
                        and prev_msg_in_channel.author
                        == (
                            bot_user
                            if curr_msg.channel.type == discord.ChannelType.private
                            else curr_msg.author
                        )
                    ):
                        curr_node.parent_msg = prev_msg_in_channel
                    else:
                        channel = curr_msg.channel
                        if isinstance(channel, discord.Thread):
                            thread: discord.Thread = channel
                            is_public_thread = (
                                thread.type == discord.ChannelType.public_thread
                            )
                            parent_is_thread_start = (
                                is_public_thread
                                and curr_msg.reference is None
                                and isinstance(thread.parent, discord.TextChannel)
                            )

                            parent_msg_id = (
                                thread.id
                                if parent_is_thread_start
                                else getattr(curr_msg.reference, "message_id", None)
                            )

                            if parent_msg_id:
                                if parent_is_thread_start and isinstance(
                                    thread.parent, discord.TextChannel
                                ):
                                    curr_node.parent_msg = (
                                        thread.starter_message
                                        or await thread.parent.fetch_message(
                                            parent_msg_id
                                        )
                                    )
                                else:
                                    cached = getattr(
                                        curr_msg.reference, "cached_message", None
                                    )
                                    if cached is not None:
                                        curr_node.parent_msg = cached
                                    else:
                                        if isinstance(
                                            channel,
                                            (discord.Thread, discord.TextChannel),
                                        ):
                                            curr_node.parent_msg = (
                                                await channel.fetch_message(
                                                    parent_msg_id
                                                )
                                            )
                        else:
                            if parent_msg_id := getattr(
                                curr_msg.reference, "message_id", None
                            ):
                                cached = getattr(
                                    curr_msg.reference, "cached_message", None
                                )
                                if cached is not None:
                                    curr_node.parent_msg = cached
                                elif isinstance(channel, (discord.TextChannel)):
                                    curr_node.parent_msg = await channel.fetch_message(
                                        parent_msg_id
                                    )

                except (discord.NotFound, discord.HTTPException):
                    # Keep going; mark and warn later
                    curr_node.fetch_parent_failed = True

            if curr_node.images[:max_images]:
                content: Any = (
                    [dict(type="text", text=curr_node.text[:max_text])]
                    if curr_node.text[:max_text]
                    else []
                ) + curr_node.images[:max_images]
            else:
                content = curr_node.text[:max_text]

            if content != "":
                # Optionally format user messages as "nickname: content"
                if experimental_message_formatting and curr_node.role == "user":
                    try:
                        display_name = getattr(getattr(curr_msg, "author", None), "display_name", None) or getattr(getattr(curr_msg, "author", None), "name", None) or "unknown"
                    except Exception:
                        display_name = "unknown"

                    if isinstance(content, list):
                        if content and isinstance(content[0], dict) and content[0].get("type") == "text":
                            original_text = content[0].get("text", "")
                            content[0]["text"] = f"{display_name}: {original_text}" if original_text else f"{display_name}:"
                        # If no text part exists, leave images as-is
                    elif isinstance(content, str):
                        content = f"{display_name}: {content}"

                message: dict[str, Any] = dict(content=content, role=curr_node.role)
                if accept_usernames and curr_node.user_id is not None:
                    message["name"] = str(curr_node.user_id)
                messages.append(message)

            if len(curr_node.text or "") > max_text:
                user_warnings.add(WARNING_MAX_TEXT_TEMPLATE.format(max_text=max_text))
            if len(curr_node.images) > max_images:
                if max_images > 0:
                    s = "" if max_images == 1 else "s"
                    user_warnings.add(
                        WARNING_MAX_IMAGES_TEMPLATE.format(max_images=max_images, s=s)
                    )
                else:
                    user_warnings.add(WARNING_CANT_SEE_IMAGES)
            if curr_node.has_bad_attachments:
                user_warnings.add(WARNING_UNSUPPORTED_ATTACHMENTS)
            if curr_node.fetch_parent_failed or (
                curr_node.parent_msg is not None and len(messages) == max_messages
            ):
                s = "" if len(messages) == 1 else "s"
                user_warnings.add(
                    WARNING_ONLY_USING_LAST_TEMPLATE.format(
                        messages_count=len(messages), s=s
                    )
                )

            curr_msg = curr_node.parent_msg

    return messages, user_warnings


__all__ = ["MsgNode", "build_conversation_context"]
