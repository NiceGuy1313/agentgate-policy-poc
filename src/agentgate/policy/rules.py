"""P1-P9 as deterministic rules (docs/policy_spec.md §3, docs/policy_impl.md)."""

from __future__ import annotations

import re

from agentgate import normalize as N
from agentgate.policy.base import Call, Context, Decision, Policy
from agentgate.trace import TRUSTED_ORIGINS, Origin, text_of_args


def _origin_verdict(policy: Policy, ctx: Context, arg: str, value, *, named_verdict: str = "escalate",
                    label: str = "destination") -> Decision:
    res = ctx.index.origin_of(value)
    ev = {"arg": arg, "value": value, **res.as_dict()}
    if res.origin in TRUSTED_ORIGINS:
        return policy.allow(f"{label} '{value}' comes from {res.origin.value}.", **ev)
    if res.origin == Origin.NAMED_SOURCE:
        if named_verdict == "allow":
            return policy.allow(f"{label} '{value}' comes from a source named by the user.", **ev)
        return policy.escalate(f"{label} '{value}' comes from a source named by the user, not from the prompt.", **ev)
    if res.origin == Origin.OTHER_TOOL:
        return policy.block(f"{label} '{value}' only appears in an unrequested tool response.", **ev)
    return policy.block(f"{label} '{value}' appears neither in the prompt nor in any tool response.", **ev)


def _worst(decisions: list[Decision]) -> Decision | None:
    from agentgate.policy.base import RANK
    if not decisions:
        return None
    return max(decisions, key=lambda d: RANK[d.verdict])


class P1Destination(Policy):
    """External destinations must come from the prompt (or a named source -> ESCALATE)."""

    policy_id = "P1"

    def applies_to(self, tool):
        return tool in self.pc.get("targets", {})

    def evaluate(self, call: Call, ctx: Context):
        out = []
        for arg in self.pc["targets"][call.tool]:
            for v in N.iter_values(call.args.get(arg)):
                if str(v).strip():
                    out.append(_origin_verdict(self, ctx, arg, v))
        return _worst(out)


class P2SensitiveEgress(Policy):
    """Sink content overlapping sensitive reads is blocked unless egress was requested."""

    policy_id = "P2"

    def applies_to(self, tool):
        return tool in self.config.get("external_sinks", {})

    def evaluate(self, call: Call, ctx: Context):
        if ctx.spec.egress_requested:
            return None
        names = self.config["external_sinks"][call.tool]
        sink = text_of_args(call.args, [n for n in names if n != "attachments"])
        cats = self.pc.get("sensitive_categories")
        hits = ctx.index.overlap(sink, self.pc.get("min_overlap_chars", 16), cats) if sink else []
        tokens = []
        if sink:
            for src in ctx.index.sources[1:]:
                if not set(src.sensitive) & set(cats or []):
                    continue
                for pat in self.pc.get("high_value_token_patterns", []):
                    for m in re.findall(pat, src.text):
                        if m in sink and not N.contains(ctx.index.prompt.hay, m):
                            tokens.append({"token": m, "sid": src.sid, "tool": src.tool})
        attach = _attachment_ids(call)
        if hits or tokens or attach:
            return self.block(
                "Outgoing content contains data from sensitive reads but the user did not ask to send data.",
                matched_reads=[{"sid": h.sid, "tool": h.tool} for h in hits],
                high_value_tokens=tokens[:10], attachments=attach,
            )
        return None


def _attachment_ids(call: Call) -> list[str]:
    ids = []
    if call.tool == "share_file" and call.args.get("file_id") is not None:
        ids.append(str(call.args["file_id"]))
    for a in call.args.get("attachments") or []:
        if isinstance(a, dict) and "file_id" in a:
            ids.append(str(a["file_id"]))
        elif isinstance(a, (str, int)):
            ids.append(str(a))
    return ids


class P3BulkEgress(Policy):
    policy_id = "P3"

    def applies_to(self, tool):
        return tool in self.config.get("external_sinks", {})

    def evaluate(self, call: Call, ctx: Context):
        names = self.config["external_sinks"][call.tool]
        sink = text_of_args(call.args, [n for n in names if n != "attachments"])
        k = (self.config.get("P2") or {}).get("min_overlap_chars", 16)
        cats = (self.config.get("P2") or {}).get("sensitive_categories")
        hits = ctx.index.overlap(sink, k, cats) if sink else []
        # distinct read results: per step, plus distinct items inside list-shaped responses
        distinct = {h.sid for h in hits}
        n_items = _matched_items(sink, hits, k)
        attach = _attachment_ids(call) if self.pc.get("count_attachments", True) and call.tool != "share_file" else []
        count = max(len(distinct), n_items) + len(attach)
        if count >= self.pc.get("min_distinct_reads", 3):
            return self.block(
                f"One outgoing call carries {count} distinct read results (bulk export).",
                matched_reads=sorted(distinct), matched_items=n_items, attachments=attach, count=count,
            )
        return None


def _matched_items(sink: str, hits, k: int) -> int:
    """Count distinct records (e-mails, files, messages) inside matched list responses."""
    from agentgate.trace import _kgrams
    sk = _kgrams(sink, k)
    n = 0
    for h in hits:
        raw_items = getattr(h, "items", None)
        if not raw_items:
            n += 1
            continue
        n += sum(1 for it in raw_items if _kgrams(it, k) & sk)
    return n


class P4Money(Policy):
    policy_id = "P4"

    def applies_to(self, tool):
        return tool in self.pc.get("tools", {})

    def evaluate(self, call: Call, ctx: Context):
        amount_arg = self.pc["tools"][call.tool]
        if call.args.get(amount_arg) is None:
            return None
        amount = N.parse_amount(call.args.get(amount_arg))
        if amount is None:
            return self.escalate("Transfer amount could not be parsed.", amount=call.args.get(amount_arg))
        ev = {"amount": amount}
        spec = ctx.spec
        if spec.amount_cap is not None and amount > spec.amount_cap + 1e-9:
            return self.block(f"Amount {amount} exceeds the cap {spec.amount_cap} implied by the request.", **ev)
        if spec.amount_cap is None:
            if N.amount_in_text(amount, ctx.index.prompt.text):
                pass
            elif spec.named_sources:
                named = [s for s in ctx.index.named_sources() if N.amount_in_text(amount, s.text)]
                if not named:
                    return self.escalate(f"Amount {amount} does not appear in the prompt or the named sources.", **ev)
            else:
                return self.escalate(f"Amount {amount} has no cap and does not appear in the prompt.", **ev)
        limit = self.pc.get("session_cumulative_limit")
        if limit is not None and call.tool != "update_scheduled_transaction" and \
                ctx.session.cumulative_amount + amount > limit + 1e-9:
            return self.block(f"Session total {ctx.session.cumulative_amount + amount} exceeds limit {limit}.", **ev)
        rcp = call.args.get("recipient")
        if rcp and call.tool != "update_scheduled_transaction":
            org = ctx.index.origin_of(rcp).origin
            n_prev = ctx.session.transfers_per_recipient[N.norm_value(rcp)]
            if org not in (Origin.PROMPT, Origin.ALLOWED_DEST, Origin.SELF) and \
                    n_prev + 1 >= self.pc.get("repeat_new_recipient_threshold", 3):
                return self.block(f"Transfer #{n_prev + 1} to the same non-prompt recipient.", recipient=rcp, **ev)
        return None


class P5Delete(Policy):
    policy_id = "P5"

    def applies_to(self, tool):
        return tool in self.pc.get("email_delete_tools", []) or tool in self.pc.get("file_delete_tools", [])

    def evaluate(self, call: Call, ctx: Context):
        if call.tool in self.pc.get("email_delete_tools", []):
            eid = str(call.args.get("email_id"))
            if eid in ctx.session.sent_email_ids:
                return self.block(f"Deleting e-mail {eid} that was sent in this session hides the agent's actions.",
                                  email_id=eid)
            return None
        fid = call.args.get("file_id")
        res = ctx.index.origin_of(fid) if fid is not None else None
        if res is None or res.origin != Origin.PROMPT:
            return self.escalate(f"File {fid} to delete was not identified by the prompt.",
                                 file_id=fid, **(res.as_dict() if res else {}))
        return None


class P6Account(Policy):
    policy_id = "P6"

    def applies_to(self, tool):
        return tool in self.pc.get("tools", [])

    def evaluate(self, call: Call, ctx: Context):
        if call.tool not in ctx.spec.requested_ops:
            return self.block(f"Account/permission change '{call.tool}' was not requested by the user.",
                              tool=call.tool)
        for arg in (self.pc.get("password_args") or {}).get(call.tool, []):
            v = call.args.get(arg)
            if v is not None:
                res = ctx.index.origin_of(v)
                if res.origin != Origin.PROMPT:
                    return self.block("New password does not come from the prompt.", arg=arg, **res.as_dict())
        return None


_URL_IN_TEXT = re.compile(r"(?:https?://|www\.)[^\s'\"<>)]+|\b[a-z0-9-]+\.(?:com|org|net|io|ly|info)(?:/[^\s'\"<>)]*)?",
                          re.IGNORECASE)


class P7Untrusted(Policy):
    policy_id = "P7"

    def applies_to(self, tool):
        return tool in self.pc.get("message_tools", {}) or tool in self.pc.get("visit_tools", {})

    def evaluate(self, call: Call, ctx: Context):
        if call.tool in self.pc.get("visit_tools", {}):
            url = call.args.get(self.pc["visit_tools"][call.tool])
            if url is None:
                return None
            res = ctx.index.origin_of(url)
            ev = {"arg": "url", "value": url, **res.as_dict()}
            if res.origin in TRUSTED_ORIGINS:
                return self.allow("URL comes from the prompt.", **ev)
            if res.origin == Origin.NAMED_SOURCE:
                return self.allow("URL is a link inside a source named by the user.", **ev)
            return self.escalate("URL to visit only appears in an unrequested tool response.", **ev)
        if call.tool not in self.pc.get("message_tools", {}):
            return None
        text = text_of_args(call.args, self.pc["message_tools"][call.tool])
        low = text.lower()
        phrases = [p for p in self.pc.get("instruction_phrases", []) if p in low]
        if phrases:
            return self.block("Message propagates instruction text from a tool response.", phrases=phrases)
        bad, seen = [], []
        for u in _URL_IN_TEXT.findall(text):
            if "@" in u:
                continue
            res = ctx.index.origin_of(u.rstrip(".,;"))
            seen.append({"url": u, **res.as_dict()})
            if res.origin == Origin.OTHER_TOOL:
                bad.append({"url": u, **res.as_dict()})
        if bad:
            return self.block("Message contains a link that only appears in an unrequested tool response.",
                              urls=bad)
        if seen:
            named = [x for x in seen if x["origin"] == Origin.NAMED_SOURCE.value]
            ev = named[0] if named else seen[0]
            return self.allow("Links in the message come from the prompt or named sources.", urls=seen,
                              origin=ev["origin"], step=ev["step"], tool=ev["tool"])
        return None


class P8Reservation(Policy):
    policy_id = "P8"

    def applies_to(self, tool):
        return tool in self.pc.get("tools", {})

    def evaluate(self, call: Call, ctx: Context):
        if call.tool not in ctx.spec.requested_ops:
            return self.block(f"Reservation '{call.tool}' was not requested by the user.", tool=call.tool)
        target = call.args.get(self.pc["tools"][call.tool])
        if target is None:
            return None
        res = ctx.index.origin_of(target)
        if res.origin in TRUSTED_ORIGINS or res.origin == Origin.NAMED_SOURCE:
            return self.allow(f"Reservation target '{target}' comes from {res.origin.value}.", value=target,
                              **res.as_dict())
        return self.escalate(f"Reservation target '{target}' is not in the prompt or a named source.",
                             value=target, **res.as_dict())


class P9Calendar(Policy):
    policy_id = "P9"

    def applies_to(self, tool):
        return tool in self.pc.get("tools", [])

    def evaluate(self, call: Call, ctx: Context):
        if call.tool not in ctx.spec.requested_ops:
            return self.block(f"Calendar change '{call.tool}' was not requested by the user.", tool=call.tool)
        return None


ALL_POLICIES = [P1Destination, P2SensitiveEgress, P3BulkEgress, P4Money, P5Delete, P6Account,
                P7Untrusted, P8Reservation, P9Calendar]
