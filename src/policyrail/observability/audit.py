from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import shorten
from uuid import uuid4

from ..core.models import (
    OutputValidation,
    PolicyDecision,
    RiskAssessment,
    SecureRequest,
    ToolCall,
    ToolExecutionResult,
)


class JsonAuditLogger:
    def __init__(self, log_path: str | Path = "logs/audit.jsonl") -> None:
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._redaction_patterns = [
            re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
            re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        ]

    def record_interaction(
        self,
        *,
        request: SecureRequest,
        risk: RiskAssessment,
        decision: PolicyDecision,
        output_validation: OutputValidation,
        response_text: str,
        tool_call: ToolCall | None,
        tool_result: ToolExecutionResult | None = None,
    ) -> str:
        payload = {
            "event_id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": request.metadata,
            "user_input_preview": self._sanitize(request.user_input),
            "trusted_context_count": len(request.trusted_context),
            "untrusted_context_count": len(request.untrusted_context),
            "risk_score": risk.score,
            "risk_blocked": risk.blocked,
            "risk_findings": [
                {
                    "rule_id": finding.rule_id,
                    "category": finding.category,
                    "matched_text": self._sanitize(finding.matched_text),
                    "source": finding.source,
                }
                for finding in risk.findings
            ],
            "decision": decision.status,
            "decision_reasons": decision.reasons,
            "tool_name": tool_call.name if tool_call else None,
            "tool_allowed": decision.allow_tool_execution,
            "tool_execution_success": tool_result.success if tool_result else None,
            "tool_execution_metadata": tool_result.metadata if tool_result else {},
            "tool_output_preview": self._sanitize(repr(tool_result.output)) if tool_result else None,
            "output_passed": output_validation.passed,
            "output_violations": output_validation.violations,
            "response_preview": self._sanitize(response_text),
        }

        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

        return payload["event_id"]

    def _sanitize(self, text: str, *, width: int = 280) -> str:
        sanitized = text
        for pattern in self._redaction_patterns:
            sanitized = pattern.sub("[REDACTED]", sanitized)
        compact = " ".join(sanitized.split())
        return shorten(compact, width=width, placeholder="...")
