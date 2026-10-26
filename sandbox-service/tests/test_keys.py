from __future__ import annotations

import pytest

from src.errors import SandboxError
from src.keys import MAX_ID_LENGTH, KernelKey, SandboxKey


def test_digest_is_stable_and_distinguishes_every_field():
    a = SandboxKey("t", "w", "u")
    assert a.digest == SandboxKey("t", "w", "u").digest
    assert len({a.digest, SandboxKey("t2", "w", "u").digest, SandboxKey("t", "w2", "u").digest,
                SandboxKey("t", "w", "u2").digest}) == 4


def test_digest_has_no_field_boundary_ambiguity():
    assert SandboxKey("ab", "c", "d").digest != SandboxKey("a", "bc", "d").digest


@pytest.mark.parametrize("bad", ["", "   ", "x" * (MAX_ID_LENGTH + 1), "a\x1fb"])
def test_ids_are_validated(bad):
    with pytest.raises(SandboxError) as ei:
        SandboxKey(bad, "w", "u")
    assert ei.value.kind == "invalid_request"
    with pytest.raises(SandboxError):
        KernelKey(SandboxKey("t", "w", "u"), bad)


def test_unsupported_language_is_rejected():
    with pytest.raises(SandboxError) as ei:
        KernelKey(SandboxKey("t", "w", "u"), "c", "ruby")
    assert ei.value.kind == "invalid_request"


def test_shell_channel_has_its_own_token_and_lock_but_code_token_is_unchanged():
    s = SandboxKey("t", "w", "u")
    code = KernelKey(s, "c1", "python3")
    shell = KernelKey(s, "c1", "python3", "shell")
    assert code.token == "c1\x1fpython3"  # historical shape stays valid
    assert shell.token != code.token and shell.lock_key != code.lock_key


def test_unsupported_channel_is_rejected():
    with pytest.raises(SandboxError) as ei:
        KernelKey(SandboxKey("t", "w", "u"), "c", "python3", "pty")
    assert ei.value.kind == "invalid_request"


def test_kernel_lock_key_depends_on_conversation_and_language():
    s = SandboxKey("t", "w", "u")
    base = KernelKey(s, "c1", "python3")
    assert base.lock_key == KernelKey(s, "c1", "python3").lock_key
    assert base.lock_key != KernelKey(s, "c2", "python3").lock_key
    assert base.lock_key != KernelKey(s, "c1", "nodejs").lock_key
    assert base.token != KernelKey(s, "c1", "nodejs").token


def test_shell_channel_has_its_own_token_and_lock_key():
    s = SandboxKey("t", "w", "u")
    code = KernelKey(s, "c1", "python3")
    shell = KernelKey(s, "c1", "python3", "shell")
    assert code.token == "c1\x1fpython3"  # historical token is unchanged
    assert shell.token != code.token and shell.lock_key != code.lock_key


def test_unsupported_channel_is_rejected():
    with pytest.raises(SandboxError) as ei:
        KernelKey(SandboxKey("t", "w", "u"), "c", "python3", "pty")
    assert ei.value.kind == "invalid_request"
