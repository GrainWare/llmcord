from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import discord
from discord.app_commands import Choice
from discord.ext import commands
import httpx
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from .config import get_config
from .constants import (
    VISION_MODEL_TAGS,
    PROVIDERS_SUPPORTING_USERNAMES,
    EMBED_DESCRIPTION_MAX_LENGTH,
    STREAMING_INDICATOR,
    MAX_MESSAGE_NODES,
)
from .discord_utils import build_warnings_embed
from .messages import MsgNode, build_conversation_context
from .auth import is_authorized, format_system_prompt
from .streaming import stream_and_reply


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# Global state
config = get_config()
curr_model = next(iter(config["models"]))
msg_nodes: dict[int, MsgNode] = {}

# Discord bot setup
intents = discord.Intents.all()
activity = discord.CustomActivity(
    name=(config["status_message"] or "github.com/GrainWare/llmcord")[:128]
)
discord_bot = commands.Bot(intents=intents, activity=activity, command_prefix="")

# Attachment handling
httpx_client: httpx.AsyncClient | None = None


@discord_bot.tree.command(name="model", description="View or switch the current model")
async def model_command(interaction: discord.Interaction, model: str) -> None:
    global curr_model

    if model == curr_model:
        output = f"Current model: `{curr_model}`"
    else:
        if interaction.user.id in config["permissions"]["users"]["admin_ids"]:
            # Ensure the requested model exists in the latest config to avoid runtime errors
            latest_cfg = await asyncio.to_thread(get_config)
            if model in latest_cfg.get("models", {}):
                curr_model = model
                output = f"Model switched to: `{model}`"
                logging.info(output)
            else:
                output = (
                    "Unknown model. Use /model autocomplete or update your config.yaml."
                )
        else:
            output = "You don't have permission to change the model."

    # Ephemeral messages are only meaningful in guilds, not in DMs
    is_ephemeral = interaction.guild_id is not None
    await interaction.response.send_message(output, ephemeral=is_ephemeral)


@model_command.autocomplete("model")
async def model_autocomplete(
    interaction: discord.Interaction, curr_str: str
) -> list[Choice[str]]:
    global config

    if curr_str == "":
        config = await asyncio.to_thread(get_config)

    choices = (
        [Choice(name=f"◉ {curr_model} (current)", value=curr_model)]
        if curr_str.lower() in curr_model.lower()
        else []
    )
    choices += [
        Choice(name=f"○ {model}", value=model)
        for model in config["models"]
        if model != curr_model and curr_str.lower() in model.lower()
    ][:24]

    return choices


@discord_bot.event
async def on_ready() -> None:
    if client_id := config["client_id"]:
        logging.info(
            f"\n\nBOT INVITE URL:\nhttps://discord.com/oauth2/authorize?client_id={client_id}&permissions=377957190720&scope=bot\n"
        )
    await discord_bot.tree.sync()


@discord_bot.event
async def on_message(new_msg: discord.Message) -> None:
    assert discord_bot.user is not None

    is_dm = new_msg.channel.type == discord.ChannelType.private
    if new_msg.author.bot:
        return
    if not is_dm and discord_bot.user not in new_msg.mentions:
        return

    cfg = await asyncio.to_thread(get_config)
    if not is_authorized(new_msg=new_msg, config=cfg, is_dm=is_dm):
        return

    provider_slash_model = curr_model
    provider, model = provider_slash_model.removesuffix(":vision").split("/", 1)

    provider_config = cfg["providers"][provider]
    base_url = provider_config["base_url"]
    api_key = provider_config.get("api_key", "sk-no-key-required")
    openai_client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    model_parameters = cfg["models"].get(provider_slash_model, None)

    extra_headers = provider_config.get("extra_headers", None)
    extra_query = provider_config.get("extra_query", None)
    extra_body = (provider_config.get("extra_body", None) or {}) | (
        model_parameters or {}
    )

    try:
        existing_stream_options = cast(
            dict[str, Any], extra_body.get("stream_options", {})
        )
    except Exception:
        existing_stream_options = {}
    extra_body["stream_options"] = {**existing_stream_options, "include_usage": True}

    accept_images = any(x in provider_slash_model.lower() for x in VISION_MODEL_TAGS)
    accept_usernames = any(
        x in provider_slash_model.lower() for x in PROVIDERS_SUPPORTING_USERNAMES
    )

    max_text = cfg.get("max_text", 100000)
    max_images = cfg.get("max_images", 5) if accept_images else 0
    max_messages = cfg.get("max_messages", 25)

    assert httpx_client is not None, "HTTPX client not initialized"
    messages, user_warnings = await build_conversation_context(
        new_msg=new_msg,
        bot_user=discord_bot.user,
        accept_images=accept_images,
        accept_usernames=accept_usernames,
        experimental_message_formatting=cfg.get("experimental_message_formatting", False),
        max_text=max_text,
        max_images=max_images,
        max_messages=max_messages,
        msg_nodes=msg_nodes,
        httpx_client=httpx_client,
    )

    logging.info(
        f"Message received (user ID: {new_msg.author.id}, attachments: {len(new_msg.attachments)}, conversation length: {len(messages)}):\n{new_msg.content}"
    )

    if system_prompt := format_system_prompt(
        cfg.get("system_prompt", ""),
        accept_usernames=accept_usernames,
        users_listing=(
            "\n".join(
                [
                    f"username: {member.name}, nickname: {member.display_name}, mention: <@{member.id}>"
                    for member in (new_msg.guild.members if new_msg.guild else [])
                ]
            )
        ),
    ):
        messages.append(dict(role="system", content=system_prompt))

    embed = build_warnings_embed(user_warnings)
    use_plain_responses = cfg.get("use_plain_responses", False)
    max_message_length = (
        2000
        if use_plain_responses
        else (EMBED_DESCRIPTION_MAX_LENGTH - len(STREAMING_INDICATOR))
    )

    # Optional safety controls
    block_response_regex = cfg.get("block_response_regex")
    reply_length_cap = cfg.get("reply_length_cap")

    try:
        response_msgs, response_contents = await stream_and_reply(
            new_msg=new_msg,
            openai_client=openai_client,
            model=model,
            display_model=provider_slash_model,
            messages=cast(list[ChatCompletionMessageParam], messages),
            embed=embed,
            use_plain_responses=use_plain_responses,
            max_message_length=max_message_length,
            extra_headers=extra_headers,
            extra_query=extra_query,
            extra_body=extra_body,
            msg_nodes=msg_nodes,
            block_response_regex=block_response_regex,
            reply_length_cap=reply_length_cap,
        )
    except Exception:
        logging.exception("Error while generating response")
        return

    for response_msg in response_msgs:
        msg_nodes[response_msg.id].text = "".join(response_contents)
        msg_nodes[response_msg.id].lock.release()

    if (num_nodes := len(msg_nodes)) > MAX_MESSAGE_NODES:
        for msg_id in sorted(msg_nodes.keys())[: num_nodes - MAX_MESSAGE_NODES]:
            node = msg_nodes.get(msg_id)
            if node is None:
                continue
            async with node.lock:
                msg_nodes.pop(msg_id, None)


async def main() -> None:
    global httpx_client
    httpx_client = httpx.AsyncClient()
    try:
        await discord_bot.start(config["bot_token"])
    finally:
        try:
            client = httpx_client
            if client is not None:
                await client.aclose()
        except Exception:
            pass


def _run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    _run()
