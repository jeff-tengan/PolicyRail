from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MCPTool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MCPToolResult:
    content: list[dict[str, Any]] = field(default_factory=list)
    structured_content: Any = None
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def text_content(self) -> str:
        chunks: list[str] = []
        for item in self.content:
            if item.get("type") == "text":
                text = item.get("text")
                if text:
                    chunks.append(str(text))
        return "\n".join(chunks).strip()


@dataclass(slots=True)
class MCPToolPolicy:
    sensitive: bool = True
    requires_human_approval: bool = True
    max_risk_score: int = 0
    description: str | None = None
