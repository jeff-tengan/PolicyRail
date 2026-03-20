from __future__ import annotations

from textwrap import shorten
from typing import Protocol

from ..core.detectors import PromptInjectionDetector
from ..core.models import (
    LLMResponse,
    OutputValidation,
    PolicyDecision,
    PromptEnvelope,
    RiskAssessment,
    SecureRequest,
    SecureResponse,
    ToolCall,
    ToolExecutionResult,
)
from ..core.partitioning import ContextPartitioner
from ..core.policies import PolicyEngine
from ..core.validators import OutputValidator
from ..observability.audit import JsonAuditLogger


class LLMAdapter(Protocol):
    def generate(self, envelope: PromptEnvelope) -> LLMResponse:
        ...


class ToolExecutor(Protocol):
    def execute(self, tool_call: ToolCall) -> ToolExecutionResult:
        ...


class MockLLMAdapter:
    def generate(self, envelope: PromptEnvelope) -> LLMResponse:
        user_input = envelope.user_input.lower()

        if "email" in user_input or "e-mail" in user_input or "enviar" in user_input:
            return LLMResponse(
                text="Posso preparar um rascunho de email, mas a execucao deve seguir a policy.",
                tool_call=ToolCall(
                    name="send_email",
                    arguments={
                        "subject": "Acao solicitada pelo usuario",
                        "body": shorten(envelope.user_input, width=140, placeholder="..."),
                    },
                ),
            )

        if "pesquise" in user_input or "busque" in user_input or "search" in user_input:
            return LLMResponse(
                text="Consulta preparada usando a base de conhecimento autorizada.",
                tool_call=ToolCall(
                    name="search_docs",
                    arguments={"query": envelope.user_input},
                ),
            )

        if "prompt" in user_input or "segredo" in user_input or "token" in user_input:
            return LLMResponse(
                text="Nao posso revelar prompts internos, segredos ou credenciais do sistema."
            )

        trusted_summary = (
            shorten(envelope.trusted_context[0], width=120, placeholder="...")
            if envelope.trusted_context
            else "nenhum contexto confiavel foi fornecido"
        )
        return LLMResponse(
            text=(
                "Resposta segura gerada com prioridade para contexto confiavel: "
                f"{trusted_summary}."
            )
        )


class SecureGenAIPipeline:
    def __init__(
        self,
        *,
        detector: PromptInjectionDetector | None = None,
        partitioner: ContextPartitioner | None = None,
        policy_engine: PolicyEngine | None = None,
        output_validator: OutputValidator | None = None,
        audit_logger: JsonAuditLogger | None = None,
        llm_adapter: LLMAdapter | None = None,
        tool_executor: ToolExecutor | None = None,
    ) -> None:
        self.detector = detector or PromptInjectionDetector()
        self.partitioner = partitioner or ContextPartitioner()
        self.policy_engine = policy_engine or self._build_default_policy_engine()
        self.output_validator = output_validator or OutputValidator()
        self.audit_logger = audit_logger or JsonAuditLogger()
        self.llm_adapter = llm_adapter or MockLLMAdapter()
        self.tool_executor = tool_executor

    def process(self, request: SecureRequest) -> SecureResponse:
        envelope = self.partitioner.build_envelope(request)
        risk = self._assess_risk(request)

        pre_decision = self.policy_engine.evaluate(risk)
        if pre_decision.status == "block":
            return self._finalize_response(
                request=request,
                envelope=envelope,
                risk=risk,
                decision=pre_decision,
                output_validation=self.output_validator.validate(
                    "Solicitacao bloqueada pela policy de seguranca."
                ),
                response_text="Solicitacao bloqueada pela policy de seguranca.",
                tool_call=None,
                tool_result=None,
                model_metadata={},
            )

        llm_response = self.llm_adapter.generate(envelope)
        decision = self.policy_engine.evaluate(risk, llm_response.tool_call)
        response_text = self._shape_response_text(decision, llm_response.text, llm_response.tool_call)
        output_validation = self.output_validator.validate(response_text)
        tool_call = llm_response.tool_call if decision.allow_tool_execution else None

        if not output_validation.passed:
            hardened_decision = self._escalate_output_violation(decision, output_validation)
            return self._finalize_response(
                request=request,
                envelope=envelope,
                risk=risk,
                decision=hardened_decision,
                output_validation=output_validation,
                response_text="A saida do modelo foi retida por potencial vazamento de informacao sensivel.",
                tool_call=None,
                tool_result=None,
                model_metadata=llm_response.metadata,
            )

        tool_result = self._execute_tool(tool_call)
        model_metadata = dict(llm_response.metadata)
        if tool_result is not None:
            model_metadata["tool_execution"] = {
                "tool_name": tool_result.tool_name,
                "success": tool_result.success,
                "metadata": dict(tool_result.metadata),
            }

        return self._finalize_response(
            request=request,
            envelope=envelope,
            risk=risk,
            decision=decision,
            output_validation=output_validation,
            response_text=response_text,
            tool_call=tool_call,
            tool_result=tool_result,
            model_metadata=model_metadata,
        )

    def _assess_risk(self, request: SecureRequest) -> RiskAssessment:
        assessments = [self.detector.detect(request.user_input, source="user_input")]
        if request.untrusted_context:
            assessments.append(
                self.detector.detect(
                    "\n".join(request.untrusted_context),
                    source="untrusted_context",
                )
            )
        return self.detector.merge(*assessments)

    def _shape_response_text(
        self,
        decision: PolicyDecision,
        model_text: str,
        tool_call: ToolCall | None,
    ) -> str:
        if decision.status == "block":
            return "Acao bloqueada pela policy de seguranca."
        if decision.status == "review" and tool_call is not None:
            return (
                f"{model_text}\n\n"
                "A tool solicitada foi retida para revisao humana antes da execucao."
            )
        if decision.status == "review":
            return f"{model_text}\n\nResposta sinalizada para revisao humana."
        return model_text

    def _finalize_response(
        self,
        *,
        request: SecureRequest,
        envelope: PromptEnvelope,
        risk: RiskAssessment,
        decision: PolicyDecision,
        output_validation: OutputValidation,
        response_text: str,
        tool_call: ToolCall | None,
        tool_result: ToolExecutionResult | None,
        model_metadata: dict | None = None,
    ) -> SecureResponse:
        response = SecureResponse(
            status=decision.status,
            response_text=response_text,
            risk=risk,
            decision=decision,
            output_validation=output_validation,
            tool_call=tool_call,
            tool_result=tool_result,
            envelope=envelope,
            model_metadata=dict(model_metadata or {}),
        )
        response.audit_id = self.audit_logger.record_interaction(
            request=request,
            risk=risk,
            decision=decision,
            output_validation=output_validation,
            response_text=response_text,
            tool_call=tool_call,
            tool_result=tool_result,
        )
        return response

    def _execute_tool(self, tool_call: ToolCall | None) -> ToolExecutionResult | None:
        if tool_call is None or self.tool_executor is None:
            return None
        try:
            return self.tool_executor.execute(tool_call)
        except Exception as exc:
            return ToolExecutionResult(
                tool_name=tool_call.name,
                arguments=dict(tool_call.arguments),
                success=False,
                output=None,
                metadata={
                    "executor": type(self.tool_executor).__name__,
                    "error": exc.__class__.__name__,
                    "detail": str(exc),
                },
            )

    def _escalate_output_violation(
        self,
        decision: PolicyDecision,
        validation: OutputValidation,
    ) -> PolicyDecision:
        reasons = list(decision.reasons)
        reasons.extend(validation.violations)
        reasons.append("Saida bloqueada pelo validador final.")
        return PolicyDecision(
            status="block",
            reasons=list(dict.fromkeys(reasons)),
            allow_tool_execution=False,
        )

    @staticmethod
    def _build_default_policy_engine() -> PolicyEngine:
        from ..templates.system_policies import default_tool_specs

        return PolicyEngine(default_tool_specs())
