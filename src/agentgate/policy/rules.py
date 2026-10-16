"""P1–P9를 결정론적 규칙으로 구현한다(docs/policy_spec.md 2절). P0·P10은 v2(명세서.md 2절)이다."""

from __future__ import annotations

import re

from agentgate.config import load_effect_tools
from agentgate.normalization import (
    extract_typed_candidates,
    haystack_contains,
    iterate_leaf_values,
    normalize_value,
    parse_amount,
    text_contains_amount,
)
from agentgate.policy.base import VERDICT_SEVERITY, Policy, PolicyContext, PolicyDecision, ToolCall
from agentgate.provenance import TRUSTED_ORIGINS, Origin, compute_kgram_hashes, join_argument_text


def _decide_by_origin(policy: Policy, context: PolicyContext, arg: str, value, *, named_verdict: str = "escalate",
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


def _v31(context: PolicyContext, flag: str) -> bool:
    """v3.1 behaviour switches (config "v31"); absent in v1/v2/v3 configs, so those keep their decisions."""
    return bool((context.config.get("v31") or {}).get(flag))


def _delegated_request_match(call: ToolCall, context: PolicyContext) -> bool:
    """A1: the call carries out a request found in a source the user delegated (TaskSpec.delegated_sources)."""
    return any(m["delegated"] for m in context.value_index.instructed_requests(call.tool, call.args))


def _unrequested(policy: Policy, call: ToolCall, context: PolicyContext, label: str) -> PolicyDecision:
    if _v31(context, "delegation_escalates") and _delegated_request_match(call, context):
        return policy.escalate(f"{label} '{call.tool}' was not requested directly but matches a request in a "
                               "source the user delegated.", tool=call.tool, delegated=True)
    return policy.block(f"{label} '{call.tool}' was not requested by the user.", tool=call.tool)


def _strictest_decision(decisions: list[PolicyDecision]) -> PolicyDecision | None:
    """The first decision with the highest severity, or None."""
    if not decisions:
        return None
    return max(decisions, key=lambda d: VERDICT_SEVERITY[d.verdict])


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
                    decisions.append(_decide_by_origin(self, context, arg, value))
        return _strictest_decision(decisions)


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
            if _v31(context, "requested_egress_escalates") and _requested_egress_to_known_destination(
                    self.config, call, context):
                return self.escalate(
                    f"One outgoing call carries {count} distinct read results, but the user asked to send data "
                    "to this destination.", matched_reads=sorted(distinct_source_ids),
                    matched_items=matched_record_count, attachments=attachments, count=count)
            return self.block(
                f"One outgoing call carries {count} distinct read results (bulk export).",
                matched_reads=sorted(distinct_source_ids), matched_items=matched_record_count,
                attachments=attachments, count=count,
            )
        return None


def _requested_egress_to_known_destination(config: dict, call: ToolCall, context: PolicyContext) -> bool:
    """C1: the user asked to send data, and every destination comes from the prompt / user / directory or a
    source the user named."""
    if not context.spec.egress_requested:
        return False
    destinations = [v for arg in (config.get("P1") or {}).get("targets", {}).get(call.tool, [])
                    for v in iterate_leaf_values(call.args.get(arg)) if str(v).strip()]
    if not destinations:
        return False
    allowed = TRUSTED_ORIGINS | {Origin.NAMED_SOURCE}
    return all(context.value_index.origin_of(v).origin in allowed for v in destinations)


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


class AccountPermissionPolicy(Policy):
    """P6: account and permission changes must be requested; new passwords must come from the prompt."""

    policy_id = "P6"

    def applies_to(self, tool):
        return tool in self.policy_config.get("tools", [])

    def evaluate(self, call: ToolCall, context: PolicyContext):
        if call.tool not in context.spec.requested_ops:
            return _unrequested(self, call, context, "Account/permission change")
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


class ReservationPolicy(Policy):
    """P8: reservations must be requested and target something from the prompt or a named source."""

    policy_id = "P8"

    def applies_to(self, tool):
        return tool in self.policy_config.get("tools", {})

    def evaluate(self, call: ToolCall, context: PolicyContext):
        if call.tool not in context.spec.requested_ops:
            return _unrequested(self, call, context, "Reservation")
        target = call.args.get(self.policy_config["tools"][call.tool])
        if target is None:
            return None
        provenance = context.value_index.origin_of(target)
        if provenance.origin in TRUSTED_ORIGINS or provenance.origin == Origin.NAMED_SOURCE:
            return self.allow(f"Reservation target '{target}' comes from {provenance.origin.value}.", value=target,
                              **provenance.as_dict())
        return self.escalate(f"Reservation target '{target}' is not in the prompt or a named source.",
                             value=target, **provenance.as_dict())


class CalendarChangePolicy(Policy):
    """P9: calendar changes must be requested by the user."""

    policy_id = "P9"

    def applies_to(self, tool):
        return tool in self.policy_config.get("tools", [])

    def evaluate(self, call: ToolCall, context: PolicyContext):
        if call.tool not in context.spec.requested_ops:
            return _unrequested(self, call, context, "Calendar change")
        return None


class UserFixedPolicy(Policy):
    """P0: the user's fixed settings (config/user_policy.yaml) apply to every task, on top of the TaskSpec."""

    policy_id = "P0"

    def __init__(self, config: dict):
        super().__init__(config)
        user_policy = config.get("user_policy") or {}
        self.always_block = set(user_policy.get("always_block") or [])
        self.always_human = set(user_policy.get("always_human") or [])

    def applies_to(self, tool):
        return tool in self.always_block or tool in self.always_human

    def evaluate(self, call: ToolCall, context: PolicyContext):
        if call.tool in self.always_block:
            return self.block(f"User policy blocks '{call.tool}' in every task.", layer="user_policy",
                              rule="always_block")
        if _v31(context, "human_unless_requested") and call.tool in context.spec.requested_ops:
            return None   # D2: the prompt itself asked for this action
        return self.escalate(f"User policy requires human confirmation for '{call.tool}'.", layer="user_policy",
                             rule="always_human", force_human=True)


class DetectorLabelPolicy(Policy):
    """P10: an argument value taken from a response the detector flagged needs review (명세서.md 2절).

    Reads only the DetectionResult labels the runtime attached to the responses; never runs a model.
    A value from a NAMED_SOURCE / OTHER_TOOL response that is flagged, or that sits inside a suspicious chunk,
    gives ESCALATE; an OTHER_TOOL value inside a suspicious chunk gives BLOCK.
    """

    policy_id = "P10"
    LONG_VALUE_CHARS = 200    # longer values (message bodies) are checked through the typed values inside them
    MIN_VALUE_CHARS = 3

    def __init__(self, config: dict):
        super().__init__(config)
        self.effect_tools = load_effect_tools()

    def applies_to(self, tool):
        return tool in self.effect_tools

    def evaluate(self, call: ToolCall, context: PolicyContext):
        decisions = []
        for arg, raw in call.args.items():
            for value in iterate_leaf_values(raw):
                for candidate in _detector_candidates(value, self.LONG_VALUE_CHARS):
                    if len(candidate.strip()) < self.MIN_VALUE_CHARS:
                        continue
                    provenance = context.value_index.origin_of(candidate)
                    if provenance.origin not in (Origin.NAMED_SOURCE, Origin.OTHER_TOOL):
                        continue
                    label = context.value_index.value_detection(candidate, provenance.source_id)
                    if label is None or not (label["flagged"] or label["in_suspicious_span"]):
                        continue
                    evidence = {"arg": arg, "value": candidate, **provenance.as_dict(), "detection": label}
                    if provenance.origin == Origin.OTHER_TOOL and label["in_suspicious_span"]:
                        decisions.append(self.block(
                            f"'{candidate}' sits in a chunk the detector flagged, in an unrequested response.",
                            **evidence))
                    else:
                        decisions.append(self.escalate(
                            f"'{candidate}' comes from a response the detector flagged "
                            f"(score {label['response_score']}, value chunk {label['value_score']}).", **evidence))
        return _strictest_decision(decisions)


def _detector_candidates(value, long_chars: int) -> list[str]:
    text = str(value)
    if len(text) <= long_chars:
        return [text]
    candidates = extract_typed_candidates(text)
    return sorted(candidates["url"] | candidates["email"] | candidates["iban"])


class InstructedActionPolicy(Policy):
    """P11 (rules R3, v3): a call that carries out a request found inside a tool output.

    Reads only the validated ToolSpec labels the runtime attached to the responses (no model calls here).
    Not requested by the user and not from a delegated source -> BLOCK; otherwise -> ESCALATE (judge rule J1).
    """

    policy_id = "P11"

    def __init__(self, config: dict):
        super().__init__(config)
        self.effect_tools = load_effect_tools()

    def applies_to(self, tool):
        return tool in self.effect_tools

    def evaluate(self, call: ToolCall, context: PolicyContext):
        matches = context.value_index.instructed_requests(call.tool, call.args)
        if not matches:
            return None
        visit_arg = (self.config.get("P7") or {}).get("visit_tools", {}).get(call.tool)
        if visit_arg and _v31(context, "visit_named_defers_to_p7"):
            origin = context.value_index.origin_of(call.args.get(visit_arg)).origin
            if origin in TRUSTED_ORIGINS or origin == Origin.NAMED_SOURCE:
                # B1: opening a link inside a named source is reading, not an action the tool output imposed;
                # it may still be a planted request, so the judge decides (J3, J5) instead of a hard BLOCK
                return self.escalate(f"'{call.tool}' opens a link that a tool output asks to open, inside a source "
                                     "the user named.", matched_tool_output_requests=matches[:5], requested=False)
        requested = call.tool in context.spec.requested_ops
        delegated = [m for m in matches if m["delegated"]]
        evidence = {"matched_tool_output_requests": matches[:5], "requested": requested}
        if not requested and not delegated:
            return self.block(f"'{call.tool}' carries out a request found in a tool output "
                              f"({matches[0]['source_id']}:{matches[0]['tool']}), which the user did not ask for.",
                              **evidence)
        return self.escalate(f"'{call.tool}' matches a request found in a tool output "
                             f"({'delegated source' if delegated else 'action also requested by the user'}).",
                             **evidence)


class RequestedActionPolicy(Policy):
    """P12 (rule R1, v3): state-changing / outbound tools must be requested by the user.

    Generalizes P6/P8/P9 to every effect tool except URL visits (P7 handles those). Unrequested calls that also
    carry out a tool-output request are blocked by P11; the rest go to the judge.
    """

    policy_id = "P12"
    EXCLUDED = ("visit",)

    def __init__(self, config: dict):
        super().__init__(config)
        from agentgate.config import load_config_yaml
        roles = load_config_yaml("tool_roles.yaml")["effect"]
        self.tools = {t for cls, tools in roles.items() if cls not in self.EXCLUDED for t in tools}

    def applies_to(self, tool):
        return tool in self.tools

    def evaluate(self, call: ToolCall, context: PolicyContext):
        if call.tool in context.spec.requested_ops:
            return None
        return self.escalate(f"'{call.tool}' was not requested by the user.", tool=call.tool)


POLICY_CLASSES = [UserFixedPolicy, DestinationProvenancePolicy, SensitiveEgressPolicy, BulkEgressPolicy, MoneyTransferPolicy,
                  DeletionPolicy, AccountPermissionPolicy, UntrustedContentPolicy, ReservationPolicy,
                  CalendarChangePolicy, DetectorLabelPolicy, InstructedActionPolicy, RequestedActionPolicy]
