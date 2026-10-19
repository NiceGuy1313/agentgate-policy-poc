"""v1 정책 P1–P9를 결정론적 규칙으로 구현한다(docs/policy_spec.md 2절).

v3.1은 이 클래스를 서브클래스로 바꾼다. 이 파일에는 v1 동작만 둔다.
"""

from __future__ import annotations

import re

from agentgate.gate.base import VERDICT_SEVERITY, Policy, PolicyContext, PolicyDecision, ToolCall
from agentgate.gate.normalization import (
    haystack_contains,
    iterate_leaf_values,
    normalize_value,
    parse_amount,
    text_contains_amount,
)
from agentgate.gate.provenance import TRUSTED_ORIGINS, Origin, compute_kgram_hashes, join_argument_text


def decide_by_origin(policy: Policy, context: PolicyContext, arg: str, value, *, named_verdict: str = "escalate",
                      label: str = "destination") -> PolicyDecision:
    provenance = context.value_index.origin_of(value)
    evidence = {"arg": arg, "value": value, **provenance.as_dict()}
    if provenance.origin in TRUSTED_ORIGINS:
        return policy.allow(f"{label} '{value}' comes from {provenance.origin.value}.", **evidence)
    if provenance.origin == Origin.NAMED_SOURCE:
        if named_verdict == "allow":
            return policy.allow(f"{label} '{value}' comes from a source named by the user.", **evidence)
        return policy.escalate(f"{label} '{value}' comes from a source named by the user, not from the prompt.",
                               **evidence)
    if provenance.origin == Origin.OTHER_TOOL:
        return policy.block(f"{label} '{value}' only appears in an unrequested tool response.", **evidence)
    return policy.block(f"{label} '{value}' appears neither in the prompt nor in any tool response.", **evidence)


def strictest_decision(decisions: list[PolicyDecision]) -> PolicyDecision | None:
    """The first decision with the highest severity, or None."""
    if not decisions:
        return None
    return max(decisions, key=lambda d: VERDICT_SEVERITY[d.verdict])


class UnrequestedActionMixin:
    """P6·P8·P9: an action the user did not request. v3.1 overrides `unrequested` (A1)."""

    def unrequested(self, call: ToolCall, context: PolicyContext, label: str) -> PolicyDecision:
        return self.block(f"{label} '{call.tool}' was not requested by the user.", tool=call.tool)


class DestinationProvenancePolicy(Policy):
    """P1: external destinations must come from the prompt (a named source gives ESCALATE)."""

    policy_id = "P1"

    def applies_to(self, tool):
        return tool in self.policy_config.get("targets", {})

    def evaluate(self, call: ToolCall, context: PolicyContext):
        decisions = []
        for arg in self.policy_config["targets"][call.tool]:
            for value in iterate_leaf_values(call.args.get(arg)):
                if str(value).strip():
                    decisions.append(decide_by_origin(self, context, arg, value))
        return strictest_decision(decisions)


class SensitiveEgressPolicy(Policy):
    """P2: sink content overlapping sensitive reads is blocked unless the user asked to send data."""

    policy_id = "P2"

    def applies_to(self, tool):
        return tool in self.config.get("external_sinks", {})

    def evaluate(self, call: ToolCall, context: PolicyContext):
        if context.spec.egress_requested:
            return None
        sink_arg_names = self.config["external_sinks"][call.tool]
        sink = join_argument_text(call.args, [n for n in sink_arg_names if n != "attachments"])
        categories = self.policy_config.get("sensitive_categories")
        hits = (context.value_index.find_overlapping_sources(sink, self.policy_config.get("min_overlap_chars", 16),
                                                             categories)
                if sink else [])
        tokens = []
        if sink:
            for source in context.value_index.sources[1:]:
                if not set(source.sensitive_categories) & set(categories or []):
                    continue
                for pattern in self.policy_config.get("high_value_token_patterns", []):
                    for token in re.findall(pattern, source.text):
                        if token in sink and not haystack_contains(context.value_index.prompt.haystack, token):
                            tokens.append({"token": token, "sid": source.source_id, "tool": source.tool})
        attachments = _attachment_file_ids(call)
        if hits or tokens or attachments:
            return self.block(
                "Outgoing content contains data from sensitive reads but the user did not ask to send data.",
                matched_reads=[{"sid": h.source_id, "tool": h.tool} for h in hits],
                high_value_tokens=tokens[:10], attachments=attachments,
            )
        return None


def _attachment_file_ids(call: ToolCall) -> list[str]:
    file_ids = []
    if call.tool == "share_file" and call.args.get("file_id") is not None:
        file_ids.append(str(call.args["file_id"]))
    for attachment in call.args.get("attachments") or []:
        if isinstance(attachment, dict) and "file_id" in attachment:
            file_ids.append(str(attachment["file_id"]))
        elif isinstance(attachment, (str, int)):
            file_ids.append(str(attachment))
    return file_ids


class BulkEgressPolicy(Policy):
    """P3: one outgoing call must not carry many distinct read results (bulk export)."""

    policy_id = "P3"

    def applies_to(self, tool):
        return tool in self.config.get("external_sinks", {})

    def evaluate(self, call: ToolCall, context: PolicyContext):
        sink_arg_names = self.config["external_sinks"][call.tool]
        sink = join_argument_text(call.args, [n for n in sink_arg_names if n != "attachments"])
        # P3 reuses P2's overlap threshold and sensitive categories.
        k = (self.config.get("P2") or {}).get("min_overlap_chars", 16)
        categories = (self.config.get("P2") or {}).get("sensitive_categories")
        hits = context.value_index.find_overlapping_sources(sink, k, categories) if sink else []
        # Distinct read results: one per step, plus distinct records inside list-shaped responses.
        distinct_source_ids = {h.source_id for h in hits}
        matched_record_count = _count_matched_records(sink, hits, k)
        attachments = (_attachment_file_ids(call)
                       if self.policy_config.get("count_attachments", True) and call.tool != "share_file" else [])
        count = max(len(distinct_source_ids), matched_record_count) + len(attachments)
        if count >= self.policy_config.get("min_distinct_reads", 3):
            return self.bulk_decision(call, context, {"matched_reads": sorted(distinct_source_ids),
                                                      "matched_items": matched_record_count,
                                                      "attachments": attachments, "count": count})
        return None

    def bulk_decision(self, call: ToolCall, context: PolicyContext, evidence: dict) -> PolicyDecision:
        """v3.1 overrides this (C1)."""
        return self.block(f"One outgoing call carries {evidence['count']} distinct read results (bulk export).",
                          **evidence)


def _count_matched_records(sink: str, hits, k: int) -> int:
    """Count distinct records (e-mails, files, messages) inside matched list responses."""
    sink_hashes = compute_kgram_hashes(sink, k)
    count = 0
    for hit in hits:
        record_texts = getattr(hit, "record_texts", None)
        if not record_texts:
            count += 1
            continue
        count += sum(1 for record in record_texts if compute_kgram_hashes(record, k) & sink_hashes)
    return count


class MoneyTransferPolicy(Policy):
    """P4: transfer amounts must be capped or grounded in the request; limits on totals and repeat recipients."""

    policy_id = "P4"

    def applies_to(self, tool):
        return tool in self.policy_config.get("tools", {})

    def evaluate(self, call: ToolCall, context: PolicyContext):
        amount_arg = self.policy_config["tools"][call.tool]
        if call.args.get(amount_arg) is None:
            return None
        amount = parse_amount(call.args.get(amount_arg))
        if amount is None:
            return self.escalate("Transfer amount could not be parsed.", amount=call.args.get(amount_arg))
        evidence = {"amount": amount}
        spec = context.spec
        if spec.amount_cap is not None and amount > spec.amount_cap + 1e-9:
            return self.block(f"Amount {amount} exceeds the cap {spec.amount_cap} implied by the request.",
                              **evidence)
        if spec.amount_cap is None:
            if text_contains_amount(amount, context.value_index.prompt.text):
                pass
            elif spec.named_sources:
                named = [s for s in context.value_index.named_source_responses()
                         if text_contains_amount(amount, s.text)]
                if not named:
                    return self.escalate(f"Amount {amount} does not appear in the prompt or the named sources.",
                                         **evidence)
            else:
                return self.escalate(f"Amount {amount} has no cap and does not appear in the prompt.", **evidence)
        limit = self.policy_config.get("session_cumulative_limit")
        if limit is not None and call.tool != "update_scheduled_transaction" and \
                context.session.cumulative_amount + amount > limit + 1e-9:
            return self.block(f"Session total {context.session.cumulative_amount + amount} exceeds limit {limit}.",
                              **evidence)
        recipient = call.args.get("recipient")
        if recipient and call.tool != "update_scheduled_transaction":
            origin = context.value_index.origin_of(recipient).origin
            previous_transfers = context.session.transfers_per_recipient[normalize_value(recipient)]
            if origin not in (Origin.PROMPT, Origin.ALLOWED_DEST, Origin.SELF) and \
                    previous_transfers + 1 >= self.policy_config.get("repeat_new_recipient_threshold", 3):
                return self.block(f"Transfer #{previous_transfers + 1} to the same non-prompt recipient.",
                                  recipient=recipient, **evidence)
        return None


class DeletionPolicy(Policy):
    """P5: no deleting e-mails sent in this session; file deletions must target prompt-identified files."""

    policy_id = "P5"

    def applies_to(self, tool):
        return (tool in self.policy_config.get("email_delete_tools", [])
                or tool in self.policy_config.get("file_delete_tools", []))

    def evaluate(self, call: ToolCall, context: PolicyContext):
        if call.tool in self.policy_config.get("email_delete_tools", []):
            email_id = str(call.args.get("email_id"))
            if email_id in context.session.sent_email_ids:
                return self.block(
                    f"Deleting e-mail {email_id} that was sent in this session hides the agent's actions.",
                    email_id=email_id)
            return None
        file_id = call.args.get("file_id")
        provenance = context.value_index.origin_of(file_id) if file_id is not None else None
        if provenance is None or provenance.origin != Origin.PROMPT:
            return self.escalate(f"File {file_id} to delete was not identified by the prompt.",
                                 file_id=file_id, **(provenance.as_dict() if provenance else {}))
        return None


class AccountPermissionPolicy(UnrequestedActionMixin, Policy):
    """P6: account and permission changes must be requested; new passwords must come from the prompt."""

    policy_id = "P6"

    def applies_to(self, tool):
        return tool in self.policy_config.get("tools", [])

    def evaluate(self, call: ToolCall, context: PolicyContext):
        if call.tool not in context.spec.requested_ops:
            return self.unrequested(call, context, "Account/permission change")
        for arg in (self.policy_config.get("password_args") or {}).get(call.tool, []):
            value = call.args.get(arg)
            if value is not None:
                provenance = context.value_index.origin_of(value)
                if provenance.origin != Origin.PROMPT:
                    return self.block("New password does not come from the prompt.", arg=arg,
                                      **provenance.as_dict())
        return None


_URL_IN_TEXT_PATTERN = re.compile(
    r"(?:https?://|www\.)[^\s'\"<>)]+|\b[a-z0-9-]+\.(?:com|org|net|io|ly|info)(?:/[^\s'\"<>)]*)?", re.IGNORECASE)


class UntrustedContentPolicy(Policy):
    """P7: no visiting unrequested URLs or propagating instructions/links from untrusted tool responses."""

    policy_id = "P7"

    def applies_to(self, tool):
        return tool in self.policy_config.get("message_tools", {}) or tool in self.policy_config.get("visit_tools", {})

    def evaluate(self, call: ToolCall, context: PolicyContext):
        if call.tool in self.policy_config.get("visit_tools", {}):
            url = call.args.get(self.policy_config["visit_tools"][call.tool])
            if url is None:
                return None
            provenance = context.value_index.origin_of(url)
            evidence = {"arg": "url", "value": url, **provenance.as_dict()}
            if provenance.origin in TRUSTED_ORIGINS:
                return self.allow("URL comes from the prompt.", **evidence)
            if provenance.origin == Origin.NAMED_SOURCE:
                return self.allow("URL is a link inside a source named by the user.", **evidence)
            return self.escalate("URL to visit only appears in an unrequested tool response.", **evidence)
        if call.tool not in self.policy_config.get("message_tools", {}):
            return None
        text = join_argument_text(call.args, self.policy_config["message_tools"][call.tool])
        lowered_text = text.lower()
        phrases = [p for p in self.policy_config.get("instruction_phrases", []) if p in lowered_text]
        if phrases:
            return self.block("Message propagates instruction text from a tool response.", phrases=phrases)
        untrusted_urls, seen_urls = [], []
        for url in _URL_IN_TEXT_PATTERN.findall(text):
            if "@" in url:
                continue
            provenance = context.value_index.origin_of(url.rstrip(".,;"))
            seen_urls.append({"url": url, **provenance.as_dict()})
            if provenance.origin == Origin.OTHER_TOOL:
                untrusted_urls.append({"url": url, **provenance.as_dict()})
        if untrusted_urls:
            return self.block("Message contains a link that only appears in an unrequested tool response.",
                              urls=untrusted_urls)
        if seen_urls:
            named = [u for u in seen_urls if u["origin"] == Origin.NAMED_SOURCE.value]
            reported = named[0] if named else seen_urls[0]
            return self.allow("Links in the message come from the prompt or named sources.", urls=seen_urls,
                              origin=reported["origin"], step=reported["step"], tool=reported["tool"])
        return None


class ReservationPolicy(UnrequestedActionMixin, Policy):
    """P8: reservations must be requested and target something from the prompt or a named source."""

    policy_id = "P8"

    def applies_to(self, tool):
        return tool in self.policy_config.get("tools", {})

    def evaluate(self, call: ToolCall, context: PolicyContext):
        if call.tool not in context.spec.requested_ops:
            return self.unrequested(call, context, "Reservation")
        target = call.args.get(self.policy_config["tools"][call.tool])
        if target is None:
            return None
        provenance = context.value_index.origin_of(target)
        if provenance.origin in TRUSTED_ORIGINS or provenance.origin == Origin.NAMED_SOURCE:
            return self.allow(f"Reservation target '{target}' comes from {provenance.origin.value}.", value=target,
                              **provenance.as_dict())
        return self.escalate(f"Reservation target '{target}' is not in the prompt or a named source.",
                             value=target, **provenance.as_dict())


class CalendarChangePolicy(UnrequestedActionMixin, Policy):
    """P9: calendar changes must be requested by the user."""

    policy_id = "P9"

    def applies_to(self, tool):
        return tool in self.policy_config.get("tools", [])

    def evaluate(self, call: ToolCall, context: PolicyContext):
        if call.tool not in context.spec.requested_ops:
            return self.unrequested(call, context, "Calendar change")
        return None


POLICY_CLASSES = [DestinationProvenancePolicy, SensitiveEgressPolicy, BulkEgressPolicy, MoneyTransferPolicy,
                  DeletionPolicy, AccountPermissionPolicy, UntrustedContentPolicy, ReservationPolicy,
                  CalendarChangePolicy]
