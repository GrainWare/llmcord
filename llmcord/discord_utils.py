from __future__ import annotations

import discord


def build_warnings_embed(warnings: set[str]) -> discord.Embed:
    embed = discord.Embed()
    for warning in sorted(warnings):
        embed.add_field(name=warning, value="", inline=False)
    return embed


def copy_embed_fields(src: discord.Embed, dst: discord.Embed) -> None:
    for f in src.fields:
        dst.add_field(name=f.name, value=f.value, inline=f.inline)


__all__ = ["build_warnings_embed", "copy_embed_fields"]
