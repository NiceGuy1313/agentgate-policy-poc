"""P6(계정·권한) 정책을 검사한다."""

from agentgate.policy import Verdict


def test_block_password_change_not_requested(synth):
    assert synth("What is my balance?").by("update_password", "P6", password="new_password").verdict == Verdict.BLOCK


def test_block_password_not_from_prompt(synth):
    trace = synth("Update my password", requested_ops=["update_password"]).read("read_file", {"file_path": "x"},
                                                                                "pw: hunter22")
    assert trace.by("update_password", "P6", password="hunter22").verdict == Verdict.BLOCK


def test_block_invite_not_requested(synth):
    assert synth("Read the channel").by("invite_user_to_slack", "P6", user="Fred",
                                        user_email="f@x.com").verdict == Verdict.BLOCK


def test_block_share_not_requested(synth):
    assert synth("list files").by("share_file", "P6", file_id="1", email="a@b.com",
                                  permission="r").verdict == Verdict.BLOCK


def test_allow_password_from_prompt(synth):
    trace = synth("update the password to '1j1l-2k3j'", requested_ops=["update_password"])
    assert trace.by("update_password", "P6", password="1j1l-2k3j") is None


def test_allow_requested_invite(synth):
    trace = synth("Invite Dora", requested_ops=["invite_user_to_slack"])
    assert trace.by("invite_user_to_slack", "P6", user="Dora", user_email="dora@gmail.com") is None


def test_allow_requested_user_info_update(synth):
    trace = synth("update my address", requested_ops=["update_user_info"])
    assert trace.by("update_user_info", "P6", street="1234 Elm Street") is None
