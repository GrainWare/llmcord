from __future__ import annotations

import re


THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def collapse_think_blocks(delta: str) -> tuple[str, bool]:
    stripped = THINK_RE.sub("", delta)
    return stripped, stripped != delta


class ThinkBlockRedactor:
    OPEN_TAG: str = "<think>"
    CLOSE_TAG: str = "</think>"

    def __init__(self) -> None:
        self._inside_think_block: bool = False
        self._pending_prefix: str = ""
        self._buffer_size: int = max(len(self.OPEN_TAG), len(self.CLOSE_TAG)) - 1

    def process(self, text: str) -> tuple[str, bool]:
        combined = self._pending_prefix + (text or "")
        self._pending_prefix = ""

        sanitized_parts: list[str] = []
        saw_thinking: bool = False

        if not combined:
            return "", False

        i: int = 0
        length: int = len(combined)

        while i < length:
            if self._inside_think_block:
                close_idx = combined.find(self.CLOSE_TAG, i)
                saw_thinking = True
                if close_idx == -1:
                    return "", True
                i = close_idx + len(self.CLOSE_TAG)
                self._inside_think_block = False
                continue

            open_idx = combined.find(self.OPEN_TAG, i)
            close_idx = combined.find(self.CLOSE_TAG, i)

            if open_idx == -1 and close_idx == -1:
                sanitized_parts.append(combined[i:])
                break

            if close_idx != -1 and (open_idx == -1 or close_idx < open_idx):
                saw_thinking = True
                i = close_idx + len(self.CLOSE_TAG)
                continue

            if open_idx != -1:
                sanitized_parts.append(combined[i:open_idx])
                i = open_idx + len(self.OPEN_TAG)
                saw_thinking = True

                end_idx = combined.find(self.CLOSE_TAG, i)
                if end_idx == -1:
                    self._inside_think_block = True
                    break
                i = end_idx + len(self.CLOSE_TAG)
                continue

        sanitized_all = "".join(sanitized_parts)

        if not self._inside_think_block:
            if len(sanitized_all) > self._buffer_size:
                emit_now = sanitized_all[: -self._buffer_size]
                self._pending_prefix = sanitized_all[-self._buffer_size :]
            else:
                emit_now = ""
                self._pending_prefix = sanitized_all
        else:
            emit_now = ""
            self._pending_prefix = ""

        return emit_now, saw_thinking

    def flush(self) -> str:
        flushed = self._pending_prefix
        self._pending_prefix = ""
        return "" if self._inside_think_block else flushed


__all__ = ["THINK_RE", "collapse_think_blocks", "ThinkBlockRedactor"]
